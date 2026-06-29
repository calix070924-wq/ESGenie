"""Tests for grounding gate G4 (unit mismatch), G5 (overclaim), G2 regression, and units.py utilities."""
from __future__ import annotations

import pytest

from esgenie.rag_gates.grounding_gate import evaluate_grounding, grounding_feedback
from esgenie.rag_gates.units import (
    convert_to_common,
    extract_number_unit_pairs,
    normalize_unit,
    numeric_equal,
    parse_number,
    units_compatible,
)


# ═══════════════════════════════════════════════════════════════════════════════
# units.py unit tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestParseNumber:
    def test_plain_integer(self):
        assert parse_number("12345") == 12345.0

    def test_comma_separated(self):
        assert parse_number("12,345") == 12345.0

    def test_decimal(self):
        assert parse_number("3.14") == 3.14

    def test_scientific_notation(self):
        assert parse_number("4.78e-1") == pytest.approx(0.478)

    def test_scientific_notation_positive(self):
        assert parse_number("1.5E+3") == 1500.0

    def test_korean_man(self):
        assert parse_number("1.5만") == 15000.0

    def test_korean_eok(self):
        assert parse_number("3억") == 300_000_000.0

    def test_invalid_returns_none(self):
        assert parse_number("abc") is None
        assert parse_number("") is None


class TestNormalizeUnit:
    def test_korean_ton(self):
        assert normalize_unit("톤") == "t"

    def test_english_ton(self):
        assert normalize_unit("ton") == "t"
        assert normalize_unit("t") == "t"

    def test_tco2eq_variants(self):
        assert normalize_unit("tCO2eq") == "tCO2eq"
        assert normalize_unit("톤CO2eq") == "tCO2eq"
        assert normalize_unit("tCO2") == "tCO2eq"

    def test_kwh(self):
        assert normalize_unit("kWh") == "kWh"
        assert normalize_unit("킬로와트시") == "kWh"

    def test_mwh(self):
        assert normalize_unit("MWh") == "MWh"

    def test_percent(self):
        assert normalize_unit("%") == "%"
        assert normalize_unit("퍼센트") == "%"

    def test_unknown_returns_none(self):
        assert normalize_unit("갤런") is None


class TestNumericEqual:
    def test_exact_equal(self):
        assert numeric_equal(100.0, 100.0) is True

    def test_within_tolerance(self):
        assert numeric_equal(100.0, 100.5, rel_tol=0.01) is True

    def test_outside_tolerance(self):
        assert numeric_equal(100.0, 102.0, rel_tol=0.01) is False


class TestUnitsCompatible:
    def test_same_unit(self):
        assert units_compatible("t", "t") is True

    def test_kwh_mwh(self):
        assert units_compatible("kWh", "MWh") is True

    def test_incompatible(self):
        assert units_compatible("t", "kWh") is False

    def test_percent_vs_ton(self):
        assert units_compatible("%", "t") is False


class TestConvertToCommon:
    def test_kwh_to_mwh(self):
        assert convert_to_common(1000.0, "kWh", "MWh") == pytest.approx(1.0)

    def test_mwh_to_kwh(self):
        assert convert_to_common(1.0, "MWh", "kWh") == pytest.approx(1000.0)

    def test_incompatible_returns_none(self):
        assert convert_to_common(100.0, "t", "kWh") is None


class TestExtractNumberUnitPairs:
    def test_basic_extraction(self):
        pairs = extract_number_unit_pairs("온실가스 배출량 12,345 tCO2eq")
        assert len(pairs) == 1
        assert pairs[0] == (12345.0, "tCO2eq")

    def test_multiple_pairs(self):
        pairs = extract_number_unit_pairs("전력 1000 kWh 배출 50 톤")
        assert len(pairs) == 2

    def test_korean_unit(self):
        pairs = extract_number_unit_pairs("배출량 120톤")
        assert pairs[0] == (120.0, "t")


# ═══════════════════════════════════════════════════════════════════════════════
# G4: Unit mismatch detection
# ═══════════════════════════════════════════════════════════════════════════════


