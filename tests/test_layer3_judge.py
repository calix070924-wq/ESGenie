"""Layer 3.5 — 룰+LLM 하이브리드 판정 테스트 (mock LLM 경로)."""
from __future__ import annotations

import json

import pytest

from esgenie.layer3_judge import (
    detect_risk_vector_hybrid,
    judge_risk_vector,
    _parse_judge_response,
    _llm_score,
)
from esgenie.llm import LLMClient
from esgenie.schemas import AxisScore, RiskVector


def _rv(d1=0.0, d2=0.0, d3=0.0, d5=0.0, d1_detail="", d2_detail="") -> RiskVector:
    return RiskVector(
        D1_numeric=AxisScore(score=d1, detail=d1_detail),
        D2_modifier=AxisScore(score=d2, detail=d2_detail),
        D3_semantic=AxisScore(score=d3),
        D5_timeseries=AxisScore(score=d5),
        aggregate={"risk_score": 0.0, "level": "low", "top_axis": ""},
    )


# ---- 트리거 로직 -------------------------------------------------------------

class TestTrigger:
    def test_below_trigger_skips_llm(self):
        """전 축이 트리거 미달이면 LLM 호출 없이 원본 유지 + 스킵 사유 기록."""
        rv = _rv(d1=0.1, d2=0.2)
        out = judge_risk_vector("안전한 문장입니다.", rv, trigger=0.25)
        assert out.aggregate["judge"]["used"] is False
        assert out.D1_numeric.score == 0.1
        assert out.D2_modifier.score == 0.2

    def test_above_trigger_invokes_judge(self):
        rv = _rv(d2=0.5, d2_detail="모호어 2개: ['선도적', '최고 수준']")
        out = judge_risk_vector("당사는 선도적이며 최고 수준입니다.", rv, trigger=0.25)
        assert out.aggregate["judge"]["used"] is True
        assert "D2_modifier" in out.aggregate["judge"]["axes_judged"]

    def test_only_triggered_axes_judged(self):
        """트리거 넘은 축만 판정 — 나머지는 룰 점수 그대로."""
        rv = _rv(d1=0.1, d2=0.6, d2_detail="모호어 1개")
        out = judge_risk_vector("선도적인 기업.", rv, trigger=0.25)
        assert out.aggregate["judge"]["axes_judged"] == ["D2_modifier"]
        assert out.D1_numeric.score == 0.1   # 판정 대상 아님 → 불변


# ---- mock 판정 동작 -----------------------------------------------------------

class TestMockJudgeBehavior:
    def test_d2_false_positive_with_numeric_backing(self):
        """수식어 + 정량 근거 동반 문장 → mock 판정이 D2를 false_positive로 강등.

        false_positive는 룰 오탐 확정이므로 룰 점수를 섞지 않고 LLM 점수만 사용.
        """
        rv = _rv(d2=0.8, d2_detail="모호어 2개: ['최고 수준']")
        sent = "업계 최고 수준의 인증을 취득하였으며 배출량은 1,200 tCO2eq입니다."
        out = judge_risk_vector(sent, rv, trigger=0.25, rule_weight=0.4)
        # false_positive → blended = llm_score = 0.05
        assert out.D2_modifier.score == pytest.approx(0.05, abs=0.01)
        assert "false_positive" in out.D2_modifier.detail

    def test_d2_confirmed_without_numbers(self):
        """정량 근거 없는 과장 문장 → confirmed, 점수 유지."""
        rv = _rv(d2=0.8, d2_detail="모호어 2개: ['선도적', '압도적']")
        sent = "당사는 압도적이고 선도적인 친환경 기업입니다."
        out = judge_risk_vector(sent, rv, trigger=0.25, rule_weight=0.4)
        # blended = 0.4*0.8 + 0.6*0.8 = 0.8
        assert out.D2_modifier.score == pytest.approx(0.8, abs=0.01)
        assert "confirmed" in out.D2_modifier.detail

    def test_d1_uncertain_when_no_match(self):
        """D1 룰 detail이 '수치 매칭 없음'이면 uncertain으로 완화."""
        rv = _rv(d1=0.6, d1_detail="수치 매칭 없음")
        out = judge_risk_vector("배출량 5,000 tCO2eq를 기록했습니다.", rv, trigger=0.25)
        assert out.D1_numeric.score < 0.6
        assert "uncertain" in out.D1_numeric.detail


