"""D1 목표/전망 문맥 가드 (2026-07-17).

배치 오탐 실사례: LG화학 E "2030년/2050년까지 재생에너지 100% 전환을 목표로" —
목표 수치 100을 실적 노드 72와 비교해 Δ=38.9% → D1 만점 → E 밴드 MEDIUM→HIGH.
가드 후: 목표/전망 문맥의 수치는 실적 비교에서 제외, 실적 서술은 여전히 검사.
"""
from __future__ import annotations

from types import SimpleNamespace

from esgenie.layer3_detect import _is_target_context, _score_d1_numeric


class _FakeGraph:
    """search_nodes가 항상 실적 노드(72%, 2025)를 돌려주는 최소 그래프."""

    def __init__(self, value=72.0, unit="%"):
        self._node = SimpleNamespace(
            id="n_e42", value=value, unit=unit, period=2025)

    def search_nodes(self, keywords, period=None):
        return [self._node]


# ---------------------------------------------------------------------------
# _is_target_context
# ---------------------------------------------------------------------------

def test_target_context_detects_goal_markers():
    s = "국내 사업장은 2050년까지 재생에너지 100% 전환을 목표로 설정하였다."
    i = s.index("100")
    assert _is_target_context(s, i, i + 3)


def test_target_context_ignores_plain_performance():
    s = "당해 연도 재생에너지 사용 비율은 100%로 집계되었다."
    i = s.index("100")
    assert not _is_target_context(s, i, i + 3)


def test_target_context_window_is_local():
    # 문장 앞머리의 '계획'이 멀리 있는 수치까지 오염시키지 않는다 (창 40자)
    s = "중장기 계획을 별도 공시하였다. " + "당사의 " * 12 + "재생에너지 사용 비율은 100%였다."
    i = s.rindex("100")
    assert not _is_target_context(s, i, i + 3)


# ---------------------------------------------------------------------------
# _score_d1_numeric 통합 — LG화학 실사례 재현
# ---------------------------------------------------------------------------

def test_d1_skips_target_numbers():
    s = "국내 사업장은 2050년까지 재생에너지 사용 비율 100% 전환을 목표로 설정하였다."
    axis = _score_d1_numeric(s, _FakeGraph())
    assert axis.score == 0.0
    assert "목표/전망 문맥" in axis.detail


def test_d1_still_fires_on_performance_mismatch():
    s = "당해 연도 재생에너지 사용 비율은 100%로 집계되었다."
    axis = _score_d1_numeric(s, _FakeGraph(value=72.0))
    assert axis.score > 0.5  # 실적 주장 100 vs 노드 72 — 여전히 검출
