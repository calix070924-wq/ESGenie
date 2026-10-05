"""Layer 6 — 통합 보고서 조립 계층.

PipelineOutput에 흩어져 있는 분석 결과(커버리지·D6 선택적 공시·ISSB 갭·4축 리스크·
개선 로드맵·증빙)를 하나의 서술형 보고서로 엮는다.

설계 원칙
---------
- **하이브리드**: 대부분의 블록은 결정적(deterministic)으로 PipelineOutput 값을 그대로
  박아 환각을 0으로 만든다. 서술이 필요한 2개 블록(Executive Summary, 업종 벤치마크
  해설)만 LLM이 작성한다.
- **mock 누수 차단**: LLM 블록은 ``CLIENT.complete``를 호출하되 ``resp.used_mock``이
  True이면 결과를 버리고 모듈 자체 결정적 fallback 텍스트로 대체한다. (llm.py의 mock
  라우터가 엉뚱한 ESG 템플릿을 뱉어 보고서에 섞이는 것을 막는다.)
- **단일 소스**: 각 블록은 마크다운 문자열(body_md)만 보유한다. Streamlit 미리보기와
  PDF(exporters/report_pdf.py)가 동일한 마크다운에서 파생된다.

진입점: ``assemble_report(output) -> ReportDoc``
"""
from __future__ import annotations

from .schemas import format_score


import datetime
import json
import re
from dataclasses import dataclass, field
from typing import Any

from .config import INDUSTRY_DIR
from .llm import CLIENT

AREA_LABELS = {"E": "환경", "S": "사회", "G": "지배구조"}


def _evidence_cov(ext: Any) -> float:
    """증빙 연결 커버리지 — 값 존재(coverage_pct)와 분리 (Phase 2)."""
    try:
        from .layer1_extract import evidence_coverage_pct
        return evidence_coverage_pct(ext)
    except Exception:
        return 0.0


# ====================================================================
# 자료구조
# ====================================================================

@dataclass
class ReportBlock:
    id: str            # "cover" | "exec_summary" | "esg_E" | "benchmark" ...
    title: str         # 섹션 제목 ("" 이면 to_markdown에서 제목 줄 생략)
    body_md: str       # 마크다운 본문 (표 포함)
    kind: str          # "deterministic" | "llm" | "reused" — 출처 추적/디버깅용
    # 생성 본문 대조의 감사 기록(바꾼 문장의 모델 원문·사유, 표시만 붙인 문장). 보고서 본문에는 싣지 않는다.
    reviews: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ReportDoc:
    corp_name: str
    industry: str
    report_year: int
    generated_at: str
    blocks: list[ReportBlock]
    meta: dict[str, Any] = field(default_factory=dict)

    def to_markdown(self) -> str:
        parts: list[str] = []
        for b in self.blocks:
            if b.title:
                parts.append(f"## {b.title}")
            parts.append(b.body_md.rstrip())
        return "\n\n".join(p for p in parts if p.strip()) + "\n"


# ====================================================================
# 마크다운 헬퍼
# ====================================================================

def _fmt(v: Any) -> str:
    if v is None or v == "":
        return "—"
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    """마크다운 pipe 표 생성. rows가 비면 빈 문자열."""
    if not rows:
        return ""
    head = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    body = "\n".join("| " + " | ".join(_fmt(c) for c in r) + " |" for r in rows)
    return f"{head}\n{sep}\n{body}"


# ====================================================================
# 업종 벤치마크 로더 (pipeline._load_industry_stats와 동일 로직, 순환 회피용 사본)
# ====================================================================

def _load_industry_stats(industry: str | None) -> dict[str, Any] | None:
    if not industry:
        return None
    try:
        for path in INDUSTRY_DIR.glob("*.json"):
            with open(path, encoding="utf-8") as fp:
                obj = json.load(fp)
            for b in obj.get("benchmarks", []):
                if b["industry"] == industry:
                    return b
    except Exception:
        return None
    return None


# ====================================================================
# 공통 추출 헬퍼
# ====================================================================

def _overall_risk(output: Any) -> tuple[float | None, str]:
    """영역별 위험도 중 최댓값과 그 밴드."""
    scores = [(v.final_score, v.final_band) for v in output.sections.values() if v.final_score is not None]
    if not scores:
        return None, "평가불가"
    score, band = max(scores, key=lambda x: x[0])
    if any(v.final_score is None or "부분 평가" in v.final_band for v in output.sections.values()):
        band = "부분 평가"
    return score, band


def _corp_meta(output: Any) -> tuple[str, str, int]:
    rep = output.report
    if rep is not None:
        return rep.corp_name, rep.industry, rep.report_year
    ext = output.extraction
    name = ext.corp_name if ext is not None else "—"
    return name, "", 0


# ====================================================================
# 블록 빌더 — 결정적
# ====================================================================

def _block_cover(output: Any) -> ReportBlock:
    name, industry, year = _corp_meta(output)
    risk, band = _overall_risk(output)
    ext = output.extraction
    cov = f"{ext.coverage_pct:.1f}%" if ext is not None else "—"
    ecov = f"{_evidence_cov(ext):.1f}%" if ext is not None else "—"
    profile = ext.profile_label if ext is not None else "—"
    d6 = output.disclosure
    issb = output.issb_gap

    rows = [
        ["대상 기업", name],
        ["업종", industry or "—"],
        ["보고 연도", f"{year}년" if year else "—"],
        ["적용 프로파일", profile],
        ["K-ESG 커버리지", cov],
        ["증빙 연결 커버리지", ecov],
        ["종합 그린워싱 위험도", f"{format_score(risk)} ({band})"],
    ]
    if d6 is not None:
        rows.append(["선택적 공시 의심도", f"{d6.score:.2f} ({d6.level})"])
    if issb is not None:
        rows.append(["ISSB 프로파일 내 공시", f"{issb.in_profile_disclosed}/{issb.in_profile_total} (누락 {issb.in_profile_missing})"])
    if output.industry_module_key:
        rows.append(["적용 업종 모듈", output.industry_module_key])

    body = (
        f"# {name} ESG 공시 신뢰성 보고서\n\n"
        f"_생성일: {output_generated_at(output)}_\n\n"
        + _md_table(["항목", "내용"], rows)
    )
    return ReportBlock(id="cover", title="", body_md=body, kind="deterministic")


