"""실측 보고서 표현의 D1 토픽 귀속 회귀 (2026-07-29)."""
from __future__ import annotations

import pytest

from esgenie.knowledge.kesg_items import ALL_ITEMS, resolve_kesg_code
from esgenie.layer3_detect import _NUMBER_PATTERN, _build_topic_terms, _match_topic_near


BASELINE_TOPIC_TERM_COUNT = 262
EXPANDED_TERMS = {
    "폐기물 발생량": "E-6-1",
    "여성 직원 비율": "S-3-1",
    "이사 출석률": "G-2-1",
    "이사 평균 참석률": "G-2-1",
}


def _assigned_codes(sentence: str) -> list[str | None]:
    """문장의 숫자별 D1 귀속 코드를 반환한다."""
    return [
        _match_topic_near(sentence, match.start(), match.end())[1]
        for match in _NUMBER_PATTERN.finditer(sentence)
    ]


@pytest.mark.parametrize(
    "sentence,expected",
    [
        ("폐기물 발생량은 72,463톤이다.", "E-6-1"),
        ("여성 직원 비율은 24.2%이다.", "S-3-1"),
        ("이사 출석률은 97.5%이다.", "G-2-1"),
        ("이사 평균 참석률은 97.5%이다.", "G-2-1"),
    ],
)
def test_measured_report_terms_recover_d1_assignment(sentence, expected):
    """실측에서 None이던 네 표현이 기대 K-ESG 코드로 귀속된다."""
    assert _assigned_codes(sentence) == [expected]


def test_inside_director_attendance_remains_g22():
    """짧은 `이사 출석률` 추가가 더 구체적인 G-2-2 표현을 삼키지 않는다."""
    assert _assigned_codes("사내이사 출석률은 95%이다.") == ["G-2-2"]


def test_search_terms_remain_disjoint_between_esg_areas():
    """retrieval_gate의 E/S/G 영역 어휘 무중복 전제를 유지한다."""
    terms_by_area = {
        area: {
            term
            for item in ALL_ITEMS
            if item.area == area
            for term in item.search_terms
        }
        for area in "ESG"
    }
    assert terms_by_area["E"].isdisjoint(terms_by_area["S"])
    assert terms_by_area["E"].isdisjoint(terms_by_area["G"])
    assert terms_by_area["S"].isdisjoint(terms_by_area["G"])


def test_topic_term_index_does_not_lose_terms():
    """모호 용어 추가로 기존 262개 토픽이 유실되지 않는다."""
    assert len(_build_topic_terms()) >= BASELINE_TOPIC_TERM_COUNT + len(EXPANDED_TERMS)


@pytest.mark.parametrize("label,expected", EXPANDED_TERMS.items())
def test_expanded_aliases_resolve_exactly(label, expected):
    """OCR backfill alias 확대는 의도한 코드의 exact 변화로 고정한다."""
    assert resolve_kesg_code(label) == (expected, 1.0, "exact")
