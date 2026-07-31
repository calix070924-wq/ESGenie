"""D1 정밀 기권(abstain) 조건 — 배치 2 회귀 테스트.

핵심 불변식:
  - ABSTAIN_ENABLED=0(기본)이면 어떤 케이스에서도 기존 동작(0.9/0.6/0.0)이
    그대로 나와야 한다(회귀 없음).
  - 기권은 "수치 주장 있음 + 코드 매핑됨 + (근거 없음 or 단위 호환 불가)"일
    때만 발동한다. 수치 없음/코드 매핑 없음/evidence_graph 없음은 기권이 아니다.
  - 문장 내 다수 수치 중 하나라도 검증되면 기권이 아니다(실제 위험 신호를
    기권으로 가리지 않기 위함).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from esgenie.layer3_detect import _score_d1_numeric
from esgenie.ssot.detector_5axis import detect_d1_numeric


class _NoNodeGraph:
    """어떤 keyword로 검색해도 노드를 찾지 못하는 그래프 — no_evidence 시나리오."""

    report_year = 2025

    def search_nodes(self, keywords, period=None):
        return []

    # ssot 경로용(EvidenceGraph.nodes_by_metric 호환)
    def nodes_by_metric(self, metric):
        return []


class _UnitMismatchGraph:
    """노드는 있으나 단위가 항상 다른 그래프 — unit_mismatch 시나리오."""

    report_year = 2025

    def __init__(self):
        self._node = SimpleNamespace(id="n1", value=100.0, unit="tCO2eq", period=2025)

    def search_nodes(self, keywords, period=None):
        return [self._node]

    def nodes_by_metric(self, metric):
        return [self._node]


class _VerifiedGraph:
    """정상 매칭되는 그래프 — 검증 성공 시나리오."""

    report_year = 2025

    def __init__(self, value=31.0, unit="%"):
        self._node = SimpleNamespace(id="n1", value=value, unit=unit, period=2025)

    def search_nodes(self, keywords, period=None):
        return [self._node]


class _PartialGraph:
    """두 코드 중 하나만 노드를 갖는 그래프 — '하나라도 검증되면 기권 아님' 시나리오."""

    report_year = 2025

    def __init__(self):
        self._nodes = {"E-4-1": SimpleNamespace(id="n_energy", value=500.0, unit="%", period=2025)}

    def search_nodes(self, keywords, period=None):
        out = []
        for k in keywords:
            if k in self._nodes:
                out.append(self._nodes[k])
        return out


class _CodeSpecificGraph:
    """지정 코드에만 노드를 반환하는 그래프(코드별 검색) — 혼합 문장 시나리오용.
    기본: E-4-2에 단위 불일치(tCO2eq) 노드만, S-2-3(이직률)엔 노드 없음."""

    report_year = 2025

    def __init__(self, nodes=None):
        self._nodes = nodes or {
            "E-4-2": SimpleNamespace(id="n_re", value=100.0, unit="tCO2eq", period=2025),
            "E-4-1": SimpleNamespace(id="n_en", value=100.0, unit="tCO2eq", period=2025),
        }

    def search_nodes(self, keywords, period=None):
        return [self._nodes[k] for k in keywords if k in self._nodes]

    def nodes_by_metric(self, metric):
        return [self._nodes[metric]] if metric in self._nodes else []


# ============================================================================
# layer3_detect._score_d1_numeric (주 타깃)
# ============================================================================

class TestLayer3DetectAbstain:
    def test_no_numeric_claim_never_abstains(self, monkeypatch):
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_ENABLED", True)
        axis = _score_d1_numeric("올해도 최선을 다해 노력하고 있습니다.", _NoNodeGraph())
        assert axis.abstain is False
        assert axis.score == 0.0

    def test_number_without_code_mapping_never_abstains(self, monkeypatch):
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_ENABLED", True)
        # "15건"은 _NUMBER_PATTERN 단위(건)엔 걸리지만, 주변에 _KEYWORD_MAP 키워드가 없어
        # 코드 매핑이 되지 않는다 → 기권 대상 아님(수치는 있으나 검증 대상이 아님).
        s = "본 사업장은 총 15건의 안전점검을 수행하였다."
        axis = _score_d1_numeric(s, _NoNodeGraph())
        assert axis.abstain is False
        assert axis.score == 0.0

    def test_evidence_graph_none_never_abstains(self, monkeypatch):
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_ENABLED", True)
        axis = _score_d1_numeric("재생에너지 사용 비율은 31.0%였다.", None)
        assert axis.abstain is False
        assert axis.score == 0.0
        assert "스킵" in axis.detail

    def test_no_evidence_abstains_when_enabled(self, monkeypatch):
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_ENABLED", True)
        axis = _score_d1_numeric("재생에너지 사용 비율은 31.0%였다.", _NoNodeGraph())
        assert axis.abstain is True
        assert axis.abstain_reason == "no_evidence"
        assert axis.score == 0.0  # 점수 산정 자체는 불변 — 표식만 추가

    def test_no_evidence_stays_score_zero_when_disabled(self, monkeypatch):
        # 비활성(False) 시 회귀 없음 확인 — env(ABSTAIN_ENABLED=1) 오염에도 안전하도록 명시.
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_ENABLED", False)
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_UNIT_MISMATCH", False)
        axis = _score_d1_numeric("재생에너지 사용 비율은 31.0%였다.", _NoNodeGraph())
        assert axis.abstain is False
        assert axis.abstain_reason is None
        assert axis.score == 0.0

    def test_unit_mismatch_does_not_abstain_by_default_even_when_enabled(self, monkeypatch):
        """2026-07-28 실키 A/B: unit_mismatch 기권이 test held-out 지표를 하락시켜
        ABSTAIN_UNIT_MISMATCH 기본값을 False로 바꿨다 — ABSTAIN_ENABLED만으로는
        더 이상 unit_mismatch가 기권되지 않는다(기존 동작인 score 0.0 유지)."""
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_ENABLED", True)
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_UNIT_MISMATCH", False)
        axis = _score_d1_numeric("재생에너지 사용 비율은 31.0%였다.", _UnitMismatchGraph())
        assert axis.abstain is False
        assert axis.abstain_reason is None
        assert axis.score == 0.0

    def test_unit_mismatch_abstains_when_both_flags_enabled(self, monkeypatch):
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_ENABLED", True)
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_UNIT_MISMATCH", True)
        axis = _score_d1_numeric("재생에너지 사용 비율은 31.0%였다.", _UnitMismatchGraph())
        assert axis.abstain is True
        assert axis.abstain_reason == "unit_mismatch"

    def test_unit_mismatch_stays_score_zero_when_disabled(self, monkeypatch):
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_ENABLED", False)
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_UNIT_MISMATCH", False)
        axis = _score_d1_numeric("재생에너지 사용 비율은 31.0%였다.", _UnitMismatchGraph())
        assert axis.abstain is False
        assert axis.score == 0.0

    def test_no_evidence_still_abstains_with_unit_mismatch_flag_off(self, monkeypatch):
        """주 타깃(no_evidence)은 ABSTAIN_UNIT_MISMATCH와 무관하게 계속 기권한다."""
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_ENABLED", True)
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_UNIT_MISMATCH", False)
        axis = _score_d1_numeric("재생에너지 사용 비율은 31.0%였다.", _NoNodeGraph())
        assert axis.abstain is True
        assert axis.abstain_reason == "no_evidence"

    def test_verified_claim_never_abstains(self, monkeypatch):
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_ENABLED", True)
        axis = _score_d1_numeric("재생에너지 사용 비율은 31.0%였다.", _VerifiedGraph(value=31.0))
        assert axis.abstain is False

    def test_one_verified_among_many_never_abstains(self, monkeypatch):
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_ENABLED", True)
        # 에너지(검증 가능) + 용수(노드 없음) 두 지표가 한 문장에 섞여 있어도,
        # 하나라도 검증되면 기권이 아니다.
        s = "에너지 사용량은 500%이며 용수 사용량은 120%였다."
        axis = _score_d1_numeric(s, _PartialGraph())
        assert axis.abstain is False

    def test_mixed_sentence_no_evidence_not_masked_by_neighbor_node(self, monkeypatch):
        """코드리뷰 must-fix 2 회귀: 혼합 문장에서 옆 지표(재생에너지, 단위불일치 노드
        존재) 때문에 자기 코드(이직률, 근거 전무)의 no_evidence가 unit_mismatch로
        오분류돼 조용히 통과하던 버그. no_evidence는 자기 코드 근거 유무로 판정되어야
        하며, no_evidence가 하나라도 있으면 우선한다."""
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_ENABLED", True)
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_UNIT_MISMATCH", False)
        s = "재생에너지 비율은 31.0%이며 이직률은 1.2%였다."
        axis = _score_d1_numeric(s, _CodeSpecificGraph())
        assert axis.abstain is True
        assert axis.abstain_reason == "no_evidence"

    def test_mismatch_risk_not_turned_into_abstain(self, monkeypatch):
        """실제 위험(수치 불일치)은 기권으로 가리지 않는다 — 근거는 있고 값만 다른 경우."""
        monkeypatch.setattr("esgenie.layer3_detect.ABSTAIN_ENABLED", True)
        axis = _score_d1_numeric("재생에너지 사용 비율은 90.0%였다.", _VerifiedGraph(value=31.0))
        assert axis.abstain is False
        assert axis.score > 0.5


# ============================================================================
# ssot/detector_5axis.detect_d1_numeric (정합화 — 프로덕션 지렛대 아님)
# ============================================================================

class TestSsotDetectorAbstain:
    def test_no_claim_never_abstains(self, monkeypatch):
        monkeypatch.setattr("esgenie.ssot.detector_5axis.ABSTAIN_ENABLED", True)
        axis = detect_d1_numeric("수치가 없는 문장입니다.", "E-4-1", _NoNodeGraph())
        assert axis.abstain is False
        assert axis.score == 0.0

    def test_no_kesg_code_abstains_when_enabled(self, monkeypatch):
        monkeypatch.setattr("esgenie.ssot.detector_5axis.ABSTAIN_ENABLED", True)
        axis = detect_d1_numeric("사용량은 128,400 kWh였습니다.", None, _NoNodeGraph())
        assert axis.abstain is True
        assert axis.abstain_reason == "no_evidence"

    def test_no_kesg_code_stays_legacy_when_disabled(self, monkeypatch):
        monkeypatch.setattr("esgenie.ssot.detector_5axis.ABSTAIN_ENABLED", False)
        axis = detect_d1_numeric("사용량은 128,400 kWh였습니다.", None, _NoNodeGraph())
        assert axis.abstain is False
        assert axis.score == 0.6

    def test_no_evidence_node_abstains_when_enabled(self, monkeypatch):
        monkeypatch.setattr("esgenie.ssot.detector_5axis.ABSTAIN_ENABLED", True)
        axis = detect_d1_numeric("사용량은 128,400 kWh였습니다.", "E-4-1", _NoNodeGraph())
        assert axis.abstain is True
        assert axis.abstain_reason == "no_evidence"

    def test_no_evidence_node_stays_legacy_when_disabled(self, monkeypatch):
        monkeypatch.setattr("esgenie.ssot.detector_5axis.ABSTAIN_ENABLED", False)
        axis = detect_d1_numeric("사용량은 128,400 kWh였습니다.", "E-4-1", _NoNodeGraph())
        assert axis.abstain is False
        assert axis.score == 0.9

    def test_unmatched_value_is_not_abstain(self):
        """미일치(근거는 있으나 값이 다름)는 기권이 아니라 실제 위험 — 건드리지 않는다."""
        graph = _VerifiedGraph_ssot()
        axis = detect_d1_numeric("사용량은 999,999 kWh였습니다.", "E-4-1", graph)
        assert axis.abstain is False
        assert axis.score >= 0.5


class _VerifiedGraph_ssot:
    """ssot detect_d1_numeric용 — nodes_by_metric이 단일 노드를 반환."""

    def __init__(self):
        self._node = SimpleNamespace(
            id="n1", value=128_400.0, unit="kWh", period=2025, source_file=None,
        )

    def nodes_by_metric(self, metric):
        return [self._node]
