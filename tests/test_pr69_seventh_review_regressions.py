"""PR 69 7차 검토(2026-10-03, 588f0ea)에서 찾은 행·절 역할 구분의 두 결함 회귀 테스트.

R1 첫 칸에 `사업장`이 **들어 있기만** 해도 축 이름으로 봐서, 목표·실적 열마다 사업장을 적은 데이터 행
   (`교육 대상 사업장 | 김해 제1공장 | 부산 제1공장`)이 새 머리글이 됐다. 아래 `산업재해 | 0 | 1`의
   목표 0이 실적이 돼 CONFIRMED·S-4-2 대표값이 됐다(5bdd777에서 거부하던 동작의 회귀).
R2 줄 머리 번호를 뗀 뒤 남은 각주·규격 번호(`[1]`·`(1)`·`ISO 45001`)를 수량으로 읽어 새 절 제목을
   사실로 건너뛰었고, 0이 앞 절의 4월·김해 범위로 CONFIRMED가 됐다(5bdd777에도 있던 잔존 결함).

검토 산출물의 독립 검사 10건(`output/reviews/pr69_20261003/seventh_review/test_pr69_header_roles.py`)을
개인 경로·`REVIEW_CODE` 없이 옮기고, 라벨·행 배치·열 순서·표기 변형의 동등성, 정상 대조군, 원본
응답 → 원장 → 확인 목록·보고서 입력 통합 검사를 붙였다. 모든 문구는 합성 입력이다.
"""
from __future__ import annotations

import itertools
import json
import socket
from types import SimpleNamespace

import pytest

from esgenie.config import SETTINGS
from esgenie.layer6_report import _block_source_review
from esgenie.llm import LLMResponse
from esgenie.source_review import build_source_review
from esgenie.ssot import ocr_router as router
from esgenie.ssot import selection
from esgenie.ssot.boundary import Boundary
from esgenie.ssot.evidence_graph import EvidenceGraph, merge_ocr_extraction


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("network access is not allowed in this test")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


def mapped(quote, hint="산업재해 발생 건수", unit="건", period="2026-04", site=""):
    issues = []
    metrics, clauses = router._map_vlm_json(
        {"metrics": [dict(metric_hint=hint, value=0, unit=unit, period=period,
                          quote=quote, boundary={"site": site} if site else {})]},
        source_text=quote, page_no=0, issues=issues,
    )
    return metrics, clauses, issues


def verdict(quote, hint="산업재해 발생 건수", period="2026-04", site="김해 제1공장"):
    return router._zero_verdict(quote, hint, period, {"site": site} if site else {})


def zero_record(metric):
    (record,) = [p for p in metric.boundary.get("provenance", []) if p.get("source") == "zero_evidence"]
    return record


def target_rejected(metrics, issues):
    return not metrics and any(i.get("cause") == "future_or_intent" for i in issues)


def ledger(quote):
    """검토 산출물과 같은 경로: 매핑 → 근거 그래프 → 대표값 → 확인 목록."""
    metrics, clauses, issues = mapped(quote, "산업재해율", "‰", site="김해 제1공장")
    extraction = router.OcrExtraction(
        source_file="synthetic-header-role-review.pdf", channel=router.DocChannel.UNSTRUCTURED,
        doc_type="report", metrics=metrics, clauses=clauses, raw_text=quote,
    )
    graph = EvidenceGraph("LOCAL", "검토")
    merge_ocr_extraction(graph, extraction, report_year=2026)
    facts = {n.metric: selection._from_nodes(graph, n.metric, [n]) for n in graph.nodes.values()}
    graph.resolved_facts = facts
    findings = build_source_review(SimpleNamespace(
        evidence_graph=graph, ocr_extractions=[extraction], extraction=None,
        sections={}, item_retrievals=[],
    ))
    return facts, findings, metrics, issues


# ── 검토 산출물 10건(R1 3 · R2 4 · 정상 대조 3) ─────────────────────────────

