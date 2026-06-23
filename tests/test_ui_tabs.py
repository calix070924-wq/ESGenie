"""공시 진단 탭 ISSB 배지 헬퍼 테스트."""
from __future__ import annotations

from types import SimpleNamespace

from esgenie.issb_gap import build_issb_gap_report
from esgenie.knowledge.kesg_items import PROFILE_LABELS, items_for_profile
import esgenie.ui.tabs as tabs
from esgenie.ui.tabs import (
    _esg_coverage_rows,
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


def test_esg_coverage_rows_before_analysis_shows_breadth_only():
    rows = _esg_coverage_rows(None, "sme")
    by_area = {row["area"]: row for row in rows}

    assert set(by_area) == {"E", "S", "G"}
    # sme(28) 영역별 추적 항목 수: E 10, S 10, G 7
    assert by_area["E"]["total"] == 10
    assert by_area["S"]["total"] == 10
    assert by_area["G"]["total"] == 7
    # 분석 전이므로 covered=0, analyzed=False, has_extraction=False
    assert all(row["covered"] == 0 for row in rows)
    assert all(row["analyzed"] is False for row in rows)
    assert all(row["has_extraction"] is False for row in rows)


def test_esg_coverage_rows_full_profile_area_totals():
    rows = _esg_coverage_rows(None, "full")
    by_area = {row["area"]: row for row in rows}
    # full(61): E 17, S 22, G 17 (+ P 5는 스트립 제외)
    assert by_area["E"]["total"] == 17
    assert by_area["S"]["total"] == 22
    assert by_area["G"]["total"] == 17


def test_esg_coverage_rows_after_analysis_counts_mapped_and_active():
    profile = "sme"
    profile_codes = {item.code for item in items_for_profile(profile)}
    mapped = {
        code: {"code": code, "beyond_profile": False}
        for code in ("E-1-1", "E-3-1", "S-4-2")
        if code in profile_codes
    }
    # beyond_profile 항목은 covered에서 제외돼야 함
    mapped["E-9-9"] = {"code": "E-9-9", "beyond_profile": True}
    extraction = SimpleNamespace(profile=profile, mapped=mapped, missing=[])
    result = SimpleNamespace(extraction=extraction, sections={"E": object()})

    rows = _esg_coverage_rows(result, profile)
    by_area = {row["area"]: row for row in rows}

    assert by_area["E"]["covered"] == 2  # E-1-1, E-3-1 (E-9-9 제외)
    assert by_area["S"]["covered"] == 1  # S-4-2
    assert by_area["G"]["covered"] == 0
    assert all(row["has_extraction"] is True for row in rows)
    assert by_area["E"]["analyzed"] is True
    assert by_area["S"]["analyzed"] is False


def test_esg_coverage_rows_unknown_profile_returns_empty():
    assert _esg_coverage_rows(None, "nope") == []


def test_supplychain_upload_cta_rows_cover_checklist_statuses():
    # flagged: ISSB 보완 권장이 '올릴 문서'에 반영되고 할 일=검토·보완
    flagged = SimpleNamespace(
        qid="SAQ-E-NUM-ENERGY", section="Environment",
        status="flagged", question_text="(수치) 연간 에너지 사용량",
        rationale="ISSB 누락", evidence_needed=[],
        flags=["보완 증빙: 전기·가스 사용량 집계표 / 에너지 사용 원천 증빙(고지서·계량기록)"],
    )
    # insufficient: derive가 채운 evidence_needed가 '올릴 문서'로 노출
    insufficient = SimpleNamespace(
        qid="SAQ-L-3", section="Labor & Human Rights",
        status="insufficient", question_text="인권 정책 또는 인권 실사 체계를 운영합니까?",
        rationale="인권정책서를 올려주세요.", evidence_needed=["인권정책서·인권헌장"],
        flags=[],
    )
    # hitl_required: 할 일=담당자 작성
    hitl = SimpleNamespace(
        qid="SAQ-B-1", section="Business Ethics",
        status="hitl_required", question_text="윤리규범/준법경영 체계를 운영합니까?",
        rationale="담당자가 직접 서술", evidence_needed=["윤리규범·행동강령"],
        flags=[],
    )
    sheet = SimpleNamespace(answers=[flagged, insufficient, hitl])
    rows = _supplychain_upload_cta_rows(sheet)
    by_q = {r["문항"]: r for r in rows}

    assert "전기·가스 사용량 집계표" in by_q["(수치) 연간 에너지 사용량"]["올릴 문서 / 작성 사항"]
    assert by_q["(수치) 연간 에너지 사용량"]["할 일"] == "검토·보완"
    assert "인권정책서" in by_q["인권 정책 또는 인권 실사 체계를 운영합니까?"]["올릴 문서 / 작성 사항"]
    assert by_q["윤리규범/준법경영 체계를 운영합니까?"]["할 일"] == "담당자 작성"
