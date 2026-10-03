"""PR 69 3차 검토(2026-09-30, 7b54d06)에서 찾은 네 경로의 회귀 테스트.

F1 분모를 읽지 못한 복합 단위(`MJ/\n톤`·`MJ/(톤)`·`MJ/100개`)가 분자 단위로 되돌아갔다.
F2 숫자 0은 사실성·기간 검사를 건너뛰고 채택됐다(`목표 0건`·`4월 1일 기준 0건`).
F3 거부 목록에 없는 부정 서술(조건·가정)이 기본값으로 사실이 됐다.
F4 연도 기간이 그해 월간 사실과 일치로 처리돼 명시적 연간 요청에도 채택됐다.

검토 산출물의 12개 테스트(`output/reviews/pr69_20260930/third_review/test_pr69_remaining_guards.py`)를
개인 경로·`REVIEW_CODE` 없이 옮기고, 조합·동등성·후속 원장 검사를 붙였다. 모든 문구는 합성 입력이다.
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


# ── 검토 산출물 12건(실패 8 · 정상 대조 4) ─────────────────────────────────

def mapped(quote, *, hint="산업재해 발생 건수", value=0, unit="건", period="2026-04"):
    issues = []
    metrics, _ = router._map_vlm_json(
        {"metrics": [{"metric_hint": hint, "value": value, "unit": unit,
                      "period": period, "quote": quote}]},
        source_text=quote, page_no=0, issues=issues,
    )
    return metrics, issues


@pytest.mark.parametrize("denominator", ["\n톤", "(톤)", "100개"],
                         ids=["line_break", "parentheses", "number"])
def test_unparsed_denominator_cannot_fall_back_to_numerator(denominator):
    quote = f"에너지 사용량 2 GJ + 500 MJ, 에너지 사용량 1만 2천 MJ/{denominator}"
    metrics, issues = mapped(quote, hint="에너지 사용량", value=2.5, unit="GJ")
    assert [m.value for m in metrics] == [2.5], issues
    assert not any(i["reason"] == "scale_chain_recomposed" for i in issues)


def test_numeric_zero_target_is_not_actual_zero():
    metrics, issues = mapped("2026년 4월 산업재해 목표 0건")
    assert not metrics, [(m.value, m.quote) for m in metrics]
    assert issues[0]["reason"] == "zero_not_in_evidence"


def test_numeric_zero_as_of_one_day_is_not_monthly_zero():
    metrics, issues = mapped("2026-04-01 기준 산업재해 0건")
    assert not metrics, [(m.value, m.period, m.quote) for m in metrics]
    assert issues[0]["cause"] == "period_unproven"


@pytest.mark.parametrize("quote", [
    "산업재해가 발생하지 않으면 사고대장을 작성하지 않는다.",
    "산업재해가 발생하지 않는다고 가정한다.",
], ids=["conditional", "assumption"])
def test_unrecognized_mood_is_not_an_observed_fact(quote):
    metrics, issues = mapped(quote)
    assert not metrics, [(m.value, m.quote) for m in metrics]
    assert issues[0]["reason"] == "zero_not_in_evidence"


def test_explicit_annual_scope_is_not_bare_report_year():
    metrics, issues = mapped("2026년 4월 산업재해는 발생하지 않았다.", period="2026년 연간")
    assert not metrics, [(m.value, m.period, m.quote) for m in metrics]
    assert issues[0]["cause"] == "period_unproven"


def test_supported_compound_unit_stays_safe():
    metrics, issues = mapped("에너지 사용량 2 GJ + 500 MJ, 에너지 사용량 1만 2천 MJ/톤",
                             hint="에너지 사용량", value=2.5, unit="GJ")
    assert [m.value for m in metrics] == [2.5], issues


def test_actual_numeric_zero_stays():
    metrics, issues = mapped("2026년 4월 산업재해 발생 건수 0건")
    assert [m.value for m in metrics] == [0] and not issues


def test_actual_negative_fact_stays():
    metrics, issues = mapped("2026년 4월 산업재해는 발생하지 않았다.")
    assert [m.value for m in metrics] == [0] and not issues


def test_current_non_possession_with_future_plan_stays():
    metrics, issues = mapped("ISMS 인증은 미보유이며 향후 취득을 계획하고 있다.",
                             hint="ISMS 인증 보유 상태")
    assert [m.value for m in metrics] == [0] and not issues


# ── F4 — 보고 연도만 받은 요청과 명시적 연간 요청을 가른다(숫자·부정 공통) ─────────

APRIL_FACTS = {"numeric": "2026년 4월 산업재해 발생 건수 0건",
               "negation": "2026년 4월 산업재해는 발생하지 않았다."}


@pytest.mark.parametrize("kind", APRIL_FACTS)
def test_report_year_request_keeps_the_april_fact_with_its_april_scope(kind):
    metrics, issues = mapped(APRIL_FACTS[kind], period="2026")
    assert [m.value for m in metrics] == [0] and issues == [], issues
    boundary = metrics[0].boundary
    assert (boundary["period_text"], boundary["aggregation"]) == ("2026년 4월", "monthly")
    assert (boundary["period_start"], boundary["period_end"]) == ("2026-04-01", "2026-04-30")
    (record,) = [p for p in boundary["provenance"] if p["source"] == "zero_evidence"]
    assert (record["status"], record["cause"], record["candidate"]) == ("SOURCE_ONLY", "report_year_only", kind)
    assert record["requested_period"] == "2026" and record["requested_scope"] == "REPORT_YEAR"


@pytest.mark.parametrize("period,hint", [("2026년 연간", "산업재해 발생 건수"),
                                         ("2026-01-01~2026-12-31", "산업재해 발생 건수"),
                                         ("2026", "연간 산업재해 발생 건수")])
@pytest.mark.parametrize("kind", APRIL_FACTS)
def test_explicit_annual_request_rejects_the_april_fact(kind, period, hint):
    metrics, issues = mapped(APRIL_FACTS[kind], period=period, hint=hint)
    assert metrics == []
    (issue,) = issues
    assert (issue["cause"], issue["status"], issue["requested_scope"]) == ("period_unproven", "REJECTED", "ANNUAL")
    assert issue["evidence_period"] == "2026년 4월" and issue["page"] == 0


def test_rejected_zero_keeps_page_none_apart_from_page_zero():
    issues = []
    router._map_vlm_json({"metrics": [{"metric_hint": "산업재해 발생 건수", "value": 0, "unit": "건",
                                       "period": "2026-04", "quote": "산업재해 목표 0건"}]},
                         source_text="산업재해 목표 0건", page_no=None, issues=issues)
    (issue,) = issues
    assert issue["page"] is None and issue["value"] == 0 and issue["evidence_text"] == "산업재해 목표 0건"


# ── 조합 — 분모 불변식(F1 판독과 실제 보정 경로) ─────────────────────────────

NUMERATORS = [("전력 사용량", "kWh"), ("에너지 사용량", "MJ"), ("용수 사용량", "m3"),
              ("온실가스 배출량", "tCO2eq")]
SLASHES = ["/", " / ", "/\n", "\n/", "／"]
DENOMINATORS = ["톤", "(톤)", "100개", "", "톤/년", "xyz"]


@pytest.mark.parametrize("label,numerator", NUMERATORS)
def test_simple_unit_control_is_recomposed(label, numerator):
    """대조군: 분모가 없으면 같은 문구가 실제로 보정된다 — 아래 불변식이 빈 검사가 아니다."""
    value, issue = router._reconcile_scale_chain(1.2, numerator, f"{label} 1만 2천 {numerator}", hint=label)
    assert value == 12_000.0 and issue["reason"] == "scale_chain_recomposed"


@pytest.mark.parametrize("denominator", DENOMINATORS)
@pytest.mark.parametrize("slash", SLASHES)
@pytest.mark.parametrize("label,numerator", NUMERATORS)
def test_a_denominator_marker_never_falls_back_to_the_numerator(label, numerator, slash, denominator):
    quote = f"{label} 1만 2천 {numerator}{slash}{denominator}"
    read = router._read_unit(quote, len(f"{label} 1만 2천"))
    assert read.status in ("COMPOUND_COMPLETE", "INCOMPLETE") and read.unit is None, read
    assert read.has_denominator_marker
    value, issue = router._reconcile_scale_chain(1.2, numerator, quote, hint=label)
    assert value == 1.2
    assert issue is None or issue["reason"] != "scale_chain_recomposed"
    assert router._trailing_unit(quote, len(f"{label} 1만 2천")) is None


# ── 조합 — 0 표기 동등성: `0건` ↔ `발생하지 않았다` ──────────────────────────

SOURCE_SCOPES = ["", "2026년 4월 ", "2026-04 ", "2026년 3월 ", "2025년 ", "2026년 ", "2026년 연간 ",
                 "2026-04-01 기준 ", "2026-04-01 ~ 2026-04-30 ", "4월 ", "3월 ", "04-01~04-07 "]
REQUESTED = ["2026", "2026-04", "2026-04-01", "2026년 연간", "2026-01-01~2026-12-31"]


def forms(scope, *, target=False):
    word = "목표 " if target else ""
    return (f"{scope}산업재해 {word}발생 건수 0건",
            f"{scope}산업재해는 발생하지 않도록 한다." if target else f"{scope}산업재해는 발생하지 않았다.")


@pytest.mark.parametrize("period", REQUESTED)
@pytest.mark.parametrize("scope", SOURCE_SCOPES)
def test_numeric_and_negative_zero_get_the_same_scope_decision(scope, period):
    numeric, negation = (router._zero_verdict(q, "산업재해 발생 건수", period) for q in forms(scope))
    assert (numeric.status, numeric.cause) == (negation.status, negation.cause)
    assert numeric.evidence_period == negation.evidence_period
    target_numeric, target_negation = (router._zero_verdict(q, "산업재해 발생 건수", period)
                                       for q in forms(scope, target=True))
    assert not target_numeric.accepted and not target_negation.accepted


# ── 조합 — 사실성 불변식 ──────────────────────────────────────────────────

FACTS = ["산업재해 발생 건수 0건", "산업재해 발생 건수 0건이다", "산업재해는 발생하지 않았다",
         "산업재해는 없었다", "ISMS 인증 미보유"]
NON_FACT_TAILS = {
    "conditional": ["산업재해 발생 건수 0건일 경우 포상한다", "산업재해 발생 건수가 0건이라면 포상한다",
                    "산업재해가 발생하지 않으면 사고대장을 작성하지 않는다",
                    "산업재해가 발생하지 않을 경우 포상한다", "산업재해가 발생하지 않는다면 포상한다"],
    "assumption": ["산업재해 발생 건수 0건으로 가정한다", "산업재해가 발생하지 않는다고 가정한다",
                   "산업재해는 발생하지 않았다고 추정된다", "산업재해는 발생하지 않는 것을 전제로 한다"],
    "future_or_intent": ["산업재해 발생 건수 0건 예상", "산업재해 목표 0건", "산업재해는 발생하지 않을 것이다"],
    "evidence_insufficient": ["산업재해가 발생하지 않았다는 증거가 없다"],
}
UNKNOWN_STRUCTURES = ["산업재해는 발생하지 않았던 듯하다", "산업재해는 발생하지 않는 편이다",
                      "산업재해는 발생하지 않았다던데", "산업재해는 발생하지 않았을까",
                      "산업재해 발생 건수 0건 달성 여부 검토", "산업재해 발생 건수 0건 여부는 미정"]


@pytest.mark.parametrize("quote", FACTS)
def test_stated_facts_are_kept(quote):
    hint = "ISMS 인증 보유 상태" if "ISMS" in quote else "산업재해 발생 건수"
    assert router._zero_verdict(quote, hint, "2026-04").accepted


@pytest.mark.parametrize("cause,quote", [(c, q) for c, qs in NON_FACT_TAILS.items() for q in qs])
def test_non_facts_are_rejected_with_their_cause(cause, quote):
    verdict = router._zero_verdict(quote, "산업재해 발생 건수", "2026-04")
    assert (verdict.status, verdict.cause) == ("REJECTED", cause)


@pytest.mark.parametrize("quote", UNKNOWN_STRUCTURES)
def test_unrecognized_structures_never_default_to_a_fact(quote):
    verdict = router._zero_verdict(quote, "산업재해 발생 건수", "2026-04")
    assert not verdict.accepted and verdict.status in ("REJECTED", "UNRESOLVED"), verdict


REWRITES = {
    "산업재해는 발생하지 않았": ["다", "다고 가정한다", "다면 포상한다", "는지 모른다", "다고 볼 증거가 없다",
                           "다는 추정이다", "을 것이다", "던 듯하다"],
    "산업재해 발생 건수 0건": ["이다", "이라고 가정한다", "이라면 포상한다", "인지 모른다", "이라고 볼 증거가 없다",
                        "이라는 추정이다", "일 것이다", "일 경우 포상한다"],
}


@pytest.mark.parametrize("stem,suffix", [(stem, suffix) for stem, tails in REWRITES.items()
                                         for suffix in tails[1:]])
def test_rewriting_a_fact_into_a_non_fact_never_keeps_it(stem, suffix):
    """사실 문장(첫 어미)을 조건·가정·추측·증거 부재로 바꾸면 보존되지 않아야 한다."""
    assert router._zero_verdict(stem + REWRITES[stem][0], "산업재해 발생 건수", "2026-04").accepted
    assert not router._zero_verdict(stem + suffix, "산업재해 발생 건수", "2026-04").accepted, stem + suffix


# ── 조합 — 기간 표기 동등성 ────────────────────────────────────────────────

EQUIVALENT_SCOPES = [
    ["2026년 연간 ", "2026-01-01~2026-12-31 ", "2026년 연합계 ", "2026년 전체 "],
    ["2026년 4월 ", "2026-04 ", "2026.04 ", "2026-04-01 ~ 2026-04-30 ", "2026-04-01~04-30 "],
    ["2026-04-01 기준 ", "2026년 4월 1일 기준 "],
]


@pytest.mark.parametrize("period", REQUESTED)
@pytest.mark.parametrize("group", EQUIVALENT_SCOPES, ids=["annual", "month", "as_of"])
def test_equivalent_period_notations_get_the_same_decision(group, period):
    for kind in (0, 1):
        verdicts = {router._zero_verdict(forms(scope)[kind], "산업재해 발생 건수", period).status
                    for scope in group}
        assert len(verdicts) == 1, (group, period, verdicts)


@pytest.mark.parametrize("scope,status", [("2026년 연간 ", "CONFIRMED"), ("2026년 4월 ", "SOURCE_ONLY"),
                                          ("2026년 ", "SOURCE_ONLY"), ("2025년 ", "REJECTED"),
                                          ("4월 ", "SOURCE_ONLY"), ("", "SOURCE_ONLY")])
def test_report_year_is_metadata_not_an_annual_total(scope, status):
    for quote in forms(scope):
        assert router._zero_verdict(quote, "산업재해 발생 건수", "2026").status == status


@pytest.mark.parametrize("scope,cause", [("3월 ", "period_mismatch"), ("04-01~04-07 ", "period_unproven")])
def test_a_partially_read_period_still_blocks_a_conflict(scope, cause):
    for quote in forms(scope):
        assert router._zero_verdict(quote, "산업재해 발생 건수", "2026-04").cause == cause


# ── 조합 — 문맥 연결: 다른 후보의 0·날짜·사업장을 빌리지 않는다 ─────────────────

@pytest.mark.parametrize("quote,period,site,status,cause", [
    ("산업재해 목표 0건, 산업재해 발생 건수 2건", "2026-04", "", "REJECTED", "future_or_intent"),
    ("산업재해 목표 0건, 실적 산업재해 발생 건수 0건", "2026-04", "", "SOURCE_ONLY", "period_not_stated"),
    ("2026년 3월 산업재해 발생 건수 0건, 2026년 4월 산업재해 발생 건수 2건", "2026-04", "", "REJECTED", "period_mismatch"),
    ("2026년 3월 산업재해 0건, 2026년 4월 산업재해 0건", "2026-04", "", "CONFIRMED", "stated_zero"),
    ("부산 제1공장 산업재해 발생 건수 0건, 김해 제1공장 산업재해 발생 건수 2건", "2026-04", "김해 제1공장",
     "REJECTED", "site_mismatch"),
    ("구분 | 목표 | 실적\n산업재해 발생 건수 | 0 | 1", "2026-04", "", "REJECTED", "future_or_intent"),
    ("구분 | 목표 | 실적\n산업재해 발생 건수 | 1 | 0", "2026-04", "", "SOURCE_ONLY", "period_not_stated"),
    ("환경 사고 0건, 산업재해 발생 건수 2건", "2026-04", "", "REJECTED", "other_subject"),
    ("2026-04-01 산업재해 점검 항목 3-0", "2026-04", "", "UNRESOLVED", "no_zero_statement"),
])
def test_candidates_are_judged_in_their_own_context(quote, period, site, status, cause):
    verdict = router._zero_verdict(quote, "산업재해 발생 건수", period, {"site": site})
    assert (verdict.status, verdict.cause) == (status, cause), verdict


# ── 통합: 원본 응답 → 라우터 → 근거 그래프 → 대표값·범위 → 확인 목록·보고서 ──────

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
    body = "안전·에너지 현황\n" + "\n".join(m["quote"] for m in metrics)
    client.replies = [LLMResponse(content=json.dumps({"metrics": metrics, "clauses": []}),
                                  used_mock=False, meta={"provider": "test-double", "model": "test-double"})]
    ext = router._extract_unstructured_text("safety.pdf", doc_type="report", raw_text=body,
                                            page_texts=[(0, body)])
    graph = EvidenceGraph("LOCAL", "검토")
    merge_ocr_extraction(graph, ext, report_year=2026)
    return ext, graph


def test_raw_response_keeps_monthly_scope_and_never_promotes_it_to_annual(client):
    ext, graph = run_response(client, [
        {"metric_hint": "산업재해 발생 건수", "value": 0, "unit": "건", "period": "2026",
         "quote": "2026년 4월 산업재해 발생 건수 0건"},
        {"metric_hint": "에너지 사용량", "value": 2.5, "unit": "GJ", "period": "2026",
         "quote": "에너지 사용량 2 GJ + 500 MJ, 에너지 사용량 1만 2천 MJ/(톤)"},
        {"metric_hint": "산업재해 발생 건수", "value": 0, "unit": "건", "period": "2026-04",
         "quote": "산업재해가 발생하지 않는다고 가정한다"},
    ])
    # F1: 원값 2.5 GJ 보존, 보정 기록 없음 / F3: 가정문의 0은 싣지 않고 사유를 남긴다.
    assert sorted((m.metric_hint, m.value) for m in ext.metrics) == [("산업재해 발생 건수", 0), ("에너지 사용량", 2.5)]
    assert not any(r.get("reason") == "scale_chain_recomposed"
                   for e in ext.router_meta.get("value_reconciliations", []) for r in e["records"])
    (entry,) = ext.router_meta["unvalued_records"]
    assert [(r["cause"], r["status"]) for r in entry["records"]] == [("assumption", "REJECTED")]

    # F4: 근거 노드의 경계는 원문의 4월이다 — 모델의 `2026`으로 연간이 되지 않는다.
    (node,) = [n for n in graph.nodes.values() if n.value == 0]
    boundary = Boundary.from_dict(node.boundary)
    assert (boundary.period_text, boundary.aggregation, boundary.period_start, boundary.period_end) == \
        ("2026년 4월", "monthly", "2026-04-01", "2026-04-30")
    assert covers_full_year(boundary) is False
    assert any(p.get("status") == "SOURCE_ONLY" for p in boundary.provenance)

    # 대표값: 부분값이고 범위 미확정 — '검사해서 일치함'으로 보이지 않는다.
    fact = selection._from_nodes(graph, node.metric, [node])
    assert fact.value == 0 and fact.completeness == "partial"
    assert "scope_source_only" in fact.flags and any("보고 연도" in n for n in fact.scope_notes)

    # 확인 목록·보고서: 범위 미확정을 보이고 '수치를 읽지 못함' 안내는 붙이지 않는다.
    graph.resolved_facts = {"S-4-2": fact}
    output = SimpleNamespace(evidence_graph=graph, ocr_extractions=[ext], extraction=None,
                             sections={}, item_retrievals=[])
    findings = build_source_review(output)
    scope = [f for f in findings if f.check_reason == "scope_source_only"]
    assert scope and "요청 기간·사업장의 실적임은 확인하지 못했습니다" in scope[0].reason
    zero = [f for f in findings if f.check_reason == "zero_not_in_evidence"]
    assert len(zero) == 1 and "가정" in zero[0].reason
    block = _block_source_review(SimpleNamespace(review_findings=findings))
    assert block is not None and "범위 미확정" in block.body_md and "연간 무재해" not in block.body_md


def test_confirmed_zero_has_no_scope_warning(client):
    _, graph = run_response(client, [
        {"metric_hint": "산업재해 발생 건수", "value": 0, "unit": "건", "period": "2026-04",
         "quote": "2026년 4월 산업재해 발생 건수 0건"}])
    (node,) = graph.nodes.values()
    fact = selection._from_nodes(graph, node.metric, [node])
    assert "scope_source_only" not in fact.flags
    (record,) = [p for p in Boundary.from_dict(node.boundary).provenance if p.get("source") == "zero_evidence"]
    assert record["status"] == "CONFIRMED"


def test_unscoped_source_only_zero_keeps_its_record_through_the_graph(client):
    _, graph = run_response(client, [
        {"metric_hint": "산업재해 발생 건수", "value": 0, "unit": "건", "period": "2026-04",
         "quote": "산업재해는 발생하지 않았다."}])
    (node,) = graph.nodes.values()
    boundary = Boundary.from_dict(node.boundary)
    assert any(p.get("status") == "SOURCE_ONLY" and p.get("cause") == "period_not_stated"
               for p in boundary.provenance)
    assert any(p.get("source") == "row" for p in boundary.provenance)   # 규칙 추론 기록도 남는다
    assert "scope_source_only" in selection._from_nodes(graph, node.metric, [node]).flags
