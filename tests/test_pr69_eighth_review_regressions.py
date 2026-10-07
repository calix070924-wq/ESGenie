"""PR 69 8차 검토(2026-10-03, 4dc849d)에서 찾은 판정 전 정규화의 두 결함 회귀 테스트.

공통 원인은 판정용 문구를 만들면서 원문의 의미 있는 정보를 지운 것이다.

R1 `_is_axis_name`이 괄호를 모두 지워 `사업장(교육 대상)`이 축 이름 `사업장`이 됐다. 데이터 행이 새
   머리글이 되어 아래 `산업재해 | 0 | 1`의 목표 0이 실적으로 채택됐다(588f0ea에도 있던 결함).
R2 `_heading_mask`가 `A2공장`의 `A2`를 규격 번호로 가려 자기 사업장을 잃었고, 위 머리말의 `김해
   제1공장`을 물려받아 CONFIRMED가 됐다. 정상 `A1공장`·`B2사업장`의 범위 확정도 깨졌다(4dc849d 회귀).

검토 산출물의 독립 검사 10건(`output/reviews/pr69_20261003/eighth_review/test_pr69_semantic_masking.py`)을
개인 경로·`REVIEW_CODE` 없이 옮기고, 두 규칙이 충돌하는 지점의 의미 보존 대조와 원본 응답 → 원장 →
확인 목록·보고서 입력 통합 검사를 붙였다. 기대값은 원문 기준으로 적었다. 모든 문구는 합성 입력이다.
"""
from __future__ import annotations

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


def verdict(quote, site="김해 제1공장", hint="산업재해 발생 건수", period="2026-04"):
    return router._zero_verdict(quote, hint, period, {"site": site} if site else {})


