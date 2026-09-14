"""L5 — 확장 Audit Trace (v15).

기존 문장 단위 audit_trace.json을 유지하되,
중소기업 ↔ 대기업 실사 대응에 맞춰 두 가지를 추가한다.

  · data_points[]  — K-ESG 항목별 '확정 정량값 + 증빙 파일 하드링크' (엑셀 시트의 원천)
  · policy_audit[] — 사내규정 검증 결과(누락 조항 + 보완 초안)

증빙 하드링크 규약:
  각 data_point는 evidence_files[]를 가지며, 파일명은 업로드된 원본 그대로
  ("한전고지서_2025_12.pdf"). 실제 파일은 outputs/evidence_pack/ 에 복사되어
  엑셀·JSON·서류철이 같은 파일을 가리킨다(상대경로 동일).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

from .evidence_graph import EvidenceGraph, EvidenceNode
from .detector_5axis import PolicyAuditResult


# ====================================================================
# 신규 스키마
# ====================================================================

@dataclass
class EvidenceLink:
    """수치 옆에 붙는 증빙 파일 하드링크."""
    file_name: str                 # "한전고지서_2025_12.pdf"
    relative_path: str             # "evidence_pack/한전고지서_2025_12.pdf"
    origin: str                    # dart | ocr_structured | ocr_unstructured
    bbox: list[float] | None = None    # 0~1 정규화 위치
    page: int | None = None            # 0-기준 페이지 인덱스
    node_id: str = ""
    kesg_codes: list[str] = field(default_factory=list)
    quote: str = ""
    resolved: bool = False
    independent: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DataPoint:
    """대기업 실사 시스템에 입력할 항목별 확정 정량값."""
    kesg_code: str                 # "E-4-1"
    kesg_name: str                 # "에너지 사용량"
    value: float
    unit: str
    period: int | None
    confidence: float
    verification: str              # "verified" | "estimated" | "unverified"
    d1_risk: float | None          # L3 D1 수치 위험도
    evidence_files: list[EvidenceLink] = field(default_factory=list)
    source_tier: str = ""
    representative_node_ids: list[str] = field(default_factory=list)
    value_role: str = "unknown"
    confidence_flags: list[str] = field(default_factory=list)
    d1_evaluation: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["evidence_files"] = [e.to_dict() for e in self.evidence_files]
        return d


@dataclass
class AuditTraceV15:
    ticker: str
    corp_name: str
    generated_at: str
    data_points: list[DataPoint] = field(default_factory=list)
    policy_audit: list[dict[str, Any]] = field(default_factory=list)
    sentences: list[dict[str, Any]] = field(default_factory=list)   # 기존 v10 문장 추적 유지
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "v15",
            "ticker": self.ticker,
            "corp_name": self.corp_name,
            "generated_at": self.generated_at,
            "data_points": [dp.to_dict() for dp in self.data_points],
            "policy_audit": self.policy_audit,
            "sentences": self.sentences,
            "summary": self.summary,
        }


# ====================================================================
# 빌더
# ====================================================================

# 출처별 파생값을 합산해야 하는 가산 코드(예: Scope1+2 = 전력 Scope2 + 가스 Scope1).
_ADDITIVE_DERIVED: frozenset[str] = frozenset({"E-3-1"})


def build_data_points(
    graph: EvidenceGraph,
    d1_scores: dict[str, float],
    *,
    target_codes: list[str],
    d1_evaluations: dict[str, dict[str, Any]] | None = None,
) -> list[DataPoint]:
    """최종 원장 선택을 소비한다. 결정 메타데이터가 없는 구버전만 공용 규칙으로 선택."""
    from .selection import resolve_fact, finite_number
    points = []
    for code in target_codes:
        fact = resolve_fact(graph, code)
        if fact is None:
            continue
        links = [_to_link(graph.nodes[nid]) for nid in fact.representative_node_ids
                 if nid in graph.nodes]
        d1 = d1_scores.get(code, 0.0)
        evaluation = (d1_evaluations or {}).get(code, {})
        incomplete = evaluation.get("status") in {"partial", "unavailable"}
        valid = bool(links) and all(e.resolved and e.independent and e.quote for e in links)
        flags = set(fact.flags)
        if incomplete or finite_number(fact.value) is None or d1 >= 0.5 or "unit_suspect" in flags:
            verification = "unverified"
        elif not valid or flags.intersection({"period_inferred", "partial_aggregate", "partial_value", "derived", "no_representative_node"}):
            verification = "estimated"
        else:
            verification = "verified" if d1 < 0.2 else "estimated"
        points.append(DataPoint(
            kesg_code=code, kesg_name=_kesg_name(code), value=fact.value,
            unit=fact.unit, period=fact.period, confidence=round(fact.confidence, 3),
            verification=verification, d1_risk=None if evaluation.get("status") == "unavailable" else round(d1, 3), evidence_files=links,
            d1_evaluation=evaluation,
            source_tier=fact.source_tier, representative_node_ids=fact.representative_node_ids,
            value_role=fact.value_role, confidence_flags=fact.flags))
    return points


def build_audit_trace_v15(
    ticker: str,
    corp_name: str,
    data_points: list[DataPoint],
    policy_results: list[PolicyAuditResult],
    *,
    sentences: list[dict[str, Any]] | None = None,
) -> AuditTraceV15:
    verified = sum(1 for d in data_points if d.verification == "verified")
    policy_dump = [
        {
            "kesg_code": p.kesg_code,
            "passed": p.passed,
            "findings": [vars(f) for f in p.findings],
            "source_files": p.source_files,
        }
        for p in policy_results
    ]
    return AuditTraceV15(
        ticker=ticker,
        corp_name=corp_name,
        generated_at=datetime.now(timezone.utc).isoformat(),
        data_points=data_points,
        policy_audit=policy_dump,
        sentences=sentences or [],
        summary={
            "data_point_count": len(data_points),
            "verified_count": verified,
            "verified_ratio": round(verified / len(data_points), 3) if data_points else 0.0,
            "policy_pass": sum(1 for p in policy_results if p.passed),
            "policy_total": len(policy_results),
        },
    )


# ====================================================================
# 헬퍼
# ====================================================================

def _to_link(n: EvidenceNode) -> EvidenceLink:
    return evidence_link(n)


def evidence_link(n: Any) -> EvidenceLink:
    """실제 노드에서만 검증 가능한 링크를 만든다. 파일명 자체는 증빙 판정이 아니다."""
    from ..survey import is_survey
    fname = n.source_file or getattr(n, "source", "") or f"{n.id}.json"
    codes = [getattr(n, k, None) for k in ("metric", "kesg_code", "rba_code")]
    return EvidenceLink(
        file_name=fname,
        relative_path=f"evidence_pack/{fname}",
        origin=n.origin,
        bbox=getattr(n, "bbox", None),
        page=n.page,
        node_id=n.id,
        kesg_codes=[c for c in codes if c],
        quote=getattr(n, "raw_text", "") or getattr(n, "text", ""),
        resolved=True,
        independent=not is_survey(n),
    )


def _verification_label(node: EvidenceNode, d1_risk: float) -> str:
    if d1_risk >= 0.5:
        return "unverified"
    if node.origin == "dart" or (node.source_file and d1_risk < 0.2):
        return "verified"
    return "estimated"


def _kesg_name(code: str) -> str:
    """K-ESG 코드 → 항목명. kesg_items 단일 출처에서 가져온다.

    하드코딩 표를 쓰던 시절 S-3-1을 '안전보건 추진체계'로 적어(실제로는
    '여성 구성원 비율') 엑셀 데이터시트에 자기모순 행이 찍혔다. 2026-07-28 정정.
    """
    from ..knowledge.kesg_items import kesg_name as _canonical

    return _canonical(code)
