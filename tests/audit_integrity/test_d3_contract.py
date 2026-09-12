from types import SimpleNamespace
import pytest
from esgenie.schemas import AxisScore, format_score
from esgenie.layer3_detect import _build_risk_vector, score_d3_semantic
from esgenie.layer3_judge import _rebuild_vector
from esgenie.layer4_verify import _can_converge, _compute_text_risk_vector
from esgenie.layer6_report import _block_esg
from esgenie.ssot.evidence_graph import EvidenceGraph


def abstain(score=0):
    return AxisScore(score, abstain=True, abstain_reason='no_evidence')


@pytest.mark.parametrize('kind', ['no-chunks','empty-index','no-results','empty-text'])
def test_d3_no_evidence(kind):
    index = None
    chunks = []
    if kind == 'empty-index':
        index = SimpleNamespace(_docs=[], search=lambda *a,**kw: [])
    elif kind == 'no-results':
        index = SimpleNamespace(_docs=[SimpleNamespace(text='source')], search=lambda *a,**kw: [])
    elif kind == 'empty-text':
        chunks = [{'text':'   ','id':'empty'}]
    axis = score_d3_semantic('재활용률 43.8%', chunks, prebuilt_index=index)
    assert axis.abstain and axis.abstain_reason == 'no_evidence'


def test_d3_normal_evidence_remains_evaluated():
    doc = SimpleNamespace(text='재활용률 43.8%', meta={'id':'source'})
    index = SimpleNamespace(_docs=[doc], search=lambda *a,**kw: [(doc, .8)])
    axis = score_d3_semantic(doc.text, [], prebuilt_index=index)
    assert axis.score == 0 and not axis.abstain and axis.evidence == ['source']


def test_partial_reweights_valid_axes_and_hybrid_uses_same_contract():
    rv = _build_risk_vector(AxisScore(1), AxisScore(0), abstain(), AxisScore(0))
    assert rv.risk_score == pytest.approx(.4 / .75, abs=1e-4)
    assert rv.aggregate['evaluated_weight'] == .75
    assert rv.aggregate['evaluation_status'] == 'partial' and not rv.evaluation_complete
    axes = {k:getattr(rv,k) for k in ('D1_numeric','D2_modifier','D3_semantic','D5_timeseries')}
    assert _rebuild_vector(axes).aggregate == rv.aggregate


def test_all_abstained_has_no_score_or_low_risk_badge():
    rv = _build_risk_vector(*(abstain(.95) for _ in range(4)))
    assert rv.risk_score is None and rv.level == 'unavailable' and not rv.high_axes()
    assert rv.to_dict()['aggregate']['risk_score'] is None
    assert rv.aggregate['evaluated_axes'] == [] and format_score(rv.risk_score) == '평가불가'


@pytest.mark.parametrize('partial', [True, False])
def test_abstention_cannot_end_verification_as_pass(partial):
    rv = _build_risk_vector(AxisScore(0), AxisScore(0), abstain() if partial else AxisScore(0), AxisScore(0))
    det = SimpleNamespace(risk_score=0, risk_vector=rv)
    assert _can_converge(det, SimpleNamespace(decision='ACCEPT'), 30, True) is (not partial)


def test_empty_generated_text_is_unavailable():
    gen = SimpleNamespace(context=SimpleNamespace(kesg_hits=[],corp_hits=[]))
    rv = _compute_text_risk_vector('', EvidenceGraph('T','T'), gen, None)
    assert rv.risk_score is None and not rv.evaluation_complete


@pytest.mark.parametrize('all_axes', [True, False])
def test_report_states_abstention_instead_of_zero_risk(all_axes):
    rv = _build_risk_vector(abstain() if all_axes else AxisScore(0),
                            abstain() if all_axes else AxisScore(0), abstain(),
                            abstain() if all_axes else AxisScore(0))
    verify = SimpleNamespace(final=SimpleNamespace(detection=SimpleNamespace(risk_vector=rv)),
                             final_score=None if all_axes else 0, final_band=rv.evaluation_label,
                             iterations_used=0,hitl_required=True,final_text='본문')
    md = _block_esg(SimpleNamespace(sections={'E':verify}), 'E').body_md
    assert '평가불가' in md and '기권 축' in md and '사람 검토 필요' in md
    assert ('부분 평가' in md) is (not all_axes)
