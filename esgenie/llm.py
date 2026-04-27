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
        if not SETTINGS.use_mock_llm:
            try:
                from openai import OpenAI  # type: ignore
                self._openai_client = OpenAI(api_key=SETTINGS.openai_api_key)
            except Exception:
                self._openai_client = None

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
        if self._openai_client is None:
            return self._mock_complete(system, user, mock_hint, json_mode, variant=mock_variant)
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
            return LLMResponse(content=text, used_mock=False, meta={"model": SETTINGS.openai_model})
        except Exception as exc:
            return self._mock_complete(system, user, mock_hint, json_mode,
                                       variant=mock_variant, error=str(exc))

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
        hint = mock_hint or _detect_hint(system + "\n" + user)
        if hint == "extract":
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
    if "extract" in t or "추출" in text or "json" in t and "항목" in text:
        return "extract"
    if "rewrite" in t or "재작성" in text or "수정" in text:
        return "rewrite"
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


def _mock_rewrite(user_prompt: str) -> str:
    base = _mock_generate(user_prompt)
    return base + " (수치 근거는 DART 공시 및 내부 대시보드 기준.)"


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


# Singleton
CLIENT = LLMClient()
