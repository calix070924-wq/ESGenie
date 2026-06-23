"""Pre-generation retrieval gate."""
from __future__ import annotations

import re
from typing import Any

from ..config import RAG_MAX_TIER, RAG_R1_MIN, RAG_R2_MIN, RAG_R4_MIN
from ..schemas import GateDecision, RetrievalDecision

_AREA_TERMS: dict[str, tuple[str, ...]] = {
    "E": ("온실가스", "배출", "재생에너지", "폐기물", "용수", "환경", "에너지", "scope", "scope1", "scope2"),
    "S": ("정규직", "이직률", "여성", "산업재해", "재해율", "정보보호", "안전"),
    "G": ("사외이사", "이사회", "출석률", "윤리", "감사", "지배구조", "배당", "배당성향", "현금배당", "주주환원"),
}
_YEAR_RE = re.compile(r"(19|20)\d{2}년?|\b(19|20)\d{2}\b")
_NUMBER_RE = re.compile(r"\d")
_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")
_STOP_TERMS = {
    "수치", "실적", "성과", "지표", "기준", "관련", "현황", "데이터", "운영",
}


def evaluate_retrieval(
    area: str,
    corp_hits: list[tuple[Any, float]],
    *,
    query: str = "",
    tier: int = 0,
    max_tier: int = RAG_MAX_TIER,
    bm25_hits: list[tuple[Any, float]] | None = None,
    embed_hits: list[tuple[Any, float]] | None = None,
    queries_tried: list[str] | None = None,
) -> RetrievalDecision:
    if not corp_hits:
        return RetrievalDecision(
            decision=_failure_decision(tier, max_tier, hard=1, soft=0),
            tier=tier,
            top1_score=0.0,
            field_coverage={"area": False, "value": False, "period": False, "source": False, "query": False},
            hard_fails=["R0_no_corp_hits"],
            soft_flags=[],
            chunk_ids=[],
            scores=[],
            queries_tried=queries_tried or [],
        )

    top_doc = corp_hits[0][0]
    top_text = top_doc.text
    report_year = str(top_doc.meta.get("report_year") or "").strip()
    field_coverage = {
        "area": _contains_area_term(area, top_text),
        "value": _has_numeric_evidence(top_doc),
        "period": bool(_YEAR_RE.search(top_text)) or bool(report_year),
        "source": bool(top_doc.chunk_id or top_doc.meta.get("source") or top_doc.meta.get("id")),
        "query": _contains_query_term(query, top_text),
    }

    top1_score = float(corp_hits[0][1])
    tail_score = float(corp_hits[-1][1]) if len(corp_hits) > 1 else 0.0
    score_margin = max(0.0, top1_score - tail_score)
    overlap = _method_overlap(bm25_hits or [], embed_hits or [])
    hard_fails: list[str] = []
    soft_flags: list[str] = []
    if top1_score < RAG_R1_MIN:
        hard_fails.append("R1_low_top1_score")
    if score_margin < RAG_R2_MIN:
        soft_flags.append("R2_low_margin")
    if not field_coverage["area"]:
        hard_fails.append("R3_area_keyword_missing")
    if query and not field_coverage["query"]:
        hard_fails.append("R3_query_keyword_missing")
    if not field_coverage["value"]:
        hard_fails.append("R3_numeric_evidence_missing")
    if not field_coverage["period"]:
        soft_flags.append("R3_period_missing")
    if not field_coverage["source"]:
        soft_flags.append("R3_source_missing")
    if bm25_hits is not None and embed_hits is not None and overlap < RAG_R4_MIN:
        soft_flags.append("R4_low_method_overlap")

    strong_structured_hit = _is_strong_structured_hit(
        top_doc=top_doc,
        top1_score=top1_score,
        field_coverage=field_coverage,
        hard_fails=hard_fails,
        soft_flags=soft_flags,
    )
    decision = GateDecision.ACCEPT.value
    if hard_fails or (len(soft_flags) > 1 and not strong_structured_hit):
        decision = _failure_decision(tier, max_tier, hard=len(hard_fails), soft=len(soft_flags))
    return RetrievalDecision(
        decision=decision,
        tier=tier,
        top1_score=top1_score,
        field_coverage=field_coverage,
        hard_fails=hard_fails,
        soft_flags=soft_flags,
        chunk_ids=[doc.chunk_id for doc, _ in corp_hits],
        scores=[float(score) for _, score in corp_hits],
        score_margin=round(score_margin, 4),
        method_overlap=round(overlap, 4),
        queries_tried=queries_tried or [],
    )


def _contains_area_term(area: str, text: str) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in _AREA_TERMS.get(area, ()))


def _contains_query_term(query: str, text: str) -> bool:
    if not query.strip():
        return True
    normalized_text = _normalize_text(text)
    terms = _normalize_terms(query)
    if len(terms) >= 2 and terms[0] not in normalized_text:
        terms = terms[1:]
    terms = [term for term in terms if len(term) >= 2 and term not in _STOP_TERMS]
    if not terms:
        return True
    matched = [term for term in terms if term in normalized_text]
    required = 2 if len(terms) >= 3 else 1
    return len(set(matched)) >= required


def _normalize_terms(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in _TOKEN_RE.findall(text):
        term = _normalize_text(raw)
        if not term or term in seen:
            continue
        seen.add(term)
        out.append(term)
    return out


def _normalize_text(text: str) -> str:
    return "".join(_TOKEN_RE.findall(text))


def _has_numeric_evidence(doc: Any) -> bool:
    text = re.sub(r"^\[[^\]]+\]\s*", "", doc.text).strip()
    if "수치:" in text:
        text = text.split("수치:", 1)[1]
    return bool(_NUMBER_RE.search(text))


def _is_strong_structured_hit(
    *,
    top_doc: Any,
    top1_score: float,
    field_coverage: dict[str, bool],
    hard_fails: list[str],
    soft_flags: list[str],
) -> bool:
    if hard_fails:
        return False
    if top_doc.meta.get("source") != "dart_struct":
        return False
    if top1_score < max(RAG_R1_MIN, 0.95):
        return False
    essentials = ("area", "value", "source", "query")
    if not all(field_coverage.get(key, False) for key in essentials):
        return False
    allowed_soft_flags = {"R2_low_margin", "R3_period_missing", "R4_low_method_overlap"}
    return all(flag in allowed_soft_flags for flag in soft_flags)


def _method_overlap(
    bm25_hits: list[tuple[Any, float]],
    embed_hits: list[tuple[Any, float]],
) -> float:
    bm25_ids = {doc.chunk_id for doc, _ in bm25_hits[:5]}
    embed_ids = {doc.chunk_id for doc, _ in embed_hits[:5]}
    union = bm25_ids | embed_ids
    if not union:
        return 0.0
    return len(bm25_ids & embed_ids) / len(union)


def _failure_decision(tier: int, max_tier: int, *, hard: int, soft: int) -> str:
    if hard >= 1 or soft >= 2:
        return GateDecision.ESCALATE.value if tier < max_tier else GateDecision.HUMAN.value
    return GateDecision.ACCEPT.value