def output_generated_at(output: Any) -> str:
    return getattr(output, "_generated_at", datetime.date.today().isoformat())


def _block_source_review(output: Any) -> ReportBlock | None:
    findings = getattr(output, "review_findings", None)
    if not findings:
        return None
    from .source_review import review_markdown
    return ReportBlock("source_review", "확인 필요 사항", review_markdown(findings), "deterministic")


def _block_esg(output: Any, area: str) -> ReportBlock | None:
    verify = output.sections.get(area)
    if verify is None:
        return None
    label = AREA_LABELS.get(area, area)
    rv = verify.final.detection.risk_vector
    axis_note = ""
    if rv is not None:
        axis_note = " · 4축(D1/D2/D3/D5): " + "/".join(
            "평가불가" if axis.abstain else format_score(axis.score, scale=100, digits=0)
            for axis in (rv.D1_numeric, rv.D2_modifier, rv.D3_semantic, rv.D5_timeseries))
        axis_note += f" · {rv.evaluation_label} · D1 {rv.numeric_coverage_label}"
        if rv.abstained_axes():
            axis_note += " (기권 축: " + ", ".join(rv.abstained_axes()) + ")"

    lead = (
        f"> **{label} ({area})** · 위험도 {format_score(verify.final_score)} ({verify.final_band}) "
        f"· 검증 {verify.iterations_used}회"
        + (" · ⚠ 사람 검토 필요(HITL)" if verify.hitl_required else "")
        + axis_note
    )
    text, marks = annotate_generated_text(output, area, verify)
    body = f"{lead}\n\n{text.strip()}"
    if marks:
        body += ("\n\n> [확인 보류]·[범위 미확정]·[범위 주의] 문구는 생성 문장을 원장·원문 확인 수치·인용 근거와 "
                 "대조해 바꾼 것이고, [검토] 표시는 판단이 갈려 덧붙인 것입니다. 모델이 쓴 원래 문장과 처리 사유는 "
                 "보고서 감사 기록(report_body_review.json)에 남겼습니다.")
    return ReportBlock(id=f"esg_{area}", title="", body_md=body, kind="reused", reviews=marks)


# 생성 본문 대조(2026-10-05 §4·§6.2, PR71 검토 R1·R2). 경고가 부록(확인 필요 사항)에만 있고 본문은 단정하던 경로를 막는다.
# PR71 검토: 잘못된 수치 문장(`정규직 15명과 기간제 6명이 출석`) 뒤에 표시만 붙여 오답이 본문에 남았고, 표·제목·
# 동의 표현(`산재율`)·부정된 유보(`추가 확인할 사항 없이 확정`)·날짜 숫자(`4월 15일`의 15)가 검사에서 빠졌다.
# 확인된 오류는 **같은 자리에서** 고친다 — 근거 없는 수량 단정·미확정 값의 확정 단정·부분값의 범위 확대는 문장을
# 확인 보류·미확정 문구로 바꾸고(원문 확인 값을 함께 적는다), 모델 원문과 사유는 감사 기록(marks)에 남긴다.
# 판단이 갈리는 경우(목표 문장, 값 없이 범위 낱말만, 단위 없는 숫자)는 기존처럼 표시만 붙인다.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。])\s+")
_SCOPE_WIDEN_RE = re.compile(r"전체|전사|연간|합산|모든\s*사업장|총합|누적")
# 유보 표현. 바로 뒤에서 부정되면(`추가 확인할 사항 없이`) 유보가 아니다.
_UNCONFIRMED_WORDS_RE = re.compile(r"미확정|확인되지\s*않|확인하지\s*못|확인되지|확인하지|원문\s*범위|참고\s*값|참고용|참고"
                                   r"|확정하지\s*않|확정되지\s*않|추가\s*확인|확인\s*필요|명확하지\s*않|불명확|확정\s*여부")
_HEDGE_NEGATED_RE = re.compile(r"\s*(?:이|가)?\s*(?:할|될|한|된|하는|되는)?\s*(?:사항|것|부분|점|내용|필요)?\s*(?:이|은|는|가)?\s*"
                               r"(?:없이|없으며|없고|없음|없다|없습니다|없는|없어)")
# 확정 단정(`확정되었다`·`확정된 실적`). `미확정`·`확정 여부`·`확정하지 않`은 단정이 아니다.
_CONFIRMED_CLAIM_RE = re.compile(r"(?<![미불])확정(?:되었|됐|된\s*(?:실적|값|수치|성과)|됨|이다|입니다|하였|했|적으로)")
_NEGATION_WORDS_RE = re.compile(r"발생하지\s*않|없었|없음|없다|미발생|미보유|0\s*건|0\s*명|제로|무재해")
# 목표·계획 문장 — 미확정 값의 단정인지 판단이 갈린다(표시만 붙인다).
_GOAL_WORDS_RE = re.compile(r"목표|계획|예정|추진할|하겠|전망")
# 쉼표로 묶은 인용(`[a_txt_0005, a_txt_0009]`)도 인용이다 — 그 번호를 본문 숫자로 세지 않는다.
_GROUP_CITATION_RE = re.compile(r"\[([0-9A-Za-z가-힣._:-]+(?:\s*,\s*[0-9A-Za-z가-힣._:-]+)+)\]")
# K-ESG 항목 코드(`S-3-1`)의 숫자는 주장한 수치가 아니다.
_ITEM_CODE_RE = re.compile(r"(?<![A-Za-z0-9])[ESGP]-\d{1,2}-\d{1,2}(?![0-9])")
# 제목의 성과 단정 낱말(`달성`·`기록`). 미확정 값의 제목에서 뗀다.
_HEADING_CLAIM_RE = re.compile(r"\s*(?:달성|기록|실현|성공|확정|확인|유지)(?:했다|하였다|됨|함|했음)?")


