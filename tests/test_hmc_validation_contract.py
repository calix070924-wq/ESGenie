"""검증기 자체의 계약. 이 합성 JSON은 제품 계산의 검증 근거로 사용하지 않는다."""
import copy

import pytest

from scripts.hmc_integrity_validation import REQUIRED, validate_payload, negative_controls
from esgenie.supplychain import get_framework


@pytest.fixture
def valid_payload():
    answers=[dict(qid=q.qid) for q in get_framework('hmc').questions]
    checklist=[]
    for key,qid in REQUIRED.items():
        a=next(a for a in answers if a['qid']==qid)
        a.update(status='self_reported', boundary_label='검증기 전달계약', boundary={'site_scope':'site'}, completeness='partial', evidence_links=[{},{}])
        if key=='communication':
            a.update(status='draft_ready',draft_citations=[{'source_file':'의사소통절차서_2025.pdf','page':0}],draft_grounding={'decision':'ACCEPT','soft_flags':[]})
            continue
        a['value'],a['unit']={'energy':(.873988,'TJ'),'renewable':(10.6,'%'),'emissions':(88.397,'tCO2eq'),'waste':(29.3,'%')}[key]
        names = ['01_전기요금청구서_2026-05.pdf','02_도시가스요금고지서_2026-05.pdf'] if key in {'energy','emissions'} else ['12_재생에너지사용현황_2026-06.pdf'] if key=='renewable' else ['03_사업장폐기물_위탁처리명세_2026-04.pdf']
        a['evidence_links']=[{'file_name':n,'page':0} for n in names]
        if key in {'energy','emissions'}:
            a['boundary'].update(period_start='2026-04-25',period_end='2026-05-24')
        if key=='renewable': a['scope_notes']=['설비 동일성 / 분모 / 조달수단 확인']
        if key=='waste': a.update(boundary={'period_start':'2026-04-01','period_end':'2026-04-30'},comparison='mismatch')
        checklist.append({'qid':qid,'action':'범위 확인·보완','request':'92 vs 29.3 검토'})
    return {'answers':answers,'checklist':checklist,'summary':{'auto_pct':100,'draft_pct':0,'hitl_pct':0,'pending_pct':0}}


def test_validator_accepts_complete_contract(valid_payload):
    assert validate_payload(valid_payload,controlled=True)['failed']==[]


@pytest.mark.parametrize('mutation',['missing_question','empty_boundary','changed_value','wrong_page','missing_checklist'])
def test_R28_validator_rejects_each_corruption(valid_payload,mutation):
    assert negative_controls(valid_payload)[mutation]


def test_empty_input_cannot_pass():
    result=validate_payload({},controlled=True)
    assert len(result['failed'])>=6
