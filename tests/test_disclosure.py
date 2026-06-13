"""D6 선택적 공시(Selective Disclosure) 탐지 단위 테스트."""
from __future__ import annotations

from types import SimpleNamespace

from esgenie.layer3_disclosure import (
    detect_selective_disclosure,
    OMISSION_SENSITIVITY,
    RATIO_CONTEXT_PAIRS,
)


def _extraction(disclosed: list[str], missing: list[str]):
    """detect_selective_disclosure가 쓰는 최소 필드만 가진 스텁."""
    return SimpleNamespace(
        mapped={c: {"code": c} for c in disclosed},
        missing=list(missing),
    )


def test_clean_no_signal():
    """민감 항목 다 공시 + 고아 비율 없음 → 점수 0, 신호 없음."""
    disclosed = list(OMISSION_SENSITIVITY.keys())  # 민감 항목 전부 공시
    ext = _extraction(disclosed, missing=[])
    d6 = detect_selective_disclosure(ext)
    assert d6.score == 0.0
    assert d6.level == "low"
    assert not d6.orphan_ratios
    assert not d6.omitted_sensitive


def test_orphan_ratio_flagged():
    """재활용률(E-6-2)은 공시하면서 폐기물 총량(E-6-1)을 누락 → 고아비율 탐지."""
    ext = _extraction(disclosed=["E-6-2"], missing=["E-6-1"])
    d6 = detect_selective_disclosure(ext)
    assert d6.orphan_ratios, "고아 비율이 탐지돼야 함"
    o = d6.orphan_ratios[0]
    assert o.ratio_code == "E-6-2"
    assert "E-6-1" in o.missing_context
    assert d6.score > 0.0


def test_orphan_not_triggered_when_ratio_absent():
    """유리 비율 자체를 공시 안 했으면 cherry-picking 아님."""
    ext = _extraction(disclosed=[], missing=["E-6-1", "E-6-2"])
    d6 = detect_selective_disclosure(ext)
    assert not d6.orphan_ratios


def test_sensitive_omission_accumulates():
    """민감 항목을 많이 누락할수록 신호 A가 커진다."""
    few = _extraction(disclosed=["E-3-1"], missing=["S-4-2"])
    many = _extraction(
        disclosed=[],
        missing=["E-3-1", "E-6-1", "E-8-1", "S-4-2", "S-9-1", "G-6-1"],
    )
    s_few = detect_selective_disclosure(few).score
    s_many = detect_selective_disclosure(many).score
    assert s_many > s_few


def test_combined_signals_high():
    """고아비율 다수 + 민감 누락 다수 → medium 이상."""
    disclosed = list(RATIO_CONTEXT_PAIRS.keys())  # 유리 비율 전부 공시
    missing = [c for ctx in RATIO_CONTEXT_PAIRS.values() for c in ctx]  # 분모 전부 누락
    missing += ["E-8-1", "S-9-1", "G-6-1"]  # 위반 항목도 누락
    ext = _extraction(disclosed, missing)
    d6 = detect_selective_disclosure(ext)
    assert d6.level in ("medium", "high")
    assert len(d6.orphan_ratios) >= 2


def test_score_bounded():
    """점수는 항상 0~1."""
    ext = _extraction(
        disclosed=list(RATIO_CONTEXT_PAIRS.keys()),
        missing=list(OMISSION_SENSITIVITY.keys())
        + [c for ctx in RATIO_CONTEXT_PAIRS.values() for c in ctx],
    )
    d6 = detect_selective_disclosure(ext)
    assert 0.0 <= d6.score <= 1.0
