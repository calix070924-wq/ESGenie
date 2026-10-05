"""최신 main(bba2206) 기준 남은 수치·범위 결함과 최종 출력 계약의 회귀 테스트(2026-10-05).

`docs/작업지시서_최신main_수치범위_최종출력검증_2026-10-05.md` §3~5. 기대값은 원문 기준으로 적었다 —
검사 대상 함수의 반환값을 정답으로 쓰지 않는다. 문서 번호·회사명·정답 수치 분기는 제품 코드에 없다.

§5 C1 규격 개정 연도(`GRI 403: 2018`·전각 콜론)는 기간이 아니다 — 제목·서술·열 머리 모두.
§5 C2 `FY2026`·`FY26`·`Q1`은 구간이 정의될 때만 날짜다. 정의가 없으면 '표기 없음'과 다른 보류 사유.
§5 C3 규격 식별자가 붙은 실적 제목은 자기 사업장·기간을 쓰는 새 절이다. 목차·단순 인용은 범위를 주지 않는다.
§3   08 내부 재투입률의 E-2-2 연결, 표 칸 라벨(대상↔참석, 합계 열 머리), 값이 빈 행의 부정 서술 0, 05 회사 답변.
§4   SOURCE_ONLY 0은 요청 범위 실적으로 비교·합산·확정되지 않는다 — 원장 → 답변 → LLM 입력·본문 → Excel·PDF.
모든 문구는 합성 입력이다. 외부 호출은 막는다.
"""
from __future__ import annotations

import json
import socket
from types import SimpleNamespace

import pytest

from esgenie.config import SETTINGS
from esgenie.llm import LLMResponse
from esgenie.source_review import build_source_review
from esgenie.ssot import ocr_router as router
from esgenie.ssot import selection
from esgenie.ssot.boundary import Boundary
from esgenie.ssot.evidence_graph import EvidenceGraph, EvidenceNode, merge_ocr_extraction


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("network access is not allowed in this test")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


KIMHAE = "김해 제1공장"
ACC = "산업재해 발생 건수 0건"


def verdict(quote, period="2026-04", site=KIMHAE, hint="산업재해 발생 건수", definitions=""):
    return router._zero_verdict(quote, hint, period, {"site": site} if site else {}, definitions=definitions)


# ── §5 C1 규격 개정 연도 ───────────────────────────────────────────────────────

REVISIONS = ["GRI 403:2018", "GRI 403: 2018", "GRI 403 :   2018", "GRI 403：2018", "GRI 403 ： 2018",
             "ISO 45001:2018", "ISO 45001: 2018"]


@pytest.mark.parametrize("code", REVISIONS)
def test_a_revision_year_in_a_standard_heading_is_never_a_period(code):
    result = verdict(f"{code} 안전 현황\n{ACC}")
    assert result.status == "SOURCE_ONLY" and "2018" not in result.evidence_period, result
    assert result.scope_boundary == f"{code} 안전 현황"
    roles = {(span[1], span[2]) for span in result.scope_spans}
    assert any(role == "standard_ref" and "2018" in text for role, text in roles), roles


@pytest.mark.parametrize("code", REVISIONS)
def test_a_real_period_next_to_a_revision_year_is_kept(code):
    result = verdict(f"{code} 2026년 4월 김해 제1공장 안전 현황\n{ACC}")
    assert (result.status, result.evidence_period, result.evidence_site) == ("CONFIRMED", "2026년 4월", "김해1공장")


@pytest.mark.parametrize("code", REVISIONS)
@pytest.mark.parametrize("shape", ["{code} 기준 2026년 4월 김해 제1공장 {acc}", "2026년 4월 김해 제1공장 {code} 기준 {acc}",
                                   "2026년 4월 김해 제1공장 {acc}({code} 기준)"])
def test_a_standard_citation_in_the_statement_is_not_a_period(code, shape):
    result = verdict(shape.format(code=code, acc=ACC))
    assert (result.status, result.evidence_period) == ("CONFIRMED", "2026년 4월"), result


def test_a_real_year_and_quantities_are_still_read():
    assert (verdict(f"2018년 김해 제1공장 {ACC}").status, verdict(f"2018년 김해 제1공장 {ACC}").cause) == \
        ("REJECTED", "period_mismatch")
    assert verdict(f"2026년 4월 김해 제1공장 안전 현황\nISO 45001 부적합 3건\n{ACC}").status == "CONFIRMED"
    # 괄호 안에 규격 말고 다른 말이 섞이면 떼지 않는다 — 사실성 판정을 그대로 받는다
    assert verdict(f"2026년 4월 김해 제1공장 {ACC}(목표)").status == "REJECTED"
    assert verdict(f"2026년 4월 김해 제1공장 {ACC}(추정)").status != "CONFIRMED"


@pytest.mark.parametrize("text,period", [("Scope 1: 2025 배출량", ["2025"]), ("GRI 403: 2018 안전", []),
                                         ("ISO/IEC 27001: 2022 인증", []), ("ISO 45001:2018 기준", [])])