_SCOPE_HEDGE_RE = re.compile(r"아닌|아니|아님|불명확|미확인|확인되지|확인하지|부분값|부분\s*범위|한정")


def _ledger_entries(output: Any, area: str | None = None) -> list[dict[str, Any]]:
    """본문 대조에 쓰는 원장 값: 이름·검색어·값·단위·플래그·경계 표기·범위 사유(영역을 주지 않으면 전 영역)."""
    from .knowledge.kesg_items import by_code
    from .ssot.boundary import Boundary
    entries = []
    for code, entry in (getattr(getattr(output, "extraction", None), "mapped", {}) or {}).items():
        fact = entry.get("resolved_fact") or {}
        if (area is not None and entry.get("area") != area) or not fact or entry.get("value") is None:
            continue
        flags = set(fact.get("flags") or ())
        item = by_code(code)
        boundary = fact.get("boundary") or {}
        entries.append({"code": code, "name": str(entry.get("name") or code), "value": entry.get("value"),
                        "unit": entry.get("unit") or "", "flags": flags,
                        "terms": tuple(t for t in (getattr(item, "search_terms", ()) or ()) if len(t) >= 3),
                        "label": Boundary.from_dict(boundary).label(),
                        "note": next(iter(fact.get("scope_notes") or ()), ""),
                        "period_text": str(boundary.get("period_text") or ""),
                        "period_start": str(boundary.get("period_start") or ""),
                        "period_end": str(boundary.get("period_end") or ""),
                        "partial": "partial_value" in flags or "incomplete_scope" in flags
                                   or fact.get("completeness") == "partial"})
    return entries


def _ledger_facts(entries: list[dict[str, Any]]) -> list:
    from .rag_gates.units import normalize_unit
    from .report_claims import Fact, label_tokens, period_of
    facts = []
    for e in entries:
        try:
            value = float(e["value"])
        except (TypeError, ValueError):
            continue
        facts.append(Fact(label=e["name"], value=value, unit=normalize_unit(e["unit"]) if e["unit"] else None,
                          period=period_of(e["period_text"], e["period_start"], e["period_end"]),
                          period_text=e["period_text"], kind="ledger", code=e["code"],
                          tokens=label_tokens(e["name"]) + tuple(e["terms"])))
    return facts


def _hedged(sentence: str) -> bool:
    """문장이 미확정·참고 상태를 실제로 밝혔는가. 부정된 유보(`추가 확인할 사항 없이`)·확정 단정은 유보가 아니다."""
    if _CONFIRMED_CLAIM_RE.search(sentence):
        return False
    return any(not _HEDGE_NEGATED_RE.match(sentence, m.end()) for m in _UNCONFIRMED_WORDS_RE.finditer(sentence))


def _entry_mentions(sentence: str, entries: list[dict[str, Any]], found: list) -> list[tuple[dict[str, Any], bool]]:
    """문장이 가리키는 원장 항목과 '값까지 말했는가'. 이름·K-ESG 검색어로 찾고, 없으면 값+단위로 찾는다.

    값+단위(`0‰`)만으로 찾은 항목이 여럿이면 어느 항목인지 특정하지 못한다 — 그때는 '애매함'으로 둔다(표시만).
    """
    from .rag_gates.units import normalize_unit
    from .report_claims import value_matches
    named, by_value = [], []
    for e in entries:
        unit = normalize_unit(e["unit"]) if e["unit"] else None
        stated = any(value_matches(q, e["value"], unit) for q in found if q.unit)
        if e["value"] == 0 and not stated:
            stated = bool(_NEGATION_WORDS_RE.search(sentence)
                          or re.search(r"(?<![\d.,])0(?:\.0+)?(?![\d.,])", sentence))
        name = e["name"].split("(")[0].strip()
        if (name and name in sentence) or any(t in sentence for t in e["terms"]):
            named.append((e, stated))
        elif stated and any(value_matches(q, e["value"], unit) for q in found if q.unit):
            by_value.append((e, True))
    if named:
        return named
    return by_value if len(by_value) == 1 else [(e, None) for e, _ in by_value]


def _hold_text(issue: dict[str, Any]) -> str:
    """확인된 오류 문장을 대신할 문구. 원래 문장은 감사 기록(marks)에만 남긴다."""
    if issue["reason"] in ("orphan_number", "role_or_date_mismatch"):
        numbers = ", ".join(issue["quantities"])
        why = ("인용 근거·원문 확인 수치에서 찾지 못해" if issue["reason"] == "orphan_number"
               else "원문에서 다른 역할·날짜의 값으로만 확인돼")
        text = f"[확인 보류] 생성 문장의 수치({numbers})는 {why} 이 문장을 본문에 싣지 않았습니다."
        if issue.get("confirmed"):
            text += " 같은 문장에서 확인된 값: " + "; ".join(issue["confirmed"]) + "."
        if issue.get("related"):
            text += " 원문 확인 값: " + "; ".join(issue["related"]) + "."
        return text
    e = issue["entry"]
    value = f"{e['value']:g}" if isinstance(e["value"], float) else str(e["value"])
    if issue["reason"] == "source_only_stated":
        return (f"[범위 미확정] {e['name']} {value}{e['unit']}는 원문 범위({e['label'] or '원문 범위 미기록'})로만 "
                f"확인된 참고값이며, 요청 기간·사업장의 확정 실적인지 확인하지 못했습니다"
                + (f"({e['note'].rstrip('.')})" if e.get("note") else "") + ".")
    return (f"[범위 주의] {e['name']} {value}{e['unit']}의 원장 범위는 {e['label'] or '경계 미기록'}이며 "
            "전체·연간·합산 값이 아닙니다. 생성 문장은 범위를 넓혀 서술해 본문에 싣지 않았습니다.")


