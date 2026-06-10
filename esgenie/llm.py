"""LLM 추상화 계층.

- `OPENAI_API_KEY` 존재 시: 실제 OpenAI chat 호출
- 없으면: 템플릿 기반 mock 응답 (제출 환경에서 키 없이도 시연 가능)

Mock 응답은 실제 파이프라인(추출 → 생성 → 탐지 → 검증)이 end-to-end로
돌아가도록 현실적인 K-ESG 보고서 문체를 모사한다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .config import SETTINGS


@dataclass
class LLMResponse:
    content: str
    used_mock: bool
    meta: dict[str, Any]


class LLMClient:
    def __init__(self) -> None:
        self._openai_client = None
        self._anthropic_client = None
        if not SETTINGS.use_mock_llm:
            if SETTINGS.openai_api_key:
                try:
                    from openai import OpenAI  # type: ignore
                    self._openai_client = OpenAI(api_key=SETTINGS.openai_api_key)
                except Exception:
                    self._openai_client = None
            if self._openai_client is None and SETTINGS.anthropic_api_key:
                try:
                    import anthropic  # type: ignore
                    self._anthropic_client = anthropic.Anthropic(api_key=SETTINGS.anthropic_api_key)
                except Exception:
                    self._anthropic_client = None

    # ---- public API ---------------------------------------------------
    def complete(
        self,
        system: str,
        user: str,
        *,
        mock_hint: str = "",
        mock_variant: str = "clean",
        temperature: float = 0.3,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Unified completion entry point with automatic mock fallback.

        `mock_variant`:
            "clean"       — 보수적·수치 기반 초안 (기본)
            "greenwash"   — 시연용 과장 초안 (Layer 3/4 시연에 사용)
        """
        if self._openai_client is not None:
            try:
                kwargs: dict[str, Any] = {
                    "model": SETTINGS.openai_model,
                    "temperature": temperature,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                }
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                resp = self._openai_client.chat.completions.create(**kwargs)
                text = resp.choices[0].message.content or ""
                return LLMResponse(content=text, used_mock=False,
                                   meta={"model": SETTINGS.openai_model, "provider": "openai"})
            except Exception as exc:
                return self._mock_complete(system, user, mock_hint, json_mode,
                                           variant=mock_variant, error=str(exc))

        if self._anthropic_client is not None:
            try:
                sys_prompt = system
                if json_mode:
                    sys_prompt += "\n\n응답은 반드시 유효한 JSON 객체 하나로만 출력하라. 코드블록·설명 금지."
                resp = self._anthropic_client.messages.create(
                    model=SETTINGS.anthropic_model,
                    max_tokens=2048,
                    temperature=temperature,
                    system=sys_prompt,
                    messages=[{"role": "user", "content": user}],
                )
                text = "".join(
                    block.text for block in resp.content if getattr(block, "type", "") == "text"
                )
                return LLMResponse(content=text, used_mock=False,
                                   meta={"model": SETTINGS.anthropic_model, "provider": "anthropic"})
            except Exception as exc:
                return self._mock_complete(system, user, mock_hint, json_mode,
                                           variant=mock_variant, error=str(exc))

        return self._mock_complete(system, user, mock_hint, json_mode, variant=mock_variant)

    # ---- mock ---------------------------------------------------------
    def _mock_complete(
        self,
        system: str,
        user: str,
        mock_hint: str,
        json_mode: bool,
        *,
        variant: str = "clean",
        error: str | None = None,
    ) -> LLMResponse:
        # Route by task hint embedded in prompt
        detected = _detect_hint(system + "\n" + user)
        # 명시적 mock_hint가 "generate"여도 user 프롬프트에 재작성 제약이 포함되어 있으면
        # rewrite 경로로 라우팅한다. (Layer 4 자가 검증 루프는 generate_section을 재사용하기 때문)
        if mock_hint == "generate" and detected == "rewrite":
            hint = "rewrite"
        else:
            hint = mock_hint or detected
        if hint == "judge":
            content = _mock_judge(user)
        elif hint == "extract":
            content = _mock_extract(user)
        elif hint == "generate":
            if variant == "greenwash":
                content = _mock_greenwash_generate(user)
            else:
                content = _mock_generate(user)
        elif hint == "rewrite":
            content = _mock_rewrite(user)
        else:
            content = _mock_default(user)
        if json_mode and not content.strip().startswith("{"):
            content = json.dumps({"result": content}, ensure_ascii=False)
        return LLMResponse(
            content=content,
            used_mock=True,
            meta={"hint": hint, "error": error},
        )


