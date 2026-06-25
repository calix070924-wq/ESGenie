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
    # 단독 환경 라벨 — 상품·서비스 수식어로 쓰이면 근거 검증 필요
    "친환경", "친환경적", "지속가능한", "녹색", "청정", "에코", "그린",
    "자연친화적", "환경친화적",
]

VAGUE_COMMITMENT = [
    "최선을 다", "노력하고 있", "지속적으로 개선", "앞장서고 있",
    "선도해 나가", "힘쓰고 있",
]

# ── 환경부·공정위 그린워싱 적발 패턴 (2023 친환경 위장표시·광고) ──────────────
# 절대형·검증불가 주장: 객관적 근거(시험성적서·LCA·인증) 없이 단정.
ABSOLUTE_UNVERIFIABLE = [
    "탄소중립", "탄소배출 걱정 없", "무공해", "무해", "100% 친환경",
    "100% 생분해", "완전 분해", "완전분해", "자연으로 돌아가",
]

# 전제조건이 빠진 주장: '산업적 퇴비화 시설에서만' 등 조건 없이 쓰면 오인 유발.
CONDITION_REQUIRED = [
    "생분해", "퇴비화 가능", "재활용 가능", "자연분해", "썩는",
]

# 막연·검증불가 추상 표현 (소비자 오인 가능, 객관 검증 불가).
VAGUE_ABSTRACT = [
    "지구를 위한", "자연을 위한", "착한", "안심",
]

ALL_VAGUE = (
    VAGUE_SUPERLATIVES + VAGUE_INTENSIFIERS + VAGUE_ENVIRONMENTAL + VAGUE_COMMITMENT
    + ABSOLUTE_UNVERIFIABLE + CONDITION_REQUIRED + VAGUE_ABSTRACT
)


def vague_matches(sentence: str, industry_module=None) -> list[str]:
    """Return vague phrases found in the sentence (case-sensitive Korean).

    industry_module이 주어지면 전역 ALL_VAGUE에 업종 추가 패턴(lexicon_extra)을
    합친 목록으로 매칭한다. None이면 전역 동작 그대로(회귀 없음).
    """
    if industry_module is None:
        terms = ALL_VAGUE
    else:
        from ..industry.base import resolve_terms
        terms = resolve_terms(industry_module, "lexicon_extra", ALL_VAGUE)
    return [phrase for phrase in terms if phrase in sentence]


def match_categories(sentence: str) -> dict[str, list[str]]:
    """매칭된 표현을 규제기관 패턴 카테고리별로 분류 (판정 근거 설명용).

    카테고리:
      absolute     — 절대형·검증불가 주장 (근거 필요)
      condition    — 전제조건 누락형 (조건 명시 필요)
      abstract     — 막연·검증불가 추상 표현
      vague        — 모호 수식어/과장
    """
    cats = {
        "absolute": [p for p in ABSOLUTE_UNVERIFIABLE if p in sentence],
        "condition": [p for p in CONDITION_REQUIRED if p in sentence],
        "abstract": [p for p in VAGUE_ABSTRACT if p in sentence],
        "vague": [p for p in (VAGUE_SUPERLATIVES + VAGUE_INTENSIFIERS
                              + VAGUE_ENVIRONMENTAL + VAGUE_COMMITMENT) if p in sentence],
    }
    return {k: v for k, v in cats.items() if v}
