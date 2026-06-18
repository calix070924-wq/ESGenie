"""자동차 부품 업종 모듈.

공급망 실사 응답에서 가장 먼저 필요한 업종이라 배출계수만큼은 명시적으로 채운다.
값은 현재 SSOT 전역 예시계수와 동일하게 시작해 회귀를 피하되, responder/SAQ가
"자동차부품 업종의 Scope 1+2 환산 문항"을 안정적으로 노출할 수 있게 한다.
"""
from __future__ import annotations

from .base import IndustryModule, register

AUTOMOTIVE_PARTS = IndustryModule(
    key="automotive_parts",
    label="자동차 부품",
    aliases=(
        "자동차부품",
        "자동차 부품",
        "자동차부품 제조",
        "자동차 신품 부품",
        "motor vehicle parts",
        "auto parts",
    ),
    emission_factors={
        # 현 단계는 한국 제조업 공통 전력/LNG 계수를 사용한다.
        # 업종 특화가 필요한 경우 이 값만 교체하면 된다.
        "kWh_to_tco2": 0.4781 / 1000,
        "MJ_gas_to_tco2": 0.0000561,
    },
    thresholds={
        # Scope 1+2 파생치가 DART/자가신고 수치와 5% 이상 벌어지면 검토 대상으로 본다.
        "scope12_reconciliation_gap_pct": 5.0,
    },
)

register(AUTOMOTIVE_PARTS)