def _detect_hint(text: str) -> str:
    t = text.lower()
    # 0) 2차 판정 (judge) — layer3_judge가 프롬프트에 마커를 심는다.
    if "[[JUDGE_TASK]]" in text:
        return "judge"
    # 1) 재작성 (rewrite) — 명시적 재작성 신호가 가장 우선.
    #    Layer 4 자가 검증 루프가 generate_section을 재사용하면서 user 프롬프트에
    #    "추가 지시:" 또는 "=== 재작성 제약" 블록을 덧붙이기 때문에 이를 먼저 잡는다.
    if (
        "rewrite" in t
        or "재작성" in text
        or "추가 지시:" in text
        or "=== 재작성 제약" in text
        or "수정" in text
    ):
        return "rewrite"
    # 2) 추출(extract) — 명시 키워드만으로 판정. (이전 "json+항목" 규칙은 일반 보고서
    #    프롬프트에도 매칭되는 false positive가 있어 제거.)
    if "extract" in t or "추출" in text:
        return "extract"
    # 3) 일반 작성(generate)
    if "보고서" in text or "generate" in t or "작성" in text:
        return "generate"
    return "default"


# ---- mock content builders ------------------------------------------------
_ENV_TEMPLATE = """\
## 환경 성과

### 전략 및 목표
{corp}은(는) {year_net}년 탄소중립 달성을 목표로 재생에너지 전환, 에너지 효율화, \
친환경 제품 확대를 3대 전략 축으로 추진하고 있습니다. \
글로벌 사업장 온실가스 배출량을 단계적으로 감축하며, \
제3자 검증을 통해 환경 데이터의 신뢰성을 확보하고 있습니다.

### 핵심 지표
| 항목 | {year}년 실적 | 단위 |
|---|---|---|
| Scope 1+2 온실가스 배출량 | {ghg:,} | tCO2eq |
| 재생에너지 사용 비율 | {renew} | % |
| 폐기물 재활용 비율 | {waste} | % |
| 용수 재사용 비율 | {water_reuse} | % |
| 에너지 사용량 | {energy} | TJ |

### 주요 활동
{year}년 기준 Scope 1+2 온실가스 배출량은 {ghg:,} tCO2eq으로, \
재생에너지 전력구매계약(PPA) 확대 및 고효율 설비 투자를 통해 전년 대비 감축 추세를 유지하고 있습니다. \
폐기물 재활용 비율은 {waste}%로 지속 개선되고 있으며, \
온실가스 데이터는 제3자 검증기관의 검증을 받아 신뢰성을 확보하였습니다.

### 향후 계획
{year_net}년 탄소중립 로드맵에 따라 국내외 사업장의 재생에너지 조달 비율을 단계적으로 확대하고, \
공정 개선과 저탄소 원부자재 전환을 병행하여 온실가스 배출량을 지속 감축할 예정입니다.\
"""