def site_data_table(scoped=False, rate=False, label="교육 대상 사업장"):
    head = "2026년 4월 안전 현황\n" if scoped else ""
    metric = "산업재해율(‰)" if rate else "산업재해 발생 건수"
    return (head + "구분 | 목표 | 실적\n교육 참여 인원 | 0 | 50\n"
            + label + " | 김해 제1공장 | 부산 제1공장\n" + metric + " | 0 | 1")


@pytest.mark.parametrize("scoped", [False, True])
def test_site_values_in_a_data_row_do_not_turn_target_zero_into_actual(scoped):
    metrics, _, issues = mapped(site_data_table(scoped), site="김해 제1공장" if scoped else "")
    assert target_rejected(metrics, issues), (metrics, issues)


def test_affiliation_data_row_control():
    metrics, _, issues = mapped(site_data_table(label="소속"))
    assert target_rejected(metrics, issues), issues


def test_real_site_header_control():
    quote = ("구분 | 목표 | 실적\n교육 참여 인원 | 0 | 50\n"
             "구분 | 1공장 | 2공장\n산업재해 발생 건수 | 0 | 1")
    metrics, _, issues = mapped(quote)
    assert [m.value for m in metrics] == [0], issues
    assert zero_record(metrics[0])["evidence_column"] == "1공장"


def sections(new_heading, rate=False, older="2026년 4월 김해 제1공장 안전 현황", earlier=None, fact=None):
    metric = "산업재해율(‰)" if rate else "산업재해 발생 건수"
    unit = "" if rate else "건"
    first = earlier if earlier is not None else metric + " 1" + unit
    head = (older + "\n" + first + "\n\n") if older else ""
    return head + new_heading + "\n" + (fact or metric + " 0" + unit)


@pytest.mark.parametrize("heading", [
    "2. 2026년 5월 부산 제1공장 교육 현황 [1]",
    "2. 2026년 5월 부산 제1공장 교육 현황 (1)",
    "2. 2026년 5월 부산 제1공장 ISO 45001 교육 현황",
])
def test_heading_identifiers_and_footnotes_do_not_reopen_previous_scope(heading):
    result = verdict(sections(heading))
    assert (result.status, result.evidence_period, result.evidence_site) == ("SOURCE_ONLY", "", ""), result
    assert "교육 현황" in result.scope_boundary and not result.scope_heading, result


def test_plain_new_section_control():
    result = verdict(sections("2026년 5월 부산 제1공장 교육 현황"))
    assert (result.status, result.evidence_period, result.evidence_site) == ("SOURCE_ONLY", "", ""), result


def test_target_zero_is_excluded_from_s42_after_site_data_row():
    facts, findings, metrics, issues = ledger(site_data_table(scoped=True, rate=True))
    assert "S-4-2" not in facts, (facts, findings, issues)


def test_footnoted_section_keeps_scope_warning_in_s42():
    facts, findings, metrics, issues = ledger(sections("2. 2026년 5월 부산 제1공장 교육 현황 [1]", rate=True))
    assert "S-4-2" in facts and facts["S-4-2"].value == 0, (facts, issues)
    assert "scope_source_only" in facts["S-4-2"].flags, facts
    assert any(f.code == "S-4-2" and f.check_reason == "scope_source_only" for f in findings), findings


# ── R1: 축 이름은 첫 칸 전체로, 데이터 라벨은 열 역할을 바꾸지 않는다 ─────────────

DATA_LABELS = ["교육 대상 사업장", "점검 대상 사업장", "실적 집계 사업장", "목표 사업장", "대상 사업장명",
               "사업장 소재지", "소속", "교육 장소"]
SITE_VALUES = ["김해 제1공장 | 부산 제1공장", "김해 제1공장 | 김해 제1공장", "부산 제1공장 | 김해 제1공장",
               "1공장 | 2공장", "본사 | 해외", "서아산공장 | 본사"]


