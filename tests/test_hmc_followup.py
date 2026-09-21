"""후속 실패 회귀: 합성 OCR 산출물부터 확정 원장/응답까지 실행한다."""
from types import SimpleNamespace

import pytest

from esgenie.ssot.boundary import derive_boundary, comparable
from esgenie.ssot.ocr_router import OcrExtraction, ExtractedMetric, DocChannel
from esgenie.ssot.evidence_graph import build_unified_graph
from esgenie.ssot.ssot_pipeline import extract_with_ssot
from esgenie.ssot.audit_trace import build_data_points
from esgenie.ssot.detector_5axis import detect_d1_numeric, cross_check_status
from esgenie.supplychain.responder import build_response_sheet
from esgenie.supplychain.claims import SupplierClaim
from esgenie.supplychain.checklist import build_checklist


def extraction(name, hint, value, unit, period="2026-05", site="제1공장", code="E-4-1", **kw):
    return OcrExtraction(name, DocChannel.STRUCTURED, "controlled_fixture",
        metrics=[ExtractedMetric(hint, value, unit, period, code, page=0, confidence=.95)],
        raw_text=site, **kw)


def pipeline(inputs, claims=None):
    g = build_unified_graph(None, inputs, corp_code="TEST", corp_name="합성 대조군", report_year=2026)
    report = SimpleNamespace(source="ssot_local", corp_code="TEST", corp_name="합성 대조군",
        report_year=2026, fiscal_year=2026, kesg_data={}, sections={}, raw_text="")
    result = extract_with_ssot(report, g, profile="sme")
    codes = [c for c in ("E-4-1", "E-4-2", "E-3-1", "E-6-2") if g.resolved_facts.get(c)]
    scores, evaluations = {}, {}
    for code in codes:
        fact = g.resolved_facts[code]
        name = result.mapped[code]["name"]
        axis = detect_d1_numeric(f"{name} {fact.value}{fact.unit}", code, g)
        scores[code], evaluations[code] = axis.score, axis.evaluation
    points = build_data_points(g, scores, target_codes=codes, d1_evaluations=evaluations)
    sheet = build_response_sheet("hmc", corp_name="합성 대조군", extraction=result,
                                 data_points=points, supplier_claims=claims)
    return g, {p.kesg_code: p for p in points}, sheet


def energy(rows):
    return pipeline(rows)[1]["E-4-1"]


def test_R01_R02_total_contains_components_stable():
    rows = [extraction("total.pdf", "총 에너지 사용량", 10, "TJ", "2026년 연간", "전사"),
            extraction("power.pdf", "총 전력 사용량", 6, "TJ", "2026년 연간", "전사"),
            extraction("gas.pdf", "도시가스 사용량", 4, "TJ", "2026년 연간", "전사")]
    for order in (rows, rows[::-1], rows + [rows[1]]):
        dp = energy(order)
        assert dp.value == 10
        assert [e.file_name for e in dp.evidence_files] == ["total.pdf"]


def test_R03_same_usage_period_limited_sum():
    dp = energy([extraction("power.pdf", "사용전력량", 142560, "kWh", "2026-04-25~2026-05-24"),
                 extraction("gas.pdf", "도시가스 사용열량", 360772, "MJ", "2026.04.25 ~ 05.24", "제1공장 도장·건조라인")])
    assert dp.value == pytest.approx(.873988)
    assert dp.verification != "verified"
    assert len(dp.evidence_files) == 2
    assert "2026-04-25" in dp.boundary_label


@pytest.mark.parametrize("p1,p2", [("2026-05", "2026-06"), ("2025년 연간", "2026년 연간")])
def test_R04_R05_different_periods_do_not_sum(p1, p2):
    dp = energy([extraction("power.pdf", "총 전력 사용량", 6, "TJ", p1),
                 extraction("gas.pdf", "도시가스 사용량", 4, "TJ", p2)])
    assert dp.value in (6, 4)
    assert any("기간" in n for n in dp.scope_notes)