_SOC_TEMPLATE = """\
## 사회 성과

### 전략 및 목표
{corp}은(는) 임직원의 안전·건강·성장을 최우선 가치로 삼으며, \
다양성·형평성·포용성(DEI) 문화 정착을 통해 지속 가능한 사회적 가치를 창출합니다. \
협력사 ESG 관리 강화와 지역사회 기여를 통해 공급망 전반의 사회적 책임을 이행하고 있습니다.

### 핵심 지표
| 항목 | {year}년 실적 | 단위 |
|---|---|---|
| 정규직 비율 | {reg} | % |
| 자발적 이직률 | {turn} | % |
| 여성 구성원 비율 | {fem} | % |
| 장애인 고용률 | {disabled} | % |
| 산업재해율 | {safety} | % |
| 개인정보 침해 건수 | {privacy} | 건 |

### 주요 활동
정규직 비율 {reg}% 및 자발적 이직률 {turn}%로 고용 안정성을 유지하고 있으며, \
산업재해율 {safety}%는 업계 평균을 하회하는 수준입니다. \
여성 구성원 비율 {fem}%, 장애인 고용률 {disabled}%를 기록하며 \
다양성 목표를 지속 이행하고 있습니다.

### 향후 계획
여성 리더십 파이프라인 강화 및 산업안전 투자 확대를 통해 다양성·안전 지표를 지속 개선하고, \
1차 협력사 대상 인권 실사 범위를 단계적으로 확대할 예정입니다.\
"""

_GOV_TEMPLATE = """\
## 지배구조 성과

### 전략 및 목표
{corp}은(는) 이사회 독립성과 전문성을 기반으로 주주 가치를 극대화하며, \
ESG위원회를 통한 전략적 의사결정으로 지속 가능한 기업 지배구조를 구현합니다. \
투명한 공시와 주주 소통 강화를 통해 장기적 신뢰를 구축하고 있습니다.

### 핵심 지표
| 항목 | {year}년 실적 | 단위 |
|---|---|---|
| 사외이사 비율 | {od} | % |
| 여성 이사 비율 | {fd} | % |
| 이사회 출석률 | {att} | % |
| 배당 성향 | {dividend} | % |

### 주요 활동
이사회는 사외이사 비율 {od}%로 독립성을 확보하고 있으며, \
출석률 {att}%로 적극적인 경영 감시 기능을 수행하고 있습니다. \
ESG위원회를 통해 분기별 ESG 현황을 점검하고, \
윤리경영 체계와 내부감사 기구를 통해 지배구조 건전성을 유지하고 있습니다.

### 향후 계획
이사회 다양성 확대 및 주주총회 전자투표 참여율 향상을 위한 소통 채널을 강화하고, \
ESG 공시 품질 제고를 통해 이해관계자 신뢰를 높일 예정입니다.\
"""


def _mock_generate(user_prompt: str) -> str:
    ctx = _parse_context(user_prompt)
    area = ctx.get("area", "E")
    corp = ctx.get("corp", "당사")
    year = ctx.get("year", 2024)
    if area == "E":
        return _ENV_TEMPLATE.format(
            corp=corp,
            year=year,
            year_net=ctx.get("year_net", 2050),
            ghg=int(ctx.get("ghg", 16_700_000)),
            renew=ctx.get("renew", 31.0),
            waste=ctx.get("waste", 95.8),
            water_reuse=ctx.get("water_reuse", 28.5),
            energy=ctx.get("energy", 355),
        )
    if area == "S":
        return _SOC_TEMPLATE.format(
            corp=corp,
            year=year,
            reg=ctx.get("reg", 98.6),
            turn=ctx.get("turn", 2.4),
            fem=ctx.get("fem", 24.8),
            disabled=ctx.get("disabled", 1.8),
            safety=ctx.get("safety", 0.098),
            privacy=ctx.get("privacy", 0),
        )
    return _GOV_TEMPLATE.format(
        corp=corp,
        year=year,
        od=ctx.get("od", 63.6),
        fd=ctx.get("fd", 18.2),
        att=ctx.get("att", 98.0),
        dividend=ctx.get("dividend", 34.5),
    )