class TestG4UnitMismatch:
    def test_unit_mismatch_escalates(self):
        """Same numeric value but incompatible units -> ESCALATE with G4."""
        answer = "온실가스 배출량은 120 t입니다 [chunk_1]"
        chunks = [{"id": "chunk_1", "text": "에너지 사용량 120 kWh"}]

        result = evaluate_grounding(answer, chunks)

        assert result.decision == "ESCALATE"
        assert len(result.g4_unit_mismatches) > 0
        assert "G4_unit_mismatch" in result.hard_fails

    def test_compatible_units_accept(self):
        """Same value with compatible units (kWh vs MWh with proper conversion) -> no G4."""
        answer = "전력 사용량은 1000 kWh입니다 [chunk_1]"
        chunks = [{"id": "chunk_1", "text": "전력 사용량 1000 kWh"}]

        result = evaluate_grounding(answer, chunks)

        assert result.decision == "ACCEPT"
        assert result.g4_unit_mismatches == []

    def test_same_unit_same_value_accept(self):
        """Exact same number and unit -> ACCEPT."""
        answer = "배출량은 500 tCO2eq입니다 [chunk_1]"
        chunks = [{"id": "chunk_1", "text": "배출량 500 tCO2eq"}]

        result = evaluate_grounding(answer, chunks)

        assert result.decision == "ACCEPT"
        assert result.g4_unit_mismatches == []

    def test_conversion_mwh_to_kwh_accept(self):
        """1 MWh in answer vs 1000 kWh in chunk -> ACCEPT (same group, conversion match)."""
        answer = "사용량은 1 MWh입니다 [c1]"
        chunks = [{"id": "c1", "text": "전력 사용량 1000 kWh"}]

        result = evaluate_grounding(answer, chunks)

        assert result.decision == "ACCEPT"
        assert result.g2_orphan_numbers == []
        assert result.g4_unit_mismatches == []

    def test_conversion_gwh_to_mwh_accept(self):
        """2.5 GWh in answer vs 2500 MWh in chunk -> ACCEPT."""
        answer = "전력생산 2.5 GWh 달성 [c1]"
        chunks = [{"id": "c1", "text": "전력생산 2500 MWh"}]

        result = evaluate_grounding(answer, chunks)

        assert result.decision == "ACCEPT"
        assert result.g2_orphan_numbers == []
        assert result.g4_unit_mismatches == []

    def test_same_group_value_mismatch_is_orphan(self):
        """Same unit group but values don't match after conversion -> G2 orphan."""
        answer = "사용량은 1 MWh입니다 [c1]"
        chunks = [{"id": "c1", "text": "전력 사용량 500 kWh"}]

        result = evaluate_grounding(answer, chunks)

        assert result.decision == "ESCALATE"
        assert "G2_orphan_numbers" in result.hard_fails
        assert result.g4_unit_mismatches == []

    def test_incompatible_units_still_g4(self):
        """Incompatible units with same numeric value -> still G4 (regression guard)."""
        answer = "배출량은 120 t입니다 [chunk_1]"
        chunks = [{"id": "chunk_1", "text": "에너지 사용량 120 kWh"}]

        result = evaluate_grounding(answer, chunks)

        assert result.decision == "ESCALATE"
        assert len(result.g4_unit_mismatches) > 0
        assert "G4_unit_mismatch" in result.hard_fails

    def test_order_independence_correct_pair_after_noise(self):
        """Chunk has noise pair (100 명) before correct pair (100 kWh) — must ACCEPT."""
        answer = "전력 사용량은 100 kWh 입니다 [c1]"
        chunks = [{"id": "c1", "text": "직원 100명이며 전력 사용량은 100 kWh 이다"}]

        result = evaluate_grounding(answer, chunks)

        assert result.decision == "ACCEPT"
        assert result.g4_unit_mismatches == []

    def test_order_independence_correct_pair_before_noise(self):
        """Chunk has correct pair (100 kWh) before noise pair (100 명) — must ACCEPT."""
        answer = "전력 사용량은 100 kWh 입니다 [c1]"
        chunks = [{"id": "c1", "text": "전력 사용량은 100 kWh 이고 직원은 100명 이다"}]

        result = evaluate_grounding(answer, chunks)

        assert result.decision == "ACCEPT"
        assert result.g4_unit_mismatches == []


# ═══════════════════════════════════════════════════════════════════════════════
# G5: Overclaim detection
# ═══════════════════════════════════════════════════════════════════════════════