def _sentence_review(sentence: str, entries: list[dict[str, Any]], facts: list, area: str | None,
                     cited_texts: list[str] | None, legacy_texts: list[str] | None = None,
                     ) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    """한 문장의 (바꿀 오류, 표시 문구, 감사 기록). `cited_texts`가 None이면(인용 없는 요약) 수량 대조는 하지 않는다.

    `cited_texts`는 원장·원문 확인 수치 청크를 뺀 인용 근거(수량 대조용 — 그 둘은 구조화 사실로 본다),
    `legacy_texts`는 그것까지 포함한 인용 근거(단위 없는 숫자의 기존 글자 대조용)다.
    """
    from .rag_gates.grounding_gate import _check_numbers_and_units
    from .report_claims import check_quantity, contexts, date_near, quantities, related_facts
    from .ssot.ocr_router import _date_spans

    issues: list[dict[str, Any]] = []
    notes: list[str] = []
    marks: list[dict[str, Any]] = []
    found = quantities(_ITEM_CODE_RE.sub(lambda m: " " * len(m.group()), sentence))
    if cited_texts is not None:
        failed: dict[str, list] = {}
        units, whens, confirmed = set(), [], []
        for q, window, anchor, anchor_end, c_start, c_end in contexts(sentence, found):
            if not q.unit:
                continue
            when = date_near(sentence, q.start, c_start, c_end)
            result = check_quantity(q, window, anchor, anchor_end, when, facts, cited_texts)
            if not result.supported:
                failed.setdefault(result.reason, []).append((q, result))
                units.add(q.unit)
                whens.append(when)
            elif result.fact is not None:
                confirmed.append(result.fact.describe())
        for reason, items in failed.items():
            when = next((w for w in whens if w is not None), None)
            related = [f.describe() for f in related_facts(sentence, when, units, facts)]
            issues.append({"reason": reason, "quantities": [q.raw for q, _ in items],
                           "confirmed": [c for c in dict.fromkeys(confirmed) if c not in related],
                           "numbers": [f"{q.value:g}" for q, _ in items], "related": related,
                           "conflicts": [{"fact": f.describe(), "problems": p} for _q, r in items
                                         for f, p in r.conflicting]})
        # 단위 없는 숫자는 근거 청크의 숫자로만 본다 — 판단이 갈리므로 표시만 붙인다.
        orphans: list[str] = []
        _check_numbers_and_units(_ITEM_CODE_RE.sub(" ", sentence),
                                 cited_texts if legacy_texts is None else legacy_texts, orphans, [])
        dated = " ".join(span.text for span in _date_spans(sentence))

        def _is_quantity(number: str) -> bool:
            try:
                value = float(number.replace(",", ""))
            except ValueError:
                return False
            return any(q.unit and abs(q.value - value) < 1e-9 for q in found)

        bare = [n for n in dict.fromkeys(orphans) if n not in dated and not _is_quantity(n)]
        if bare:
            notes.append(f"근거 확인 필요: 인용 근거에서 찾지 못한 숫자 {', '.join(bare)} — 확정 사실로 보지 마세요")
            marks.append({"area": area, "sentence": sentence, "reason": "orphan_number", "numbers": bare,
                          "action": "marked"})
    hedged = _hedged(sentence)
    goal = bool(_GOAL_WORDS_RE.search(sentence))
    for e, stated in _entry_mentions(sentence, entries, found):
        if "scope_source_only" in e["flags"] and stated is not False and not hedged:
            if stated is None or goal:
                notes.append(f"범위 미확정: {e['name']} {e['value']}{e['unit']}은 원문 범위"
                             f"({e['label'] or '원문 범위 미기록'})로만 확인된 값이며 요청 기간·사업장의 확정 실적이 아닙니다")
                marks.append({"area": area, "sentence": sentence, "reason": "source_only_stated", "code": e["code"],
                              "action": "marked"})
            else:
                issues.append({"reason": "source_only_stated", "entry": e, "code": e["code"]})
        widened = _SCOPE_WIDEN_RE.search(sentence) and not _SCOPE_WIDEN_RE.search(e["label"]) \
            and not _SCOPE_HEDGE_RE.search(sentence)
        if e["partial"] and widened:
            if stated:
                issues.append({"reason": "scope_widened", "entry": e, "code": e["code"]})
            else:
                notes.append(f"범위 주의: {e['name']} 값의 원장 범위는 {e['label'] or '경계 미기록'}이며 "
                             "전체·연간·합산 값이 아닙니다")
                marks.append({"area": area, "sentence": sentence, "reason": "scope_widened", "code": e["code"],
                              "action": "marked"})
    return issues, list(dict.fromkeys(notes)), marks


def _split_sentences(line: str) -> list[str]:
    sentences: list[str] = []
    for part in _SENTENCE_SPLIT_RE.split(line):
        # `문장. [chunk_id] 다음 문장` — 마침표 뒤 인용은 앞 문장의 근거다.
        lead = re.match(r"(?:\s*\[[^\[\]\n]+\])+", part)
        if lead and sentences:
            sentences[-1] += " " + lead.group(0).strip()
            part = part[lead.end():]
        if part.strip():
            sentences.append(part)
    return sentences


def _with_notes(sentence: str, notes: list[str]) -> str:
    # 보고서 PDF 글꼴이 그리는 ASCII 괄호로 표시한다(〔〕·【】는 PDF에서 빠진다).
    return sentence + "".join(f" [검토: {n}]" for n in notes)