def test_only_standards_with_revisions_absorb_a_year_after_a_colon(text, period):
    # 개정판을 쓰지 않는 이름(`Scope 1`)의 콜론 뒤 연도는 보고 연도다 — 숫자 모양만으로 가리지 않는다
    assert [s.text for s in router._date_spans(router._standard_mask(text))] == period


def test_the_revision_mask_keeps_dates_written_after_a_colon():
    # `GRI 403: 2026년 4월`의 2026년 4월은 개정 연도가 아니라 실제 기간이다 — 번호(403)만 가린다
    assert router._standard_mask("GRI 403: 2026년 4월 안전 현황") == "GRI    : 2026년 4월 안전 현황"
    assert [s.text for s in router._date_spans(router._standard_mask("GRI 403: 2026년 4월"))] == ["2026년 4월"]


# ── §5 C2 회계연도·분기 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("heading,cause", [
    ("FY2026 김해 제1공장 안전 현황", "fiscal_period_undefined"),
    ("FY 2026 김해 제1공장 안전 현황", "fiscal_period_undefined"),
    ("FY26 김해 제1공장 안전 현황", "fiscal_year_abbreviated"),
    ("Q1 김해 제1공장 안전 현황", "quarter_undefined"),
    ("2026년 1분기 김해 제1공장 안전 현황", "quarter_months_not_stated"),
])
@pytest.mark.parametrize("period", ["2026-04", "2026", "2026-01~2026-03"])
def test_undefined_fiscal_and_quarter_labels_keep_the_site_and_a_specific_reason(heading, cause, period):
    result = verdict(f"{heading}\n{ACC}", period=period)
    assert (result.status, result.cause, result.evidence_site) == ("SOURCE_ONLY", cause, "김해1공장"), result
    assert cause != "period_not_stated" and result.evidence_period


def test_an_upper_month_heading_is_not_borrowed_under_a_fiscal_heading():
    result = verdict(f"2026년 4월 김해 제1공장 안전 현황\nFY2026 김해 제1공장 안전 현황\n{ACC}")
    assert (result.status, result.cause, result.evidence_period) == ("SOURCE_ONLY", "fiscal_period_undefined", "FY2026")


@pytest.mark.parametrize("definition", ["FY2026 = 2026-01-01~2026-12-31", "FY2026(2026.1.1~2026.12.31)",
                                        "FY2026: 2026년 1월 1일 ~ 2026년 12월 31일"])
def test_a_defined_fiscal_year_is_used_and_never_split_into_april(definition):
    quote = f"FY2026 김해 제1공장 안전 현황\n{ACC}"
    april = verdict(quote, definitions=definition)
    assert (april.status, april.cause) == ("REJECTED", "period_unproven"), april   # 연간 값으로 4월을 추정하지 않는다
    annual = verdict(quote, period="2026-01~2026-12", definitions=definition)
    assert (annual.status, annual.evidence_site) == ("CONFIRMED", "김해1공장"), annual


def test_fy26_resolves_only_through_an_explicit_link():
    quote = f"FY26 김해 제1공장 안전 현황\n{ACC}"
    assert verdict(quote, period="2026-01~2026-12").cause == "fiscal_year_abbreviated"
    linked = verdict(quote, period="2026-01~2026-12",
                     definitions="FY2026 = 2026-01-01~2026-12-31\nFY26 = FY2026")
    assert linked.status == "CONFIRMED", linked
    year_only = verdict(quote, period="2026-01~2026-12", definitions="FY26(2026년)")
    assert year_only.status == "SOURCE_ONLY"                 # 연도만 이어서는 회계연도 구간이 정해지지 않는다


@pytest.mark.parametrize("period,status", [("2026-04", "REJECTED"), ("2026-01~2026-03", "CONFIRMED")])
def test_an_explicit_quarter_with_months_is_a_real_range(period, status):
    result = verdict(f"2026년 1분기(1~3월) 김해 제1공장 안전 현황\n{ACC}", period=period)
    assert result.status == status and result.evidence_period == "2026년 1분기(1~3월)", result


def test_fy_with_a_month_stays_a_dated_month():
    assert [s.text for s in router._date_spans("FY 2026.04 김해 제1공장")] == ["2026.04"]
    assert router._date_spans("FY2026")[0].undefined == "fiscal_period_undefined"


# ── §5 C3 규격 제목 + 사업장 ──────────────────────────────────────────────────

PARENT = "2026년 4월 김해 제1공장 안전 현황"


