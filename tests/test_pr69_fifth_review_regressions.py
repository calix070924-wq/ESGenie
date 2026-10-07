"""PR 69 5차 검토(2026-10-02, 47a2990)에서 찾은 표·셀·절 경계의 세 결함 회귀 테스트.

F1 숫자가 없는 문자형 데이터 행(`점검 상태 | 완료 | 완료`)이 머리글로 인정돼 실제 `목표 | 실적`
   머리글을 덮었다 — 목표 0이 S-4-2 실적 대표값에 남았다.
F2 구조화 셀(`_attach_column_headers`의 `0(합계|2026)`)의 연도를 읽고도 행 전체의 다른 셀
   (`1(합계|2025)`)을 서술·기간으로 다시 읽어 2026의 정상 0이 지워졌다.
F3 적용할 수 없는 새 절 머리말(`2026년 5월 부산 제1공장 교육 현황`)을 건너뛰고 앞 절의
   `2026년 4월 김해 제1공장 안전 현황`까지 거슬러 올라가 0이 CONFIRMED가 됐다.

검토 산출물의 독립 재현 12건(`output/reviews/pr69_20261002/fifth_review/test_pr69_cell_and_scope_boundaries.py`)을
개인 경로·`REVIEW_CODE` 없이 옮기고, 행 삽입·삭제·재배열, 셀 위치·이웃 셀 변경, 앞 절 변경·제거에
대한 불변성 검사와 정상 대조군, 원본 응답 → 원장 → 확인 목록 통합 검사를 붙였다. 모든 문구는 합성 입력이다.
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


def verdict(quote, hint="산업재해 발생 건수", period="2026-04", site=""):
    return router._zero_verdict(quote, hint, period, {"site": site} if site else {})


def zero_record(metric):
    (record,) = [p for p in metric.boundary.get("provenance", []) if p.get("source") == "zero_evidence"]
    return record


def ledger(quote, site=""):
    metrics, clauses, issues = mapped(quote, hint="산업재해율", unit="‰", site=site)
    extraction = router.OcrExtraction(
        source_file="synthetic-review.pdf", channel=router.DocChannel.UNSTRUCTURED,
        doc_type="report", metrics=metrics, clauses=clauses, raw_text=quote,
    )
    graph = EvidenceGraph("LOCAL", "검토")
    merge_ocr_extraction(graph, extraction, report_year=2026)
    facts = {node.metric: selection._from_nodes(graph, node.metric, [node]) for node in graph.nodes.values()}
    graph.resolved_facts = facts
    findings = build_source_review(SimpleNamespace(
        evidence_graph=graph, ocr_extractions=[extraction], extraction=None,
        sections={}, item_retrievals=[],
    ))
    return facts, findings, issues


# ── 검토 산출물 12건(실패 8 · 정상 대조 4) ─────────────────────────────────

def mixed_table(text="완료", metric="산업재해 발생 건수"):
    return ("구분 | 목표 | 실적\n교육 참여 인원 | 0 | 50\n"
            f"점검 상태 | {text} | {text}\n{metric} | 0 | 1")


@pytest.mark.parametrize("text", ["완료", "이행"])
def test_text_data_row_cannot_replace_the_target_header(text):
    metrics, _, issues = mapped(mixed_table(text))
    assert not metrics, [(m.value, m.boundary) for m in metrics]
    assert issues[0]["cause"] == "future_or_intent", issues


def generated_row(order, values=None, label="산업재해율(‰)", aggregate="합계"):
    """제품의 `_attach_column_headers`로 연도 행 / 집계 행 / 값 행을 거친 값 행 문구."""
    positions = [100 * (i + 1) for i in range(len(order))]
    values = values or {y: ("0" if y == 2026 else "1") for y in order}
    rows = [
        list(zip(positions, map(str, order))),
        [(0, "구분"), *[(p, aggregate) for p in positions]],
        [(0, label), *[(p, values[y]) for p, y in zip(positions, order)]],
    ]
    return " | ".join(t for _, t in router._attach_column_headers(rows)[-1])


@pytest.mark.parametrize("order", [(2025, 2026), (2026, 2025)])
def test_zero_uses_its_own_attached_year_not_other_cells(order):
    quote = generated_row(order)
    assert "0(합계|2026)" in quote and "1(합계|2025)" in quote
    metrics, _, issues = mapped(quote, hint="산업재해율", unit="‰", period="2026")
    assert [m.value for m in metrics] == [0], (quote, issues)
    record = zero_record(metrics[0])
    assert (record["source_period"], record["status"]) == ("2026", "SOURCE_ONLY"), record


def separated_sections(fact="산업재해 발생 건수 0건", earlier="산업재해 발생 건수 1건",
                       older="2026년 4월 김해 제1공장 안전 현황", new="2026년 5월 부산 제1공장 교육 현황"):
    head = f"{older}\n{earlier}\n\n" if older is not None else ""
    return f"{head}{new}\n{fact}"


@pytest.mark.parametrize("fact", ["산업재해 발생 건수 0건", "산업재해는 발생하지 않았다"])
def test_new_section_blocks_inheritance_from_an_older_heading(fact):
    result = verdict(separated_sections(fact), site="김해 제1공장")
    assert result.status == "SOURCE_ONLY", result
    assert not result.evidence_period and not result.evidence_site, result


def test_mixed_table_target_zero_does_not_reach_the_actual_ledger():
    facts, _findings, issues = ledger(mixed_table(metric="산업재해율(‰)"))
    assert "S-4-2" not in facts, facts
    assert issues and issues[0]["cause"] == "future_or_intent", issues


def test_cross_section_scope_stays_unconfirmed_in_the_ledger_and_review():
    quote = separated_sections("산업재해율(‰) 0", "산업재해율(‰) 1")
    facts, findings, issues = ledger(quote, "김해 제1공장")
    assert "S-4-2" in facts and facts["S-4-2"].value == 0, (facts, issues)
    assert "scope_source_only" in facts["S-4-2"].flags, facts["S-4-2"]
    assert any(f.code == "S-4-2" and f.check_reason == "scope_source_only" for f in findings), findings


@pytest.mark.parametrize("hint,unit", [("온실가스 배출량", "tCO2eq"), ("장기차입금", "백만원")])
def test_supported_zero_unit_control(hint, unit):
    metrics, _, issues = mapped(f"2026년 4월 {hint} 0 {unit}", hint=hint, unit=unit)
    assert [m.value for m in metrics] == [0] and not issues, issues


def test_numeric_data_row_control():
    quote = "구분 | 목표 | 실적\n교육 참여 인원 | 0 | 50\n산업재해 발생 건수 | 0 | 1"
    metrics, _, issues = mapped(quote)
    assert not metrics and issues[0]["cause"] == "future_or_intent", (metrics, issues)


def test_adjacent_valid_heading_control():
    quote = "2026년 4월 김해 제1공장 안전 현황\n산업재해 발생 건수 0건"
    assert verdict(quote, site="김해 제1공장").status == "CONFIRMED"


# ── F1 — 머리글은 열 역할·연도·축 또는 표 구조로 확인한다 ─────────────────────

TARGET_ACTUAL = "구분 | 목표 | 실적"
ACTUAL_TARGET = "구분 | 실적 | 목표"
# 숫자·상태·일반 문자형 데이터 행. 문자형 행은 숫자가 없어도 데이터다.
MIXED_ROWS = ["교육 참여 인원 | 0 | 50", "점검 상태 | 완료 | 완료", "담당 부서 | 안전팀 | 안전팀",
              "이행 여부 | 이행 | 이행", "협력사 평가 | 12 | 10"]


def accident_row(target, actual, header=TARGET_ACTUAL, metric="산업재해 발생 건수"):
    cells = (target, actual) if header == TARGET_ACTUAL else (actual, target)
    return f"{metric} | {cells[0]} | {cells[1]}"


ARRANGEMENTS = [(), (1,), (1, 2), (0, 1), (1, 0), (2, 3, 1), (3, 0, 2, 1, 4), (4, 1, 3)]


@pytest.mark.parametrize("arrangement", ARRANGEMENTS, ids=lambda a: "-".join(map(str, a)) or "none")
@pytest.mark.parametrize("header", [TARGET_ACTUAL, ACTUAL_TARGET], ids=["target_first", "actual_first"])
@pytest.mark.parametrize("target,actual,status,column", [
    (0, 1, "REJECTED", "목표"), (1, 0, "SOURCE_ONLY", "실적"), (0, 0, "SOURCE_ONLY", "실적"),
])
def test_text_rows_keep_the_role_of_the_target_cell(target, actual, status, column, header, arrangement):
    """숫자·문자형 행의 삽입·삭제·재배열에도 대상 셀의 목표/실적 역할이 유지된다."""
    rows = [MIXED_ROWS[i] for i in arrangement]
    quote = "\n".join([header, *rows, accident_row(target, actual, header)])
    result = verdict(quote)
    assert (result.status, result.evidence_column, result.column_state) == (status, column, "header"), \
        (quote, result)
    if status == "REJECTED":
        assert result.cause == "future_or_intent"


@pytest.mark.parametrize("text", ["완료", "이행", "안전팀"])
def test_a_text_row_placed_between_header_and_target_is_never_the_header(text):
    for position in range(3):
        rows = ["교육 참여 인원 | 0 | 50", "협력사 평가 | 12 | 10"]
        rows.insert(position, f"점검 상태 | {text} | {text}")
        quote = "\n".join([TARGET_ACTUAL, *rows, accident_row(0, 1)])
        result = verdict(quote)
        assert (result.cause, result.evidence_column) == ("future_or_intent", "목표"), (quote, result)


def test_a_real_new_header_replaces_the_previous_roles():
    """대조군: 같은 표 안에서 실제 새 머리글(열 역할 낱말)이 나오면 그 역할로 갱신한다."""
    quote = "구분 | 목표 | 실적\n교육 참여 인원 | 0 | 50\n구분 | 실적 | 목표\n산업재해 발생 건수 | 0 | 1"
    result = verdict(quote)
    assert (result.status, result.evidence_column) == ("SOURCE_ONLY", "실적"), result


@pytest.mark.parametrize("second", [
    "항목 | 2025 | 2026\n점검 상태 | 완료 | 완료\n산업재해 발생 건수 | 3 | 0",
    "항목 | 2025 | 2026\n산업재해 발생 건수 | 3 | 0",
])
def test_a_new_table_does_not_inherit_the_target_role(second):
    for join in ("\n\n", "\n"):
        quote = "구분 | 목표 | 실적\n점검 상태 | 완료 | 완료\n교육 참여 인원 | 0 | 50" + join + second
        result = verdict(quote, period="2026")
        assert (result.status, result.cause, result.evidence_column) == \
            ("SOURCE_ONLY", "report_year_only", "2026"), (quote, result)


def test_a_new_table_with_no_header_has_no_column_role():
    quote = "구분 | 목표 | 실적\n교육 참여 인원 | 0 | 50\n\n교육 참여 인원 | 5 | 6\n산업재해 발생 건수 | 0 | 1"
    result = verdict(quote)
    assert result.accepted and (result.column_state, result.evidence_column) == ("none", ""), result


@pytest.mark.parametrize("quote,column", [
    ("구분 | 지하수 | 상수도\n교육 참여 인원 | 5 | 6\n산업재해 발생 건수 | 0 | 1", "지하수"),
    ("항목 | 지하수 | 상수도\n--- | --- | ---\n점검 상태 | 완료 | 완료\n산업재해 발생 건수 | 0 | 1", "지하수"),
])
def test_a_structural_header_without_role_words_is_still_a_header(quote, column):
    """표의 첫 행·구분선 위 행은 역할 낱말이 없어도 머리글이다(기존 표 형식 보존)."""
    result = verdict(quote)
    assert (result.column_state, result.evidence_column) == ("header", column), result


def test_a_multi_line_header_survives_text_rows():
    quote = ("구분 | 2026 | 2026\n항목 | 목표 | 실적\n점검 상태 | 완료 | 완료\n교육 참여 인원 | 0 | 50\n"
             "산업재해 발생 건수 | 0 | 1")
    result = verdict(quote, period="2026")
    assert (result.status, result.cause, result.evidence_column) == ("REJECTED", "future_or_intent", "2026 목표")


# ── F2 — 구조화 셀은 자기 셀·행 라벨·연결된 머리글로 판정한다 ─────────────────

@pytest.mark.parametrize("position", [0, 1, 2], ids=["first", "middle", "last"])
@pytest.mark.parametrize("others", [("1", "2"), ("0.4", "-"), ("12", "0")], ids=["ints", "dash", "zero"])
def test_the_target_cell_position_and_neighbours_do_not_change_the_verdict(position, others):
    years = [2024, 2025]
    years.insert(position, 2026)
    values = dict(zip([y for y in years if y != 2026], others))
    values[2026] = "0"
    quote = generated_row(tuple(years), values)
    assert "0(합계|2026)" in quote, quote
    result = verdict(quote, "산업재해율", period="2026")
    assert (result.status, result.cause, result.evidence_column, result.column_state) == \
        ("SOURCE_ONLY", "report_year_only", "합계|2026", "cell"), (quote, result)
    assert result.evidence_period == "2026" and not result.scope_confirmed
    start, end = result.evidence_start, result.evidence_end
    assert quote[start:end].strip() == "0(합계|2026)", (quote, start, end)
    assert result.evidence_text == quote and result.row_label == "산업재해율(‰)"


@pytest.mark.parametrize("order", [(2025, 2026), (2026, 2025)])
@pytest.mark.parametrize("period", ["2026", "2026년 연간", "2025", "2026-04"])
def test_requested_period_contract_is_the_same_as_a_single_cell(order, period):
    """다른 요청 기간은 기존 기간 계약을 따른다 — 다른 셀 때문에 판정이 바뀌지 않는다."""
    multi = verdict(generated_row(order), "산업재해율", period=period)
    single = verdict("산업재해율(‰) 0(2026)", "산업재해율", period=period)
    assert (multi.status, multi.cause) == (single.status, single.cause), (period, multi, single)


def test_a_target_modifier_in_its_own_cell_is_rejected_and_the_actual_cell_kept():
    quote = generated_row((2026, "2030 목표"), {2026: "0", "2030 목표": "0"})
    assert "0(합계|2030 목표)" in quote and "0(합계|2026)" in quote, quote
    # 자기 셀이 목표 열이면 거부, 실적(2026) 셀의 0은 보존한다 — 같은 행 두 후보가 각자 판정된다.
    target_only = generated_row((2025, "2030 목표"), {2025: "3", "2030 목표": "0"})
    assert verdict(target_only, "산업재해율", period="2026").cause == "future_or_intent"
    kept = verdict(quote, "산업재해율", period="2026")
    assert (kept.status, kept.evidence_column) == ("SOURCE_ONLY", "합계|2026"), kept


def test_a_target_word_in_another_plain_cell_is_not_read_as_this_zero():
    result = verdict("구분 | 실적 | 비고\n산업재해 발생 건수 | 0 | 목표 1")
    assert (result.status, result.evidence_column) == ("SOURCE_ONLY", "실적"), result
    own = verdict("구분 | 실적 | 비고\n산업재해 발생 건수 | 목표 0 | 1")
    assert own.cause == "future_or_intent", own


@pytest.mark.parametrize("aggregate", ["합계", "국내", "해외", "국내(별도)"])
def test_generator_aggregate_headers_including_parentheses_are_consumed(aggregate):
    quote = generated_row((2025, 2026), aggregate=aggregate)
    assert f"0({aggregate}|2026)" in quote, quote
    result = verdict(quote, "산업재해율", period="2026")
    assert (result.status, result.evidence_column, result.column_state) == \
        ("SOURCE_ONLY", f"{aggregate}|2026", "cell"), (quote, result)
    assert result.evidence_period == "2026"


def test_two_level_generator_header_with_parenthesised_groups():
    """국내(별도)/해외 × 2개 연도의 2단 머리글을 생성기로 만든 행."""
    rows = [
        [(150, "2025"), (350, "2026")],
        [(0, "구분"), (100, "국내(별도)"), (200, "해외"), (300, "국내(별도)"), (400, "해외")],
        [(0, "산업재해율(‰)"), (100, "2"), (200, "1"), (300, "0"), (400, "3")],
    ]
    quote = " | ".join(t for _, t in router._attach_column_headers(rows)[-1])
    assert "0(국내(별도)|2026)" in quote and "2(국내(별도)|2025)" in quote, quote
    result = verdict(quote, "산업재해율", period="2026")
    assert (result.status, result.cause, result.evidence_column) == \
        ("SOURCE_ONLY", "report_year_only", "국내(별도)|2026"), (quote, result)


def test_table_cells_respect_annotation_brackets():
    line = "산업재해율(‰) | 1(국내(별도)|2025) | 0(합계|2026) | 4)"
    cells = [line[s:e].strip() for s, e in router._table_cells(line)]
    assert cells == ["산업재해율(‰)", "1(국내(별도)|2025)", "0(합계|2026)", "4)"]


# ── F3 — 새 절은 이전 절의 범위 상속을 끝낸다 ─────────────────────────────

FACTS = {"numeric": "산업재해 발생 건수 0건", "negation": "산업재해는 발생하지 않았다"}


@pytest.mark.parametrize("older,earlier", [
    ("2026년 4월 김해 제1공장 안전 현황", "산업재해 발생 건수 1건"),
    ("2026년 4월 김해 제1공장 안전 현황", "산업재해 발생 건수 7건"),
    ("2026년 3월 부산 제2공장 안전 현황", "산업재해 발생 건수 1건"),
    ("김해 제1공장 안전 현황", "산업재해 발생 건수 2건"),
    (None, ""),
], ids=["review", "other_value", "other_scope", "site_only", "removed"])
@pytest.mark.parametrize("blank", ["\n\n", "\n"], ids=["blank_line", "no_blank_line"])
@pytest.mark.parametrize("kind", FACTS)
def test_the_new_section_verdict_is_independent_of_the_older_section(kind, older, earlier, blank):
    quote = separated_sections(FACTS[kind], earlier, older).replace("\n\n", blank)
    result = verdict(quote, site="김해 제1공장")
    assert (result.status, result.evidence_period, result.evidence_site, result.scope_heading) == \
        ("SOURCE_ONLY", "", "", ""), (quote, result)
    assert result.scope_boundary == "2026년 5월 부산 제1공장 교육 현황", result


@pytest.mark.parametrize("kind", FACTS)
def test_an_applicable_new_section_scope_is_used_alone(kind):
    older = "2026년 3월 부산 제2공장 안전 현황\n산업재해 발생 건수 2건\n\n"
    confirmed = verdict(older + "2026년 4월 김해 제1공장 안전 현황\n" + FACTS[kind], site="김해 제1공장")
    assert (confirmed.status, confirmed.scope_from, confirmed.scope_heading) == \
        ("CONFIRMED", "heading", "2026년 4월 김해 제1공장 안전 현황"), confirmed
    # 새 절의 범위가 요청과 충돌하면 거부한다 — 앞 절의 맞는 범위로 대체하지 않는다.
    matching_older = "2026년 4월 김해 제1공장 안전 현황\n산업재해 발생 건수 2건\n\n"
    for new, cause in [("2026년 5월 김해 제1공장 안전 현황", "period_mismatch"),
                       ("2026년 4월 부산 제1공장 안전 현황", "site_mismatch")]:
        result = verdict(matching_older + new + "\n" + FACTS[kind], site="김해 제1공장")
        assert (result.status, result.cause) == ("REJECTED", cause), (new, result)


@pytest.mark.parametrize("kind", FACTS)
def test_a_period_only_new_section_does_not_borrow_the_older_site(kind):
    quote = "김해 제1공장 안전 현황\n산업재해 발생 건수 1건\n\n2026년 4월 안전 현황\n" + FACTS[kind]
    result = verdict(quote, site="김해 제1공장")
    assert (result.status, result.cause, result.evidence_period, result.evidence_site) == \
        ("SOURCE_ONLY", "site_not_stated", "2026년 4월", ""), result


@pytest.mark.parametrize("quote", [
    "2026년 4월 김해 제1공장 안전 현황\n{fact}",                                    # 바로 적용되는 머리말
    "2026년 4월 김해 제1공장 안전 현황\n교육 참석 인원 50명\n{fact}",                 # 같은 절의 무관한 데이터
    "2026년 4월 김해 제1공장 안전 현황\n안전 교육을 실시했다\n{fact}",                # 같은 절의 서술
    "2026년 4월 김해 제1공장\n안전 현황\n{fact}",                                     # 확인된 상위 공통 머리말
    "2026년 4월 김해 제1공장\n\n안전 현황\n\n{fact}",                                 # 빈 줄은 결과를 정하지 않는다
    "김해 제1공장\n2026년 4월 안전 현황\n구분 | 실적\n교육 참여 인원 | 50\n{row}",       # 상위 머리말 + 표 행
], ids=["direct", "unrelated_row", "narrative", "parent", "parent_blank", "parent_table"])
@pytest.mark.parametrize("kind", FACTS)
def test_confirmed_common_headings_are_still_inherited(quote, kind):
    row = "산업재해 발생 건수 | 0" if kind == "numeric" else "산업재해 발생 여부 | 미발생"
    text = quote.format(fact=FACTS[kind], row=row)
    hint = "산업재해 발생 건수" if kind == "numeric" or "{row}" not in quote else "산업재해 발생 여부"
    result = verdict(text, hint, site="김해 제1공장")
    assert (result.status, result.cause) == ("CONFIRMED", "stated_zero"), (text, result)
    assert result.evidence_period == "2026년 4월" and result.evidence_site and not result.scope_boundary


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


def accident_rate(quote, period="2026-04", boundary=None):
    return {"metric_hint": "산업재해율", "kesg_code": "S-4-2", "value": 0, "unit": "‰",
            "period": period, "quote": quote, "boundary": boundary or {}}


def test_integration_text_row_target_zero_is_excluded_with_its_own_cell(client):
    quote = mixed_table(metric="산업재해율(‰)")
    ext, graph, facts, findings, _ = run_response(client, accident_rate(quote))
    assert ext.metrics == [] and not graph.nodes and "S-4-2" not in facts
    (entry,) = ext.router_meta["unvalued_records"]
    (record,) = entry["records"]
    assert (record["cause"], record["status"], record["evidence_column"], record["column_state"]) == \
        ("future_or_intent", "REJECTED", "목표", "header")
    row_start = quote.index("산업재해율(‰) | 0")
    assert record["evidence_offset"] == row_start + len("산업재해율(‰) | ")
    assert quote[record["evidence_start"]:record["evidence_end"]].strip() == "0"
    assert record["row_label"] == "산업재해율(‰)"
    zero = [f for f in findings if f.check_reason == "zero_not_in_evidence"]
    assert zero and any("목표" in f.reason or "계획" in f.reason for f in zero), [f.reason for f in zero]


@pytest.mark.parametrize("order", [(2025, 2026), (2026, 2025), (2024, 2026, 2025)])
def test_integration_generated_multi_year_zero_reaches_s_4_2(client, order):
    quote = generated_row(order)
    ext, graph, facts, findings, _ = run_response(client, accident_rate(quote, period="2026"))
    (metric,) = ext.metrics
    assert (metric.value, metric.unit, metric.quote) == (0, "‰", quote)
    record = zero_record(metric)
    assert (record["status"], record["cause"], record["source_period"], record["evidence_column"]) == \
        ("SOURCE_ONLY", "report_year_only", "2026", "합계|2026")
    assert quote[record["evidence_start"]:record["evidence_end"]].strip() == "0(합계|2026)"
    (node,) = graph.nodes.values()
    assert (node.metric, node.value, node.unit) == ("S-4-2", 0, "‰")
    assert Boundary.from_dict(node.boundary).aggregation != "annual"   # 보고 연도를 연간으로 넓히지 않는다
    fact = facts["S-4-2"]
    assert (fact.value, fact.unit) == (0, "‰") and "scope_source_only" in fact.flags
    assert not [f for f in findings if f.check_reason == "zero_not_in_evidence"]


def test_integration_new_section_zero_stays_unconfirmed(client):
    quote = separated_sections("산업재해율(‰) 0", "산업재해율(‰) 1")
    ext, graph, facts, findings, block = run_response(
        client, accident_rate(quote, boundary={"site": "김해 제1공장"}))
    (node,) = graph.nodes.values()
    assert (node.metric, node.unit, node.value) == ("S-4-2", "‰", 0)
    boundary = Boundary.from_dict(node.boundary)
    (record,) = [p for p in boundary.provenance if p.get("source") == "zero_evidence"]
    assert (record["status"], record["source_period"], record["source_site"], record["scope_heading"]) == \
        ("SOURCE_ONLY", "", "", "")
    assert record["scope_boundary"] == "2026년 5월 부산 제1공장 교육 현황"
    assert record["requested_period"] == "2026-04"       # 요청 범위는 원문 범위와 따로 남는다
    assert boundary.period_text not in ("2026년 4월", "2026년 5월")
    fact = facts["S-4-2"]
    assert fact.value == 0 and "scope_source_only" in fact.flags
    scope = [f for f in findings if f.check_reason == "scope_source_only"]
    assert scope and scope[0].code == "S-4-2", [(f.code, f.check_reason) for f in findings]
    assert block is not None and "범위 미확정" in block.body_md
    assert "안전 현황" not in json.dumps(record, ensure_ascii=False)   # 앞 절이 근거로 나가지 않는다


def test_integration_applicable_heading_has_no_warning(client):
    quote = "2026년 3월 부산 제2공장 안전 현황\n산업재해율(‰) 1\n\n2026년 4월 김해 제1공장 안전 현황\n산업재해율(‰) 0"
    ext, graph, facts, findings, _ = run_response(client, accident_rate(quote, boundary={"site": "김해 제1공장"}))
    (node,) = graph.nodes.values()
    boundary = Boundary.from_dict(node.boundary)
    (record,) = [p for p in boundary.provenance if p.get("source") == "zero_evidence"]
    assert (record["status"], record["scope_heading"]) == ("CONFIRMED", "2026년 4월 김해 제1공장 안전 현황")
    assert (boundary.period_text, boundary.aggregation) == ("2026년 4월", "monthly")
    assert "scope_source_only" not in facts["S-4-2"].flags
    assert not [f for f in findings if f.check_reason in ("scope_source_only", "zero_not_in_evidence")]


def test_integration_normal_unit_zeros_still_survive(client):
    for hint, unit in [("온실가스 배출량", "tCO2eq"), ("장기차입금", "백만원")]:
        ext, graph, _facts, findings, _ = run_response(client, {
            "metric_hint": hint, "value": 0, "unit": unit, "period": "2026-04",
            "quote": f"2026년 4월 {hint} 0 {unit}"})
        assert [(m.value, m.unit) for m in ext.metrics] == [(0, unit)]
        assert [n.value for n in graph.nodes.values()] == [0]
        assert not [f for f in findings if f.check_reason == "zero_not_in_evidence"]
