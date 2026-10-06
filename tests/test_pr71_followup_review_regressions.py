"""PR71 검토(4972df2) R1~R6 보완 회귀 검사.

기대값은 각 입력 원문에서 정했다(검사 대상 함수의 반환값으로 정답을 만들지 않는다). 검토 독립 검사
(`output/reviews/pr71_20261005/test_pr71_*.py`)의 입력 의미를 유지하고, 결함 원인에 직접 닿는 정상·오류 대조
(공백·순서·동의 표현·수치 역할·날짜)를 더했다. 실제 회사·문서의 정답 수치(46·50 등)를 쓰지 않은 대조 입력도 둔다.
네트워크·모델·OCR 호출 없음, 외부 PDF·개인 경로 의존 없음.
"""
from __future__ import annotations

import socket
from types import SimpleNamespace as NS

import pytest

from esgenie.ssot import ocr_router as router
from esgenie.ssot.boundary import Boundary
from esgenie.ssot.evidence_graph import EvidenceGraph, EvidenceNode


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("회귀 검사는 네트워크를 쓰지 않는다")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


# ── R3 콜론 뒤 실제 연도 ─────────────────────────────────────────────────────

@pytest.mark.parametrize("colon", [":", ": ", ":  ", "：", "： "])
@pytest.mark.parametrize("year,status,cause", [(2026, "CONFIRMED", "stated_zero"), (2025, "REJECTED", "period_mismatch")])
def test_a_real_year_after_a_standard_colon_is_kept_regardless_of_spacing(colon, year, status, cause):
    quote = f"GRI 403{colon}{year}년 4월 김해 제1공장 산업재해 발생 건수 0건"
    v = router._zero_verdict(quote, "산업재해 발생 건수", "2026-04", {"site": "김해 제1공장"})
    assert (v.status, v.cause, v.evidence_period) == (status, cause, f"{year}년 4월"), v


@pytest.mark.parametrize("text", ["GRI 403:2026.04 김해 제1공장 산업재해 0건", "GRI 403:2026-04 김해 제1공장 산업재해 0건"])
def test_a_dotted_or_dashed_date_after_the_colon_is_a_period(text):
    v = router._zero_verdict(text, "산업재해", "2026-04", {"site": "김해 제1공장"})
    assert v.status == "CONFIRMED", v


@pytest.mark.parametrize("ref", ["GRI 403:2018", "GRI 403: 2018", "GRI 403：2018", "ISO 45001:2018", "ISO 45001: 2018",
                                 "OHSAS 18001:2007"])
def test_a_standard_revision_year_is_still_not_a_reporting_period(ref):
    # 개정 연도는 가리고, 같은 문구에 따로 적힌 실제 기간은 보존한다.
    masked = router._standard_mask(f"{ref} 기준 2026년 4월 김해 제1공장 산업재해 0건")
    assert ref.split()[-1][-4:] not in masked.split("기준")[0] and "2026년 4월" in masked
    v = router._zero_verdict(f"{ref} 기준 2026년 4월 김해 제1공장 산업재해 0건", "산업재해", "2026-04",
                             {"site": "김해 제1공장"})
    assert (v.status, v.evidence_period) == ("CONFIRMED", "2026년 4월"), v


@pytest.mark.parametrize("space", ["", " "])
def test_a_scope_number_does_not_swallow_the_reporting_year(space):
    masked = router._standard_mask(f"Scope 1:{space}2025 배출량")
    assert "2025" in masked and "1" not in masked.split(":")[0].replace("Scope", "")
    assert [s.text for s in router._date_spans(masked)] == ["2025"]


def test_a_site_identifier_and_a_counted_quantity_survive_the_standard_mask():
    assert "A2공장" in router._standard_mask("GRI 403-9 A2공장 안전 현황")
    assert router._standard_refs("ISO 3건") == []                    # 단위가 붙은 수량은 규격 번호가 아니다


# ── R4 상충 회계연도 정의 ────────────────────────────────────────────────────

CALENDAR = "FY2026 = 2026-01-01~2026-12-31"
APRIL = "FY2026 = 2025-04-01~2026-03-31"


def fiscal(definitions, statement="FY2026 김해 제1공장 안전 현황\n산업재해 발생 건수 0건", site="김해 제1공장"):
    return router._zero_verdict(statement, "산업재해 발생 건수", "2026-01~2026-12", {"site": site},
                                definitions=definitions)


@pytest.mark.parametrize("definitions", [CALENDAR + "\n" + APRIL, APRIL + "\n" + CALENDAR,
                                         APRIL + "\n" + CALENDAR + "\n" + CALENDAR])