@pytest.mark.parametrize("fact", [ACC, "산업재해가 발생하지 않았다."])
@pytest.mark.parametrize("heading,expected", [
    ("GRI 403-9 A2공장 안전 현황", ("REJECTED", "site_mismatch", "A2공장", "")),
    ("GRI 403-9 2026년 4월 김해 제1공장 안전 현황", ("CONFIRMED", "stated_zero", "김해1공장", "2026년 4월")),
    ("GRI 403-9 2026년 4월 부산 제2공장 안전 현황", ("REJECTED", "site_mismatch", "부산2공장", "")),
    ("GRI 403-9 김해 제1공장 안전 현황", ("SOURCE_ONLY", "period_not_stated", "김해1공장", "")),
    ("GRI 403-9 산업재해", ("SOURCE_ONLY", "period_not_stated", "", "")),
    ("ISO 45001:2018 안전 현황", ("SOURCE_ONLY", "period_not_stated", "", "")),
    ("GRI 403-9 산업재해 ··· 45", ("SOURCE_ONLY", "period_not_stated", "", "")),
    ("GRI 403-9 산업재해 ........ 45", ("SOURCE_ONLY", "period_not_stated", "", "")),
    ("GRI 403 산업안전보건", ("SOURCE_ONLY", "period_not_stated", "", "")),
    ("ISO 45001 교육 현황", ("SOURCE_ONLY", "period_not_stated", "", "")),
])
def test_a_standard_heading_is_a_boundary_that_keeps_only_its_own_scope(heading, expected, fact):
    result = verdict(f"{PARENT}\n{heading}\n{fact}")
    assert (result.status, result.cause, result.evidence_site, result.evidence_period) == expected, result
    # 새 절 경계 — 위 머리말 범위를 빌리지 않는다(마침표 점선은 기존 절 분리가 마침표에서 토막을 나눈다)
    assert result.scope_boundary and heading.startswith(result.scope_boundary), result
    assert "김해" not in result.evidence_site or "김해" in heading


@pytest.mark.parametrize("child,expected", [
    ("A2공장 안전 현황", ("REJECTED", "A2공장")),           # 규격 없는 다른 사업장 하위 제목(PR69 8차)
    ("안전 현황", ("CONFIRMED", "김해1공장")),               # 자기 범위 없는 하위 제목은 계속 상속
])
def test_plain_child_headings_keep_their_existing_contract(child, expected):
    result = verdict(f"{PARENT}\n{child}\n{ACC}")
    assert (result.status, result.evidence_site) == expected


# ── §3 08 공정스크랩: 내부 재투입률은 재생 원부자재 비율이 아니다 ─────────────

def metric(hint, value, unit, kesg=None):
    return router.ExtractedMetric(metric_hint=hint, value=value, unit=unit, period="2026-04", kesg_code_guess=kesg)


@pytest.mark.parametrize("hint,guess", [("생산 스크랩 내부 재투입률", None), ("생산 스크랩 내부 재투입률", "E-2-2"),
                                        ("스크랩 발생량", None), ("스크랩 외부 반출", None)])
def test_scrap_reinput_metrics_never_become_recycled_material_ratio(hint, guess):
    from esgenie.ssot.evidence_graph import _resolve_kesg_code
    ext = router.OcrExtraction(source_file="scrap.pdf", channel=router.DocChannel.UNSTRUCTURED, doc_type="report",
                               metrics=[metric(hint, 92.0 if "률" in hint else 12500, "%" if "률" in hint else "kg", guess)])
    router._backfill_kesg_codes(ext)
    assert ext.metrics[0].kesg_code_guess in (None, guess) and "alias_backfill" not in ext.router_meta
    assert _resolve_kesg_code(ext.metrics[0]) is None


@pytest.mark.parametrize("hint", ["재생 원부자재 비율", "재활용 원료 사용 비율", "고철 사용 비율"])
def test_real_recycled_material_labels_keep_e22(hint):
    from esgenie.ssot.evidence_graph import _resolve_kesg_code
    assert _resolve_kesg_code(metric(hint, 30.0, "%")) == "E-2-2"


# ── §3 표 칸 라벨 근거화(08 합계·09 대상·13 중복 제외 합계) ───────────────────

SCRAP = ("공정 스크랩 관리대장\n기간 | 발생량 | 내부 재투입 | 외부 반출\n04-01 ~ 04-07 | 3,200 | 2,944 | 256\n"
         "합계 | 12,500 | 11,500 | 1,000")
TRAINING = "이번 교육 출석 집계\n대상 | 참석 | 이번 회차 미참석 | 참석 비율\n50명 | 46명 | 4명 | 92.0%"
FOLLOWUP = ("4월 해당 교육 대상자 대조\n구분 | 고유 인원 | 근거\n4월 22일 참석 | 46명 | HN-G01~HN-G46\n"
            "4월 27일 추가 참석 | 4명 | HN-G47~HN-G50\n중복 제외 합계 | 50명 | 대상 HN-G01~HN-G50과 일치")


def map_one(hint, value, unit, quote, source):
    issues = []
    metrics, _ = router._map_vlm_json({"metrics": [dict(metric_hint=hint, value=value, unit=unit, period="2026-04",
                                                        quote=quote)]}, page_no=0, source_text=source, issues=issues)
    return metrics, issues


@pytest.mark.parametrize("value,column", [(12500, "발생량"), (11500, "내부 재투입"), (1000, "외부 반출")])
def test_a_row_label_only_label_gets_its_column_header(value, column):
    (m,), issues = map_one("합계", value, "kg", "합계 | 12,500 | 11,500 | 1,000", SCRAP)
    assert m.metric_hint == f"합계 · {column}" and m.value == value
    (issue,) = [i for i in issues if i["reason"] == "label_from_table_header"]
    assert (issue["model_label"], issue["column_header"]) == ("합계", column)
    (record,) = [p for p in m.boundary["provenance"] if p.get("source") == "label_check"]
    assert record["model_label"] == "합계"


