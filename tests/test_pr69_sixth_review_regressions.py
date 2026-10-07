"""PR 69 6차 검토(2026-10-02, 73a5e66)에서 찾은 숫자 역할 구분의 세 결함 회귀 테스트와 main 병합 검사.

F1 번호가 붙은 새 절 제목(`2. `·`제2절 `)의 번호를 수량으로 읽어 제목을 사실로 건너뛰었고, 0이 앞 절의
   4월·김해 범위로 CONFIRMED가 됐다.
F2 표 바깥 테두리(`| … |`) 앞의 빈 칸을 첫 칸으로, `Scope 3`의 3을 값으로 읽어 행 라벨을 잃었다 —
   `Scope 3 배출량(tCO2eq) | 0`의 0이 `other_subject`로 지워졌다(47a2990에서는 통과하던 회귀).
F3 `1공장`을 수량으로 읽어 표 중간의 새 머리글 `구분 | 1공장 | 2공장`을 놓쳤고, 이전 `목표 | 실적`
   역할이 남아 정상 0이 `future_or_intent`로 지워졌다.
병합 main(PR 68)의 `source_conflict`와 PR 69의 `scope_source_only`는 서로 다른 사유로 함께 남아야 한다.

검토 산출물의 독립 검사 9건(`output/reviews/pr69_20261002/sixth_review/test_pr69_numeric_labels.py`)을
개인 경로·`REVIEW_CODE` 없이 옮기고, 표기 변형의 동등성·정상 대조군·원본 응답 → 원장 → 확인 목록
통합 검사를 붙였다. 모든 문구는 합성 입력이다.
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
from esgenie.ssot.evidence_graph import EvidenceGraph, EvidenceNode, merge_ocr_extraction


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


# ── 검토 산출물 9건(실패 5 · 정상 대조 4) ──────────────────────────────────

def test_new_factory_columns_do_not_inherit_old_target_roles():
    quote = ("구분 | 목표 | 실적\n교육 참여 인원 | 0 | 50\n"
             "구분 | 1공장 | 2공장\n산업재해 발생 건수 | 0 | 1")
    metrics, _, issues = mapped(quote)
    assert [m.value for m in metrics] == [0], issues
    records = [p for p in metrics[0].boundary["provenance"] if p.get("source") == "zero_evidence"]
    assert records[0]["evidence_column"] != "목표", records


@pytest.mark.parametrize("outer_pipes", [False, True])
def test_scope_three_label_survives_table_border_pipes(outer_pipes):
    rows = ["항목 | 2026", "Scope 3 배출량(tCO2eq) | 0"]
    quote = "\n".join(f"| {row} |" if outer_pipes else row for row in rows)
    metrics, _, issues = mapped(quote, "Scope 3 배출량", "tCO2eq", "2026")
    assert [(m.value, m.unit) for m in metrics] == [(0, "tCO2eq")], issues


def sections(prefix, rate=False, older="2026년 4월 김해 제1공장 안전 현황", earlier=None):
    first = earlier or ("산업재해율(‰) 1" if rate else "산업재해 발생 건수 1건")
    second = "산업재해율(‰) 0" if rate else "산업재해 발생 건수 0건"
    head = f"{older}\n{first}\n\n" if older else ""
    return head + prefix + "2026년 5월 부산 제1공장 교육 현황\n" + second


@pytest.mark.parametrize("prefix", ["2. ", "제2절 "])
def test_numbering_a_section_does_not_reopen_the_previous_scope(prefix):
    result = router._zero_verdict(sections(prefix), "산업재해 발생 건수", "2026-04", {"site": "김해 제1공장"})
    assert result.status == "SOURCE_ONLY", result
    assert not result.evidence_period and not result.evidence_site, result


def test_unnumbered_section_control():
    result = router._zero_verdict(sections(""), "산업재해 발생 건수", "2026-04", {"site": "김해 제1공장"})
    assert result.status == "SOURCE_ONLY", result


@pytest.mark.parametrize("hint,unit", [("온실가스 배출량", "tCO2eq"), ("장기차입금", "백만원")])
def test_normal_unit_zero_control(hint, unit):
    metrics, _, issues = mapped(f"2026년 4월 {hint} 0 {unit}", hint, unit)
    assert [m.value for m in metrics] == [0] and not issues, issues


def test_numbered_section_scope_warning_reaches_the_real_ledger():
    quote = sections("2. ", rate=True)
    metrics, clauses, issues = mapped(quote, "산업재해율", "‰", site="김해 제1공장")
    extraction = router.OcrExtraction(
        source_file="synthetic-numbered-section.pdf", channel=router.DocChannel.UNSTRUCTURED,
        doc_type="report", metrics=metrics, clauses=clauses, raw_text=quote,
    )
    graph = EvidenceGraph("LOCAL", "검토")
    merge_ocr_extraction(graph, extraction, report_year=2026)
    facts = {node.metric: selection._from_nodes(graph, node.metric, [node]) for node in graph.nodes.values()}
    assert "S-4-2" in facts and facts["S-4-2"].value == 0, (facts, issues)
    graph.resolved_facts = facts
    findings = build_source_review(SimpleNamespace(
        evidence_graph=graph, ocr_extractions=[extraction], extraction=None,
        sections={}, item_retrievals=[],
    ))
    assert "scope_source_only" in facts["S-4-2"].flags, facts["S-4-2"]
    assert any(f.code == "S-4-2" and f.check_reason == "scope_source_only" for f in findings), findings


# ── F1 — 절 번호는 수량이 아니다 ─────────────────────────────────────────

NUMBERINGS = ["", "2. ", "제2절 ", "(2) ", "2) ", "2-1. ", "2.1 ", "2.1. ", "[2] ", "Ⅱ. ", "II. ", "가. ",
              "① ", "## 2. ", "**2.** "]
FACTS = {"numeric": "산업재해 발생 건수 0건", "negation": "산업재해는 발생하지 않았다"}


def numbered(prefix, heading, fact, above=""):
    return f"{above}{prefix}{heading}\n{fact}"


@pytest.mark.parametrize("kind", FACTS)
@pytest.mark.parametrize("prefix", NUMBERINGS)
@pytest.mark.parametrize("older", [
    "2026년 4월 김해 제1공장 안전 현황\n산업재해 발생 건수 1건\n\n",
    "2026년 4월 김해 제1공장 안전 현황\n산업재해 발생 건수 7건\n",
    "2026년 3월 김해 제2공장 안전 현황\n산업재해 발생 건수 1건\n\n",
    "",
], ids=["review", "other_value", "other_scope", "removed"])
def test_a_numbered_new_section_ends_inheritance_like_an_unnumbered_one(kind, prefix, older):
    quote = numbered(prefix, "2026년 5월 부산 제1공장 교육 현황", FACTS[kind], older)
    result = verdict(quote, site="김해 제1공장")
    assert (result.status, result.evidence_period, result.evidence_site, result.scope_heading) == \
        ("SOURCE_ONLY", "", "", ""), result
    # 경계는 원문의 새 절 제목 그대로다(번호를 지우지 않는다; 마침표로 갈린 `Ⅱ.`·`가.`는 제목 토막).
    assert result.scope_boundary in quote and result.scope_boundary.endswith("2026년 5월 부산 제1공장 교육 현황")
    assert quote[quote.index(result.scope_boundary) - 1] in "\n. " or quote.startswith(result.scope_boundary)


@pytest.mark.parametrize("kind", FACTS)
@pytest.mark.parametrize("prefix", NUMBERINGS)
def test_an_applicable_numbered_section_scope_is_used(kind, prefix):
    quote = numbered(prefix, "2026년 4월 김해 제1공장 안전 현황", FACTS[kind],
                     "2026년 3월 부산 제2공장 교육 현황\n교육 1건\n\n")
    result = verdict(quote, site="김해 제1공장")
    assert (result.status, result.evidence_period, result.evidence_site) == \
        ("CONFIRMED", "2026년 4월", "김해1공장"), result
    assert result.scope_heading.endswith("2026년 4월 김해 제1공장 안전 현황") and result.scope_heading in quote


@pytest.mark.parametrize("kind", FACTS)
@pytest.mark.parametrize("prefix", NUMBERINGS)
def test_a_numbered_sub_heading_still_inherits_its_parent(kind, prefix):
    quote = numbered(prefix, "안전 현황", FACTS[kind], "2026년 4월 김해 제1공장\n")
    result = verdict(quote, site="김해 제1공장")
    assert (result.status, result.scope_heading) == ("CONFIRMED", "2026년 4월 김해 제1공장"), result


@pytest.mark.parametrize("kind", FACTS)
@pytest.mark.parametrize("prefix", ["2. ", "제2절 ", "(2) "])
def test_a_numbered_section_with_a_conflicting_scope_is_rejected(kind, prefix):
    quote = numbered(prefix, "2026년 5월 김해 제1공장 안전 현황", FACTS[kind],
                     "2026년 4월 김해 제1공장 안전 현황\n산업재해 발생 건수 1건\n\n")
    result = verdict(quote, site="김해 제1공장")
    assert (result.status, result.cause, result.evidence_period) == \
        ("REJECTED", "period_mismatch", "2026년 5월"), result      # 앞 절의 맞는 범위로 바꾸지 않는다


@pytest.mark.parametrize("kind", FACTS)
@pytest.mark.parametrize("line", ["2.5 톤 감축", "1-2 명 교육 실시", "2. 교육 참석자 46명", "3) 점검 2회",
                                  "12 건 점검"])
def test_a_same_section_quantity_line_is_not_a_heading(kind, line):
    quote = f"2026년 4월 김해 제1공장 안전 현황\n{line}\n{FACTS[kind]}"
    result = verdict(quote, site="김해 제1공장")
    assert (result.status, result.scope_heading, result.scope_boundary) == \
        ("CONFIRMED", "2026년 4월 김해 제1공장 안전 현황", ""), result


@pytest.mark.parametrize("piece,number", [
    ("2. 2026년 5월 부산 제1공장 교육 현황", "2. "), ("제2절 교육 현황", "제2절 "), ("(2) 교육", "(2) "),
    ("2-1. 교육", "2-1. "), ("2.1 교육", "2.1 "), ("**2.** 교육", "**2.** "), ("2.5 톤", ""), ("1-2 명", ""),
    ("2026년 4월", ""), ("12건 점검", ""), ("0건", ""),
])
def test_heading_number_is_only_a_line_head_marker(piece, number):
    length = router._heading_number(piece)
    assert piece[:length].strip() == number.strip(), (piece, length)


# ── F2 — 바깥 테두리와 지표명의 숫자 ───────────────────────────────────────

def scope3(header_cells, value_cells, border=False, rule=False, tight=False):
    sep = "|" if tight else " | "

    def row(cells):
        text = sep.join(cells)
        return (f"|{text}|" if tight else f"| {text} |") if border else text
    rows = [row(["항목", *header_cells])]
    if rule:
        rows.append(row(["---"] * (len(header_cells) + 1)))
    rows.append(row(["Scope 3 배출량(tCO2eq)", *value_cells]))
    return "\n".join(rows)


LAYOUTS = [dict(border=b, rule=r, tight=t) for b in (False, True) for r in (False, True) for t in (False, True)]
TABLES = [(["2026"], ["0"]), (["2025", "2026"], ["5", "0"]), (["2026", "2025"], ["0", "5"]),
          (["2024", "2025", "2026"], ["", "5", "0"]), (["2026", "2024", "2025"], ["0", "", "5"])]


@pytest.mark.parametrize("layout", LAYOUTS, ids=lambda l: "-".join(k for k, v in l.items() if v) or "plain")
@pytest.mark.parametrize("header,values", TABLES, ids=["one", "two", "two_rev", "gap", "gap_rev"])
def test_scope_three_zero_is_the_same_with_or_without_borders(header, values, layout):
    quote = scope3(header, values, **layout)
    result = verdict(quote, "Scope 3 배출량", "2026")
    assert (result.status, result.cause, result.evidence_column, result.column_state) == \
        ("SOURCE_ONLY", "report_year_only", "2026", "header"), (quote, result)
    assert (result.row_label, result.source_unit, result.unit_location) == \
        ("Scope 3 배출량(tCO2eq)", "tCO2eq", "label"), result
    assert quote[result.evidence_start:result.evidence_end].strip() == "0"
    assert quote[result.evidence_offset] == "0"
    # 열 머리 위치도 같은 칸 좌표로 원문의 `2026` 칸을 가리킨다.
    column, state, head_offset = router._column_header(quote, result.evidence_offset)
    assert (column, state) == ("2026", "header") and quote[head_offset:head_offset + 4] == "2026"


@pytest.mark.parametrize("border", [False, True])
def test_scope_three_mapping_keeps_value_unit_and_label(border):
    quote = scope3(["2026"], ["0"], border=border)
    metrics, _, issues = mapped(quote, "Scope 3 배출량", "tCO2eq", "2026")
    (metric,) = metrics
    assert (metric.value, metric.unit) == (0, "tCO2eq") and not issues
    record = zero_record(metric)
    assert (record["status"], record["cause"], record["source_unit"], record["unit_location"]) == \
        ("SOURCE_ONLY", "report_year_only", "tCO2eq", "label")
    assert "Scope 3 배출량" in record["row_label"]


@pytest.mark.parametrize("border", [False, True])
def test_unit_column_is_part_of_the_label_not_the_subject(border):
    rows = ["항목 | 단위 | 2026", "Scope 3 배출량 | tCO2eq | 0"]
    quote = "\n".join(f"| {r} |" if border else r for r in rows)
    result = verdict(quote, "Scope 3 배출량", "2026")
    assert (result.status, result.cause, result.row_label, result.source_unit) == \
        ("SOURCE_ONLY", "report_year_only", "Scope 3 배출량 | tCO2eq", "tCO2eq"), result


@pytest.mark.parametrize("border", [False, True])
def test_a_real_inner_empty_cell_keeps_column_positions(border):
    rows = ["구분 | 목표 | 실적", "산업재해 발생 건수 |  | 0"]
    quote = "\n".join(f"| {r} |" if border else r for r in rows)
    result = verdict(quote)
    assert (result.status, result.evidence_column) == ("SOURCE_ONLY", "실적"), result
    rows = ["구분 | 실적 | 목표", "산업재해 발생 건수 |  | 0"]
    quote = "\n".join(f"| {r} |" if border else r for r in rows)
    result = verdict(quote)
    assert (result.status, result.cause, result.evidence_column) == ("REJECTED", "future_or_intent", "목표")


@pytest.mark.parametrize("border", [False, True])
def test_own_cell_target_zero_is_still_rejected_with_borders(border):
    rows = ["항목 | 2026 목표 | 2026", "Scope 3 배출량(tCO2eq) | 0 | 7"]
    quote = "\n".join(f"| {r} |" if border else r for r in rows)
    result = verdict(quote, "Scope 3 배출량", "2026")
    assert (result.status, result.cause, result.evidence_column) == ("REJECTED", "future_or_intent", "2026 목표")


@pytest.mark.parametrize("border", [False, True])
def test_generator_annotations_survive_borders(border):
    row = "Scope 3 배출량(tCO2eq) | 1(합계|2025) | 0(국내(별도)|2026)"
    quote = f"| {row} |" if border else row
    result = verdict(quote, "Scope 3 배출량", "2026")
    assert (result.status, result.evidence_column, result.column_state) == \
        ("SOURCE_ONLY", "국내(별도)|2026", "cell"), result
    assert quote[result.evidence_start:result.evidence_end].strip() == "0(국내(별도)|2026)"


@pytest.mark.parametrize("line,cells", [
    ("| 항목 | 2026 |", ["항목", "2026"]),
    ("|항목|2026|", ["항목", "2026"]),
    ("| | 2025 | 2026 |", ["", "2025", "2026"]),           # 모서리 빈 칸은 실제 칸이다
    ("| 산업재해 |  | 0 |", ["산업재해", "", "0"]),          # 값이 빈 칸도 실제 칸이다
    ("항목 | 2026", ["항목", "2026"]),
    ("| 항목 | 2026", ["", "항목", "2026"]),                # 한쪽만 `|` — 테두리로 단정하지 않는다
    ("| 0(합계|2026) |", ["0(합계|2026)"]),
])
def test_table_cells_drop_only_the_outer_border(line, cells):
    assert [line[s:e].strip() for s, e in router._table_cells(line)] == cells
    assert router._is_table_line(line)


@pytest.mark.parametrize("text,value", [
    ("Scope 3 배출량(tCO2eq)", False), ("Scope 3", False), ("CO2 배출량", False), ("PM2.5 농도", False),
    ("1공장", False), ("제1공장", False), ("tCO2eq", False),
    ("0", True), ("0(합계|2026)", True), ("46명", True), ("-", True), ("", True), ("약 3건", True),
    ("누계 0", True), ("미보유", True),
])
def test_value_cell_is_a_quantity_not_a_number_inside_a_name(text, value):
    assert router._is_value_cell(text) is value


# ── F3 — 새 사업장 머리글은 이전 열 역할을 끝낸다 ─────────────────────────

OLD = "구분 | 목표 | 실적\n교육 참여 인원 | 0 | 50\n"


@pytest.mark.parametrize("sites", [("1공장", "2공장"), ("제1공장", "제2공장"), ("김해 제1공장", "부산 제1공장"),
                                   ("서아산공장", "본사")])
@pytest.mark.parametrize("swap", [False, True])
def test_new_site_header_resets_the_column_roles(sites, swap):
    first, second = sites[::-1] if swap else sites
    values = "1 | 0" if swap else "0 | 1"
    quote = OLD + f"구분 | {first} | {second}\n산업재해 발생 건수 | {values}"
    result = verdict(quote)
    assert (result.status, result.cause, result.evidence_column, result.column_state) == \
        ("SOURCE_ONLY", "period_not_stated", sites[0], "header"), result
    assert result.row_label == "산업재해 발생 건수"


@pytest.mark.parametrize("between", ["", "점검 상태 | 완료 | 완료\n", "교육 참여 인원 | 3 | 4\n",
                                     "---|---|---\n"], ids=["none", "text_row", "numeric_row", "rule"])
@pytest.mark.parametrize("after", ["", "---|---|---\n"], ids=["no_rule", "rule_after"])
def test_new_site_header_holds_with_rows_and_rules_around_it(between, after):
    quote = OLD + between + "구분 | 1공장 | 2공장\n" + after + "산업재해 발생 건수 | 0 | 1"
    result = verdict(quote)
    assert (result.status, result.evidence_column) == ("SOURCE_ONLY", "1공장"), (quote, result)


def test_new_site_header_after_a_blank_line_is_a_new_table():
    quote = OLD + "\n구분 | 1공장 | 2공장\n산업재해 발생 건수 | 0 | 1"
    assert (verdict(quote).status, verdict(quote).evidence_column) == ("SOURCE_ONLY", "1공장")
    quote = OLD + "구분 | 1공장 | 2공장\n\n산업재해 발생 건수 | 0 | 1"
    result = verdict(quote)
    assert (result.status, result.column_state) == ("SOURCE_ONLY", "none"), result   # 목표 역할이 오지 않는다


@pytest.mark.parametrize("row", ["점검 상태 | 완료 | 완료", "소속 | 김해공장 | 김해공장", "담당 부서 | 안전팀 | 안전팀"])
def test_text_data_rows_still_cannot_replace_the_target_header(row):
    quote = OLD + row + "\n산업재해 발생 건수 | 0 | 1"
    result = verdict(quote)
    assert (result.status, result.cause, result.evidence_column) == ("REJECTED", "future_or_intent", "목표")


def test_a_new_header_that_names_roles_applies_them():
    quote = "구분 | 1공장 | 2공장\n교육 참여 인원 | 0 | 50\n구분 | 목표 | 실적\n산업재해 발생 건수 | 0 | 1"
    result = verdict(quote)
    assert (result.status, result.cause, result.evidence_column) == ("REJECTED", "future_or_intent", "목표")
    quote = OLD + "구분 | 실적 | 목표\n산업재해 발생 건수 | 0 | 1"
    assert (verdict(quote).status, verdict(quote).evidence_column) == ("SOURCE_ONLY", "실적")


def test_a_wider_new_header_does_not_fall_back_to_the_old_roles():
    quote = OLD + "구분 | 1공장 | 2공장 | 3공장\n산업재해 발생 건수 | 0 | 1"
    result = verdict(quote)
    assert (result.status, result.cause, result.column_state, result.row_label) == \
        ("UNRESOLVED", "column_unresolved", "unresolved", "산업재해 발생 건수"), result


def test_site_identifier_alone_does_not_pick_a_regional_site():
    # `1공장`만으로 `김해 제1공장`을 고르지 않는다(기존 사업장 규약) — 목표 역할로 지워진 것과는 다르다.
    quote = OLD + "구분 | 1공장 | 2공장\n산업재해 발생 건수 | 0 | 1"
    result = verdict(quote, site="김해 제1공장")
    assert (result.status, result.cause, result.evidence_column) == ("REJECTED", "site_mismatch", "1공장")
    quote = OLD + "구분 | 김해 제1공장 | 부산 제1공장\n산업재해 발생 건수 | 0 | 1"
    result = verdict(quote, site="김해 제1공장")
    assert (result.status, result.evidence_column, result.evidence_site) == \
        ("SOURCE_ONLY", "김해 제1공장", "김해1공장"), result


@pytest.mark.parametrize("cells,names", [
    (["구분", "1공장", "2공장"], True), (["", "제1공장", "제2공장"], True), (["사업장", "김해공장", "부산공장"], True),
    (["소속", "김해공장", "부산공장"], False), (["구분", "1공장", "1공장"], False), (["점검 상태", "완료", "완료"], False),
    (["구분", "목표", "실적"], True), (["구분", "1공장", "50"], False),
])
def test_names_columns_structural_rule(cells, names):
    assert router._names_columns(cells) is names


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


@pytest.mark.parametrize("prefix", ["2. ", "제2절 ", "(2) ", "2-1. "])
def test_integration_numbered_new_section_zero_stays_unconfirmed(client, prefix):
    quote = sections(prefix, rate=True)
    ext, graph, facts, findings, block = run_response(
        client, accident_rate(quote, boundary={"site": "김해 제1공장"}))
    (node,) = graph.nodes.values()
    assert (node.metric, node.unit, node.value) == ("S-4-2", "‰", 0)
    boundary = Boundary.from_dict(node.boundary)
    (record,) = [p for p in boundary.provenance if p.get("source") == "zero_evidence"]
    assert (record["status"], record["source_period"], record["source_site"], record["scope_heading"]) == \
        ("SOURCE_ONLY", "", "", "")
    assert record["scope_boundary"] == prefix + "2026년 5월 부산 제1공장 교육 현황"
    assert record["requested_period"] == "2026-04"        # 요청 범위는 원문 범위와 따로 남는다
    assert boundary.period_text not in ("2026년 4월", "2026년 5월")
    fact = facts["S-4-2"]
    assert fact.value == 0 and "scope_source_only" in fact.flags
    scope = [f for f in findings if f.check_reason == "scope_source_only"]
    assert scope and scope[0].code == "S-4-2", [(f.code, f.check_reason) for f in findings]
    assert block is not None and "범위 미확정" in block.body_md
    assert "안전 현황" not in json.dumps(record, ensure_ascii=False)    # 앞 절이 근거로 나가지 않는다


@pytest.mark.parametrize("border", [False, True])
def test_integration_scope_three_zero_reaches_the_ledger(client, border):
    quote = scope3(["2025", "2026"], ["5", "0"], border=border)
    ext, graph, facts, findings, _ = run_response(client, {
        "metric_hint": "Scope 3 배출량", "value": 0, "unit": "tCO2eq", "period": "2026", "quote": quote})
    (metric,) = ext.metrics
    assert (metric.value, metric.unit, metric.quote) == (0, "tCO2eq", quote)
    record = zero_record(metric)
    assert (record["status"], record["cause"], record["source_period"], record["evidence_column"]) == \
        ("SOURCE_ONLY", "report_year_only", "2026", "2026")
    assert (record["row_label"], record["source_unit"], record["unit_location"]) == \
        ("Scope 3 배출량(tCO2eq)", "tCO2eq", "label")
    assert quote[record["evidence_start"]:record["evidence_end"]].strip() == "0"
    (node,) = graph.nodes.values()
    assert (node.value, node.unit) == (0, "tCO2eq")
    assert Boundary.from_dict(node.boundary).aggregation != "annual"
    assert not [f for f in findings if f.check_reason == "zero_not_in_evidence"]


def test_integration_factory_column_zero_is_kept(client):
    quote = ("구분 | 목표 | 실적\n교육 참여 인원 | 0 | 50\n"
             "구분 | 1공장 | 2공장\n산업재해율(‰) | 0 | 1")
    ext, graph, facts, findings, _ = run_response(client, accident_rate(quote))
    (node,) = graph.nodes.values()
    assert (node.metric, node.value, node.unit) == ("S-4-2", 0, "‰")
    (record,) = [p for p in Boundary.from_dict(node.boundary).provenance if p.get("source") == "zero_evidence"]
    assert (record["status"], record["evidence_column"], record["column_state"], record["row_label"]) == \
        ("SOURCE_ONLY", "1공장", "header", "산업재해율(‰)")
    assert quote[record["evidence_start"]:record["evidence_end"]].strip() == "0"
    assert record["evidence_offset"] == quote.index("산업재해율(‰) | 0") + len("산업재해율(‰) | ")
    assert "목표" not in json.dumps(record, ensure_ascii=False)      # 이전 표의 역할이 되살아나지 않는다
    fact = facts["S-4-2"]
    assert fact.value == 0 and "scope_source_only" in fact.flags
    assert not [f for f in findings if f.check_reason == "zero_not_in_evidence"]


def test_integration_real_target_zero_under_a_role_header_is_rejected(client):
    quote = ("구분 | 1공장 | 2공장\n교육 참여 인원 | 0 | 50\n"
             "구분 | 목표 | 실적\n산업재해율(‰) | 0 | 1")
    ext, graph, facts, findings, _ = run_response(client, accident_rate(quote))
    assert ext.metrics == [] and not graph.nodes and "S-4-2" not in facts
    (entry,) = ext.router_meta["unvalued_records"]
    (record,) = entry["records"]
    assert (record["status"], record["cause"], record["evidence_column"]) == ("REJECTED", "future_or_intent", "목표")
    zero = [f for f in findings if f.check_reason == "zero_not_in_evidence"]
    assert zero and any("목표" in f.reason or "계획" in f.reason for f in zero)


# ── main 병합: scope_source_only와 source_conflict는 독립 사유다 ───────────────

CONFLICT = f"review.pdf 1쪽 {selection.SOURCE_CONFLICT_NOTE}(같은 문서 검산 불일치) — 명시 0 ≠ 계산 1"


def conflict_graph(scope_only, conflict):
    provenance = ({"source": "zero_evidence", "status": "SOURCE_ONLY" if scope_only else "CONFIRMED",
                   "cause": "period_not_stated" if scope_only else "stated_zero"},)
    boundary = Boundary(provenance=provenance, review_notes=(CONFLICT,) if conflict else ())
    graph = EvidenceGraph("LOCAL", "검토")
    graph.report_year = 2026
    graph.add_node(EvidenceNode("LOCAL_S-4-2_2026__ocr", "S-4-2", 0, "‰", 2026, "ocr/report",
                                origin="ocr", source_file="review.pdf", quote="산업재해율(‰) 0",
                                value_role="actual", boundary=boundary))
    result = SimpleNamespace(mapped={"S-4-2": {"value": 0, "unit": "‰", "source_tier": "ocr_node_gated",
                                               "name": "산업재해율"}},
                             confidence_flags={})
    selection.finalize_ledger(result, graph)
    return graph


@pytest.mark.parametrize("scope_only,conflict", [(True, False), (False, True), (True, True), (False, False)],
                         ids=["scope_only", "conflict_only", "both", "neither"])
def test_scope_and_conflict_flags_coexist_through_ledger_review_and_report(scope_only, conflict):
    from esgenie.ssot.audit_trace import build_data_points
    graph = conflict_graph(scope_only, conflict)
    fact = graph.resolved_facts["S-4-2"]
    assert fact is not None and fact.value == 0 and fact.representative_node_ids
    assert ("scope_source_only" in fact.flags, "source_conflict" in fact.flags) == (scope_only, conflict)
    assert any(selection.SOURCE_CONFLICT_NOTE in n for n in fact.scope_notes) is conflict

    findings = build_source_review(SimpleNamespace(evidence_graph=graph, ocr_extractions=[], extraction=None,
                                                   sections={}, item_retrievals=[]))
    reasons = {f.check_reason: f for f in findings if f.code == "S-4-2"}
    assert ("scope_source_only" in reasons, "source_conflict" in reasons) == (scope_only, conflict)
    if conflict:
        assert CONFLICT in reasons["source_conflict"].reason      # 상충 근거 문구가 그대로 보인다
    block = _block_source_review(SimpleNamespace(review_findings=findings))
    body = block.body_md if block is not None else ""
    assert ("범위 미확정" in body, "원측정값 상충" in body) == (scope_only, conflict)

    (point,) = build_data_points(graph, {"S-4-2": 0.0}, target_codes=["S-4-2"])
    assert ("scope_source_only" in point.confidence_flags, "source_conflict" in point.confidence_flags) == \
        (scope_only, conflict)
    if conflict:
        assert point.verification == "unverified"                 # 상충은 범위 사유로 묻히지 않는다
    elif scope_only:
        assert point.verification == "estimated"


def test_pr68_meter_mismatch_reaches_the_review_list_as_its_own_item(monkeypatch):
    # 병합 전 main에서는 이 상충이 확인 목록에 `partial_value`로만 남았다 — 상충 사유가 따로 보여야 한다.
    from tests.test_pr68_review_r1_r5 import _case, _pipeline
    for key in ("_get_openai_key", "_get_anthropic_key", "_get_upstage_key"):
        monkeypatch.setattr(router, key, lambda: None)
    graph, points, _, _ = _pipeline([_case("explicit_meter_mismatch")])
    fact = graph.resolved_facts["E-4-1"]
    assert "source_conflict" in fact.flags and points["E-4-1"].verification == "unverified"
    findings = build_source_review(SimpleNamespace(evidence_graph=graph, ocr_extractions=[], extraction=None,
                                                   sections={}, item_retrievals=[]))
    conflict = [f for f in findings if f.code == "E-4-1" and f.check_reason == "source_conflict"]
    assert conflict and selection.SOURCE_CONFLICT_NOTE in conflict[0].reason
    block = _block_source_review(SimpleNamespace(review_findings=findings))
    assert block is not None and "원측정값 상충" in block.body_md