def test_R06_different_months_no_mismatch():
    g, _, _ = pipeline([extraction("a.pdf", "사용전력량", 100, "kWh"),
                         extraction("b.pdf", "사용전력량", 200, "kWh", "2026-06")])
    assert not [e for e in g.edges if e.edge_type == "cross_check"]
    assert cross_check_status("E-4-1", g)[0] != "mismatch"


def test_R07_period_normalization():
    a = derive_boundary("사용전력량", "2026-05", doc_context="제1공장")
    b = derive_boundary("사용전력량", "2026년 5월", doc_context="제1공장")
    assert comparable(a, b)[0] == "compared"
    annual = derive_boundary("총 에너지 사용량", "2026-01-01~2026-12-31", doc_context="전사")
    assert annual.aggregation == "annual"
    assert annual.period_start == "2026-01-01"


@pytest.mark.parametrize("period,site", [("2026", "제1공장"), ("2026-05", "")])
def test_R08_unknown_axes_do_not_sum(period, site):
    dp = energy([extraction("a.pdf", "전력 사용량", 6, "TJ", period, site),
                 extraction("b.pdf", "도시가스 사용량", 4, "TJ", period, site)])
    assert dp.value in (6, 4)
    assert dp.verification != "verified"


def test_R10_independent_same_origin_mismatch_and_checklist():
    g, points, sheet = pipeline([extraction("a.pdf", "사용전력량", 100, "kWh"),
                                 extraction("b.pdf", "사용전력량", 200, "kWh")])
    assert cross_check_status("E-4-1", g)[0] == "mismatch"
    assert points["E-4-1"].comparison == "mismatch"
    assert "HMC-C-8-E-4-1" in {c.qid for c in build_checklist(sheet)}


def test_R11_units_equivalent():
    g, _, _ = pipeline([extraction("a.pdf", "使用 전력 사용량", 142560, "kWh"),
                         extraction("b.pdf", "전력 사용량", 142.56, "MWh")])
    assert cross_check_status("E-4-1", g)[0] == "compared"


@pytest.mark.parametrize("period", ["2026", "2026년 연간"])
def test_R14_R15_incomplete_ratio_not_verified(period):
    _, points, sheet = pipeline([extraction("ratio.pdf", "재생에너지 비율", 10.6, "%", period, "", "E-4-2")])
    assert points["E-4-2"].verification != "verified"
    assert points["E-4-2"].completeness != "total"
    assert any("분모" in n for n in points["E-4-2"].scope_notes)
    assert "HMC-C-8-E-4-2" in {c.qid for c in build_checklist(sheet)}


def test_R16_confirmed_total_ratio_verified():
    _, points, _ = pipeline([extraction("ratio.pdf", "총 에너지 대비 전체 재생에너지 사용 비율 실적", 10.6, "%", "2026년 연간", "전사", "E-4-2")])
    assert points["E-4-2"].verification == "verified"
    assert points["E-4-2"].completeness == "total"


def test_R17_different_denominators_not_equal():
    a = derive_boundary("총 에너지 대비 재생에너지 비율", "2026년 연간", unit="%", doc_context="전사")
    b = derive_boundary("총 전력 대비 재생에너지 비율", "2026년 연간", unit="%", doc_context="전사")
    assert comparable(a, b)[0] == "not_comparable"


def test_R18_row_measure_not_overwritten():
    b = derive_boundary("총 전력 사용량", "2026-05", doc_context="도시가스 사용량 500MJ 전사")
    assert b.measure_kind == "electricity_total"


def test_R19_R21_partial_claim_scope_precedes_difference():
    _, points, sheet = pipeline([extraction("ratio.pdf", "재생에너지 비율", 10.6, "%", "2026년 상반기", "전사", "E-4-2")],
        claims={"E-4-2": SupplierClaim(code="E-4-2", value=92, unit="%", raw="재생에너지 비율 92%", source="survey")})
    a = next(a for a in sheet.answers if a.qid == "HMC-C-8-E-4-2")
    assert a.comparison == "scope_unconfirmed"
    assert not any("D1 불일치" in f for f in a.flags)
    assert any(c.qid == a.qid and "범위" in c.action for c in build_checklist(sheet))


