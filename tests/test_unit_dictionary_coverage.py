"""단위 사전 확장 (2026-09-29) — 넓힌 것과 **합치지 않은 것**을 함께 고정한다.

두 보고서 지표 7,201건의 단위를 전수로 넣어 보니 사전 밖이 15.0%·28.3%였고, 금액 단위만
251건이 사전에 없었다(`천 원` 230 · `조 원` 11 등). 사전을 넓히면 대표값 선택의 단위 축과
환산 경로가 달라지므로, **의도한 환산**과 **막아야 하는 환산**을 같은 무게로 시험한다.
"""
from __future__ import annotations

import pytest

from esgenie.rag_gates.units import (
    convert_to_common,
    normalize_unit,
    units_compatible,
)


# ── 실측으로 확인한 빠진 칸 ──────────────────────────────────────────────────

def test_measured_missing_amount_units_are_now_read():
    """`천 원` 230건·`조 원` 11건이 사전 밖이었다 — `억 원`·`백만 원`은 있는데 빠진 칸이다."""
    assert normalize_unit("천 원") == "천원"
    assert normalize_unit("조 원") == "조원"
    assert normalize_unit("십억 원") == "십억원"
    assert normalize_unit("억 원") == "억원"       # 기존 동작 유지


def test_tco2e_with_a_single_e_is_the_same_unit():
    """삼성전기 97건이 `tCO2e` 표기라 사전 밖으로 떨어지고 있었다."""
    assert normalize_unit("tCO2e") == "tCO2eq"
    assert normalize_unit("tCO2 eq") == "tCO2eq"
    assert normalize_unit("톤CO2e") == "tCO2eq"


def test_subscript_and_english_notations_are_read():
    """확장 후 재측정에서 남아 있던 표기 — 전각표로는 아래 첨자가 잡히지 않는다."""
    assert normalize_unit("tCO₂eq") == "tCO2eq"    # 모비스 2건
    assert normalize_unit("percent") == "%"         # 삼성전기 2건


def test_scaled_mass_and_headcount_keep_their_multiplier():
    """`만 톤` 5건·`만 명` 2건 — 배율을 못 읽으면 값이 10,000배 작게 실린다."""
    assert normalize_unit("만 톤") == "만t"
    assert normalize_unit("만 명") == "만명"
    assert convert_to_common(1.0, "만t", "t") == pytest.approx(10_000.0)
    assert convert_to_common(1.0, "만명", "명") == pytest.approx(10_000.0)


def test_volume_units_are_read():
    assert normalize_unit("m3") == "m3"
    assert normalize_unit("㎥") == "m3"
    assert normalize_unit("리터") == "L"
    assert normalize_unit("kL") == "kL"


def test_dollar_notations_are_read():
    assert normalize_unit("백만 $") == "백만달러"
    assert normalize_unit("억 달러") == "억달러"
    assert normalize_unit("억 USD") == "억달러"
    assert normalize_unit("USD") == "달러"


# ── 의도한 환산 ──────────────────────────────────────────────────────────────

def test_korean_currency_scales_convert_within_the_won_family():
    assert convert_to_common(1.0, "조원", "억원") == pytest.approx(10_000.0)
    assert convert_to_common(230.0, "천원", "원") == pytest.approx(230_000.0)
    assert convert_to_common(21_116.0, "백만원", "원") == pytest.approx(21_116_000_000.0)


def test_volume_converts_within_its_own_family():
    assert convert_to_common(1.0, "m3", "L") == pytest.approx(1_000.0)


def test_emission_scales_convert():
    assert convert_to_common(1_000.0, "kgCO2eq", "tCO2eq") == pytest.approx(1.0)


def test_added_energy_units_convert_on_the_kwh_axis():
    assert convert_to_common(1.0, "toe", "MWh") == pytest.approx(11.63)
    assert convert_to_common(1.0, "Gcal", "kWh") == pytest.approx(1_163.0)


# ── 합치면 사고가 나는 것 (이쪽이 더 중요하다) ──────────────────────────────

def test_won_and_dollar_are_never_compatible():
    """환율을 모르는 모듈이 통화를 환산하면 G4가 호환이라 판정한 뒤 값이 틀어진다."""
    assert units_compatible("원", "달러") is False
    assert units_compatible("억원", "억달러") is False
    assert convert_to_common(100.0, "억원", "억달러") is None


def test_volume_and_mass_stay_separate_outside_water_metrics():
    """물만 밀도 1 t/m³로 같이 본다 — 그 예외는 물 지표 코드에서만 적용된다.

    전역에서 합치면 폐기물 발생량 톤이 m³로 환산된다.
    """
    assert units_compatible("m3", "t") is False
    assert convert_to_common(100.0, "m3", "t") is None


def test_emissions_are_not_plain_mass():
    assert units_compatible("tCO2eq", "t") is False
    assert units_compatible("kgCO2eq", "kg") is False


def test_permille_is_still_not_percent():
    assert units_compatible("%", "‰") is False


def test_count_units_are_not_convertible_into_each_other():
    """개수 단위는 사전에 들어왔을 뿐 환산군이 없다 — 순위 판정은 이전과 같다."""
    assert normalize_unit("개사") == "개사"
    assert units_compatible("개", "회") is False
    assert units_compatible("개사", "명") is False


def test_unknown_unit_is_still_unknown():
    """사전을 넓힌 것이 '모르는 단위를 아는 척한다'는 뜻이 아니다."""
    assert normalize_unit("갤런") is None
    assert normalize_unit("ton/억 원") is None      # 복합 단위는 그대로 미상이다
    assert normalize_unit("조 억 원") is None       # 배율이 둘 붙은 깨진 표기


def test_compound_units_with_a_scale_in_the_denominator_stay_unknown():
    """분모에 배율이 있는 복합 단위 158건 — 배율로 읽으면 값이 반대로 틀어진다."""
    for unit in ("ton/억 원", "TJ/억 원", "MWh/억원", "건/백만 시간", "백만 원/명",
                 "m3/억 원", "tCO2e/억 원"):
        assert normalize_unit(unit) is None, unit


def test_ambiguous_korean_units_are_deliberately_left_out():
    """`주`(주식/주간)·`일`·`점`은 뜻이 갈린다 — 사전에 넣으면 다른 축을 같다고 본다."""
    for unit in ("주", "일", "년", "점", "배", "수"):
        assert normalize_unit(unit) is None, unit
