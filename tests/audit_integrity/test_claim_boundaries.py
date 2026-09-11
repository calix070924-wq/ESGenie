import pytest
from esgenie.supplychain.claims import parse_saq_claims, merge_claims, manual_claims
from esgenie.supplychain.responder import build_response_sheet
from esgenie.ssot.audit_trace import DataPoint, EvidenceLink
from esgenie.supplychain.schema import Framework, Question
from esgenie.supplychain.mapping import _reconcile_claim
from esgenie.supplychain.schema import Answer
from esgenie.supplychain.claims import SupplierClaim


@pytest.mark.parametrize('text,expected', [
    ('2030년 재활용률 목표 90.2% / 2025년 재활용률 43.8% 달성', 43.8),
    ('2025년 재활용률 43.8% 달성 / 2030년 재활용률 90.2% 목표', 43.8),
    ('재활용률 계획 90.2%\n재활용률 43.8% 달성', 43.8),
    ('재활용률 90.2% 전망', None), ('재활용률 −4%', None),
    ('매립·소각 -8%', None), ('재활용률 1000%', None),
])
def test_local_context_and_signed_rate(monkeypatch, text, expected):
    monkeypatch.setattr('esgenie.supplychain.claims._extract_text', lambda _: text)
    claims = parse_saq_claims(['saq.pdf'])
    assert (claims.get('E-6-2').value if claims else None) == expected
    if expected is None:
        assert claims.diagnostics


def test_conflicting_actuals_require_review_and_manual_can_override(monkeypatch):
    monkeypatch.setattr('esgenie.supplychain.claims._extract_text', lambda _: '2025년 재활용률 43.8%\n2025년 재활용률 63.8%')
    claims = parse_saq_claims(['saq.pdf'])
    claim = claims['E-6-2']
    assert claim.value is None and claim.status == 'ambiguous'
    assert len(claim.candidates) == 2 and all(c['period'] == 2025 for c in claim.candidates)
    ans = next(a for a in build_response_sheet('saq5_env', supplier_claims=claims).answers if a.qid == 'SAQ-E-NUM-WASTE')
    assert ans.status == 'flagged' and ans.value is None
    assert ans.self_reports[0]['candidates'][0]['source'] == 'saq:saq.pdf'
    override = merge_claims(claims, manual_claims({'E-6-2': 43.8}))
    assert override['E-6-2'].value == 43.8 and override['E-6-2'].source == 'manual'


def test_numeric_ghost_link_cannot_verify():
    point = DataPoint('E-4-1', 'energy', 2, 'MWh', 2025, .99, 'verified', 0,
                      [EvidenceLink('ghost.pdf', '', 'ocr_structured', node_id='ghost')])
    ans = build_response_sheet(Framework('test', 'test', (Question('q', 'E', 'energy', 'numeric', kesg_codes=('E-4-1',)),)), data_points=[point]).answers[0]
    assert ans.status == 'self_reported' and not ans.evidence_links


@pytest.mark.parametrize('evidence,claim', [(-1,-1), (101,101), (float('nan'),float('nan')), (float('inf'),float('inf')), (-float('inf'),0), (0,-float('inf')), ('bad',0), (0,'bad')])
def test_invalid_both_sides_never_match(evidence, claim):
    ans = _reconcile_claim(Answer('q','E','rate',evidence,'verified'), SupplierClaim('E-6-2',claim), evidence, '%', code='E-6-2')
    assert ans.status == 'flagged' and not any('자가신고 일치' in f for f in ans.flags)


@pytest.mark.parametrize('evidence,claim,expected', [(40,49.99,'verified'), (40,50,'flagged'), (0,0,'verified'), (100,100,'verified')])
def test_percentage_point_boundary(evidence,claim,expected):
    ans = _reconcile_claim(Answer('q','E','rate',evidence,'verified'), SupplierClaim('E-6-2',claim), evidence, '%', code='E-6-2')
    assert ans.status == expected


@pytest.mark.parametrize('evidence,claim,expected', [(0,0,'verified'), (0,1,'flagged'), (1000,1000,'verified'), (1000,1149.9,'verified'), (1000,1150,'flagged')])
def test_non_rate_relative_error_and_zero(evidence, claim, expected, monkeypatch):
    monkeypatch.setattr('esgenie.config.D1_THRESHOLD', .15)
    ans = _reconcile_claim(Answer('q','E','energy',evidence,'verified'), SupplierClaim('E-4-1',claim,'kWh'), evidence, 'kWh', code='E-4-1')
    assert ans.status == expected
    assert all('%p' not in f for f in ans.flags)


def test_rate_code_cannot_convert_unrelated_physical_unit():
    ans = _reconcile_claim(Answer('q','E','rate',2,'verified'), SupplierClaim('E-6-2',2,'%'), 2,'kWh',code='E-6-2')
    assert ans.status == 'flagged' and not any('자가신고 일치' in f for f in ans.flags)
