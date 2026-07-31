"""D1 토픽 귀속 회귀 (2026-07-27).

라이브 실측 근거: `outputs/audit_trace_00164788_E_20260726_203615.json` 문장 1(D1=1.0).
detail 11건 중 7건은 Δ=0.0%(대칭 성립)였고, 남은 3건은 전부 **숫자가 엉뚱한 코드로
귀속된 것**이었다 — 102,462(E-2-1)를 E-6-1 폐기물 72,463과, 2.0(E-2-2)을 E-4-2
재생에너지 10.0과, 3,136,024(E-3-2)를 E-3-1 396,152와 비교했다. 세 값 모두 원장값과
정확히 일치하므로 데이터 문제는 0건, 검출기 오탐 100%다.

원인 4가지:
  (가) _KEYWORD_MAP이 13개 코드만 커버 — E-2-1·E-2-2·E-3-2·E-7-1·E-8-1 등 부재
  (나) "Scope" → E-3-1 무조건 — Scope3를 Scope1+2로 보낸다
  (다) 최후 폴백이 문장 전체 스캔(`kw in sentence`) — 다지표 문장에서 임의 배정
  (라) 숫자 정규식이 "Scope3 배출량"에서 ('3','배')를 뽑는다

이 파일이 고정하는 것: 토픽 인덱스가 kesg_items.search_terms 단일 출처에서 나오고,
겹침을 leftmost-longest로 해소하고, 근처에 용어가 없으면 None을 돌린다는 것.
"""
from __future__ import annotations

from types import SimpleNamespace

from esgenie.layer3_detect import (
    _NUMBER_PATTERN,
    _match_topic_near,
    _score_d1_numeric,
    _sentence_topic_codes,
)

# 실측 문장 — trace의 sentence_text 그대로.
LIVE_SENTENCE = (
    "현대모비스의 원부자재 사용량은 102,462.0톤으로 집계되었다. "
    "재생 원부자재 비율은 2.0%로 나타나 원부자재 중 재생자재 사용 비중이 제한적임을 알 수 있다. "
    "온실가스 배출량은 Scope1과 Scope2를 합쳐 396,152.0 tCO2eq이며, "
    "Scope3 배출량은 3,136,024.0 tCO2eq에 달한다. "
    "총 에너지 사용량은 7,929.0 TJ이며, 이 중 재생에너지 사용 비율은 10.0%로 나타났다. "
    "용수 사용량은 55,647.0톤이며, 폐기물 배출량은 72,463.0톤, "
    "폐기물 재활용 비율은 92.9%로 집계되었다. "
    "대기오염물질 배출량은 210,680.0kg, 수질오염물질 배출량은 555,371.0kg이다. "
    "환경 법규 위반 건수는 1건으로 보고되었다."
)

# 원장 실적값 — trace의 핵심지표 표와 동일(전부 정답).
LEDGER = {
    "E-2-1": (102462.0, "톤"),
    "E-2-2": (2.0, "%"),
    "E-3-1": (396152.0, "tCO2eq"),
    "E-3-2": (3136024.0, "tCO2eq"),
    "E-4-1": (7929.0, "TJ"),
    "E-4-2": (10.0, "%"),
    "E-5-1": (55647.0, "ton"),
    "E-6-1": (72463.0, "톤"),
    "E-6-2": (92.9, "%"),
    "E-8-1": (1.0, "건"),
}


def _claims(sentence: str) -> list[tuple[str, str, str | None]]:
    """(숫자, 단위, 귀속 코드) 목록 — 정규식 + 토픽 매칭 결과."""
    out = []
    for m in _NUMBER_PATTERN.finditer(sentence):
        _, code = _match_topic_near(sentence, m.start(), m.end())
        out.append((m.group("num"), m.group("unit"), code))
    return out


class _LedgerGraph:
    """LEDGER 값을 코드별 노드로 돌려주는 최소 그래프."""

    report_year = 2025

    def __init__(self, ledger=None):
        self.nodes = {
            code: SimpleNamespace(id=f"n_{code}", metric=code, value=v, unit=u,
                                  period=2025, origin="ocr_unstructured")
            for code, (v, u) in (ledger or LEDGER).items()
        }
        self.representative_node_ids = {c: n.id for c, n in self.nodes.items()}
        self.edges: list = []

    def search_nodes(self, keywords, period=None):
        return [self.nodes[k] for k in keywords if k in self.nodes]


# ---- 1. 실측 문장 10/10 -------------------------------------------------------

