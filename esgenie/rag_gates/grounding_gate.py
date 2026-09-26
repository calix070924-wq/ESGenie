"""Post-generation grounding gate for citation and numeric support checks."""
from __future__ import annotations

import re
from typing import Any

from .signals import (
    CitedSentence,
    extract_numbers,
    is_claim_sentence,
    number_in_text,
    parse_cited_sentences,
    strip_citation_markers,
)
from .units import convert_to_common, extract_number_unit_pairs, numeric_equal, units_compatible
from ..knowledge.greenwash_lexicon import (
    ABSOLUTE_UNVERIFIABLE,
    VAGUE_ENVIRONMENTAL,
    VAGUE_SUPERLATIVES,
)
from ..schemas import GroundingResult

# Absolute/superlative expressions that trigger G5 when ungrounded
_G5_OVERCLAIM_PATTERNS: list[str] = ABSOLUTE_UNVERIFIABLE + VAGUE_SUPERLATIVES + [
    "업계 유일", "업계 1위", "세계 1위", "국내 유일", "국내 1위",
    "유일한", "완전한",
]

# "100%" + 과장/절대화 어휘 결합 시에만 G5 발화 (정량 사실 오탐 방지)
_100_PERCENT_ABSOLUTES = VAGUE_ENVIRONMENTAL + [
    "천연", "재활용", "완전", "유일", "무공해", "생분해", "자연분해",
]
_G5_100_PERCENT_RE = re.compile(
    r"100\s*%\s*(" + "|".join(re.escape(w) for w in _100_PERCENT_ABSOLUTES) + r")"
)

# 인증 취득 주장 — 인증 종류(name)와 부정·계획 문맥(gap 이후)을 함께 본다.
# name은 '인증' 바로 앞의 어휘 묶음이다(ISMS, ISO 14001, 정보보호 관리체계 …).
_CERT_COMPLETION_RE = re.compile(
    r"(?P<name>[0-9A-Za-z가-힣][0-9A-Za-z가-힣\s\-()·]{0,24}?)?인증"
    r"(?P<gap>[^.!?\n]{0,12}?)(?:취득|획득|완료|받았|받아)"
)
_CERT_NOT_DONE_RE = re.compile(r"않|못|미취득|없|불가|취소|반납|실패")
_CERT_PENDING_RE = re.compile(r"예정|준비|계획|목표|추진|신청")
# 취득 술어에 바로 붙어 '이미 취득했다'로 끝내는 어미. 이 어미가 있으면 그 절에서
# 상태가 확정되므로 뒤따르는 다른 행위의 어휘는 읽지 않는다.
_CERT_DONE_TAIL_RE = re.compile(r"^(?:했|하였|되었|됐|완료)")
# 뒤 절로 이어 주는 연결 어미. `취득하여 운영 중이다`(완료)와 `취득해서 제출할
# 예정이다`(계획)에 똑같이 쓰이므로 이것만으로 완료를 확정하지 않는다(5차 검토 2).
_CERT_LINK_TAIL_RE = re.compile(r"^(?:하여|해서|해|되어|돼)")
# 연결 어미가 이어 주는 뒤 절의 끝 — 문장 종결 부호. `-고 `나 쉼표에서 자르면
# `취득해서 제출하고, 운영할 예정이다`의 계획 표지가 잘려 완료로 읽힌다(6차 검토 2).
_CERT_CLAUSE_END_RE = re.compile(r"[.!?\n]")
# 연결 어미 뒤 절의 계획·의무 표지 — 문장 전체를 계획으로 만든다.
_CERT_LINK_PENDING_RE = re.compile(r"예정|계획|목표|향후|앞으로|추후|^(?:하여|해)야")
# 연결 어미 뒤 절의 `준비·추진·신청` 술어. `취득해 갱신을 준비 중이다`(완료+별도 행위)와
# `취득해 제출을 준비 중이다`를 어휘로 가를 수 없다 — 판정은 호출 쪽에 맡긴다.
# 술어일 때만 센다 — `취득하여 준비 자료를 보관 중이다`의 '준비'는 명사 수식어다.
_CERT_LINK_PREPARING_RE = re.compile(
    r"(?:준비|추진|신청)\s*(?:중|할|하고\s*있|합니다|한다|이다|입니다)")
# 취득 술어에 바로 붙은 부정 어미 — `취득하지 않았으며`, `취득하지 못했으며`, `취득 못했다`.
# 매치는 `취득`에서 끝나므로 매치 문자열만 보면 이 부정을 놓친다(7차 검토 P2).
# 계획·준비는 넣지 않는다 — 명시적인 미취득만 센다.
_CERT_DENIED_TAIL_RE = re.compile(r"^(?:하지|되지)?\s*(?:않|못)")