def test_conflicting_fiscal_definitions_are_held_in_any_order_with_both_sources(definitions):
    v = fiscal(definitions)
    assert (v.status, v.cause) == ("SOURCE_ONLY", "fiscal_definition_conflict"), v
    traced = [text for where, _role, text, _s, _e in v.scope_spans if where == "fiscal_definition"]
    assert CALENDAR in traced and APRIL in traced
    assert "정의가 서로 다른 구간으로" in router._ZERO_KEPT_CAUSES[v.cause]


def test_a_repeated_identical_definition_or_a_single_definition_still_decides():
    assert fiscal(CALENDAR + "\n" + CALENDAR).status == "CONFIRMED"
    assert fiscal(CALENDAR).status == "CONFIRMED"
    assert (fiscal(APRIL).status, fiscal(APRIL).cause) == ("REJECTED", "period_unproven")
    assert fiscal("").cause == "fiscal_period_undefined"


def test_an_alias_to_a_conflicting_definition_stays_ambiguous():
    statement = "FY26 김해 제1공장 안전 현황\n산업재해 발생 건수 0건"
    v = fiscal("FY26 = FY2026\n" + CALENDAR + "\n" + APRIL, statement)
    assert (v.status, v.cause) == ("SOURCE_ONLY", "fiscal_definition_conflict")
    assert fiscal("FY26 = FY2026\n" + CALENDAR, statement).status == "CONFIRMED"          # 단일 정의는 그대로 잇는다
    # 별칭과 직접 정의가 서로 다른 구간을 주는 것도 충돌이다.
    assert fiscal("FY26 = FY2026\n" + CALENDAR + "\nFY26 = 2025-04-01~2026-03-31", statement).cause \
        == "fiscal_definition_conflict"


def test_a_definition_stated_for_a_site_applies_only_to_that_site():
    qualified = "김해 제1공장 " + CALENDAR + "\n부산 제2공장 " + APRIL
    assert fiscal(qualified).status == "CONFIRMED"
    assert fiscal("부산 제2공장 " + APRIL + "\n김해 제1공장 " + CALENDAR).status == "CONFIRMED"
    # 사업장이 적히지 않은 정의가 다른 구간을 주면 적용 관계를 원문으로 가를 수 없다.
    assert fiscal("김해 제1공장 " + CALENDAR + "\n" + APRIL).cause == "fiscal_definition_conflict"


# ── R6 참석·미참석·대상 칸 역할 ──────────────────────────────────────────────

def mapped(hint, value, source, quote):
    issues = []
    metrics, _ = router._map_vlm_json({"metrics": [{"metric_hint": hint, "value": value, "unit": "명",
                                                    "period": "2026-04", "quote": quote}]},
                                      page_no=0, source_text=source, issues=issues)
    return metrics, issues


ORDERS = {
    "대상 먼저": ("출석 집계\n대상 | 참석 | 미참석\n30명 | 27명 | 3명", "30명 | 27명 | 3명"),
    "미참석 먼저": ("출석 집계\n미참석 | 참석 | 대상\n3명 | 27명 | 30명", "3명 | 27명 | 30명"),
}


@pytest.mark.parametrize("order", ORDERS)
@pytest.mark.parametrize("hint,value,role", [("교육 미참석 인원", 27, "참석"), ("교육 참석 인원", 3, "미참석"),
                                             ("교육 출석 인원", 30, "대상")])
def test_a_wrong_role_label_is_corrected_both_ways_in_either_column_order(order, hint, value, role):
    source, quote = ORDERS[order]
    (m,), issues = mapped(hint, value, source, quote)
    assert m.metric_hint.endswith(f"· {role}") and (role != "참석" or "미참석" not in m.metric_hint), m.metric_hint
    (record,) = [p for p in m.boundary["provenance"] if p.get("source") == "label_check"]
    assert (record["model_label"], record["column_header"], record["source_cell"]) == (hint, role, f"{value}명")
    assert record["label_reason"] in ("label_names_other_column", "label_lacks_column")
    (issue,) = [i for i in issues if i["reason"] == "label_from_table_header"]
    assert issue["model_label"] == hint and issue["source_cell"] == f"{value}명"


@pytest.mark.parametrize("order", ORDERS)
@pytest.mark.parametrize("hint,value", [("교육 참석 인원", 27), ("교육 미참석 인원", 3), ("교육 대상 인원", 30)])
def test_a_label_that_already_names_its_column_is_kept(order, hint, value):
    source, quote = ORDERS[order]
    (m,), issues = mapped(hint, value, source, quote)
    assert m.metric_hint == hint and not [i for i in issues if i["reason"] == "label_from_table_header"]


