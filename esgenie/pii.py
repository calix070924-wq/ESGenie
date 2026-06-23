"""PII(개인정보) 마스킹 모듈.

mask_pii(text)      — 단일 문자열의 PII를 플레이스홀더로 치환
mask_pii_obj(obj)   — dict/list/str을 재귀 순회하며 모든 문자열 값에 mask_pii 적용
                      키는 건드리지 않는다.

ESG 수치(연도·비율·단위 동반 숫자·천단위 콤마 숫자)는 마스킹하지 않는다.
모든 숫자 패턴은 하이픈 구분자를 포함한 형태로만 매칭한다.
"""
from __future__ import annotations

import re
from typing import Any


def _acct_replace(m: re.Match[str]) -> str:
    """총 자릿수 10 이상인 경우만 계좌번호로 치환 (거짓양성 억제)."""
    digits = re.sub(r"\D", "", m.group(0))
    return "[ACCT]" if len(digits) >= 10 else m.group(0)


# (compiled_pattern, placeholder)
# placeholder가 None이면 _acct_replace 콜백을 사용한다.
# 더 구체적인 패턴이 먼저 오도록 순서를 유지한다.
PII_PATTERNS: list[tuple[re.Pattern[str], str | None]] = [
    # 신용카드: 4-4-4-4 (16자리)
    (re.compile(r"(?<!\d)\d{4}-\d{4}-\d{4}-\d{4}(?!\d)"), "[CARD]"),
    # 주민등록번호: 6-7
    (re.compile(r"(?<!\d)\d{6}-\d{7}(?!\d)"), "[RRN]"),
    # 사업자등록번호: 3-2-5
    (re.compile(r"(?<!\d)\d{3}-\d{2}-\d{5}(?!\d)"), "[BRN]"),
    # 휴대전화: 01X-XXXX-XXXX (하이픈 필수)
    (re.compile(r"(?<!\d)01[016-9]-\d{3,4}-\d{4}(?!\d)"), "[PHONE]"),
    # 유선전화: 0X(X)-XXXX-XXXX (하이픈 필수)
    (re.compile(r"(?<!\d)0\d{1,2}-\d{3,4}-\d{4}(?!\d)"), "[PHONE]"),
    # 이메일
    (re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"), "[EMAIL]"),
    # 계좌번호: 3그룹 이상 하이픈 구분, 총 자릿수 ≥ 10 (콜백 검증)
    # YYYY-MM-DD(8자리) 같은 날짜는 10자리 미만이라 콜백에서 통과
    (re.compile(r"(?<!\d)\d{2,6}-\d{2,6}-\d{2,6}(?:-\d{2,6})?(?!\d)"), None),
]


def mask_pii(text: str) -> str:
    """텍스트 내 PII 패턴을 플레이스홀더로 치환해 반환."""
    for pattern, placeholder in PII_PATTERNS:
        if placeholder is None:
            text = pattern.sub(_acct_replace, text)
        else:
            text = pattern.sub(placeholder, text)
    return text


def mask_pii_obj(obj: Any) -> Any:
    """dict/list/str을 재귀 순회하며 모든 문자열 값에 mask_pii 적용.

    키는 건드리지 않는다. 비문자열 값(int, float 등)은 그대로 반환한다.
    """
    if isinstance(obj, str):
        return mask_pii(obj)
    if isinstance(obj, dict):
        return {k: mask_pii_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask_pii_obj(item) for item in obj]
    return obj