_GREENWASH_E = """\
## 환경 성과

### 전략 및 목표
{corp}은(는) 세계 최고 수준의 친환경 경영을 선도적으로 실천하며, 혁신적인 탄소중립 성과를 달성하고 있습니다. \
압도적인 재생에너지 전환과 탄소 감축 혁신으로 글로벌 기후 위기 극복을 선도하고 있습니다.

### 핵심 지표
| 항목 | 실적 | 단위 |
|---|---|---|
| Scope 1+2 온실가스 배출량 | 200,000 | tCO2eq |
| 재생에너지 사용 비율 | 85 | % |
| 폐기물 재활용 비율 | 100 | % |
| 온실가스 감축률 | 50 | % |

### 주요 활동
재생에너지 비율은 업계 최고 수준인 85%를 기록하였고, \
온실가스 배출량은 전년 대비 50% 대폭 감축되어 200,000 tCO2eq으로 획기적으로 개선되었습니다. \
폐기물 재활용 비율 100%를 달성하며 완전한 순환경제를 실현하였습니다.

### 향후 계획
혁신적인 기술 투자를 통해 탄소 넷제로를 조기 달성하며 글로벌 친환경 경영의 새로운 기준을 제시할 예정입니다.\
"""

_GREENWASH_S = """\
## 사회 성과

### 전략 및 목표
{corp}은(는) 인권·다양성 분야에서 독보적인 위상을 확립하며 최고 수준의 조직문화를 선도하고 있습니다. \
완벽한 안전 사업장 구현과 탁월한 인력 관리로 업계 최고의 신뢰를 받고 있습니다.

### 핵심 지표
| 항목 | 실적 | 단위 |
|---|---|---|
| 정규직 비율 | 100 | % |
| 자발적 이직률 | 0 | % |
| 여성 구성원 비율 | 65 | % |
| 산업재해율 | 0.00 | % |

### 주요 활동
여성 구성원 비율은 혁신적으로 65%를 달성하였고, \
자발적 이직률은 0%로 완벽한 인력 안정성을 보였습니다. \
산업재해율 0.00%로 완벽한 안전 사업장을 구현하였습니다.

### 향후 계획
압도적인 인적 자원 경쟁력을 바탕으로 글로벌 최고 수준의 사회적 책임을 지속 선도할 예정입니다.\
"""

_GREENWASH_G = """\
## 지배구조 성과

### 전략 및 목표
{corp}의 이사회는 세계 최고 수준의 투명성과 독립성을 선도적으로 구현하며 \
압도적인 지배구조 건전성을 확립하였습니다.

### 핵심 지표
| 항목 | 실적 | 단위 |
|---|---|---|
| 사외이사 비율 | 95 | % |
| 여성 이사 비율 | 50 | % |
| 이사회 출석률 | 100 | % |

### 주요 활동
사외이사 비율은 혁신적으로 95%에 달하며, 여성 이사 비율은 50%로 타의 추종을 불허하는 다양성을 확보하였습니다. \
이사회 출석률은 완벽한 100%로 최상의 거버넌스를 구현하고 있습니다.

### 향후 계획
세계 최고 수준의 지배구조 모범 사례를 지속 확산하여 글로벌 투자자의 신뢰를 압도적으로 높일 예정입니다.\
"""


def _mock_greenwash_generate(user_prompt: str) -> str:
    ctx = _parse_context(user_prompt)
    area = ctx.get("area", "E")
    corp = ctx.get("corp", "당사")
    if area == "E":
        return _GREENWASH_E.format(corp=corp)
    if area == "S":
        return _GREENWASH_S.format(corp=corp)
    return _GREENWASH_G.format(corp=corp)


# ---- _mock_rewrite 보조 데이터 -----------------------------------------------

# D2: 최상급·과장·모호 수식어. 긴 표현부터 매칭되도록 길이 내림차순.
_VAGUE_MODIFIERS: tuple[str, ...] = tuple(sorted([
    "세계 최고 수준의", "세계 최고 수준", "세계 최고의",
    "업계 최고 수준인", "업계 최고 수준의", "업계 최고 수준",
    "타의 추종을 불허하는",
    "혁신적인", "혁신적으로", "혁신적",
    "압도적인", "압도적으로", "압도적",
    "선도적인", "선도적으로", "선도적",
    "획기적으로", "획기적인", "획기적",
    "탁월하게", "탁월한", "탁월",
    "독보적인", "독보적",
    "최상의", "최고 수준의", "최고의", "최상위", "최첨단", "최선의",
    "완벽한", "완전한",
    "대폭",
], key=len, reverse=True))