def test_a_negated_label_is_corrected_even_without_the_opposite_column():
    (m,), _ = mapped("미참석 인원", 27, "출석 집계\n대상 | 참석\n30명 | 27명", "30명 | 27명")
    assert m.metric_hint.endswith("· 참석")


def test_a_value_repeated_in_two_cells_is_not_given_a_role():
    (m,), issues = mapped("교육 미참석 인원", 27, "출석 집계\n대상 | 참석 | 미참석\n27명 | 27명 | 0명", "27명 | 27명 | 0명")
    assert m.metric_hint == "교육 미참석 인원" and not [i for i in issues if i["reason"] == "label_from_table_header"]


def test_relabelling_runs_after_the_zero_check_and_leaves_the_zero_record_alone():
    # 0 판정은 모델 라벨로 먼저 하고, 라벨 정정은 그 뒤에만 돈다 — 0의 판정 기록·라벨을 바꾸지 않는다.
    source = "2026년 4월 김해 제1공장 안전 현황\n구분 | 건수\n산업재해 발생 | 0건\n아차사고 | 3건"
    quote = "구분 | 건수\n산업재해 발생 | 0건\n아차사고 | 3건"
    rows = [{"metric_hint": "산업재해 발생 건수", "value": 0, "unit": "건", "period": "2026-04", "quote": quote,
             "boundary": {"site": "김해 제1공장"}},
            {"metric_hint": "아차사고 건수", "value": 3, "unit": "건", "period": "2026-04", "quote": quote}]
    metrics, _ = router._map_vlm_json({"metrics": rows}, page_no=0, source_text=source, issues=[])
    zero, three = sorted(metrics, key=lambda m: m.value)
    assert (zero.metric_hint, zero.value, three.metric_hint) == ("산업재해 발생 건수", 0, "아차사고 건수")
    assert [p["source"] for p in zero.boundary["provenance"]] == ["zero_evidence"]       # 정정 기록 없음


# ── R5 같은 비율 후보 ────────────────────────────────────────────────────────

APRIL_SITE = dict(period_start="2026-04-01", period_end="2026-04-30", period_year=2026, site="김해 제1공장",
                  site_scope="site", basis="actual")


def candidate_graph(**overrides):
    graph = EvidenceGraph("LOCAL", "검토")
    graph.report_year = 2026
    boundary = {**APRIL_SITE, **overrides.pop("boundary", {})}
    graph.add_node(EvidenceNode("scrap_rate", "생산 스크랩 내부 재투입률", 92.0, "%", overrides.pop("period", 2026),
                                "ocr/report", origin="ocr", source_file="scrap.pdf",
                                value_role=overrides.pop("role", "actual"),
                                quote="생산 스크랩 내부 재투입률 92%\n계산: 내부 재투입 23 kg ÷ 발생 25 kg × 100 = 92%",
                                boundary=Boundary.from_dict(boundary)))
    return graph


def traced(graph, context, texts):
    from esgenie.supplychain.claims import ClaimSet, SupplierClaim, trace_claim_values
    claim = SupplierClaim("E-6-2", 92.0, "%", raw="2026년 4월 김해 제1공장 사업장폐기물 재활용률 92%",
                          source="saq:answer.pdf", context={"scope_from": "answer_text", **context},
                          boundary={"period_start": "2026-04-01", "period_end": "2026-04-30",
                                    "site": "김해 제1공장", "site_scope": "site"})
    return trace_claim_values(ClaimSet({"E-6-2": claim}), NS(evidence_graph=graph, ocr_extractions=[
        NS(source_file=f, raw_text=t) for f, t in texts.items()]))["E-6-2"].context


@pytest.mark.parametrize("kind,graph_kw", [
    ("different_scope", dict(period=2025, boundary=dict(period_start="2025-01-01", period_end="2025-12-31",
                                                         period_year=2025, site="부산 제2공장"))),
    ("target", dict(role="target", boundary=dict(basis="target"))),
])
def test_an_equal_rate_without_a_direct_link_is_only_a_lead(kind, graph_kw):
    ctx = traced(candidate_graph(**graph_kw), {}, {"scrap.pdf": "SCR-001"})
    assert ctx["value_trace"] == []
    (lead,) = ctx["value_leads"]
    expected = {"different_scope": ("different", "different", "actual"), "target": ("same", "same", "target")}[kind]
    assert (lead["scope_check"]["period"], lead["scope_check"]["site"], lead["scope_check"]["role"]) == expected


def test_a_missing_memo_document_is_recorded_and_not_replaced_by_an_unrelated_equal_value():
    ctx = traced(candidate_graph(), {"source_ids": ["OTHER-SRC-202601"]}, {"scrap.pdf": "SCR-001"})
    assert ctx["value_trace"] == [] and ctx["note_link"]["missing"] == ["OTHER-SRC-202601"]
    assert ctx["note_link"]["found_in"] == []


