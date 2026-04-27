"""Layer 0 — DART 사실 노드 Evidence Graph 구축.

정형 XBRL/JSON(kesg_data) 기반 수치 항목을 파싱해 사실 노드 그래프로 구조화하고,
raw_text_snippets에서 YoY 정보를 추출해 시계열 엣지를 생성한다.

비정형 PDF 처리는 Phase 2 스텁만 남긴다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any

from .dart_client import CompanyReport


# ---- 노드 / 엣지 스키마 -------------------------------------------------------

@dataclass
class EvidenceNode:
    id: str           # "{corp_code}_{metric}_{period}" 또는 "_inferred" 접미사
    metric: str       # K-ESG 코드 (예: "E-3-1")
    value: float
    unit: str
    period: int       # 보고 연도
    source: str       # 데이터 출처 (예: "kesg_data/E-3-1", "raw_text/2_inferred")
    raw_text: str     # note 또는 원문 스니펫

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceEdge:
    source_id: str    # 이전 period 노드 ID (시간 순 앞)
    target_id: str    # 최신 period 노드 ID
    edge_type: str    # "timeseries"
    yoy: float | None     # 전년 대비 변화율 (%)
    cagr: float | None    # 연평균 성장률 (%)
    years_gap: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---- EvidenceGraph -----------------------------------------------------------

class EvidenceGraph:
    """사실 노드 + 시계열 엣지 그래프."""

    def __init__(self, corp_code: str, corp_name: str) -> None:
        self.corp_code = corp_code
        self.corp_name = corp_name
        self._nodes: dict[str, EvidenceNode] = {}
        self._edges: list[EvidenceEdge] = []

    # ---- 변경 API ------------------------------------------------------------

    def add_node(self, node: EvidenceNode) -> None:
        self._nodes[node.id] = node

    def add_edge(self, edge: EvidenceEdge) -> None:
        self._edges.append(edge)

    # ---- 조회 API ------------------------------------------------------------

    @property
    def nodes(self) -> dict[str, EvidenceNode]:
        return self._nodes

    @property
    def edges(self) -> list[EvidenceEdge]:
        return self._edges

    def nodes_by_metric(self, metric: str) -> list[EvidenceNode]:
        """특정 K-ESG 코드의 모든 노드 반환 (period 오름차순)."""
        return sorted(
            (n for n in self._nodes.values() if n.metric == metric),
            key=lambda n: n.period,
        )

    def search_nodes(
        self,
        keywords: list[str],
        period: int | None = None,
    ) -> list[EvidenceNode]:
        """키워드 또는 K-ESG 코드로 노드 검색.

        L1 evidence_node_ids 매칭 인터페이스:
          keywords — K-ESG 코드 리스트, 또는 텍스트 키워드 혼용 가능
          period   — 연도 필터 (None 이면 전체)
        """
        results: list[EvidenceNode] = []
        for node in self._nodes.values():
            if period is not None and node.period != period:
                continue
            # 코드 직접 매칭
            if node.metric in keywords:
                results.append(node)
                continue
            # 텍스트 키워드 매칭 (raw_text + unit)
            haystack = f"{node.metric} {node.raw_text} {node.unit}"
            if any(kw in haystack for kw in keywords):
                results.append(node)
        return results

    # ---- 직렬화 --------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "corp_code": self.corp_code,
            "corp_name": self.corp_name,
            "nodes": {k: v.to_dict() for k, v in self._nodes.items()},
            "edges": [e.to_dict() for e in self._edges],
            "stats": {
                "node_count": len(self._nodes),
                "edge_count": len(self._edges),
                "metrics_covered": sorted({n.metric for n in self._nodes.values()}),
            },
        }


# ---- 헬퍼 함수 ---------------------------------------------------------------

def calc_yoy(v_current: float, v_prior: float) -> float | None:
    """전년 대비 변화율 (%) 계산. v_prior == 0이면 None."""
    if v_prior == 0:
        return None
    return round((v_current - v_prior) / abs(v_prior) * 100, 3)


def calc_cagr(v_start: float, v_end: float, years: int) -> float | None:
    """연평균 성장률 (CAGR, %) 계산. v_start <= 0 또는 years <= 0이면 None."""
    if v_start <= 0 or years <= 0:
        return None
    return round(((v_end / v_start) ** (1.0 / years) - 1) * 100, 3)


# ---- YoY 추출 패턴 -----------------------------------------------------------

# "전년 대비 2.1% 감소" / "전년 대비 0.4%p 상승" / "전년 대비 각각 5.2%, 3.8% 감소" 등을 캡처
_YOY_PATTERN = re.compile(
    r"전년\s*대비\s+(?:각각\s+)?(?P<num>\d+\.?\d*)\s*%(?P<pp>p)?\s*"
    r"(?:,\s*\d+\.?\d*\s*%p?\s*)?(?P<dir>감소|하락|증가|상승|개선|절감)",
)

# K-ESG 코드 → 텍스트 키워드 (역방향 매핑: 스니펫 → 코드 추론)
_METRIC_KEYWORDS: dict[str, list[str]] = {
    "E-2-1": ["원부자재"],
    "E-2-2": ["재생 원부자재", "고철", "스크랩"],
    "E-3-1": ["온실가스", "Scope 1+2", "Scope1+2", "tCO2", "배출량"],
    "E-4-1": ["에너지 사용량", "에너지사용"],
    "E-4-2": ["재생에너지", "RE100"],
    "E-5-1": ["취수량", "취수"],
    "E-5-2": ["재사용 용수", "용수 재사용", "공정 내 재사용"],
    "E-6-1": ["폐기물 배출", "슬래그"],
    "E-6-2": ["폐기물 재활용", "재활용 비율"],
    "E-7-1": ["대기오염", "NOx", "SOx", "비산먼지"],
    "E-7-2": ["수질오염", "COD", "SS"],
    "S-2-2": ["정규직 비율"],
    "S-2-3": ["이직률"],
    "S-2-4": ["교육훈련비"],
    "S-3-1": ["여성 구성원", "여성 비율"],
    "S-3-3": ["장애인"],
    "S-4-2": ["재해율", "산업재해"],
    "G-1-2": ["사외이사"],
    "G-1-4": ["여성 이사"],
    "G-2-1": ["출석률", "이사회 출석"],
    "G-3-4": ["배당"],
}


def _match_metric(text: str) -> str | None:
    """텍스트에서 가장 먼저 매칭되는 K-ESG 코드를 반환."""
    for metric, keywords in _METRIC_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return metric
    return None


# ---- 핵심 빌드 함수 ----------------------------------------------------------

def build_evidence_graph(report: CompanyReport) -> EvidenceGraph:
    """CompanyReport → EvidenceGraph.

    1단계: kesg_data의 수치(int/float) 항목 → 현재 연도 사실 노드
    2단계: raw_text_snippets에서 YoY 패턴 추출 → 추론 전년도 노드 + 시계열 엣지
    """
    graph = EvidenceGraph(corp_code=report.corp_code, corp_name=report.corp_name)

    # 1) 정형 kesg_data → 사실 노드
    for code, entry in report.kesg_data.items():
        value = entry.get("value")
        if not isinstance(value, (int, float)):
            continue  # 정성 항목(문자열) 제외
        node = EvidenceNode(
            id=f"{report.corp_code}_{code}_{report.report_year}",
            metric=code,
            value=float(value),
            unit=entry.get("unit", ""),
            period=report.report_year,
            source=f"kesg_data/{code}",
            raw_text=entry.get("note", ""),
        )
        graph.add_node(node)

    # 2) raw_text_snippets → YoY 추론 노드 + 시계열 엣지
    inferred_nodes, edges = _infer_timeseries(graph, report)
    for node in inferred_nodes:
        graph.add_node(node)
    for edge in edges:
        graph.add_edge(edge)

    return graph


def _infer_timeseries(
    graph: EvidenceGraph,
    report: CompanyReport,
) -> tuple[list[EvidenceNode], list[EvidenceEdge]]:
    """raw_text_snippets에서 '전년 대비 X% 변화' 구절을 파싱해
    추론 전년도 노드와 시계열 엣지를 생성한다."""
    inferred: list[EvidenceNode] = []
    edges: list[EvidenceEdge] = []
    prior_period = report.report_year - 1

    for idx, snippet in enumerate(report.raw_text_snippets):
        m = _YOY_PATTERN.search(snippet)
        if not m:
            continue

        pct = float(m.group("num"))
        direction = m.group("dir")
        is_pp = m.group("pp") == "p"  # percentage point (절대 변화)
        yoy_sign = -1.0 if direction in ("감소", "하락", "절감") else 1.0
        yoy = yoy_sign * pct  # 부호 포함 변화율 (%)

        metric = _match_metric(snippet)
        if not metric:
            continue

        current_id = f"{report.corp_code}_{metric}_{report.report_year}"
        current_node = graph.nodes.get(current_id)
        if not current_node:
            continue

        prior_id = f"{report.corp_code}_{metric}_{prior_period}_inferred"

        # 전년도 값 역산
        if is_pp:
            # 절대 변화 (percentage point): prior = current - Δ
            prior_value = current_node.value - yoy
        else:
            # 상대 변화 (percentage): prior = current / (1 + yoy/100)
            denom = 1.0 + yoy / 100.0
            if denom == 0:
                continue
            prior_value = current_node.value / denom

        # 이미 동일 inferred 노드가 추가된 경우 엣지만 추가
        existing = graph.nodes.get(prior_id) or next(
            (n for n in inferred if n.id == prior_id), None
        )
        if not existing:
            inferred.append(EvidenceNode(
                id=prior_id,
                metric=metric,
                value=round(prior_value, 4),
                unit=current_node.unit,
                period=prior_period,
                source=f"raw_text/{idx}_inferred",
                raw_text=snippet,
            ))

        yoy_cagr = round(yoy, 3)
        edges.append(EvidenceEdge(
            source_id=prior_id,
            target_id=current_id,
            edge_type="timeseries",
            yoy=yoy_cagr,
            cagr=yoy_cagr,  # 1년 간격이면 CAGR == YoY
            years_gap=1,
        ))

    return inferred, edges


# ---- Phase 2 스텁 ------------------------------------------------------------

def parse_pdf_evidence(pdf_path: str) -> EvidenceGraph:
    """Phase 2 스텁: 비정형 PDF 파싱 (미구현).

    Phase 2에서 pdfminer / PDFPlumber 기반 표 추출 + 수치 파싱 구현 예정.
    """
    raise NotImplementedError(
        "PDF 기반 Evidence Graph 파싱은 Phase 2에서 구현됩니다. "
        "현재는 정형 XBRL/JSON(kesg_data) 소스만 지원합니다."
    )
