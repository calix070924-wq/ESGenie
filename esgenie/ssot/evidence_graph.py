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

import hashlib
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
    bbox: list[float] | None = None  # ★ 신규: 원문 내 위치(0~1 정규화)
    page: int | None = None          # ★ 신규: 0-기준 페이지 인덱스
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
    rba_code: str | None = None    # RBA 자가진단 substrate 매칭(고유 조항용)

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

    def text_nodes_by_rba_code(self, code: str) -> list[TextNode]:
        return [t for t in self._text_nodes.values() if t.rba_code == code]

    def search_nodes(
        self,
        keywords: list[str],
        period: int | None = None,
    ) -> list[EvidenceNode]:
        """K-ESG 코드/키워드로 노드 검색 (v10 layer1 호환 API).

        매칭 우선순위:
          1) node.metric이 keywords 중 하나와 정확히 일치 (K-ESG 코드 직접 매칭)
          2) node.metric에 keyword가 부분 포함
        period가 주어지면 해당 연도 노드만 반환.
        """
        result: list[EvidenceNode] = []
        for node in self._nodes.values():
            matched = any(
                kw == node.metric or kw.lower() in node.metric.lower()
                for kw in keywords
            )
            if not matched:
                continue
            if period is not None and node.period != period:
                continue
            result.append(node)
        return sorted(result, key=lambda n: n.period)

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
    """DART CompanyReport → EvidenceGraph (v15 SSOT).

    v10의 build_evidence_graph()를 호출한 뒤, 생성된 노드/엣지를
    v15 스키마(origin='dart', confidence=1.0)로 변환해 반환한다.
    """
    from esgenie.layer0_evidence_graph import (
        build_evidence_graph as _v10_build,
    )

    v10_graph = _v10_build(report)
    graph = EvidenceGraph(corp_code=v10_graph.corp_code, corp_name=v10_graph.corp_name)

    # v10 EvidenceNode → v15 EvidenceNode (origin/confidence 필드 추가)
    for v10_node in v10_graph.nodes.values():
        node = EvidenceNode(
            id=v10_node.id,
            metric=v10_node.metric,
            value=v10_node.value,
            unit=v10_node.unit,
            period=v10_node.period,
            source=v10_node.source,
            raw_text=v10_node.raw_text,
            origin="dart",
            source_file=None,   # DART는 파일 증빙 없음
            bbox=None,
            confidence=1.0,     # DART 공식 공시 = 신뢰도 최대
        )
        graph.add_node(node)

    # v10 EvidenceEdge → v15 EvidenceEdge (detail 필드 추가)
    for v10_edge in v10_graph.edges:
        edge = EvidenceEdge(
            source_id=v10_edge.source_id,
            target_id=v10_edge.target_id,
            edge_type=v10_edge.edge_type,
            yoy=v10_edge.yoy,
            cagr=v10_edge.cagr,
            years_gap=v10_edge.years_gap,
            detail=f"dart timeseries yoy={v10_edge.yoy}%",
        )
        graph.add_edge(edge)

    return graph


# ====================================================================
# 빌더 2 — OCR 증빙 → 노드 편입  ★ 신규 핵심
# ====================================================================

