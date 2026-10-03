"""PR 69 4차 검토(2026-10-02, 8d093d8)에서 찾은 0값 연결의 세 결함 회귀 테스트.

R1 숫자 0 뒤 단위를 `[A-Za-z%]+\\d*`·한글 1~2글자로 다시 정의해 사전이 지원하는 `0 tCO2eq`·
   `0 백만원`이 미분류 문장으로 지워졌다.
R2 칸 수가 같은 가장 가까운 앞 행을 머리글로 써서 데이터 행(`교육 참여 인원 | 0 | 50`)이 열 머리가
   됐다 — 목표 0이 실적으로 남고, 연도 표에서는 연도 열을 잃었다.
R3 다른 절 전체를 폴백 문맥으로 써서 다른 지표(교육)의 기간·사업장으로 산업재해 0이 CONFIRMED가 됐다.

검토 산출물의 독립 재현 11건(`output/reviews/pr69_20261002/fourth_review/test_pr69_zero_context.py`)을
개인 경로·`REVIEW_CODE` 없이 옮기고, 단위 표기·무관한 행·관련 없는 문장을 바꾸는 대조군과 원장·확인
목록 통합 검사를 붙였다. 모든 문구는 합성 입력이다.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from esgenie.config import SETTINGS
from esgenie.layer6_report import _block_source_review
from esgenie.llm import LLMResponse
from esgenie.source_review import build_source_review
from esgenie.ssot import ocr_router as router
from esgenie.ssot import selection
from esgenie.ssot.boundary import Boundary, covers_full_year
from esgenie.ssot.evidence_graph import EvidenceGraph, merge_ocr_extraction


def mapped(quote, hint="산업재해 발생 건수", unit="건", period="2026-04", boundary=None, value=0):
    issues = []
    metrics, _ = router._map_vlm_json(
        {"metrics": [dict(metric_hint=hint, value=value, unit=unit,
                          period=period, quote=quote, boundary=boundary or {})]},
        source_text=quote, page_no=0, issues=issues,
    )
    return metrics, issues


def zero_record(metric):
    (record,) = [p for p in metric.boundary.get("provenance", []) if p.get("source") == "zero_evidence"]
    return record


def verdict(quote, hint="산업재해 발생 건수", period="2026-04", site="", unit=""):
    return router._zero_verdict(quote, hint, period, {"site": site}, unit)


# ── 검토 산출물 11건(실패 6 · 정상 대조 5) ─────────────────────────────────

@pytest.mark.parametrize("hint,unit", [
    ("온실가스 배출량", "tCO2eq"), ("장기차입금", "백만원"),
])
def test_normal_zero_with_supported_unit_is_preserved(hint, unit):
    quote = f"2026년 4월 {hint} 0 {unit}"
    metrics, issues = mapped(quote, hint, unit)
    assert [m.value for m in metrics] == [0], issues


def test_inserting_a_data_row_does_not_turn_target_zero_into_actual_zero():
    header = "구분 | 목표 | 실적\n"
    row = "산업재해 발생 건수 | 0 | 1"
    direct, direct_issues = mapped(header + row)
    assert not direct and direct_issues[0]["cause"] == "future_or_intent"
    metrics, issues = mapped(header + "교육 참여 인원 | 0 | 50\n" + row)
    assert not metrics, [(m.value, m.boundary) for m in metrics]
    assert issues[0]["cause"] == "future_or_intent"


def test_inserting_a_data_row_does_not_lose_the_year_column():
    header = "항목 | 2025 | 2026\n"
    row = "산업재해 발생 건수 | 3 | 0"
    direct, issues = mapped(header + row, period="2026")
    assert [m.value for m in direct] == [0], issues
    metrics, issues = mapped(header + "교육 참석 인원 | 10 | 20\n" + row, period="2026")
    assert [m.value for m in metrics] == [0], issues


@pytest.mark.parametrize("fact", ["산업재해 발생 건수 0건", "산업재해는 발생하지 않았다"])
def test_unrelated_education_cannot_supply_accident_period_or_site(fact):
    quote = fact + ", 2026년 4월 김해 제1공장 교육 참석 인원 50명"
    metrics, issues = mapped(quote, boundary={"site": "김해 제1공장"})
    assert [m.value for m in metrics] == [0], issues
    result = verdict(quote, site="김해 제1공장")
    assert result.status == "SOURCE_ONLY", result
    assert not result.evidence_period and not result.evidence_site, result
    assert zero_record(metrics[0])["status"] == "SOURCE_ONLY"


@pytest.mark.parametrize("hint,unit", [("용수 사용량", "m3"), ("장기차입금", "원")])
def test_simple_supported_unit_control(hint, unit):
    metrics, issues = mapped(f"2026년 4월 {hint} 0 {unit}", hint, unit)
    assert [m.value for m in metrics] == [0], issues


@pytest.mark.parametrize("fact", ["산업재해 발생 건수 0건", "산업재해는 발생하지 않았다"])
def test_own_scope_control(fact):
    assert verdict("2026년 4월 김해 제1공장 " + fact, site="김해 제1공장").status == "CONFIRMED"


def test_complex_denominator_control():
    metrics, issues = mapped("에너지 사용량 2 GJ + 500 MJ, 에너지 사용량 1만 2천 MJ/(톤)",
                             hint="에너지 사용량", unit="GJ", value=2.5)
    assert [m.value for m in metrics] == [2.5], issues
    assert not any(i["reason"] == "scale_chain_recomposed" for i in issues)


# ── R1 — 단위 사전으로 읽은 단위 뒤만 서술로 판정한다 ──────────────────────────

# (지표, 원문 단위 표기, 모델 단위). 모두 `rag_gates.units.normalize_unit`이 지원하는 표기다.
SUPPORTED_UNITS = [
    ("온실가스 배출량", "tCO2eq", "tCO2eq"), ("온실가스 배출량", "tCO2e", "tCO2eq"),
    ("온실가스 배출량", "tCO2", "tCO2eq"),
    ("장기차입금", "백만원", "백만원"), ("장기차입금", "억원", "억원"), ("장기차입금", "원", "원"),
    ("용수 사용량", "m3", "m3"), ("용수 사용량", "m³", "m3"), ("용수 사용량", "㎥", "m3"),
    ("재생에너지 비율", "%", "%"), ("산업재해율", "‰", "‰"), ("전력 사용량", "kWh", "kWh"),
    ("폐기물 발생량", "톤", "t"), ("폐기물 발생량", "kg", "kg"), ("산업재해 발생 건수", "건", "건"),
]
SPACING = [" ", ""]


@pytest.mark.parametrize("space", SPACING, ids=["spaced", "attached"])
@pytest.mark.parametrize("hint,written,unit", SUPPORTED_UNITS)
def test_a_supported_unit_after_zero_keeps_the_fact(hint, written, unit, space):
    quote = f"2026년 4월 {hint} 0{space}{written}"
    metrics, issues = mapped(quote, hint, unit)
    assert [m.value for m in metrics] == [0] and not issues, issues
    record = zero_record(metrics[0])
    assert (record["status"], record["source_unit"], record["unit_location"]) == \
        ("CONFIRMED", written, "after_value")


@pytest.mark.parametrize("tail", ["이다", "으로 집계됐다", "이었다", "입니다"])
@pytest.mark.parametrize("hint,written,unit", SUPPORTED_UNITS[:7])
def test_particles_after_the_unit_are_read_after_the_consumed_span(hint, written, unit, tail):
    assert verdict(f"2026년 4월 {hint} 0 {written}{tail}", hint, unit=unit).status == "CONFIRMED"


# 같은 정상 수치를 목표·예상·조건·가정으로 바꾼 문장. `{u}`는 단위 표기.
NON_FACT_FORMS = {
    "future_or_intent": ["{h} 목표 0 {u}", "{h} 0 {u} 예상", "{h} 0 {u} 달성 목표", "{h} 0 {u}을 목표로 한다"],
    "conditional": ["{h} 0 {u}일 경우 공시한다", "{h}이 0 {u}이라면 공시한다", "{h}이 0 {u}이면 공시한다"],
    "assumption": ["{h} 0 {u}으로 가정한다", "{h}이 0 {u}이라고 가정한다"],
}


@pytest.mark.parametrize("cause,form", [(c, f) for c, fs in NON_FACT_FORMS.items() for f in fs])
@pytest.mark.parametrize("hint,written,unit", [SUPPORTED_UNITS[0], SUPPORTED_UNITS[3],
                                               SUPPORTED_UNITS[7], SUPPORTED_UNITS[9],
                                               SUPPORTED_UNITS[10]])
def test_rewriting_a_unit_fact_as_a_non_fact_is_never_kept(hint, written, unit, cause, form):
    quote = "2026년 4월 " + form.format(h=hint, u=written)
    result = verdict(quote, hint, unit=unit)
    assert (result.status, result.cause) == ("REJECTED", cause), (quote, result)


@pytest.mark.parametrize("written,unit", [("tCO2eqx", "tCO2eq"), ("tCO2e2", "tCO2eq"), ("m3x", "m3"),
                                          ("%p", "%"), ("kWhz", "kWh")])
def test_an_unknown_suffix_after_a_known_unit_is_not_cut_off(written, unit):
    """알려진 단위 뒤에 사전 밖 접미사가 붙으면 앞머리만 떼어 채택하지 않는다(판단 보류)."""
    result = verdict(f"2026년 4월 온실가스 배출량 0 {written}", "온실가스 배출량", unit=unit)
    assert not result.accepted and (result.status, result.cause) == ("UNRESOLVED", "unit_unknown"), result


@pytest.mark.parametrize("written", ["MJ/", "MJ/ ", "MJ／"])
def test_an_unfinished_denominator_is_unit_incomplete_not_a_fact(written):
    result = verdict(f"2026년 4월 에너지 사용량 0 {written}", "에너지 사용량", unit="MJ")
    assert (result.status, result.cause) == ("UNRESOLVED", "unit_incomplete"), result


def test_a_digit_after_a_hangul_unit_is_not_read_as_a_fact():
    assert not verdict("2026년 4월 장기차입금 0 백만원2", "장기차입금", unit="백만원").accepted


def test_unit_unreadable_is_recorded_apart_from_interpretation_unknown():
    _, unit_issues = mapped("2026년 4월 온실가스 배출량 0 tCO2eqx", "온실가스 배출량", "tCO2eq")
    _, mood_issues = mapped("2026년 4월 온실가스 배출량 0 tCO2eq 여부는 미정", "온실가스 배출량", "tCO2eq")
    assert [(i["cause"], i["status"]) for i in unit_issues] == [("unit_unknown", "UNRESOLVED")]
    assert [(i["cause"], i["status"]) for i in mood_issues] == [("interpretation_unknown", "UNRESOLVED")]
    # 원문·모델값·사유를 함께 남긴다 — 모델 단위(`unit`)와 원문 표기(`evidence_text`)는 섞지 않는다.
    (issue,) = unit_issues
    assert issue["unit"] == "tCO2eq" and "tCO2eqx" in issue["evidence_text"] and issue["value"] == 0


def test_a_hangul_unit_outside_the_dictionary_needs_the_same_model_unit():
    assert verdict("2026년 4월 협력사 감사 0곳", "협력사 감사", unit="곳").status == "CONFIRMED"
    assert not verdict("2026년 4월 협력사 감사 0곳", "협력사 감사", unit="개").accepted


@pytest.mark.parametrize("quote,source_unit,location", [
    ("2026년 4월 산업재해율(‰) 0", "‰", "label"),
    ("구분 | 2026년 4월\n산업재해율(‰) | 0", "‰", "label"),
    ("2026년 4월 산업재해율 0 ‰", "‰", "after_value"),
    ("2026년 4월 산업재해율 0", "", ""),
])
def test_the_source_unit_location_is_recorded_without_the_model_unit(quote, source_unit, location):
    result = verdict(quote, "산업재해율", unit="‰")
    assert result.accepted and (result.source_unit, result.unit_location) == (source_unit, location), result


def test_the_reported_unit_alone_is_not_written_as_a_source_unit():
    metrics, _ = mapped("2026년 4월 산업재해율 0", "산업재해율", "‰")
    assert zero_record(metrics[0])["source_unit"] == "" and metrics[0].unit == "‰"


@pytest.mark.parametrize("denominator", ["(톤)", "\n톤", "100개"])
def test_the_composite_case_stays_with_unit_aware_zero_reading(denominator):
    metrics, issues = mapped(f"에너지 사용량 2 GJ + 500 MJ, 에너지 사용량 1만 2천 MJ/{denominator}",
                             hint="에너지 사용량", unit="GJ", value=2.5)
    assert [m.value for m in metrics] == [2.5]
    assert not any(i["reason"] == "scale_chain_recomposed" for i in issues)


# ── R2 — 데이터 행과 머리글 행을 가른다 ─────────────────────────────────────

TARGET_ACTUAL = "구분 | 목표 | 실적"
ACTUAL_TARGET = "구분 | 실적 | 목표"
UNRELATED_ROWS = ["교육 참여 인원 | 0 | 50", "협력사 평가 | 12 | 10", "용수 사용량 | 0 | 0",
                  "환경 사고 | - | 2"]


def table(header, rows):
    return "\n".join([header, *rows])


def accident_row(target, actual, header=TARGET_ACTUAL):
    cells = (target, actual) if header == TARGET_ACTUAL else (actual, target)
    return f"산업재해 발생 건수 | {cells[0]} | {cells[1]}"


@pytest.mark.parametrize("inserted", [0, 1, 2, 4])
@pytest.mark.parametrize("header", [TARGET_ACTUAL, ACTUAL_TARGET], ids=["target_first", "actual_first"])
@pytest.mark.parametrize("target,actual,status,column", [
    (0, 1, "REJECTED", "목표"), (1, 0, "SOURCE_ONLY", "실적"), (0, 0, "SOURCE_ONLY", "실적"),
])
def test_each_zero_is_read_from_its_own_cell(target, actual, status, column, header, inserted):
    rows = [*UNRELATED_ROWS[:inserted], accident_row(target, actual, header)]
    result = verdict(table(header, rows))
    assert (result.status, result.evidence_column, result.column_state) == (status, column, "header"), result
    if status == "REJECTED":
        assert result.cause == "future_or_intent"


@pytest.mark.parametrize("order", [(0, 1, 2, 3), (3, 2, 1, 0), (2, 0, 3, 1)])
def test_reordering_unrelated_rows_keeps_the_verdict(order):
    for target, actual, status in [(0, 1, "REJECTED"), (1, 0, "SOURCE_ONLY")]:
        rows = [UNRELATED_ROWS[i] for i in order]
        before = verdict(table(TARGET_ACTUAL, [*rows, accident_row(target, actual)]))
        middle = verdict(table(TARGET_ACTUAL, [*rows[:2], accident_row(target, actual), *rows[2:]]))
        assert before.status == middle.status == status


def test_both_zero_cells_keep_the_actual_cell_as_the_source():
    metrics, issues = mapped(table(TARGET_ACTUAL, ["교육 참여 인원 | 0 | 50", accident_row(0, 0)]))
    assert [m.value for m in metrics] == [0], issues
    record = zero_record(metrics[0])
    assert (record["evidence_column"], record["column_state"]) == ("실적", "header")


YEAR_TABLES = {
    "plain": "항목 | 2025 | 2026\n{rows}산업재해 발생 건수 | 3 | 0",
    "reordered_years": "항목 | 2026 | 2025\n{rows}산업재해 발생 건수 | 0 | 3",
    "other_table_before": "구분 | 2023 | 2024\n교육 참석 인원 | 0 | 5\n\n항목 | 2025 | 2026\n{rows}"
                          "산업재해 발생 건수 | 3 | 0",
    "other_table_adjacent": "구분 | 2023 | 2024\n교육 참석 인원 | 0 | 5\n항목 | 2025 | 2026\n{rows}"
                            "산업재해 발생 건수 | 3 | 0",
    "title_between": "2024년 교육 현황\n구분 | 2023 | 2024\n교육 참석 인원 | 0 | 5\n안전 현황\n"
                     "항목 | 2025 | 2026\n{rows}산업재해 발생 건수 | 3 | 0",
}


@pytest.mark.parametrize("rows", ["", "교육 참석 인원 | 10 | 20\n",
                                  "교육 참석 인원 | 10 | 20\n협력사 평가 | 0 | 3\n용수 사용량 | 7 | 0\n"],
                         ids=["no_row", "one_row", "three_rows"])
@pytest.mark.parametrize("name", YEAR_TABLES)
def test_the_year_column_is_kept_but_never_promoted_to_annual(name, rows):
    quote = YEAR_TABLES[name].format(rows=rows)
    report_year = verdict(quote, period="2026")
    # 보고 연도만 받은 요청: 2026 열의 0은 보존하되 연간 확정이 아니다(F4의 report_year_only).
    assert (report_year.status, report_year.cause, report_year.evidence_column) == \
        ("SOURCE_ONLY", "report_year_only", "2026"), report_year
    assert not report_year.scope_confirmed
    # 다른 연도를 요청하면 거부한다(열 연결이 실제로 쓰인다).
    assert verdict(quote, period="2025").cause == "period_mismatch"
    # 열 머리의 `2026`은 문장의 `2026년`과 같은 범위 판정을 받는다 — 표라서 넓히거나 좁히지 않는다.
    for period in ("2026", "2026년 연간", "2025", "2026-04"):
        column, sentence = verdict(quote, period=period), verdict("2026년 산업재해 발생 건수 0건", period=period)
        assert (column.status, column.cause) == (sentence.status, sentence.cause), period


def test_year_column_kept_through_mapping_with_its_column_record():
    metrics, issues = mapped(YEAR_TABLES["plain"].format(rows="교육 참석 인원 | 10 | 20\n"), period="2026")
    assert [m.value for m in metrics] == [0], issues
    record = zero_record(metrics[0])
    assert (record["evidence_column"], record["source_period"], record["cause"]) == \
        ("2026", "2026", "report_year_only")
    assert metrics[0].boundary.get("aggregation") != "annual"


def test_a_multi_line_header_joins_its_rows_per_column():
    quote = "구분 | 2026 | 2026\n항목 | 목표 | 실적\n교육 참여 인원 | 0 | 50\n산업재해 발생 건수 | 0 | 1"
    result = verdict(quote, period="2026")
    assert (result.status, result.cause, result.evidence_column) == ("REJECTED", "future_or_intent", "2026 목표")


def test_a_separator_row_does_not_break_the_header_link():
    quote = "구분 | 목표 | 실적\n--- | --- | ---\n교육 참여 인원 | 0 | 50\n산업재해 발생 건수 | 0 | 1"
    assert verdict(quote).cause == "future_or_intent"


def test_a_header_of_another_width_leaves_the_column_unresolved():
    quote = "구분 | 2025 목표 | 2025 실적 | 2026 목표\n산업재해 발생 건수 | 0 | 1"
    result = verdict(quote)
    assert (result.status, result.cause, result.column_state) == ("UNRESOLVED", "column_unresolved", "unresolved")


@pytest.mark.parametrize("quote", ["산업재해 발생 건수 | 0", "산업재해 발생 건수 | 0 | 1",
                                   "교육 참여 인원 | 5\n산업재해 발생 건수 | 0"])
def test_a_table_without_any_header_row_keeps_a_clear_single_value(quote):
    """머리글이 없다는 이유만으로 단일 수치 행의 사실을 일괄 삭제하지 않는다."""
    result = verdict(quote)
    assert result.accepted and result.column_state == "none", result


@pytest.mark.parametrize("cell,status,cause", [
    ("0(합계|2026)", "SOURCE_ONLY", "report_year_only"),
    ("0(합계|2030 목표)", "REJECTED", "future_or_intent"),
    ("0(국내|2025)", "REJECTED", "period_mismatch"),
])
def test_a_structured_cell_header_is_used_as_the_cell_coordinate(cell, status, cause):
    result = verdict(f"산업재해 발생 건수 {cell} 건", period="2026")
    assert (result.status, result.cause, result.column_state) == (status, cause, "cell"), result


# ── R3 — 범위는 자기 서술·셀 또는 확인된 공통 머리말에서만 상속한다 ─────────────────

FACTS = {"numeric": "산업재해 발생 건수 0건", "negation": "산업재해는 발생하지 않았다"}
OTHER_SENTENCES = ["2026년 4월 김해 제1공장 교육 참석 인원 50명", "2026년 3월 부산 제2공장 교육 참석 인원 12명",
                   "2026년 4월 김해 제1공장 교육을 실시했다"]


@pytest.mark.parametrize("other", OTHER_SENTENCES)
@pytest.mark.parametrize("join", [", {f}{o}", "; {f}{o}", "\n{f}{o}", ", {o}{f}", "\n{o}{f}"],
                         ids=["after_comma", "after_semicolon", "after_line", "before_comma", "before_line"])
@pytest.mark.parametrize("kind", FACTS)
def test_another_metric_never_lends_its_scope(kind, join, other):
    fact = FACTS[kind]
    head, sep = (fact, other) if "{f}{o}" in join else (other, fact)
    quote = head + join.split("{")[0] + sep
    result = verdict(quote, site="김해 제1공장")
    assert (result.status, result.evidence_period, result.evidence_site, result.scope_from) == \
        ("SOURCE_ONLY", "", "", ""), (quote, result)
    assert not result.scope_confirmed


@pytest.mark.parametrize("kind", FACTS)
def test_borrowed_scope_stays_source_only_through_the_mapping(kind):
    quote = FACTS[kind] + ", 2026년 4월 김해 제1공장 교육 참석 인원 50명"
    metrics, issues = mapped(quote, boundary={"site": "김해 제1공장"})
    assert [m.value for m in metrics] == [0] and not issues
    record = zero_record(metrics[0])
    assert (record["status"], record["source_period"], record["source_site"], record["scope_from"]) == \
        ("SOURCE_ONLY", "", "", "")
    assert record["requested_period"] == "2026-04"        # 요청 범위는 따로 남는다
    assert "period_start" not in metrics[0].boundary or metrics[0].boundary.get("period_text") != "2026년 4월"


@pytest.mark.parametrize("scope", ["2026년 4월 김해 제1공장 ", "김해 제1공장 2026-04 "])
@pytest.mark.parametrize("kind", FACTS)
def test_own_scope_in_the_sentence_is_confirmed(kind, scope):
    quote = scope + FACTS[kind] + ", 2026년 3월 부산 제2공장 교육 참석 인원 12명"
    result = verdict(quote, site="김해 제1공장")
    assert (result.status, result.scope_from) == ("CONFIRMED", "statement"), result


@pytest.mark.parametrize("heading", ["2026년 4월 김해 제1공장 안전 현황", "[2026년 4월 김해 제1공장 실적]",
                                     "2026년 4월 김해 제1공장 산업재해 현황", "2026년 4월 김해 제1공장"])
@pytest.mark.parametrize("kind", FACTS)
def test_a_confirmed_common_heading_is_inherited(kind, heading):
    quote = f"{heading}\n{FACTS[kind]}"
    result = verdict(quote, site="김해 제1공장")
    assert (result.status, result.scope_from) == ("CONFIRMED", "heading"), (quote, result)
    assert result.evidence_period and result.evidence_site and result.scope_heading


@pytest.mark.parametrize("heading", ["2026년 4월 김해 제1공장 교육 현황", "2026년 4월 김해 제1공장 교육 50명",
                                     "2026년 4월 김해 제1공장 협력사 평가 결과"])
@pytest.mark.parametrize("kind", FACTS)
def test_an_unclear_adjacent_heading_is_not_grounds_for_confirmation(kind, heading):
    result = verdict(f"{heading}\n{FACTS[kind]}", site="김해 제1공장")
    assert result.status == "SOURCE_ONLY" and not result.evidence_period, result


def test_a_heading_under_unrelated_rows_still_reaches_the_table_row():
    quote = "2026년 4월 김해 제1공장 안전 현황\n구분 | 실적\n교육 참여 인원 | 50\n산업재해 발생 건수 | 0"
    result = verdict(quote, site="김해 제1공장")
    assert (result.status, result.scope_from, result.evidence_column) == ("CONFIRMED", "heading", "실적")


@pytest.mark.parametrize("scope,site,cause", [
    ("2026년 3월 김해 제1공장 ", "김해 제1공장", "period_mismatch"),
    ("2026년 4월 부산 제2공장 ", "김해 제1공장", "site_mismatch"),
])
@pytest.mark.parametrize("kind", FACTS)
def test_an_explicit_conflict_in_its_own_scope_is_still_rejected(kind, scope, site, cause):
    quote = scope + FACTS[kind] + ", 2026년 4월 김해 제1공장 교육 참석 인원 50명"
    result = verdict(quote, site=site)
    assert (result.status, result.cause) == ("REJECTED", cause), result


def test_own_scope_wins_over_a_conflicting_heading():
    quote = "2026년 3월 안전 현황\n2026년 4월 산업재해 발생 건수 0건"
    assert (verdict(quote).status, verdict(quote).scope_from) == ("CONFIRMED", "statement")


def test_current_non_possession_followed_by_a_future_plan_is_kept():
    quote = "ISMS 인증은 미보유이며 향후 취득을 계획하고 있다, 2026년 4월 김해 제1공장 교육 참석 인원 50명"
    result = verdict(quote, "ISMS 인증 보유 상태", site="김해 제1공장")
    assert result.status == "SOURCE_ONLY" and not result.evidence_site, result


# ── §5 통합: 원본 응답 → 라우터 → 근거 그래프 → 대표값·원장 → 확인 목록·보고서 입력 ────────

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


def run_response(client, metrics):
    body = "안전·환경 데이터\n" + "\n".join(m["quote"] for m in metrics)
    client.replies = [LLMResponse(content=json.dumps({"metrics": metrics, "clauses": []}),
                                  used_mock=False, meta={"provider": "test-double", "model": "test-double"})]
    ext = router._extract_unstructured_text("safety.pdf", doc_type="report", raw_text=body,
                                            page_texts=[(0, body)])
    graph = EvidenceGraph("LOCAL", "검토")
    merge_ocr_extraction(graph, ext, report_year=2026)
    return ext, graph


def resolve(graph, ext):
    facts = {}
    for node in graph.nodes.values():
        fact = selection._from_nodes(graph, node.metric, [node])
        if fact is not None:
            facts[node.metric] = fact
    graph.resolved_facts = facts
    output = SimpleNamespace(evidence_graph=graph, ocr_extractions=[ext], extraction=None,
                             sections={}, item_retrievals=[])
    findings = build_source_review(output)
    return facts, findings, _block_source_review(SimpleNamespace(review_findings=findings))


def accident_rate(quote, boundary=None):
    return {"metric_hint": "산업재해율", "kesg_code": "S-4-2", "value": 0, "unit": "‰",
            "period": "2026-04", "quote": quote, "boundary": boundary or {}}


def test_normal_unit_zeros_survive_the_whole_path(client):
    ext, graph = run_response(client, [
        {"metric_hint": "온실가스 배출량", "value": 0, "unit": "tCO2eq", "period": "2026-04",
         "quote": "2026년 4월 온실가스 배출량 0 tCO2eq"},
        {"metric_hint": "장기차입금", "value": 0, "unit": "백만원", "period": "2026-04",
         "quote": "2026년 4월 장기차입금 0 백만원"},
    ])
    assert sorted((m.metric_hint, m.value, m.unit) for m in ext.metrics) == \
        [("온실가스 배출량", 0, "tCO2eq"), ("장기차입금", 0, "백만원")]
    assert sorted(m.quote for m in ext.metrics) == ["2026년 4월 온실가스 배출량 0 tCO2eq",
                                                   "2026년 4월 장기차입금 0 백만원"]
    assert not ext.router_meta.get("unvalued_records")
    assert sorted(n.value for n in graph.nodes.values()) == [0, 0]
    _, findings, _ = resolve(graph, ext)
    assert not [f for f in findings if f.check_reason == "zero_not_in_evidence"]


def test_target_zero_of_the_accident_rate_is_excluded_with_its_reason(client):
    quote = "구분 | 목표 | 실적\n교육 참여 인원 | 0 | 50\n산업재해율(‰) | 0 | 1"
    ext, graph = run_response(client, [accident_rate(quote)])
    assert ext.metrics == [] and not graph.nodes
    (entry,) = ext.router_meta["unvalued_records"]
    (record,) = entry["records"]
    assert (record["cause"], record["status"], record["evidence_column"], record["page"]) == \
        ("future_or_intent", "REJECTED", "목표", 0)
    assert record["evidence_offset"] == quote.index("산업재해율(‰) | 0") + len("산업재해율(‰) | ")
    facts, findings, _ = resolve(graph, ext)
    assert "S-4-2" not in facts
    zero = [f for f in findings if f.check_reason == "zero_not_in_evidence"]
    assert zero and any("목표" in f.reason or "계획" in f.reason for f in zero), [f.reason for f in zero]


def test_borrowed_scope_zero_stays_unconfirmed_up_to_the_report_input(client):
    quote = "산업재해율(‰) 0; 2026년 4월 김해 제1공장 교육 참석 인원 50명"
    ext, graph = run_response(client, [accident_rate(quote, {"site": "김해 제1공장"})])
    (node,) = graph.nodes.values()
    assert node.metric == "S-4-2" and node.unit == "‰" and node.value == 0
    boundary = Boundary.from_dict(node.boundary)
    (record,) = [p for p in boundary.provenance if p.get("source") == "zero_evidence"]
    assert (record["status"], record["source_period"], record["source_site"]) == ("SOURCE_ONLY", "", "")
    assert boundary.period_text != "2026년 4월"      # 교육의 4월을 경계에 싣지 않는다
    facts, findings, block = resolve(graph, ext)
    fact = facts["S-4-2"]
    assert fact.value == 0 and "scope_source_only" in fact.flags
    scope = [f for f in findings if f.check_reason == "scope_source_only"]
    assert scope and scope[0].code == "S-4-2", [(f.code, f.check_reason) for f in findings]
    assert block is not None and "범위 미확정" in block.body_md


def test_own_scope_zero_has_no_warning_and_stays_monthly(client):
    ext, graph = run_response(client, [accident_rate("2026년 4월 김해 제1공장 산업재해율(‰) 0",
                                                     {"site": "김해 제1공장"})])
    (node,) = graph.nodes.values()
    assert node.metric == "S-4-2" and node.unit == "‰"
    boundary = Boundary.from_dict(node.boundary)
    assert (boundary.period_text, boundary.aggregation) == ("2026년 4월", "monthly")
    assert covers_full_year(boundary) is False
    (record,) = [p for p in boundary.provenance if p.get("source") == "zero_evidence"]
    assert (record["status"], record["source_unit"], record["unit_location"]) == ("CONFIRMED", "‰", "label")
    facts, findings, _ = resolve(graph, ext)
    assert "scope_source_only" not in facts["S-4-2"].flags
    assert not [f for f in findings if f.check_reason == "scope_source_only"]


@pytest.mark.parametrize("quote,period", [
    ("산업재해율(‰) 0; 2026년 4월 김해 제1공장 교육 참석 인원 50명", ""),
    ("2026년 4월 김해 제1공장 교육 현황\n산업재해율(‰) 0", "2026"),
    ("사용기간 2026-04-01~2026-04-30 김해 제1공장\n산업재해율(‰) 0", "2026"),
])
def test_doc_context_or_model_boundary_never_reconfirms_the_scope(quote, period):
    """원장 병합의 문서 문맥 추론(`derive_boundary`)이 경계를 채워도 범위 미확정 기록은 남는다."""
    issues = []
    metrics, clauses = router._map_vlm_json(
        {"metrics": [dict(metric_hint="산업재해율", kesg_code="S-4-2", value=0, unit="‰", period=period,
                          quote=quote, boundary={"site": "김해 제1공장"})]},
        source_text=quote, page_no=0, issues=issues)
    ext = router.OcrExtraction(source_file="safety.pdf", channel=router.DocChannel.UNSTRUCTURED,
                               doc_type="report", metrics=metrics, clauses=clauses, raw_text=quote)
    graph = EvidenceGraph("LOCAL", "검토")
    merge_ocr_extraction(graph, ext, report_year=2026)
    (node,) = graph.nodes.values()
    assert node.metric == "S-4-2"
    statuses = [p.get("status") for p in Boundary.from_dict(node.boundary).provenance
                if p.get("source") == "zero_evidence"]
    assert statuses == ["SOURCE_ONLY"]
    assert "scope_source_only" in selection._from_nodes(graph, node.metric, [node]).flags


def test_every_zero_cause_has_a_review_text():
    from esgenie import source_review
    assert router._ZERO_CAUSES <= set(source_review._ZERO_CAUSES), \
        router._ZERO_CAUSES - set(source_review._ZERO_CAUSES)
