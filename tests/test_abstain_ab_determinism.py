"""단일 capture 기반 결정적 A/B 하네스 — 배치 4 하네스 결함 회귀 방지.

배치 4에서 발견된 문제: OFF/ON을 별도 capture()(=별개의 LLM 실행)로
비교하면, abstain과 무관한 judge 결과 변동이 A/B 지표를 오염시킨다.
이 파일은 그 결함이 재발하지 않음을 다음으로 증명한다:
  1) 룰 레이어 불변식 — abstain 플래그가 rule_score/judgeable을 바꾸지
     않는다(핵심 사실 3, 실측).
  2) with_abstain_ignored()로 유도한 OFF 해석이 ON과 pred/y/correct가
     100% 동일하다(같은 rows에서 유도되므로 구조적으로 보장).
  3) abstained 케이스만 coverage 분모에서 빠지고, 비-abstain 케이스는
     영향받지 않는다.
  4) Coverage/Overall 산식이 단일 캐시 기준으로 정확하다.
  5) 기본값(ABSTAIN_UNIT_MISMATCH=False)에서는 기권 0건 → OFF==ON.
"""
from __future__ import annotations

from esgenie.calibrate import _simulate_vector
from esgenie.evaluate import _case_rows, _prf, abstain_coverage, with_abstain_ignored
from esgenie.layer3_detect import detect_risk_vector
from esgenie.calibrate import _is_judgeable
import esgenie.layer3_detect as _layer3_detect

_CFG = {"trigger": 0.25, "rule_weight": 0.4, "threshold": 0.25, "axis_flag": 0.80}


class _NoNodeGraph:
    report_year = 2025
    edges: list = []

    def search_nodes(self, keywords, period=None):
        return []


# ============================================================================
# 1) 룰 레이어 불변식(핵심 사실 3) — abstain이 rule_score/judgeable을 바꾸지 않는다
# ============================================================================

def test_abstain_flag_does_not_change_rule_score_or_judgeable(monkeypatch):
    sentence = "재생에너지 사용 비율은 31.0%였다."

    monkeypatch.setattr(_layer3_detect, "ABSTAIN_ENABLED", False)
    rv_off = detect_risk_vector(sentence, evidence_graph=_NoNodeGraph())

    monkeypatch.setattr(_layer3_detect, "ABSTAIN_ENABLED", True)
    rv_on = detect_risk_vector(sentence, evidence_graph=_NoNodeGraph())

    # rule_score는 완전히 동일 — abstain은 표식만 추가한다.
    assert rv_off.D1_numeric.score == rv_on.D1_numeric.score == 0.0
    assert rv_off.D2_modifier == rv_on.D2_modifier
    assert rv_off.D3_semantic == rv_on.D3_semantic
    assert rv_off.D5_timeseries == rv_on.D5_timeseries

    # abstain 플래그 자체는 다르다(이게 유일한 차이).
    assert rv_off.D1_numeric.abstain is False
    assert rv_on.D1_numeric.abstain is True

    # judgeable(LLM 판정 대상 여부)도 abstain 래핑과 무관하게 동일해야 한다 —
    # 다르면 with_abstain_ignored로 유도한 OFF가 부정확해진다("정확성 주의").
    assert _is_judgeable(rv_off.D1_numeric) == _is_judgeable(rv_on.D1_numeric)


# ============================================================================
# 2)~4) with_abstain_ignored() / abstain_coverage() — 단일 캐시에서 두 해석
# ============================================================================

def _rec(case_id, *, label, d1_score=0.0, d1_abstain=False, d1_reason=None, d2_score=0.0):
    def _axis(score, abstain=False, reason=None):
        return {"rule_score": score, "detail": "", "judgeable": False,
                "abstain": abstain, "abstain_reason": reason}

    return {
        "id": case_id, "label": label, "category": "cat",
        "axes": {
            "D1_numeric": _axis(d1_score, d1_abstain, d1_reason),
            "D2_modifier": _axis(d2_score),
            "D3_semantic": _axis(0.5),
            "D5_timeseries": _axis(0.0),
        },
    }


def _mixed_records():
    return [
        _rec("A1", label="clean", d1_score=0.1, d2_score=0.1),                       # 비-abstain
        _rec("A2", label="greenwash", d1_score=0.0, d2_score=0.95),                  # 비-abstain, flagged(D2)
        _rec("A3", label="clean", d1_score=0.0, d1_abstain=True, d1_reason="no_evidence", d2_score=0.1),  # 기권 후보
        _rec("A4", label="greenwash", d1_score=0.0, d1_abstain=True, d1_reason="no_evidence", d2_score=0.95),  # D2가 flag → 기권 아님
    ]