class TestG5Overclaim:
    def test_ungrounded_overclaim_flagged(self):
        """Overclaim expression NOT in chunk -> g5_overclaim=True, soft_flags."""
        answer = "당사는 업계 유일 탄소중립 기업입니다 [chunk_1]"
        chunks = [{"id": "chunk_1", "text": "당사 온실가스 배출량 500 tCO2eq 감축"}]

        result = evaluate_grounding(answer, chunks)

        assert result.g5_overclaim is True
        assert "G5_overclaim" in result.soft_flags
        # G5 alone does NOT cause ESCALATE (it's soft)
        assert "G5_overclaim" not in result.hard_fails

    def test_grounded_overclaim_passes(self):
        """Overclaim expression EXISTS in chunk -> g5_overclaim=False (grounded)."""
        answer = "당사는 업계 유일 탄소중립 인증 기업입니다 [chunk_1]"
        chunks = [{"id": "chunk_1", "text": "당사는 업계 유일 탄소중립 인증을 획득하였습니다"}]

        result = evaluate_grounding(answer, chunks)

        assert result.g5_overclaim is False
        assert "G5_overclaim" not in result.soft_flags

    def test_g5_is_soft_flag_not_hard_fail(self):
        """G5 alone should NOT cause ESCALATE — it's only a soft flag."""
        answer = "당사는 세계 최고 수준의 기술을 보유합니다 [chunk_1]"
        chunks = [{"id": "chunk_1", "text": "당사 기술 개발 현황 보고"}]

        result = evaluate_grounding(answer, chunks)

        assert result.g5_overclaim is True
        assert result.decision == "ACCEPT"  # soft flag only

    def test_100_percent_quantitative_fact_no_g5(self):
        """'재생에너지 100% 달성' is a quantitative fact, not overclaim."""
        answer = "재생에너지 100% 달성 [chunk_1]"
        chunks = [{"id": "chunk_1", "text": "재생에너지 비율 85%"}]

        result = evaluate_grounding(answer, chunks)

        assert result.g5_overclaim is False

    def test_100_percent_with_absolute_modifier_g5(self):
        """'업계 유일 100% 친환경' combines absolute modifier -> triggers G5."""
        answer = "업계 유일 100% 친환경 제품입니다 [chunk_1]"
        chunks = [{"id": "chunk_1", "text": "친환경 인증 제품 생산"}]

        result = evaluate_grounding(answer, chunks)

        assert result.g5_overclaim is True


# ═══════════════════════════════════════════════════════════════════════════════
# G2: Regression tests for normalized number comparison
# ═══════════════════════════════════════════════════════════════════════════════


class TestG2Regression:
    def test_comma_vs_no_comma_same_unit(self):
        """'12,345 t' in answer vs '12345톤' in chunk -> normalized equal, not orphan."""
        answer = "온실가스 배출량은 12,345 t입니다 [chunk_1]"
        chunks = [{"id": "chunk_1", "text": "온실가스 배출량 12345톤"}]

        result = evaluate_grounding(answer, chunks)

        assert "12345" not in result.g2_orphan_numbers
        assert result.decision == "ACCEPT"

    def test_scientific_notation_equivalence(self):
        """'4.78e-1' should match '0.478' via normalization."""
        answer = "농도는 4.78e-1 입니다 [chunk_1]"
        chunks = [{"id": "chunk_1", "text": "농도 0.478 측정"}]

        result = evaluate_grounding(answer, chunks)

        assert result.g2_orphan_numbers == []

    def test_existing_accept_still_accepts(self):
        """Original ACCEPT case must remain ACCEPT after refactoring."""
        answer = (
            "온실가스 배출량은 120입니다 [corp_1]\n"
            "재생에너지 비율은 31입니다 [corp_2]"
        )
        chunks = [
            {"id": "corp_1", "text": "온실가스 배출량 120"},
            {"id": "corp_2", "text": "재생에너지 비율 31"},
        ]

        result = evaluate_grounding(answer, chunks)

        assert result.decision == "ACCEPT"
        assert result.g2_orphan_numbers == []
        assert result.faithfulness == 1.0

    def test_orphan_number_still_detected(self):
        """Number present in answer but not in any chunk -> still orphan."""
        answer = "수익률은 99입니다 [chunk_1]"
        chunks = [{"id": "chunk_1", "text": "수익률 관련 보고서"}]

        result = evaluate_grounding(answer, chunks)

        assert "99" in result.g2_orphan_numbers


# ═══════════════════════════════════════════════════════════════════════════════
# Feedback function tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestGroundingFeedback:
    def test_g4_feedback_includes_unit_instruction(self):
        answer = "배출량은 120 t입니다 [chunk_1]"
        chunks = [{"id": "chunk_1", "text": "에너지 120 kWh"}]

        result = evaluate_grounding(answer, chunks)
        feedback = grounding_feedback(result)

        assert "단위 불일치" in feedback or "단위를 통일" in feedback

    def test_g5_feedback_includes_overclaim_instruction(self):
        answer = "당사는 업계 유일 기업입니다 [chunk_1]"
        chunks = [{"id": "chunk_1", "text": "당사 사업 현황"}]

        result = evaluate_grounding(answer, chunks)
        feedback = grounding_feedback(result)

        assert "절대화" in feedback or "강조 표현" in feedback

    def test_accept_no_feedback(self):
        answer = "배출량은 120입니다 [chunk_1]"
        chunks = [{"id": "chunk_1", "text": "배출량 120"}]

        result = evaluate_grounding(answer, chunks)
        feedback = grounding_feedback(result)

        assert feedback == ""
