"""K-ESG ↔ ISSB/KSSB 얇은 매핑 레이어.

설계 원칙
---------
- 이 모듈은 엔진 로직이 아니라 **순수 데이터 컨테이너 + 조회 함수**다.
- 매핑이 없는 K-ESG 코드는 기존 동작을 그대로 유지한다(빈 결과 반환).
- ISSB/KSSB 세부 문단번호는 출처가 확실할 때만 채우고, 불확실하면 빈 문자열로 둔다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from . import kesg_items

Standard = Literal["S1", "S2"]
Pillar = Literal["Governance", "Strategy", "RiskManagement", "MetricsTargets"]
Anchor = Literal["climate", "greenwash_defense", "general"]
KSSB = Literal["KSSB1", "KSSB2"]


@dataclass(frozen=True)
class ISSBMapping:
    kesg_code: str
    standard: Standard
    pillar: Pillar
    kssb: KSSB
    requirement: str
    anchor: Anchor
    paragraph: str = ""


def _kssb_for(standard: Standard) -> KSSB:
    return "KSSB1" if standard == "S1" else "KSSB2"


PILLAR_LABELS: dict[str, str] = {
    "Governance": "거버넌스",
    "Strategy": "전략",
    "RiskManagement": "위험관리",
    "MetricsTargets": "지표·목표",
}


# ISSB 권위가 명확한 항목만 제한적으로 매핑한다.
MAPPINGS: list[ISSBMapping] = [
    ISSBMapping(
        kesg_code="E-3-1",
        standard="S2",
        pillar="MetricsTargets",
        kssb=_kssb_for("S2"),
        requirement="Scope1·2 온실가스 배출량 공시",
        anchor="climate",
    ),
    ISSBMapping(
        kesg_code="E-3-2",
        standard="S2",
        pillar="MetricsTargets",
        kssb=_kssb_for("S2"),
        requirement="Scope3 온실가스 배출량 공시",
        anchor="greenwash_defense",
    ),
    ISSBMapping(
        kesg_code="E-3-3",
        standard="S2",
        pillar="MetricsTargets",
        kssb=_kssb_for("S2"),
        requirement="온실가스 배출량 제3자 검증",
        anchor="climate",
    ),
    ISSBMapping(
        kesg_code="E-4-1",
        standard="S2",
        pillar="MetricsTargets",
        kssb=_kssb_for("S2"),
        requirement="기후 관련 에너지 사용량 공시",
        anchor="climate",
    ),
    ISSBMapping(
        kesg_code="E-4-2",
        standard="S2",
        pillar="MetricsTargets",
        kssb=_kssb_for("S2"),
        requirement="재생에너지 사용 비중 공시",
        anchor="climate",
    ),
    ISSBMapping(
        kesg_code="G-1-1",
        standard="S1",
        pillar="Governance",
        kssb=_kssb_for("S1"),
        requirement="지속가능성 관련 위험·기회를 감독하는 거버넌스 공시",
        anchor="general",
    ),
    ISSBMapping(
        kesg_code="E-1-1",
        standard="S1",
        pillar="Strategy",
        kssb=_kssb_for("S1"),
        requirement="지속가능성 관련 위험·기회에 대한 전략과 목표 공시",
        anchor="general",
    ),
    ISSBMapping(
        kesg_code="S-5-2",
        standard="S1",
        pillar="RiskManagement",
        kssb=_kssb_for("S1"),
        requirement="지속가능성 관련 리스크 식별·평가·관리 절차 공시",
        anchor="general",
    ),
]


def mappings_for(code: str) -> list[ISSBMapping]:
    return [mapping for mapping in MAPPINGS if mapping.kesg_code == code]


def has_issb(code: str) -> bool:
    return bool(mappings_for(code))


def pillars_for(code: str) -> list[str]:
    seen: set[str] = set()
    pillars: list[str] = []
    for mapping in mappings_for(code):
        if mapping.pillar not in seen:
            seen.add(mapping.pillar)
            pillars.append(mapping.pillar)
    return pillars


def by_anchor(anchor: str) -> list[ISSBMapping]:
    return [mapping for mapping in MAPPINGS if mapping.anchor == anchor]


assert set(PILLAR_LABELS) == {"Governance", "Strategy", "RiskManagement", "MetricsTargets"}
assert all(kesg_items.by_code(mapping.kesg_code) is not None for mapping in MAPPINGS)
assert all(mapping.kssb == _kssb_for(mapping.standard) for mapping in MAPPINGS)