def evaluate_grounding(answer_text: str, cited_chunks: list[dict[str, Any]]) -> GroundingResult:
    sentences = parse_cited_sentences(answer_text)
    chunk_map = {
        str(chunk.get("id") or ""): str(chunk.get("text") or "")
        for chunk in cited_chunks
        if chunk.get("id")
    }

    uncited: list[str] = []
    orphan_numbers: list[str] = []
    unit_mismatches: list[str] = []
    overclaim = False
    supported_sentences = 0
    claim_sentences = 0

    for sent in sentences:
        if not is_claim_sentence(sent.clean_text):
            continue
        claim_sentences += 1
        if not sent.cited_chunk_ids:
            uncited.append(sent.clean_text)
            continue

        cited_texts = [chunk_map[cid] for cid in sent.cited_chunk_ids if cid in chunk_map]
        if cited_texts:
            supported_sentences += 1

        # G2 + G4: number and unit checks
        _check_numbers_and_units(sent.clean_text, cited_texts, orphan_numbers, unit_mismatches)

        # G5: overclaim check
        if not overclaim:
            overclaim = _check_overclaim(sent.clean_text, cited_texts)

    hard_fails: list[str] = []
    soft_flags: list[str] = []
    for sent in sentences:
        claimed = _certification_claims(sent.clean_text)
        if not claimed:
            continue
        cited_texts = [chunk_map[cid] for cid in sent.cited_chunk_ids if cid in chunk_map]
        if (not all(any(_certification_acquired(name, t) for t in cited_texts) for name in claimed)
                and "G5_unproven_certification_completion" not in soft_flags):
            soft_flags.append("G5_unproven_certification_completion")
    if uncited:
        hard_fails.append("G1_uncited_claims")
    if orphan_numbers:
        hard_fails.append("G2_orphan_numbers")
    if unit_mismatches:
        hard_fails.append("G4_unit_mismatch")
    if overclaim:
        soft_flags.append("G5_overclaim")

    decision = "ACCEPT" if not hard_fails else "ESCALATE"
    faithfulness = 1.0 if claim_sentences == 0 else round(supported_sentences / claim_sentences, 4)

    return GroundingResult(
        decision=decision,
        g1_uncited_sentences=uncited,
        g2_orphan_numbers=_dedupe(orphan_numbers),
        g4_unit_mismatches=_dedupe(unit_mismatches),
        g5_overclaim=overclaim,
        hard_fails=hard_fails,
        soft_flags=soft_flags,
        faithfulness=faithfulness,
    )


def _certification_claims(sentence: str) -> list[frozenset[str]]:
    """문장이 '어떤 인증을 취득했다'고 주장하는지 — 인증 식별자 집합으로 돌려준다.

    부정형(`취득하지 않았다`)과 **계획 진술**(`취득할 예정이다`)은 완료 주장이 아니다.
    계획 진술을 완료 주장으로 세면 원문과 답변이 같은 `취득할 예정이다`인 정상 답변이
    미입증 취득으로 막힌다(2026-09-21 3차 검토 R3-b). 계획 진술에 대한 인용·숫자 등
    다른 grounding 검사는 그대로 적용된다.
    """
    names = []
    for m in _CERT_COMPLETION_RE.finditer(sentence):
        if not _cert_completed(m, sentence, as_claim=True):
            continue
        name = _certification_identity(m.group("name") or "")
        if name and name not in names:
            names.append(name)
    return names


def _certification_acquired(name: frozenset[str], text: str) -> bool:
    """청크 원문이 **같은 인증의 실제 취득**을 진술하는가.

    종전에는 인용 청크 어디든 `인증 … 취득`이 있으면 인정했다. 그래서
    `ISMS 인증을 취득하지 않았다`(부정형)와 `ISO 인증을 취득했다`(다른 인증)가
    `ISMS 인증을 취득했다`의 근거로 통과했다(2026-09-21 재검토 R3). 이어 부분 문자열
    비교로 종류를 맞추던 방식은 ISMS와 ISMS-P를 같다고 보고, 문장 주어까지 인증명에
    넣어 `당사는 ↔ 회사는`을 다른 인증으로 보았다(3차 검토 R3-a). 그래서 식별자
    집합으로 비교한다 — 주장의 식별자가 원문 식별자에 모두 있어야 근거가 된다.
    `ISO 27001` 주장은 포괄적인 `ISO` 취득 진술로 입증되지 않는다.
    """
    matches = list(_CERT_COMPLETION_RE.finditer(text))
    for m in matches:
        if not _cert_completed(m, text):
            continue
        source = _certification_identity(m.group("name") or "")
        if source and name <= source and not _cert_denied_nearby(name, m, matches, text):
            return True
    return False


