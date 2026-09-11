"""Adversarial diagnostics; failures represent proposed correctness requirements.

Synthetic component inputs exercise production functions. They do not measure
real-world error frequency. Existing implementations are intentionally unchanged.
"""
from types import SimpleNamespace

import pytest

from esgenie.supplychain.schema import Question, Framework
from esgenie.supplychain.responder import build_response_sheet
from esgenie.supplychain.claims import SupplierClaim, parse_saq_claims
from esgenie.supplychain import claims as claims_module
from esgenie.ssot.evidence_graph import EvidenceGraph, EvidenceNode, TextNode
from esgenie.ssot.audit_trace import DataPoint, EvidenceLink, build_data_points
from esgenie.layer3_detect import score_d3_semantic, _build_risk_vector
from esgenie.schemas import AxisScore
from esgenie.layer3_disclosure import DisclosureReport, OrphanRatio


def fw(code, kind='numeric'):
    return Framework('audit', 'audit', (Question('Q1', 'E', code, kind, True, (code,)),))


def numeric(value, claim_value, unit='%', claim_unit='%', code='E-6-2'):
    dp = DataPoint(code, code, value, unit, 2025, .95, 'verified', 0,
                   [EvidenceLink('bill.pdf', 'evidence_pack/bill.pdf', 'ocr_structured', node_id='n')])
    return build_response_sheet(fw(code), data_points=[dp],
        supplier_claims={code: SupplierClaim(code, claim_value, claim_unit)}).answers[0]


def describe(ans):
    return f'status={ans.status}; value={ans.value}; flags={ans.flags}; rationale={ans.rationale}'


@pytest.mark.parametrize('actual,claim,expected', [(29.3,29.3,'verified'), (29.3,92,'flagged'), (0,0,'verified'), (100,100,'verified')])
def test_valid_rate_controls(actual, claim, expected):
    ans=numeric(actual, claim)
    assert ans.status == expected, describe(ans)


@pytest.mark.parametrize('actual,claim', [(-5,-5),(105,105),(float('nan'),29.3),(29.3,float('nan'))], ids=['negative','above_100','nan_evidence','nan_claim'])
def test_invalid_rate_cannot_be_verified_as_matching(actual, claim):
    ans=numeric(actual,claim)
    assert ans.status != 'verified' and not any('자가신고 일치' in f for f in ans.flags), describe(ans)


def test_equivalent_energy_units_are_not_a_discrepancy():
    ans=numeric(1000,1,unit='kWh',claim_unit='MWh',code='E-4-1')
    assert ans.status != 'flagged', describe(ans)


def test_equal_numbers_with_different_energy_units_are_not_matching():
    ans=numeric(1,1,unit='kWh',claim_unit='MWh',code='E-4-1')
    assert not any('자가신고 일치' in f for f in ans.flags), describe(ans)


def test_valid_matching_energy_control():
    ans=numeric(1000,1000,unit='kWh',claim_unit='kWh',code='E-4-1')
    assert ans.status == 'verified', describe(ans)


def presence(entry, graph=None):
    return build_response_sheet(fw('E-1-1','yes_no_evidence'),
        extraction=SimpleNamespace(mapped={'E-1-1':entry},missing=[]), evidence_graph=graph).answers[0]


def test_dangling_evidence_id_does_not_prove_policy():
    ans=presence({'value': True, 'evidence_node_ids':['nonexistent_node']}, EvidenceGraph('X','X'))
    assert ans.status != 'verified', describe(ans)


def test_explicit_negative_policy_answer_is_not_changed_to_yes():
    graph=EvidenceGraph('X','X')
    graph.add_text_node(TextNode('policy','환경','환경 경영목표가 수립되지 않았다.','E-1-1','policy.pdf',0))
    ans=presence({'value':False,'evidence_node_ids':['policy']},graph)
    assert ans.value is not True, describe(ans)


def test_positive_policy_link_control():
    graph=EvidenceGraph('X','X')
    graph.add_text_node(TextNode('policy','환경','환경 경영목표를 수립한다.','E-1-1','policy.pdf',0))
    ans=presence({'value':True,'evidence_node_ids':['policy']},graph)
    assert ans.value is True and ans.status == 'verified', describe(ans)


def test_unlinked_policy_control():
    ans=presence({'value':True,'evidence_node_ids':[]})
    assert ans.status == 'self_reported', describe(ans)


@pytest.mark.parametrize('text,expected', [
    ('2025년 재활용률 29.3% 달성',29.3),
    ('2025년 재활용률 0% 달성',0),
    ('2030년 재활용률 목표 92%. 2025년 재활용률 29.3% 달성',29.3),
    ('2030년 재활용률 목표 92%',None),
    ('2025년 재활용률 -5%',None),
    ('2025년 재활용률 105%',None),
], ids=['actual','zero','target_before_actual','target_only','negative','above_100'])
def test_saq_actual_claim_extraction(monkeypatch,text,expected):
    # Controlled PDF-extraction text: parser boundary test, not an OCR benchmark.
    monkeypatch.setattr(claims_module,'_extract_text',lambda _:text)
    parsed=parse_saq_claims(['audit_saq.pdf'])
    actual=parsed.get('E-6-2')
    if expected is None:
        assert actual is None, f'Invalid or target-only text promoted to actual: {actual}'
    else:
        assert actual is not None and actual.value == expected, f'expected={expected}; actual={actual}'


def make_node(nid,value,role='total',period=2025,confidence=.9):
    return EvidenceNode(nid,'E-4-1',value,'TJ',period,'ocr',origin='ocr_structured',
                        source_file='energy.pdf',confidence=confidence,value_role=role)