def test_R09_duplicate_content_does_not_add_independent_checks_or_emissions():
    a = extraction("a.pdf", "사용전력량", 142560, "kWh")
    b = extraction("copy.pdf", "사용전력량", 142560, "kWh")
    gas = extraction("gas.pdf", "도시가스 사용열량", 360772, "MJ")
    g, points, _ = pipeline([a, b, gas])
    assert points["E-4-1"].value == pytest.approx(.873988)
    assert points["E-3-1"].value == pytest.approx(88.397)
    assert not [e for e in g.edges if e.edge_type == "cross_check"]


@pytest.mark.parametrize("claim", [0, 80])
def test_R12_verified_same_scope_bidirectional(claim):
    _, _, sheet = pipeline([extraction("ratio.pdf", "총 에너지 대비 전체 재생에너지 비율", 31, "%", "2026년 연간", "전사", "E-4-2")],
        claims={"E-4-2": SupplierClaim("E-4-2", claim, "%")})
    a = next(a for a in sheet.answers if a.qid == "HMC-C-8-E-4-2")
    assert a.comparison == "mismatch" and a.status == "flagged"


def test_R20_upstream_mismatch_survives_matching_claim():
    _, points, sheet = pipeline([extraction("a.pdf", "사용전력량", 100, "kWh"),
                                  extraction("b.pdf", "사용전력량", 200, "kWh")])
    from esgenie.supplychain.mapping import _reconcile_claim
    a = next(a for a in sheet.answers if a.qid == "HMC-C-8-E-4-1")
    before = a.comparison_reason
    _reconcile_claim(a, SupplierClaim("E-4-1", a.value, "TJ"), a.value, "TJ", code="E-4-1")
    assert a.comparison == "mismatch" and before in a.comparison_reason
    assert any(c["comparison"] == "scope_unconfirmed" for c in a.comparisons)


@pytest.mark.parametrize("different_facility", [False, True])
def test_R23_facility_conflict_and_explicit_distinct_facilities(different_facility):
    from esgenie.ssot.ocr_router import ExtractedMetric
    e = extraction("facility.pdf", "재생에너지 비율", 10.6, "%", "2026년 상반기", "전사", "E-4-2")
    e.metrics.append(ExtractedMetric("태양광 설비 A 사용량 실적", 45, "MWh", "2026년 상반기", page=0))
    e.raw_text = "사업장: 전사\n태양광 설비 " + ("B" if different_facility else "A") + " 2026년 하반기 착공 예정"
    _, points, sheet = pipeline([e])
    notes = " ".join(points["E-4-2"].scope_notes)
    assert ("설비 동일성" in notes) is not different_facility
    if not different_facility:
        assert any("설비 동일성" in c.request for c in build_checklist(sheet))


def test_R24_scope12_estimated_with_period_and_sources():
    _, points, _ = pipeline([extraction("power.pdf", "사용전력량", 142560, "kWh"),
                              extraction("gas.pdf", "도시가스 사용열량", 360772, "MJ")])
    p = points["E-3-1"]
    assert p.value == pytest.approx(88.397)
    assert p.verification == "estimated" and "derived" in p.confidence_flags
    assert len(p.evidence_files) == 2 and p.boundary["period_start"] == "2026-05-01"


def test_R26_grounding_preparation_does_not_prove_certification():
    from esgenie.rag_gates.grounding_gate import evaluate_grounding
    chunks = [{"id": "SEC_1", "text": "ISMS 인증을 준비하며 2026년 하반기 신청 예정이다."}]
    wrong = evaluate_grounding("ISMS 인증을 취득하여 운영한다. [SEC_1]", chunks)
    assert wrong.soft_flags
    right = evaluate_grounding("ISMS 인증을 준비하며 2026년 하반기 신청 예정이다. [SEC_1]", chunks)
    assert not right.soft_flags and right.decision == "ACCEPT"


def test_R29_empty_flagged_is_pending_not_auto():
    from esgenie.supplychain.schema import Answer, ResponseSheet
    sheet = ResponseSheet("x", "x", "x", [Answer("x", "E", "x", None, "flagged")])
    assert not sheet.answers[0].answered
    assert sheet.auto_pct == 0 and sheet.pending_pct == 100 and sheet.flagged_count == 1
