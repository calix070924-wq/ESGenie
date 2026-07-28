"""scripts/abstain_prevalence_audit.py 핵심 분류 로직 회귀 가드.

읽기 전용 분석 스크립트라 프로덕션 판정 로직에는 영향이 없지만, 분류
함수(_claim_codes/_classify_code)가 조용히 깨지면 실태 보고서가 틀린
결론을 낼 수 있으므로 고정한다.
"""
from __future__ import annotations

from types import SimpleNamespace

import scripts.abstain_prevalence_audit as audit


def test_claim_codes_extracts_mapped_non_target_codes():
    s = "자발적 이직률은 1.2%를 기록했다."
    assert audit._claim_codes(s) == ["S-2-3"]


def test_claim_codes_excludes_target_context():
    s = "2030년까지 재생에너지 사용 비율을 100%로 늘릴 계획이다."
    assert audit._claim_codes(s) == []


def test_claim_codes_no_number_returns_empty():
    assert audit._claim_codes("지속가능한 미래를 위해 노력하고 있습니다.") == []


class _FakeGraph:
    def __init__(self, metrics_with_nodes: set[str]):
        self._metrics = metrics_with_nodes

    def nodes_by_metric(self, code):
        return [SimpleNamespace(id="n1")] if code in self._metrics else []


def test_classify_code_has_node():
    graph = _FakeGraph({"E-3-1"})
    report = SimpleNamespace(kesg_data={}, raw_text_snippets=[])
    assert audit._classify_code("E-3-1", report, graph) == "has_node"


def test_classify_code_search_failure_text():
    graph = _FakeGraph(set())
    report = SimpleNamespace(
        kesg_data={},
        raw_text_snippets=["당해 온실가스 Scope 1+2 배출량은 전년 대비 감소하였다."],
    )
    assert audit._classify_code("E-3-1", report, graph) == "search_failure_text"


def test_classify_code_search_failure_structured():
    graph = _FakeGraph(set())
    report = SimpleNamespace(kesg_data={"E-3-1": {"value": "해당사항 없음"}}, raw_text_snippets=[])
    assert audit._classify_code("E-3-1", report, graph) == "search_failure_structured"


def test_classify_code_true_non_disclosure():
    graph = _FakeGraph(set())
    report = SimpleNamespace(kesg_data={}, raw_text_snippets=["아무 관련 없는 문장입니다."])
    assert audit._classify_code("E-3-1", report, graph) == "true_non_disclosure"


def test_classify_code_unclassifiable_when_no_keyword_pattern():
    graph = _FakeGraph(set())
    report = SimpleNamespace(kesg_data={}, raw_text_snippets=[])
    # E-9-9는 layer0_evidence_graph._METRIC_KEYWORDS에 정의돼 있지 않은 임의 코드.
    assert audit._classify_code("E-9-9", report, graph) == "unclassifiable_no_pattern"