def _rows_from_records(records):
    return _case_rows(records, _CFG)


def test_off_and_on_pred_identical_for_every_case():
    """핵심 회귀 방지점: with_abstain_ignored는 pred/y/correct를 절대 바꾸지 않는다."""
    on_rows = _rows_from_records(_mixed_records())
    off_rows = with_abstain_ignored(on_rows)

    assert [r["pred"] for r in off_rows] == [r["pred"] for r in on_rows]
    assert [r["y"] for r in off_rows] == [r["y"] for r in on_rows]
    assert [r["correct"] for r in off_rows] == [r["correct"] for r in on_rows]
    assert _prf(off_rows) == _prf(on_rows)


def test_only_abstained_and_unflagged_case_is_excluded_in_on():
    on_rows = {r["id"]: r for r in _rows_from_records(_mixed_records())}
    off_rows = {r["id"]: r for r in with_abstain_ignored(list(on_rows.values()))}

    # A3: abstain 축 있음 + D2도 안 flag → ON에서 기권.
    assert on_rows["A3"]["abstained"] is True
    # A4: abstain 축 있어도 D2가 flag → ON에서 기권 아님(설계대로).
    assert on_rows["A4"]["abstained"] is False
    # A1/A2: abstain 축 자체가 없음 → 애초에 기권 대상 아님.
    assert on_rows["A1"]["abstained"] is False
    assert on_rows["A2"]["abstained"] is False

    # OFF 해석에서는 전부 abstained=False(무시).
    assert all(not r["abstained"] for r in off_rows.values())


def test_coverage_and_overall_formula_from_single_cache():
    on_rows = _rows_from_records(_mixed_records())
    off_rows = with_abstain_ignored(on_rows)

    on_cov = abstain_coverage(on_rows)
    off_cov = abstain_coverage(off_rows)

    # 4건 중 1건(A3)만 기권 → ON coverage = 3/4.
    assert on_cov["coverage"] == 0.75
    assert on_cov["abstains"]["total"] == 1
    assert on_cov["abstains"]["by_reason"]["no_evidence"] == 1

    # OFF는 항상 coverage=1.0(전부 assessed).
    assert off_cov["coverage"] == 1.0
    assert off_cov["abstains"]["total"] == 0

    # Overall = Accuracy(assessed) * Coverage — 두 해석 모두 산식 그대로.
    assert on_cov["overall"] == round(on_cov["accuracy_on_assessed"] * on_cov["coverage"], 4)
    assert off_cov["overall"] == round(off_cov["accuracy_on_assessed"] * off_cov["coverage"], 4)


def test_no_abstain_records_give_off_equals_on():
    """기본값(ABSTAIN_UNIT_MISMATCH=False)에서는 기권 0건 → OFF==ON(회귀 없음)."""
    records = [
        _rec("B1", label="clean", d1_score=0.1, d2_score=0.1),
        _rec("B2", label="greenwash", d1_score=0.0, d2_score=0.95),
    ]
    on_rows = _rows_from_records(records)
    off_rows = with_abstain_ignored(on_rows)

    assert on_rows == off_rows  # 기권 케이스가 없으면 두 리스트가 완전히 같다.
    assert abstain_coverage(on_rows) == abstain_coverage(off_rows)


# ============================================================================
# 5) 캐시 왕복(calibrate._simulate_vector) 경유 — 실제 capture 스키마로 재확인
# ============================================================================

def test_with_abstain_ignored_after_simulate_vector_roundtrip():
    """judge_cache.json → _simulate_vector → _case_rows → with_abstain_ignored
    전체 배관이 abstain 표식만 무시하고 판정 자체는 바꾸지 않는지 확인."""
    rec = _rec("C1", label="clean", d1_score=0.0, d1_abstain=True, d1_reason="no_evidence", d2_score=0.1)
    rv = _simulate_vector(rec, trigger=_CFG["trigger"], rule_weight=_CFG["rule_weight"])
    assert rv.D1_numeric.abstain is True

    on_rows = _case_rows([rec], _CFG)
    off_rows = with_abstain_ignored(on_rows)

    assert on_rows[0]["abstained"] is True
    assert off_rows[0]["abstained"] is False
    assert on_rows[0]["pred"] == off_rows[0]["pred"]
