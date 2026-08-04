"""실측 보고서 표현의 D1 토픽 귀속 회귀 (2026-07-29)."""
from __future__ import annotations

from dataclasses import replace

import pytest

from esgenie.knowledge import kesg_items
from esgenie.knowledge.kesg_items import resolve_kesg_code
from esgenie.layer3_detect import _NUMBER_PATTERN, _build_topic_terms, _match_topic_near
from esgenie.rag_gates.retrieval_gate import _area_terms
from scripts import replay_search_terms


EXPANDED_TERMS = {
    "폐기물 발생량": "E-6-1",
    "여성 직원 비율": "S-3-1",
    "이사 출석률": "G-2-1",
    "이사 평균 참석률": "G-2-1",
}


def _assigned_codes(sentence: str) -> list[str | None]:
    """문장의 숫자별 D1 귀속 코드를 반환한다."""
    return [
        _match_topic_near(sentence, match.start(), match.end())[1]
        for match in _NUMBER_PATTERN.finditer(sentence)
    ]


@pytest.mark.parametrize(
    "sentence,expected",
    [
        ("폐기물 발생량은 72,463톤이다.", "E-6-1"),
        ("여성 직원 비율은 24.2%이다.", "S-3-1"),
        ("이사 출석률은 97.5%이다.", "G-2-1"),
        ("이사 평균 참석률은 97.5%이다.", "G-2-1"),
    ],
)
def test_measured_report_terms_recover_d1_assignment(sentence, expected):
    """실측에서 None이던 네 표현이 기대 K-ESG 코드로 귀속된다."""
    assert _assigned_codes(sentence) == [expected]


def test_inside_director_attendance_remains_g22():
    """짧은 `이사 출석률` 추가가 더 구체적인 G-2-2 표현을 삼키지 않는다."""
    assert _assigned_codes("사내이사 출석률은 95%이다.") == ["G-2-2"]


def test_search_terms_remain_disjoint_between_esg_areas():
    """retrieval_gate가 실제 소비하는 seed+search_terms 어휘는 영역별로 겹치지 않는다."""
    terms_by_area = {
        area: {term.lower() for term in _area_terms()[area]}
        for area in "ESG"
    }
    assert terms_by_area["E"].isdisjoint(terms_by_area["S"])
    assert terms_by_area["E"].isdisjoint(terms_by_area["G"])
    assert terms_by_area["S"].isdisjoint(terms_by_area["G"])


def test_topic_term_index_does_not_lose_terms(monkeypatch):
    """신규 네 용어를 추가해도 그 전 토픽 인덱스의 모든 항목이 보존된다."""
    expanded_index = set(_build_topic_terms())
    baseline_items = tuple(
        replace(
            item,
            search_terms=tuple(
                term for term in item.search_terms if term not in EXPANDED_TERMS
            ),
        )
        for item in kesg_items.ALL_ITEMS
    )
    with monkeypatch.context() as patch:
        patch.setattr(kesg_items, "ALL_ITEMS", baseline_items)
        baseline_index = set(_build_topic_terms())

    assert baseline_index <= expanded_index
    assert len(expanded_index - baseline_index) == len(EXPANDED_TERMS)


def test_replay_snapshot_freezes_alias_input(monkeypatch):
    """artifact가 바뀌어도 비교 snapshot은 기준선 라벨만 재평가한다."""
    monkeypatch.setattr(
        replay_search_terms,
        "_collect_dump_labels",
        lambda: ({"비교 후 새 artifact 라벨"}, []),
    )

    result = replay_search_terms.snapshot(
        audit_sentences=[], alias_labels={"폐기물 발생량"}
    )

    assert set(result["aliases"]) == {"폐기물 발생량"}
    assert result["artifact_labels"] == ["비교 후 새 artifact 라벨"]


@pytest.mark.parametrize("label,expected", EXPANDED_TERMS.items())
def test_expanded_aliases_resolve_exactly(label, expected):
    """OCR backfill alias 확대는 의도한 코드의 exact 변화로 고정한다."""
    assert resolve_kesg_code(label) == (expected, 1.0, "exact")