@pytest.mark.parametrize("wrong", ["교육 출석 인원", "교육 참석 인원", "당일 참석자 수"])
def test_a_target_cell_is_not_relabelled_as_attendance(wrong):
    (m,), issues = map_one(wrong, 50, "명", "대상 | 참석 | 이번 회차 미참석 | 참석 비율\n50명 | 46명 | 4명 | 92.0%", TRAINING)
    assert "대상" in m.metric_hint and "참석 인원" not in m.metric_hint and "출석 인원" not in m.metric_hint
    assert any(i.get("model_label") == wrong for i in issues)


@pytest.mark.parametrize("hint,value,unit", [("교육 대상 인원", 50, "명"), ("교육 참석 인원", 46, "명"),
                                             ("교육 미참석 인원", 4, "명"), ("교육 참석 비율", 92.0, "%")])
def test_labels_that_already_name_their_column_are_untouched(hint, value, unit):
    (m,), issues = map_one(hint, value, unit, "대상 | 참석 | 이번 회차 미참석 | 참석 비율\n50명 | 46명 | 4명 | 92.0%", TRAINING)
    assert m.metric_hint == hint and not [i for i in issues if i["reason"] == "label_from_table_header"]


@pytest.mark.parametrize("hint,value,expected", [
    ("교육 대상자 합계", 50, "중복 제외 합계 · 고유 인원"),       # 원문 행은 '중복 제외 합계'
    ("교육 대상자 수", 46, "4월 22일 참석 · 고유 인원"),
    ("교육 대상자 수", 4, "4월 27일 추가 참석 · 고유 인원"),
])
def test_followup_counts_keep_their_source_row_meaning(hint, value, expected):
    line = next(line for line in FOLLOWUP.splitlines() if f"| {value}명" in line)
    (m,), _ = map_one(hint, value, "명", line, FOLLOWUP)
    assert m.metric_hint == expected


@pytest.mark.parametrize("quote,source,hint,value", [
    ("구분 | 2024 | 2025\n산업재해율 | 0.12 | 0.10", "구분 | 2024 | 2025\n산업재해율 | 0.12 | 0.10", "산업재해율", 0.10),
    ("구분 | 목표 | 실적\n재활용률 | 30 | 29.3", "구분 | 목표 | 실적\n재활용률 | 30 | 29.3", "재활용률", 29.3),
    ("구분 | 국내 | 해외\n임직원 수 | 100 | 50", "구분 | 국내 | 해외\n임직원 수 | 100 | 50", "해외 임직원 수", 50),
    ("HN-G01 | 8 | 8 | 8 | 40", "인원 | 13일 | 14일 | 15일 | 합계\nHN-G01 | 8 | 8 | 8 | 40", "주간 근로시간", 8),
])
def test_period_role_axis_headers_and_repeated_values_never_relabel(quote, source, hint, value):
    (m,), issues = map_one(hint, value, "", quote, source)
    assert m.metric_hint == hint and not [i for i in issues if i["reason"] == "label_from_table_header"]


# ── §3 11 정보보호: 모델이 값을 비운 명시적 미보유 서술 ───────────────────────

ISMS = "인증 보유 상태 | 2026-04-30 기준 ISMS 인증 미보유"


def null_row(quote, hint="인증 보유 상태", period="2026-04-30"):
    issues = []
    metrics, _ = router._map_vlm_json({"metrics": [dict(metric_hint=hint, value=None, unit=None, period=period,
                                                        quote=quote)]}, page_no=0, source_text=quote, issues=issues)
    return metrics, issues


def test_an_explicit_negation_with_a_null_model_value_is_kept_as_zero():
    (m,), issues = null_row(ISMS)
    assert (m.metric_hint, m.value) == ("인증 보유 상태", 0)
    zero = [p for p in m.boundary["provenance"] if p.get("source") == "zero_evidence"]
    assert zero and zero[0]["status"] == "CONFIRMED" and zero[0]["source_period"] == "2026-04-30"
    recovery = [p for p in m.boundary["provenance"] if p.get("source") == "value_recovery"]
    assert recovery and recovery[0]["model_value"] is None
    assert [i["reason"] for i in issues] == ["zero_recovered_from_negation"]
    assert "zero_recovered_from_negation" in router._VALUE_RECONCILED_REASONS


@pytest.mark.parametrize("quote", ["인증 보유 상태 | -", "인증 보유 상태 | 0", "인증 보유 상태 | 해당 없음",
                                   "인증 보유 상태 | 미확인", "인증 보유 상태 | 2027년 취득 목표(현재 미보유 여부 미확인)",
                                   "인증 보유 상태 | (그래프)"])
