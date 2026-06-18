"""공시 진단 탭 ISSB 배지 헬퍼 테스트."""
from __future__ import annotations

from esgenie.ui.tabs import _issb_badge_text, _item_name_with_issb


def test_item_name_with_issb_badge():
    labeled = _item_name_with_issb("온실가스 배출량 (Scope1 + Scope2)", "E-3-1")
    assert labeled.endswith("[ISSB S2 · 지표·목표]")


def test_empty_mapping_keeps_item_name_unchanged(monkeypatch):
    monkeypatch.setattr("esgenie.ui.tabs.mappings_for", lambda code: [])
    assert _issb_badge_text("E-3-1") == ""
    assert _item_name_with_issb("온실가스 배출량 (Scope1 + Scope2)", "E-3-1") == "온실가스 배출량 (Scope1 + Scope2)"
