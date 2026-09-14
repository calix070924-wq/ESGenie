"""2026-09-14 product policy: absent comparisons never count as verified.

Replaces the previous opt-in/legacy-zero expectations from the task's step 4.
Measured mismatches are retained independently from incomplete claim coverage.
"""
from dataclasses import replace
import pytest
from esgenie.layer3_detect import score_d1_numeric, _build_risk_vector
from esgenie.ssot.detector_5axis import detect_d1_numeric
from esgenie.schemas import AxisScore
from tests.d1_fixtures import make_evidence

@pytest.mark.parametrize('ssot', [False, True])
@pytest.mark.parametrize('sentence,code,reason', [
    ('용수 사용량은 100톤이다.', 'E-5-1', 'no_evidence'),
    ('재생에너지 사용 비율은 31%p였다.', 'E-4-2', 'unit_mismatch'),
    ('총 15건을 수행하였다.', None, 'ambiguous_topic'),
    ('재생에너지 사용 비율은 -31%였다.', 'E-4-2', 'invalid_number'),
    ('재생에너지 사용 비율은 3,1%였다.', 'E-4-2', 'invalid_number'),
])
def test_unverified_reasons_are_distinct(ssot, sentence, code, reason):
    _, graph, _ = make_evidence()
    axis = detect_d1_numeric(sentence, code, graph) if ssot else score_d1_numeric(sentence, graph)
    assert axis.score == 0 and axis.abstain and axis.abstain_reason == reason
    assert axis.evaluation['compared_claims'] == 0
    assert axis.evaluation['unverified_claims'] == 1
    assert axis.evaluation['reasons'] == {reason: 1}

@pytest.mark.parametrize('ssot', [False, True])
@pytest.mark.parametrize('sentence', ['수치가 없는 문장입니다.', '2025년 제3장 IFRS S2 RE100', '재생에너지 비율 100% 전환 목표'])
def test_no_actual_comparison_is_not_abstention(ssot, sentence):
    _, graph, _ = make_evidence()
    axis = detect_d1_numeric(sentence, 'E-4-2', graph) if ssot else score_d1_numeric(sentence, graph)
    assert not axis.abstain and axis.score == 0
    assert axis.evaluation['status'] == 'not_applicable'


def test_missing_graph_is_no_evidence():
    axis = score_d1_numeric('재생에너지 사용 비율은 31%였다.', None)
    assert axis.abstain_reason == 'no_evidence'

@pytest.mark.parametrize('value,score', [(31,0),(92,1)])
def test_partial_verification_preserves_measured_result(value, score):
    _, graph, _ = make_evidence()
    axis = score_d1_numeric(f'재생에너지 사용 비율은 {value}%이며 용수 사용량은 100톤이다.', graph)
    assert axis.score == score and not axis.abstain
    assert axis.evaluation['status'] == 'partial'
    assert (axis.evaluation['compared_claims'], axis.evaluation['unverified_claims']) == (1,1)
    rv = _build_risk_vector(axis, AxisScore(0), AxisScore(0), AxisScore(0))
    assert rv.risk_score == .4 * score
    assert not rv.evaluation_complete
    assert rv.aggregate['incomplete_axes'] == ['D1_numeric']


def test_mixed_unverified_reasons_do_not_mask_each_other():
    _, graph, _ = make_evidence()
    axis = score_d1_numeric('재생에너지 비율은 31%p이며 용수 사용량은 100톤이다.', graph)
    assert axis.abstain_reason == 'no_evidence'
    assert axis.evaluation['reasons'] == {'unit_mismatch':1,'no_evidence':1}


def test_explicit_no_selection_is_not_legacy_missing_metadata():
    _, graph, _ = make_evidence()
    graph.resolved_facts['E-4-2'] = None
    assert score_d1_numeric('재생에너지 비율은 31%이다.', graph).abstain_reason == 'no_evidence'
    del graph.resolved_facts['E-4-2']
    axis = score_d1_numeric('재생에너지 비율은 31%이다.', graph)
    assert not axis.abstain and axis.score == 0


def test_finalized_unit_cannot_reselect_compatible_decoy():
    from esgenie.ssot.evidence_graph import EvidenceNode
    _, graph, _ = make_evidence()
    graph.resolved_facts['E-4-2'] = replace(graph.resolved_facts['E-4-2'], unit='tCO2eq')
    graph.add_node(EvidenceNode('decoy','E-4-2',31,'%',2025,'dart'))
    axis = score_d1_numeric('재생에너지 비율은 31%이다.', graph)
    assert axis.abstain_reason == 'unit_mismatch' and 'decoy' not in axis.evidence


def test_old_axis_payload_and_all_abstention_contract():
    axis = AxisScore(**{'score':0,'detail':'old','evidence':[]})
    assert axis.evaluation_complete
    ab = AxisScore(0,abstain=True,abstain_reason='no_evidence')
    rv = _build_risk_vector(ab,ab,ab,ab)
    assert rv.risk_score is None and rv.aggregate['evaluated_weight'] == 0
    assert rv.numeric_coverage_label == '평가불가'
