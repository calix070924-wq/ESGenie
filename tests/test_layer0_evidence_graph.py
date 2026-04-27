"""Layer 0 EvidenceGraph 단위 테스트.

샘플 3사(삼성전자/현대자동차/포스코홀딩스) 모두에서:
- 수치 노드 생성 / 정성 항목 제외
- 노드 스키마 준수
- 시계열 엣지 생성
- 검색 API
- 직렬화 (to_dict)
- 헬퍼 함수 (calc_yoy, calc_cagr)
- Phase 2 스텁 예외
"""
from __future__ import annotations

import pytest

from esgenie.dart_client import load_sample_report
from esgenie.layer0_evidence_graph import (
    EvidenceEdge,
    EvidenceGraph,
    EvidenceNode,
    build_evidence_graph,
    calc_cagr,
    calc_yoy,
    parse_pdf_evidence,
)

CORP_CODES = ["005930", "005380", "005490"]


# ---- 기본 구조 ---------------------------------------------------------------

@pytest.mark.parametrize("corp_code", CORP_CODES)
def test_build_returns_evidence_graph(corp_code: str) -> None:
    report = load_sample_report(corp_code)
    graph = build_evidence_graph(report)
    assert isinstance(graph, EvidenceGraph)
    assert graph.corp_code == corp_code


@pytest.mark.parametrize("corp_code", CORP_CODES)
def test_has_numeric_nodes(corp_code: str) -> None:
    report = load_sample_report(corp_code)
    graph = build_evidence_graph(report)
    assert len(graph.nodes) > 0
    for node in graph.nodes.values():
        assert isinstance(node.value, float), f"node {node.id} 값이 float 아님"


@pytest.mark.parametrize("corp_code", CORP_CODES)
def test_qualitative_entries_excluded(corp_code: str) -> None:
    """정성(문자열) 항목은 노드에서 제외돼야 한다."""
    report = load_sample_report(corp_code)
    graph = build_evidence_graph(report)
    qualitative_codes = ["E-1-1", "P-1-1", "S-1-1", "G-1-1"]
    for code in qualitative_codes:
        node_id = f"{corp_code}_{code}_{report.report_year}"
        assert node_id not in graph.nodes, f"정성 항목 {code}가 노드에 포함됨"


# ---- 노드 스키마 -------------------------------------------------------------

@pytest.mark.parametrize("corp_code", CORP_CODES)
def test_node_schema(corp_code: str) -> None:
    report = load_sample_report(corp_code)
    graph = build_evidence_graph(report)
    for node in graph.nodes.values():
        assert node.id, "id 비어 있음"
        assert node.metric, "metric 비어 있음"
        assert node.unit is not None, "unit이 None"
        assert isinstance(node.period, int), "period가 int 아님"
        assert node.source, "source 비어 있음"
        assert node.period == report.report_year or "_inferred" in node.id


# ---- 시계열 엣지 -------------------------------------------------------------

@pytest.mark.parametrize("corp_code", CORP_CODES)
def test_timeseries_edges_generated(corp_code: str) -> None:
    """raw_text_snippets에 '전년 대비' 패턴이 있으므로 엣지가 최소 1개 이상."""
    report = load_sample_report(corp_code)
    graph = build_evidence_graph(report)
    assert len(graph.edges) >= 1, f"{corp_code}: 시계열 엣지 0개"


@pytest.mark.parametrize("corp_code", CORP_CODES)
def test_edge_schema(corp_code: str) -> None:
    report = load_sample_report(corp_code)
    graph = build_evidence_graph(report)
    for edge in graph.edges:
        assert edge.source_id in graph.nodes, f"source_id {edge.source_id} 노드 없음"
        assert edge.target_id in graph.nodes, f"target_id {edge.target_id} 노드 없음"
        assert edge.edge_type == "timeseries"
        assert edge.yoy is not None
        assert edge.years_gap == 1


@pytest.mark.parametrize("corp_code", CORP_CODES)
def test_inferred_node_period(corp_code: str) -> None:
    """추론 전년도 노드의 period는 report_year - 1이어야 한다."""
    report = load_sample_report(corp_code)
    graph = build_evidence_graph(report)
    for node in graph.nodes.values():
        if "_inferred" in node.id:
            assert node.period == report.report_year - 1


# ---- 삼성전자 확정 값 검증 ---------------------------------------------------

