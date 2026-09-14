import pytest
from esgenie.layer3_detect import extract_numeric_claims, _NUMBER_PATTERN, _extract_claim_value_for_code

@pytest.mark.parametrize('value', ['0', '0.25', '999', '1000', '9999', '12345.67', '1000000'])
@pytest.mark.parametrize('space', ['', ' '])
def test_full_numeric_variants(value, space):
    grouped = format(float(value), ',f').rstrip('0').rstrip('.') if '.' in value else format(int(value), ',')
    a, = extract_numeric_claims(f'온실가스 배출량은 {value}{space}tCO2eq이다.')
    b, = extract_numeric_claims(f'온실가스 배출량은 {grouped}{space}tCO2eq이다.')
    assert (a.number, a.unit, a.matched_code) == (b.number, b.unit, b.matched_code)
    assert a.number == float(value) and not a.issue

@pytest.mark.parametrize('token', ['9,99', '12,34,567', '1234,567', '1.2.3', '1,,000', '1e3', '.5'])
def test_invalid_token_is_never_partially_accepted(token):
    sentence = f'온실가스 배출량은 {token} tCO2eq이다.'
    assert list(_NUMBER_PATTERN.finditer(sentence)) == []
    claim, = extract_numeric_claims(sentence)
    assert claim.number is None and claim.issue == 'invalid_number'
    assert claim.raw == f'{token} tCO2eq'

@pytest.mark.parametrize('token,value,issue', [('-100',-100,'invalid_number'),('−100',-100,'invalid_number'),('+100',100,None)])
def test_sign_preserved(token, value, issue):
    claim, = extract_numeric_claims(f'온실가스 배출량은 {token} tCO2eq이다.')
    assert claim.number == value and claim.issue == issue


def test_unit_tokens_scales_and_identifiers():
    assert extract_numeric_claims('Scope3 배출량, RE100, IFRS S2, 2025년, 제3장') == []
    assert [c.unit for c in extract_numeric_claims('재생에너지 31%p, 재활용률 92%')] == ['%p', '%']
    claim, = extract_numeric_claims('온실가스 배출량 2만 tCO2eq')
    assert claim.number == 20000 and claim.unit == 'tCO2eq'


def test_d5_shared_extraction_preserves_full_value():
    for token in ['9999', '9,999']:
        assert _extract_claim_value_for_code(f'온실가스 배출량 {token} tCO2eq', 'E-3-1') == 9999
