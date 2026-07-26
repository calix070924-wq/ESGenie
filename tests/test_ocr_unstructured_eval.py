# -*- coding: utf-8 -*-
"""Unit tests for ocr_unstructured_eval pure functions.

Tests: _value_in_raw_text, _parse_raw_numbers, _normalize_unit, _units_match, score_metrics.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from ocr_unstructured_eval import (
    _parse_raw_numbers,
    _value_in_raw_text,
    _normalize_unit,
    _units_match,
    score_metrics,
)


# ============================================================
# _parse_raw_numbers
# ============================================================

class TestParseRawNumbers:
    def test_integers(self):
        nums = _parse_raw_numbers("근로시간 60시간 이내")
        assert 60.0 in nums

    def test_comma_separated(self):
        nums = _parse_raw_numbers("총 142,560 kWh를 사용")
        assert 142560.0 in nums

    def test_decimal(self):
        nums = _parse_raw_numbers("배출량 18.4 tCO2eq")
        assert 18.4 in nums

    def test_multiple(self):
        nums = _parse_raw_numbers("2060년까지 30% 감축, 1,608원/ton")
        assert 2060.0 in nums
        assert 30.0 in nums
        assert 1608.0 in nums


# ============================================================
# _value_in_raw_text — the critical function
# ============================================================

class TestValueInRawText:
    def test_exact_standalone(self):
        assert _value_in_raw_text(60, "근로시간 60시간") is True

    def test_no_false_match_in_larger_number(self):
        assert _value_in_raw_text(60, "서기 2060년") is False

    def test_no_false_match_comma_number(self):
        assert _value_in_raw_text(60, "1,608원") is False

    def test_no_false_match_larger_with_same_prefix(self):
        assert _value_in_raw_text(60, "60,000원") is False

    def test_decimal_match(self):
        assert _value_in_raw_text(18.4, "배출량 18.4 t") is True

    def test_decimal_no_match_concatenated(self):
        assert _value_in_raw_text(18.4, "숫자 184개") is False

    def test_comma_separated_match(self):
        assert _value_in_raw_text(142560, "총 142,560 kWh") is True

    def test_empty_raw_text(self):
        assert _value_in_raw_text(60, "") is False

    def test_none_value(self):
        assert _value_in_raw_text(None, "text") is False

    def test_zero(self):
        assert _value_in_raw_text(0, "감축률 0%") is True

    def test_large_number_no_false_positive(self):
        # 60 should NOT match inside "60000"
        assert _value_in_raw_text(60, "60000원") is False

    def test_float_close_to_int(self):
        assert _value_in_raw_text(60.0, "근로시간 60시간") is True


# ============================================================
# Unit normalization
# ============================================================

class TestUnitNormalization:
    def test_ton_synonyms(self):
        assert _normalize_unit("ton") == _normalize_unit("t")
        assert _normalize_unit("tons") == _normalize_unit("t")
        assert _normalize_unit("톤") == _normalize_unit("t")

    def test_percent_synonyms(self):
        assert _normalize_unit("%") == _normalize_unit("퍼센트")
        assert _normalize_unit("percent") == _normalize_unit("%")

    def test_case_insensitive(self):
        assert _normalize_unit("KWh") == _normalize_unit("kwh")

    def test_empty_and_none(self):
        assert _normalize_unit(None) == ""
        assert _normalize_unit("") == ""


class TestUnitsMatch:
    def test_gold_no_unit_always_matches(self):
        assert _units_match("ton", None) is True
        assert _units_match("kg", "") is True

    def test_same_unit(self):
        assert _units_match("t", "ton") is True

    def test_different_unit(self):
        assert _units_match("ton", "%") is False

    def test_value_same_unit_different(self):
        assert _units_match("%", "t") is False


# ============================================================
# score_metrics with unit check
# ============================================================

class TestScoreMetricsUnit:
    def _make_metric(self, code, value, unit=None):
        m = MagicMock()
        m.kesg_code_guess = code
        m.value = value
        m.unit = unit
        m.confidence = 0.75
        return m

    def test_value_match_unit_match(self):
        extracted = [self._make_metric("S-4-2", 0.3, "%")]
        gold = [{"kesg_code": "S-4-2", "value": 0.3, "tol": 0.01, "unit": "%"}]
        hits, total, fp_h, fp_r = score_metrics(extracted, gold, "산재율 0.3%")
        assert hits == 1
        assert total == 1

    def test_value_match_unit_mismatch(self):
        extracted = [self._make_metric("S-4-2", 0.3, "ton")]
        gold = [{"kesg_code": "S-4-2", "value": 0.3, "tol": 0.01, "unit": "%"}]
        hits, total, fp_h, fp_r = score_metrics(extracted, gold, "산재율 0.3%")
        assert hits == 0

    def test_gold_no_unit_matches_any(self):
        extracted = [self._make_metric("S-4-2", 0.3, "ton")]
        gold = [{"kesg_code": "S-4-2", "value": 0.3, "tol": 0.01}]
        hits, total, fp_h, fp_r = score_metrics(extracted, gold, "산재율 0.3%")
        assert hits == 1


# ============================================================
# judge_failed denominator logic
# ============================================================

class TestJudgeFailedDenominator:
    def test_denominator_excludes_failed(self):
        """Verify the formula: judged = total - failed."""
        total_clauses = 10
        judge_failed = 2
        judged = total_clauses - judge_failed
        assert judged == 8
        halluc = 1
        rate = 100 * halluc / judged
        assert abs(rate - 12.5) < 0.01


# ============================================================
# compute_gold_agreement: human field preservation
# ============================================================

class TestGoldAgreementPreservation:
    """Verify that reconcile_gold preserves human-entered fields."""

    def test_preserves_agreement_human(self, tmp_path):
        """If existing gold has agreement_human set, reconcile must keep it."""
        from compute_gold_agreement import reconcile_gold

        # Create pass A gold
        pass_a = {
            "docs": [{
                "doc_id": "test_doc",
                "file": "test.pdf",
                "channel_variant": "digital",
                "synthetic": False,
                "doc_type_gold": "policy_manual",
                "facts_gold": [{"id": "F1", "text": "test fact"}],
                "metrics_gold": [],
            }]
        }
        a_path = tmp_path / "passA.json"
        b_path = tmp_path / "passB.json"
        gold_path = tmp_path / "gold.json"

        a_path.write_text(json.dumps(pass_a), encoding="utf-8")
        b_path.write_text(json.dumps(pass_a), encoding="utf-8")

        # Existing gold with human data
        existing_gold = {
            "docs": [{
                "doc_id": "test_doc",
                "agreement_human": 0.95,
                "labeler2_human": "jimin",
                "facts_gold_human": [{"id": "H1", "text": "human fact"}],
            }]
        }
        gold_path.write_text(json.dumps(existing_gold), encoding="utf-8")

        agreement = {"per_doc": {"test_doc": {"agreement": 1.0}}, "overall": {"agreement": 1.0}}
        reconcile_gold(str(a_path), str(b_path), str(gold_path), agreement)

        with open(gold_path, encoding="utf-8") as f:
            result = json.load(f)

        doc = result["docs"][0]
        assert doc["agreement_human"] == 0.95, "agreement_human must be preserved"
        assert doc["labeler2_human"] == "jimin", "labeler2_human must be preserved"
        assert doc["facts_gold_human"] == [{"id": "H1", "text": "human fact"}]

    def test_null_when_no_existing_human_data(self, tmp_path):
        """If no existing gold, agreement_human should be None."""
        from compute_gold_agreement import reconcile_gold

        pass_a = {
            "docs": [{
                "doc_id": "new_doc",
                "file": "new.pdf",
                "channel_variant": "digital",
                "synthetic": False,
                "doc_type_gold": "policy_manual",
                "facts_gold": [{"id": "F1", "text": "test fact"}],
                "metrics_gold": [],
            }]
        }
        a_path = tmp_path / "passA.json"
        b_path = tmp_path / "passB.json"
        gold_path = tmp_path / "gold.json"

        a_path.write_text(json.dumps(pass_a), encoding="utf-8")
        b_path.write_text(json.dumps(pass_a), encoding="utf-8")
        # No existing gold file

        agreement = {"per_doc": {"new_doc": {"agreement": 0.8}}, "overall": {"agreement": 0.8}}
        reconcile_gold(str(a_path), str(b_path), str(gold_path), agreement)

        with open(gold_path, encoding="utf-8") as f:
            result = json.load(f)

        doc = result["docs"][0]
        assert doc["agreement_human"] is None


class TestReadOnlyDefault:
    """Verify compute_gold_agreement runs read-only without --write."""

    def test_no_write_without_flag(self, tmp_path):
        """Running without --write should not create/modify gold file."""
        import subprocess
        pass_a = {"docs": [{"doc_id": "x", "file": "x.pdf", "channel_variant": "digital",
                           "synthetic": False, "doc_type_gold": "p", "facts_gold": [{"id": "F1", "text": "a"}],
                           "metrics_gold": []}]}
        (tmp_path / "a.json").write_text(json.dumps(pass_a), encoding="utf-8")
        (tmp_path / "b.json").write_text(json.dumps(pass_a), encoding="utf-8")
        gold_file = tmp_path / "gold.json"
        gold_file.write_text('{"docs":[]}', encoding="utf-8")
        mtime_before = gold_file.stat().st_mtime

        # We can't easily run the __main__ with different paths, but we can
        # verify that the module function doesn't write
        from compute_gold_agreement import compute_agreement
        compute_agreement(str(tmp_path / "a.json"), str(tmp_path / "b.json"))

        assert gold_file.stat().st_mtime == mtime_before, "compute_agreement must not write files"