# ---- 점수 결합 -----------------------------------------------------------------

class TestBlending:
    def test_blend_weights(self):
        rv = _rv(d2=1.0, d2_detail="모호어 4개")
        out = judge_risk_vector(
            "압도적 선도 기업.", rv, trigger=0.25, rule_weight=0.7,
        )
        # mock: confirmed → llm_score = rule = 1.0 → blended = 1.0
        assert out.D2_modifier.score == pytest.approx(1.0)

    def test_aggregate_rebuilt(self):
        rv = _rv(d2=0.8, d2_detail="모호어 2개")
        sent = "최고 수준 인증 취득, 1,200 tCO2eq."
        out = judge_risk_vector(sent, rv, trigger=0.25, rule_weight=0.4)
        # D2 0.8→0.05(false_positive)로 내려가면 aggregate도 재계산되어야 함
        assert out.aggregate["risk_score"] == pytest.approx(0.25 * 0.05, abs=0.01)
        assert out.aggregate["level"] == "low"

    def test_llm_score_fallback_by_verdict(self):
        assert _llm_score({"verdict": "false_positive"}, 0.8) == 0.0
        assert _llm_score({"verdict": "uncertain"}, 0.8) == pytest.approx(0.4)
        assert _llm_score({"verdict": "confirmed"}, 0.8) == pytest.approx(0.8)
        assert _llm_score({"llm_score": 0.33}, 0.8) == pytest.approx(0.33)
        assert _llm_score({"llm_score": 7}, 0.8) == 1.0   # 클램프


# ---- 응답 파싱 -----------------------------------------------------------------

class TestParsing:
    def test_parse_clean_json(self):
        text = json.dumps({"axes": {"D2_modifier": {"verdict": "confirmed", "llm_score": 0.7}}})
        out = _parse_judge_response(text)
        assert out["D2_modifier"]["verdict"] == "confirmed"

    def test_parse_json_with_noise(self):
        text = '판정 결과입니다:\n{"axes": {"D1_numeric": {"verdict": "uncertain"}}}'
        out = _parse_judge_response(text)
        assert "D1_numeric" in out

    def test_parse_garbage_returns_empty(self):
        assert _parse_judge_response("판정 불가") == {}

    def test_garbage_response_keeps_rule_score(self):
        """LLM 응답 파싱 실패 시 룰 점수 유지 (안전 폴백)."""
        class BrokenLLM:
            def complete(self, **kwargs):
                from esgenie.llm import LLMResponse
                return LLMResponse(content="not json at all", used_mock=True, meta={})
        rv = _rv(d2=0.8, d2_detail="모호어 2개")
        out = judge_risk_vector("압도적 기업.", rv, llm=BrokenLLM(), trigger=0.25)
        assert out.D2_modifier.score == 0.8


# ---- 하이브리드 통합 진입점 -----------------------------------------------------

class TestHybridEntry:
    def test_hybrid_returns_risk_vector(self):
        out = detect_risk_vector_hybrid(
            "당사는 선도적인 친환경 기업으로 최고 수준의 성과를 달성했습니다.",
            evidence_graph=None,
            retrieved_chunks=[{"id": "c1", "text": "온실가스 감축 노력"}],
        )
        assert isinstance(out, RiskVector)
        assert "judge" in out.aggregate

    def test_mock_llm_judge_roundtrip(self):
        """mock LLMClient로 judge 프롬프트 → JSON 응답 왕복 확인."""
        llm = LLMClient()
        resp = llm.complete(
            system="judge",
            user=(
                "[[JUDGE_TASK]]\n\n[문장]\n압도적 1위 기업, 1,200 tCO2eq 감축.\n"
                "[축별 룰 판정]\n- D2_modifier | rule_score=0.5 | detail=모호어 1개\n"
            ),
            mock_hint="judge",
            json_mode=True,
        )
        data = json.loads(resp.content)
        assert "axes" in data
        assert data["axes"]["D2_modifier"]["verdict"] == "false_positive"
