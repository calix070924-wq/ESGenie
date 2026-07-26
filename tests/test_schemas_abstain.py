"""AxisScore/RiskVector 기권(abstain) 필드 — 스키마 추가 배치(동작 불변) 검증.

이번 배치는 필드 추가만 한다: 아무 곳에서도 abstain=True를 세팅하지 않으므로
기존 탐지 결과(score/level/top_axis 등)는 전혀 바뀌지 않아야 한다.
"""
from __future__ import annotations

from esgenie.layer3_detect import detect_risk_vector
from esgenie.schemas import AxisScore, RiskVector


def test_axis_score_default_abstain_false() -> None:
    ax = AxisScore(score=0.3)
    assert ax.abstain is False
    assert ax.abstain_reason is None


def test_axis_score_to_dict_includes_new_fields() -> None:
    ax = AxisScore(score=0.9, evidence=["n1"], detail="근거 없음",
                   abstain=True, abstain_reason="no_evidence")
    d = ax.to_dict()
    assert d["abstain"] is True
    assert d["abstain_reason"] == "no_evidence"

    # 기본값 케이스도 직렬화에 포함되는지 확인(하위 호환 — 필드 자체는 항상 존재)
    ax2 = AxisScore(score=0.1)
    d2 = ax2.to_dict()
    assert d2["abstain"] is False
    assert d2["abstain_reason"] is None


def test_high_axes_excludes_abstained_axis() -> None:
    high_but_abstained = AxisScore(score=0.95, abstain=True, abstain_reason="no_evidence")
    low = AxisScore(score=0.05)
    rv = RiskVector(
        D1_numeric=high_but_abstained,
        D2_modifier=low,
        D3_semantic=low,
        D5_timeseries=low,
        aggregate={"risk_score": 0.4, "level": "medium", "top_axis": "D1_numeric"},
    )
    # score만 보면 D1_numeric이 high 축이지만, abstain=True이므로 제외되어야 한다.
    assert "D1_numeric" not in rv.high_axes()


def test_abstained_axes_lists_flagged_axis() -> None:
    high_but_abstained = AxisScore(score=0.95, abstain=True, abstain_reason="no_evidence")
    low = AxisScore(score=0.05)
    rv = RiskVector(
        D1_numeric=high_but_abstained,
        D2_modifier=low,
        D3_semantic=low,
        D5_timeseries=low,
        aggregate={},
    )
    assert rv.abstained_axes() == ["D1_numeric"]


def test_no_axis_abstains_by_default_in_production_path() -> None:
    """기존 탐지 경로는 어디서도 abstain을 세팅하지 않는다 — 동작 불변 확인."""
    rv = detect_risk_vector("온실가스 배출량은 1,670만 tCO2eq으로 전년 대비 2.1% 감소하였다.")
    assert rv.abstained_axes() == []
    assert rv.aggregate.get("abstained_axes") == []