def test_a_null_value_without_an_explicit_negation_stays_unreported(quote):
    metrics, issues = null_row(quote)
    assert metrics == [] and [i["reason"] for i in issues] == ["value_not_reported"]


def test_a_regulation_does_not_turn_into_certification():
    metrics, issues = null_row("정보보호 관리규정 | 시행 2026-01-02", hint="ISMS 인증 보유")
    assert metrics == []


# ── §3 05 회사 답변 92%: 같은 기간·사업장, 다른 분모의 값을 옮긴 경우 ─────────

COMPANY = ("협력사 자가진단 응답 초안\n문항\n요청 내용\n협력사 작성 답변\nA-02\n"
           "4월 김해 제1공장 외부 위탁 전체 폐기물의 재활용률\n2026년 4월 김해 제1공장 사업장폐기물 재활용률 92%\n"
           "A-03\n2025년 두 공장 전체 Scope 1·2 배출량\n미작성\n작성 메모\n"
           "A-02의 입력 근거 메모: 공정 스크랩 대장 HW-SCR-202604의 재활용 관련 수치에서 옮겨 적음.")


@pytest.fixture
def company_pdf(monkeypatch):
    # 파서의 PDF 글자 추출은 검사 대상이 아니다 — 원문 줄 배치를 그대로 넣는다(실제 05 PDF와 같은 순서).
    from esgenie.supplychain import claims
    monkeypatch.setattr(claims, "_extract_text", lambda path: COMPANY)
    return "05_자가진단응답초안.pdf"


def test_the_company_answer_keeps_its_stated_scope_question_and_note(company_pdf):
    from esgenie.supplychain import parse_saq_claims
    claim = parse_saq_claims([company_pdf])["E-6-2"]
    assert claim.value == 92.0
    assert (claim.boundary["period_start"], claim.boundary["period_end"]) == ("2026-04-01", "2026-04-30")
    assert claim.boundary["site"] and "denominator_kind" not in claim.boundary
    assert claim.context["question_id"] == "A-02"
    assert "외부 위탁 전체 폐기물" in claim.context["request"]
    assert claim.context["source_ids"] == ["HW-SCR-202604"]


def waste_graph(with_scrap=True, with_training=True):
    graph = EvidenceGraph("LOCAL", "검토")
    graph.report_year = 2026
    april = dict(period_start="2026-04-01", period_end="2026-04-30", period_year=2026, aggregation="monthly",
                 coverage_months=1, site="제1공장", site_scope="site", site_path=("제1공장",), measure_kind="waste",
                 completeness="partial")
    rate = EvidenceNode("LOCAL_E-6-2_2026__ocr", "E-6-2", 29.3, "%", 2026, "ocr/waste", origin="ocr_structured",
                        source_file="04_waste.pdf", value_role="actual",
                        boundary=Boundary.from_dict({**april, "provenance": [
                            {"source": "document_header",
                             "quote": "계산: 5,400 kg ÷ 18,400 kg × 100 = 29.3478···% → 소수 첫째 자리 29.3%\n"
                                      "분자: 외부 위탁 폐기물 중 재활용 처리 중량. 분모: 전체 외부 위탁 처리 중량."}]}))
    graph.add_node(rate)
    if with_scrap:
        graph.add_node(EvidenceNode("LOCAL_scrap_rate", "생산 스크랩 내부 재투입률", 92.0, "%", 2026, "ocr/scrap",
                                    origin="ocr_unstructured", source_file="08_scrap.pdf",
                                    quote="생산 스크랩 내부 재투입률 92.0%\n계산: 내부 재투입 11,500 kg ÷ 공정 스크랩 발생 12,500 kg × 100 = 92.0%"))
    if with_training:
        graph.add_node(EvidenceNode("LOCAL_training_rate", "교육 참석 비율", 92.0, "%", 2026, "ocr/training",
                                    origin="ocr_unstructured", source_file="09_training.pdf"))
    result = SimpleNamespace(mapped={"E-6-2": {"value": 29.3, "unit": "%", "source_tier": "ocr_node_gated",
                                               "name": "폐기물 재활용 비율", "area": "E"}}, confidence_flags={})
    selection.finalize_ledger(result, graph)
    texts = [SimpleNamespace(source_file="08_scrap.pdf", raw_text="HW-SCR-202604 공정 스크랩 관리대장"),
             SimpleNamespace(source_file="09_training.pdf", raw_text="HW-TRN-20260422")]
    return graph, result, texts


def answer_for(graph, result, texts, claims):
    from esgenie.ssot.audit_trace import build_data_points
    from esgenie.supplychain.claims import trace_claim_values
    from esgenie.supplychain.mapping import derive_answer
    from esgenie.supplychain.frameworks import get_framework
    q = next(q for q in get_framework("rba42").questions if q.primary_code == "E-6-2" and q.qtype == "numeric")
    traced = trace_claim_values(claims, SimpleNamespace(evidence_graph=graph, ocr_extractions=texts))
    points = {p.kesg_code: p for p in build_data_points(graph, {"E-6-2": 0.0}, target_codes=["E-6-2"])}
    return derive_answer(q, mapped=result.mapped, missing=set(), dp_by_code=points, claims=traced), traced