def test_a_memo_linked_source_keeps_its_scope_check():
    ctx = traced(candidate_graph(), {"source_ids": ["SCR-001"]}, {"scrap.pdf": "SCR-001 공정 스크랩"})
    (trace,) = ctx["value_trace"]
    assert trace["linked_by_note"] and trace["scope_check"]["period"] == "same"


def waste_answer(context, graph_kw=None, basis_quote="계산: 5 kg ÷ 20 kg × 100 = 25%"):
    """요청 지표 증빙 25%(부분값)와 회사 답변 92% — 답변 생성 경로 전체(원장 → DataPoint → 답변)."""
    from esgenie.ssot import selection
    from esgenie.ssot.audit_trace import build_data_points
    from esgenie.supplychain.claims import ClaimSet, SupplierClaim, _stated_scope, trace_claim_values
    from esgenie.supplychain.frameworks import get_framework
    from esgenie.supplychain.mapping import derive_answer
    graph = candidate_graph(**(graph_kw or {}))
    april = dict(period_start="2026-04-01", period_end="2026-04-30", period_year=2026, aggregation="monthly",
                 coverage_months=1, site="제1공장", site_scope="site", site_path=("제1공장",), measure_kind="waste",
                 completeness="partial")
    graph.add_node(EvidenceNode("LOCAL_E-6-2", "E-6-2", 25.0, "%", 2026, "ocr/waste", origin="ocr_structured",
                                source_file="waste.pdf", value_role="actual",
                                boundary=Boundary.from_dict({**april, "provenance": [
                                    {"source": "document_header", "quote": basis_quote}]})))
    result = NS(mapped={"E-6-2": {"value": 25.0, "unit": "%", "source_tier": "ocr_node_gated", "name": "폐기물 재활용 비율",
                                  "area": "E"}}, confidence_flags={})
    selection.finalize_ledger(result, graph)
    raw = "2026년 4월 김해 제1공장 사업장폐기물 재활용률 92%"
    claims = ClaimSet({"E-6-2": SupplierClaim("E-6-2", 92.0, "%", raw=raw, source="saq:answer.pdf",
                                              boundary=_stated_scope(raw),
                                              context={"scope_from": "answer_text", **context})})
    texts = [NS(source_file="scrap.pdf", raw_text="SCR-001 공정 스크랩")]
    claims = trace_claim_values(claims, NS(evidence_graph=graph, ocr_extractions=texts))
    q = next(q for q in get_framework("rba42").questions if q.primary_code == "E-6-2" and q.qtype == "numeric")
    points = {p.kesg_code: p for p in build_data_points(graph, {"E-6-2": 0.0}, target_codes=["E-6-2"])}
    return derive_answer(q, mapped=result.mapped, missing=set(), dp_by_code=points, claims=claims)


@pytest.mark.parametrize("context,graph_kw,missing_word", [
    ({}, dict(period=2025, boundary=dict(period_start="2025-01-01", period_end="2025-12-31", period_year=2025,
                                         site="부산 제2공장")), None),
    ({}, dict(role="target", boundary=dict(basis="target")), None),
    ({"source_ids": ["OTHER-SRC-202601"]}, None, "OTHER-SRC-202601"),
])
def test_the_answer_names_what_is_missing_instead_of_a_transcription_source(context, graph_kw, missing_word):
    ans = waste_answer(context, graph_kw)
    assert ans.comparison == "scope_unconfirmed" and ans.status != "flagged"
    assert "옮긴" not in ans.comparison_reason and "scrap.pdf" not in ans.comparison_reason
    assert "분모가 적혀 있지 않아" in ans.comparison_reason
    if missing_word:
        assert f"작성 메모가 가리킨 문서({missing_word})를 제출 자료에서 찾지 못해" in ans.comparison_reason


def test_a_memo_linked_source_of_another_period_is_explained_as_a_mismatch_not_as_evidence():
    ans = waste_answer({"source_ids": ["SCR-001"]}, dict(period=2025, boundary=dict(
        period_start="2025-01-01", period_end="2025-12-31", period_year=2025)))
    assert ans.comparison == "not_comparable" and ans.status == "flagged"
    assert "작성 메모가 가리킨 scrap.pdf" in ans.comparison_reason and "기간 2025-01-01~2025-12-31" in ans.comparison_reason
    assert "요청 범위의 실적 근거로 쓰지 않았고" in ans.comparison_reason


