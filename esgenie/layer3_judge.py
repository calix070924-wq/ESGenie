"""Layer 3.5 — LLM 2차 판정 (룰+LLM 하이브리드 검출).

설계 철학
---------
  1차 (룰, layer3_detect):  전 문장 고속 스크리닝 — 재현 가능, 비용 0, recall 담당
  2차 (LLM, 본 모듈):       룰이 의심한 문장만 맥락 판정 — precision 담당

룰 단독의 한계를 LLM이 보정한다:
  - D2: "업계 최고 수준의 인증을 취득(ISO 50001, 2025)" → 사전 매칭은 과장으로
    잡지만 LLM은 정량 근거 수반 여부를 보고 false_positive 처리
  - D5: "감소 목표를 수립했다"(미래 계획) vs "감소했다"(실적 주장) 시제 구분
  - D1: 연도·페이지번호 오탐 제거, 단위 불일치 식별

비용 제어: 룰 점수가 JUDGE_TRIGGER(기본 0.25) 이상인 축이 하나도 없으면
LLM 호출 자체를 생략한다. 전수 LLM 호출 대비 호출량을 대폭 줄이는 것이
이 아키텍처의 핵심 주장(벤치마크로 입증 예정).

점수 결합:
  - verdict=false_positive → final = llm_score (룰 오탐 확정 — 룰 점수 잔존 금지)
  - 그 외(confirmed/uncertain) → final = JUDGE_RULE_WEIGHT*rule + (1-w)*llm
    (기본 0.4 : 0.6 — 환경변수로 조정 가능)
"""
from __future__ import annotations

import json
import re
from typing import Any

from .config import D_WEIGHTS, JUDGE_RULE_WEIGHT, JUDGE_TRIGGER, RISK_LEVEL_THRESHOLDS
from .schemas import AxisScore, RiskVector

# ---- 프롬프트 ---------------------------------------------------------------

JUDGE_SYSTEM = """\
당신은 K-ESG 공시 그린워싱 검증 감사관이다. 룰 기반 1차 검출기가 의심 표시한
문장을 받아, 각 위험 축이 진짜 위험인지 맥락을 보고 최종 판정한다.

판정 기준:
- false_positive: 룰이 잡았지만 맥락상 위험이 아님
  (예: 수식어가 같은 문장의 정량 근거로 뒷받침됨, 숫자가 연도/식별자임,
   '감소 목표 수립' 같은 미래 계획을 실적 주장으로 오인)
- uncertain: 위험 단정도 해제도 어려움 — 추가 증빙 필요
- confirmed: 룰 판정대로 실제 위험 (정량 근거 없는 과장, 증빙과 불일치하는
   수치 주장, 실측 추세와 반대 방향 주장)

각 축에 대해 llm_score(0.0=안전 ~ 1.0=위험)와 판정 근거가 되는 문장 내
인용(quote)을 반드시 제시하라. 보수적으로 판정하되, 근거 없는 위험 해제는 금지.

핵심 경계 (자주 틀리는 지점):
- 수식어·주장이 **같은 문장 안의 구체적 정량 실적**(증빙과 일치하는 수치·비율)으로
  뒷받침되면 → false_positive. 수치가 증빙값과 맞으면 D1은 confirmed가 아니다.
- 반대로 **측정 가능한 근거가 전혀 없는 공허한 비전·다짐**("최선을 다하겠다",
  "미래지향적", "앞장서겠다")은 정량 실적이 0이므로 → D2는 confirmed(위험)다.
  공허할수록 해제가 아니라 확정이다.

[판정 예시]
예시1 (정상 → 해제): "선도적인 공정 혁신으로 단위당 온실가스 배출을 전년 대비 18% 줄였습니다."
  · 증빙: 배출 원단위 전년比 -18% 일치
  · D2_modifier=false_positive (수식어 '선도적인'이 동일 문장의 정량 실적 -18%로 뒷받침)
  · D5_timeseries=false_positive (감소 주장이 실측 추세와 일치)
예시2 (위험 → 확정): "지속가능한 미래를 향해 친환경 경영에 앞장서겠습니다."
  · 증빙: 해당 수치·실적 없음
  · D2_modifier=confirmed (측정 가능한 정량 근거가 전무한 공허한 다짐 — 과장 위험)"""

JUDGE_PROMPT_TEMPLATE = """\
[[JUDGE_TASK]]

[문장]
{sentence}

[축별 룰 판정]
{axes_block}

[축 설명]
- D1_numeric: 문장 수치가 DART/OCR 증빙값과 일치하는가 (연도·식별자는 수치 주장이 아님)
- D2_modifier: 최상급·모호 수식어가 정량 근거 없이 쓰였는가
- D3_semantic: 문장이 검색된 원문 근거의 의미 범위를 벗어나는가
- D5_timeseries: 증감 주장이 실측 시계열 방향과 일치하는가 (미래 계획·목표는 실적 주장이 아님)

다음 JSON 스키마로만 응답하라:
{{"axes": {{"<축이름>": {{"verdict": "false_positive|uncertain|confirmed",
"llm_score": 0.0, "rationale": "한 문장 근거", "quote": "문장 내 인용"}}}}}}
판정 대상 축: {axis_names}"""

_VERDICT_FALLBACK_SCORE = {
    # llm_score 누락 시 verdict 기반 폴백 (rule_score 인자에 곱함)
    "false_positive": 0.0,
    "uncertain": 0.5,
    "confirmed": 1.0,
}


# ---- 공개 API ---------------------------------------------------------------

