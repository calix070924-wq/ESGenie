"""업종 모듈(세로축) 패키지.

공통엔진(가로축)은 업종 무관하게 동작하고, 업종별 차이는 IndustryModule
설정 객체로만 주입한다. 이 패키지를 import하면 내장 업종 모듈이 레지스트리에
자동 등록된다.
"""
from __future__ import annotations

from .base import (
    IndustryModule,
    all_keys,
    get_module,
    infer_industry,
    register,
    resolve_map,
    resolve_module,
    resolve_scalar,
    resolve_terms,
)

# 내장 업종 모듈 등록 (import 시 self-register)
from . import automotive_parts  # noqa: F401,E402

__all__ = [
    "IndustryModule",
    "all_keys",
    "get_module",
    "infer_industry",
    "register",
    "resolve_map",
    "resolve_module",
    "resolve_scalar",
    "resolve_terms",
]