def test_a_memo_linked_source_with_both_formulas_keeps_the_denominator_explanation():
    ans = waste_answer({"source_ids": ["SCR-001"]})
    assert (ans.status, ans.comparison) == ("flagged", "not_comparable")
    for text in ("23 kg ÷ 발생 25 kg", "5 kg ÷ 20 kg", "두 원문 계산식의 분모가 달라", "확정하지 않았습니다"):
        assert text in ans.comparison_reason, (text, ans.comparison_reason)


def test_a_memo_linked_source_without_the_request_formula_does_not_assert_a_transcription():
    ans = waste_answer({"source_ids": ["SCR-001"]}, basis_quote="재활용 비율 25%")
    assert ans.comparison == "scope_unconfirmed" and "요청 지표 증빙의 계산식(분모)이 원문에 적혀 있지 않아" in ans.comparison_reason
    assert "옮긴 것으로 보여" not in ans.comparison_reason


# ── R2·R1 생성 본문 대조 ─────────────────────────────────────────────────────

def report_output(flags=("scope_source_only",), notes=("원문에 기간 표기가 없어 요청 기간의 실적인지 확인하지 못했습니다.",)):
    return NS(extraction=NS(mapped={"S-4-2": {
        "name": "산업재해율", "area": "S", "value": 0, "unit": "‰",
        "resolved_fact": {"flags": list(flags), "boundary": {}, "completeness": "unknown", "scope_notes": list(notes)},
    }}))


def generated(text, chunks, facts=None):
    from esgenie.embeddings import IndexedDoc
    docs = [(IndexedDoc(text=t, meta={}, chunk_id=c), 1.0) for c, t in chunks.items()]
    if facts is not None:
        docs.append((IndexedDoc(text="facts", meta={"source": "source_facts", "facts": facts},
                                chunk_id="source_facts_S"), 1.0))
    context = NS(all_hits=lambda: docs)
    return NS(final=NS(generation=NS(text=text, context=context)), final_text=text)


def review(text, chunks=None, facts=None, output=None):
    from esgenie.layer6_report import annotate_generated_text
    return annotate_generated_text(output or report_output(), "S",
                                   generated(text, chunks or {"c1": "산업재해율 0‰. 원문 기간 미상."}, facts))


@pytest.mark.parametrize("text", [
    "산업재해율은 0‰로 확인되었습니다 [c1].",
    "산재율은 0‰로 확인되었습니다 [c1].",                                      # 동의 표현 — 값+단위로 찾는다
    "산업재해율은 0‰이며 추가 확인할 사항 없이 확정되었습니다 [c1].",            # 부정된 유보는 유보가 아니다
    "산업재해율 제로를 달성했다 [c1].",                                          # 숫자 없는 0 단정
])
def test_a_source_only_zero_asserted_in_prose_is_replaced_with_its_state_and_reason(text):
    body, marks = review(text)
    assert [(m["reason"], m["action"]) for m in marks] == [("source_only_stated", "replaced")], marks
    assert body.startswith("[범위 미확정] 산업재해율 0‰는 원문 범위") and "확정되었습니다" not in body
    assert "원문에 기간 표기가 없어" in body and marks[0]["model_text"].startswith(text.split(" [")[0][:10])


def test_a_source_only_zero_in_a_confirmed_actuals_table_loses_the_confirmed_label():
    body, marks = review("| 지표 | 확정 실적 |\n|---|---|\n| 산업재해율 | 0‰ | [c1]")
    header, _sep, row = body.splitlines()
    assert "확정" not in header and "실적" in header
    assert "0‰ [범위 미확정: 원문 범위로만 확인된 참고값]" in row
    assert [m["reason"] for m in marks] == ["source_only_stated"]


def test_a_source_only_zero_heading_is_not_an_achievement():
    body, marks = review("# 산업재해율 0‰ 달성 [c1]")
    assert body.startswith("# 산업재해율 0‰ (범위 미확정 참고값") and "달성" not in body
    assert marks[0]["reason"] == "source_only_stated"


def test_a_real_reference_sentence_and_a_confirmed_zero_are_kept():
    body, marks = review("산업재해율 0‰는 원문 범위로만 확인된 참고값이다 [c1].")
    assert marks == [] and body.startswith("산업재해율 0‰는 원문 범위로만 확인된 참고값이다")
    body, marks = review("산업재해율은 0‰로 확인되었습니다 [c1].", output=report_output(flags=(), notes=()))
    assert marks == [] and body.startswith("산업재해율은 0‰로 확인되었습니다")


def test_a_goal_sentence_about_a_source_only_zero_is_only_marked():
    body, marks = review("산업재해율 0‰ 유지를 목표로 한다 [c1].")
    assert [(m["reason"], m["action"]) for m in marks] == [("source_only_stated", "marked")]
    assert "목표로 한다" in body and "[검토: 범위 미확정" in body