@pytest.mark.parametrize("label", DATA_LABELS)
@pytest.mark.parametrize("values", SITE_VALUES)
def test_data_label_with_site_values_keeps_the_target_role(label, values):
    quote = "구분 | 목표 | 실적\n교육 참여 인원 | 0 | 50\n" + label + " | " + values + "\n산업재해 발생 건수 | 0 | 1"
    metrics, _, issues = mapped(quote)
    assert target_rejected(metrics, issues), (label, values, metrics, issues)
    (issue,) = [i for i in issues if i.get("cause") == "future_or_intent"]
    record = issue["records"][0] if issue.get("records") else issue
    assert record.get("evidence_column", "목표") == "목표", issue


ROWS = ["교육 참여 인원 | 0 | 50", "교육 대상 사업장 | 김해 제1공장 | 부산 제1공장",
        "점검 상태 | 완료 | 완료", "소속 | 김해공장 | 부산공장", "점검 대상 사업장 | 본사 | 해외"]


@pytest.mark.parametrize("rows", [p for k in range(4) for p in itertools.permutations(ROWS, k)])
def test_inserting_removing_or_reordering_data_rows_keeps_the_target_role(rows):
    quote = "구분 | 목표 | 실적\n" + "".join(r + "\n" for r in rows) + "산업재해 발생 건수 | 0 | 1"
    metrics, _, issues = mapped(quote)
    assert target_rejected(metrics, issues), (rows, issues)


@pytest.mark.parametrize("roles,values,kept", [
    (("목표", "실적"), ("0", "1"), False),
    (("목표", "실적"), ("1", "0"), True),
    (("목표", "실적"), ("0", "0"), True),
    (("실적", "목표"), ("1", "0"), False),
    (("실적", "목표"), ("0", "1"), True),
    (("실적", "목표"), ("0", "0"), True),
])
@pytest.mark.parametrize("label", ["교육 대상 사업장", "실적 집계 사업장"])
def test_the_real_column_role_decides_after_a_site_data_row(roles, values, kept, label):
    quote = (f"구분 | {roles[0]} | {roles[1]}\n{label} | 김해 제1공장 | 부산 제1공장\n"
             f"산업재해 발생 건수 | {values[0]} | {values[1]}")
    metrics, _, issues = mapped(quote)
    if not kept:
        assert target_rejected(metrics, issues), issues
        return
    assert [m.value for m in metrics] == [0], issues
    record = zero_record(metrics[0])
    assert (record["evidence_column"], record["column_state"]) == ("실적", "header"), record
    actual = roles.index("실적")
    row = quote.splitlines()[-1]
    cell_start = quote.rindex(row) + sum(len(c) + 1 for c in row.split("|")[:actual + 1])
    assert record["evidence_start"] >= cell_start - 1 and quote[record["evidence_start"]:record["evidence_end"]].strip() == "0"


@pytest.mark.parametrize("header,column", [
    ("구분 | 1공장 | 2공장", "1공장"), ("사업장 | 김해 제1공장 | 부산 제1공장", "김해 제1공장"),
    (" | 1공장 | 2공장", "1공장"), ("사업장명 | 김해공장 | 부산공장", "김해공장"),
    ("사업장 구분 | 1공장 | 2공장", "1공장"), ("구분 | 실적 | 목표", "실적"),
])
def test_a_real_new_header_still_resets_the_roles(header, column):
    quote = ("구분 | 목표 | 실적\n교육 참여 인원 | 0 | 50\n교육 대상 사업장 | 김해 제1공장 | 부산 제1공장\n"
             + header + "\n산업재해 발생 건수 | 0 | 1")
    metrics, _, issues = mapped(quote)
    assert [m.value for m in metrics] == [0], (header, issues)
    assert zero_record(metrics[0])["evidence_column"] == column


def test_a_repeated_role_header_keeps_a_later_actual_zero():
    quote = ("구분 | 목표 | 실적\n교육 참여 인원 | 0 | 50\n교육 대상 사업장 | 김해 제1공장 | 부산 제1공장\n"
             "구분 | 목표 | 실적\n산업재해 발생 건수 | 1 | 0")
    metrics, _, issues = mapped(quote)
    assert [m.value for m in metrics] == [0], issues
    assert zero_record(metrics[0])["evidence_column"] == "실적"


