"""L0 — 통합 Evidence Graph (DART + 내부 증빙 OCR).

기존 v10 EvidenceGraph(DART 정형 수치 전용)를 확장한다.
핵심 변경: **단일 진실 원천(SSOT)** 에 두 출처를 함께 묶는다.

  DART JSON ─┐
             ├─► EvidenceGraph (nodes + edges)  ──► L1/L2/L3 …
  OCR 증빙 ──┘
   (ocr_router.OcrExtraction)

설계 원칙
  - EvidenceNode.origin 으로 출처를 구분(dart | ocr_structured | ocr_unstructured).
  - 모든 노드는 source_file(원본 증빙 파일명)을 보존 → L5 증빙 서류철 하드링크 키.
  - DART와 OCR이 같은 metric/period를 가지면 cross-check 엣지로 연결(D1 교차검증 재료).
  - 정성 조항(ExtractedClause)은 TextNode로 별도 보관 → 사내규정 검증(detector)에서 사용.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Literal

from .ocr_router import OcrExtraction, ExtractedMetric, ExtractedClause, DocChannel

Origin = Literal["dart", "ocr_structured", "ocr_unstructured"]


# ====================================================================
# 노드 / 엣지 스키마 (v10 호환 + 확장 필드)
# ====================================================================

@dataclass
class EvidenceNode:
    id: str            # "{corp}_{metric}_{period}__{origin}"
    metric: str        # K-ESG 코드 (예: "E-4-1")
    value: float
    unit: str
    period: int        # 보고 연도
    source: str        # 데이터 출처 경로 (예: "kesg_data/E-4-1", "ocr/kepco_bill")
    raw_text: str = ""
    origin: Origin = "dart"          # ★ 신규: 출처 구분
    source_file: str | None = None   # ★ 신규: 원본 증빙 파일명 (감사 하드링크)
    bbox: list[float] | None = None  # ★ 신규: 원문 내 위치
    confidence: float = 1.0          # ★ 신규: OCR/추출 신뢰도 (DART=1.0)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TextNode:
    """정성 조항 노드 (회의록·규정집 등 서술형)."""
    id: str            # "{corp}_TXT_{idx:04d}"
    section: str
    text: str
    kesg_code: str | None
    source_file: str
    page: int | None = None
    origin: Origin = "ocr_unstructured"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceEdge:
    source_id: str
    target_id: str
    edge_type: str        # "timeseries" | "cross_check"
    yoy: float | None = None
    cagr: float | None = None
    years_gap: int = 1
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ====================================================================
# EvidenceGraph
# ====================================================================

class EvidenceGraph:
    """사실 노드 + 정성 노드 + 엣지 통합 그래프 (SSOT)."""

    def __init__(self, corp_code: str, corp_name: str) -> None:
        self.corp_code = corp_code
        self.corp_name = corp_name
        self._nodes: dict[str, EvidenceNode] = {}
        self._text_nodes: dict[str, TextNode] = {}
        self._edges: list[EvidenceEdge] = []
        self._text_seq = 0

    # ---- 변경 API ----------------------------------------------------
    def add_node(self, node: EvidenceNode) -> None:
        self._nodes[node.id] = node

    def add_text_node(self, node: TextNode) -> None:
        self._text_nodes[node.id] = node

    def add_edge(self, edge: EvidenceEdge) -> None:
        self._edges.append(edge)

    # ---- 조회 API ----------------------------------------------------
    @property
    def nodes(self) -> dict[str, EvidenceNode]:
        return self._nodes

    @property
    def text_nodes(self) -> dict[str, TextNode]:
        return self._text_nodes

    @property
    def edges(self) -> list[EvidenceEdge]:
        return self._edges

    def nodes_by_metric(self, metric: str) -> list[EvidenceNode]:
        return sorted(
            (n for n in self._nodes.values() if n.metric == metric),
            key=lambda n: n.period,
        )

    def text_nodes_by_code(self, code: str) -> list[TextNode]:
        return [t for t in self._text_nodes.values() if t.kesg_code == code]

    def to_dict(self) -> dict[str, Any]:
        return {
            "corp_code": self.corp_code,
            "corp_name": self.corp_name,
            "nodes": [n.to_dict() for n in self._nodes.values()],
            "text_nodes": [t.to_dict() for t in self._text_nodes.values()],
            "edges": [e.to_dict() for e in self._edges],
        }

    # ---- 내부 시퀀스 -------------------------------------------------
    def _next_text_id(self) -> str:
        self._text_seq += 1
        return f"{self.corp_code}_TXT_{self._text_seq:04d}"


# ====================================================================
# 빌더 1 — DART (기존 v10 로직 위임; 여기선 인터페이스만)
# ====================================================================

def build_from_dart(report: Any) -> EvidenceGraph:
    """DART CompanyReport → EvidenceGraph.

    기존 esgenie/layer0_evidence_graph.build_evidence_graph 로직을 그대로 사용하되,
    생성되는 노드에 origin="dart", confidence=1.0 을 부여한다.
    (실제 구현은 기존 모듈 재사용 — 여기서는 시그니처 고정.)
    """
    raise NotImplementedError("기존 build_evidence_graph 로직을 origin='dart'로 래핑")


# ====================================================================
# 빌더 2 — OCR 증빙 → 노드 편입  ★ 신규 핵심
# ====================================================================

# OCR metric_hint → K-ESG 코드 매핑 사전 (LLM 추정 보정용 화이트리스트)
_HINT_TO_KESG: dict[str, str] = {
    "사용전력량": "E-4-1",   # 에너지 사용량
    "전력": "E-4-1",
    "도시가스": "E-4-1",
    "가스사용량": "E-4-1",
    "재생에너지": "E-4-2",
    "용수": "E-5-1",
    "수도": "E-5-1",
    "폐기물": "E-6-1",
    "지정폐기물": "E-6-1",
    "재활용": "E-6-2",
    "온실가스": "E-3-1",
    "scope1": "E-3-1",
    "scope2": "E-3-1",
}

# 단위 환산 → 탄소/에너지 표준화 (예시 계수, 실제는 환경부/한전 배출계수 사용)
_EMISSION_FACTORS = {
    "kWh_to_tco2": 0.4781 / 1000,   # 전력 tCO2eq/kWh (2025 국가 전력배출계수 예시)
    "MJ_gas_to_tco2": 0.0000561,    # 도시가스 tCO2eq/MJ (예시)
}


def merge_ocr_extraction(
    graph: EvidenceGraph,
    extraction: OcrExtraction,
    *,
    report_year: int,
) -> EvidenceGraph:
    """OCR 추출 결과를 기존 그래프에 편입(SSOT 통합).

    1) 정량 metric → EvidenceNode 추가 (origin=ocr_*).
    2) 동일 metric/period의 DART 노드가 있으면 cross_check 엣지 생성.
    3) 정성 clause → TextNode 추가.
    4) 탄소 배출량 파생 노드 자동 산출(전력·가스 → tCO2eq).
    """
    origin: Origin = (
        "ocr_structured" if extraction.channel is DocChannel.STRUCTURED else "ocr_unstructured"
    )

    for m in extraction.metrics:
        code = _resolve_kesg_code(m)
        period = _normalize_period(m.period, fallback=report_year)
        node = EvidenceNode(
            id=f"{graph.corp_code}_{code or m.metric_hint}_{period}__{origin}",
            metric=code or m.metric_hint,
            value=m.value,
            unit=m.unit,
            period=period,
            source=f"ocr/{extraction.doc_type}",
            raw_text=f"{m.metric_hint}={m.value}{m.unit} ({extraction.source_file})",
            origin=origin,
            source_file=extraction.source_file,
            bbox=m.bbox,
            confidence=m.confidence,
        )
        graph.add_node(node)
        _link_cross_check(graph, node)
        _emit_derived_emission(graph, node)

    for c in extraction.clauses:
        tnode = TextNode(
            id=graph._next_text_id(),
            section=c.section,
            text=c.text,
            kesg_code=c.kesg_code_guess,
            source_file=extraction.source_file,
            page=c.page,
            origin=origin,
        )
        graph.add_text_node(tnode)

    return graph


def build_unified_graph(
    dart_report: Any | None,
    extractions: list[OcrExtraction],
    *,
    corp_code: str,
    corp_name: str,
    report_year: int,
) -> EvidenceGraph:
    """최상위 진입점 — DART + 모든 OCR 증빙을 하나의 SSOT로 통합.

    app.py 가 호출하는 핵심 함수.
    """
    if dart_report is not None:
        graph = build_from_dart(dart_report)
    else:
        graph = EvidenceGraph(corp_code, corp_name)

    for ext in extractions:
        merge_ocr_extraction(graph, ext, report_year=report_year)
    return graph


# ====================================================================
# 내부 헬퍼
# ====================================================================

def _resolve_kesg_code(m: ExtractedMetric) -> str | None:
    """LLM 추정 코드 + 화이트리스트 사전으로 K-ESG 코드 확정."""
    if m.kesg_code_guess:
        return m.kesg_code_guess
    hint = m.metric_hint.lower().replace(" ", "")
    for key, code in _HINT_TO_KESG.items():
        if key.lower() in hint:
            return code
    return None


def _normalize_period(period_raw: str, *, fallback: int) -> int:
    """'2025-12' / '2025년' / '' → 연도 정수."""
    import re
    m = re.search(r"(20\d{2})", period_raw or "")
    return int(m.group(1)) if m else fallback


def _link_cross_check(graph: EvidenceGraph, node: EvidenceNode) -> None:
    """같은 metric/period의 DART 노드와 cross_check 엣지 연결 (D1 교차검증 재료)."""
    for other in graph.nodes_by_metric(node.metric):
        if other.id == node.id or other.period != node.period:
            continue
        if other.origin == "dart" or other.origin != node.origin:
            diff_pct = _pct_diff(node.value, other.value)
            graph.add_edge(EvidenceEdge(
                source_id=other.id,
                target_id=node.id,
                edge_type="cross_check",
                detail=f"교차검증 오차 {diff_pct:.1f}% ({other.origin}↔{node.origin})",
            ))


def _emit_derived_emission(graph: EvidenceGraph, node: EvidenceNode) -> None:
    """전력/가스 사용량 노드 → 탄소 배출량(E-3-1) 파생 노드 자동 생성."""
    tco2: float | None = None
    if node.unit.lower() == "kwh" and node.metric == "E-4-1":
        tco2 = node.value * _EMISSION_FACTORS["kWh_to_tco2"]
    elif node.unit.lower() == "mj" and node.metric == "E-4-1":
        tco2 = node.value * _EMISSION_FACTORS["MJ_gas_to_tco2"]
    if tco2 is None:
        return
    derived = EvidenceNode(
        id=f"{graph.corp_code}_E-3-1_{node.period}__derived_{node.origin}",
        metric="E-3-1",
        value=round(tco2, 3),
        unit="tCO2eq",
        period=node.period,
        source=f"derived_from:{node.id}",
        raw_text=f"{node.raw_text} → 배출계수 환산",
        origin=node.origin,
        source_file=node.source_file,
        confidence=node.confidence * 0.95,   # 환산 불확실성 반영
    )
    graph.add_node(derived)


def _pct_diff(a: float, b: float) -> float:
    if b == 0:
        return 0.0 if a == 0 else 100.0
    return abs(a - b) / abs(b) * 100.0