def _strip_vague_modifiers(text: str) -> str:
    """D2 제약: 최상급·모호 수식어를 제거하거나 보수적 표현으로 치환."""
    for mod in _VAGUE_MODIFIERS:
        text = text.replace(mod + " ", "")
        text = text.replace(mod, "")
    text = text.replace("선도하고 있습니다", "지속적으로 추진하고 있습니다")
    text = text.replace("을 선도하며", "을 꾸준히 추진하며")
    text = text.replace("를 선도하며", "를 꾸준히 추진하며")
    text = text.replace("선도합니다", "꾸준히 추진합니다")
    return text


def _greenwash_numeric_replacements(ctx: dict[str, Any]) -> list[tuple[str, str]]:
    """D1 제약: 그린워싱 템플릿 잔존 수치 → DART 실측값 치환 규칙."""
    return [
        # 환경 (E)
        ("200,000 tCO2eq", f"{int(ctx.get('ghg', 16_700_000)):,} tCO2eq"),
        ("재생에너지 사용 비율 | 85", f"재생에너지 사용 비율 | {ctx.get('renew', 31.0)}"),
        ("재생에너지 비율은 업계 최고 수준인 85%", f"재생에너지 사용 비율은 {ctx.get('renew', 31.0)}%"),
        ("재생에너지 비율은 85%", f"재생에너지 사용 비율은 {ctx.get('renew', 31.0)}%"),
        ("폐기물 재활용 비율 | 100", f"폐기물 재활용 비율 | {ctx.get('waste', 95.8)}"),
        ("폐기물 재활용 비율 100%", f"폐기물 재활용 비율 {ctx.get('waste', 95.8)}%"),
        ("온실가스 감축률 | 50", "온실가스 감축률 | (DART 시계열 검증 필요)"),
        ("전년 대비 50% 대폭 감축", "DART 시계열 기준 단계적 감축"),
        # 사회 (S)
        ("정규직 비율 | 100", f"정규직 비율 | {ctx.get('reg', 98.6)}"),
        ("자발적 이직률 | 0", f"자발적 이직률 | {ctx.get('turn', 2.4)}"),
        ("자발적 이직률은 0%", f"자발적 이직률은 {ctx.get('turn', 2.4)}%"),
        ("여성 구성원 비율 | 65", f"여성 구성원 비율 | {ctx.get('fem', 24.8)}"),
        ("여성 구성원 비율은 혁신적으로 65%", f"여성 구성원 비율은 {ctx.get('fem', 24.8)}%"),
        ("여성 구성원 비율은 65%", f"여성 구성원 비율은 {ctx.get('fem', 24.8)}%"),
        ("산업재해율 | 0.00", f"산업재해율 | {ctx.get('safety', 0.098)}"),
        ("산업재해율 0.00%", f"산업재해율 {ctx.get('safety', 0.098)}%"),
        # 지배구조 (G)
        ("사외이사 비율 | 95", f"사외이사 비율 | {ctx.get('od', 63.6)}"),
        ("사외이사 비율은 혁신적으로 95%", f"사외이사 비율은 {ctx.get('od', 63.6)}%"),
        ("여성 이사 비율 | 50", f"여성 이사 비율 | {ctx.get('fd', 18.2)}"),
        ("여성 이사 비율은 50%", f"여성 이사 비율은 {ctx.get('fd', 18.2)}%"),
        ("이사회 출석률 | 100", f"이사회 출석률 | {ctx.get('att', 98.0)}"),
        ("이사회 출석률은 완벽한 100%", f"이사회 출석률은 {ctx.get('att', 98.0)}%"),
        ("이사회 출석률은 100%", f"이사회 출석률은 {ctx.get('att', 98.0)}%"),
    ]