def test_samsung_ghg_node() -> None:
    """삼성 E-3-1(온실가스) 노드 값이 원본과 일치해야 한다."""
    report = load_sample_report("005930")
    graph = build_evidence_graph(report)
    node_id = "005930_E-3-1_2024"
    assert node_id in graph.nodes
    node = graph.nodes[node_id]
    assert node.value == pytest.approx(16_700_000.0)
    assert node.unit == "tCO2eq"


def test_samsung_ghg_yoy_edge() -> None:
    """삼성 E-3-1 전년 대비 2.1% 감소 → 전년도 값 역산 검증."""
    report = load_sample_report("005930")
    graph = build_evidence_graph(report)
    # prior = 16_700_000 / (1 - 0.021) ≈ 17_054_120
    prior_id = "005930_E-3-1_2023_inferred"
    assert prior_id in graph.nodes
    prior_node = graph.nodes[prior_id]
    expected_prior = 16_700_000 / (1 - 0.021)
    assert prior_node.value == pytest.approx(expected_prior, rel=0.001)

    # 엣지 확인
    edge = next(
        (e for e in graph.edges if e.source_id == prior_id and "E-3-1" in e.target_id),
        None,
    )
    assert edge is not None
    assert edge.yoy == pytest.approx(-2.1, abs=0.01)


# ---- 검색 API ----------------------------------------------------------------

def test_search_by_metric_code() -> None:
    report = load_sample_report("005930")
    graph = build_evidence_graph(report)
    results = graph.search_nodes(["E-3-1"])
    assert any(n.metric == "E-3-1" for n in results)


def test_search_by_keyword() -> None:
    report = load_sample_report("005930")
    graph = build_evidence_graph(report)
    results = graph.search_nodes(["tCO2eq"])
    assert len(results) > 0


def test_search_with_period_filter() -> None:
    report = load_sample_report("005930")
    graph = build_evidence_graph(report)
    results_current = graph.search_nodes(["E-3-1"], period=2024)
    results_prior = graph.search_nodes(["E-3-1"], period=2023)
    assert all(n.period == 2024 for n in results_current)
    assert all(n.period == 2023 for n in results_prior)


def test_nodes_by_metric() -> None:
    report = load_sample_report("005930")
    graph = build_evidence_graph(report)
    ghg_nodes = graph.nodes_by_metric("E-3-1")
    assert len(ghg_nodes) >= 1
    # period 오름차순 정렬 확인
    periods = [n.period for n in ghg_nodes]
    assert periods == sorted(periods)


# ---- 직렬화 ------------------------------------------------------------------

@pytest.mark.parametrize("corp_code", CORP_CODES)
def test_to_dict_schema(corp_code: str) -> None:
    report = load_sample_report(corp_code)
    graph = build_evidence_graph(report)
    d = graph.to_dict()
    assert d["corp_code"] == corp_code
    assert "nodes" in d
    assert "edges" in d
    assert "stats" in d
    assert d["stats"]["node_count"] == len(graph.nodes)
    assert d["stats"]["edge_count"] == len(graph.edges)
    assert isinstance(d["stats"]["metrics_covered"], list)


@pytest.mark.parametrize("corp_code", CORP_CODES)
def test_node_to_dict(corp_code: str) -> None:
    report = load_sample_report(corp_code)
    graph = build_evidence_graph(report)
    for node in list(graph.nodes.values())[:3]:
        d = node.to_dict()
        for key in ("id", "metric", "value", "unit", "period", "source", "raw_text"):
            assert key in d, f"{key} 누락"


# ---- 헬퍼 함수 ---------------------------------------------------------------

def test_calc_yoy_increase() -> None:
    assert calc_yoy(110, 100) == pytest.approx(10.0)


def test_calc_yoy_decrease() -> None:
    assert calc_yoy(90, 100) == pytest.approx(-10.0)


def test_calc_yoy_zero_prior() -> None:
    assert calc_yoy(100, 0) is None


def test_calc_cagr_two_years() -> None:
    # 100 → 121, 2년 → CAGR = 10%
    assert calc_cagr(100, 121, 2) == pytest.approx(10.0, rel=0.001)


def test_calc_cagr_one_year() -> None:
    assert calc_cagr(100, 110, 1) == pytest.approx(10.0)


def test_calc_cagr_invalid() -> None:
    assert calc_cagr(0, 100, 2) is None
    assert calc_cagr(100, 100, 0) is None


# ---- Phase 2 스텁 ------------------------------------------------------------

def test_pdf_stub_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        parse_pdf_evidence("dummy.pdf")
