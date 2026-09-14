"""L3 — 5축 위험 분해 + 사내 규정 검증 (v15 확장).

기존 D1~D5(수치·수식어·의미·업종·시계열)는 유지하되,
v15에서 두 가지를 강화한다:

  · D1(수치 일치성) — DART 노드뿐 아니라 OCR 증빙 노드, 그리고
    DART↔OCR cross_check 엣지까지 활용해 '증빙된 수치인가'를 판정.
  · 신규 P축(Policy Compliance) — 사내 규정집 TextNode를 K-ESG 체크리스트와
    LLM 대조하여 누락 조항을 잡는다(방향성 3-검증). 5축과 분리된 정성 검증 트랙.

여기서는 핵심 로직 뼈대(D1 + Policy)만 구현 인터페이스로 제공한다.
D2~D5는 기존 esgenie/layer3_detect.py 로직을 재사용한다고 가정.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..config import ABSTAIN_ENABLED
from ..schemas import AxisScore   # 공유 스키마 (v15 통합 시 중복 정의 제거)
from .evidence_graph import EvidenceGraph, EvidenceNode, TextNode
from . import prompts


# ====================================================================
# 결과 스키마 — AxisScore는 esgenie.schemas와 공유
# ====================================================================

@dataclass
class PolicyFinding:
    requirement: str
    status: str                        # met | insufficient | missing
    evidence_quote: str | None
    gap_comment: str
    suggested_fix: str


@dataclass
class PolicyAuditResult:
    kesg_code: str
    findings: list[PolicyFinding]
    passed: bool
    source_files: list[str]


# ====================================================================
# D1 — 수치 일치성 (증빙 기반)
# ====================================================================

def detect_d1_numeric(
    sentence: str,
    kesg_code: str | None,
    graph: EvidenceGraph,
    *,
    tolerance_pct: float = 2.0,
) -> AxisScore:
    """SSOT keeps its tolerance/risk scale and shares D1 ownership and coverage."""
    from ..layer3_detect import _compare_numeric_claims, _numeric_axis
    records = _compare_numeric_claims(sentence, graph, _ssot_claims(sentence, kesg_code),
                                      tolerance=tolerance_pct / 100)
    mismatches = sum(r['status'] == 'compared' and r['reason'] == 'mismatch' for r in records)
    score = min(.5 + .1 * mismatches, 1.0) if mismatches else 0.0
    if not mismatches and any(r['status'] == 'compared' for r in records) and kesg_code:
        score = _cross_check_risk(kesg_code, graph)
    axis = _numeric_axis(records, score)
    axis.evidence = list(dict.fromkeys(axis.evidence + [f for r in records if r["status"] == "compared" for f in r.get("evidence_files", [])]))
    return axis


# ====================================================================
# P축 — 사내 규정 검증 (LLM gap detection)
# ====================================================================

def audit_policy_documents(
    kesg_code: str,
    graph: EvidenceGraph,
    llm: Any,
) -> PolicyAuditResult:
    """규정집 TextNode를 K-ESG 체크리스트와 LLM 대조 → 누락 조항 검출.

    방향성 3-검증의 핵심. 결과는 인라인 코멘트(suggested_fix 포함)로 반환되어
    L5 산출물과 UI 양쪽에서 '근로자 대표 참여 문구 누락' 식으로 표시된다.
    """
    checklist = prompts.POLICY_CHECKLISTS.get(kesg_code)
    if not checklist:
        return PolicyAuditResult(kesg_code, [], passed=True, source_files=[])

    from ..survey import is_survey
    text_nodes = [n for n in graph.text_nodes_by_code(kesg_code) if not is_survey(n)]
    if not text_nodes:
        # 규정 자체가 없음 → 전 항목 missing 처리
        findings = [
            PolicyFinding(req, "missing", None, "관련 규정 미제출", "규정 신규 작성 필요")
            for req in checklist
        ]
        return PolicyAuditResult(kesg_code, findings, passed=False, source_files=[])

    policy_text = "\n".join(t.text for t in text_nodes)
    source_files = sorted({t.source_file for t in text_nodes})

    resp = llm.complete(
        system=prompts.POLICY_AUDIT_SYSTEM,
        user=prompts.POLICY_AUDIT_PROMPT.format(
            kesg_code=kesg_code,
            kesg_name=_kesg_name(kesg_code),
            checklist="\n".join(f"- {c}" for c in checklist),
            policy_text=policy_text[:6000],
        ),
        json_mode=True,
        temperature=0.0,
    )
    data = _safe_json(resp.content)
    findings = [
        PolicyFinding(
            requirement=f.get("requirement", ""),
            status=f.get("status", "insufficient"),
            evidence_quote=f.get("evidence_quote"),
            gap_comment=f.get("gap_comment", ""),
            suggested_fix=f.get("suggested_fix", ""),
        )
        for f in data.get("findings", [])
    ]
    passed = bool(data.get("overall", {}).get("pass", False))
    return PolicyAuditResult(kesg_code, findings, passed=passed, source_files=source_files)


def draft_missing_policy(
    kesg_code: str,
    audit: PolicyAuditResult,
    corp_name: str,
    industry: str,
    llm: Any,
) -> str:
    """검증에서 발견된 누락/미흡 조항을 표준 조문 초안으로 자동 생성(방향성 3-생성)."""
    gaps = [f for f in audit.findings if f.status in ("insufficient", "missing")]
    if not gaps:
        return ""
    gap_text = "\n".join(f"- [{f.status}] {f.requirement}: {f.gap_comment}" for f in gaps)
    resp = llm.complete(
        system=prompts.POLICY_DRAFT_SYSTEM,
        user=prompts.POLICY_DRAFT_PROMPT.format(
            corp_name=corp_name, industry=industry,
            kesg_code=kesg_code, kesg_name=_kesg_name(kesg_code), gaps=gap_text,
        ),
        temperature=0.4,
    )
    return resp.content


# ====================================================================
# D2 · D3 · D5 — v10 재사용 래퍼
# ====================================================================

def detect_d2_modifier(sentence: str, industry_module=None) -> AxisScore:
    """D2: 모호어/최상급 수식어 밀도 — 코어 로직 재사용 (공유 스키마라 그대로 반환)."""
    from ..layer3_detect import score_d2_modifier
    return score_d2_modifier(sentence, industry_module)


def detect_d3_semantic(
    sentence: str,
    retrieved_chunks: list[dict[str, Any]] | None = None,
) -> AxisScore:
    """D3: RAG 청크와 코사인 유사도 역수 — 코어 로직 재사용."""
    from ..layer3_detect import score_d3_semantic
    return score_d3_semantic(sentence, retrieved_chunks or [])


def detect_d5_timeseries(sentence: str, graph: EvidenceGraph) -> AxisScore:
    """D5: 시계열 엣지 방향과 문장 주장 비교 — 코어 로직 재사용.

    SSOT EvidenceGraph는 코어와 엣지 스키마가 호환되므로 직접 전달 가능.
    """
    from ..layer3_detect import score_d5_timeseries
    return score_d5_timeseries(sentence, graph)


def detect_risk_axes(
    sentence: str,
    kesg_code: str | None,
    graph: EvidenceGraph,
    retrieved_chunks: list[dict[str, Any]] | None = None,
    industry_module=None,
) -> dict[str, AxisScore]:
    """4축(D1·D2·D3·D5) 종합 위험 점수 계산 (v15 통합 진입점).

    Returns: {"D1": AxisScore, "D2": AxisScore, "D3": AxisScore, "D5": AxisScore,
              "aggregate": AxisScore}
    """
    d1 = detect_d1_numeric(sentence, kesg_code, graph)
    d2 = detect_d2_modifier(sentence, industry_module)
    d3 = detect_d3_semantic(sentence, retrieved_chunks)
    d5 = detect_d5_timeseries(sentence, graph)

    from ..layer3_detect import _build_risk_vector
    rv = _build_risk_vector(d1, d2, d3, d5)
    aggregate = AxisScore(
        score=rv.risk_score if rv.risk_score is not None else 0.0,
        evidence=d1.evidence + d3.evidence,
        detail=f"{rv.evaluation_label} · 유효 축: {', '.join(rv.aggregate['evaluated_axes'])}",
        abstain=rv.risk_score is None,
        abstain_reason="no_evidence" if rv.risk_score is None else None,
        evaluation=rv.aggregate)

    return {"D1": d1, "D2": d2, "D3": d3, "D5": d5, "aggregate": aggregate}


# ====================================================================
# 내부 헬퍼
# ====================================================================

from ..numeric_tokens import TOKEN_START, CANDIDATE_NUMBER, TOKEN_END, parse_number

_SCALE = {"만": 1e4, "억": 1e8, "천": 1e3}
# Preserve the SSOT unit vocabulary; numeric grammar has one implementation.
_NUM_RE = re.compile(TOKEN_START + rf"(?P<num>{CANDIDATE_NUMBER})" + TOKEN_END
    + r"\s*(?P<scale>만|억|천)?\s*(?P<unit>tCO2eq|tCO2|kWh|MWh|GWh|TJ|GJ|MJ|kg|ton|톤|%p|%|원|건|명)?")


def _ssot_claims(sentence: str, kesg_code: str | None):
    from ..layer3_detect import NumericClaim, _match_topic_near, _sentence_topic_codes
    out = []
    for m in _NUM_RE.finditer(sentence):
        raw, scale, unit = m.group('num', 'scale', 'unit')
        val = parse_number(raw)
        if unit is None and scale is None:
            if val is not None and 1900 <= val <= 2100 and '.' not in raw:
                continue
            if ',' not in raw and '.' not in raw:
                continue
        if val is not None and scale:
            val *= _SCALE[scale]
        topic, code = _match_topic_near(sentence, m.start(), m.end())
        # The caller's explicit metric is only a fallback for unnamed single-metric
        # SSOT rows. It must not override ambiguous named metrics in prose.
        if code is None and not _sentence_topic_codes(sentence):
            code = kesg_code
        out.append(NumericClaim(m.group().rstrip(), val, unit or '', topic, code,
                                sentence, m.start(), m.end(),
                                'invalid_number' if val is None or val < 0 else None))
    return out


def _extract_numbers(sentence: str) -> list[tuple[float, str | None]]:
    return [(c.number, c.unit or None) for c in _ssot_claims(sentence, None) if c.number is not None]


def _find_matching_node(
    claim: float,
    claim_unit: str | None,
    nodes: list[EvidenceNode],
    tol: float,
) -> EvidenceNode | None:
    """±tol% 이내 + 단위 호환 노드 탐색 (128,400원 ≠ 128,400 kWh)."""
    from ..rag_gates.units import normalize_unit, convert_to_common
    from .selection import finite_number
    for n in nodes:
        if finite_number(n.value) is None or finite_number(claim) is None:
            continue
        cu, nu = normalize_unit(claim_unit or "") or claim_unit, normalize_unit(n.unit) or n.unit
        comparable = convert_to_common(claim, cu, nu) if cu else claim
        if comparable is None:
            continue
        if n.value == 0:
            if comparable == 0:
                return n
        elif abs(comparable - n.value) / abs(n.value) * 100 <= tol:
            return n
    return None


def _cross_check_risk(kesg_code: str, graph: EvidenceGraph) -> float:
    """DART↔OCR cross_check 엣지 오차가 크면 위험 가산."""
    risk = 0.0
    for e in graph.edges:
        if e.edge_type != "cross_check":
            continue
        if kesg_code in e.source_id or kesg_code in e.target_id:
            mobj = re.search(r"([0-9\.]+)%", e.detail)
            if mobj and float(mobj.group(1)) > 5.0:
                risk = max(risk, 0.4)
    return risk


def _safe_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0)) if m else {}


def _kesg_name(code: str) -> str:
    """K-ESG 코드 → 항목명. kesg_items 단일 출처에서 가져온다.

    이 이름은 POLICY_AUDIT_PROMPT의 kesg_name으로 LLM에 그대로 들어가므로,
    체크리스트와 어긋나면 판정 자체가 오염된다. 하드코딩 표를 쓰던 시절
    S-4-1(안전보건 추진체계)이 '정보보호 정책'으로 넘어갔다. 2026-07-28 정정.
    """
    from ..knowledge.kesg_items import kesg_name as _canonical

    return _canonical(code)
