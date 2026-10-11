"""A·B 원값 대조 요약 — 노출 기준 부분집합과 분모 0 처리."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "ab_sum", Path(__file__).resolve().parents[1] / "scripts" / "eval_ab_compare_summary.py")
ab = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ab)


def row(qid, a, b, b_status, a_status="pending", a_reason="", b_reason="", av="", bv=""):
    return {"set": "s", "stage": "initial", "qid": qid, "A_decision": a, "B_decision": b,
            "A_hold_reason": a_reason, "B_hold_reason": b_reason, "A_value": av,
            "B_value_20261009": "", "B_value_20261010": bv,
            "B_status": b_status, "A_status": a_status}


def test_subsets_exclude_exposed_and_pending_rows():
    rows = [
        row("q1", "hold", "hold", "independent", a_reason="x", b_reason="x"),
        row("q2", "hold", "hold", "independent", a_reason="x", b_reason="y"),
        row("q3", "hold", "hold", "independent", a_status="excluded", a_reason="x", b_reason="x"),
        row("q4", "hold", "hold", "excluded", a_reason="x", b_reason="x"),
        row("q5", "hold", "hold", "pending", a_reason="x", b_reason="x"),
    ]
    s = ab.summarize_rows(rows)
    assert s["raw"]["rows"] == 5
    assert s["b_independent"]["rows"] == 3
    assert s["b_independent"]["hold_reason_match"]["text"] == "2/3"
    assert s["b_independent_a_not_reported"]["rows"] == 2
    assert s["b_independent_a_not_reported"]["hold_reason_match"]["text"] == "1/2"
    assert s["b_status_counts"] == {"independent": 3, "excluded": 1, "pending": 1}


def test_zero_denominator_is_not_reported_as_zero_percent():
    s = ab.summarize_rows([row("q1", "hold", "hold", "pending", a_reason="x", b_reason="x")])
    frac = s["b_independent"]["decision_match"]
    assert frac["d"] == 0 and "산출 불가" in frac["text"]
    assert "0%" not in frac["text"]


def test_value_match_is_string_equal_without_unit_conversion():
    rows = [row("q1", "answer", "answer", "independent", av="18.4", bv="18,400")]
    s = ab.summarize_rows(rows)
    assert s["b_independent"]["value_match_b_20261010"]["text"] == "0/1"
