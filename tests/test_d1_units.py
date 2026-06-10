"""D1 단위 검증 + 연도/번호 오탐 필터 테스트 (단점 4 해소)."""
from __future__ import annotations

import pytest

from esgenie.layer3_detect import canon_unit, units_compatible, _score_d1_numeric
from esgenie.ssot import detector_5axis as det
from esgenie.ssot.detector_5axis import _extract_numbers
from esgenie.ssot.evidence_graph import EvidenceGraph, EvidenceNode


def _graph(value: float, unit: str, metric: str = "E-4-1") -> EvidenceGraph:
    g = EvidenceGraph("LOCAL", "테스트")
    g.add_node(EvidenceNode(
        id=f"LOCAL_{metric}_2025", metric=metric, value=value, unit=unit,
        period=2025, source="dart", origin="dart",
    ))
    return g


# ---- 단위 정규화/호환성 ---------------------------------------------------------

class TestUnitCompat:
    def test_canon_aliases(self):
        assert canon_unit("톤") == "ton"
        assert canon_unit(" tCO2eq ") == "tco2eq"
        assert canon_unit("tCO2") == "tco2eq"
        assert canon_unit(None) == ""

    def test_same_unit_compatible(self):
        assert units_compatible("kWh", "kwh")
        assert units_compatible("톤", "ton")

    def test_different_units_incompatible(self):
        assert not units_compatible("%", "tCO2eq")
        assert not units_compatible("원", "kWh")
        assert not units_compatible("TJ", "%")

    def test_unknown_unit_is_permissive(self):
        """한쪽 단위 미상이면 보수적으로 비교 허용 (기존 동작 유지)."""
        assert units_compatible(None, "kWh")
        assert units_compatible("", "%")


# ---- 클레임 추출 오탐 필터 (SSOT) ------------------------------------------------

class TestClaimExtraction:
    def test_year_not_a_claim(self):
        claims = _extract_numbers("2025년 보고서에 따르면 배출량이 감소했다.")
        assert all(v != 2025 for v, _ in claims)

    def test_bare_integer_not_a_claim(self):
        """페이지·항목 번호류 맨 정수는 클레임이 아님."""
        assert _extract_numbers("제3장 5절에 명시되어 있다.") == []

    def test_unit_number_is_claim(self):
        claims = _extract_numbers("사용전력량은 128,400 kWh입니다.")
        assert (128400.0, "kWh") in claims

    def test_comma_number_without_unit_kept(self):
        """쉼표 있는 큰 수는 단위 없어도 수량 주장으로 유지."""
        claims = _extract_numbers("총 128,400을 사용했다.")
        assert (128400.0, None) in claims

    def test_scale_number_kept(self):
        claims = _extract_numbers("1,670만 tCO2eq를 배출했다.")
        assert any(v == pytest.approx(16_700_000) for v, _ in claims)


# ---- D1 단위 검증 (SSOT) ---------------------------------------------------------

class TestSsotD1Units:
    def test_same_value_wrong_unit_no_match(self):
        """128,400 '원' 주장은 128,400 kWh 노드와 일치하면 안 됨."""
        g = _graph(128400.0, "kWh")
        r = det.detect_d1_numeric("전기요금으로 128,400원을 납부했다.", "E-4-1", g)
        assert r.score >= 0.5, "단위 불일치 → 미일치 처리"

    def test_same_value_same_unit_matches(self):
        g = _graph(128400.0, "kWh")
        r = det.detect_d1_numeric("사용전력량은 128,400 kWh입니다.", "E-4-1", g)
        assert r.score < 0.5

    def test_year_only_sentence_safe(self):
        g = _graph(128400.0, "kWh")
        r = det.detect_d1_numeric("2025년에도 절감 노력을 지속했다.", "E-4-1", g)
        assert r.score == 0.0   # 연도는 클레임 아님 → 검증 대상 없음


# ---- D1 단위 검증 (v10 코어) -------------------------------------------------------

class TestCoreD1Units:
    def test_percent_claim_skips_absolute_node(self):
        """'온실가스 30 %' 주장을 tCO2eq 절대량 노드와 비교하지 않음 (목표치 오탐 차단)."""
        g = _graph(16_700_000, "tCO2eq", metric="E-3-1")
        r = _score_d1_numeric("온실가스 배출량을 30% 감축할 계획입니다.", g)
        assert r.score == 0.0
        assert "단위 불일치" in r.detail

    def test_same_unit_mismatch_still_detected(self):
        """단위가 같으면 기존 오차 검출은 그대로 동작해야 함 (회귀 가드)."""
        g = _graph(31.0, "%", metric="E-4-2")
        r = _score_d1_numeric("재생에너지 사용 비율은 45%입니다.", g)
        assert r.score > 0.5

    def test_ton_alias_compatible(self):
        """'톤' 주장 vs 'ton' 노드 — 별칭 정규화로 비교 가능."""
        # "배출"이 E-3-1로 먼저 매칭되므로 "발생량"으로 표현 (키워드맵 특성)
        g = _graph(2_190_000, "ton", metric="E-6-1")
        r = _score_d1_numeric("폐기물 발생량은 2,190,000톤입니다.", g)
        assert r.score == 0.0   # 값 일치 + 단위 호환
        assert "Δ=0.0%" in r.detail