def _apply(sentence: str, issues: list[dict[str, Any]], notes: list[str], marks: list[dict[str, Any]],
           area: str | None) -> tuple[str, list[dict[str, Any]]]:
    """오류가 있으면 문장을 바꾼 문구, 없으면 표시를 붙인 문장. 바꾼 경우 모델 원문을 감사 기록에 남긴다."""
    if not issues:
        return _with_notes(sentence, notes), marks
    output = " ".join(dict.fromkeys(_hold_text(issue) for issue in issues))
    records = []
    for issue in issues:
        record = {"area": area, "sentence": sentence, "reason": issue["reason"], "action": "replaced",
                  "model_text": sentence, "output": output}
        for key in ("code", "numbers", "quantities", "related", "conflicts"):
            if issue.get(key):
                record[key] = issue[key]
        records.append(record)
    return output, records + marks


def _review_heading(line: str, entries, facts, area, cited, legacy=None) -> tuple[str, list[dict[str, Any]]]:
    """제목도 본문과 같은 계약을 쓴다 — 미확정 값의 성과 제목·근거 없는 수량 제목을 그대로 두지 않는다."""
    m = re.match(r"(\s*#+\s*)(.*)", line)
    prefix, text = m.group(1), m.group(2).strip()
    issues, notes, marks = _sentence_review(text, entries, facts, area, cited, legacy)
    if not issues:
        return prefix + _with_notes(text, notes), marks
    new = text
    for issue in issues:
        if issue["reason"] == "source_only_stated":
            new = _HEADING_CLAIM_RE.sub("", new).strip() + " (범위 미확정 참고값 — 요청 범위의 확정 실적 아님)"
        elif issue["reason"] == "scope_widened":
            new = _SCOPE_WIDEN_RE.sub("", new).strip() + f" (원장 범위: {issue['entry']['label'] or '경계 미기록'})"
        else:
            for raw in issue["quantities"]:
                new = new.replace(raw, "[확인 보류]")
    records = [{"area": area, "sentence": text, "reason": i["reason"], "action": "replaced", "model_text": text,
                "output": new, **({"code": i["code"]} if i.get("code") else {}),
                **({"numbers": i["numbers"]} if i.get("numbers") else {})} for i in issues]
    return prefix + new, records + marks


def _review_table(rows: list[str], entries, facts, area, chunks, all_chunks=None) -> tuple[list[str], list[dict[str, Any]]]:
    """생성 표도 같은 계약을 쓴다 — 미확정 값의 칸에 상태·사유를 싣고 `확정` 열 표기를 떼며, 근거 없는 수량 칸은 보류한다.

    시스템이 넣은 원장 표(`공시 상태` 열에 `범위미확정`이 이미 있다)는 바꾸지 않는다.
    """
    from .rag_gates.signals import _CITATION_RE, strip_citation_markers
    out, marks, header_at, touched = [], [], None, False
    for raw in rows:
        line = raw.strip()
        cells = line.strip("|").split("|")
        if re.fullmatch(r"\|?\s*:?-{2,}.*", line) or not re.search(r"\d", strip_citation_markers(line)):
            if header_at is None and not re.fullmatch(r"\|?\s*:?-{2,}.*", line):
                header_at = len(out)
            out.append(strip_citation_markers(raw))
            continue
        cites = _CITATION_RE.findall(line)
        cited = [chunks[c] for c in cites if c in chunks]
        legacy = [(all_chunks or chunks)[c] for c in cites if c in (all_chunks or chunks)]
        clean = [strip_citation_markers(c).strip() for c in cells]
        row_text = " ".join(c for c in clean if c)
        issues, notes, row_marks = _sentence_review(row_text, entries, facts, area, cited if cites else None, legacy)
        for issue in issues:
            if issue["reason"] in ("source_only_stated", "scope_widened"):
                e = issue["entry"]
                status = ("범위 미확정: 원문 범위로만 확인된 참고값" if issue["reason"] == "source_only_stated"
                          else f"범위 주의: 원장 범위 {e['label'] or '경계 미기록'}")
                k = next((i for i, c in enumerate(clean) if re.search(r"\d", c) and not _ITEM_CODE_RE.fullmatch(c)),
                         len(clean) - 1)
                clean[k] = f"{clean[k]} [{status}]"
                touched = True
            else:
                for i, c in enumerate(clean):
                    if any(q in c for q in issue["quantities"]):
                        clean[i] = "[확인 보류]"
            marks.append({"area": area, "sentence": row_text, "reason": issue["reason"], "action": "replaced",
                          "model_text": line, "output": "| " + " | ".join(c for c in clean if c) + " |",
                          **({"code": issue["code"]} if issue.get("code") else {}),
                          **({"numbers": issue["numbers"]} if issue.get("numbers") else {})})
        if notes:
            clean[-1] = _with_notes(clean[-1], notes)
        marks += row_marks
        out.append("| " + " | ".join(c for c in clean if c) + " |")
    if touched and header_at is not None:
        # 미확정 값이 든 표의 `확정 실적` 같은 열 표기에서 `확정`을 뗀다(그 열의 값이 모두 확정이 아니다).
        out[header_at] = re.sub(r"(?<!미)확정\s*", "", out[header_at])
    return out, marks


def _structured_facts(output: Any, area: str | None, docs: dict[str, Any]) -> list:
    from .report_claims import facts_from_rows
    facts = _ledger_facts(_ledger_entries(output, area))
    for doc in docs.values():
        if (getattr(doc, "meta", None) or {}).get("source") == "source_facts":
            facts += facts_from_rows(doc.meta.get("facts") or [])
    return facts


