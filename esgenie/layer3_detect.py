"""Layer 3 — 그린워싱 탐지.

v10 구조:
- 기존 detect(text, report) → DetectionResult 는 _legacy 로직을 래핑해 하위 호환 유지
- 신규 detect_risk_vector(
      claim_sentence, evidence_graph, retrieved_chunks, industry_stats, industry_module
  ) → RiskVector (D1~D5 분해 점수)
- DetectionResult에 risk_vector 필드 추가 (기본값 None)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .config import (
    D1_THRESHOLD, D2_THRESHOLD, D3_THRESHOLD, D5_THRESHOLD,
    D_WEIGHTS, RISK_LEVEL_THRESHOLDS,
)
from .dart_client import CompanyReport
from .embeddings import VectorIndex
from .knowledge.greenwash_lexicon import vague_matches
from .schemas import AxisScore, RiskVector

# ---- 수치 주장 패턴 --------------------------------------------------------
_NUMBER_PATTERN = re.compile(
    r"(?P<num>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*"
    r"(?P<unit>%|억원|조원|tCO2eq|만\s*tCO2eq|톤|ton|TJ|%p|건|명|배)",
)
_KEYWORD_MAP = {
    "재생에너지": ["renewable", "E-4-2"],
    "온실가스":   ["ghg", "E-3-1"],
    "탄소":       ["ghg", "E-3-1"],
    "Scope":      ["ghg", "E-3-1"],
    "배출":       ["ghg", "E-3-1"],
    "폐기물":     ["waste", "E-6-1"],
    "재활용":     ["waste_recycle", "E-6-2"],
    "용수":       ["water", "E-5-1"],
    "여성 이사":  ["board_female", "G-1-4"],
    "이사회 성별":["board_female", "G-1-4"],
    "여성":       ["female", "S-3-1"],
    "정규직":     ["regular", "S-2-2"],
    "이직률":     ["turnover", "S-2-3"],
    "재해율":     ["safety", "S-4-2"],
    "사외이사":   ["outside_director", "G-1-2"],
    "출석률":     ["attendance", "G-2-1"],
    # 주의: "재생에너지"가 먼저 매칭되도록 "에너지"는 사전 끝에 둔다 (동순위 시 선순위 유지)
    "에너지":     ["energy", "E-4-1"],
}

# 업종별 벤치마크 메트릭 키 → K-ESG 코드 대응
# ---- 데이터클래스 -----------------------------------------------------------

@dataclass
class NumericClaim:
    raw: str
    number: float
    unit: str
    topic: str | None
    matched_code: str | None
    sentence: str


@dataclass
class ClaimCheck:
    claim: NumericClaim
    dart_value: float | None
    dart_unit: str | None
    delta_pct: float | None
    verdict: str   # ok | mismatch | unverifiable | approximate


@dataclass
class DetectionResult:
    text: str
    sentences: list[str]
    numeric_claims: list[NumericClaim]
    claim_checks: list[ClaimCheck]
    vague_phrases: list[dict[str, Any]]
    semantic_similarity: float
    risk_score: float
    components: dict[str, float] = field(default_factory=dict)
    highlights: list[dict[str, Any]] = field(default_factory=list)
    risk_vector: RiskVector | None = None   # v10 신설


# ---- 공통 헬퍼 --------------------------------------------------------------

def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?。\n])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _normalize_number(num: str, unit: str) -> tuple[float, str]:
    n = float(num.replace(",", ""))
    u = unit.replace(" ", "")
    if u.startswith("만"):
        n *= 10_000
        u = u[1:]
    return n, u


def _match_topic_near(sentence: str, span_start: int, span_end: int, window: int = 25) -> tuple[str | None, str | None]:
    s = max(0, span_start - window)
    e = min(len(sentence), span_end + window)
    before = sentence[s:span_start]
    after  = sentence[span_end:e]

    best_before: tuple[int, tuple[str | None, str | None]] | None = None
    for kw, (topic, code) in _KEYWORD_MAP.items():
        idx = before.rfind(kw)
        if idx < 0:
            continue
        dist = len(before) - (idx + len(kw))
        if best_before is None or dist < best_before[0]:
            best_before = (dist, (topic, code))
    if best_before:
        return best_before[1]

    best_after: tuple[int, tuple[str | None, str | None]] | None = None
    for kw, (topic, code) in _KEYWORD_MAP.items():
        idx = after.find(kw)
        if idx < 0:
            continue
        if best_after is None or idx < best_after[0]:
            best_after = (idx, (topic, code))
    if best_after:
        return best_after[1]

    for kw, (topic, code) in _KEYWORD_MAP.items():
        if kw in sentence:
            return topic, code
    return None, None


def _norm_unit(u: str | None) -> str:
    return (u or "").replace(" ", "").lower()


# ---- 단위 호환성 ------------------------------------------------------------
# D1이 단위를 무시하고 값만 비교하면 "95.8 톤"이 "95.8 %" 노드와 일치 판정되거나,
# "% 목표치"가 절대량(tCO2eq) 노드와 비교되는 오류가 생긴다.

_UNIT_ALIASES: dict[str, str] = {
    "톤": "ton", "t": "ton",
    "tco2": "tco2eq", "tco₂eq": "tco2eq",
    "퍼센트": "%", "percent": "%",
    "킬로와트시": "kwh",
}


def canon_unit(u: str | None) -> str:
    """단위 정규화: 공백 제거·소문자·별칭 통일. 빈 문자열 = 단위 미상."""
    s = _norm_unit(u)
    return _UNIT_ALIASES.get(s, s)


def units_compatible(a: str | None, b: str | None) -> bool:
    """두 단위가 비교 가능한가. 어느 한쪽이 미상이면 허용(보수적), 둘 다 있으면 동일해야 함."""
    ca, cb = canon_unit(a), canon_unit(b)
    if not ca or not cb:
        return True
    return ca == cb


# ---- 기존(legacy) 탐지 로직 -------------------------------------------------

def extract_numeric_claims(text: str) -> list[NumericClaim]:
    claims: list[NumericClaim] = []
    for sent in _sentences(text):
        for m in _NUMBER_PATTERN.finditer(sent):
            num_str, unit = m.group("num"), m.group("unit")
            n, u = _normalize_number(num_str, unit)
            topic, code = _match_topic_near(sent, m.start(), m.end())
            claims.append(NumericClaim(
                raw=f"{num_str} {unit}", number=n, unit=u,
                topic=topic, matched_code=code, sentence=sent,
            ))
    return claims


def _dart_numeric_value(report: CompanyReport, code: str) -> tuple[float | None, str | None]:
    entry = report.kesg_data.get(code)
    if not entry:
        return None, None
    v = entry.get("value")
    if isinstance(v, (int, float)):
        return float(v), entry.get("unit")
    return None, entry.get("unit")


def _compare_claim(claim: NumericClaim, report: CompanyReport) -> ClaimCheck:
    if not claim.matched_code:
        return ClaimCheck(claim=claim, dart_value=None, dart_unit=None,
                          delta_pct=None, verdict="unverifiable")
    dart_v, dart_u = _dart_numeric_value(report, claim.matched_code)
    if dart_v is None:
        return ClaimCheck(claim=claim, dart_value=None, dart_unit=dart_u,
                          delta_pct=None, verdict="unverifiable")
    if dart_u and claim.unit and _norm_unit(dart_u) != _norm_unit(claim.unit):
        return ClaimCheck(claim=claim, dart_value=dart_v, dart_unit=dart_u,
                          delta_pct=None, verdict="approximate")
    if dart_v == 0:
        delta = 0.0
    else:
        delta = (claim.number - dart_v) / abs(dart_v) * 100
    verdict = "ok" if abs(delta) <= 10 else "mismatch"
    return ClaimCheck(claim=claim, dart_value=dart_v, dart_unit=dart_u,
                      delta_pct=delta, verdict=verdict)


def detect_vague_phrases(sentences: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in sentences:
        phrases = vague_matches(s)
        if phrases:
            out.append({"sentence": s, "phrases": phrases})
    return out


def _mean_similarity(generated: str, report: CompanyReport) -> float:
    refs = list(report.raw_text_snippets) + [
        f"{e.get('note', '')} {e.get('value')} {e.get('unit', '')}"
        for e in report.kesg_data.values()
    ]
    if not refs:
        return 0.0
    idx = VectorIndex()
    from .embeddings import IndexedDoc
    idx.build([IndexedDoc(text=r, meta={}) for r in refs])
    hits = idx.search(generated, k=min(5, len(refs)))
    if not hits:
        return 0.0
    return float(np.mean([s for _, s in hits]))


def _legacy_score(
    claim_checks: list[ClaimCheck],
    vague: list[dict[str, Any]],
    similarity: float,
) -> tuple[float, dict[str, float]]:
    mismatch_cnt      = sum(1 for c in claim_checks if c.verdict == "mismatch")
    unverifiable_cnt  = sum(1 for c in claim_checks if c.verdict == "unverifiable")
    total_claims      = max(len(claim_checks), 1)
    vague_cnt         = sum(len(v["phrases"]) for v in vague)

    mismatch_score    = min(1.0, mismatch_cnt / total_claims * 2.0)
    unverifiable_score= min(1.0, unverifiable_cnt / total_claims)
    vague_score       = min(1.0, vague_cnt / 4.0)
    similarity_score  = max(0.0, 1.0 - similarity)

    weights = {"numeric_mismatch": 0.45, "unverifiable": 0.10,
               "vague_language": 0.25, "semantic_gap": 0.20}
    composite = (
        weights["numeric_mismatch"] * mismatch_score
        + weights["unverifiable"]   * unverifiable_score
        + weights["vague_language"] * vague_score
        + weights["semantic_gap"]   * similarity_score
    )
    components = {
        "numeric_mismatch": round(mismatch_score * 100, 1),
        "unverifiable":     round(unverifiable_score * 100, 1),
        "vague_language":   round(vague_score * 100, 1),
        "semantic_gap":     round(similarity_score * 100, 1),
    }
    return round(composite * 100, 1), components


def detect(text: str, report: CompanyReport) -> DetectionResult:
    """하위 호환 진입점. layer4_verify / app.py 호출 시 기존 인터페이스 유지."""
    sents  = _sentences(text)
    claims = extract_numeric_claims(text)
    checks = [_compare_claim(c, report) for c in claims]
    vague  = detect_vague_phrases(sents)
    sim    = _mean_similarity(text, report)
    risk, comps = _legacy_score(checks, vague, sim)

    highlights: list[dict[str, Any]] = []
    for c in checks:
        if c.verdict == "mismatch":
            highlights.append({
                "type": "mismatch",
                "sentence": c.claim.sentence,
                "claim": c.claim.raw,
                "dart_value": f"{c.dart_value} {c.dart_unit or ''}",
                "delta_pct": c.delta_pct,
            })
    for v in vague:
        highlights.append({"type": "vague", "sentence": v["sentence"], "phrases": v["phrases"]})

    return DetectionResult(
        text=text, sentences=sents, numeric_claims=claims,
        claim_checks=checks, vague_phrases=vague,
        semantic_similarity=sim, risk_score=risk,
        components=comps, highlights=highlights,
        risk_vector=None,   # v10: 문장별 분석은 detect_risk_vector() 사용
    )


def risk_band(score: float) -> str:
    if score < 25:
        return "LOW"
    if score < 50:
        return "MEDIUM"
    if score < 75:
        return "HIGH"
    return "CRITICAL"


# ---- v10 신설: 5축 위험 분해 ------------------------------------------------

def detect_risk_vector(
    claim_sentence: str,
    evidence_graph: Any | None = None,   # EvidenceGraph | None
    retrieved_chunks: list[dict[str, Any]] | None = None,
    industry_stats: dict[str, Any] | None = None,
    industry_module=None,
    _d3_index: Any | None = None,        # 외부에서 미리 빌드된 VectorIndex (재사용용)
) -> RiskVector:
    """단일 문장에 대한 4축 위험 분해 (D1·D2·D3·D5).

    Args:
        claim_sentence: 분석 대상 문장
        evidence_graph: L0 EvidenceGraph (없으면 D1·D5 스킵)
        retrieved_chunks: L2 RAG 청크 목록 [{"id":..., "text":...}]
        industry_stats:  사용 안 함 (하위 호환용 파라미터 유지)
        industry_module: 업종 모듈. D2 lexicon 확장에 사용, 없으면 전역 동작.
        _d3_index: 미리 빌드된 VectorIndex — 제공 시 D3에서 재빌드 생략

    Returns:
        RiskVector (D1·D2·D3·D5 + aggregate)
    """
    d1 = _score_d1_numeric(claim_sentence, evidence_graph)
    d2 = _score_d2_modifier(claim_sentence, industry_module)
    d3 = _score_d3_semantic(claim_sentence, retrieved_chunks, prebuilt_index=_d3_index)
    d5 = _score_d5_timeseries(claim_sentence, evidence_graph)

    return _build_risk_vector(d1, d2, d3, d5)


# ---- D1: 수치 오차 ----------------------------------------------------------

def _score_d1_numeric(
    sentence: str,
    evidence_graph: Any | None,
) -> AxisScore:
    """claim 숫자 vs L0 노드값 상대 오차."""
    if evidence_graph is None:
        return AxisScore(score=0.0, evidence=[], detail="evidence_graph 없음 — 스킵")

    worst_delta = 0.0
    hit_node_ids: list[str] = []
    details: list[str] = []

    for m in _NUMBER_PATTERN.finditer(sentence):
        num_str, unit = m.group("num"), m.group("unit")
        claim_val, claim_unit = _normalize_number(num_str, unit)
        _, code = _match_topic_near(sentence, m.start(), m.end())
        if not code:
            continue

        nodes = evidence_graph.search_nodes(keywords=[code])
        if not nodes:
            continue

        # 단위 호환 노드만 비교 대상 ("31 %" 주장을 tCO2eq 노드와 비교하지 않음)
        compat = [n for n in nodes if units_compatible(claim_unit, getattr(n, "unit", None))]
        if not compat:
            details.append(f"{code}: claim={claim_val}{claim_unit} — 단위 불일치(노드 단위와 비교 불가, 스킵)")
            continue

        # 가장 최신 노드와 비교
        node = max(compat, key=lambda n: n.period)
        if node.value == 0:
            continue
        delta = abs(claim_val - node.value) / abs(node.value)
        if delta > worst_delta:
            worst_delta = delta
        hit_node_ids.append(node.id)
        details.append(f"{code}: claim={claim_val} vs node={node.value} (Δ={delta:.1%})")

    score = min(1.0, worst_delta / max(D1_THRESHOLD, 1e-9))
    return AxisScore(
        score=round(score, 4),
        evidence=hit_node_ids,
        detail="; ".join(details) if details else "수치 매칭 없음",
    )


# ---- D2: 모호어 밀도 --------------------------------------------------------

def _score_d2_modifier(sentence: str, industry_module=None) -> AxisScore:
    """greenwash_lexicon 모호어/최상급 밀도. industry_module이 있으면 업종 패턴 포함."""
    hits = vague_matches(sentence, industry_module)
    # 문장당 밀도: 히트 수 / threshold 정규화
    density = len(hits) / max(D2_THRESHOLD * 4, 1)   # 4개 = 만점 기준
    score = min(1.0, density)
    return AxisScore(
        score=round(score, 4),
        evidence=[],
        detail=f"모호어 {len(hits)}개: {hits[:5]}" if hits else "모호어 없음",
    )


# ---- D3: 의미 유사도 --------------------------------------------------------

def _score_d3_semantic(
    sentence: str,
    retrieved_chunks: list[dict[str, Any]] | None,
    prebuilt_index: Any | None = None,
) -> AxisScore:
    """SBERT cos-sim(claim, evidence chunk) 역수."""
    if not retrieved_chunks and prebuilt_index is None:
        return AxisScore(score=0.5, evidence=[], detail="retrieved_chunks 없음 — 중립값")

    from .embeddings import IndexedDoc, VectorIndex
    if prebuilt_index is not None:
        idx = prebuilt_index
        docs = idx._docs
    else:
        idx = VectorIndex()
        docs = [IndexedDoc(text=c.get("text", ""), meta={"id": c.get("id", "")}) for c in retrieved_chunks]
        idx.build(docs)
    hits = idx.search(sentence, k=min(3, len(docs)))
    if not hits:
        return AxisScore(score=1.0, evidence=[], detail="유사 청크 없음")

    best_sim = max(s for _, s in hits)
    best_chunk_id = hits[0][0].meta.get("id", "")
    # 유사도가 높을수록 안전 → score = 1 - sim (임계치 기준 정규화)
    raw_risk = max(0.0, D3_THRESHOLD - best_sim) / D3_THRESHOLD
    score = min(1.0, raw_risk)
    return AxisScore(
        score=round(score, 4),
        evidence=[best_chunk_id] if best_chunk_id else [],
        detail=f"최고 cos-sim={best_sim:.3f} (임계치 {D3_THRESHOLD})",
    )


def _extract_claim_value_for_code(sentence: str, code: str) -> float | None:
    """문장에서 특정 K-ESG 코드에 대응하는 수치 추출."""
    for m in _NUMBER_PATTERN.finditer(sentence):
        _, matched_code = _match_topic_near(sentence, m.start(), m.end())
        if matched_code == code:
            val, _ = _normalize_number(m.group("num"), m.group("unit"))
            return val
    return None


# ---- D5: 시계열 모순 --------------------------------------------------------

def _score_d5_timeseries(
    sentence: str,
    evidence_graph: Any | None,
) -> AxisScore:
    """L0 시계열 엣지의 YoY·CAGR 방향과 문장 주장 비교."""
    if evidence_graph is None:
        return AxisScore(score=0.0, evidence=[], detail="evidence_graph 없음 — 스킵")

    contradictions: list[str] = []
    edge_ids: list[str] = []

    for m in _NUMBER_PATTERN.finditer(sentence):
        _, code = _match_topic_near(sentence, m.start(), m.end())
        if not code:
            continue
        # 해당 코드의 timeseries 엣지 검색
        for edge in evidence_graph.edges:
            if code not in edge.target_id:
                continue
            edge_ids.append(edge.target_id)
            if edge.yoy is None:
                continue
            # 문장에 "감소" / "증가" 방향이 엣지 YoY 방향과 일치하는지 확인
            sent_lower = sentence.lower()
            claim_down = any(w in sentence for w in ("감소", "하락", "절감"))
            claim_up   = any(w in sentence for w in ("증가", "상승", "개선"))
            yoy_down = edge.yoy < 0
            yoy_up   = edge.yoy > 0

            if (claim_down and yoy_up) or (claim_up and yoy_down):
                contradictions.append(
                    f"{code} 문장방향={'감소' if claim_down else '증가'} "
                    f"vs YoY={edge.yoy:+.1f}%"
                )

    if not edge_ids:
        return AxisScore(score=0.0, evidence=[], detail="시계열 엣지 없음")

    contradiction_ratio = len(contradictions) / max(len(edge_ids), 1)
    score = min(1.0, contradiction_ratio / max(D5_THRESHOLD, 1e-9))
    return AxisScore(
        score=round(score, 4),
        evidence=edge_ids[:5],
        detail="; ".join(contradictions) if contradictions else "시계열 모순 없음",
    )


# ---- aggregate 계산 ---------------------------------------------------------

def _build_risk_vector(
    d1: AxisScore, d2: AxisScore, d3: AxisScore,
    d5: AxisScore,
) -> RiskVector:
    axes = {
        "D1_numeric":    d1,
        "D2_modifier":   d2,
        "D3_semantic":   d3,
        "D5_timeseries": d5,
    }
    weighted = sum(D_WEIGHTS[k] * ax.score for k, ax in axes.items())
    risk_score = round(weighted, 4)

    if risk_score < RISK_LEVEL_THRESHOLDS["low"]:
        level = "low"
    elif risk_score < RISK_LEVEL_THRESHOLDS["medium"]:
        level = "medium"
    else:
        level = "high"

    top_axis = max(axes, key=lambda k: axes[k].score)

    return RiskVector(
        D1_numeric=d1, D2_modifier=d2, D3_semantic=d3,
        D5_timeseries=d5,
        aggregate={
            "risk_score": risk_score,
            "level":      level,
            "top_axis":   top_axis,
        },
    )


# ---- 공개 별칭 (esgenie.ssot 등 외부 모듈 재사용용) ---------------------------
score_d1_numeric = _score_d1_numeric
score_d2_modifier = _score_d2_modifier
score_d3_semantic = _score_d3_semantic
score_d5_timeseries = _score_d5_timeseries