def test_live_sentence_assigns_all_ten_codes_correctly():
    """★ 실측 문장의 숫자 10건이 전부 제 코드로 귀속된다.

    수정 이전에는 102,462→E-6-1, 2.0→E-4-2, 3,136,024→E-3-1로 새고
    ('3','배')라는 가짜 claim이 하나 더 붙어 11건이었다.
    """
    got = [(num, code) for num, _unit, code in _claims(LIVE_SENTENCE)]
    assert got == [
        ("102,462.0", "E-2-1"),
        ("2.0", "E-2-2"),
        ("396,152.0", "E-3-1"),
        ("3,136,024.0", "E-3-2"),
        ("7,929.0", "E-4-1"),
        ("10.0", "E-4-2"),
        ("55,647.0", "E-5-1"),
        ("72,463.0", "E-6-1"),
        ("92.9", "E-6-2"),
        ("1", "E-8-1"),
    ], got


# ---- 2. Scope 3 vs Scope 1+2 -------------------------------------------------

def test_scope3_is_not_scope12():
    """(나) "Scope" → E-3-1 무조건 매핑 회귀. Scope3는 E-3-2로 가야 한다."""
    s3 = "Scope3 배출량은 3,136,024.0 tCO2eq에 달한다."
    assert [c for _n, _u, c in _claims(s3)] == ["E-3-2"], _claims(s3)

    s12 = "Scope1과 Scope2를 합쳐 396,152.0 tCO2eq이다."
    assert [c for _n, _u, c in _claims(s12)] == ["E-3-1"], _claims(s12)


# ---- 3. 겹침 해소 (leftmost) --------------------------------------------------

def test_overlapping_terms_resolved_leftmost():
    """`재생에너지`(E-4-2)와 `에너지사용`(E-4-1)은 겹치되 포함관계가 아니다.

    최장 일치만 쓰면 더 뒤·더 긴 `에너지사용`이 이겨 E-4-1로 간다.
    leftmost 우선이어야 E-4-2가 나온다.
    """
    s = "재생에너지 사용 비율은 10.0%로 나타났다."
    assert [c for _n, _u, c in _claims(s)] == ["E-4-2"], _claims(s)


# ---- 4. 폴백 제거 -------------------------------------------------------------

def test_no_topic_term_returns_none():
    """(다) 문장 전체 스캔 폴백 제거 — 지표 용어가 없으면 임의 코드를 붙이지 않는다."""
    s = "매출은 100억원 증가했다."
    topic, code = next(
        (_match_topic_near(s, m.start(), m.end()) for m in _NUMBER_PATTERN.finditer(s))
    )
    assert code is None and topic is None


def test_distant_term_does_not_leak_into_claim():
    """창(25자) 밖의 용어는 귀속되지 않는다 — 폴백이 없으므로 None."""
    s = "폐기물 관리 체계를 정비하였다. " + "당사는 " * 8 + "총 1,234건을 처리하였다."
    codes = [c for _n, _u, c in _claims(s)]
    assert "E-6-1" not in codes, codes


# ---- 5. 숫자 정규식 (라) ------------------------------------------------------

def test_number_pattern_rejects_glued_digits():
    """"Scope3 배출량"의 3이 '3배' claim으로 뽑히지 않는다."""
    s = "Scope3 배출량은 3,136,024.0 tCO2eq이다."
    pairs = [(m.group("num"), m.group("unit")) for m in _NUMBER_PATTERN.finditer(s)]
    assert ("3", "배") not in pairs
    assert ("3,136,024.0", "tCO2eq") in pairs


def test_number_pattern_keeps_all_real_claims():
    """정규식 강화로 정상 추출이 줄지 않는다 — 실측 문장은 11건 → 10건.

    줄어든 1건이 정확히 ('3','배')여야 한다(가짜 claim만 사라졌다는 확인).
    """
    pairs = [(m.group("num"), m.group("unit")) for m in _NUMBER_PATTERN.finditer(LIVE_SENTENCE)]
    assert len(pairs) == 10
    assert ("3", "배") not in pairs
    # 배수 단위 자체는 살아 있어야 한다('배출'만 배제).
    assert ("3", "배") in [
        (m.group("num"), m.group("unit"))
        for m in _NUMBER_PATTERN.finditer("전년 대비 3배 증가하였다.")
    ]


def test_number_pattern_ignores_alphanumeric_tokens():
    """RE100·S2 류 토큰의 숫자를 claim으로 보지 않는다."""
    s = "RE100 가입과 IFRS S2 대응을 추진한다."
    assert list(_NUMBER_PATTERN.finditer(s)) == []


# ---- 6. 단위성 용어 배제 ------------------------------------------------------

