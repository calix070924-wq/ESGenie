"""Layer 3 RiskVector 단위 테스트.

- detect() 하위 호환 (기존 인터페이스)
- detect_risk_vector() 5축 분해
- AxisScore 범위 (0~1)
- 각 축 독립 동작 (None 입력 폴백)
- risk_band() 기존 호환
"""
from __future__ import annotations

import pytest

from esgenie.dart_client import load_sample_report
from esgenie.layer0_evidence_graph import build_evidence_graph
from esgenie.layer3_detect import (
    detect,
    detect_risk_vector,
    risk_band,
)
from esgenie.schemas import AxisScore, RiskVector

CORP_CODES = ["005930", "005380", "005490"]

SAMPLE_SENTENCE_CLEAN = "온실가스 배출량은 1,670만 tCO2eq으로 전년 대비 2.1% 감소하였다."
SAMPLE_SENTENCE_GREENWASH = "세계 최고 수준의 혁신적이고 압도적인 친환경 성과를 달성하였다."


# ---- detect() 하위 호환 -----------------------------------------------------

@pytest.mark.parametrize("corp_code", CORP_CODES)
def test_detect_returns_detection_result(corp_code: str) -> None:
    from esgenie.layer3_detect import DetectionResult
    report = load_sample_report(corp_code)
    graph = build_evidence_graph(report)
    rag_text = "재생에너지 사용 비율은 31.0%이며 온실가스 배출량 감축을 추진하고 있다."
    result = detect(rag_text, report)
    assert isinstance(result, DetectionResult)
    assert 0.0 <= result.risk_score <= 100.0


@pytest.mark.parametrize("corp_code", CORP_CODES)
def test_detect_has_risk_vector_field(corp_code: str) -> None:
    """v10: DetectionResult에 risk_vector 필드가 있어야 한다 (기본값 None)."""
    report = load_sample_report(corp_code)
    result = detect("온실가스 배출량은 31%이다.", report)
    assert hasattr(result, "risk_vector")   # 필드 존재


def test_risk_band_low() -> None:
    assert risk_band(10.0) == "LOW"


def test_risk_band_medium() -> None:
    assert risk_band(40.0) == "MEDIUM"


def test_risk_band_high() -> None:
    assert risk_band(60.0) == "HIGH"


def test_risk_band_critical() -> None:
    assert risk_band(80.0) == "CRITICAL"


# ---- detect_risk_vector() 기본 동작 -----------------------------------------

def test_detect_risk_vector_returns_risk_vector() -> None:
    rv = detect_risk_vector(SAMPLE_SENTENCE_CLEAN)
    assert isinstance(rv, RiskVector)


def test_detect_risk_vector_axis_score_range() -> None:
    """모든 축 score가 0~1 범위여야 한다."""
    rv = detect_risk_vector(SAMPLE_SENTENCE_CLEAN)
    for axis in (rv.D1_numeric, rv.D2_modifier, rv.D3_semantic, rv.D5_timeseries):
        assert 0.0 <= axis.score <= 1.0, f"score 범위 초과: {axis}"


def test_detect_risk_vector_aggregate_keys() -> None:
    rv = detect_risk_vector(SAMPLE_SENTENCE_CLEAN)
    assert "risk_score" in rv.aggregate
    assert "level" in rv.aggregate
    assert "top_axis" in rv.aggregate
    assert rv.aggregate["level"] in ("low", "medium", "high")


def test_detect_risk_vector_greenwash_higher_d2() -> None:
    """그린워싱 문장의 D2 score가 깨끗한 문장보다 높아야 한다."""
    rv_clean = detect_risk_vector(SAMPLE_SENTENCE_CLEAN)
    rv_greenwash = detect_risk_vector(SAMPLE_SENTENCE_GREENWASH)
    assert rv_greenwash.D2_modifier.score >= rv_clean.D2_modifier.score


# ---- evidence_graph 연동 ---------------------------------------------------

def test_d1_with_evidence_graph() -> None:
    report = load_sample_report("005930")
    graph = build_evidence_graph(report)
    rv = detect_risk_vector(SAMPLE_SENTENCE_CLEAN, evidence_graph=graph)
    # D1은 evidence_graph가 있을 때 동작 — score가 계산됨
    assert isinstance(rv.D1_numeric.score, float)
    assert 0.0 <= rv.D1_numeric.score <= 1.0


def test_d5_with_evidence_graph() -> None:
    report = load_sample_report("005930")
    graph = build_evidence_graph(report)
    rv = detect_risk_vector(SAMPLE_SENTENCE_CLEAN, evidence_graph=graph)
    assert isinstance(rv.D5_timeseries.score, float)
    assert 0.0 <= rv.D5_timeseries.score <= 1.0


def test_d1_without_evidence_graph_is_zero() -> None:
    """evidence_graph 없으면 D1은 0점(스킵)이어야 한다."""
    rv = detect_risk_vector(SAMPLE_SENTENCE_CLEAN, evidence_graph=None)
    assert rv.D1_numeric.score == 0.0


def test_d5_without_evidence_graph_is_zero() -> None:
    rv = detect_risk_vector(SAMPLE_SENTENCE_CLEAN, evidence_graph=None)
    assert rv.D5_timeseries.score == 0.0


# ---- to_dict 직렬화 ---------------------------------------------------------

def test_risk_vector_to_dict() -> None:
    rv = detect_risk_vector(SAMPLE_SENTENCE_CLEAN)
    d = rv.to_dict()
    for key in ("D1_numeric", "D2_modifier", "D3_semantic", "D5_timeseries", "aggregate"):
        assert key in d, f"{key} 누락"
    for axis_key in ("D1_numeric", "D2_modifier", "D3_semantic", "D5_timeseries"):
        assert "score" in d[axis_key]
        assert "evidence" in d[axis_key]
        assert "detail" in d[axis_key]


# ---- 3사 전체 smoke test ---------------------------------------------------

@pytest.mark.parametrize("corp_code", CORP_CODES)
def test_detect_risk_vector_three_companies(corp_code: str) -> None:
    report = load_sample_report(corp_code)
    graph = build_evidence_graph(report)
    sentence = "온실가스 배출량을 감축하고 있으며 재생에너지 비율을 확대하고 있다."
    rv = detect_risk_vector(sentence, evidence_graph=graph)
    assert isinstance(rv, RiskVector)
    assert 0.0 <= rv.risk_score <= 1.0