def annotate_generated_text(output: Any, area: str, verify: Any) -> tuple[str, list[dict[str, Any]]]:
    """LLM 영역 본문의 문장·제목·표를 원장·원문 확인 수치·인용 근거와 대조한다(PR71 검토 R1·R2).

    1. 수량(숫자+단위)이 인용 근거·원문 확인 수치에서 같은 값·역할·날짜로 확인되지 않으면 그 문장을 확인 보류
       문구로 바꾸고 원문 확인 값을 함께 적는다(날짜·항목 코드·인용 번호 안의 숫자는 수량이 아니다).
    2. 원장이 '요청 범위 실적 미확정'(SOURCE_ONLY)으로 둔 값을 그 상태 없이 단정한 문장·제목·표 칸은 미확정
       문구로 바꾸거나 상태를 싣는다. 이름·K-ESG 검색어·값+단위(`산재율 0‰`)로 찾고, 부정된 유보는 유보가 아니다.
    3. 원장 범위가 부분값인데 값을 전체·전사·연간·합산으로 넓힌 문장은 원장 범위 문구로 바꾼다.
    판단이 갈리는 경우(목표 문장, 값 없이 범위 낱말만, 단위 없는 숫자)는 `[검토: …]` 표시만 붙인다.
    돌려주는 목록은 감사 기록이다 — 바꾼 문장은 `action="replaced"`와 모델 원문(`model_text`)을 담는다.
    """
    from .rag_gates.signals import _CITATION_RE, _is_structural_line, strip_citation_markers
    from .report_claims import is_pseudo_chunk

    generation = getattr(getattr(verify, "final", None), "generation", None)
    raw = str(getattr(generation, "text", "") or "")
    context = getattr(generation, "context", None)
    if not raw or not hasattr(context, "all_hits"):
        return verify.final_text, []        # 생성 기록이 없는 결과(차단·이전 형식)는 원문 그대로 둔다
    docs = {doc.chunk_id: doc for doc, _ in context.all_hits()}
    # 원장·원문 확인 수치 청크는 구조화 사실(역할·날짜 포함)로 대조한다 — 글자 숫자만으로 통과시키지 않는다.
    chunks = {cid: doc.text for cid, doc in docs.items() if not is_pseudo_chunk(cid)}
    all_chunks = {cid: doc.text for cid, doc in docs.items()}
    entries = _ledger_entries(output, area)
    facts = _structured_facts(output, area, docs)
    marks: list[dict[str, Any]] = []
    out_lines: list[str] = []
    lines = raw.splitlines()
    i = 0
    while i < len(lines):
        raw_line = lines[i]
        line = raw_line.strip()
        if line.startswith("|"):
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            reviewed, found = _review_table(block, entries, facts, area, chunks, all_chunks)
            out_lines += reviewed
            marks += found
            continue
        i += 1
        if line.startswith("#"):
            cites = _CITATION_RE.findall(line)
            text, found = _review_heading(strip_citation_markers(raw_line), entries, facts, area,
                                          [chunks[c] for c in cites if c in chunks],
                                          [all_chunks[c] for c in cites if c in all_chunks])
            out_lines.append(text)
            marks += found
            continue
        if not line or _is_structural_line(line):
            out_lines.append(strip_citation_markers(raw_line) if line else raw_line)
            continue
        line_cites = _CITATION_RE.findall(line)
        pieces = []
        for raw_sentence in _split_sentences(line):
            grouped = [c.strip() for g in _GROUP_CITATION_RE.findall(raw_sentence) for c in g.split(",")]
            cites = (_CITATION_RE.findall(raw_sentence) + grouped) or line_cites
            sentence = strip_citation_markers(_GROUP_CITATION_RE.sub("", raw_sentence)).strip()
            if not sentence:
                continue
            # 인용 표기가 없는 문장은 인용 관계를 알 수 없다 — 수량 근거는 생성 문맥 전체에서 찾는다(인용 번호를
            # 빠뜨린 것과 근거가 없는 것을 가른다). 단위 없는 숫자의 기존 글자 대조는 그대로 인용 근거만 본다.
            support = [chunks[c] for c in cites if c in chunks] if cites else list(chunks.values())
            issues, notes, found = _sentence_review(sentence, entries, facts, area, support,
                                                    [all_chunks[c] for c in cites if c in all_chunks])
            text, records = _apply(sentence, issues, notes, found, area)
            marks += records
            pieces.append(text)
        out_lines.append(" ".join(pieces))
    return "\n".join(out_lines), marks


def annotate_summary_text(output: Any, text: str) -> tuple[str, list[dict[str, Any]]]:
    """인용 없는 LLM 요약(Executive Summary)도 같은 계약을 쓴다 — 미확정 값의 확정 단정·부분값의 범위 확대는
    바꾸고, 판단이 갈리는 경우만 표시한다. 인용이 없으므로 수량 근거 대조는 하지 않는다."""
    entries = _ledger_entries(output)
    marks: list[dict[str, Any]] = []
    out_lines = []
    for line in str(text or "").splitlines():
        pieces = []
        for sentence in _split_sentences(line.strip()):
            issues, notes, found = _sentence_review(sentence.strip(), entries, [], None, None)
            text_out, records = _apply(sentence.strip(), issues, notes, found, None)
            marks += records
            pieces.append(text_out)
        out_lines.append(" ".join(pieces) if line.strip() else line)
    return "\n".join(out_lines), marks


def _block_issb(output: Any) -> ReportBlock | None:
    issb = output.issb_gap
    if issb is None or not issb.rows:
        return None
    in_rows = [r for r in issb.rows if r.scope == "in_profile"]
    table_rows = [
        [
            r.kesg_code,
            r.name,
            ", ".join(r.standards) or "—",
            {"disclosed": "공시됨", "missing": "누락", "out_of_scope": "범위 외"}.get(r.status, r.status),
            {"verified": "증빙연결", "self_reported": "자기기재", "missing": "누락", "out_of_scope": "범위 외"}.get(r.evidence_status, r.evidence_status),
        ]
        for r in in_rows
    ]
    missing_names = [r.name for r in in_rows if r.status == "missing"]
    lead = (
        f"프로파일 대상 ISSB/KSSB 연계 항목 {issb.in_profile_total}건 중 "
        f"{issb.in_profile_disclosed}건이 공시되었고 {issb.in_profile_missing}건이 누락되었다."
    )
    if missing_names:
        lead += " 누락 항목: " + ", ".join(missing_names) + "."
    body = lead + "\n\n" + _md_table(
        ["K-ESG", "항목", "ISSB 기준", "공시 상태", "증빙 상태"], table_rows)
    return ReportBlock(id="issb", title="ISSB/KSSB 갭 분석", body_md=body, kind="deterministic")


