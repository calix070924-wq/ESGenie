"""양식 레지스트리 — key로 Framework를 조회한다."""
from __future__ import annotations

from ..schema import Framework
from .kesg_self import KESG28, KESG61
from .saq5 import SAQ5, SAQ5_ENV

_REGISTRY: dict[str, Framework] = {
    # 캐노니컬 자가진단(substrate) — 1차 출력.
    KESG28.key: KESG28,
    KESG61.key: KESG61,
    # OEM 폼 어댑터.
    SAQ5.key: SAQ5,
    SAQ5_ENV.key: SAQ5_ENV,
}


def get_framework(key: str) -> Framework:
    try:
        return _REGISTRY[key]
    except KeyError:
        raise KeyError(
            f"미등록 양식 키: '{key}'. 사용 가능: {sorted(_REGISTRY)}"
        ) from None


def all_framework_keys() -> list[str]:
    return sorted(_REGISTRY)