def _cert_denied_nearby(name: frozenset[str], match: re.Match[str],
                        matches: list[re.Match[str]], text: str) -> bool:
    """같은 문장이 같은 인증의 **미취득**을 명시하는가.

    `ISMS 인증은 미취득 상태이며, … 취득해서 …`처럼 한 문장이 현재 미취득을 밝혔다면
    뒤 술어를 어떻게 읽든 완료 근거가 될 수 없다(6차 검토 2). 다른 문장의 과거
    미취득(`2023년에는 받지 못했다. 2025년 취득했다.`)은 막지 않는다.
    `취득하지 않았으며`처럼 술어 뒤 어미로 밝힌 미취득도 같이 센다(7차 검토 P2).
    """
    start = max(text.rfind(ch, 0, match.start()) for ch in ".!?\n") + 1
    end = _CERT_CLAUSE_END_RE.search(text, match.end())
    stop = end.start() if end else len(text)
    for other in matches:
        if other is match or other.start() < start or other.start() >= stop:
            continue
        if (name <= _certification_identity(other.group("name") or "")
                and (_CERT_NOT_DONE_RE.search(other.group(0))
                     or _CERT_DENIED_TAIL_RE.match(text[other.end():other.end() + 12]))):
            return True
    return False


def _cert_completed(match: re.Match[str], text: str, *, as_claim: bool = False) -> bool:
    """이 인증 취득 술어가 **이미 취득**을 진술하는가 — 부정·계획이면 False.

    뒤 12글자를 통째로 읽으면 `취득했고 준비 자료를 보관한다`의 '준비'가 완료 주장을
    지운다. 그러면 미입증 취득이 soft flag 없이 통과한다(4차 검토 C). 그래서 술어에
    바로 붙은 어미를 먼저 읽는다 — `취득했다/취득하여`처럼 완료로 끝나면 그 절에서
    상태가 확정되고, 뒤에 이어지는 별도 행위의 '준비·계획'은 이 인증의 상태가 아니다.
    완료 어미가 없을 때만(`취득을 준비 중`, `취득할 예정`, `취득하지 않았다`) 창을
    넓혀 부정·계획을 판정한다.

    `하여·해서·해` 같은 연결 어미는 완료가 아니다 — 뒤 절이 상태를 정한다. 이것을
    완료로 확정하면 `향후 … 취득해서 제출할 예정이다`가 취득 근거로 통과했다(5차 검토
    2). 그래서 연결 어미 뒤 절의 끝까지 읽어 부정·계획이 없을 때만 완료로 본다.
    뒤 절이 `준비 중` 같은 술어로 끝나 완료 여부가 불분명하면 양쪽 모두 보수적으로
    판정한다 — 답변(`as_claim=True`)은 취득 주장으로 세고, 원문은 근거로 쓰지 않는다.
    """
    tail = text[match.end():match.end() + 12]
    if _CERT_DONE_TAIL_RE.match(tail):
        # 완료 어미 앞의 `미취득`처럼 술어 자체의 부정은 그대로 막는다.
        return not _CERT_NOT_DONE_RE.search(match.group(0))
    if _CERT_LINK_TAIL_RE.match(tail):
        rest = text[match.end():]
        end = _CERT_CLAUSE_END_RE.search(rest)
        clause = rest[:end.start()] if end else rest
        if (_CERT_NOT_DONE_RE.search(match.group(0))
                or _CERT_LINK_PENDING_RE.search(clause) or _CERT_LINK_PENDING_RE.search(match.group(0))):
            return False
        return as_claim or not _CERT_LINK_PREPARING_RE.search(clause)
    context = match.group(0) + tail
    return not (_CERT_NOT_DONE_RE.search(context) or _CERT_PENDING_RE.search(context))


# 인증명이 아닌 수식어 — 문장 주어, 연·월 표기, 시점·일반 서술. 식별자에서 분리한다.
# 인증 번호는 지우지 않는다 — `\d{4}년`처럼 단위가 붙은 표기만 연도로 본다.
_CERT_SUBJECT_RE = re.compile(r"^(당사|본사|회사|우리|그룹|법인|저희)[은는이가의]?$")
_CERT_GENERIC_RE = re.compile(r"^(\d{4}년|\d{1,2}월|현재|기준|이미|국제|국내|해외|관련|주요)$")