def _block_disclosure(output: Any) -> ReportBlock | None:
    d6 = output.disclosure
    if d6 is None:
        return None
    lead = f"선택적 공시(cherry-picking) 의심도는 **{d6.score:.2f} ({d6.level})**이다."
    if d6.rationale:
        lead += f" {d6.rationale}"
    parts = [lead]

    if d6.orphan_ratios:
        rows = [[o.ratio_code, o.ratio_name, ", ".join(o.missing_context), o.detail] for o in d6.orphan_ratios]
        parts.append("**고아 비율(분모 없는 유리 지표):**")
        parts.append(_md_table(["비율 코드", "지표", "누락된 맥락", "상세"], rows))

    if d6.omitted_sensitive:
        rows = [[o.code, o.name, o.area, f"{o.sensitivity:.1f}", o.reason] for o in d6.omitted_sensitive]
        parts.append("**민감 항목 누락:**")
        parts.append(_md_table(["코드", "항목", "영역", "민감도", "사유"], rows))

    if not d6.orphan_ratios and not d6.omitted_sensitive:
        parts.append("탐지된 고아 비율·민감 항목 누락 신호는 없다.")

    body = "\n\n".join(p for p in parts if p.strip())
    return ReportBlock(id="disclosure", title="선택적 공시(D6) 점검", body_md=body, kind="deterministic")


def _block_risk(output: Any) -> ReportBlock | None:
    rows = output.risk_rows
    if not rows:
        return None
    headers = ["K-ESG 코드", "값", "D1 수치", "D2 수식어", "D3 의미", "D5 시계열", "종합 위험도", "평가 상태"]
    labels = {"complete": "평가완료", "partial": "부분 평가", "unavailable": "평가불가"}
    table_rows = []
    for row in rows:
        values = ["평가불가" if row.get(h) is None else row[h] for h in headers[:-1]]
        values.append(labels.get(row.get("평가 범위", {}).get("evaluation_status"), "평가 정보 없음"))
        table_rows.append(values)
    body = (
        "증빙(L0 노드)에 연결된 정량 항목별 4축 그린워싱 위험 분해다. "
        "D1=수치 정확성, D2=과장 수식어, D3=의미 괴리, D5=시계열 모순. "
        "기권 축은 종합 점수에서 제외하고 유효 가중치를 재정규화한다. "
        "부분 평가 항목은 추가 근거 확인이 필요하다.\n\n"
        + _md_table(headers, table_rows)
    )
    return ReportBlock(id="risk", title="항목별 4축 리스크", body_md=body, kind="deterministic")


def _block_roadmap(output: Any) -> ReportBlock | None:
    drafts = output.policy_drafts or {}
    if not drafts:
        return None
    parts = ["검증에서 미흡·누락으로 판정된 정책 항목과 자동 생성된 보완 초안이다."]
    for code, draft in drafts.items():
        parts.append(f"### {code}\n\n{draft.strip()}")
    body = "\n\n".join(parts)
    return ReportBlock(id="roadmap", title="개선 로드맵 (정책 보완 초안)", body_md=body, kind="deterministic")


def _block_evidence(output: Any) -> ReportBlock:
    g = output.evidence_graph
    rows = [
        ["Evidence 노드", len(getattr(g, "nodes", []))],
        ["텍스트 노드", len(getattr(g, "text_nodes", []))],
        ["엣지", len(getattr(g, "edges", []))],
    ]
    parts = [
        "본 보고서의 모든 정량 주장은 아래 증빙 그래프에 연결되어 있으며, "
        "영역별 감사 추적(audit trace) JSON으로 수치-증빙 연결을 검증할 수 있다.",
        _md_table(["항목", "수"], rows),
    ]
    if output.trace_paths:
        parts.append("**감사 추적 파일:**")
        for area, path in output.trace_paths.items():
            import os
            parts.append(f"- [{area}] `{os.path.basename(path)}`")
    body = "\n\n".join(parts)
    return ReportBlock(id="evidence", title="증빙 및 감사 추적", body_md=body, kind="deterministic")


# ====================================================================
# 블록 빌더 — LLM (mock 시 결정적 fallback)
# ====================================================================

def _llm_or_fallback(system: str, user: str, fallback: str) -> str:
    """LLM 호출. used_mock이면 결과를 버리고 fallback 반환."""
    try:
        resp = CLIENT.complete(system, user, mock_hint="generate", temperature=0.3)
        if resp.used_mock or not resp.content.strip():
            return fallback
        return resp.content.strip()
    except Exception:
        return fallback


