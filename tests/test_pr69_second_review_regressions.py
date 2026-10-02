"""PR 69 2차 검토(2026-09-30, ba65f9e)에서 찾은 세 결함의 회귀 테스트.

A 복합 단위(`MJ/톤`)의 분자만 읽어 원단위 값으로 총량을 덮어썼다.
B 0의 근거를 연도·부분 문자열로만 대조해 다른 월·기준일·사업장의 미발생으로 0을 채택했다.
C 부정 어근만 보고 미래 예상·증거 부재를 미발생 사실로 읽었다.

검토 산출물의 8개 테스트(`output/reviews/pr69_20260930/fix_review/test_remaining_regressions.py`)를
개인 경로·`REVIEW_CODE` 없이 옮기고, 정상 사례와 구조 변형을 붙였다. 모든 문구는 합성 입력이다.
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
from esgenie.ssot.evidence_graph import EvidenceGraph


def mapped(hint, value, unit, quote, *, period="2026-04", site=""):
    issues = []
    metrics, _ = router._map_vlm_json(
        {"metrics": [{"metric_hint": hint, "value": value, "unit": unit,
                      "period": period, "boundary": {"site": site}, "quote": quote}]},
        source_text=quote, page_no=0, issues=issues,
    )
    return metrics, issues


def dropped(issues):
    (issue,) = issues
    assert issue["reason"] == "zero_not_in_evidence"
    return issue["cause"]


# ── 검토 산출물 8건 ──────────────────────────────────────────────────────────

def test_intensity_unit_must_not_overwrite_absolute_energy():
    quote = "에너지 사용량 2 GJ + 500 MJ, 에너지 사용량 원단위 1만 2천 MJ/톤"
    metrics, issues = mapped("에너지 사용량", 2.5, "GJ", quote)
    assert [m.value for m in metrics] == [2.5], issues
    assert not any(i["reason"] == "scale_chain_recomposed" for i in issues)


@pytest.mark.parametrize("period,quote", [
    ("2026-04", "2026년 3월 산업재해는 발생하지 않았다."),
    ("2026-04-30", "2026-04-01 기준 산업재해는 발생하지 않았다."),
], ids=["different_month", "different_day"])
def test_other_period_does_not_ground_zero(period, quote):
    metrics, issues = mapped("산업재해 발생 건수", 0, "건", quote, period=period)
    assert not metrics, [(m.value, m.period, m.quote) for m in metrics]
    assert issues[0]["reason"] == "zero_not_in_evidence"


@pytest.mark.parametrize("site,quote", [
    ("김해 제1공장", "부산 제1공장은 산업재해가 발생하지 않았다."),
    ("서아산공장", "아산공장은 산업재해가 발생하지 않았다."),
], ids=["same_factory_number", "substring_name"])
def test_other_site_does_not_ground_zero(site, quote):
    metrics, issues = mapped("산업재해 발생 건수", 0, "건", quote, site=site)
    assert not metrics, [(m.value, m.boundary, m.quote) for m in metrics]
    assert issues[0]["reason"] == "zero_not_in_evidence"


@pytest.mark.parametrize("quote", [
    "산업재해는 발생하지 않을 것이다.",
    "산업재해가 발생하지 않았다는 증거가 없다.",
], ids=["future_prediction", "no_proof"])
def test_prediction_or_lack_of_proof_is_not_observed_zero(quote):
    metrics, issues = mapped("산업재해 발생 건수", 0, "건", quote)
    assert not metrics, [(m.value, m.quote) for m in metrics]
    assert issues[0]["reason"] == "zero_not_in_evidence"


def test_current_non_possession_survives_separate_future_plan():
    quote = "ISMS 인증은 미보유이며 향후 취득을 계획하고 있다."
    metrics, issues = mapped("ISMS 인증 보유 상태", 0, "건", quote)
    assert [m.value for m in metrics] == [0], issues
    assert issues == []


# ── A — 단위 표현 전체와 지표 의미를 먼저 확인한다 ───────────────────────────

@pytest.mark.parametrize("unit_text", ["MJ/톤", "MJ / 톤", "MJ/ton", "MJ/억원"])
def test_compound_unit_is_read_whole_and_never_as_its_numerator(unit_text):
    read = router._read_unit(f"1만 2천 {unit_text}", len("1만 2천"))
    assert read.compound and read.unit is None
    assert read.text.replace(" ", "") == unit_text.replace(" ", "")


@pytest.mark.parametrize("text,unit", [(" tCO2eq", "tCO2eq"), (" m3", "m³"), (" m³", "m³"),
                                       ("톤을 처리했다", "t"), (" kg이며", "kg")])
def test_simple_units_are_read_fully_with_their_particle(text, unit):
    read = router._read_unit("1만" + text, 2)
    assert not read.compound
    assert read.unit == unit


def test_unknown_suffix_is_not_taken_for_a_particle():
    assert router._read_unit("1만 2천 MJx", len("1만 2천")).unit is None


@pytest.mark.parametrize("hint,value,unit,quote", [
    ("용수 사용량", 1.2, "m3", "용수 사용량 1,200 m3, 용수 원단위 1만 2천 m3/억원"),
    ("폐기물 배출량", 1.84, "kg", "폐기물 배출량 1,840 kg, 1인당 폐기물 1만 8천 kg/명"),
    ("에너지 사용량", 2.5, "GJ", "에너지 사용량 2 GJ + 500 MJ, 에너지 사용량 원단위 1만 2천 MJ / 톤"),
])
def test_other_intensity_metrics_never_overwrite_a_total(hint, value, unit, quote):
    metrics, issues = mapped(hint, value, unit, quote)
    assert [m.value for m in metrics] == [value]
    assert not any(i["reason"] == "scale_chain_recomposed" for i in issues)


@pytest.mark.parametrize("label,hint", [
    ("에너지 사용량 원단위", "에너지 사용량"), ("평균 교육 시간", "교육 시간"),
    ("누적 투자액", "투자액"), ("재활용 비율", "재활용량"), ("폐기물 배출량 목표", "폐기물 배출량"),
])
def test_total_and_intensity_average_cumulative_or_target_are_different_metrics(label, hint):
    assert not router._label_names_metric(label, hint)


def test_same_metric_label_still_names_the_metric():
    assert router._label_names_metric("에너지 사용량 원단위", "에너지 사용량 원단위")


def test_intensity_metric_is_reconciled_against_its_own_label_without_recomposing():
    metrics, issues = mapped("에너지 사용량 원단위", 12_000.0, "MJ/톤",
                             "에너지 사용량 원단위 1만 2천 MJ/톤")
    assert [m.value for m in metrics] == [12_000.0]
    assert not any(i["reason"] == "scale_chain_recomposed" for i in issues)


def test_recomposition_record_keeps_the_full_source_unit_text():
    _, issues = mapped("매출액", 57_237.0, "억 원", "매출액 57조 2,370억 원")
    (issue,) = issues
    assert issue["reason"] == "scale_chain_recomposed" and issue["source_unit_text"] == "원"


# ── B — 기간·사업장이 같은 범위일 때만 0을 채택한다 ──────────────────────────

@pytest.mark.parametrize("period,quote", [
    ("2026-04", "2026년 4월 산업재해는 발생하지 않았다."),
    ("2026-04", "2026.04 산업재해는 발생하지 않았다."),
    ("2026-04", "2026-04-01 ~ 2026-04-30 기간 산업재해는 발생하지 않았다."),
    ("2026-04", "2026-04-01~04-30 산업재해는 발생하지 않았다."),
    ("2026-04-01 ~ 2026-04-30", "2026년 4월 산업재해는 발생하지 않았다."),
    ("2026-04-30", "2026-04-30 기준 산업재해는 발생하지 않았다."),
    ("2026-04-30", "2026년 4월 30일 기준 산업재해는 발생하지 않았다."),
    ("2026-04", "산업재해는 발생하지 않았다."),       # 원문에 기간이 없으면 판정하지 않는다
])
def test_same_period_in_other_formats_keeps_the_zero(period, quote):
    metrics, issues = mapped("산업재해 발생 건수", 0, "건", quote, period=period)
    assert [m.value for m in metrics] == [0] and issues == [], issues


@pytest.mark.parametrize("period,quote,cause", [
    ("2026-04", "2026년 3월 산업재해는 발생하지 않았다.", "period_mismatch"),
    ("2026-04-30", "2026-04-01 기준 산업재해는 발생하지 않았다.", "period_mismatch"),
    ("2026-04", "2025년 산업재해는 발생하지 않았다.", "period_mismatch"),
    ("2026-04", "2026-04-01 기준 산업재해는 발생하지 않았다.", "period_unproven"),  # 기준일 ≠ 한 달
    ("2026-04", "2026년 산업재해는 발생하지 않았다.", "period_unproven"),          # 연간 ≠ 한 달
])
def test_different_or_unproven_period_is_recorded_with_both_scopes(period, quote, cause):
    metrics, issues = mapped("산업재해 발생 건수", 0, "건", quote, period=period)
    assert metrics == [] and dropped(issues) == cause
    assert issues[0]["period"] == period and issues[0]["evidence_period"]


@pytest.mark.parametrize("site,quote", [
    ("김해 제1공장", "김해 제1공장은 산업재해가 발생하지 않았다."),
    ("김해 제1공장", "김해제1공장은 산업재해가 발생하지 않았다."),
    ("김해제1공장", "김해 제 1 공장에서 산업재해가 발생하지 않았다."),
    ("서아산공장", "서아산공장의 산업재해는 발생하지 않았다."),
    ("김해 제1공장", "산업재해는 발생하지 않았다."),  # 원문에 사업장이 없으면 판정하지 않는다
])
def test_same_site_in_other_spellings_keeps_the_zero(site, quote):
    metrics, issues = mapped("산업재해 발생 건수", 0, "건", quote, site=site)
    assert [m.value for m in metrics] == [0] and issues == [], issues


@pytest.mark.parametrize("site,quote", [
    ("김해 제1공장", "부산 제1공장은 산업재해가 발생하지 않았다."),
    ("서아산공장", "아산공장은 산업재해가 발생하지 않았다."),
    ("아산공장", "서아산공장은 산업재해가 발생하지 않았다."),
    ("김해 제1공장", "제1공장은 산업재해가 발생하지 않았다."),   # 지역 없는 번호로 고르지 않는다
    ("부산 제1공장", "제1공장은 산업재해가 발생하지 않았다."),
    ("김해 제1공장", "김해 제2공장은 산업재해가 발생하지 않았다."),
])
def test_other_site_is_recorded_with_both_sites(site, quote):
    metrics, issues = mapped("산업재해 발생 건수", 0, "건", quote, site=site)
    assert metrics == [] and dropped(issues) == "site_mismatch"
    assert issues[0]["metric_site"] == site and issues[0]["evidence_site"]


@pytest.mark.parametrize("period,site,quote,cause", [
    ("2026-04-30", "", "2026-04-01 기준 산업재해 발생 건수 0건", "period_mismatch"),
    ("2026-04", "", "2026년 3월 산업재해 발생 건수 | 0", "period_mismatch"),
    ("2026-04", "김해 제1공장", "부산 제1공장 산업재해 발생 건수 0건", "site_mismatch"),
])
def test_numeric_zero_does_not_bypass_the_scope_checks(period, site, quote, cause):
    metrics, issues = mapped("산업재해 발생 건수", 0, "건", quote, period=period, site=site)
    assert metrics == [] and dropped(issues) == cause


@pytest.mark.parametrize("quote", ["2026년 4월 산업재해 발생 건수 0건",
                                   "2026-04-01 ~ 2026-04-30 산업재해 발생 건수 | 0 | 1"])
def test_numeric_zero_in_the_same_scope_is_kept(quote):
    metrics, issues = mapped("산업재해 발생 건수", 0, "건", quote, site="김해 제1공장")
    assert [m.value for m in metrics] == [0] and issues == []


# ── C — 부정 서술이 실제 사실인지 판정한다 ──────────────────────────────────

@pytest.mark.parametrize("quote", [
    "ISMS 인증은 미보유이며 향후 취득을 계획하고 있다.",
    "ISMS 인증은 미보유이고 내년 취득을 검토한다.",
    "ISMS 인증은 보유하지 않았으나 2027년 취득을 목표로 한다.",
    "현재 ISMS 인증을 보유하지 않았다.",
    "ISMS 인증 미보유",
])
def test_current_non_possession_is_kept_even_with_a_separate_plan(quote):
    metrics, issues = mapped("ISMS 인증 보유 상태", 0, "건", quote)
    assert [m.value for m in metrics] == [0] and issues == [], issues


@pytest.mark.parametrize("quote,cause", [
    ("ISMS 인증은 취득하지 않을 계획이다.", "future_or_intent"),
    ("ISMS 인증은 미보유 상태가 아니다.", "negation_negated"),
    ("ISMS 인증을 보유하지 않았다는 것은 사실이 아니다.", "negation_negated"),
    ("ISMS 인증 미보유 여부는 확인하지 않았다.", "not_confirmed"),
])
def test_plan_or_double_negation_is_not_current_non_possession(quote, cause):
    metrics, issues = mapped("ISMS 인증 보유 상태", 0, "건", quote)
    assert metrics == [] and dropped(issues) == cause


@pytest.mark.parametrize("quote,cause", [
    ("산업재해는 발생하지 않을 것이다.", "future_or_intent"),
    ("산업재해는 발생하지 않을 것으로 예상된다.", "future_or_intent"),
    ("산업재해가 발생하지 않기를 기대한다.", "future_or_intent"),
    ("산업재해는 발생하지 않겠다.", "future_or_intent"),
    ("산업재해가 발생하지 않도록 관리한다.", "future_or_intent"),
    ("산업재해가 발생하지 않았다는 증거가 없다.", "evidence_insufficient"),
    ("산업재해가 발생하지 않았다고 단정하기 어렵다.", "evidence_insufficient"),
    ("산업재해가 발생하지 않았음을 입증할 근거가 부족하다.", "evidence_insufficient"),
    ("산업재해가 발생하지 않았는지는 불명확하다.", "evidence_insufficient"),
])
def test_future_or_unproven_negation_is_rejected_with_its_cause(quote, cause):
    metrics, issues = mapped("산업재해 발생 건수", 0, "건", quote)
    assert metrics == [] and dropped(issues) == cause


@pytest.mark.parametrize("quote", [
    "산업재해는 발생하지 않았다.",
    "산업재해가 한 건도 발생하지 않았습니다.",
    "산업재해가 발생하지 않았음을 확인했다.",
    "산업재해는 발생하지 않은 것으로 확인됐다.",
    "산업재해는 발생하지 않았고 안전점검을 계획하고 있다.",
    "산업재해는 없었다.",
])
def test_stated_facts_keep_the_zero(quote):
    metrics, issues = mapped("산업재해 발생 건수", 0, "건", quote)
    assert [m.value for m in metrics] == [0] and issues == [], issues


def test_zero_is_never_promoted_to_a_held_certificate():
    metrics, _ = mapped("ISMS 인증 보유 상태", 0, "건", "ISMS 인증은 미보유 상태가 아니다.")
    assert all(m.value != 1 for m in metrics)


# ── 원본 응답 → 라우터 메타 → 확인 목록 → 보고서 (합성 입력) ─────────────────

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


@pytest.mark.parametrize("record,quote,cause,text", [
    ({"period": "2026-04"}, "2026년 3월 산업재해는 발생하지 않았다.", "period_mismatch", "기간이 지표 기간과 달라"),
    ({"period": "2026-04", "boundary": {"site": "김해 제1공장"}},
     "부산 제1공장은 산업재해가 발생하지 않았다.", "site_mismatch", "사업장이 지표 사업장과 달라"),
    ({"period": "2026-04"}, "산업재해는 발생하지 않을 것이다.", "future_or_intent", "미래 예상"),
    ({"period": "2026-04"}, "산업재해가 발생하지 않았다는 증거가 없다.", "evidence_insufficient", "증거 부족"),
])
def test_raw_response_to_report_explains_why_the_zero_was_dropped(client, record, quote, cause, text):
    body = "안전 현황\n" + quote
    client.replies = [LLMResponse(content=json.dumps({"metrics": [
        {"metric_hint": "산업재해 발생 건수", "value": 0, "unit": "건", "quote": quote, **record}],
        "clauses": []}), used_mock=False, meta={"provider": "test-double", "model": "test-double"})]
    ext = router._extract_unstructured_text("safety.pdf", doc_type="report", raw_text=body,
                                            page_texts=[(0, body)])
    assert ext.metrics == []
    (entry,) = ext.router_meta["unvalued_records"]
    (rec,) = entry["records"]
    assert (rec["reason"], rec["cause"]) == ("zero_not_in_evidence", cause)
    output = SimpleNamespace(evidence_graph=EvidenceGraph("LOCAL", "검토"), ocr_extractions=[ext],
                             extraction=None, sections={}, item_retrievals=[])
    findings = [f for f in build_source_review(output) if f.check_reason == "zero_not_in_evidence"]
    (finding,) = findings
    assert text in finding.reason and "명시적 미발생·미보유 서술이 없어" not in finding.reason
    block = _block_source_review(SimpleNamespace(review_findings=findings))
    assert block is not None and "safety\\.pdf · 1쪽" in block.body_md and text in block.body_md