def _certification_identity(name: str) -> frozenset[str]:
    """인증명 → 식별자 집합. 주어·연월·일반 수식어는 뺀다.

    `2025년 정보보호 ISMS` → {정보보호, ISMS}, `당사는 ISMS` → {ISMS},
    `ISO 27001` → {ISO, 27001}. 확장 접미사와 인증 번호는 식별자로 남는다 —
    `ISMS-P`는 `ISMS`와 다른 종류이고, `ISO 27001`은 `ISO`만으로 입증되지 않는다.
    """
    tokens = []
    for raw in re.split(r"[\s,·]+", name.strip()):
        token = re.sub(r"[()]", "", raw).upper()
        if not token or _CERT_SUBJECT_RE.match(token) or _CERT_GENERIC_RE.match(token):
            continue
        tokens.append(token)
    return frozenset(tokens)


def _check_numbers_and_units(
    sentence: str,
    cited_texts: list[str],
    orphan_numbers: list[str],
    unit_mismatches: list[str],
) -> None:
    """Check sentence numbers against cited chunks; route to G2 or G4.

    2-pass approach per (s_val, s_unit) to eliminate order dependence:
    Pass 1: full scan for compatible-unit match (grounded) — if found, skip G4.
    Pass 2: only if no match found, collect incompatible-unit same-value pairs as G4.
    """
    sent_pairs = extract_number_unit_pairs(sentence)
    chunk_pairs_all = []
    for ct in cited_texts:
        chunk_pairs_all.extend(extract_number_unit_pairs(ct))

    matched_numbers: set[str] = set()
    for s_val, s_unit in sent_pairs:
        # Pass 1: search for a grounded match (compatible units + value match)
        found_match = False
        for c_val, c_unit in chunk_pairs_all:
            if units_compatible(s_unit, c_unit):
                converted = convert_to_common(c_val, c_unit, s_unit)
                if converted is not None and numeric_equal(s_val, converted):
                    found_match = True
                    break

        if found_match:
            matched_numbers.add(str(int(s_val)) if s_val == int(s_val) else str(s_val))
            continue

        # Pass 2: no grounded match — check for G4 (incompatible unit, same value)
        found_g4 = False
        for c_val, c_unit in chunk_pairs_all:
            if not units_compatible(s_unit, c_unit) and numeric_equal(s_val, c_val):
                unit_mismatches.append(f"{s_val} {s_unit} ↔ {c_val} {c_unit}")
                found_g4 = True
                break

        if found_g4:
            matched_numbers.add(str(int(s_val)) if s_val == int(s_val) else str(s_val))

    # Plain numbers without recognized units: fall back to G2 text search
    for number in extract_numbers(sentence):
        if number in matched_numbers:
            continue
        if not any(number_in_text(number, chunk_text) for chunk_text in cited_texts):
            orphan_numbers.append(number)


def _check_overclaim(sentence: str, cited_texts: list[str]) -> bool:
    """G5: detect overclaim expressions not grounded in cited chunks."""
    for pattern in _G5_OVERCLAIM_PATTERNS:
        if pattern in sentence:
            if any(pattern in ct for ct in cited_texts):
                continue
            return True
    # "100% + 과장/절대화 어휘" 결합 패턴
    m = _G5_100_PERCENT_RE.search(sentence)
    if m:
        matched_expr = m.group(0)
        if not any(matched_expr in ct for ct in cited_texts):
            return True
    return False


def grounding_feedback(result: GroundingResult) -> str:
    if result.decision == "ACCEPT" and not result.soft_flags:
        return ""

    parts = [
        "=== 근거 게이트 재작성 제약 ===",
        "모든 주장 문장 끝에 제공된 검색 청크의 [chunk_id] 인용을 붙일 것.",
        "인용한 청크에 없는 숫자는 절대 새로 쓰지 말 것.",
        "근거가 없으면 해당 문장을 삭제하거나 보수적으로 완화할 것.",
    ]
    if result.g1_uncited_sentences:
        parts.append("인용 누락 문장:")
        parts.extend(f"- {text}" for text in result.g1_uncited_sentences[:5])
    if result.g2_orphan_numbers:
        parts.append("청크 원문에서 확인되지 않은 숫자:")
        parts.append("- " + ", ".join(result.g2_orphan_numbers[:10]))
    if result.g4_unit_mismatches:
        parts.append("단위 불일치 — 인용 문장과 청크의 단위를 통일하거나 환산해 일치시킬 것:")
        parts.extend(f"- {m}" for m in result.g4_unit_mismatches[:10])
    if result.g5_overclaim:
        parts.append("근거 없는 절대화/강조 표현을 완화하거나 출처를 제시할 것.")
    parts.append("=== 위 제약을 모두 지켜 재작성하라. ===")
    return "\n".join(parts)


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


__all__ = [
    "CitedSentence",
    "evaluate_grounding",
    "grounding_feedback",
    "parse_cited_sentences",
    "strip_citation_markers",
]
