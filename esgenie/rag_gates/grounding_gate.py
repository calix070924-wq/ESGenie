"""Post-generation grounding gate for citation and numeric support checks."""
from __future__ import annotations

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
from ..knowledge.greenwash_lexicon import ABSOLUTE_UNVERIFIABLE, VAGUE_SUPERLATIVES, vague_matches
from ..schemas import GroundingResult

# Absolute/superlative expressions that trigger G5 when ungrounded
_G5_OVERCLAIM_PATTERNS: list[str] = ABSOLUTE_UNVERIFIABLE + VAGUE_SUPERLATIVES + [
    "업계 유일", "업계 1위", "세계 1위", "국내 유일", "국내 1위",
    "100%", "유일한", "완전한",
]


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


def _check_numbers_and_units(
    sentence: str,
    cited_texts: list[str],
    orphan_numbers: list[str],
    unit_mismatches: list[str],
) -> None:
    """Check sentence numbers against cited chunks; route to G2 or G4.

    Matching order per (s_val, s_unit):
    1. units_compatible → convert_to_common → numeric_equal → MATCHED (grounded)
    2. units NOT compatible but numeric_equal(s_val, c_val) → G4 (unit mismatch)
    3. No match at all → fall through to plain G2 orphan check
    """
    sent_pairs = extract_number_unit_pairs(sentence)
    chunk_pairs_all = []
    for ct in cited_texts:
        chunk_pairs_all.extend(extract_number_unit_pairs(ct))

    matched_numbers: set[str] = set()
    for s_val, s_unit in sent_pairs:
        found_match = False
        found_g4 = False
        for c_val, c_unit in chunk_pairs_all:
            if units_compatible(s_unit, c_unit):
                # Same group: convert chunk value to sentence unit scale, then compare
                converted = convert_to_common(c_val, c_unit, s_unit)
                if converted is not None and numeric_equal(s_val, converted):
                    found_match = True
                    break
                # Compatible units but values don't match after conversion — not a match,
                # continue searching other chunk pairs
            else:
                # Incompatible units with same numeric value → G4
                if numeric_equal(s_val, c_val):
                    unit_mismatches.append(f"{s_val} {s_unit} ↔ {c_val} {c_unit}")
                    found_g4 = True
                    break
        if found_match or found_g4:
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
            # Exemption: if the same expression exists in any cited chunk, it's grounded
            if any(pattern in ct for ct in cited_texts):
                continue
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
