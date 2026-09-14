import pytest
from esgenie.layer3_detect import score_d1_numeric, extract_numeric_claims
from tests.d1_fixtures import make_evidence

@pytest.mark.parametrize('sentence,codes', [
    ('재생에너지 사용 비율은 31%이며 폐기물 재활용 비율은 92%였다.', ['E-4-2','E-6-2']),
    ('폐기물 재활용 비율은 92%, 재생에너지 사용 비율은 31%였다.', ['E-6-2','E-4-2']),
    ('재생에너지 사용 비율은 31%. 폐기물 재활용 비율은 92%였다.', ['E-4-2','E-6-2']),
    ('31%인 재생에너지 사용 비율과 92%인 폐기물 재활용 비율이다.', ['E-4-2','E-6-2']),
    ('31%는 재생에너지 사용 비율이며 92%는 폐기물 재활용 비율이다.', ['E-4-2','E-6-2']),
])
def test_position_and_order_do_not_change_ownership(sentence, codes):
    _, graph, _ = make_evidence()
    assert [c.matched_code for c in extract_numeric_claims(sentence)] == codes
    assert score_d1_numeric(sentence, graph).score == 0
    wrong = sentence.replace('31%', 'TEMP').replace('92%', '31%').replace('TEMP', '92%')
    assert score_d1_numeric(wrong, graph).score == 1


def test_enumeration_is_ambiguous():
    claims = extract_numeric_claims('재생에너지 사용 비율과 폐기물 재활용 비율은 31%와 92%였다.')
    assert all(c.matched_code is None for c in claims)

@pytest.mark.parametrize('sentence', [
    '재생에너지 사용 비율 목표는 100%이며 폐기물 재활용 비율 실적은 31%였다.',
    '폐기물 재활용 비율 실적은 31%이며 재생에너지 사용 비율 목표는 100%이다.',
    '재생에너지 사용 비율 목표는 100%, 폐기물 재활용 비율은 31%였다.',
])
def test_target_clause_does_not_hide_neighbor_actual(sentence):
    _, graph, _ = make_evidence()
    assert score_d1_numeric(sentence, graph).score == 1