def test_selected_actual_survives_audit_and_response_export():
    graph=EvidenceGraph('X','X'); graph.report_year=2025
    graph.add_node(make_node('actual',100,confidence=.9))
    graph.add_node(make_node('target',60,role='target',confidence=.99))
    graph.representative_node_ids['E-4-1']='actual'
    points=build_data_points(graph,{'E-4-1':0},target_codes=['E-4-1'])
    ans=build_response_sheet(fw('E-4-1'),data_points=points).answers[0]
    assert ans.value == 100, f'L1 representative=actual 100; exported {describe(ans)}'


def test_future_target_cannot_replace_reporting_year_actual():
    graph=EvidenceGraph('X','X'); graph.report_year=2025
    graph.add_node(make_node('actual',100))
    graph.add_node(make_node('target',60,role='target',period=2030))
    graph.representative_node_ids['E-4-1']='actual'
    point=build_data_points(graph,{'E-4-1':0},target_codes=['E-4-1'])[0]
    assert (point.period,point.value)==(2025,100), point.to_dict()


def test_single_valid_actual_export_control():
    graph=EvidenceGraph('X','X'); graph.add_node(make_node('actual',100))
    point=build_data_points(graph,{'E-4-1':0},target_codes=['E-4-1'])[0]
    assert point.value==100 and point.period==2025


def test_missing_semantic_evidence_is_abstention_requirement():
    d3=score_d3_semantic('재활용률은 29.3%다.',[])
    rv=_build_risk_vector(AxisScore(0),AxisScore(0),d3,AxisScore(0))
    assert d3.abstain, f'D3={d3.to_dict()}; aggregate={rv.aggregate}'


def test_d6_overrides_matching_numeric_answer_control():
    dp=DataPoint('E-6-2','재활용률',29.3,'%',2025,.95,'verified',0)
    disclosure=DisclosureReport(.6,'high',orphan_ratios=[OrphanRatio('E-6-2','재활용률',['E-6-1'],'총량 없음')])
    ans=build_response_sheet(fw('E-6-2'),data_points=[dp],disclosure=disclosure,
        supplier_claims={'E-6-2':SupplierClaim('E-6-2',29.3)}).answers[0]
    assert ans.status=='flagged' and any('D6' in f for f in ans.flags),describe(ans)


def test_cache_prompt_change_requires_another_provider_call(monkeypatch,tmp_path):
    from esgenie import llm, llm_cache
    client=llm.LLMClient()  # FORCE_MOCK prevents real SDK creation.
    calls=[]
    def fake_create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='fake provider response'))])
    client._openai_client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)))
    monkeypatch.setattr(llm.SETTINGS,'force_mock',False)
    monkeypatch.setattr(llm.SETTINGS,'pii_mask',False)
    monkeypatch.setenv('ESGENIE_FORCE_MOCK','0')
    monkeypatch.setenv('ESGENIE_LLM_CACHE_DIR',str(tmp_path))
    llm_cache.reset_stats()
    first=client.complete('system','original')
    repeat=client.complete('system','original')
    changed=client.complete('system','new input')
    assert len(calls)==2
    assert [first.meta['cache'],repeat.meta['cache'],changed.meta['cache']]==['miss','hit','miss']


@pytest.mark.parametrize('reply', ['예', '아니오'])
def test_production_survey_input_cannot_create_verified_yes(reply):
    from esgenie.layer1_extract import ExtractionResult
    from esgenie.pipeline import _apply_survey_answers

    extraction = ExtractionResult('audit', {}, ['E-1-1'], 0, profile='sme')
    _apply_survey_answers(extraction, {'E-1-1': {'yn': reply, 'text': ''}})
    sheet = build_response_sheet(fw('E-1-1', 'yes_no_evidence'),
                                 extraction=extraction, evidence_graph=EvidenceGraph('X', 'X'))
    ans = sheet.answers[0]
    assert ans.status != 'verified', f'survey={reply}; mapped={extraction.mapped}; {describe(ans)}'
    if reply == '아니오':
        assert ans.value is not True, describe(ans)


def test_production_l1_selection_survives_l5_export():
    from esgenie.layer1_extract import ExtractionResult
    from esgenie.ssot.ssot_pipeline import _merge_ssot_evidence
    from esgenie.pipeline import _build_risk_rows

    graph = EvidenceGraph('X', 'X')
    graph.report_year = 2025
    total = make_node('total', 100, confidence=.9)
    total.raw_text = '에너지 사용량 전사 합계 = 100 TJ'
    component = make_node('component', 60, role='component', confidence=.99)
    component.raw_text = '에너지 사용량 국내 사업장 = 60 TJ'
    graph.add_node(total)
    graph.add_node(component)
    extraction = ExtractionResult('audit', {}, ['E-4-1'], 0, profile='sme',
                                  by_area={a: {'present': 0, 'total': 1} for a in ('P', 'E', 'S', 'G')})
    _merge_ssot_evidence(extraction, graph)
    assert extraction.mapped['E-4-1']['value'] == 100, extraction.mapped
    assert graph.representative_node_ids['E-4-1'] == 'total'
    scores, _ = _build_risk_rows(graph, target_codes=['E-4-1'])
    points = build_data_points(graph, scores, target_codes=['E-4-1'])
    ans = build_response_sheet(fw('E-4-1'), extraction=extraction,
                              data_points=points, evidence_graph=graph).answers[0]
    assert ans.value == extraction.mapped['E-4-1']['value'], (
        f'L1={extraction.mapped["E-4-1"]["value"]}; d1_scores={scores}; L5 response={describe(ans)}')
