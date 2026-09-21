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
    g, _, _ = pipeline([extraction("a.pdf", "전력 사용량", 142560, "kWh"),
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


def test_other_site_or_target_total_does_not_displace_components():
    rows = [extraction("power.pdf", "전력 사용량", 6, "TJ"),
            extraction("gas.pdf", "도시가스 사용량", 4, "TJ"),
            extraction("other.pdf", "총 에너지 사용량", 100, "TJ", site="제2공장"),
            extraction("plan.pdf", "총 에너지 목표", 200, "TJ")]
    for values in (rows, rows[::-1]):
        assert energy(values).value == 10


def test_construction_date_is_not_annual_generation_target():
    from esgenie.ssot.boundary_conflicts import renewable_review_notes
    ext = extraction("solar.pdf", "태양광 사용 실적", 45, "MWh", "2026년 상반기")
    ext.raw_text = "태양광 패널 설치 — 2026년 하반기 착공, 연간 약 540MWh 발전 목표"
    assert any("설비 동일성" in n for n in renewable_review_notes(ext))


def test_labeled_reporting_period_is_preserved():
    b = derive_boundary("총 폐기물 위탁량", "", doc_context="배출사업장 제1공장\n대상 기간 2026-04-01 ~ 2026-04-30\n1. 폐기물 현황")
    assert (b.period_start, b.period_end) == ("2026-04-01", "2026-04-30")


def test_R27_real_multipage_clause_locator_and_unknown(tmp_path):
    import fitz
    from esgenie.ssot.ocr_router import ExtractedClause, _resolve_clause_pages
    from esgenie.supplychain.schema import Answer
    from esgenie.supplychain.render import source_lines, draft_body
    path=tmp_path/'multipage.pdf'
    with fitz.open() as doc:
        doc.new_page().insert_text((72,72),'First page introduction.')
        doc.new_page().insert_text((72,72),'Employees submit grievances through a protected hotline.')
        doc.save(path)
    ext=OcrExtraction(path.name,DocChannel.UNSTRUCTURED,'controlled_pdf',clauses=[
        ExtractedClause('process','Employees submit grievances through a protected hotline.',page=9),
        ExtractedClause('unknown','Unlocated sentence.',page=1)])
    _resolve_clause_pages(ext,str(path))
    assert [c.page for c in ext.clauses]==[1,None]
    a=Answer('q','s','q',None,'draft_ready',draft_text='내용 [N1] [N2]',draft_citations=[
        {'node_id':'N1','source_file':path.name,'page':ext.clauses[0].page},
        {'node_id':'N2','source_file':path.name,'page':ext.clauses[1].page}])
    assert source_lines(a)==['[1] multipage.pdf p.2','[2] multipage.pdf (위치 미확인)']
    assert '위치' in ' '.join(draft_body(a)[1])


# ====================================================================
# 2026-09-21 후속 재검토(ba0ea8c) R1~R4 — 검토자가 고정한 결함의 정상 동작 회귀
# ====================================================================

def separate_documents(second_value):
    """같은 측정 대상을 담은 별도 발행 문서 두 건. 내용 지문이 달라 복사본이 아니다."""
    a = extraction("a.pdf", "사용전력량", 100, "kWh")
    b = extraction("b.pdf", "사용전력량", second_value, "kWh")
    a.raw_text = "사업장: 제1공장\n문서번호: 원본 고지서 A"
    b.raw_text = "사업장: 제1공장\n문서번호: 검토용 집계 B"
    return [a, b]


@pytest.mark.parametrize("second,comparison", [(100, "scope_unconfirmed"), (200, "mismatch")])
def test_followup_R1_derived_emission_follows_source_measurement(second, comparison):
    _, points, _ = pipeline(separate_documents(second))
    single = pipeline([extraction("single.pdf", "사용전력량", 100, "kWh")])[1]
    assert points["E-4-1"].value == pytest.approx(.00036)
    assert points["E-3-1"].value == pytest.approx(single["E-3-1"].value)   # 두 번 더하지 않는다
    assert points["E-3-1"].comparison == comparison
    assert any("원측정값" in n for n in points["E-3-1"].scope_notes)
    if second != 100:
        assert points["E-3-1"].verification == "unverified"


def test_followup_R2_line_total_does_not_absorb_factory_rows():
    factory = [extraction("b_power.pdf", "총 전력 사용량", 6, "TJ", "2026년 연간", "제1공장"),
               extraction("c_gas.pdf", "도시가스 사용량", 4, "TJ", "2026년 연간", "제1공장")]
    line = extraction("a_total.pdf", "총 에너지 사용량", 3, "TJ", "2026년 연간", "제1공장 도장라인")
    for order in ([line, *factory], [*factory[::-1], line]):
        dp = energy(order)
        assert dp.value == 10                                    # 3 TJ 대체도 13 TJ 합산도 금지
        assert [e.file_name for e in dp.evidence_files] == ["b_power.pdf", "c_gas.pdf"]
        assert any("포괄 관계 미확인" in n for n in dp.scope_notes)


def test_followup_R2_site_containment_has_direction():
    from esgenie.ssot.boundary import compatible_sites, site_covers
    factory = derive_boundary("총 전력 사용량", "2026년 연간", doc_context="제1공장")
    line = derive_boundary("총 에너지 사용량", "2026년 연간", doc_context="제1공장 도장라인")
    assert compatible_sites(factory, line, inclusion=True)       # 제한적 합산 자격은 유지
    assert site_covers(factory, line) and not site_covers(line, factory)


@pytest.mark.parametrize("source,supported", [
    ("ISMS 인증을 취득했다.", True),
    ("2025년 정보보호 ISMS 인증을 취득하여 운영 중이다.", True),
    ("ISMS 인증을 취득하지 않았다.", False),
    ("ISMS 인증 미취득 상태다.", False),
    ("ISO 인증을 취득했다. ISMS 인증은 준비 중이다.", False),
    ("ISMS 인증을 준비하며 하반기 신청 예정이다.", False),
])
def test_followup_R3_certification_completion_needs_same_certificate(source, supported):
    from esgenie.rag_gates.grounding_gate import evaluate_grounding
    gate = evaluate_grounding("ISMS 인증을 취득했다. [S]", [{"id": "S", "text": source}])
    assert bool(gate.soft_flags) is not supported


@pytest.mark.parametrize("source", ["ISMS 인증을 취득하지 않았다.",
                                    "ISO 인증을 취득했다. ISMS 인증은 준비 중이다."])
def test_followup_R3_unproven_certification_is_not_draft_ready(source):
    from unittest.mock import MagicMock
    from esgenie.supplychain.drafter import _attempt_draft
    from esgenie.supplychain.schema import Answer
    answer = Answer("controlled-q", "정책", "인증 현황", None, "insufficient")
    llm = MagicMock()
    llm.complete.return_value = SimpleNamespace(content="ISMS 인증을 취득했다. [S]")
    _attempt_draft(answer, [{"id": "S", "text": source, "source_file": "policy.pdf", "page": 0}],
                   llm, max_retries=0)
    assert answer.status != "draft_ready"


def test_followup_R4_opposite_signs_are_not_zero_difference():
    g, points, _ = pipeline([extraction("a.pdf", "총 에너지 사용량", -10, "TJ", "2026년 연간", "전사"),
                             extraction("b.pdf", "총 에너지 사용량", 10, "TJ", "2026년 연간", "전사")])
    edge = next(e for e in g.edges if e.edge_type == "cross_check")
    assert (edge.comparison, edge.difference_pct) == ("mismatch", 200.0)
    assert points["E-4-1"].verification != "verified"


@pytest.mark.parametrize("a,b,expected", [(0, 0, 0.), (0, 10, 100.), (10, 0, 100.),
                                          (-10, 10, 200.), (10, -10, 200.), (10, 10, 0.),
                                          (100, 200, 100.), (200, 100, 100.)])
def test_followup_R4_difference_is_order_independent_and_signed(a, b, expected):
    from esgenie.ssot.evidence_graph import _signed_pct_diff
    assert _signed_pct_diff(a, b) == pytest.approx(expected)


# ====================================================================
# 2026-09-21 3차 검토(0fb6df2) R1-a·R1-b·R3-a·R3-b — 남은 입력 조합의 정상 동작
# ====================================================================

def certification_draft(answer_text, source):
    """원문 하나를 인용한 고정 답변을 실제 게이트에 통과시키고 상태를 돌려준다."""
    from unittest.mock import MagicMock
    from esgenie.supplychain.drafter import _attempt_draft
    from esgenie.supplychain.schema import Answer
    cited = answer_text + " [S]"
    chunks = [{"id": "S", "text": source, "source_file": "policy.pdf", "page": 0}]
    answer = Answer("controlled-cert", "정책", "인증 현황", None, "insufficient")
    llm = MagicMock()
    llm.complete.return_value = SimpleNamespace(content=cited)
    _attempt_draft(answer, chunks, llm, max_retries=0)
    return answer.status


def test_followup_R1a_equivalent_unit_duplicates_are_converted_once():
    """대표 원측정값이 환산 대상 단위가 아니어도 중복을 되돌려 더하지 않는다."""
    rows = [extraction("a.pdf", "사용전력량", 100, "kWh"),
            extraction("b.pdf", "사용전력량", 100, "kWh"),
            extraction("total.pdf", "사용전력량", .1, "MWh")]
    for i, row in enumerate(rows):
        row.raw_text = f"사업장: 제1공장\n문서번호: 문서 {i + 1}"
    single = pipeline([extraction("single.pdf", "사용전력량", 100, "kWh")])[1]["E-3-1"].value
    for order in (rows, rows[::-1]):
        dp = pipeline(order)[1]["E-3-1"]
        assert dp.value == pytest.approx(single)          # 0.096으로 되살아나지 않는다
        assert {e.node_id for e in dp.evidence_files}.isdisjoint(
            {e.node_id for e in dp.reference_files})      # 쓴 근거와 제외 근거가 겹치지 않는다
        assert dp.comparison != "mismatch"


@pytest.mark.parametrize("second,site", [(101, "제1공장"), (50, "제1공장 도장라인")])
def test_followup_R1b_sum_exclusion_is_not_a_mismatch(second, site):
    """허용 오차 안의 대조와 범위가 다른 대조는 환산 뒤에도 불일치가 되지 않는다."""
    _, points, _ = pipeline([extraction("a.pdf", "사용전력량", 100, "kWh"),
                             extraction("b.pdf", "사용전력량", second, "kWh", site=site)])
    assert points["E-3-1"].value == pytest.approx(.048)
    assert points["E-3-1"].comparison != "mismatch"
    assert points["E-3-1"].verification != "unverified"
    assert any("파생 배출량 제외" in n for n in points["E-3-1"].scope_notes)


@pytest.mark.parametrize("answer,source,supported", [
    ("ISMS-P 인증을 취득했다.", "ISMS 인증을 취득했다.", False),
    ("ISO 27001 인증을 취득했다.", "ISO 인증을 취득했다. ISO 27001은 준비 중이다.", False),
    ("ISO 9001 인증을 취득했다.", "ISO 인증을 취득했다.", False),
    ("당사는 ISMS 인증을 취득했다.", "회사는 ISMS 인증을 취득했다.", True),
    ("ISMS 인증을 취득했다.", "2025년 정보보호 ISMS 인증을 취득하여 운영 중이다.", True),
    ("ISMS 인증을 취득했다.", "ISMS 인증을 취득하지 않았다.", False),
    ("ISMS 인증을 취득했다.", "ISO 인증을 취득했다. ISMS 인증은 준비 중이다.", False),
])
def test_followup_R3a_certification_identity_is_compared_exactly(answer, source, supported):
    assert (certification_draft(answer, source) == "draft_ready") is supported


@pytest.mark.parametrize("answer,supported", [("ISMS 인증을 취득할 예정이다.", True),
                                              ("ISMS 인증을 준비 중이다.", True),
                                              ("이미 ISMS 인증을 취득했다.", False)])
def test_followup_R3b_future_plan_is_not_a_completion_claim(answer, supported):
    """계획 원문을 그대로 인용한 계획 진술은 허용하고, 완료 주장은 계속 막는다."""
    assert (certification_draft(answer, "ISMS 인증을 취득할 예정이다.") == "draft_ready") is supported


def test_R27_single_page_still_requires_located_quote(tmp_path):
    import fitz
    from esgenie.ssot.ocr_router import ExtractedClause, _resolve_clause_pages
    path=tmp_path/'one.pdf'
    with fitz.open() as doc:
        doc.new_page().insert_text((72,72),'Actual source sentence.')
        doc.save(path)
    ext=OcrExtraction(path.name,DocChannel.UNSTRUCTURED,'controlled_pdf',clauses=[
        ExtractedClause('known','Actual source sentence.',page=1),
        ExtractedClause('missing','Invented claim.',page=1)])
    _resolve_clause_pages(ext,str(path))
    assert [c.page for c in ext.clauses]==[0,None]
