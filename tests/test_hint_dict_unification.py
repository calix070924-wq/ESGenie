"""OCR hint 코드 배정 사전 통합 회귀 테스트.

실측 근거는 ``data/_cache/ocr``의 5개사 LLM 원본 응답이다.  코드 후보를
``kesg_items.search_terms`` 단일 출처로 넓히되, 기존 G1~G6 게이트와 모호 별칭 배제는
그대로 유지하는지 고정한다.
"""
from __future__ import annotations

from collections import defaultdict

import pytest

from esgenie.knowledge import kesg_items
from esgenie.layer3_detect import _build_topic_terms
from esgenie.ssot.evidence_graph import _resolve_kesg_code
from esgenie.ssot.ocr_router import ExtractedMetric


def _metric(hint: str, unit: str, *, guess: str | None = None) -> ExtractedMetric:
    return ExtractedMetric(
        metric_hint=hint,
        value=1.0,
        unit=unit,
        period="2025",
        kesg_code_guess=guess,
    )


@pytest.mark.parametrize(
    "hint,unit,expected",
    [
        ("Scope 3 배출량 국내 합계", "tCO2e", "E-3-2"),
        ("산업용수 사용량", "m³", "E-5-1"),
        ("대기오염물질 배출량 질소산화물(NOx)", "톤", "E-7-1"),
        ("총 Scope 3 배출량", "tCO2e", "E-3-2"),
        ("총 용수 취수량", "m³", "E-5-1"),
        ("대기오염 배출량 NOx", "ton", "E-7-1"),
        ("수질오염 배출량 BOD", "ton", "E-7-2"),
    ],
)
def test_cached_hints_recover_assignment(hint: str, unit: str, expected: str) -> None:
    """LG화학·삼성전기 캐시에 실제로 있는 7개 hint가 제 코드를 받는다."""
    assert _resolve_kesg_code(_metric(hint, unit)) == expected


def test_scope3_overrides_wrong_llm_scope12_guess() -> None:
    """삼성전기 원장의 Scope 3 값이 E-3-1에 남지 않고 E-3-2로 교정된다."""
    assert _resolve_kesg_code(
        _metric("총 Scope 3 배출량", "tCO2e", guess="E-3-1")
    ) == "E-3-2"


def test_scope3_leftmost_wins_explanatory_scope12_text() -> None:
    """동길이 Scope 별칭은 지표명으로 먼저 나온 Scope 3가 이긴다."""
    hint = "Scope 3 배출량 - Scope 1이나 2에 포함되지 않는 연료 및 에너지 관련 활동"
    assert _resolve_kesg_code(_metric(hint, "tCO2 eq")) == "E-3-2"


def test_intensity_never_becomes_absolute_air_emission() -> None:
    """G1 원단위 가드는 넓어진 별칭보다 먼저 적용된다."""
    assert _resolve_kesg_code(
        _metric("대기오염물질 배출량 집약도", "kg/억원")
    ) is None


def test_designated_waste_stays_out_of_total() -> None:
    """총량의 하위 분류인 지정폐기물은 E-6-1을 차지하지 않는다."""
    assert _resolve_kesg_code(_metric("지정폐기물", "톤")) is None


def test_fuzzy_only_label_is_rejected() -> None:
    """배정 단계는 fuzzy 후보를 받지 않고 exact 후보만 채택한다."""
    code, _confidence, method = kesg_items.resolve_kesg_code("자발적이직율")
    assert method == "fuzzy"
    assert code == "S-2-3"
    assert _resolve_kesg_code(_metric("자발적이직율", "%")) is None


def test_search_terms_appended_without_topic_loss() -> None:
    """실측 용어 4개가 추가되고 모호화로 기존 토픽 용어가 사라지지 않는다."""
    expected = {
        "E-6-1": "폐기물 발생량",
        "S-3-1": "여성 직원 비율",
        "G-2-1": "이사 출석률",
        "G-2-1#2": "이사 평균 참석률",
    }
    by_code = {item.code: item for item in kesg_items.ALL_ITEMS}
    assert expected["E-6-1"] in by_code["E-6-1"].search_terms
    assert expected["S-3-1"] in by_code["S-3-1"].search_terms
    assert expected["G-2-1"] in by_code["G-2-1"].search_terms
    assert expected["G-2-1#2"] in by_code["G-2-1"].search_terms

    # 추가 전 현재 기준선은 268개다. 중복 없이 4개 append하면 고유 alias도 줄지 않아야 한다.
    assert len(kesg_items._ALIAS_UNIQUE) >= 272


def test_search_terms_keep_area_disjoint_and_specific_director_match() -> None:
    """영역 중복은 0건이고 더 구체적인 사내이사 용어가 G-2-2로 남는다."""
    by_area: dict[str, set[str]] = defaultdict(set)
    for item in kesg_items.ALL_ITEMS:
        by_area[item.area].update(kesg_items._normalize_label(t) for t in item.search_terms)

    assert not (by_area["E"] & by_area["S"])
    assert not (by_area["E"] & by_area["G"])
    assert not (by_area["S"] & by_area["G"])
    assert kesg_items.resolve_kesg_code("사내이사 출석률")[:1] == ("G-2-2",)


def test_topic_terms_do_not_drop_existing_terms() -> None:
    """한 용어의 다중 코드 귀속으로 _build_topic_terms 결과가 감소하지 않는다."""
    terms = _build_topic_terms()
    # 변경 전 실측 기준선은 262개. 신규 4개가 모호화 없이 모두 살아야 한다.
    assert len(terms) >= 266
    assert any(
        code == "G-2-2" and term == "사내이사출석률"
        for term, _topic, code in terms
    )