def test_a_multi_line_header_still_joins_after_a_site_data_row():
    quote = ("구분 | 2026 | 2026\n구분 | 목표 | 실적\n교육 대상 사업장 | 김해 제1공장 | 부산 제1공장\n"
             "산업재해 발생 건수 | 1 | 0")
    metrics, _, issues = mapped(quote, period="2026")
    assert [m.value for m in metrics] == [0], issues
    assert zero_record(metrics[0])["evidence_column"] == "2026 실적"


@pytest.mark.parametrize("cells,axis", [
    ("구분", True), ("사업장", True), ("사업장명", True), ("구분/연도", True), ("항목(단위: 건)", True),
    ("구 분", True), ("사업장 구분", True), ("연도별", True), ("", False),
    ("교육 대상 사업장", False), ("실적 집계 사업장", False), ("목표 사업장", False), ("소속", False),
    ("소계", False), ("합계", False), ("목표", False), ("산업재해 발생 건수", False),
])
def test_axis_name_is_the_whole_first_cell(cells, axis):
    assert router._is_axis_name(cells) is axis


@pytest.mark.parametrize("cells,names", [
    (["구분", "1공장", "2공장"], True), (["", "제1공장", "제2공장"], True), (["사업장", "김해공장", "부산공장"], True),
    (["구분", "목표", "실적"], True), (["", "2025", "2026"], True),
    (["교육 대상 사업장", "김해 제1공장", "부산 제1공장"], False), (["실적 집계 사업장", "1공장", "2공장"], False),
    (["점검 대상", "본사", "해외"], False), (["소속", "김해공장", "부산공장"], False),
    (["점검 상태", "완료", "완료"], False), (["구분", "1공장", "1공장"], False),
])
def test_names_columns_needs_an_axis_first_cell(cells, names):
    assert router._names_columns(cells) is names


@pytest.mark.parametrize("row", ["점검 상태 | 완료 | 완료", "소속 | 김해공장 | 부산공장", "소속 | 김해공장 | 김해공장"])
def test_text_data_rows_keep_their_existing_behavior(row):
    metrics, _, issues = mapped("구분 | 목표 | 실적\n" + row + "\n산업재해 발생 건수 | 0 | 1")
    assert target_rejected(metrics, issues), issues


# ── R2: 각주·규격 번호는 수량이 아니고, 새 절은 상속을 끝낸다 ──────────────────

NEW_HEADINGS = [
    "2. 2026년 5월 부산 제1공장 교육 현황 [1]", "2. 2026년 5월 부산 제1공장 교육 현황 [7]",
    "2. 2026년 5월 부산 제1공장 교육 현황 (12)", "2. 2026년 5월 부산 제1공장 교육 현황 [주3]",
    "2. 2026년 5월 부산 제1공장 교육 현황 [123]", "2. 2026년 5월 부산 제1공장 교육 현황 주101)",
    "2. 2026년 5월 부산 제1공장 교육 현황 주1)", "2. 2026년 5월 부산 제1공장 교육 현황*1",
    "2. 2026년 5월 부산 제1공장 교육 현황¹", "2. 2026년 5월 부산 제1공장 교육 현황 1)",
    "2. 2026년 5월 부산 제1공장 ISO 45001 교육 현황", "2. 2026년 5월 부산 제1공장 ISO 14001 교육 현황",
    "2. 2026년 5월 부산 제1공장 ISO 45001:2018 교육 현황", "2. 2026년 5월 부산 제1공장 ISO/IEC 27001(2022) 교육 현황",
    "2. 2026년 5월 부산 제1공장 OHSAS 18001 교육 현황", "2. 2026년 5월 부산 제1공장 GRI 403-9 교육 현황",
    "2. 2026년 5월 부산 제1공장 KS Q ISO 45001 교육 현황", "Ⅱ. 2026년 5월 부산 제1공장 교육 현황 [1]",
    "2.1 2026년 5월 부산 제1공장 교육 현황 [1]", "제2절 2026년 5월 부산 제1공장 교육 현황 [1]",
    "## 2. 2026년 5월 부산 제1공장 교육 현황 [1]", "**2.** 2026년  5월 부산 제1공장 교육 현황[1]",
    "(2) 2026년 5월 부산 제1공장 교육 현황 (1)", "2) 2026년 5월 부산 제1공장 제2차 교육 현황",
    "2026년 5월 부산 제1공장 교육 현황 [1]", "교육 현황 [1]", "ISO 45001 교육 현황",
    "2026년 5월 부산 제1공장 교육 현황",
]
OLDER = [
    {},                                                                   # 검토 원문
    {"earlier": "산업재해 발생 건수 3건"},                                 # 앞 절 값 변경
    {"older": "2026년 3월 부산 제2공장 안전 현황"},                        # 앞 절 범위 변경
    {"older": "", "earlier": ""},                                          # 앞 절 없음
]
FACTS = ["산업재해 발생 건수 0건", "산업재해는 발생하지 않았다"]


