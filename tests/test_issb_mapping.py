"""ISSB 매핑 데이터 레이어 단위 테스트."""
from __future__ import annotations

from esgenie.knowledge import kesg_items
from esgenie.knowledge.issb_mapping import (
    MAPPINGS,
    PILLAR_LABELS,
    by_anchor,
    has_issb,
    mappings_for,
    pillars_for,
)


def test_all_mapping_codes_exist_in_kesg_items():
    for mapping in MAPPINGS:
        assert kesg_items.by_code(mapping.kesg_code) is not None


def test_unmapped_code_returns_empty_results():
    code = "P-1-1"
    assert mappings_for(code) == []
    assert pillars_for(code) == []
    assert has_issb(code) is False


def test_climate_anchor_contains_key_codes():
    climate_codes = {mapping.kesg_code for mapping in by_anchor("climate")}
    assert {"E-3-1", "E-4-1", "E-4-2"}.issubset(climate_codes)


def test_greenwash_defense_anchor_contains_scope3():
    scoped = by_anchor("greenwash_defense")
    assert any(mapping.kesg_code == "E-3-2" for mapping in scoped)


def test_kssb_matches_standard():
    expected = {"S1": "KSSB1", "S2": "KSSB2"}
    for mapping in MAPPINGS:
        assert mapping.kssb == expected[mapping.standard]


def test_paragraph_may_be_empty():
    assert any(mapping.paragraph == "" for mapping in MAPPINGS)
    for mapping in MAPPINGS:
        assert isinstance(mapping.paragraph, str)


def test_pillars_for_returns_unique_pillars():
    assert pillars_for("E-3-1") == ["MetricsTargets"]


def test_pillar_labels_are_defined():
    assert PILLAR_LABELS["Governance"] == "거버넌스"
    assert PILLAR_LABELS["MetricsTargets"] == "지표·목표"