# OCR metric_hint → K-ESG 코드 매핑 사전 (LLM 추정 보정용 화이트리스트)
_HINT_TO_KESG: dict[str, str] = {
    # ── 환경 E ──
    "사용전력량": "E-4-1",   # 에너지 사용량
    "전력": "E-4-1",
    "도시가스": "E-4-1",
    "가스사용량": "E-4-1",
    "재생에너지": "E-4-2",
    "용수": "E-5-1",
    "수도": "E-5-1",
    "폐기물": "E-6-1",
    # 지정폐기물은 하위 분류 → 보조수치(None). E-6-1 총량에 중복으로 들어가지 않게 hint 제외.
    # E-6-2는 재활용 '비율(%)' 전용. '재활용량(톤)'은 부분문자열 "재활용"에 걸려
    # E-6-1 총량 대신 비율 칸을 덮어쓰던 버그가 있어, 비율 키워드로만 한정한다.
    # (재활용량은 코드 None으로 남겨 보조수치로만 다룬다 → 비율은 별도 추출/파생)
    "재활용비율": "E-6-2",
    "순환이용률": "E-6-2",
    "재활용률": "E-6-2",
    "온실가스": "E-3-1",
    "scope1": "E-3-1",
    "scope2": "E-3-1",
    # ── 사회 S ──
    "신규채용": "S-2-1",
    "채용인원": "S-2-1",
    "정규직비율": "S-2-2",
    "정규직전환율": "S-2-2",
    "비정규직비율": "S-2-2",
    "이직률": "S-2-3",
    "퇴사율": "S-2-3",
    "교육훈련비": "S-2-4",
    "1인당교육훈련비": "S-2-4",
    "복리후생비": "S-2-5",
    "1인당복리후생비": "S-2-5",
    "노조가입률": "S-2-6",
    "여성비율": "S-3-1",
    "여성임직원비율": "S-3-1",
    "여성급여비율": "S-3-2",
    "장애인고용률": "S-3-3",
    "재해율": "S-4-2",
    "사망만인율": "S-4-2",
    "ltifr": "S-4-2",
    "봉사참여율": "S-7-2",
    "개인정보유출": "S-8-2",
    "법규위반건수": "S-9-1",
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
    industry_module=None,
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

    for idx, m in enumerate(extraction.metrics):
        code = _resolve_kesg_code(m)
        period = _normalize_period(m.period, fallback=report_year)
        node = EvidenceNode(
            id=_make_ocr_node_id(
                graph.corp_code,
                code or m.metric_hint,
                period,
                origin,
                extraction.source_file,
                m.metric_hint,
                idx,
            ),
            metric=code or m.metric_hint,
            value=m.value,
            unit=m.unit,
            period=period,
            source=f"ocr/{extraction.doc_type}",
            raw_text=f"{m.metric_hint}={m.value}{m.unit} ({extraction.source_file})",
            origin=origin,
            source_file=extraction.source_file,
            bbox=m.bbox,
            page=m.page,
            confidence=m.confidence,
        )
        graph.add_node(node)
        _link_cross_check(graph, node)
        _emit_derived_emission(
            graph,
            node,
            industry_module=industry_module,
            source_file=extraction.source_file,
            seq=idx,
        )

    for c in extraction.clauses:
        tnode = TextNode(
            id=graph._next_text_id(),
            section=c.section,
            text=c.text,
            kesg_code=c.kesg_code_guess,
            source_file=extraction.source_file,
            page=c.page,
            origin=origin,
            rba_code=getattr(c, "rba_code_guess", None),
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
    industry_module=None,
) -> EvidenceGraph:
    """최상위 진입점 — DART + 모든 OCR 증빙을 하나의 SSOT로 통합.

    app.py 가 호출하는 핵심 함수.
    """
    if dart_report is not None:
        graph = build_from_dart(dart_report)
    else:
        graph = EvidenceGraph(corp_code, corp_name)

    for ext in extractions:
        merge_ocr_extraction(
            graph, ext, report_year=report_year, industry_module=industry_module)
    return graph


# ====================================================================
# 내부 헬퍼
# ====================================================================

# 총량/대표 코드로 잡으면 안 되는 하위·보조 수치(상위코드 부분문자열에 걸리는 것).
# 예: '지정폐기물'은 '폐기물'(E-6-1)에 걸리지만 총량이 아니라 하위 분류다.
_HINT_EXCLUDE: tuple[str, ...] = ("지정폐기물",)


def _resolve_kesg_code(m: ExtractedMetric) -> str | None:
    """LLM 추정 코드 + 화이트리스트 사전으로 K-ESG 코드 확정."""
    hint = m.metric_hint.lower().replace(" ", "")
    # 하위·보조 수치는 어떤 추정코드가 와도 총량 코드로 잡지 않는다(중복 노드 방지).
    if any(x in hint for x in _HINT_EXCLUDE):
        return None
    if m.kesg_code_guess:
        return m.kesg_code_guess
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


def _emit_derived_emission(
    graph: EvidenceGraph,
    node: EvidenceNode,
    industry_module=None,
    *,
    source_file: str | None = None,
    seq: int = 0,
) -> None:
    """전력/가스 사용량 노드 → 탄소 배출량(E-3-1) 파생 노드 자동 생성.

    industry_module이 업종 배출계수를 제공하면 전역값 위에 덮어쓴다(부분 키만
    줘도 나머지는 전역 폴백). None이면 전역 _EMISSION_FACTORS 그대로.
    """
    from ..industry.base import resolve_map
    factors = resolve_map(industry_module, "emission_factors", _EMISSION_FACTORS)

    tco2: float | None = None
    if node.unit.lower() == "kwh" and node.metric == "E-4-1":
        tco2 = node.value * factors["kWh_to_tco2"]
    elif node.unit.lower() == "mj" and node.metric == "E-4-1":
        tco2 = node.value * factors["MJ_gas_to_tco2"]
    if tco2 is None:
        return
    derived = EvidenceNode(
        id=_make_derived_node_id(
            graph.corp_code,
            "E-3-1",
            node.period,
            node.origin,
            source_file or node.source_file,
            node.id,
            seq,
        ),
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


def _make_ocr_node_id(
    corp_code: str,
    metric: str,
    period: int,
    origin: Origin,
    source_file: str | None,
    metric_hint: str,
    seq: int,
) -> str:
    suffix = _stable_suffix(source_file or "", metric_hint, seq)
    return f"{corp_code}_{metric}_{period}__{origin}__{suffix}"


def _make_derived_node_id(
    corp_code: str,
    metric: str,
    period: int,
    origin: Origin,
    source_file: str | None,
    parent_id: str,
    seq: int,
) -> str:
    suffix = _stable_suffix(source_file or "", parent_id, seq)
    return f"{corp_code}_{metric}_{period}__derived_{origin}__{suffix}"


def _stable_suffix(*parts: object) -> str:
    raw = "||".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