def test_the_summary_follows_the_same_state_contract():
    from esgenie.layer6_report import annotate_summary_text
    body, marks = annotate_summary_text(report_output(), "산재율 0‰로 무재해를 달성했다. 교육을 실시했다.")
    assert body.startswith("[범위 미확정] 산업재해율 0‰") and "교육을 실시했다." in body
    assert marks[0]["action"] == "replaced"


@pytest.mark.parametrize("sentence,citation,held", [
    ("2026년 4월 15일 교육에 정규직 15명이 출석했습니다 [c1].",
     "교육일 2026년 4월 15일. 참석자: 정규직 40명, 기간제 6명, 합계 46명.", True),     # 날짜의 15는 인원 근거가 아니다
    ("2026년 4월 15일 교육에 정규직 15명이 출석했습니다 [c1].",
     "교육일 2026년 4월 15일. 참석자: 정규직 15명, 기간제 6명, 합계 21명.", False),    # 근거에 실제 15명이 있으면 보존
    ("교육에 HN-G15까지 15명이 출석했다 [c1].", "출석 대장 HN-G01 … HN-G15 출석", True),  # 식별자 안의 숫자도 근거가 아니다
])
def test_a_quantity_is_grounded_by_a_quantity_not_by_a_date_or_identifier(sentence, citation, held):
    body, marks = review(sentence, {"c1": citation}, output=NS(extraction=NS(mapped={})))
    if held:
        assert any(m["reason"] == "orphan_number" and m["action"] == "replaced" and "15" in m["numbers"] for m in marks)
        assert "15명이 출석" not in body and body.startswith("[확인 보류]")
    else:
        assert marks == [] and "정규직 15명이 출석" in body


# 실제 사례와 다른 값·이름의 교육 집계: 6/3 대상 30·참석 27·미참석 3, 6/9 추가 3, 6월 중복 제외 30.
FACTS = [
    {"label": "이번 교육 출석 집계 · 대상", "value": 30.0, "unit": "명", "period_text": "2026-06-03", "source_file": "a.pdf"},
    {"label": "교육 참석 인원", "value": 27.0, "unit": "명", "period_text": "2026-06-03", "source_file": "a.pdf"},
    {"label": "교육 미참석 인원", "value": 3.0, "unit": "명", "period_text": "2026-06-03", "source_file": "a.pdf"},
    {"label": "6월 9일 추가 참석 · 고유 인원", "value": 3.0, "unit": "명", "period_text": "2026-06-09", "source_file": "b.pdf"},
    {"label": "중복 제외 합계 · 고유 인원", "value": 30.0, "unit": "명", "period_text": "2026-06", "source_file": "b.pdf"},
]


@pytest.mark.parametrize("sentence", [
    "2026년 6월 3일 교육 대상 30명 중 27명이 참석했고 3명은 미참석했다 [source_facts_S].",
    "6월 9일 추가 교육에 3명이 참석해 중복 제외 고유 인원은 30명이다 [source_facts_S].",
    "6월 3일 참석 27명과 6월 9일 추가 참석 3명을 합한 중복 제외 30명이 교육을 받았다 [source_facts_S].",
])
def test_correct_attendance_sentences_survive(sentence):
    body, marks = review(sentence, {}, FACTS, output=NS(extraction=NS(mapped={})))
    assert marks == [] and body.startswith(sentence.split(" [")[0][:20])


# PR71 재검토(2026-10-05): 값은 있으나 관계가 어긋난 사유 코드 `role_or_date_mismatch`를 `relation_mismatch`로 넓혔다
# (역할·날짜에 고용형태·사업장을 더함). 어긋난 관계는 `problems`에 남는다 — 같은 입력의 기대 의미(그 관계로 보류)는 같다.
@pytest.mark.parametrize("sentence,reason,problems", [
    ("2026년 6월 3일 교육에는 정규직 11명과 기간제 4명이 출석하였다 [source_facts_S].", "orphan_number", set()),
    ("2026년 6월 교육에 총 15명이 참석하였다 [source_facts_S].", "orphan_number", set()),
    ("2026년 6월 3일 교육에는 30명이 참석했다 [source_facts_S].", "relation_mismatch", {"role", "date"}),   # 대상·월 합계를 그날 참석으로
    ("2026년 6월 3일 교육에서 27명이 미참석했다 [source_facts_S].", "relation_mismatch", {"role"}),
    ("명단 ID 30개를 검증했다 [source_facts_S].", "orphan_number", set()),
])
def test_wrong_attendance_sentences_are_held_with_the_confirmed_values(sentence, reason, problems):
    body, marks = review(sentence, {}, FACTS, output=NS(extraction=NS(mapped={})))
    (mark,) = [m for m in marks if m["action"] == "replaced"]
    assert mark["reason"] == reason and mark["model_text"].rstrip(" .") == sentence.split(" [")[0]
    assert problems <= set(mark.get("problems") or ()), mark
    assert body.startswith("[확인 보류]") and sentence.split(" [")[0] not in body
    if "6월 3일" in sentence or "6월 교육" in sentence:
        assert "교육 참석 인원 27명(2026-06-03)" in body


