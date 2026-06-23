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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DataPoint:
    """대기업 실사 시스템에 입력할 항목별 확정 정량값."""
    kesg_code: str                 # "E-4-1"
    kesg_name: str                 # "에너지 사용량"
    value: float
    unit: str
    period: int
    confidence: float
    verification: str              # "verified" | "estimated" | "unverified"
    d1_risk: float                 # L3 D1 수치 위험도
    evidence_files: list[EvidenceLink] = field(default_factory=list)

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
) -> list[DataPoint]:
    """K-ESG 코드별로 SSOT에서 확정값 1개를 선정 + 증빙 링크 부착.

    선정 규칙:
      - 같은 코드/연도에 노드가 여럿이면 confidence 최댓값 노드 채택.
      - DART와 OCR이 모두 있으면 DART를 1순위(공시 우선), OCR을 보조 증빙으로 첨부.
    """
    points: list[DataPoint] = []
    for code in target_codes:
        nodes = graph.nodes_by_metric(code)
        if not nodes:
            continue
        latest_year = max(n.period for n in nodes)
        year_nodes = [n for n in nodes if n.period == latest_year]
        primary = _pick_primary(year_nodes)
        value = primary.value
        # 가산 코드(Scope1+2 등)는 출처별 파생값을 합산한다 — 단 공시(reported)값이
        # 있으면 그것을 우선해 이중계산을 피한다(전력 Scope2 + 가스 Scope1 합산).
        if code in _ADDITIVE_DERIVED and len(year_nodes) > 1:
            derived = [n for n in year_nodes
                       if str(getattr(n, "source", "")).startswith("derived_from:")]
            reported = [n for n in year_nodes if n not in derived]
            if derived and not reported:
                value = round(sum(n.value for n in derived), 3)
                primary = _pick_primary(derived)
        links = [_to_link(n) for n in year_nodes]
        d1 = d1_scores.get(code, 0.0)
        points.append(DataPoint(
            kesg_code=code,
            kesg_name=_kesg_name(code),
            value=value,
            unit=primary.unit,
            period=primary.period,
            confidence=round(primary.confidence, 3),
            verification=_verification_label(primary, d1),
            d1_risk=round(d1, 3),
            evidence_files=links,
        ))
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

def _pick_primary(nodes: list[EvidenceNode]) -> EvidenceNode:
    dart = [n for n in nodes if n.origin == "dart"]
    pool = dart or nodes
    return max(pool, key=lambda n: n.confidence)


def _to_link(n: EvidenceNode) -> EvidenceLink:
    fname = n.source_file or f"{n.id}.json"
    return EvidenceLink(
        file_name=fname,
        relative_path=f"evidence_pack/{fname}",
        origin=n.origin,
        bbox=n.bbox,
        page=n.page,
        node_id=n.id,
    )


def _verification_label(node: EvidenceNode, d1_risk: float) -> str:
    if d1_risk >= 0.5:
        return "unverified"
    if node.origin == "dart" or (node.source_file and d1_risk < 0.2):
        return "verified"
    return "estimated"


def _kesg_name(code: str) -> str:
    return {
        "E-3-1": "온실가스 배출량(Scope1+2)",
        "E-4-1": "에너지 사용량",
        "E-5-1": "용수 사용량",
        "E-6-1": "폐기물 배출량",
        "S-3-1": "안전보건 추진체계",
    }.get(code, code)
