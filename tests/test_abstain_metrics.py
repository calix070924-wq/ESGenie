"""기권(abstain) 기반 Coverage/Accuracy(assessed)/Overall 지표 — 산식·배관 검증.

측정 전용 배치(Step 3): 게이트/HITL은 건드리지 않는다. 여기서는
1) evaluate.abstain_coverage()의 산식,
2) evaluate._case_rows()가 RiskVector의 abstain 축을 올바르게 반영하는지,
3) calibrate.capture()/_simulate_vector()가 abstain/abstain_reason을
   캐시 왕복(직렬화→역직렬화)에서 잃어버리지 않는지,
4) benchmark.DetectorReport.metrics()의 coverage 관련 필드,
를 검증한다.
"""
from __future__ import annotations

import pytest

from esgenie.benchmark import CaseResult, DetectorReport
from esgenie.calibrate import _simulate_vector
from esgenie.evaluate import _case_rows, abstain_coverage
from esgenie.schemas import AxisScore, RiskVector


# ============================================================================
# 1) evaluate.abstain_coverage() — 순수 산식 검증
# ============================================================================

def _row(correct: bool, abstained: bool, reasons=None) -> dict:
    return {"id": "x", "category": "c", "p": 0.0, "y": 0, "pred": 0,
            "correct": int(correct), "abstained": abstained,
            "abstain_reasons": reasons or []}


def test_zero_abstains_gives_full_coverage():
    rows = [_row(True, False), _row(False, False), _row(True, False), _row(True, False)]
    out = abstain_coverage(rows)
    assert out["coverage"] == 1.0
    assert out["accuracy_on_assessed"] == 0.75  # 3/4 correct
    assert out["overall"] == 0.75
    assert out["abstains"]["total"] == 0


def test_k_abstains_formula():
    # 5건 중 2건 기권(reason=no_evidence 1건, unit_mismatch 1건) → assessed 3건, 그중 2건 정답
    rows = [
        _row(True, False),
        _row(True, False),
        _row(False, False),
        _row(True, True, ["no_evidence"]),
        _row(False, True, ["unit_mismatch"]),
    ]
    out = abstain_coverage(rows)
    assert out["n"] == 5
    assert out["coverage"] == pytest.approx(3 / 5)
    assert out["accuracy_on_assessed"] == pytest.approx(2 / 3, abs=1e-3)
    assert out["overall"] == pytest.approx((3 / 5) * (2 / 3), abs=1e-3)
    assert out["abstains"]["total"] == 2
    assert out["abstains"]["by_reason"] == {"no_evidence": 1, "unit_mismatch": 1, "low_confidence": 0}


def test_empty_rows_do_not_crash():
    out = abstain_coverage([])
    assert out["coverage"] == 0.0
    assert out["accuracy_on_assessed"] == 0.0
    assert out["overall"] == 0.0
    assert out["abstains"]["total"] == 0


# ============================================================================
# 2) evaluate._case_rows() — abstain 축 → 케이스 단위 기권 판정
# ============================================================================

_CFG = {"trigger": 0.25, "rule_weight": 0.4, "threshold": 0.25, "axis_flag": 0.80}


def _rec(d1_score=0.0, d1_abstain=False, d1_reason=None, d2_score=0.0, label="clean"):
    """calibrate.capture()가 만드는 judge_cache.json 레코드 스키마를 흉내낸다."""
    def _axis(score, abstain=False, reason=None):
        return {"rule_score": score, "detail": "", "judgeable": False,
                "abstain": abstain, "abstain_reason": reason}

    return {
        "id": "R1", "label": label, "category": "cat",
        "axes": {
            "D1_numeric": _axis(d1_score, d1_abstain, d1_reason),
            "D2_modifier": _axis(d2_score),
            "D3_semantic": _axis(0.5),
            "D5_timeseries": _axis(0.0),
        },
    }


def test_case_rows_marks_abstained_when_no_other_axis_flags():
    # D1이 기권(no_evidence)이고, D2도 낮아 flagged 안 됨 → 기권 케이스로 카운트.
    rec = _rec(d1_score=0.0, d1_abstain=True, d1_reason="no_evidence", d2_score=0.1, label="clean")
    rows = _case_rows([rec], _CFG)
    assert rows[0]["abstained"] is True
    assert rows[0]["abstain_reasons"] == ["no_evidence"]
    assert rows[0]["pred"] == 0


