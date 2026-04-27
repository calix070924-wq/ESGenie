"""그린워싱 탐지용 과장 수식어 사전.

도메인 분석 결과 정량 근거 없이 자주 남용되는 표현을 카테고리화했다.
"""
from __future__ import annotations

VAGUE_SUPERLATIVES = [
    "선도적", "최고 수준", "세계 최고", "업계 최고", "최고의", "최상의",
    "독보적", "타의 추종을 불허", "압도적", "초격차", "탁월한",
]

VAGUE_INTENSIFIERS = [
    "혁신적", "획기적", "대대적", "전면적", "적극적", "전폭적",
    "최첨단", "미래지향적", "차세대",
]

VAGUE_ENVIRONMENTAL = [
    "친환경적", "지속가능한", "녹색", "청정", "에코", "그린",
    "자연친화적", "환경친화적",
]

VAGUE_COMMITMENT = [
    "최선을 다", "노력하고 있", "지속적으로 개선", "앞장서고 있",
    "선도해 나가", "힘쓰고 있",
]

ALL_VAGUE = (
    VAGUE_SUPERLATIVES + VAGUE_INTENSIFIERS + VAGUE_ENVIRONMENTAL + VAGUE_COMMITMENT
)


def vague_matches(sentence: str) -> list[str]:
    """Return vague phrases found in the sentence (case-sensitive Korean)."""
    return [phrase for phrase in ALL_VAGUE if phrase in sentence]
