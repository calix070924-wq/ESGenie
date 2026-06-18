"""공시 진단 탭 ISSB 배지 헬퍼 테스트."""
from __future__ import annotations

from types import SimpleNamespace

from esgenie.issb_gap import build_issb_gap_report
from esgenie.knowledge.kesg_items import PROFILE_LABELS, items_for_profile
import esgenie.ui.tabs as tabs
from esgenie.ui.tabs import (
    _extract_upload_recommendations,
    _issb_badge_text,
    _issb_gap_table_rows,
    _item_name_with_issb,
    _supplychain_issb_alert_rows,
    _supplychain_upload_cta_rows,
)


def test_item_name_with_issb_badge():
    labeled = _item_name_with_issb("온실가스 배출량 (Scope1 + Scope2)", "E-3-1")
    assert labeled.endswith("[ISSB S2 · 지표·목표]")


def test_empty_mapping_keeps_item_name_unchanged(monkeypatch):
    monkeypatch.setattr(tabs, "mappings_for", lambda code: [])
    assert _issb_badge_text("E-3-1") == ""
    assert _item_name_with_issb("온실가스 배출량 (Scope1 + Scope2)", "E-3-1") == "온실가스 배출량 (Scope1 + Scope2)"


def test_issb_gap_table_rows_formats_statuses():
    profile = "full"
    profile_codes = {item.code for item in items_for_profile(profile)}
    extraction = SimpleNamespace(
        profile=profile,
        profile_label=PROFILE_LABELS[profile],
        mapped={
            "E-3-1": {
                "code": "E-3-1",
                "name": "온실가스 배출량 (Scope1 + Scope2)",
                "evidence_node_ids": ["node_1"],
                "beyond_profile": "E-3-1" not in profile_codes,
            }
        },
        missing=[code for code in ("E-3-2", "E-3-3", "E-4-1", "E-4-2", "E-1-1", "G-1-1", "S-5-2") if code in profile_codes],
    )
    report = build_issb_gap_report(extraction)
    rows = _issb_gap_table_rows(report, "climate")
    row_by_code = {row["K-ESG"]: row for row in rows}

    assert row_by_code["E-3-1"]["상태"] == "공시됨"
    assert row_by_code["E-3-1"]["증빙"] == "증빙 연결"
    assert row_by_code["E-3-3"]["상태"] == "누락"


def test_supplychain_issb_alert_rows_only_include_in_profile_missing_defense_items():
    profile = "full"
    profile_codes = {item.code for item in items_for_profile(profile)}
    extraction = SimpleNamespace(
        profile=profile,
        profile_label=PROFILE_LABELS[profile],
        mapped={
            "E-3-1": {
                "code": "E-3-1",
                "name": "온실가스 배출량 (Scope1 + Scope2)",
                "evidence_node_ids": ["node_1"],
                "beyond_profile": "E-3-1" not in profile_codes,
            }
        },
        missing=["E-3-2", "E-3-3", "E-4-1", "E-4-2", "E-1-1", "G-1-1", "S-5-2"],
    )
    report = build_issb_gap_report(extraction)
    rows = _supplychain_issb_alert_rows(report)
    codes = {row["K-ESG"] for row in rows}

    assert "E-3-2" in codes
    assert "E-4-1" in codes
    assert "G-1-1" not in codes
    energy_row = next(row for row in rows if row["K-ESG"] == "E-4-1")
    assert "전기·가스 사용량 집계표" in energy_row["권장 증빙"]


def test_extract_upload_recommendations_deduplicates_flags():
    answer = SimpleNamespace(
        flags=[
            "ISSB S2 연계 누락: 에너지 사용량",
            "보완 증빙: 전기·가스 사용량 집계표 / 에너지 사용 원천 증빙(고지서·계량기록)",
            "보완 증빙: 전기·가스 사용량 집계표 / 에너지 사용 원천 증빙(고지서·계량기록)",
        ]
    )
    recs = _extract_upload_recommendations(answer)
    assert recs == ["전기·가스 사용량 집계표", "에너지 사용 원천 증빙(고지서·계량기록)"]


def test_supplychain_upload_cta_rows_include_next_action():
    answer = SimpleNamespace(
        status="flagged",
        question_text="(수치) 연간 에너지 사용량",
        flags=[
            "보완 증빙: 전기·가스 사용량 집계표 / 에너지 사용 원천 증빙(고지서·계량기록)",
        ],
    )
    sheet = SimpleNamespace(answers=[answer])
    rows = _supplychain_upload_cta_rows(sheet, uploaded_names=["kepco_bill.pdf"])
    assert rows[0]["현재 업로드"] == 1
    assert "전기·가스 사용량 집계표" in rows[0]["권장 증빙"]
    assert "다시 분석" in rows[0]["다음 행동"]