@pytest.mark.parametrize("heading", NEW_HEADINGS)
@pytest.mark.parametrize("older", range(len(OLDER)))
@pytest.mark.parametrize("fact", FACTS)
def test_a_new_section_heading_ends_inheritance_whatever_its_numbers(heading, older, fact):
    result = verdict(sections(heading, fact=fact, **OLDER[older]))
    assert (result.status, result.evidence_period, result.evidence_site, result.scope_heading) == \
        ("SOURCE_ONLY", "", "", ""), result
    # 경계 문구는 새 제목 그 자체다(로마 숫자 절 머리 `Ⅱ.`는 기존 절 분리가 떼어 낸다)
    assert result.scope_boundary and heading.strip().endswith(result.scope_boundary), result
    assert "교육 현황" in result.scope_boundary and "안전 현황" not in result.scope_boundary


@pytest.mark.parametrize("heading", [
    "2. 2026년 4월 김해 제1공장 안전 현황 [1]", "2. 2026년 4월 김해 제1공장 안전 현황 (2)",
    "2026년 4월 김해 제1공장 안전 현황¹", "## 2026년 4월 김해 제1공장 안전 현황 [1]",
])
@pytest.mark.parametrize("fact", FACTS)
def test_an_applicable_footnoted_heading_gives_its_own_scope(heading, fact):
    result = verdict(sections(heading, fact=fact, older="2026년 5월 부산 제1공장 교육 현황"))
    assert (result.status, result.evidence_period, result.evidence_site) == ("CONFIRMED", "2026년 4월", "김해1공장")
    assert result.scope_heading == heading.strip() and not result.scope_boundary


@pytest.mark.parametrize("heading,cause", [
    ("2. 2026년 5월 김해 제1공장 안전 현황 [1]", "period_mismatch"),
    ("2. 2026년 4월 부산 제1공장 안전 현황 (1)", "site_mismatch"),
])
def test_an_own_footnoted_scope_that_conflicts_is_rejected_not_replaced(heading, cause):
    result = verdict(sections(heading))      # 앞 절은 요청과 맞는 4월·김해다 — 찾아가지 않는다
    assert (result.status, result.cause) == ("REJECTED", cause), result
    assert result.scope_heading == heading.strip()


def test_a_standard_revision_year_is_not_a_period():
    result = verdict(sections("2. 김해 제1공장 ISO 45001:2026 안전 현황", older="2026년 4월 김해 제1공장 안전 현황"))
    assert result.evidence_period == "" and result.status != "CONFIRMED", result


def test_a_footnoted_common_heading_still_reaches_a_sub_heading():
    quote = "2026년 4월 김해 제1공장 안전 현황 [1]\n\n1. 산업재해 [2]\n산업재해 발생 건수 0건"
    result = verdict(quote)
    assert (result.status, result.evidence_period, result.evidence_site) == ("CONFIRMED", "2026년 4월", "김해1공장")


