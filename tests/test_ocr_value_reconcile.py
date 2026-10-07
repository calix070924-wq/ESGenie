"""추출한 값을 그 값의 근거 문구와 대조하는 후처리 (2026-09-29).

두 보고서 라이브 실측에서 기존 게이트가 구조적으로 못 보던 결함 두 종류를 찾았다. D1·G2·G4는
모두 '생성된 문장'을 기준으로 돌기 때문에, 문장이 인용하지 않은 지표는 검사 범위 밖이었다.
이 테스트는 그 두 결함의 실제 원문 표기를 그대로 넣어 다시 생기지 않는지 지킨다.
"""
from __future__ import annotations

from esgenie.ssot import ocr_router as o


# ── 한국식 자릿수 합성 ────────────────────────────────────────────────────────

def test_descending_scale_terms_compose_into_one_amount():
    assert o._kr_scale_chain_amounts("자본 총계 46조 1,182억 원") == {46_118_200_000_000.0}
    assert o._kr_scale_chain_amounts("211억 1,600만 원") == {21_116_000_000.0}
    assert o._kr_scale_chain_amounts("1만 5,000톤") == {15_000.0}


def test_single_scale_term_is_not_a_chain():
    """배율이 하나뿐인 표기는 모델이 틀릴 여지가 없어 대조 대상이 아니다."""
    assert o._kr_scale_chain_amounts("5억 원을 출자했다") == set()


def test_chain_does_not_swallow_an_unrelated_neighbouring_number():
    """이 조건이 없으면 옆 문장의 숫자를 끌어와 원문에 없는 금액을 만든다."""
    assert o._kr_scale_chain_amounts("1만 5,000톤을 확보하였으며 620억 원을 투자했다") == {15_000.0}
    assert o._kr_scale_chain_amounts("20% 중 5,000") == set()


def test_unit_prefix_gives_the_scale_even_with_inner_whitespace():
    assert o._unit_scale("억 원") == 1e8
    assert o._unit_scale("조 원") == 1e12
    assert o._unit_scale("백만 원") == 1e6
    assert o._unit_scale("달러") == 1.0
    assert o._unit_scale("") == 1.0


# ── 자릿수 붕괴를 원문 표기로 되잡는다 ────────────────────────────────────────

def test_collapsed_korean_amount_is_recomposed_from_the_source():
    """실측 결함: `57조 2,370억 원`을 모델이 `57,237억 원`으로 적었다(약 10배 낮다)."""
    value, issue = o._reconcile_scale_chain(57_237.0, "억 원", "매출액 57조 2,370억 원")
    assert value == 572_370.0
    assert issue["reason"] == "scale_chain_recomposed"
    assert issue["value_before"] == 57_237.0 and issue["fatal"] is False


def test_recomposed_value_equals_the_source_amount_for_every_measured_defect():
    for before, quote, after in [
        (46_182.0, "자본 총계 46조 1,182억 원", 461_182.0),
        (10_409.0, "유형 자산 10조 4,809억 원", 104_809.0),
        (57_237.0, "매출액 57조 2,370억 원", 572_370.0),
    ]:
        assert o._reconcile_scale_chain(before, "억 원", quote)[0] == after


def test_value_matching_the_source_amount_is_left_alone():
    value, issue = o._reconcile_scale_chain(461_182.0, "억 원", "자본 총계 46조 1,182억 원")
    assert (value, issue) == (461_182.0, None)


def test_rounded_source_notation_is_not_treated_as_a_defect():
    """`19조 5,184억`을 `19.5조 원`으로 적은 것은 반올림이며 자릿수 붕괴가 아니다."""
    assert o._reconcile_scale_chain(19.5, "조 원", "2024년 매출액 19조 5,184억 원")[1] is None


def test_value_written_in_the_quote_is_another_cell_and_never_overwritten():
    """같은 행의 다른 칸 수치를 합성 금액으로 덮어쓰면 옳은 값을 잃는다."""
    quote = "매출 46조 1,182억 원 | 영업이익 3,000억 원"
    assert o._reconcile_scale_chain(3_000.0, "억 원", quote) == (3_000.0, None)


def test_two_candidate_amounts_are_left_for_a_human_instead_of_guessing():
    """실측: `9억 4,000만 달러 … 중 8억 400만 달러` — 어느 쪽인지 문구만으로 정할 수 없다."""
    quote = "9억 4,000만 달러 한도 승인 금액 중 8억 400만 달러를 분할 인출하여"
    value, issue = o._reconcile_scale_chain(840_000_000.0, "달러", quote)
    assert value == 840_000_000.0          # 값을 임의로 바꾸지 않는다
    assert issue["reason"] == "scale_chain_unresolved"
    assert len(issue["source_amounts"]) == 2


def test_no_chain_in_the_quote_means_nothing_to_reconcile():
    assert o._reconcile_scale_chain(123.0, "TJ", "사용량 | TJ | 123") == (123.0, None)


# ── 원문의 '-'(미공시)를 0으로 싣지 않는다 ────────────────────────────────────