def claim_set(context=None, boundary=None):
    from esgenie.supplychain.claims import ClaimSet, SupplierClaim
    raw = "2026년 4월 김해 제1공장 사업장폐기물 재활용률 92%"
    from esgenie.supplychain.claims import _stated_scope
    return ClaimSet({"E-6-2": SupplierClaim("E-6-2", 92.0, "%", raw=raw, source="saq:05.pdf",
                                            boundary=_stated_scope(raw) if boundary is None else boundary,
                                            context=context if context is not None else {
                                                "question_id": "A-02",
                                                "request": "4월 김해 제1공장 외부 위탁 전체 폐기물의 재활용률",
                                                "source_note": "A-02의 입력 근거 메모: 공정 스크랩 대장 HW-SCR-202604의 재활용 관련 수치에서 옮겨 적음.",
                                                "source_ids": ["HW-SCR-202604"]})})


def test_a_transcribed_internal_rate_is_explained_with_both_source_lines():
    graph, result, texts = waste_graph()
    ans, traced = answer_for(graph, result, texts, claim_set())
    trace = traced["E-6-2"].context["value_trace"]
    assert [t["source_file"] for t in trace] == ["08_scrap.pdf"]         # 같은 92%인 교육 참석률은 고르지 않는다
    assert (ans.status, ans.comparison) == ("flagged", "not_comparable")
    reason = ans.comparison_reason
    for text in ("2026-04-01~2026-04-30", "08_scrap.pdf", "11,500 kg ÷ 공정 스크랩 발생 12,500 kg",
                 "5,400 kg ÷ 18,400 kg", "29.3", "HW-SCR-202604", "확정하지 않았습니다"):
        assert text in reason, (text, reason)
    assert "기간·사업장·분모 미확인" not in reason and not any("D1 불일치" in f for f in ans.flags)


def test_without_a_source_trace_a_missing_denominator_is_named_not_a_conflict():
    graph, result, texts = waste_graph(with_scrap=False, with_training=False)
    ans, _ = answer_for(graph, result, texts, claim_set(context={}))
    assert ans.comparison == "scope_unconfirmed" and "분모가 적혀 있지 않아" in ans.comparison_reason
    assert ans.status != "flagged" and not any("D1 불일치" in f for f in ans.flags)


def test_without_a_note_only_related_metrics_are_traced():
    graph, result, texts = waste_graph()
    _, traced = answer_for(graph, result, texts, claim_set(context={"question_id": "A-02"}))
    assert [t["metric"] for t in traced["E-6-2"].context["value_trace"]] == ["생산 스크랩 내부 재투입률"]


def test_a_claim_without_stated_scope_keeps_the_existing_partial_rule():
    graph, result, texts = waste_graph(with_scrap=False, with_training=False)
    ans, _ = answer_for(graph, result, texts, claim_set(context={}, boundary={}))
    assert ans.comparison == "scope_unconfirmed" and "D1 불일치" not in " ".join(ans.flags)


# ── §4 SOURCE_ONLY 0의 최종 소비 경로 ─────────────────────────────────────────

def zero_graph(status="SOURCE_ONLY", cause="period_not_stated", code="S-4-2", unit="‰", name="산업재해율"):
    provenance = ({"source": "zero_evidence", "status": status, "cause": cause},)
    notes = ("원문에 기간 표기가 없어 요청 기간의 실적인지 확인하지 못했습니다.",) if status == "SOURCE_ONLY" else ()
    graph = EvidenceGraph("LOCAL", "검토")
    graph.report_year = 2026
    graph.add_node(EvidenceNode(f"LOCAL_{code}_2026__ocr", code, 0, unit, 2026, "ocr/report", origin="ocr",
                                source_file="safety.pdf", quote=f"{name} 0", value_role="actual",
                                boundary=Boundary(provenance=provenance, review_notes=notes)))
    result = SimpleNamespace(mapped={code: {"value": 0, "unit": unit, "source_tier": "ocr_node_gated",
                                            "name": name, "area": code[0], "evidence_node_ids": [f"LOCAL_{code}_2026__ocr"]}},
                             confidence_flags={}, missing=[])
    selection.finalize_ledger(result, graph)
    return graph, result


@pytest.mark.parametrize("status,unconfirmed", [("SOURCE_ONLY", True), ("CONFIRMED", False)])
def test_a_source_only_zero_is_never_compared_as_a_requested_actual(status, unconfirmed):
    from esgenie.ssot.audit_trace import build_data_points
    graph, _ = zero_graph(status, "period_not_stated" if status == "SOURCE_ONLY" else "stated_zero")
    (point,) = build_data_points(graph, {"S-4-2": 0.0}, target_codes=["S-4-2"],
                                 d1_evaluations={"S-4-2": {"status": "complete", "comparison": "compared"}})
    assert point.value == 0 and point.representative_node_ids                  # 원문 0은 지우지 않는다
    # 정상 0(CONFIRMED)은 새로 미확정 처리하지 않는다 — 대조 결과를 그대로 둔다
    assert point.comparison == ("scope_unconfirmed" if unconfirmed else "compared")
    if unconfirmed:
        assert point.verification == "estimated"
        assert "요청 기간의 실적인지 확인하지 못했습니다" in point.comparison_reason


