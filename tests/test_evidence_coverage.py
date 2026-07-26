"""Phase 2 — 증빙 연결 커버리지 + 단위 타당성 플래그 (2026-07-17).

핵심 보장:
- evidence_coverage_pct는 값 존재(coverage_pct)와 분리 — 합성값·설문 항목은 분자 제외
- 무상태 계산이라 survey 주입 이후에도 낡지 않음
- unit_suspect는 표기 요동('ton CO2eq'↔'tCO2eq')은 통과, 오결합('명'↔'TJ')만 플래그
- L6 표지에 두 커버리지가 병기됨
"""
from __future__ import annotations

from types import SimpleNamespace

from esgenie.layer1_extract import _unit_suspect, evidence_coverage_pct


def _ext(mapped, missing=(), beyond=()):
    return SimpleNamespace(
        mapped=mapped, missing=list(missing), beyond_profile=list(beyond),
    )


def _entry(area="E", evidence=()):
    return {"area": area, "evidence_node_ids": list(evidence)}


# ---------------------------------------------------------------------------
# evidence_coverage_pct
# ---------------------------------------------------------------------------

def test_evidence_coverage_counts_only_linked_items():
    ext = _ext(
        mapped={
            "E-3-1": _entry(evidence=["n1"]),          # 증빙 연결 → 분자
            "E-4-1": _entry(),                          # 값만 존재 → 제외
            "E-5-1": _entry(evidence=["survey_E-5-1"]),  # 설문 → 제외
        },
        missing=["E-6-1"],  # 분모 포함
    )
    # 분자 1 / 분모 4(mapped 3 + missing 1)
    assert evidence_coverage_pct(ext) == 25.0


def test_evidence_coverage_excludes_beyond_profile():
    ext = _ext(
        mapped={"E-3-1": _entry(evidence=["n1"]), "E-9-1": _entry(evidence=["n2"])},
        beyond=["E-9-1"],  # 프로파일 외 — 분자·분모 모두 제외
    )
    assert evidence_coverage_pct(ext) == 100.0


def test_evidence_coverage_fresh_after_survey_injection():
    """_apply_survey_answers처럼 mapped/missing이 사후 변형돼도 즉시 반영 (무상태)."""
    ext = _ext(mapped={"E-3-1": _entry(evidence=["n1"])}, missing=["E-4-1"])
    assert evidence_coverage_pct(ext) == 50.0
    # 설문 주입 시뮬레이션: missing → mapped(survey)
    ext.mapped["E-4-1"] = _entry(evidence=["survey_E-4-1"])
    ext.missing.remove("E-4-1")
    assert evidence_coverage_pct(ext) == 50.0  # 값 커버리지는 올라도 증빙 커버리지는 불변


def test_evidence_coverage_empty_denominator():
    assert evidence_coverage_pct(_ext(mapped={})) == 0.0


# ---------------------------------------------------------------------------
# _unit_suspect — 오결합만 플래그, 표기 요동은 통과
# ---------------------------------------------------------------------------

def test_unit_suspect_flags_obvious_mismatch():
    assert _unit_suspect("명", "TJ")            # 신한 E-4-1 실사례
    assert _unit_suspect("십억 원", "ton")       # 신한 E-6-1 실사례


def test_unit_suspect_passes_notation_variants():
    assert not _unit_suspect("ton CO2eq", "tCO2eq")  # 신한 E-3-1 — 표기 요동
    assert not _unit_suspect("톤CO2eq", "tCO2eq")
    assert not _unit_suspect("%", "%")


def test_unit_suspect_passes_convertible_units():
    assert not _unit_suspect("MWh", "kWh")  # 환산 그룹 호환


def test_unit_suspect_conservative_on_missing_units():
    assert not _unit_suspect("", "TJ")
    assert not _unit_suspect(None, "TJ")
    assert not _unit_suspect("명", "")  # 항목 정의에 단위 없음 → 판단 보류


# ---------------------------------------------------------------------------
# L6 병기
# ---------------------------------------------------------------------------

def test_report_cover_shows_both_coverages():
    from esgenie.layer6_report import _block_cover

    ext = _ext(mapped={"E-3-1": _entry(evidence=["n1"])}, missing=["E-4-1"])
    ext.coverage_pct = 50.0
    ext.profile_label = "전체 (61항목)"
    output = SimpleNamespace(
        extraction=ext,
        report=SimpleNamespace(corp_name="테스트", industry="전자", report_year=2025),
        sections={},
        disclosure=None,
        issb_gap=None,
        industry_module_key=None,
    )
    body = _block_cover(output).body_md
    assert "K-ESG 커버리지 | 50.0%" in body
    assert "증빙 연결 커버리지 | 50.0%" in body