def _mock_rewrite(user_prompt: str) -> str:
    """user_prompt에 포함된 5축 제약 지시문을 파싱해 실제로 다른 텍스트를 반환.

    제약이 없으면 _mock_generate 결과를 그대로 돌려준다.
    """
    # "=== 재작성 제약 ===" 블록이 없으면 기존 동작 (clean baseline)
    if "=== 재작성 제약" not in user_prompt and "추가 지시:" not in user_prompt:
        return _mock_generate(user_prompt)

    # 제약 블록 추출 — "=== 재작성 제약" 부터 다음 "===" 마커까지
    block = user_prompt
    start = user_prompt.find("=== 재작성 제약")
    if start >= 0:
        end_marker = user_prompt.find("준수해 재작성", start)
        block = user_prompt[start: end_marker + 30] if end_marker > 0 else user_prompt[start:]

    axes = {
        axis: f"[{axis}]" in block
        for axis in ("D1_numeric", "D2_modifier", "D3_semantic", "D5_timeseries")
    }

    # 어떤 축도 명시 안 됐는데 "추가 지시:"만 있는 경우 → legacy 피드백 모드.
    # 이때는 D1·D2를 보수적으로 둘 다 적용해 의미 있는 변화가 생기게 한다.
    if not any(axes.values()):
        axes["D1_numeric"] = True
        axes["D2_modifier"] = True

    # 기준선: DART 실측값 기반 보수적 텍스트
    text = _mock_generate(user_prompt)
    ctx = _parse_context(user_prompt)

    # D1: 잔존 그린워싱 수치 → DART 실측값으로 치환 (clean baseline에는 no-op)
    if axes["D1_numeric"]:
        for src, dst in _greenwash_numeric_replacements(ctx):
            text = text.replace(src, dst)

    # D2: 최상급·과장 수식어 제거 / 보수적 표현으로 치환
    if axes["D2_modifier"]:
        text = _strip_vague_modifiers(text)

    # D3/D5 (+ 활성 축 전반)에 대한 검증 근거 섹션 추가
    appendix: list[str] = []
    if axes["D1_numeric"]:
        appendix.append(
            "- **[D1 수치 정확성]** 모든 정량 수치는 DART 공시 원문에 기재된 값과 "
            "동일하며, 임의 추정치 없이 그대로 인용하였습니다."
        )
    if axes["D2_modifier"]:
        appendix.append(
            "- **[D2 표현 절제]** 최상급·과장 수식어를 제거하고 정량 근거가 있는 "
            "표현으로 대체하였습니다."
        )
    if axes["D3_semantic"]:
        appendix.append(
            "- **[D3 근거 충실성]** 본 보고서는 DART 공시 원문 및 K-ESG 가이드라인의 "
            "의미 범위 내에서만 보수적으로 서술되었습니다."
        )
    if axes["D5_timeseries"]:
        appendix.append(
            "- **[D5 시계열 일관성]** 전년 대비 추세는 DART 시계열 데이터의 실제 "
            "방향과 일치하도록 보수적으로 서술하였습니다."
        )

    if appendix:
        text = text + "\n\n### 자가 검증 근거\n" + "\n".join(appendix)

    return text


def _mock_extract(user_prompt: str) -> str:
    return json.dumps({
        "extracted": True,
        "note": "mock extraction — real extraction is supplied by DART JSON loader in pipeline.",
    }, ensure_ascii=False)


def _mock_default(user_prompt: str) -> str:
    return "[mock LLM] 요청을 처리했습니다. 실제 응답을 보려면 OPENAI_API_KEY를 설정하세요."


