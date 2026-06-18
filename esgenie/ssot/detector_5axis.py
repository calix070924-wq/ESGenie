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

# 문장에서 수치 토큰 추출: 1,670만 / 12.3% / 4781 tCO2 등
_NUM_RE = re.compile(r"([0-9][0-9,\.]*)\s*(만|억|천)?\s*(tCO2eq|tCO2|kWh|MJ|ton|%|원|건|명)?")


def detect_d1_numeric(
    sentence: str,
    kesg_code: str | None,
    graph: EvidenceGraph,
    *,
    tolerance_pct: float = 2.0,
) -> AxisScore:
    """문장 속 수치를 Evidence Graph 노드값과 대조.

    판정 단계:
      1) 문장에서 수치 클레임 추출.
      2) kesg_code에 연결된 노드(DART + OCR) 후보 수집.
      3) 각 클레임이 ±tolerance_pct 안에 일치하는 노드가 있는지 확인.
         - 일치 노드가 OCR 증빙(origin=ocr_*)이면 source_file을 evidence에 기록 → 증빙 추적.
         - 근거 노드 자체가 없으면 '근거 부족' = 고위험(0.9).
      4) DART↔OCR cross_check 엣지의 오차가 크면 가산 위험.

    Returns: AxisScore(score, evidence=[node_id|source_file], detail)
    """
    claims = _extract_numbers(sentence)
    if not claims:
        return AxisScore(0.0, [], "수치 클레임 없음")

    if not kesg_code:
        return AxisScore(0.6, [], "수치는 있으나 K-ESG 매핑 없음 → 근거 추적 불가")

    candidates = graph.nodes_by_metric(kesg_code)
    if not candidates:
        return AxisScore(0.9, [], f"{kesg_code} 근거 노드 없음 → 미증빙 수치(고위험)")

    matched_evidence: list[str] = []
    unmatched: list[float] = []
    for claim_val, claim_unit in claims:
        hit = _find_matching_node(claim_val, claim_unit, candidates, tolerance_pct)
        if hit is None:
            unmatched.append(claim_val)
        else:
            tag = hit.source_file or hit.id   # 증빙 파일명 우선(감사 추적)
            matched_evidence.append(tag)

    if unmatched:
        score = min(0.5 + 0.1 * len(unmatched), 1.0)
        return AxisScore(
            score, matched_evidence,
            f"미일치 수치 {len(unmatched)}건(±{tolerance_pct}% 초과): {unmatched}",
        )

    # 모두 일치 → cross_check 엣지 점검
    xrisk = _cross_check_risk(kesg_code, graph)
    return AxisScore(
        round(xrisk, 3), matched_evidence,
        "모든 수치 증빙 일치" + (f"; 교차검증 경고" if xrisk > 0 else ""),
    )


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

    text_nodes = graph.text_nodes_by_code(kesg_code)
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

    # 가중평균 (D4 제거 후 재배분: D1=40%, D2=25%, D3=25%, D5=10%)
    weighted = d1.score * 0.40 + d2.score * 0.25 + d3.score * 0.25 + d5.score * 0.10
    top = max({"D1": d1, "D2": d2, "D3": d3, "D5": d5}.items(), key=lambda kv: kv[1].score)

    aggregate = AxisScore(
        score=round(weighted, 4),
        evidence=d1.evidence + d3.evidence,
        detail=f"종합 위험도={weighted:.3f} | 최고위험={top[0]}({top[1].score:.3f})",
    )
    return {"D1": d1, "D2": d2, "D3": d3, "D5": d5, "aggregate": aggregate}


# ====================================================================
# 내부 헬퍼
# ====================================================================

_SCALE = {"만": 1e4, "억": 1e8, "천": 1e3}


def _extract_numbers(sentence: str) -> list[tuple[float, str | None]]:
    """문장에서 (수치, 단위) 클레임 추출.

    오탐 필터:
      - 연도(1900~2100, 단위·스케일 없음) 제외 — "2025년"은 수치 주장이 아님
      - 단위·스케일·쉼표·소수점이 전혀 없는 맨 정수 제외 — 페이지·항목 번호 오탐 방지
    """
    out: list[tuple[float, str | None]] = []
    for m in _NUM_RE.finditer(sentence):
        raw, scale, unit = m.group(1), m.group(2), m.group(3)
        try:
            val = float(raw.replace(",", ""))
        except ValueError:
            continue
        if unit is None and scale is None:
            if 1900 <= val <= 2100 and "." not in raw:
                continue   # 연도
            if "," not in raw and "." not in raw:
                continue   # 맨 정수 (번호류)
        if scale:
            val *= _SCALE.get(scale, 1)
        out.append((val, unit))
    return out


def _find_matching_node(
    claim: float,
    claim_unit: str | None,
    nodes: list[EvidenceNode],
    tol: float,
) -> EvidenceNode | None:
    """±tol% 이내 + 단위 호환 노드 탐색 (128,400원 ≠ 128,400 kWh)."""
    from ..layer3_detect import units_compatible
    for n in nodes:
        if n.value == 0:
            continue
        if not units_compatible(claim_unit, n.unit):
            continue
        if abs(claim - n.value) / abs(n.value) * 100 <= tol:
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
    return {
        # E 환경
        "E-1-1": "환경경영 목표",
        "E-1-2": "환경경영 추진체계",
        "E-2-1": "환경 법규 준수",
        "E-3-1": "온실가스 관리",
        "E-4-1": "에너지 사용량",
        "E-4-2": "재생에너지 사용",
        "E-5-1": "용수 사용량",
        "E-6-1": "폐기물 발생량",
        "E-6-2": "폐기물 재활용",
        # S 사회
        "S-1-1": "인권/노동 정책",
        "S-2-1": "공정거래·동반성장",
        "S-3-1": "안전보건 시스템",
        "S-4-1": "정보보호 정책",
        # G 지배구조
        "G-1-1": "이사회 구성·독립성",
        "G-2-1": "주주권리 보호",
        "G-3-1": "윤리경영·부패방지",
        "G-4-1": "감사기구 운영",
        # P 기반
        "P-1-1": "ESG 추진체계",
        "P-2-1": "ESG 정보공시",
    }.get(code, code)