@pytest.mark.parametrize("line", [
    "2.5 톤 감축", "1-2 명 교육 실시", "2. 교육 참석자 46명", "3) 점검 2회", "12 건 점검",
    "교육 참석자 **46**명", "ISO 3건 취득", "교육 (12명)", "Scope 3 배출량 1,200 tCO2eq", "주1회 점검",
    "교육 참석 *2명", "점검 결과 1)항 3건 보완",
])
@pytest.mark.parametrize("fact", FACTS)
def test_a_same_section_quantity_line_keeps_the_common_heading(line, fact):
    quote = "2026년 4월 김해 제1공장 안전 현황\n" + line + "\n" + fact
    result = verdict(quote)
    assert router._heading_kind(line, set(router._ZERO_HEADING_WORDS)) == "fact", line
    assert (result.status, result.evidence_period, result.evidence_site) == ("CONFIRMED", "2026년 4월", "김해1공장")


@pytest.mark.parametrize("piece,masked", [
    ("2. 2026년 5월 교육 현황 [1]", "   2026년 5월 교육 현황    "),
    ("ISO 45001:2018 교육 현황", "ISO            교육 현황"),
    ("FY2026 안전 현황", "FY2026 안전 현황"),
    ("ISO 3건 취득", "ISO 3건 취득"),
    ("교육 (12명)", "교육 (12명)"),
    ("제2차 교육 현황", "    교육 현황"),
    ("교육 현황 [123]", "교육 현황      "),
    ("교육 (100건)", "교육 (100건)"),
    ("교육 현황 (2026)", "교육 현황 (2026)"),
])
def test_heading_mask_keeps_length_and_real_quantities(piece, masked):
    assert router._heading_mask(piece) == masked


# ── 통합: 고정 원본 응답 → 라우터 → 근거 그래프 → 대표값·원장 → 확인 목록·보고서 입력 ────────

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(SETTINGS, "strict_llm", False)
    monkeypatch.setattr(SETTINGS, "force_mock", False)
    monkeypatch.setenv("ESGENIE_OCR_CACHE", "0")

    class Client:
        replies: list = []

        def complete(self, **kwargs):
            return self.replies.pop(0)
    monkeypatch.setattr("esgenie.llm.LLMClient", Client)
    return Client


def run_response(client, metric):
    body = "안전·환경 데이터\n" + metric["quote"]
    client.replies = [LLMResponse(content=json.dumps({"metrics": [metric], "clauses": []}),
                                  used_mock=False, meta={"provider": "test-double", "model": "test-double"})]
    ext = router._extract_unstructured_text("safety.pdf", doc_type="report", raw_text=body,
                                            page_texts=[(0, body)])
    graph = EvidenceGraph("LOCAL", "검토")
    merge_ocr_extraction(graph, ext, report_year=2026)
    facts = {}
    for node in graph.nodes.values():
        fact = selection._from_nodes(graph, node.metric, [node])
        if fact is not None:
            facts[node.metric] = fact
    graph.resolved_facts = facts
    findings = build_source_review(SimpleNamespace(evidence_graph=graph, ocr_extractions=[ext],
                                                   extraction=None, sections={}, item_retrievals=[]))
    return ext, graph, facts, findings, _block_source_review(SimpleNamespace(review_findings=findings))


def accident_rate(quote, period="2026-04", site="김해 제1공장"):
    return {"metric_hint": "산업재해율", "kesg_code": "S-4-2", "value": 0, "unit": "‰",
            "period": period, "quote": quote, "boundary": {"site": site} if site else {}}


@pytest.mark.parametrize("scoped", [False, True])
def test_integration_target_zero_under_a_site_data_row_is_excluded(client, scoped):
    quote = site_data_table(scoped=scoped, rate=True)
    ext, graph, facts, findings, block = run_response(
        client, accident_rate(quote, site="김해 제1공장" if scoped else ""))
    assert ext.metrics == [] and not graph.nodes and "S-4-2" not in facts
    (entry,) = ext.router_meta["unvalued_records"]
    (record,) = entry["records"]
    assert (record["status"], record["cause"], record["evidence_column"], record["column_state"]) == \
        ("REJECTED", "future_or_intent", "목표", "header")
    assert record["row_label"] == "산업재해율(‰)"
    row = quote.index("산업재해율(‰) | 0")
    assert record["evidence_offset"] == row + len("산업재해율(‰) | ")
    assert quote[record["evidence_start"]:record["evidence_end"]].strip() == "0"
    zero = [f for f in findings if f.check_reason == "zero_not_in_evidence"]
    assert zero and any("목표" in f.reason or "계획" in f.reason for f in zero), findings
    assert not [f for f in findings if f.code == "S-4-2" and f.check_reason == "scope_source_only"]


