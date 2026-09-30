"""PR 69 검토(2026-09-30)에서 찾은 세 결함의 회귀 테스트.

R1 자릿수 보정이 정상 환산값·다른 물리량의 값을 덮어썼다.
R2 원문에 숫자 `0`이 없다는 이유로 명확한 미발생·미보유의 0을 지웠다.
R3 미해결 값 대조 기록이 확인 목록(화면·보고서 공통)에 전달되지 않았다.

검토 산출물의 추가 테스트 5개(`output/reviews/pr69_20260930/test_review_regressions.py`)를
개인 경로 없이 옮기고, 변형·부정 사례를 붙였다. 한울정밀 11번 원문과 모델 응답은 시연용
가상자료이며 필요한 부분만 아래 상수로 둔다.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from esgenie.config import SETTINGS
from esgenie.layer6_report import _block_source_review
from esgenie.llm import LLMResponse
from esgenie.source_review import build_source_review, review_markdown
from esgenie.ssot import ocr_router as o
from esgenie.ssot.evidence_graph import EvidenceGraph


def mapped(hint, value, unit, quote, *, period="2026", source_text=None, boundary=None):
    issues: list[dict] = []
    record = {"metric_hint": hint, "value": value, "unit": unit, "period": period, "quote": quote}
    if boundary is not None:
        record["boundary"] = boundary
    metrics, _ = o._map_vlm_json({"metrics": [record], "clauses": []}, page_no=0,
                                 source_text=quote if source_text is None else source_text,
                                 issues=issues)
    return metrics, issues


def values(metrics):
    return [(m.value, m.unit) for m in metrics]


# ── R1 — 정상 환산값과 다른 물리량을 덮어쓰지 않는다 ──────────────────────────

def test_preserve_correct_kg_to_tonne_conversion():
    """한울정밀 폐기물 총량의 표기 변형(원본 PDF는 `18,400 kg`)."""
    metrics, issues = mapped("폐기물 배출량", 18.4, "ton", "폐기물 배출량 1만 8,400 kg")
    assert values(metrics) == [(18.4, "ton")]
    assert issues == []   # 환산된 값은 원문에 적힌 값이다 — 확인 표시도 붙이지 않는다


def test_plain_kg_notation_counts_as_written_for_a_tonne_value():
    metrics, issues = mapped("폐기물 배출량", 18.4, "ton", "폐기물 배출량 | 18,400 kg")
    assert values(metrics) == [(18.4, "ton")] and issues == []


def test_training_duration_must_not_become_its_fee():
    metrics, issues = mapped("교육 시간", 2.5, "시간", "교육 시간 2시간 30분, 교육비 1만 2천 원")
    assert values(metrics) == [(2.5, "시간")]
    assert [i["reason"] for i in issues] == ["value_not_written_in_evidence"]  # 2시간 30분 → 2.5 해석


def test_collapsed_amount_is_still_recomposed_with_full_trace():
    """기존에 고친 자릿수 복원(`57조 2,370억 원` → 572,370억 원)은 유지한다."""
    metrics, issues = mapped("매출액", 57_237.0, "억 원", "매출액 57조 2,370억 원")
    assert values(metrics) == [(572_370.0, "억 원")]
    (issue,) = issues
    assert issue["reason"] == "scale_chain_recomposed"
    assert (issue["value_before"], issue["value_after"]) == (57_237.0, 572_370.0)
    assert issue["unit"] == "억 원" and issue["source_unit"] == "원"
    assert issue["source_surface"] == "57조 2,370억" and issue["quote"] == "매출액 57조 2,370억 원"
    assert issue["page"] == 0 and issue["period"] == "2026"


def test_different_scale_of_the_same_metric_and_unit_group_is_recomposed_in_the_value_unit():
    metrics, issues = mapped("폐기물 배출량", 1.84, "ton", "폐기물 배출량 1만 8,400 kg")
    assert values(metrics) == [(18.4, "ton")]
    assert issues[0]["reason"] == "scale_chain_recomposed" and issues[0]["source_unit"] == "kg"


def test_other_currency_is_never_a_candidate():
    """원화와 달러는 환율을 모르므로 다른 수량이다."""
    metrics, issues = mapped("차입금", 9_400.0, "억 원", "차입금 9억 4,000만 달러")
    assert values(metrics) == [(9_400.0, "억 원")]
    assert all(i["reason"] != "scale_chain_recomposed" for i in issues)


def test_mass_is_not_replaced_by_volume():
    metrics, issues = mapped("용수 사용량", 12.0, "ton", "용수 사용량 1만 2,000 m³")
    assert values(metrics) == [(12.0, "ton")]
    assert all(i["reason"] != "scale_chain_recomposed" for i in issues)


def test_other_metric_in_the_same_sentence_is_not_used_to_overwrite():
    metrics, issues = mapped("매출액", 5_000.0, "억 원", "영업이익 1조 2,000억 원을 기록했고 매출은 증가했다")
    assert values(metrics) == [(5_000.0, "억 원")]
    assert issues[0]["reason"] == "scale_chain_unresolved"
    assert issues[0]["cause"] == "metric_unproven"


def test_same_unit_neighbouring_metric_written_in_another_unit_is_left_alone():
    """재활용량 5.39 ton은 `5,390 kg`으로 원문에 적혀 있다 — 배출량으로 덮지도, 경고하지도 않는다."""
    metrics, issues = mapped("폐기물 재활용량", 5.39, "ton", "폐기물 배출량 1만 8,400 kg 중 재활용량 5,390 kg")
    assert values(metrics) == [(5.39, "ton")] and issues == []


@pytest.mark.parametrize("unit,quote", [("", "폐기물 배출량 1만 8,400 kg"),
                                        ("억 원", "매출액 57조 2,370억")])
def test_unknown_unit_is_not_guessed_and_is_left_for_review(unit, quote):
    value = 1.84 if unit == "" else 57_237.0
    metrics, issues = mapped("폐기물 배출량" if unit == "" else "매출액", value, unit, quote)
    assert [m.value for m in metrics] == [value]
    assert issues[0]["reason"] == "scale_chain_unresolved" and issues[0]["cause"] == "unit_unproven"


def test_several_candidates_keep_the_value_and_record_every_candidate_with_its_unit():
    quote = "9억 4,000만 달러 한도 승인 금액 중 8억 400만 달러를 분할 인출하여"
    metrics, issues = mapped("인출금", 840_000_000.0, "달러", quote)
    assert [m.value for m in metrics] == [840_000_000.0]
    (issue,) = issues
    assert issue["cause"] == "multiple_candidates"
    assert [(c["surface"], c["unit"]) for c in issue["candidates"]] == [
        ("9억 4,000만", "달러"), ("8억 400만", "달러")]


@pytest.mark.parametrize("value,unit,quote", [
    (461_182.0, "억 원", "자본 총계 46조 1,182억 원"),      # 이미 올바른 금액
    (19.5, "조 원", "2024년 매출액 19조 5,184억 원"),         # 반올림 표기
    (15_000.0, "톤", "폐기물 1만 5,000톤을 처리했다"),
])
def test_already_correct_or_rounded_values_are_untouched(value, unit, quote):
    assert o._reconcile_scale_chain(value, unit, quote, hint="자본 총계 매출액 폐기물") == (value, None)


# ── R2 — 명확한 '없음'과 미공시·미확인을 구분한다 ────────────────────────────

def test_explicit_no_incidents_preserves_zero():
    metrics, issues = mapped("산업재해 발생 건수", 0, "건", "2026년 4월 산업재해는 발생하지 않았다.")
    assert [m.value for m in metrics] == [0] and issues == []


def test_hanwool_explicit_no_isms_preserves_zero():
    metrics, issues = mapped("ISMS 인증 보유 상태", 0, "건", "ISMS 인증서는 보유하지 않았다.")
    assert [m.value for m in metrics] == [0] and issues == []


# 한울정밀 11번 `11_정보보호관리규정_2026.pdf` 1쪽 원문(표 부분)과 실제 모델 응답의 지표.
# 출처: output/reviews/pr69_20260930/live_cache/ocr/2d3fdc8f…json (gpt-4.1-mini, 2026-09-30).
HANWOOL_11_TEXT = (
    "정보보호 관리규정\n"
    "한울정밀공업(주) / 시행 2026-01-02 / 상태 기준일 2026-04-30\n"
    "항목 | 내용\n"
    "적용 범위 | 두 공장의 업무 문서와 임직원·거래처 정보\n"
    "책임 | 경영지원 담당자 / 승인: 대표이사\n"
    "인증 보유 상태 | 2026-04-30 기준 ISMS 인증 미보유\n"
    "4. 외부 인증과 내부 방침의 구분\n"
    "현재 보유 문서는 내부 관리규정이다. ISMS 인증서는 보유하지 않았다. 향후 인증 준비를 "
    "검토하고 있으나 신청·심사·취득 실적으로 기재하지 않는다."
)
HANWOOL_11_RESPONSE = {
    "metrics": [{"metric_hint": "인증 보유 상태", "value": 0, "unit": "건", "period": "2026-04-30",
                 "kesg_code": None, "page": 0,
                 "quote": "인증 보유 상태 | 2026-04-30 기준 ISMS 인증 미보유"}],
    "clauses": [{"section": "4. 외부 인증과 내부 방침의 구분",
                 "text": "현재 보유 문서는 내부 관리규정이다. ISMS 인증서는 보유하지 않았다.",
                 "kesg_code": None, "page": 0,
                 "quote": "현재 보유 문서는 내부 관리규정이다. ISMS 인증서는 보유하지 않았다."}],
}


def test_hanwool_11_real_response_keeps_the_zero_and_the_non_possession_clause():
    issues: list[dict] = []
    metrics, clauses = o._map_vlm_json(HANWOOL_11_RESPONSE, page_no=0,
                                       source_text=HANWOOL_11_TEXT, issues=issues)
    assert [(m.metric_hint, m.value, m.page) for m in metrics] == [("인증 보유 상태", 0.0, 0)]
    assert metrics[0].quote == "인증 보유 상태 | 2026-04-30 기준 ISMS 인증 미보유"
    assert issues == []    # '수치를 읽지 못함'도, '인용에 없는 값'도 아니다
    assert "보유하지 않았다" in clauses[0].quote   # 미보유 조항과 출처가 남는다


@pytest.mark.parametrize("quote", ["산업재해 발생 건수 | 0 | 1", "산업재해 발생 건수 | 0.0"])
def test_explicit_numeric_zero_is_kept(quote):
    metrics, _ = mapped("산업재해 발생 건수", 0, "건", quote)
    assert [m.value for m in metrics] == [0]


@pytest.mark.parametrize("quote", [
    "산업재해 발생 건수 | - | 1",                          # 미공시
    "산업재해 발생 건수 |  | 1",                           # 빈칸
    "산업재해 발생 여부는 확인하지 않았다.",                # 미확인
    "산업재해가 발생하지 않도록 관리한다.",                  # 예방 목표
    "산업재해 건수는 미집계이며 사고 기록 없음",             # 미집계
    "환경 사고는 발생하지 않았다.",                          # 다른 지표의 부정
    "산업재해 예방교육은 없었다.",                           # 다른 대상의 부정
    "2025년 산업재해는 발생하지 않았다.",                    # 다른 기간
])
def test_non_facts_do_not_justify_a_zero(quote):
    metrics, issues = mapped("산업재해 발생 건수", 0, "건", quote)
    assert metrics == []
    assert issues[0]["reason"] == "zero_not_in_evidence"


def test_negation_for_another_site_does_not_justify_the_zero():
    metrics, _ = mapped("산업재해 발생 건수", 0, "건", "아산공장은 산업재해가 발생하지 않았다.",
                        boundary={"site": "평택공장"})
    assert metrics == []
    kept, _ = mapped("산업재해 발생 건수", 0, "건", "평택공장은 산업재해가 발생하지 않았다.",
                     boundary={"site": "평택공장"})
    assert [m.value for m in kept] == [0]


def test_quote_presence_alone_does_not_flip_the_same_fact():
    """인용이 원문과 맞지 않아 비어도, 인용이 있어도 같은 미발생 사실은 0으로 남는다."""
    text = "2026년 4월 산업재해는 발생하지 않았다."
    with_quote, _ = mapped("산업재해 발생 건수", 0, "건", text)
    without_quote, _ = mapped("산업재해 발생 건수", 0, "건", "모델이 지어낸 문장", source_text=text)
    assert [m.value for m in with_quote] == [m.value for m in without_quote] == [0]


def test_non_possession_zero_is_not_promoted_to_a_positive_certification():
    """0건은 0건으로 남는다 — 보유(1)나 충족으로 바뀌지 않는다."""
    metrics, _ = o._map_vlm_json(HANWOOL_11_RESPONSE, page_no=0, source_text=HANWOOL_11_TEXT)
    graph = EvidenceGraph("LOCAL", "검토")
    from esgenie.ssot.evidence_graph import merge_ocr_extraction
    merge_ocr_extraction(graph, o.OcrExtraction(
        source_file="11_정보보호관리규정_2026.pdf", channel=o.DocChannel.UNSTRUCTURED,
        doc_type="policy_manual", metrics=metrics, router_meta={"extraction_status": "complete"}),
        report_year=2026)
    nodes = [n for n in graph.nodes.values() if "인증" in n.raw_text]
    assert nodes and all(n.value == 0 for n in nodes)
    assert all(n.quote.endswith("ISMS 인증 미보유") for n in nodes)


# ── R3 — 미해결 값 대조를 확인 목록·보고서로 전달한다 ─────────────────────────

def review_of(*extractions):
    output = SimpleNamespace(evidence_graph=EvidenceGraph("LOCAL", "검토"),
                             ocr_extractions=list(extractions), extraction=None,
                             sections={}, item_retrievals=[])
    return build_source_review(output)


def extraction(source_file, reconciliations, **meta):
    return o.OcrExtraction(source_file=source_file, channel=o.DocChannel.UNSTRUCTURED,
                           doc_type="report",
                           router_meta={"extraction_status": "complete",
                                        "value_reconciliations": reconciliations, **meta})


UNRESOLVED = {"reason": "scale_chain_unresolved", "fatal": False, "metric_hint": "인출금",
              "value": 840000000, "unit": "달러", "source_amounts": [804000000, 940000000],
              "quote": "9억 4,000만 달러 한도 중 8억 400만 달러를 인출"}


def test_unresolved_reconciliation_is_visible_to_reviewer():
    ext = extraction("amount.pdf", [{"chunk_index": 0, "page": 0, "records": [UNRESOLVED]}])
    findings = review_of(ext)
    assert any("scale_chain_unresolved" in str(f.to_dict()) for f in findings)


def test_unresolved_finding_carries_file_page_values_candidates_quote_and_action():
    record = o._reconcile_scale_chain(840_000_000.0, "달러", UNRESOLVED["quote"], hint="인출금")[1]
    record = {"record_type": "metric", "record_index": 0, "metric_hint": "인출금", "page": 0, **record}
    (finding,) = review_of(extraction("amount.pdf", [{"chunk_index": 0, "page": 0, "records": [record]}]))
    assert finding.check_reason == "scale_chain_unresolved"
    assert "인출금" in finding.fact and "840,000,000 달러" in finding.fact   # 반올림하지 않는다
    assert "9억 4,000만 달러" in finding.fact and "8억 400만 달러" in finding.fact
    assert "여러 개" in finding.reason and finding.action
    (ref,) = finding.evidence
    assert (ref.source_file, ref.page, ref.quote) == ("amount.pdf", 0, UNRESOLVED["quote"])
    md = review_markdown([finding])
    assert "amount\\.pdf · 1쪽" in md and "9억 4,000만 달러 한도" in md


def test_value_not_written_is_a_check_not_an_error():
    record = {"reason": "value_not_written_in_evidence", "fatal": False, "metric_hint": "윤리교육 수료율",
              "value": 100.0, "unit": "%", "page": 4, "quote": "교육 대상 임직원 전원이 수료"}
    (finding,) = review_of(extraction("r.pdf", [{"chunk_index": 1, "page": 4, "records": [record]}]))
    assert finding.check_reason == "value_not_written_in_evidence"
    assert "100 %" in finding.fact and "틀렸다는 판정은 아닙니다" in finding.reason
    assert finding.evidence[0].page == 4


def test_recomposed_value_is_a_traceable_correction_not_an_unresolved_error():
    _, issues = mapped("매출액", 57_237.0, "억 원", "매출액 57조 2,370억 원")
    ext = extraction("r.pdf", [{"chunk_index": 0, "page": 0, "records": issues}],
                     unvalued_records=[], unvalued_record_count=0)
    (finding,) = review_of(ext)
    assert finding.category == "correction" and finding.check_reason == "scale_chain_recomposed"
    assert "57,237 억 원 → 572,370 억 원" in finding.fact and "57조 2,370억" in finding.fact
    assert ext.router_meta["unvalued_record_count"] == 0


def test_page_falls_back_to_the_chunk_and_unknown_page_stays_unknown():
    no_page = {**UNRESOLVED, "page": None}
    chunk_page, unknown = review_of(
        extraction("a.pdf", [{"chunk_index": 0, "page": 7, "records": [no_page]}]),
        extraction("b.pdf", [{"chunk_index": 0, "page": None, "records": [no_page]}]))
    assert chunk_page.evidence[0].page == 7
    assert unknown.evidence[0].page is None
    assert "페이지 미확인" in review_markdown([unknown])


def test_zero_value_is_shown_as_zero():
    record = {"reason": "scale_chain_unresolved", "fatal": False, "metric_hint": "재해",
              "value": 0.0, "unit": "건", "source_amounts": [12000.0], "quote": "q"}
    (finding,) = review_of(extraction("a.pdf", [{"chunk_index": 0, "page": 0, "records": [record]}]))
    assert "추출값 0 건" in finding.fact


def test_duplicates_collapse_but_different_documents_and_pages_stay():
    entry = {"chunk_index": 0, "page": 0, "records": [UNRESOLVED, dict(UNRESOLVED)]}
    other_page = {"chunk_index": 1, "page": 1, "records": [UNRESOLVED]}
    findings = review_of(extraction("a.pdf", [entry, other_page]),
                         extraction("b.pdf", [entry]))
    assert sorted((f.evidence[0].source_file, f.evidence[0].page) for f in findings) == [
        ("a.pdf", 0), ("a.pdf", 1), ("b.pdf", 0)]


def test_special_characters_in_the_quote_survive_markdown():
    record = {**UNRESOLVED, "quote": "인출금 | *8억* 400만 달러 (한도 [9억 4,000만])"}
    (finding,) = review_of(extraction("a.pdf", [{"chunk_index": 0, "page": 0, "records": [record]}]))
    md = review_markdown([finding])
    assert "인출금 \\| \\*8억\\* 400만 달러 \\(한도 \\[9억 4,000만\\]\\)" in md


def test_zero_without_evidence_explains_what_is_uncertain_instead_of_the_graphic_notice():
    ext = extraction("r.pdf", [], unvalued_records=[{"chunk_index": 0, "page": 2, "records": [
        {"record_type": "metric", "record_index": 0, "reason": "zero_not_in_evidence", "fatal": False,
         "metric_hint": "장기차입금", "unit": "천 원", "period": "2024", "quote": "장기차입금 | - | 1"}]}],
        unvalued_record_count=1)
    (finding,) = review_of(ext)
    assert finding.check_reason == "zero_not_in_evidence" and finding.title == "0값의 원문 근거 확인"
    assert "그림" not in finding.reason and "미공시(-)" in finding.reason
    assert finding.evidence[0].page == 2 and finding.evidence[0].quote == "장기차입금 | - | 1"


# ── R3 — OCR 원본 응답 → 라우터 메타 → 확인 목록 → 보고서 ────────────────────

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


def test_raw_response_to_report_carries_the_unresolved_warning(client):
    """합성 입력: 미해결 후보가 있는 고정 문구를 원본 응답부터 보고서 블록까지 통과시킨다."""
    body = "차입 현황\n9억 4,000만 달러 한도 승인 금액 중 8억 400만 달러를 분할 인출하여 운영자금에 사용"
    client.replies = [LLMResponse(content=json.dumps({"metrics": [
        {"metric_hint": "인출금", "value": 840000000, "unit": "달러", "period": "2026",
         "quote": "9억 4,000만 달러 한도 승인 금액 중 8억 400만 달러를 분할 인출하여"}],
        "clauses": []}), used_mock=False, meta={"provider": "test-double", "model": "test-double"})]
    ext = o._extract_unstructured_text("loan.pdf", doc_type="report", raw_text=body,
                                       page_texts=[(0, body)])
    assert [m.value for m in ext.metrics] == [840_000_000.0]
    assert ext.router_meta["value_reconciliation_count"] == 1
    assert ext.router_meta["unvalued_record_count"] == 0     # 값이 실린 행은 미보고로 세지 않는다
    findings = review_of(ext)
    output = SimpleNamespace(review_findings=findings)
    block = _block_source_review(output)
    assert block is not None and "추출값과 원문 표기 대조 미해결" in block.body_md
    assert "loan\\.pdf · 1쪽" in block.body_md and "8억 400만 달러" in block.body_md