def _one_metric(quote: str, value: float, unit: str = "천 원"):
    issues: list[dict] = []
    text = f"보고 기간 표\n{quote}\n끝"
    metrics, _ = o._map_vlm_json(
        {"metrics": [{"metric_hint": "장기차입금", "value": value, "unit": unit,
                      "period": "2024년", "quote": quote}], "clauses": []},
        page_no=3, source_text=text, issues=issues)
    return metrics, issues


def test_zero_is_dropped_when_the_source_cell_is_a_dash():
    """실측 결함: 원문 칸이 `-`인데 0으로 실렸다. '0원'과 '미공시'는 다른 문장을 만든다."""
    metrics, issues = _one_metric("장기차입금 | 216,522,310 | - | 188,444,339", 0.0)
    assert metrics == []
    assert issues[0]["reason"] == "zero_not_in_evidence"
    assert issues[0]["fatal"] is False      # 청크 하나로 문서 전체를 실패시키지 않는다


def test_a_real_zero_written_in_the_source_is_kept():
    metrics, issues = _one_metric("장기차입금 | 0 | - | 188,444,339", 0.0)
    assert [m.value for m in metrics] == [0.0]
    assert [i for i in issues if i["reason"] == "zero_not_in_evidence"] == []


def test_zero_written_with_a_decimal_point_is_kept():
    metrics, _ = _one_metric("장기차입금 | 0.0 | 12.4", 0.0, unit="ton")
    assert [m.value for m in metrics] == [0.0]


def test_zero_of_another_row_is_not_this_metric():
    """PR 69 3차 검토: 숫자 0도 같은 지표의 행이어야 한다(이전에는 `지하수` 행의 0이 장기차입금 0이 됐다)."""
    metrics, issues = _one_metric("지하수 | 0.0 | 12.4", 0.0, unit="ton")
    assert metrics == [] and issues[0]["cause"] == "other_subject"


def test_zero_without_any_quote_is_left_untouched():
    """근거 문구가 없으면 대조할 수 없다 — 추측으로 값을 버리지 않는다."""
    metrics, _ = o._map_vlm_json(
        {"metrics": [{"metric_hint": "장기차입금", "value": 0.0, "unit": "천 원"}],
         "clauses": []}, page_no=3, source_text=None)
    assert [m.value for m in metrics] == [0.0]


# ── 서술을 수치로 옮긴 값은 살리되 표시를 남긴다 ──────────────────────────────

def test_number_absent_from_the_narrative_is_flagged_but_the_value_is_kept():
    """실측: "교육 대상 임직원 전원이 수료" → 100%. 값은 타당하나 인용에 숫자가 없다."""
    issues: list[dict] = []
    quote = "2025년 기준, 교육 대상 임직원 전원이 윤리교육을 수료하였으며"
    metrics, _ = o._map_vlm_json(
        {"metrics": [{"metric_hint": "윤리교육 수료율", "value": 100.0, "unit": "%",
                      "period": "2025", "quote": quote}], "clauses": []},
        page_no=1, source_text=quote, issues=issues)
    assert [m.value for m in metrics] == [100.0]        # 값을 지우지 않는다
    assert issues[0]["reason"] == "value_not_written_in_evidence"
    assert issues[0]["fatal"] is False


def test_a_value_visible_in_the_quote_is_not_flagged():
    """`Lv.5`·`제7회`·붙어 나온 표 칸의 숫자는 사람 눈에 보인다 — 표시하지 않는다."""
    for quote, value, unit in [
        ("MSRS 진단 | 1) | Lv. | Lv.5 | Lv.6 | Lv.7", 5.0, "Lv."),
        ("현대모비스는 2024년 제7회 자율주행자동차 경진대회를 개최하였습니다.", 7.0, "회"),
        ("배당금 | 3,671억 원4,073억 원5,395억 원", 5_395.0, "억 원"),
        ("기타자본 | -146,701,456 | -146,701,456", -146_701_456.0, "천 원"),
    ]:
        issues: list[dict] = []
        o._map_vlm_json({"metrics": [{"metric_hint": "지표", "value": value, "unit": unit,
                                      "period": "2024", "quote": quote}], "clauses": []},
                        page_no=1, source_text=quote, issues=issues)
        assert issues == [], f"표시가 붙어서는 안 된다: {quote}"


def test_reconciled_reasons_are_not_counted_as_unreported_values():
    """값이 실린 사유를 `unvalued_records`에 섞으면 '수치 미보고' 수가 부풀려진다."""
    assert "value_not_reported" not in o._VALUE_RECONCILED_REASONS
    assert "zero_not_in_evidence" not in o._VALUE_RECONCILED_REASONS
    # 기대값 변경(2026-10-05): 원문 표 칸 라벨로 정정한 행(`label_from_table_header`)과 원문 부정 서술로
    # 0을 보존한 행(`zero_recovered_from_negation`)도 값이 실린 채 원문과 대조된 기록이다. 두 사유가 실제로
    # 값을 싣는지는 `tests/test_numeric_scope_output_20261005.py`가 따로 확인한다.
    assert {"scale_chain_recomposed", "scale_chain_unresolved", "value_not_written_in_evidence",
            "label_from_table_header", "zero_recovered_from_negation"} == set(o._VALUE_RECONCILED_REASONS)