def ledger(quote):
    """검토 산출물과 같은 경로: 매핑 → 근거 그래프 → 대표값 → 확인 목록."""
    issues = []
    metrics, clauses = router._map_vlm_json(
        {"metrics": [dict(metric_hint="산업재해율", value=0, unit="‰", period="2026-04",
                          quote=quote, boundary={"site": "김해 제1공장"})]},
        source_text=quote, page_no=0, issues=issues,
    )
    extraction = router.OcrExtraction(
        source_file="synthetic-semantic-mask-review.pdf", channel=router.DocChannel.UNSTRUCTURED,
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


# ── 검토 산출물 10건(R1 4 · R2 4 · 정상 대조 2) ─────────────────────────────

def target_table(label, rate=False, roles=("목표", "실적"), values=("0", "1")):
    metric = "산업재해율(‰)" if rate else "산업재해 발생 건수"
    return (f"2026년 4월 안전 현황\n구분 | {roles[0]} | {roles[1]}\n교육 참여 인원 | 0 | 50\n"
            f"{label} | 김해 제1공장 | 부산 제1공장\n{metric} | {values[0]} | {values[1]}")


@pytest.mark.parametrize("label", ["사업장(교육 대상)", "사업장（교육 대상）"])
def test_parenthesized_data_qualifier_does_not_disappear(label):
    result = verdict(target_table(label))
    assert (result.status, result.cause, result.evidence_column) == \
        ("REJECTED", "future_or_intent", "목표"), result


def test_nonparenthesized_data_label_control():
    result = verdict(target_table("교육 대상 사업장"))
    assert (result.status, result.cause, result.evidence_column) == \
        ("REJECTED", "future_or_intent", "목표"), result


def test_real_unit_annotation_header_control():
    quote = ("구분 | 목표 | 실적\n교육 참여 인원 | 0 | 50\n"
             "항목(단위: 건) | 1공장 | 2공장\n산업재해 발생 건수 | 0 | 1")
    result = verdict(quote, site="")
    assert (result.status, result.evidence_column) == ("SOURCE_ONLY", "1공장"), result


@pytest.mark.parametrize("site", ["A1공장", "B2사업장"])
def test_alphanumeric_site_stays_confirmed_in_its_own_heading(site):
    result = verdict(f"2026년 4월 {site} 안전 현황\n산업재해 발생 건수 0건", site)
    assert (result.status, result.evidence_site) == ("CONFIRMED", site), result


def child_site_quote(rate=False, parent="2026년 4월 김해 제1공장", child="A2공장 안전 현황"):
    statement = "산업재해율(‰) 0" if rate else "산업재해 발생 건수 0건"
    return (parent + "\n" if parent else "") + child + "\n" + statement


def test_explicit_child_site_cannot_be_replaced_by_parent_site():
    result = verdict(child_site_quote())
    assert (result.status, result.cause, result.evidence_site) == \
        ("REJECTED", "site_mismatch", "A2공장"), result


def test_korean_numbered_site_control():
    result = verdict("2026년 4월 김해 제1공장 안전 현황\n산업재해 발생 건수 0건")
    assert (result.status, result.evidence_site) == ("CONFIRMED", "김해1공장"), result


def test_parenthesized_target_is_excluded_from_s42():
    facts, findings, metrics, issues = ledger(target_table("사업장(교육 대상)", rate=True))
    assert "S-4-2" not in facts, (facts, findings, issues)


def test_wrong_child_site_is_excluded_from_s42():
    facts, findings, metrics, issues = ledger(child_site_quote(rate=True))
    assert "S-4-2" not in facts, (facts, findings, issues)


# ── R1: 괄호는 내용 전체가 단위·기간·열 머리 표기일 때만 뗀다 ───────────────────

@pytest.mark.parametrize("inner,unit", [
    ("단위: 건", True), ("단위：건", True), ("단위 : 건", True), (" 건 ", True), ("%", True),
    ("단위: 명, %", True), ("단위: tCO2eq", True), ("단위: 개소", True),
    ("2026년", True), ("2026", True), ("2026년 4월", True), ("국내", True), ("국내, 별도", True),
    ("교육 대상", False), ("집계 범위", False), ("단위: 건, 교육 대상", False), ("교육 대상, 건", False),
    ("2026 목표", False), ("목표", False), ("2026년 교육 대상", False), ("1공장", False),
    ("단위", False), ("", False),
])
def test_axis_annotation_is_the_whole_parenthesis(inner, unit):
    assert router._is_axis_annotation(inner) is unit


@pytest.mark.parametrize("cell,axis", [
    # 단위 괄호 — 떼고 축 이름
    ("항목(단위: 건)", True), ("항목（단위：건）", True), ("구분 (단위 : 건)", True), ("구분(건)", True),
    ("구분(단위: 명, %)", True), ("사업장(단위: 개소)", True), ("구분(%)(건)", True),
    # 설명 괄호 — 데이터 행의 라벨
    ("사업장(교육 대상)", False), ("사업장（교육 대상）", False), ("사업장 ( 교육 대상 )", False),
    ("사업장(집계 범위)", False), ("사업장(단위: 건, 교육 대상)", False), ("구분(%)(교육 대상)", False),
    ("사업장((교육) 대상)", False), ("사업장(교육(대상))", False), ("사업장(단위(건))", False),
    # 짝이 맞지 않는 괄호는 지워서 축 이름을 만들지 않는다
    ("사업장(교육 대상", False), ("사업장 교육 대상)", False), ("사업장)(", False),
    # 괄호 없는 대조
    ("교육 대상 사업장", False), ("사업장", True), ("사업장명", True),
])
def test_axis_name_keeps_descriptions_in_parentheses(cell, axis):
    assert router._is_axis_name(cell) is axis


R1_LABELS = ["교육 대상 사업장", "사업장(교육 대상)", "사업장（교육 대상）", "사업장 (교육 대상)",
             "사업장( 교육 대상 )", "사업장(집계 범위)", "사업장(단위: 건, 교육 대상)",
             "사업장(교육(대상))", "사업장(교육 대상", "사업장 교육 대상)"]


@pytest.mark.parametrize("roles,values,kept", [
    (("목표", "실적"), ("0", "1"), None),
    (("목표", "실적"), ("1", "0"), 2),
    (("목표", "실적"), ("0", "0"), 2),
    (("실적", "목표"), ("1", "0"), None),
    (("실적", "목표"), ("0", "1"), 1),
    (("실적", "목표"), ("0", "0"), 1),
])
@pytest.mark.parametrize("label", R1_LABELS)
@pytest.mark.parametrize("scoped_site", ["", "김해 제1공장"])
def test_a_described_site_row_keeps_the_real_column_roles(label, roles, values, kept, scoped_site):
    """괄호 안·밖의 설명은 같은 뜻이다. 목표 0은 거부하고, 실적 0은 **실적 칸**을 근거로 보존한다."""
    quote = target_table(label, roles=roles, values=values)
    result = verdict(quote, site=scoped_site)
    if kept is None:
        assert (result.status, result.cause, result.evidence_column) == \
            ("REJECTED", "future_or_intent", "목표"), result
        return
    # 데이터 행의 사업장 값은 열 범위가 아니다 — 사업장을 요청하면 원문 사업장 미기재(SOURCE_ONLY)
    expected = ("SOURCE_ONLY", "site_not_stated") if scoped_site else ("CONFIRMED", "stated_zero")
    assert (result.status, result.cause, result.evidence_column, result.evidence_site) == (*expected, "실적", ""), result
    row = quote.splitlines()[-1]
    cell = quote.rindex(row) + sum(len(c) + 1 for c in row.split("|")[:kept])
    assert result.evidence_start >= cell - 1, result      # 근거는 실적 칸이지 목표 칸이 아니다
    assert quote[result.evidence_start:result.evidence_end].strip() == "0"


@pytest.mark.parametrize("label", R1_LABELS)
def test_a_described_label_gives_the_same_verdict_as_the_plain_label(label):
    plain = verdict(target_table("교육 대상 사업장"))
    other = verdict(target_table(label))
    assert (other.status, other.cause, other.evidence_column, other.evidence_offset - len(label)) == \
        (plain.status, plain.cause, plain.evidence_column, plain.evidence_offset - len("교육 대상 사업장"))


@pytest.mark.parametrize("header", ["항목(단위: 건)", "구분（단위：건）", "구분 (단위 : 건)", "항목(건)",
                                    "사업장(단위: 개소)", "구분(2026년)", "구분(2026)", "구분(국내)",
                                    "구분", "사업장"])
def test_a_unit_annotated_new_header_still_resets_the_roles(header):
    quote = (f"구분 | 목표 | 실적\n교육 참여 인원 | 0 | 50\n{header} | 1공장 | 2공장\n"
             "산업재해 발생 건수 | 0 | 1")
    result = verdict(quote, site="")
    assert (result.status, result.evidence_column, result.evidence_site) == ("SOURCE_ONLY", "1공장", "1공장"), result


@pytest.mark.parametrize("header", ["구분(2026 목표)", "구분(목표)", "사업장(2026년 교육 대상)"])
def test_a_role_or_description_in_the_axis_cell_is_not_erased(header):
    """괄호 안의 목표·설명을 지워 새 머리글을 만들지 않는다 — 앞 머리글의 목표 열 역할이 남는다."""
    quote = (f"구분 | 목표 | 실적\n교육 참여 인원 | 0 | 50\n{header} | 1공장 | 2공장\n"
             "산업재해 발생 건수 | 0 | 1")
    result = verdict(quote, site="")
    assert (result.status, result.cause, result.evidence_column) == ("REJECTED", "future_or_intent", "목표"), result


def test_the_model_zero_is_never_swapped_for_another_cell():
    """목표 0 / 실적 1에서 모델의 0을 실적 1로 바꿔 싣지 않는다 — 0을 거부할 뿐이다."""
    issues = []
    quote = target_table("사업장(교육 대상)")
    metrics, _ = router._map_vlm_json(
        {"metrics": [dict(metric_hint="산업재해 발생 건수", value=0, unit="건", period="2026-04", quote=quote)]},
        source_text=quote, page_no=0, issues=issues)
    assert metrics == [] and any(i.get("cause") == "future_or_intent" for i in issues), (metrics, issues)


# ── R2: 규격 번호를 가려도 원문 사업장은 그대로 ─────────────────────────────

@pytest.mark.parametrize("piece,masked", [
    ("A2공장 안전 현황", "A2공장 안전 현황"),
    ("B2사업장 안전 현황", "B2사업장 안전 현황"),
    ("K2센터 안전 현황", "K2센터 안전 현황"),
    ("Plant2공장 안전 현황", "Plant2공장 안전 현황"),
    ("2026년 4월 A-1공장", "2026년 4월 A-1공장"),
    ("GRI 403-9 A1공장", "GRI       A1공장"),
    ("2026년 4월 A1공장 ISO 45001:2018 안전 현황", "2026년 4월 A1공장 ISO            안전 현황"),
    ("ISO/IEC 27001(2022) B2사업장 현황 [1]", "ISO/IEC             B2사업장 현황    "),
    # 번호가 날짜로 시작하면 기간이다 — 개정 연도(`:2018`)만 가린다
    ("FY 2026.04 안전 현황", "FY 2026.04 안전 현황"),
    ("FY2026.4 안전 현황", "FY2026.4 안전 현황"),
    ("ISO 9001:2015 2026년 4월 김해 제1공장", "ISO           2026년 4월 김해 제1공장"),
    # 7차 규칙 유지
    ("ISO 45001:2018 교육 현황", "ISO            교육 현황"),
    ("FY2026 안전 현황", "FY2026 안전 현황"),
    ("교육 현황 [123]", "교육 현황      "),
    ("2. 2026년 4월 김해 제1공장 안전 현황 [1]", "   2026년 4월 김해 제1공장 안전 현황    "),
])
def test_heading_mask_never_covers_a_source_site(piece, masked):
    assert router._heading_mask(piece) == masked
    assert len(router._heading_mask(piece)) == len(piece)


OWN_SITES = [("A1공장", "A1공장"), ("B2사업장", "B2사업장"), ("K2센터", "K2센터"),
             ("Plant2공장", "Plant2공장"), ("김해 제1공장", "김해1공장"), ("A2공장", "A2공장")]
PARENTS = ["", "2026년 4월", "2026년 4월 김해 제1공장", "2026년 4월 부산 제2공장", "2026년 4월 C3공장"]
FACTS = ["산업재해 발생 건수 0건", "산업재해는 발생하지 않았다"]


@pytest.mark.parametrize("site,key", OWN_SITES)
@pytest.mark.parametrize("own_period", [True, False])
@pytest.mark.parametrize("parent", PARENTS)
@pytest.mark.parametrize("fact", FACTS)
def test_an_own_site_decides_whatever_the_parent_heading(site, key, own_period, parent, fact):
    """자기 머리말에 사업장이 있으면 위 머리말을 바꾸거나 없애도 그 사업장으로 대조한다."""
    if not own_period and "2026년 4월" not in parent:
        parent = ("2026년 4월 " + parent).strip()        # 기간은 위에서만 받는다(사업장과 별개)
    child = ("2026년 4월 " if own_period else "") + site + " 안전 현황"
    quote = (parent + "\n" if parent else "") + child + "\n" + fact
    same = verdict(quote, site=site)
    assert (same.status, same.evidence_site, same.evidence_period) == ("CONFIRMED", key, "2026년 4월"), same
    other = "부산 제9공장"
    wrong = verdict(quote, site=other)
    assert (wrong.status, wrong.cause, wrong.evidence_site) == ("REJECTED", "site_mismatch", key), wrong


@pytest.mark.parametrize("child", ["A2공장 안전 현황", "2026년 4월 A2공장 안전 현황", "A2공장 안전 현황 [1]"])
@pytest.mark.parametrize("parent", ["2026년 4월 김해 제1공장", "2026년 4월 김해 제1공장 안전 현황", ""])
@pytest.mark.parametrize("fact", FACTS)
def test_a_child_site_is_rejected_not_replaced_by_the_parent(child, parent, fact):
    result = verdict(child_site_quote(parent=parent, child=child).replace("산업재해 발생 건수 0건", fact))
    assert (result.status, result.cause, result.evidence_site) == ("REJECTED", "site_mismatch", "A2공장"), result
    assert "김해" not in result.evidence_site


@pytest.mark.parametrize("parent", ["2026년 4월 김해 제1공장", "2026년 4월 김해 제1공장 안전 보건"])
@pytest.mark.parametrize("child", ["안전 현황", "안전 현황 [1]", "2. 안전 현황"])
@pytest.mark.parametrize("fact", FACTS)
def test_a_child_without_its_own_site_still_inherits(parent, child, fact):
    result = verdict(parent + "\n" + child + "\n" + fact)
    assert (result.status, result.evidence_site, result.evidence_period) == \
        ("CONFIRMED", "김해1공장", "2026년 4월"), result


@pytest.mark.parametrize("heading,expected", [
    ("2026년 4월 A1공장 ISO 45001:2018 안전 현황", ("CONFIRMED", "stated_zero", "2026년 4월", "A1공장")),
    ("A1공장 ISO 45001:2018 안전 현황", ("SOURCE_ONLY", "period_not_stated", "", "A1공장")),
    ("GRI 403-9 A2공장 안전 현황", ("REJECTED", "site_mismatch", "", "A2공장")),
])
@pytest.mark.parametrize("fact", FACTS)
def test_site_period_and_code_in_one_heading_keep_their_roles(heading, expected, fact):
    """규격 식별자가 붙은 실적 제목은 새 절이다 — 개정 연도가 기간이 되지 않고, 위 머리말 범위도 물려받지 않는다.

    기능 변경(2026-10-05 §5 C3, `docs/수치범위_최종출력_검증기록_2026-10-05.md`): 종전 기대값은 세 제목 모두
    `SOURCE_ONLY`·사업장 없음이었다. 그 계약은 앞 절(4월·김해) 차용을 막으려고 규격 제목 전체를 다른 대상의
    제목으로 버려 제목에 직접 적힌 A1공장·A2공장·2026년 4월까지 잃었다. 규격 이름·번호를 뗀 나머지가 이 지표의
    범위 낱말(`안전 현황`)뿐이면 그 제목의 사업장·기간은 아래 값에 적용된다. 차용 금지(경계 = 그 제목)는 유지한다.
    반례 대조는 `tests/test_numeric_scope_output_20261005.py`(다른 대상 규격 제목·목차·단순 인용·상위 문맥)에 있다.
    """
    quote = "2026년 4월 김해 제1공장 안전 현황\n산업재해 발생 건수 1건\n\n" + heading + "\n" + fact
    result = verdict(quote, site="A1공장")
    assert (result.status, result.cause, result.evidence_period, result.evidence_site) == expected, result
    assert result.scope_boundary == heading and "2018" not in result.evidence_period
    assert "김해" not in result.evidence_site


# 기능 변경(2026-10-05 §5 C2): `FY2026`은 달력 연도 2026이 아니라 구간 미정의 회계연도 표기다(종전 기대값 "2026").
# 월이 붙은 `FY 2026.04`는 종전대로 실제 월이다.
@pytest.mark.parametrize("piece,period", [
    ("FY 2026.04 김해 제1공장 안전 현황", "2026.04"), ("FY2026 김해 제1공장 안전 현황", "FY2026"),
    ("2026년 4월 A1공장 ISO 45001:2018 안전 현황", "2026년 4월"), ("ISO 45001:2018 안전 현황", None),
    ("ISO/IEC 27001(2022) B2사업장 현황", None),
])
def test_heading_mask_keeps_real_periods_and_hides_revision_years(piece, period):
    spans = [s.text for s in router._date_spans(router._heading_mask(piece))]
    assert spans == ([period] if period else []), spans


def test_a_revision_year_is_still_not_a_period_next_to_a_site():
    result = verdict("A1공장 ISO 45001:2018\n산업재해 발생 건수 0건", site="A1공장", period="2018")
    assert result.status != "CONFIRMED" and "2018" not in result.evidence_period, result


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


def rejected_record(ext):
    (entry,) = ext.router_meta["unvalued_records"]
    (record,) = entry["records"]
    return record


@pytest.mark.parametrize("label", ["사업장(교육 대상)", "사업장（교육 대상）"])
def test_integration_parenthesized_target_zero_is_excluded(client, label):
    quote = target_table(label, rate=True)
    ext, graph, facts, findings, block = run_response(client, accident_rate(quote))
    assert ext.metrics == [] and not graph.nodes and "S-4-2" not in facts
    record = rejected_record(ext)
    assert (record["status"], record["cause"], record["evidence_column"], record["column_state"],
            record["row_label"]) == ("REJECTED", "future_or_intent", "목표", "header", "산업재해율(‰)")
    row = quote.index("산업재해율(‰) | 0")
    assert record["evidence_offset"] == row + len("산업재해율(‰) | ")      # 자기 셀(목표 칸)
    zero = [f for f in findings if f.check_reason == "zero_not_in_evidence"]
    assert zero and any("목표" in f.reason or "계획" in f.reason for f in zero), findings
    assert block is not None and zero[0].title in block.body_md      # 보고서 입력의 근거 확인 영역


def test_integration_parenthesized_label_actual_zero_is_kept(client):
    quote = target_table("사업장(교육 대상)", rate=True, values=("1", "0"))
    ext, graph, facts, findings, block = run_response(client, accident_rate(quote, site=""))
    (node,) = graph.nodes.values()
    assert (node.metric, node.value, node.unit) == ("S-4-2", 0, "‰")
    (record,) = [p for p in Boundary.from_dict(node.boundary).provenance if p.get("source") == "zero_evidence"]
    assert (record["evidence_column"], record["column_state"], record["row_label"]) == ("실적", "header", "산업재해율(‰)")
    assert record["evidence_offset"] == quote.index("산업재해율(‰) | 1 | ") + len("산업재해율(‰) | 1 | ")
    assert record["source_period"] == "2026년 4월"
    assert facts["S-4-2"].value == 0
    assert not [f for f in findings if f.check_reason == "zero_not_in_evidence"]


@pytest.mark.parametrize("parent", ["2026년 4월 김해 제1공장", "2026년 4월 김해 제1공장 안전 현황"])
def test_integration_child_site_conflict_is_excluded_with_its_source_site(client, parent):
    quote = child_site_quote(rate=True, parent=parent)
    ext, graph, facts, findings, block = run_response(client, accident_rate(quote))
    assert ext.metrics == [] and not graph.nodes and "S-4-2" not in facts
    record = rejected_record(ext)
    assert (record["status"], record["cause"], record["evidence_site"]) == ("REJECTED", "site_mismatch", "A2공장")
    assert record["scope_heading"].startswith("A2공장 안전 현황")
    zero = [f for f in findings if f.check_reason == "zero_not_in_evidence"]
    assert zero and any("사업장" in f.reason for f in zero), [(f.check_reason, f.reason) for f in findings]
    assert block is not None and zero[0].title in block.body_md
    assert not [f for f in findings if f.code == "S-4-2" and f.check_reason == "scope_source_only"]


@pytest.mark.parametrize("site,key", [("A1공장", "A1공장"), ("B2사업장", "B2사업장")])
def test_integration_own_alphanumeric_site_zero_is_confirmed_without_a_warning(client, site, key):
    quote = f"2026년 4월 김해 제1공장\n2026년 4월 {site} 안전 현황\n산업재해율(‰) 0"
    ext, graph, facts, findings, block = run_response(client, accident_rate(quote, site=site))
    (node,) = graph.nodes.values()
    assert (node.metric, node.unit, node.value) == ("S-4-2", "‰", 0)
    (record,) = [p for p in Boundary.from_dict(node.boundary).provenance if p.get("source") == "zero_evidence"]
    assert (record["status"], record["source_period"], record["source_site"]) == ("CONFIRMED", "2026년 4월", key)
    fact = facts["S-4-2"]
    assert fact.value == 0 and "scope_source_only" not in fact.flags
    assert not [f for f in findings if f.code == "S-4-2" and f.check_reason in ("scope_source_only", "zero_not_in_evidence")]
