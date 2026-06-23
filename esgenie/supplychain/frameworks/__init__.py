"""양식 레지스트리 — key로 Framework를 조회한다."""
from __future__ import annotations

from ..schema import Framework
from .hmc import HMC
from .kesg_self import KESG28, KESG61
from .rba_self import RBA42
from .saq5 import SAQ5, SAQ5_ENV

_REGISTRY: dict[str, Framework] = {
    # 캐노니컬 자가진단(substrate) — 1차 출력.
    #   공시 기둥: K-ESG / 실사 기둥: RBA v8.0.
    KESG28.key: KESG28,
    KESG61.key: KESG61,
    RBA42.key: RBA42,
    # OEM 폼 어댑터.
    #   공시: SAQ(Drive Sustainability) / 실사: 현대차.
    SAQ5.key: SAQ5,
    SAQ5_ENV.key: SAQ5_ENV,
    HMC.key: HMC,
}


def get_framework(key: str) -> Framework:
    try:
        return _REGISTRY[key]
    except KeyError:
        raise KeyError(
            f"미등록 양식 키: '{key}'. 사용 가능: {sorted(_REGISTRY)}"
        ) from None


def all_framework_keys() -> list[str]:
    # 등록(삽입) 순서를 유지한다 — substrate(K-ESG/RBA) 먼저, OEM 어댑터(SAQ/현대차) 나중.
    # 알파벳 정렬을 쓰지 않는 이유: UI 기본 선택(첫 항목)이 새 양식 등록만으로 바뀌지 않게 한다.
    return list(_REGISTRY)