@pytest.mark.parametrize("claim_value", [0.0, 2.0])
def test_a_supplier_claim_is_not_matched_or_conflicted_against_a_source_only_zero(claim_value):
    from esgenie.ssot.audit_trace import build_data_points
    from esgenie.supplychain.claims import SupplierClaim
    from esgenie.supplychain.frameworks import get_framework
    from esgenie.supplychain.mapping import derive_answer
    graph, result = zero_graph()
    points = {p.kesg_code: p for p in build_data_points(graph, {"S-4-2": 0.0}, target_codes=["S-4-2"])}
    q = next(q for q in get_framework("kesg28").questions if q.primary_code == "S-4-2")
    claims = {"S-4-2": SupplierClaim("S-4-2", claim_value, "‰", raw=f"산업재해율 {claim_value}‰", source="manual")}
    ans = derive_answer(q, mapped=result.mapped, missing=set(), dp_by_code=points, claims=claims)
    assert ans.comparison == "scope_unconfirmed" and ans.status != "verified"
    assert not any(f.startswith(("자가신고 일치", "D1 불일치")) for f in ans.flags), ans.flags
    assert any("원문 범위로만 보존" in f for f in ans.flags)


def test_a_non_basic_code_answer_keeps_the_source_only_state():
    from esgenie.supplychain.frameworks import get_framework
    from esgenie.supplychain.mapping import derive_answer
    graph, result = zero_graph(code="E-8-1", unit="건", name="환경 법규 위반")
    q = next(q for q in get_framework("kesg61").questions if q.primary_code == "E-8-1")
    ans = derive_answer(q, mapped=result.mapped, missing=set(), dp_by_code={}, claims={})
    assert ans.value == 0 and ans.status == "self_reported"
    assert "scope_source_only" in ans.confidence_flags and ans.comparison == "scope_unconfirmed"
    assert ans.scope_notes and "범위 확인 필요" in ans.review_note


def test_the_llm_ledger_and_chunks_carry_the_unconfirmed_state():
    from esgenie.layer2_rag import _area_item_rows, _kesg_pseudo_chunk, _row_scope_note
    _, result = zero_graph()
    result.confidence_flags = {"S-4-2": list(result.mapped["S-4-2"]["resolved_fact"]["flags"])}
    covered, _ = _area_item_rows(result, "S")
    (row,) = covered
    assert row["status"].endswith("·범위미확정") and "요청 범위 실적 미확정" in row["state"]
    assert "[상태: 요청 범위 실적 미확정" in _row_scope_note(row)
    assert "요청 범위 실적 미확정" in _kesg_pseudo_chunk(covered, "S").text


def test_confirmed_ledger_rows_keep_their_existing_status():
    from esgenie.layer2_rag import _area_item_rows
    _, result = zero_graph("CONFIRMED", "stated_zero")
    result.confidence_flags = {"S-4-2": list(result.mapped["S-4-2"]["resolved_fact"]["flags"])}
    (row,), _ = _area_item_rows(result, "S")
    assert "범위미확정" not in row["status"] and "미확정" not in row["state"]


def generated(text, chunks):
    from esgenie.layer2_rag import IndexedDoc
    context = SimpleNamespace(all_hits=lambda: [(IndexedDoc(text=t, meta={}, chunk_id=c), 1.0) for c, t in chunks.items()])
    return SimpleNamespace(final=SimpleNamespace(generation=SimpleNamespace(text=text, context=context)),
                           final_text=text)


def test_generated_prose_that_asserts_a_source_only_zero_is_marked_in_the_body():
    from esgenie.layer6_report import annotate_generated_text
    _, result = zero_graph()
    output = SimpleNamespace(extraction=result)
    text = "### 지표 해설\n요청 기간 산업재해율은 0‰로 확인되었다 [kesg_items_S]."
    body, marks = annotate_generated_text(output, "S", generated(text, {"kesg_items_S": "S-4-2 산업재해율 0 ‰"}))
    assert "[검토: 범위 미확정" in body and [m["reason"] for m in marks] == ["source_only_stated"]
    hedged = "### 지표 해설\n산업재해율 0‰는 원문 범위로만 확인된 참고값이다 [kesg_items_S]."
    body, marks = annotate_generated_text(output, "S", generated(hedged, {"kesg_items_S": "S-4-2 산업재해율 0 ‰"}))
    assert not marks and "[검토" not in body


