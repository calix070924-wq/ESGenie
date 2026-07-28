"""ABSTAIN_ENABLED × ABSTAIN_UNIT_MISMATCH 조합별 동작 — 2026-07-28 배치.

배경: 실키 A/B(scripts/abstain_ab_eval.py, docs/abstention_metrics_result.md)에서
unit_mismatch 기권이 test held-out의 Overall·recall을 하락시켰다(주 타깃인
no_evidence는 0건 관측). 그 결과 ABSTAIN_UNIT_MISMATCH를 기본 False로 신설해
unit_mismatch 기권을 별도로 끌 수 있게 했다. 이 파일은 두 플래그의 4가지
조합(off/off, on/off, on/on, off/on)에서 no_evidence·unit_mismatch 각각의
기권 여부가 정확한지 확인한다.
"""
from __future__ import annotations

from types import SimpleNamespace

from esgenie.config import ABSTAIN_UNIT_MISMATCH as _DEFAULT_ABSTAIN_UNIT_MISMATCH
from esgenie.layer3_detect import _score_d1_numeric
from esgenie.ssot.detector_5axis import detect_d1_numeric


def test_default_config_value_is_false():
    """기본값 자체가 False인지 확인 — 회귀 시 다른 테스트가 조용히 무의미해지는 것 방지."""
    assert _DEFAULT_ABSTAIN_UNIT_MISMATCH is False


class _NoNodeGraph:
    report_year = 2025

    def search_nodes(self, keywords, period=None):
        return []

    def nodes_by_metric(self, metric):
        return []


class _UnitMismatchGraph:
    report_year = 2025

    def __init__(self):
        self._node = SimpleNamespace(id="n1", value=100.0, unit="tCO2eq", period=2025)

    def search_nodes(self, keywords, period=None):
        return [self._node]

    def nodes_by_metric(self, metric):
        return [self._node]


SENTENCE = "재생에너지 사용 비율은 31.0%였다."


class TestLayer3DetectToggleMatrix:
    """layer3_detect._score_d1_numeric — 주 타깃(실제 생성문 D1 경로)."""

    def test_both_off_no_evidence_not_abstained(self, monkeypatch):
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_ENABLED", False)
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_UNIT_MISMATCH", False)
        axis = _score_d1_numeric(SENTENCE, _NoNodeGraph())
        assert axis.abstain is False and axis.score == 0.0

    def test_both_off_unit_mismatch_not_abstained(self, monkeypatch):
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_ENABLED", False)
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_UNIT_MISMATCH", False)
        axis = _score_d1_numeric(SENTENCE, _UnitMismatchGraph())
        assert axis.abstain is False and axis.score == 0.0

    def test_enabled_only_no_evidence_abstains(self, monkeypatch):
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_ENABLED", True)
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_UNIT_MISMATCH", False)
        axis = _score_d1_numeric(SENTENCE, _NoNodeGraph())
        assert axis.abstain is True
        assert axis.abstain_reason == "no_evidence"

    def test_enabled_only_unit_mismatch_does_not_abstain(self, monkeypatch):
        """ABSTAIN_ENABLED=True만으로는 unit_mismatch가 더 이상 기권되지 않는다(핵심 변경)."""
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_ENABLED", True)
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_UNIT_MISMATCH", False)
        axis = _score_d1_numeric(SENTENCE, _UnitMismatchGraph())
        assert axis.abstain is False
        assert axis.abstain_reason is None
        assert axis.score == 0.0

    def test_both_on_unit_mismatch_abstains(self, monkeypatch):
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_ENABLED", True)
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_UNIT_MISMATCH", True)
        axis = _score_d1_numeric(SENTENCE, _UnitMismatchGraph())
        assert axis.abstain is True
        assert axis.abstain_reason == "unit_mismatch"

    def test_both_on_no_evidence_still_abstains(self, monkeypatch):
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_ENABLED", True)
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_UNIT_MISMATCH", True)
        axis = _score_d1_numeric(SENTENCE, _NoNodeGraph())
        assert axis.abstain is True
        assert axis.abstain_reason == "no_evidence"

    def test_unit_mismatch_flag_alone_without_abstain_enabled_has_no_effect(self, monkeypatch):
        """ABSTAIN_ENABLED=False면 ABSTAIN_UNIT_MISMATCH가 True여도 아무 것도 안 바뀐다."""
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_ENABLED", False)
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_UNIT_MISMATCH", True)
        axis = _score_d1_numeric(SENTENCE, _UnitMismatchGraph())
        assert axis.abstain is False
        assert axis.score == 0.0


class TestSsotDetectorToggleMatrix:
    """ssot.detect_d1_numeric — no_evidence만 기권, unit_mismatch 분기 자체가 없음(정합화 확인)."""

    def test_no_evidence_abstains_when_enabled_regardless_of_unit_mismatch_flag(self, monkeypatch):
        monkeypatch.setattr("esgenie.ssot.detector_5axis.ABSTAIN_ENABLED", True)
        axis = detect_d1_numeric("사용량은 128,400 kWh였습니다.", "E-4-1", _NoNodeGraph())
        assert axis.abstain is True
        assert axis.abstain_reason == "no_evidence"