def _block_exec_summary(output: Any) -> ReportBlock:
    name, industry, year = _corp_meta(output)
    risk, band = _overall_risk(output)
    ext = output.extraction
    cov = ext.coverage_pct if ext is not None else 0.0
    ecov = _evidence_cov(ext) if ext is not None else 0.0
    d6 = output.disclosure
    issb = output.issb_gap

    facts = {
        "기업": name,
        "업종": industry,
        "연도": year,
        "K-ESG_커버리지_pct": round(cov, 1),
        "증빙_연결_커버리지_pct": round(ecov, 1),
        "종합_위험도": round(risk, 1) if risk is not None else None,
        "영역별_평가범위": {a: v.final.detection.risk_vector.aggregate if v.final.detection.risk_vector else {} for a, v in output.sections.items()},
        "위험_밴드": band,
        "선택적공시_의심도": round(d6.score, 2) if d6 else None,
        "선택적공시_수준": d6.level if d6 else None,
        "ISSB_누락": issb.in_profile_missing if issb else None,
        "영역별_위험도": {a: round(v.final_score, 1) if v.final_score is not None else None for a, v in output.sections.items()},
    }

    fallback = (
        f"{name}의 K-ESG 공시 커버리지는 {cov:.1f}%(증빙 연결 기준 {ecov:.1f}%)이며, 종합 그린워싱 위험도는 "
        f"{format_score(risk)}({band}) 수준이다. "
        + (f"선택적 공시 의심도는 {d6.score:.2f}({d6.level})로 평가되었다. " if d6 else "")
        + (f"ISSB/KSSB 연계 항목 중 {issb.in_profile_missing}건이 누락되었다. " if issb else "")
        + "정량 근거가 확보된 영역은 신뢰도가 높으나, 누락·고아 비율 항목은 추가 증빙 보완이 필요하다."
    )

    system = (
        "당신은 ESG 공시 신뢰성 평가 보고서의 Executive Summary를 쓰는 전문가다. "
        "주어진 수치만 사용하고, 표·제목·불릿 없이 5~7문장의 서술형 한 단락으로만 작성하라. "
        "과장 수식어를 피하고 경영진이 한눈에 읽을 수 있게 요약하라. "
        "평가불가·부분 평가·기권 축은 반드시 명시하고, 근거 없는 검증 통과나 저위험으로 바꾸지 마라."
    )
    user = (
        "다음 분석 결과를 바탕으로 Executive Summary를 작성하라.\n"
        + json.dumps(facts, ensure_ascii=False)
    )
    body = _llm_or_fallback(system, user, fallback)
    body, marks = annotate_summary_text(output, body)
    return ReportBlock(id="exec_summary", title="Executive Summary", body_md=body, kind="llm",
                       reviews=[{**m, "area": "summary"} for m in marks])


def _block_benchmark(output: Any) -> ReportBlock | None:
    name, industry, year = _corp_meta(output)
    stats = _load_industry_stats(industry)
    rep = output.report
    if stats is None or rep is None:
        return None

    metrics = stats.get("metrics", {})
    # 산업 평균 지표명 → 자사 대응 코드 추정은 단순화: 산업 metrics 키를 그대로 비교 표기
    rows = []
    for k, v in metrics.items():
        rows.append([k, _fmt(v)])
    issues = "; ".join(stats.get("key_issues", []))

    table = _md_table(["산업 평균 지표", "값"], rows)

    fallback = (
        f"{industry} 업종의 산업 평균 지표와 핵심 이슈를 기준으로 볼 때, {name}의 공시는 "
        f"업계 공통 관심사({issues})를 중심으로 점검될 필요가 있다. 자사 수치는 본 보고서의 "
        f"각 영역 핵심 지표 표를 참조하라."
    )
    system = (
        "당신은 ESG 업종 벤치마크 해설을 쓰는 애널리스트다. 주어진 산업 평균 지표와 자사 "
        "데이터를 비교해 표·제목 없이 3~5문장의 서술형 한 단락으로만 작성하라. 데이터에 없는 "
        "수치를 지어내지 말 것."
    )
    user = (
        f"회사: {name} ({industry}, {year}년)\n"
        f"산업 평균 지표(JSON): {json.dumps(metrics, ensure_ascii=False)}\n"
        f"산업 핵심 이슈: {issues}\n"
        f"자사 K-ESG 데이터(JSON): {json.dumps(rep.kesg_data, ensure_ascii=False)}\n\n"
        "위 자사 수치와 산업 평균을 비교해 해설하라."
    )
    narrative = _llm_or_fallback(system, user, fallback)
    body = narrative + ("\n\n" + table if table else "")
    return ReportBlock(id="benchmark", title="업종 벤치마크 비교", body_md=body, kind="llm")


# ====================================================================
# 진입점
# ====================================================================

def assemble_report(output: Any) -> ReportDoc:
    """PipelineOutput → 통합 ReportDoc.

    블록 순서: 표지 → Exec Summary → E/S/G 본문 → 벤치마크 → ISSB 갭 →
    선택적 공시 → 4축 리스크 → 개선 로드맵 → 증빙 부록.
    데이터가 없는 블록(None 반환)은 자동 생략된다.
    """
    name, industry, year = _corp_meta(output)
    setattr(output, "_generated_at", datetime.date.today().isoformat())

    candidates: list[ReportBlock | None] = [_block_cover(output), _block_exec_summary(output),
                                          _block_source_review(output)]
    for area in output.requested_areas or list(output.sections.keys()):
        candidates.append(_block_esg(output, area))
    candidates += [
        _block_benchmark(output),
        _block_issb(output),
        _block_disclosure(output),
        _block_risk(output),
        _block_roadmap(output),
        _block_evidence(output),
    ]
    blocks = [b for b in candidates if b is not None]

    risk, band = _overall_risk(output)
    meta = {
        "coverage_pct": output.extraction.coverage_pct if output.extraction else None,
        "evidence_coverage_pct": _evidence_cov(output.extraction) if output.extraction else None,
        "overall_risk": risk,
        "overall_band": band,
        "d6_score": output.disclosure.score if output.disclosure else None,
        "issb_missing": output.issb_gap.in_profile_missing if output.issb_gap else None,
        "llm_blocks": [b.id for b in blocks if b.kind == "llm"],
        "review_findings": [finding.to_dict() for finding in getattr(output, "review_findings", [])],
        # 생성 본문 대조 감사 기록(PR71 검토 R1): 바꾼 문장의 모델 원문·사유, 표시만 붙인 문장.
        "body_reviews": [{"block": b.id, **m} for b in blocks for m in b.reviews],
    }
    return ReportDoc(
        corp_name=name,
        industry=industry,
        report_year=year,
        generated_at=output_generated_at(output),
        blocks=blocks,
        meta=meta,
    )