def test_unit_like_terms_are_not_topic_evidence():
    """`TJ`가 앞에 있다고 E-4-1이 되면 안 된다 — 지표명이 근거여야 한다."""
    # 단위만 있는 문장 → 귀속 없음
    only_unit = "직전 값 TJ 기준으로 7,929.0 TJ이다."
    assert all(c is None for _n, _u, c in _claims(only_unit)), _claims(only_unit)

    # 지표명이 있으면 정상 귀속
    with_name = "총 에너지 사용량은 7,929.0 TJ이다."
    assert [c for _n, _u, c in _claims(with_name)] == ["E-4-1"]


# ---- 7. D1 통합 — 실측 문장의 D1이 0이 된다 -----------------------------------

def test_d1_zero_on_live_sentence_with_correct_ledger():
    """정답 노드 풀에서 실측 문장의 D1 = 0.0.

    라이브에서 D1=1.0이었던 게 데이터가 아니라 귀속 실패였다는 증명이다.
    """
    axis = _score_d1_numeric(LIVE_SENTENCE, _LedgerGraph())
    assert axis.score == 0.0, axis.detail
    # 세 오귀속 발화가 소멸했는가
    for gone in ("vs node=72463.0 (Δ=41.4%", "vs node=10.0 (Δ=80.0%",
                 "vs node=396152.0 (Δ=691.6%"):
        assert gone not in axis.detail, axis.detail
    assert "3.0배" not in axis.detail, axis.detail


def test_d1_still_fires_on_real_mismatch():
    """귀속이 맞아도 값이 다르면 여전히 검출한다(폴백 제거가 검출력을 죽이지 않는다)."""
    ledger = dict(LEDGER, **{"E-2-1": (50000.0, "톤")})
    axis = _score_d1_numeric(LIVE_SENTENCE, _LedgerGraph(ledger))
    assert axis.score > 0.5, axis.detail


def test_sentence_topic_codes_uses_same_index():
    """`_sentence_topic_codes`도 새 인덱스를 쓴다 — E-3-2가 후보에 들어와야 한다."""
    codes = _sentence_topic_codes(LIVE_SENTENCE)
    assert {"E-2-1", "E-2-2", "E-3-1", "E-3-2", "E-4-1", "E-4-2",
            "E-5-1", "E-6-1", "E-6-2", "E-8-1"} <= codes, sorted(codes)


# ---- top_axis / high_risk_axes — 위험 0인 문장이 '고위험 축'으로 보고되면 안 된다 ----

def test_top_axis_is_empty_when_all_axes_are_zero():
    """전 축 0 → top_axis="".

    max()는 동점에서 첫 키를 돌려주므로 깨끗한 문장이 늘 'D1_numeric'으로 찍혔고,
    L5 summary의 high_risk_axes가 그걸 최빈값으로 집계해 **위험 0인 섹션을
    '고위험 축 D1'으로 보고**했다(2026-07-27 현대모비스 E 라이브: 전 문장 D1=0인데
    high_risk_axes=['D1_numeric', 'D2_modifier']). D1을 0으로 만든 작업의 결과가
    산출물에서 안 보이던 원인이다.
    """
    from esgenie.layer3_detect import _build_risk_vector
    from esgenie.schemas import AxisScore

    zero = lambda: AxisScore(score=0.0, evidence=[], detail="")
    rv = _build_risk_vector(zero(), zero(), zero(), zero())

    assert rv.risk_score == 0.0
    assert rv.top_axis == "", f"위험 0인데 top_axis가 '{rv.top_axis}'로 찍혔다"


def test_top_axis_still_reports_the_real_axis_when_nonzero():
    """과차단 방지 — 실제 위험이 있으면 그 축을 정확히 지목해야 한다."""
    from esgenie.layer3_detect import _build_risk_vector
    from esgenie.schemas import AxisScore

    zero = lambda: AxisScore(score=0.0, evidence=[], detail="")
    hot = AxisScore(score=1.0, evidence=[], detail="모호어")
    rv = _build_risk_vector(zero(), hot, zero(), zero())
    assert rv.top_axis == "D2_modifier"


def test_high_risk_axes_excludes_clean_sentences():
    """L5 집계 — 깨끗한 문장만 있으면 high_risk_axes가 빈 목록이어야 한다."""
    from esgenie.schemas import AxisScore, RiskVector

    zero = lambda: AxisScore(score=0.0, evidence=[], detail="")
    clean = RiskVector(D1_numeric=zero(), D2_modifier=zero(),
                       D3_semantic=zero(), D5_timeseries=zero(),
                       aggregate={"risk_score": 0.0, "level": "low", "top_axis": ""})
    from collections import Counter
    counter: Counter[str] = Counter()
    for _ in range(4):
        counter[clean.top_axis] += 1
    assert [ax for ax, _ in counter.most_common(3) if ax] == []