@pytest.mark.parametrize("sentence", [
    # PR71 후속 실측(실제 생성 문장)과 같은 구조, 다른 값: 뒤 수량의 명사(`총 대상 인원은`)를 앞 수량의 역할로 읽지 않는다.
    "2026년 6월 3일 기준 교육 참석 인원은 27명이며, 미참석 인원은 3명으로 총 대상 인원은 30명이다 [source_facts_S].",
    "6월 9일에는 미참석자 3명이 추가 교육에 참여하여 전체 대상자의 교육 참석을 마쳤다 [source_facts_S].",
    "대상 30명 중 참석 27명, 미참석 3명이다 [source_facts_S].",
    "27명 출석 / 3명 미참석 [source_facts_S].",
])
def test_role_words_belong_to_their_own_quantity(sentence):
    body, marks = review(sentence, {}, FACTS, output=NS(extraction=NS(mapped={})))
    assert marks == [], marks


@pytest.mark.parametrize("sentence,roles", [
    ("대상 50명 중 46명이 참석하고 4명이 미참석했다", ["대상", "참석", "미참석"]),
    ("대상 50명 중 참석 46명, 미참석 4명", ["대상", "참석", "미참석"]),
    ("미참석 인원은 4명으로 총 대상 인원은 50명이다", ["미참석", "대상"]),
    ("미참석자 4명이 추가 교육에 참여하여", ["참석"]),                 # 그날의 서술어가 역할이다
    ("27명은 참석하지 않았다", ["미참석"]),
    ("정규직 15명과 기간제 6명이 출석하였고", ["", "참석"]),          # 역할을 끌어오지 않는다
])
def test_count_role_reads_the_predicate_after_or_the_noun_before(sentence, roles):
    from esgenie import report_claims as rc
    found = rc.quantities(sentence)
    assert [rc.count_role(w, a, b) for _q, w, a, b, _s, _e in rc.contexts(sentence, found)] == roles


def test_the_section_shows_a_deterministic_table_of_dated_role_facts():
    from esgenie.layer2_rag import _render_source_facts_table
    from esgenie.report_claims import label_role
    # 합계 행은 그래프에서 value_role=total로 온다(`중복 제외 합계`). 참여 역할 행은 라벨로 역할을 읽는다.
    rows = [dict(r, role=label_role(r["label"]), page=0,
                 value_role="total" if "합계" in r["label"] else "unknown") for r in FACTS]
    rows.append({"label": "내부 재투입", "value": 2760.0, "unit": "kg", "role": "", "period_text": "04-01 ~ 04-07",
                 "value_role": "unknown", "source_file": "c.pdf", "page": 0})      # 주간 행 — 표에 싣지 않는다
    rows.append({"label": "사업장별 인원 - 제1공장", "value": 30.0, "unit": "명", "role": "", "period_text": "2026-06-30",
                 "value_role": "component", "source_file": "d.pdf", "page": 0})    # 구성값 — 표에 싣지 않는다
    rows.append({**rows[1], "label": "출석 인원", "source_file": "a.pdf"})          # 같은 값·역할·기간 — 한 줄
    table = _render_source_facts_table(rows)
    assert "### 원문 확인 수치(K-ESG 코드 없음)" in table
    assert "| 교육 참석 인원 | 27명 | 2026-06-03 | 원문 미기록 | a.pdf 1쪽 |" in table
    assert "| 6월 9일 추가 참석 · 고유 인원 | 3명 | 2026-06-09 | 원문 미기록 | b.pdf 1쪽 |" in table
    assert "| 중복 제외 합계 · 고유 인원 | 30명 | 2026-06 | 원문 미기록 | b.pdf 1쪽 |" in table
    assert "내부 재투입" not in table and "사업장별 인원" not in table and "| 출석 인원 |" not in table
    assert _render_source_facts_table([]) == ""
    labels = [line.split(" | ")[0][2:] for line in table.splitlines() if line.startswith("| ") and "---" not in line][1:]
    # 기간 끝 순(6/3 대상·참석·미참석 → 6/9 추가 → 6월 중복 제외 합계)
    assert labels == ["이번 교육 출석 집계 · 대상", "교육 참석 인원", "교육 미참석 인원", "6월 9일 추가 참석 · 고유 인원",
                      "중복 제외 합계 · 고유 인원"]