def judge_risk_vector(
    sentence: str,
    rv: RiskVector,
    llm: Any | None = None,
    *,
    trigger: float = JUDGE_TRIGGER,
    rule_weight: float = JUDGE_RULE_WEIGHT,
) -> RiskVector:
    """룰 1차 RiskVector에 LLM 2차 판정을 적용해 보정된 RiskVector를 반환.

    트리거 미달(전 축 < trigger)이면 LLM 호출 없이 원본을 그대로 반환하고
    aggregate["judge"]에 스킵 사유를 기록한다.
    """
    axes = {
        "D1_numeric":    rv.D1_numeric,
        "D2_modifier":   rv.D2_modifier,
        "D3_semantic":   rv.D3_semantic,
        "D5_timeseries": rv.D5_timeseries,
    }
    # 중립값·스킵 축은 판정할 신호가 없으므로 트리거에서 제외 (불필요 호출 방지)
    triggered = {
        name: ax for name, ax in axes.items()
        if ax.score >= trigger
        and "중립값" not in ax.detail
        and "스킵" not in ax.detail
    }

    if not triggered:
        rv.aggregate["judge"] = {
            "used": False,
            "reason": f"전 축 룰 점수 < {trigger} — LLM 호출 생략(비용 절감)",
        }
        return rv

    if llm is None:
        llm = _get_judge_llm()

    axes_block = "\n".join(
        f"- {name} | rule_score={ax.score} | detail={ax.detail}"
        for name, ax in triggered.items()
    )
    resp = llm.complete(
        system=JUDGE_SYSTEM,
        user=JUDGE_PROMPT_TEMPLATE.format(
            sentence=sentence,
            axes_block=axes_block,
            axis_names=", ".join(triggered),
        ),
        mock_hint="judge",
        json_mode=True,
        temperature=0.0,
    )
    verdicts = _parse_judge_response(resp.content)

    # ── 축별 점수 결합 ────────────────────────────────────────────────
    new_axes: dict[str, AxisScore] = {}
    judged_axes: list[str] = []
    for name, ax in axes.items():
        v = verdicts.get(name)
        if name not in triggered or v is None:
            new_axes[name] = ax
            continue
        judged_axes.append(name)
        llm_score = _llm_score(v, ax.score)
        if v.get("verdict") == "false_positive":
            # 룰 오탐 확정 — 룰 점수를 섞으면 오탐이 잔존하므로 LLM 점수만 사용
            blended = round(llm_score, 4)
        else:
            blended = round(rule_weight * ax.score + (1.0 - rule_weight) * llm_score, 4)
        new_axes[name] = AxisScore(
            score=blended,
            evidence=ax.evidence,
            detail=(
                f"{ax.detail} | LLM판정[{v.get('verdict', '?')}] "
                f"rule={ax.score:.2f}→final={blended:.2f}: {v.get('rationale', '')}"
            ),
        )

    out = _rebuild_vector(new_axes)
    out.aggregate["judge"] = {
        "used": True,
        "used_mock": bool(resp.used_mock),
        "model": resp.meta.get("model", "mock"),
        "axes_judged": judged_axes,
        "verdicts": {k: verdicts[k].get("verdict") for k in judged_axes if k in verdicts},
        "rule_weight": rule_weight,
    }
    return out


def detect_risk_vector_hybrid(
    claim_sentence: str,
    evidence_graph: Any | None = None,
    retrieved_chunks: list[dict[str, Any]] | None = None,
    industry_stats: dict[str, Any] | None = None,
    _d3_index: Any | None = None,
    llm: Any | None = None,
) -> RiskVector:
    """룰 1차(detect_risk_vector) + LLM 2차(judge_risk_vector) 통합 진입점.

    시그니처는 detect_risk_vector와 호환 — 호출부에서 함수만 바꿔치기 가능.
    """
    from .layer3_detect import detect_risk_vector

    rv = detect_risk_vector(
        claim_sentence,
        evidence_graph=evidence_graph,
        retrieved_chunks=retrieved_chunks,
        industry_stats=industry_stats,
        _d3_index=_d3_index,
    )
    return judge_risk_vector(claim_sentence, rv, llm=llm)


# ---- 내부 헬퍼 --------------------------------------------------------------

_JUDGE_LLM = None


def _get_judge_llm() -> Any:
    """판정용 LLMClient 싱글톤 (문장 루프에서 재생성 방지)."""
    global _JUDGE_LLM
    if _JUDGE_LLM is None:
        from .llm import LLMClient
        _JUDGE_LLM = LLMClient()
    return _JUDGE_LLM


def _parse_judge_response(text: str) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return {}
        try:
            data = json.loads(m.group(0))
        except Exception:
            return {}
    axes = data.get("axes", data)
    return axes if isinstance(axes, dict) else {}


def _llm_score(verdict: dict[str, Any], rule_score: float) -> float:
    s = verdict.get("llm_score")
    if isinstance(s, (int, float)):
        return max(0.0, min(1.0, float(s)))
    factor = _VERDICT_FALLBACK_SCORE.get(verdict.get("verdict", ""), 1.0)
    return round(rule_score * factor, 4)


def _rebuild_vector(axes: dict[str, AxisScore]) -> RiskVector:
    weighted = sum(D_WEIGHTS[k] * ax.score for k, ax in axes.items())
    risk_score = round(weighted, 4)
    if risk_score < RISK_LEVEL_THRESHOLDS["low"]:
        level = "low"
    elif risk_score < RISK_LEVEL_THRESHOLDS["medium"]:
        level = "medium"
    else:
        level = "high"
    top_axis = max(axes, key=lambda k: axes[k].score)
    return RiskVector(
        D1_numeric=axes["D1_numeric"],
        D2_modifier=axes["D2_modifier"],
        D3_semantic=axes["D3_semantic"],
        D5_timeseries=axes["D5_timeseries"],
        aggregate={"risk_score": risk_score, "level": level, "top_axis": top_axis},
    )