def test_generated_prose_with_unfound_numbers_or_widened_scope_is_marked():
    from esgenie.layer6_report import annotate_generated_text
    graph = EvidenceGraph("LOCAL", "검토")
    graph.report_year = 2026
    boundary = Boundary.from_dict(dict(period_start="2026-04-01", period_end="2026-04-30", period_year=2026,
                                       aggregation="monthly", site="제1공장", site_scope="site", measure_kind="electricity_grid",
                                       measure="사용전력량", completeness="partial"))
    graph.add_node(EvidenceNode("LOCAL_E-4-1", "E-4-1", 142560.0, "kWh", 2026, "ocr/bill", origin="ocr",
                                source_file="02_bill.pdf", value_role="actual", boundary=boundary))
    result = SimpleNamespace(mapped={"E-4-1": {"value": 0.513216, "unit": "TJ", "source_tier": "ocr_node_gated",
                                               "name": "에너지 사용량", "area": "E"}}, confidence_flags={}, missing=[])
    selection.finalize_ledger(result, graph)
    text = ("### 지표 해설\n에너지 사용량은 0.513216 TJ이며 이는 사업장 전체의 전기·가스 사용량을 합산한 결과다. [kesg_items_E] "
            "2026년 4월 22일 교육에는 정규직 39명이 출석하였다. [c1]\n### 주요 활동\n교육은 4월 22일 실시되었다. [c1]")
    chunks = {"kesg_items_E": "E-4-1 에너지 사용량 0.513216 TJ", "c1": "2026-04-22 교육 대상 50명 참석 46명"}
    body, marks = annotate_generated_text(SimpleNamespace(extraction=result), "E", generated(text, chunks))
    reasons = [m["reason"] for m in marks]
    assert reasons.count("scope_widened") == 1 and "orphan_number" in reasons
    orphan = next(m for m in marks if m["reason"] == "orphan_number")
    assert orphan["numbers"] == ["39"]                          # 날짜(4월 22일)의 숫자는 표시하지 않는다
    assert body.count("[검토:") == 2 and "정규직 39명이 출석하였다." in body   # 원래 문장은 고치지 않는다


def test_the_datasheet_shows_scope_and_the_unconfirmed_state(tmp_path):
    import openpyxl
    from esgenie.ssot.audit_trace import AuditTraceV15, build_data_points
    from esgenie.ssot.excel_exporter import export_datasheet
    graph, _ = zero_graph()
    points = build_data_points(graph, {"S-4-2": 0.0}, target_codes=["S-4-2"])
    paths = export_datasheet(AuditTraceV15("LOCAL", "검토", "2026-10-05", data_points=points), tmp_path)
    ws = openpyxl.load_workbook(paths["xlsx"])["DataSheet"]
    head = [c.value for c in ws[1]]
    row = dict(zip(head, [c.value for c in ws[2]]))
    assert head[:12] == ["K-ESG 코드", "항목명", "값", "단위", "연도", "검증상태", "D1 위험도", "증빙 파일", "D1 평가",
                         "비교 주장", "미검증 주장", "미검증 사유"]          # 기존 열 순서 유지
    assert row["값"] == 0 and "원문 범위로만 보존" in row["범위·확정 상태"] and "범위 확인 필요" in row["범위·확정 상태"]


def test_a_rejected_zero_does_not_come_back_as_a_requested_actual():
    issues = []
    quote = "구분 | 목표 | 실적\n산업재해율(‰) | 0 | 1"
    metrics, _ = router._map_vlm_json({"metrics": [dict(metric_hint="산업재해율", value=0, unit="‰", period="2026-04",
                                                        quote=quote, kesg_code="S-4-2")]},
                                      page_no=0, source_text=quote, issues=issues)
    assert metrics == [] and issues[0]["status"] == "REJECTED"
    ext = router.OcrExtraction(source_file="t.pdf", channel=router.DocChannel.UNSTRUCTURED, doc_type="report",
                               metrics=metrics, router_meta={"unvalued_records": [{"page": 0, "records": issues}]})
    graph = EvidenceGraph("LOCAL", "검토")
    merge_ocr_extraction(graph, ext, report_year=2026)
    assert not graph.nodes
    findings = build_source_review(SimpleNamespace(evidence_graph=graph, ocr_extractions=[ext], extraction=None,
                                                   sections={}, item_retrievals=[]))
    assert any(f.check_reason == "zero_not_in_evidence" for f in findings)


# ── 내보내기 ──────────────────────────────────────────────────────────────

def test_a_very_long_answer_row_splits_across_pdf_pages_instead_of_failing(tmp_path):
    import fitz
    from esgenie.supplychain.exporters.pdf import export_response_sheet_pdf
    from esgenie.supplychain.schema import Answer, ResponseSheet
    long = " / ".join(f"HN-G{i:02d} | 정규직 | 14:00~16:00 | 출석" for i in range(1, 60))
    answers = [Answer(qid="RBA-E-6", section="경영시스템", question_text="[E-6] 교육", value=True, status="verified",
                      rationale=f"응답: 예 · 독립 증빙: {long}")]
    path = export_response_sheet_pdf(ResponseSheet("rba42", "RBA", "검토", answers=answers, gaps=[]), tmp_path,
                                     embed_evidence=False)
    text = "".join(page.get_text() for page in fitz.open(path)).replace("\n", "")
    assert "HN-G59" in text.replace(" ", "")                    # 마지막 칸까지 잘리지 않는다
