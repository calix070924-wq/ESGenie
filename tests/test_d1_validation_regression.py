"""Required before/after reproductions on a finalized real graph."""
import pytest
from esgenie.layer3_detect import score_d1_numeric
from tests.d1_fixtures import CASES, make_evidence

@pytest.mark.parametrize('name,sentence,score,compared,unverified', CASES, ids=[c[0] for c in CASES])
def test_required_cases(name, sentence, score, compared, unverified):
    _, graph, _ = make_evidence()
    axis = score_d1_numeric(sentence, graph)
    assert axis.score == score, axis.detail
    assert axis.abstain == (compared == 0 and unverified > 0)
    assert axis.evaluation['compared_claims'] == compared
    assert axis.evaluation['unverified_claims'] == unverified
    expected_status = ('unavailable' if not compared else 'partial') if unverified else 'complete'
    assert axis.evaluation['status'] == expected_status

@pytest.mark.parametrize('value', [0,.25,999,1000,9999,12345.67,1000000])
def test_notation_variants_have_identical_d1_decisions(value):
    _, graph, _ = make_evidence()
    a = score_d1_numeric(f'온실가스 배출량은 {value}tCO2eq이다.',graph)
    b = score_d1_numeric(f'온실가스 배출량은 {value:,} tCO2eq이다.',graph)
    assert a.score == b.score and a.evaluation['compared_claims'] == b.evaluation['compared_claims'] == 1
    for axis in (a,b):
        assert axis.evaluation['claims'][0]['claim_value'] == value
        assert axis.evaluation['claims'][0]['claim_unit'] == 'tCO2eq'


def test_zero_evidence_is_comparable_and_nonzero_against_zero_is_mismatch():
    from dataclasses import replace
    _, graph, _ = make_evidence()
    graph.resolved_facts['E-3-1'] = replace(graph.resolved_facts['E-3-1'],value=0)
    for value, expected in [(0,0),(100,1)]:
        axis = score_d1_numeric(f'온실가스 배출량은 {value}tCO2eq이다.',graph)
        assert axis.score == expected and axis.evaluation['compared_claims'] == 1
        import json
        json.dumps(axis.to_dict(),allow_nan=False)
