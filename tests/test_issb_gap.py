"""ISSB 갭 리포트 단위 테스트."""
from __future__ import annotations

from types import SimpleNamespace

from esgenie.issb_gap import build_issb_gap_report, remediation_text_for, rows_for_anchor
from esgenie.knowledge.kesg_items import PROFILE_LABELS, items_for_profile


def _extraction(profile: str, mapped_defs: dict[str, list[str] | None]):
    profile_codes = {item.code for item in items_for_profile(profile)}
    mapped = {
        code: {
            "code": code,
            "name": code,
            "evidence_node_ids": evidence_ids or [],
            "beyond_profile": code not in profile_codes,
        }
        for code, evidence_ids in mapped_defs.items()
    }
    issb_profile_codes = {"E-3-1", "E-3-2", "E-3-3", "E-4-1", "E-4-2", "E-1-1", "G-1-1", "S-5-2"} & profile_codes
    missing = sorted(code for code in issb_profile_codes if code not in mapped)
    return SimpleNamespace(
        profile=profile,
        profile_label=PROFILE_LABELS[profile],
        mapped=mapped,
        missing=missing,
    )


def test_full_profile_gap_report_summarizes_disclosed_and_missing():
    extraction = _extraction(
        "full",
        {
            "E-3-1": ["node_1"],
            "E-4-1": [],
            "G-1-1": [],
        },
    )
    report = build_issb_gap_report(extraction)

    assert report.in_profile_total == 8
    assert report.in_profile_disclosed == 3
    assert report.in_profile_missing == 5
    assert report.verified_count == 1
    assert report.self_reported_count == 2
    assert "프로파일 내 ISSB 연계 3/8개 공시" in report.rationale


def test_sme_profile_marks_scope3_and_human_rights_as_out_of_scope():
    extraction = _extraction(
        "sme",
        {
            "E-3-1": ["node_1"],
            "E-4-1": [],
            "E-4-2": [],
            "E-1-1": [],
            "G-1-1": [],
        },
    )
    report = build_issb_gap_report(extraction)
    row_by_code = {row.kesg_code: row for row in report.rows}

    assert row_by_code["E-3-2"].status == "out_of_scope"
    assert row_by_code["S-5-2"].status == "out_of_scope"
    assert row_by_code["E-3-3"].status == "missing"
    assert report.out_of_scope_total >= 2


def test_disclosed_beyond_profile_is_counted_as_reference_disclosure():
    extraction = _extraction(
        "sme",
        {
            "E-3-1": ["node_1"],
            "E-3-2": ["node_2"],
            "E-4-1": [],
            "E-4-2": [],
            "E-1-1": [],
            "G-1-1": [],
        },
    )
    report = build_issb_gap_report(extraction)
    row_by_code = {row.kesg_code: row for row in report.rows}

    assert row_by_code["E-3-2"].scope == "beyond_profile"
    assert row_by_code["E-3-2"].status == "disclosed"
    assert report.beyond_profile_disclosed >= 1


def test_rows_for_anchor_filters_climate_rows():
    extraction = _extraction("full", {"E-3-1": ["node_1"]})
    report = build_issb_gap_report(extraction)

    climate_codes = {row.kesg_code for row in rows_for_anchor(report, "climate")}
    assert {"E-3-1", "E-3-3", "E-4-1", "E-4-2"}.issubset(climate_codes)


def test_greenwash_anchor_summary_counts_scope3():
    extraction = _extraction("full", {})
    report = build_issb_gap_report(extraction)
    summaries = {summary.anchor: summary for summary in report.anchor_summary}

    assert summaries["greenwash_defense"].missing == 1


def test_remediation_text_for_energy_contains_expected_evidence():
    text = remediation_text_for("E-4-1")
    assert "전기·가스 사용량 집계표" in text
    assert "고지서" in text