def _parse_context(text: str) -> dict[str, Any]:
    ctx: dict[str, Any] = {}
    # crude JSON snippet detection
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        obj = json.loads(text[start:end])
        if isinstance(obj, dict):
            ctx.update({k: v for k, v in obj.items() if isinstance(v, (str, int, float))})
            # try to unpack kesg-shaped data
            if "kesg_data" in obj:
                kd = obj["kesg_data"]
                def _v(code: str, default: Any) -> Any:
                    entry = kd.get(code) or {}
                    return entry.get("value", default) if isinstance(entry, dict) else default
                # 환경 지표
                ctx.setdefault("ghg", _v("E-3-1", 16_700_000))
                ctx.setdefault("renew", _v("E-4-2", 31.0))
                ctx.setdefault("waste", _v("E-6-2", 95.8))
                ctx.setdefault("water_reuse", _v("E-5-2", 28.5))
                ctx.setdefault("energy", _v("E-4-1", 355))
                # 사회 지표
                ctx.setdefault("reg", _v("S-2-2", 98.6))
                ctx.setdefault("turn", _v("S-2-3", 2.4))
                ctx.setdefault("fem", _v("S-3-1", 24.8))
                ctx.setdefault("disabled", _v("S-3-3", 1.8))
                ctx.setdefault("safety", _v("S-4-2", 0.098))
                ctx.setdefault("privacy", _v("S-8-2", 0))
                # 지배구조 지표
                ctx.setdefault("od", _v("G-1-2", 63.6))
                ctx.setdefault("fd", _v("G-1-4", 18.2))
                ctx.setdefault("att", _v("G-2-1", 98.0))
                ctx.setdefault("dividend", _v("G-3-4", 34.5))
            if "corp_name" in obj:
                ctx["corp"] = obj["corp_name"]
    except (ValueError, json.JSONDecodeError):
        pass
    # area detection — look for explicit marker first, then fall back
    m = re.search(r"영역\s*:\s*([ESG])\b", text)
    if m:
        ctx["area"] = m.group(1)
    elif "E=환경" not in text and "환경" in text:
        ctx["area"] = "E"
    elif "사회" in text and "S=사회" not in text:
        ctx["area"] = "S"
    elif "지배구조" in text and "G=지배구조" not in text:
        ctx["area"] = "G"
    return ctx


def _mock_judge(user_prompt: str) -> str:
    """LLM 2차 판정 mock — layer3_judge 프롬프트 형식에 맞춰 결정적 판정을 돌려준다.

    실제 키 없이도 하이브리드 파이프라인(룰 1차 → LLM 2차)이 end-to-end로
    동작하도록, 룰 detail에서 읽을 수 있는 신호로 간단한 판정을 모사한다:
      - D2: 문장에 정량 수치가 함께 있으면 false_positive (수식어가 근거를 수반)
      - D1: 룰 detail이 '수치 매칭 없음'이면 uncertain (매칭 실패 ≠ 허위)
      - 그 외: confirmed (룰 점수 유지)
    """
    # 문장 추출
    m = re.search(r"\[문장\]\s*(.+?)\s*\[축별 룰 판정\]", user_prompt, re.S)
    sentence = m.group(1).strip() if m else ""
    has_number = bool(re.search(r"\d", sentence))

    axes_out: dict[str, Any] = {}
    for am in re.finditer(
        r"- (?P<axis>D[1235]_\w+) \| rule_score=(?P<score>[0-9.]+) \| detail=(?P<detail>.*)",
        user_prompt,
    ):
        axis, rule_score, detail = am.group("axis"), float(am.group("score")), am.group("detail")
        if axis == "D2_modifier" and has_number:
            verdict, llm_score = "false_positive", 0.05
            rationale = "[MOCK] 수식어가 문장 내 정량 수치로 뒷받침됨 — 과장으로 보기 어려움"
        elif axis == "D1_numeric" and "수치 매칭 없음" in detail:
            verdict, llm_score = "uncertain", round(rule_score * 0.5, 4)
            rationale = "[MOCK] 증빙 노드 매칭 실패는 허위 단정 근거가 아님 — 추가 증빙 필요"
        else:
            verdict, llm_score = "confirmed", rule_score
            rationale = "[MOCK] 룰 판정과 일치 — 위험 유지"
        axes_out[axis] = {
            "verdict": verdict,
            "llm_score": llm_score,
            "rationale": rationale,
            "quote": sentence[:80],
        }
    return json.dumps({"axes": axes_out}, ensure_ascii=False)


# Singleton
CLIENT = LLMClient()
