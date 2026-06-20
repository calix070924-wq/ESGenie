"""시연용 회사명 익명화 별칭.

본선 시연 영상에서 실명 노출로 인한 법적 리스크(명예훼손·식별)를 피하기 위해,
화면·산출물에 찍히는 **회사명만** 익명으로 치환한다. DART 조회는 corp_code로
실제 데이터를 그대로 사용하므로 파이프라인 동작/정확도엔 영향이 없다.

- 이름(부분일치) 기준으로 매칭 → DART 내부 corp_code(8자리)와 stock_code 차이 무관.
- 끄려면 _NAME_ALIASES 를 비우면 된다(완전 무력화, 일반 사용 회귀 없음).
"""
from __future__ import annotations

# 실명 부분문자열 → 표시용 익명. (시연 대상만 등록)
_NAME_ALIASES: dict[str, str] = {
    "화신": "코스피 상장 A사",   # 자동차 차체부품 상장사 — 영상 익명화
}


def display_name(corp_name: str | None) -> str:
    """corp_name 에 등록된 실명이 포함되면 익명으로 치환, 아니면 원본 유지."""
    if not corp_name:
        return corp_name or ""
    for real, alias in _NAME_ALIASES.items():
        if real in corp_name:
            return alias
    return corp_name
