"""그린워싱 탐지용 과장 수식어 사전.

도메인 분석 결과 정량 근거 없이 자주 남용되는 표현을 카테고리화했다.
"""
from __future__ import annotations

import re

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


# ── 문맥 면제 (2026-07-29) ──────────────────────────────────────────────────
# 라이브 실측(00164788 E 섹션)에서 D2 발화 3건이 전부 오탐이었다. 원인은 어휘가
# 아니라 **문맥**이다 — 사전에서 단어를 빼면 진성 그린워싱을 놓치므로
# (`친환경`·`탄소중립`은 환경부 적발 패턴의 핵심 신호), 특정 구간만 면제한다.
# 상세: docs/D2_영향조사_2026-07-29.md

# (다) 조직 접미 — 모호어에 **바로 붙으면** 부서 고유명사다.
# `탄소중립추진팀`은 부서명이고 `탄소중립을 추진한다`는 주장이다.
_ORG_SUFFIXES: tuple[str, ...] = (
    "추진팀", "추진단", "추진실", "위원회", "협의체", "본부", "센터", "사업부",
    "팀", "부", "실", "국", "과",
)
# 긴 접미 우선(추진팀이 팀보다 앞) — 정규식 대안 순서가 곧 매칭 우선순위다.
_ORG_SUFFIX_RE = re.compile("|".join(re.escape(s) for s in _ORG_SUFFIXES))


def _kesg_item_name_spans(sentence: str) -> list[tuple[int, int]]:
    """문장에 **항목명 전체**가 나타난 구간 목록.

    Phase 1 v2 본문은 미공시 항목을 이름으로 나열한다("…친환경 인증 제품 및 서비스
    항목의 공개가 필요하다"). 우리 항목명을 우리 검출기가 그린워싱으로 잡는 자책골이라,
    L2가 정직하게 미공시를 밝힐수록 D2가 오르는 구조였다.

    `name`만 쓴다 — `search_terms`는 안 된다. `친환경 인증`(E-9-1 search_term)으로
    면제하면 벤치 양성 GOLD-40 "친환경 인증 침대로 가족 건강을 지키세요"가 통째로
    면제된다. 항목명은 그 자체로 K-ESG 지표 참조라 오인 소지가 없지만, search_terms는
    광고 문구와도 겹치는 짧은 표현이다.
    """
    from .kesg_items import ALL_ITEMS

    spans: list[tuple[int, int]] = []
    for item in ALL_ITEMS:
        for variant in _name_variants(item.name):
            start = sentence.find(variant)
            while start != -1:
                spans.append((start, start + len(variant)))
                start = sentence.find(variant, start + 1)
    return spans


def _name_variants(name: str) -> tuple[str, ...]:
    """항목명의 구분자 변형. 정의는 `친환경 인증 제품/서비스`인데 본문은
    `친환경 인증 제품 및 서비스`로 쓴다 — `/`를 자연어 접속으로 풀어 쓴 형태까지 덮는다."""
    if "/" not in name:
        return (name,)
    return (name, name.replace("/", " 및 "), name.replace("/", ", "), name.replace("/", " "))


def _is_exempt(sentence: str, start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    """[start, end) 구간의 모호어 매칭이 문맥상 면제 대상인가."""
    # (나) 항목명 구간 안에 들어 있다
    if any(s <= start and end <= e for s, e in spans):
        return True
    # (다) 직후에 조직 접미가 바로 붙어 있다 (공백이 있으면 면제 아님)
    m = _ORG_SUFFIX_RE.match(sentence, end)
    return m is not None


def vague_matches(sentence: str, industry_module=None) -> list[str]:
    """Return vague phrases found in the sentence (case-sensitive Korean).

    industry_module이 주어지면 전역 ALL_VAGUE에 업종 추가 패턴(lexicon_extra)을
    합친 목록으로 매칭한다. None이면 전역 동작 그대로(회귀 없음).

    K-ESG 항목명 구간과 조직 고유명사는 면제한다 — 모든 출현이 면제 문맥일 때만
    빠지므로, 같은 단어가 문장 안에서 한 번이라도 자유롭게 쓰이면 그대로 잡힌다.
    """
    if industry_module is None:
        terms = ALL_VAGUE
    else:
        from ..industry.base import resolve_terms
        terms = resolve_terms(industry_module, "lexicon_extra", ALL_VAGUE)

    spans = _kesg_item_name_spans(sentence)
    hits: list[str] = []
    for phrase in terms:
        if phrase not in sentence:
            continue
        # 한 표현이 여러 번 나올 수 있다 — 자유로운 출현이 하나라도 있으면 히트.
        free = False
        for m in re.finditer(re.escape(phrase), sentence):
            if not _is_exempt(sentence, m.start(), m.end(), spans):
                free = True
                break
        if free:
            hits.append(phrase)
    return hits


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