def test_case_rows_not_abstained_when_another_axis_flags_risk():
    # D1은 기권이지만 D2가 강하게 위험을 잡으면(>=axis_flag) 기권으로 세지 않는다.
    rec = _rec(d1_score=0.0, d1_abstain=True, d1_reason="no_evidence", d2_score=0.95, label="greenwash")
    rows = _case_rows([rec], _CFG)
    assert rows[0]["pred"] == 1
    assert rows[0]["abstained"] is False


def test_case_rows_no_abstain_axis_never_marked_abstained():
    rec = _rec(d1_score=0.1, d1_abstain=False, d2_score=0.1, label="clean")
    rows = _case_rows([rec], _CFG)
    assert rows[0]["abstained"] is False
    assert rows[0]["abstain_reasons"] == []


# ============================================================================
# 3) calibrate._simulate_vector() — 캐시 왕복에서 abstain 보존
# ============================================================================

def test_simulate_vector_roundtrips_abstain_fields():
    rec = _rec(d1_score=0.0, d1_abstain=True, d1_reason="unit_mismatch")
    rv = _simulate_vector(rec, trigger=_CFG["trigger"], rule_weight=_CFG["rule_weight"])
    assert rv.D1_numeric.abstain is True
    assert rv.D1_numeric.abstain_reason == "unit_mismatch"
    assert rv.abstained_axes() == ["D1_numeric"]


def test_simulate_vector_backward_compatible_without_abstain_keys():
    """구버전 judge_cache.json(abstain 키 없음)도 깨지지 않아야 한다."""
    rec = {
        "id": "OLD", "label": "clean", "category": "cat",
        "axes": {
            "D1_numeric": {"rule_score": 0.1, "detail": "", "judgeable": False},
            "D2_modifier": {"rule_score": 0.1, "detail": "", "judgeable": False},
            "D3_semantic": {"rule_score": 0.5, "detail": "", "judgeable": False},
            "D5_timeseries": {"rule_score": 0.0, "detail": "", "judgeable": False},
        },
    }
    rv = _simulate_vector(rec, trigger=_CFG["trigger"], rule_weight=_CFG["rule_weight"])
    assert rv.abstained_axes() == []


# ============================================================================
# 4) benchmark.DetectorReport.metrics() — coverage 관련 필드
# ============================================================================

def test_detector_report_metrics_zero_abstain_matches_legacy_accuracy():
    report = DetectorReport(name="rule")
    report.cases = [
        CaseResult("c1", "cat", "greenwash", True, 0.9),
        CaseResult("c2", "cat", "clean", False, 0.1),
        CaseResult("c3", "cat", "greenwash", False, 0.1),  # miss
    ]
    m = report.metrics()
    assert m["coverage"] == 1.0
    assert m["accuracy_on_assessed"] == m["accuracy"]
    assert m["overall"] == m["accuracy"]
    assert m["abstains"]["total"] == 0


def test_detector_report_metrics_with_abstained_case():
    report = DetectorReport(name="rule")
    report.cases = [
        CaseResult("c1", "cat", "greenwash", True, 0.9),
        CaseResult("c2", "cat", "clean", False, 0.1),
        CaseResult("c3", "cat", "greenwash", False, 0.0,
                   abstained=True, abstain_reasons=["no_evidence"]),
    ]
    m = report.metrics()
    assert m["coverage"] == pytest.approx(2 / 3, abs=1e-3)
    assert m["accuracy_on_assessed"] == 1.0  # 남은 2건(c1,c2) 모두 정답
    assert m["overall"] == pytest.approx((2 / 3) * 1.0, abs=1e-3)
    assert m["abstains"] == {"total": 1, "by_reason": {"no_evidence": 1, "unit_mismatch": 0, "low_confidence": 0}}


# ============================================================================
# 5) RiskVector.abstained_axes() 직접 확인(회귀 가드 — 이전 배치 기능)
# ============================================================================

def test_risk_vector_abstained_axes_used_by_metrics():
    rv = RiskVector(
        D1_numeric=AxisScore(score=0.0, abstain=True, abstain_reason="no_evidence"),
        D2_modifier=AxisScore(score=0.1),
        D3_semantic=AxisScore(score=0.5),
        D5_timeseries=AxisScore(score=0.0),
        aggregate={},
    )
    assert rv.abstained_axes() == ["D1_numeric"]
    assert "D1_numeric" not in rv.high_axes()