def test_a_month_level_hold_lists_the_follow_up_and_the_deduplicated_total():
    body, _ = review("2026년 6월 교육에 총 15명이 참석하였다 [source_facts_S].", {}, FACTS,
                     output=NS(extraction=NS(mapped={})))
    assert "6월 9일 추가 참석 · 고유 인원 3명(2026-06-09)" in body and "중복 제외 합계 · 고유 인원 30명(2026-06)" in body
    day, _ = review("2026년 6월 3일 교육에는 정규직 11명이 출석하였다 [source_facts_S].", {}, FACTS,
                    output=NS(extraction=NS(mapped={})))
    assert "중복 제외 합계" not in day                                  # 하루의 문장에 월 합계를 붙이지 않는다


def test_generation_input_carries_uncoded_aggregates_with_role_date_and_source():
    from esgenie.layer2_rag import RAGContext, _source_facts_chunk
    from esgenie.embeddings import IndexedDoc
    graph = EvidenceGraph("LOCAL", "검토")
    graph.report_year = 2026
    graph.add_node(EvidenceNode("t1", "교육 참석 인원", 27.0, "명", 2026, "ocr/a", origin="ocr_unstructured",
                                source_file="a.pdf", quote="대상 | 참석\n30명 | 27명",
                                boundary=Boundary.from_dict({"period_text": "2026-06-03"})))
    graph.add_node(EvidenceNode("t2", "교육 참석 인원", 26.0, "명", 2026, "ocr/a", origin="ocr_unstructured",
                                source_file="a.pdf", quote="대상 | 참석\n30명 | 27명"))       # 값이 인용에 없다 — 모델 셈
    graph.add_node(EvidenceNode("t3", "합계", 48.0, "시간", 2026, "ocr/a", origin="ocr_unstructured",
                                source_file="a.pdf", quote="HN-01 | 48"))                   # 대상 없는 라벨
    graph.add_node(EvidenceNode("t4", "교육 참석 인원", 27.0, "명", 2026, "ocr/other", origin="ocr_unstructured",
                                source_file="other.pdf", quote="참석 27명"))                # 생성 문맥 밖 문서
    ctx = RAGContext(kesg_hits=[], industry_hits=[], corp_hits=[
        (IndexedDoc(text="교육 기록", meta={"source_file": "a.pdf"}, chunk_id="c1"), 1.0)])
    chunk = _source_facts_chunk(graph, ctx, "S")
    assert [(r["label"], r["value"], r["role"], r["period_text"], r["source_file"]) for r in chunk.meta["facts"]] \
        == [("교육 참석 인원", 27.0, "참석", "2026-06-03", "a.pdf")]
    assert "[역할: 참석]" in chunk.text and "[기간: 2026-06-03]" in chunk.text and "[출처: a.pdf" in chunk.text
    assert _source_facts_chunk(None, ctx, "S") is None


def test_the_section_prompt_shows_the_facts_only_when_there_are_any(monkeypatch):
    from esgenie.layer2_rag import HybridRAG, RAGContext, IndexedDoc
    from esgenie import layer2_rag
    sent = []
    monkeypatch.setattr(layer2_rag.CLIENT, "complete",
                        lambda system, user, **kw: sent.append(user) or NS(content="## 사회 성과", used_mock=True))
    graph = EvidenceGraph("LOCAL", "검토")
    graph.add_node(EvidenceNode("t1", "교육 참석 인원", 27.0, "명", 2026, "ocr/a", origin="ocr_unstructured",
                                source_file="a.pdf", quote="참석 27명"))
    report = NS(corp_name="가상", industry="제조", report_year=2026, to_context_dict=lambda: {})
    for g in (graph, None):
        ctx = RAGContext(kesg_hits=[], industry_hits=[], corp_hits=[
            (IndexedDoc(text="교육 기록", meta={"source_file": "a.pdf"}, chunk_id="c1"), 1.0)])
        object.__new__(HybridRAG)._generate_section_v2(
            report, "S", "사회", ctx, covered=[], missing=[], extra_instruction=None, demo_greenwash=False,
            system="s", evidence_graph=g)
    with_facts, without = sent
    assert "[원문 확인 수치(K-ESG 코드 없음)]" in with_facts and "- 교육 참석 인원: 27명 [역할: 참석]" in with_facts
    assert "[원문 확인 수치" not in without and "원장 밖 인원·물량은" not in without
