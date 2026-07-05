"""D5(시계열 모순) — edge_type 필터 회귀 테스트.

_score_d5_timeseries()는 code가 매칭되는 엣지를 전부 분모(edge_ids)에 넣던 버그가 있었다.
cross_check 엣지(yoy=None)까지 분모에 섞이면 timeseries 모순이 있어도
contradiction_ratio가 인위적으로 희석된다. edge_type == "timeseries" 필터가
분모 오염을 막는지, 그리고 timeseries 엣지가 없을 때 스킵 경로가 정상 동작하는지 검증한다.
"""
from __future__ import annotations

from esgenie.layer3_detect import score_d5_timeseries
from esgenie.ssot.evidence_graph import EvidenceEdge, EvidenceGraph

# "배출"이 온실가스(E-3-1) 코드에 매칭되고, "2.1%"가 _NUMBER_PATTERN에 매칭된다.
# "감소" 키워드로 claim_down=True가 되므로 yoy>0(증가) 엣지와 방향이 어긋나 모순 처리된다.
SENTENCE = "온실가스 배출량은 2.1% 감소하였다."


def _graph_with_cross_check_edges(n_cross_check: int) -> EvidenceGraph:
    """timeseries 모순 엣지 1개 + cross_check(yoy=None) 엣지 n개를 가진 그래프."""
    graph = EvidenceGraph(corp_code="TESTCO", corp_name="테스트기업")
    graph.add_edge(EvidenceEdge(
        source_id="TESTCO_E-3-1_2023__dart",
        target_id="TESTCO_E-3-1_2024__dart",
        edge_type="timeseries",
        yoy=5.0,   # 문장은 "감소" 주장인데 YoY는 +5.0% (증가) → 모순
        cagr=None,
        years_gap=1,
        detail="dart timeseries yoy=5.0%",
    ))
    for i in range(n_cross_check):
        graph.add_edge(EvidenceEdge(
            source_id=f"TESTCO_E-3-1_2024__dart",
            target_id=f"TESTCO_E-3-1_2024__ocr_structured_{i}",
            edge_type="cross_check",
            detail=f"교차검증 오차 {i}% (dart↔ocr_structured)",
        ))
    return graph


def test_d5_cross_check_edges_do_not_dilute_contradiction_ratio() -> None:
    """cross_check 엣지 개수가 늘어나도 timeseries 모순 점수는 변하지 않아야 한다."""
    rv_1cc = score_d5_timeseries(SENTENCE, _graph_with_cross_check_edges(1))
    rv_5cc = score_d5_timeseries(SENTENCE, _graph_with_cross_check_edges(5))

    assert rv_1cc.score == rv_5cc.score
    # timeseries 엣지 1개, 모순 1개 → ratio=1.0 → score=min(1.0, 1.0/0.2)=1.0
    assert rv_1cc.score == 1.0
    assert rv_5cc.score == 1.0


def test_d5_only_cross_check_edges_is_skipped() -> None:
    """timeseries 엣지가 하나도 없으면(cross_check만 있으면) '시계열 엣지 없음'으로 스킵되어야 한다."""
    graph = EvidenceGraph(corp_code="TESTCO", corp_name="테스트기업")
    graph.add_edge(EvidenceEdge(
        source_id="TESTCO_E-3-1_2024__dart",
        target_id="TESTCO_E-3-1_2024__ocr_structured",
        edge_type="cross_check",
        detail="교차검증 오차 1.0% (dart↔ocr_structured)",
    ))

    rv = score_d5_timeseries(SENTENCE, graph)

    assert rv.detail == "시계열 엣지 없음"
    assert rv.score == 0.0