def test_integration_actual_zero_under_the_same_site_data_row_is_kept(client):
    quote = ("2026년 4월 안전 현황\n구분 | 목표 | 실적\n교육 참여 인원 | 0 | 50\n"
             "교육 대상 사업장 | 김해 제1공장 | 부산 제1공장\n산업재해율(‰) | 1 | 0")
    ext, graph, facts, findings, block = run_response(client, accident_rate(quote, site=""))
    (node,) = graph.nodes.values()
    assert (node.metric, node.value, node.unit) == ("S-4-2", 0, "‰")
    (record,) = [p for p in Boundary.from_dict(node.boundary).provenance if p.get("source") == "zero_evidence"]
    assert (record["evidence_column"], record["column_state"], record["row_label"]) == ("실적", "header", "산업재해율(‰)")
    assert record["evidence_offset"] == quote.index("산업재해율(‰) | 1 | ") + len("산업재해율(‰) | 1 | ")
    assert record["source_period"] == "2026년 4월" and record["requested_period"] == "2026-04"
    assert facts["S-4-2"].value == 0
    assert not [f for f in findings if f.check_reason == "zero_not_in_evidence"]


@pytest.mark.parametrize("heading", [
    "2. 2026년 5월 부산 제1공장 교육 현황 [1]", "2. 2026년 5월 부산 제1공장 교육 현황 (1)",
    "2. 2026년 5월 부산 제1공장 ISO 45001 교육 현황",
])
def test_integration_footnoted_new_section_zero_carries_the_scope_warning(client, heading):
    quote = sections(heading, rate=True)
    ext, graph, facts, findings, block = run_response(client, accident_rate(quote))
    (node,) = graph.nodes.values()
    assert (node.metric, node.unit, node.value) == ("S-4-2", "‰", 0)
    boundary = Boundary.from_dict(node.boundary)
    (record,) = [p for p in boundary.provenance if p.get("source") == "zero_evidence"]
    assert (record["status"], record["source_period"], record["source_site"], record["scope_heading"]) == \
        ("SOURCE_ONLY", "", "", "")
    assert record["scope_boundary"] == heading
    assert record["requested_period"] == "2026-04"        # 요청 범위는 원문 범위와 따로 남는다
    assert boundary.period_text not in ("2026년 4월", "2026년 5월")
    assert "안전 현황" not in json.dumps(record, ensure_ascii=False)    # 앞 절이 근거로 나가지 않는다
    fact = facts["S-4-2"]
    assert fact.value == 0 and "scope_source_only" in fact.flags
    scope = [f for f in findings if f.check_reason == "scope_source_only"]
    assert scope and scope[0].code == "S-4-2", [(f.code, f.check_reason) for f in findings]
    assert block is not None and "범위 미확정" in block.body_md


def test_integration_own_scope_zero_is_confirmed_without_a_warning(client):
    quote = sections("2. 2026년 4월 김해 제1공장 안전 현황 [1]", rate=True, older="2026년 5월 부산 제1공장 교육 현황")
    ext, graph, facts, findings, block = run_response(client, accident_rate(quote))
    (node,) = graph.nodes.values()
    assert (node.metric, node.unit, node.value) == ("S-4-2", "‰", 0)
    (record,) = [p for p in Boundary.from_dict(node.boundary).provenance if p.get("source") == "zero_evidence"]
    assert (record["status"], record["source_period"], record["source_site"]) == ("CONFIRMED", "2026년 4월", "김해1공장")
    assert record["scope_heading"] == "2. 2026년 4월 김해 제1공장 안전 현황 [1]" and not record["scope_boundary"]
    fact = facts["S-4-2"]
    assert fact.value == 0 and "scope_source_only" not in fact.flags
    assert not [f for f in findings if f.code == "S-4-2" and f.check_reason in ("scope_source_only", "zero_not_in_evidence")]
