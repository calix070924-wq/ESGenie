"""실사 응답서 채점기 테스트 — 전부 합성 입력이다.

여기 쓰인 라벨·응답 값은 **검증용 합성 fixture**이며 한울정밀 BM 개편 세트의 정답
라벨이 아니다. 실제 정답은 `data/eval/labels/*.csv`에만 두고(사람이 작성·검토),
제품 코드와 테스트에는 넣지 않는다.

공통 형식·어댑터 테스트는 `tests/test_eval_answer_format.py`에 있다.
"""
from __future__ import annotations

import csv
import importlib
import json

import pytest

from esgenie.eval import answer_format as af
from esgenie.eval import response_scoring as rs

cli = importlib.import_module("scripts.eval_response_quality")

SYNTHETIC_QIDS = ("SYN-1", "SYN-2", "SYN-3")


# ── 합성 입력 만들기 ──────────────────────────────────────────────────────
def _label_row(**over) -> dict[str, str]:
    row = {c: "" for c in rs.LABEL_COLUMNS}
    row.update({"stage": "initial", "qid": "SYN-1", "expected_decision": "hold",
                "hold_reason": "no_evidence", "labeler": "synthetic"})
    row.update({k: str(v) for k, v in over.items()})
    # `answer` 라벨에는 expected_sources가 필수다(2026-10-06 확정). 값 비교만 보는
    # 테스트가 그 때문에 깨지지 않도록 합성 출처를 기본값으로 둔다. 필수 규칙 자체는
    # `test_answer_label_requires_expected_sources`에서 본다.
    if row["expected_decision"] == "answer" and "expected_sources" not in over:
        row["expected_sources"] = "SYN_합성증빙.pdf#1"
    return row


def _write_labels(tmp_path, rows) -> str:
    path = tmp_path / "synthetic_labels.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rs.LABEL_COLUMNS))
        w.writeheader()
        w.writerows(rows)
    return str(path)


def _label(**over) -> rs.Label:
    return rs.parse_label_row(_label_row(**over), "synthetic")


def _answer(**over) -> rs.SystemAnswer:
    base = {"stage": "initial", "qid": "SYN-1", "decision": rs.V_SELF_REPORTED,
            "value": 1.0, "unit": "", "evidence": ()}
    base.update(over)
    return rs.SystemAnswer(**base)


def _doc(answers: list[dict], **meta) -> dict:
    base = {"system": "synthetic", "run_id": "SYN-RUN-1", "stage": "initial",
            "framework": "rba42", "data_source": "synthetic"}
    base.update(meta)
    return af.new_document(answers=answers, **base)


# ── 공통 형식 → 채점 대상 읽기 ───────────────────────────────────────────
def test_answers_come_from_the_common_format_without_page_conversion():
    """공통 형식 page는 이미 1-기준이다 — 채점기는 다시 변환하지 않는다."""
    doc = _doc([af.new_answer(qid="SYN-1", decision=af.D_CONFIRMED, value=7.5, unit="톤",
                              evidence=[{"file_name": "08.pdf", "page": 3, "quote": None},
                                        {"file_name": "09.pdf", "page": None}])])
    (answer,) = rs.answers_from_document(doc)
    assert answer.stage == "initial"
    assert [e.page_1based for e in answer.evidence] == [3, None]


def test_source_fields_are_preserved_for_inspection():
    doc = _doc([af.new_answer(qid="SYN-1", decision=af.D_UNVERIFIED, value=63.0,
                              source={"status": "self_reported", "comparison": ""})])
    (answer,) = rs.answers_from_document(doc)
    assert (answer.source_status, answer.source_comparison) == ("self_reported", "")


def test_control_document_without_status_is_still_scored():
    """대조군에는 ESGenie 전용 status가 없다 — 그것만으로 전부 미검증으로 바꾸지 않는다."""
    doc = _doc([af.new_answer(qid="SYN-1", decision=af.D_CONFIRMED, value=1.0)],
               system="general_ai")
    (answer,) = rs.answers_from_document(doc)
    assert answer.decision == af.D_CONFIRMED
    assert answer.source_status == ""


@pytest.mark.parametrize("broken, needle", [
    ({"answers": []}, "meta"),
    ({"meta": {"stage": "final"}, "answers": []}, "stage"),
    ({"meta": {"stage": "initial"}}, "answers"),
])
def test_malformed_document_is_rejected(broken, needle):
    with pytest.raises(af.FormatError, match=needle):
        rs.answers_from_document(broken)


# ── §6.2 미검증 전달 행의 집계 ───────────────────────────────────────────
def test_self_reported_hold_no_evidence_is_correct_hold():
    label = _label(expected_decision="hold", hold_reason="no_evidence")
    bucket, detail, _ = rs.assign_bucket(rs.V_SELF_REPORTED, label, None)
    assert (bucket, detail) == (rs.B_CORRECT_HOLD, "")


@pytest.mark.parametrize("reason", ["mismatch", "not_comparable", "scope_unconfirmed"])
def test_self_reported_hold_mismatch_family_is_wrong_confirmation(reason):
    label = _label(expected_decision="hold", hold_reason=reason)
    bucket, detail, _ = rs.assign_bucket(rs.V_SELF_REPORTED, label, None)
    assert (bucket, detail) == (rs.B_WRONG_CONFIRMATION, rs.D_MISSED_MISMATCH)


def test_self_reported_answer_value_mismatch_is_wrong_confirmation():
    label = _label(expected_decision="answer", expected_value="41.6", hold_reason="")
    bucket, detail, _ = rs.assign_bucket(rs.V_SELF_REPORTED, label, False)
    assert (bucket, detail) == (rs.B_WRONG_CONFIRMATION, rs.D_MISSED_MISMATCH)


def test_self_reported_answer_value_match_is_unnecessary_hold():
    label = _label(expected_decision="answer", expected_value="41.6", hold_reason="")
    bucket, detail, _ = rs.assign_bucket(rs.V_SELF_REPORTED, label, True)
    assert (bucket, detail) == (rs.B_UNNECESSARY_HOLD, rs.D_EVIDENCE_LINK_MISSING)


# ── 2026-10-06 확정 판정표 ───────────────────────────────────────────────
def test_confirmed_answer_both_match_is_correct_answer():
    """확정 × 정답 라벨에서 값·근거가 모두 맞으면 `correct_answer`다."""
    label = _label(expected_decision="answer", expected_value="41.6", hold_reason="")
    bucket, detail, _ = rs.assign_bucket(rs.V_CONFIRMED, label, True, True)
    assert (bucket, detail) == (rs.B_CORRECT_ANSWER, "")


@pytest.mark.parametrize("value_match,source_match,detail", [
    (False, True, "value_error"),
    (True, False, "evidence_error"),
    (False, False, "value_and_evidence_error"),
])
def test_confirmed_answer_errors_are_split_by_cause(value_match, source_match, detail):
    """근거만 틀린 확정도 잘못된 확정이지만, 원인은 분리해서 센다."""
    label = _label(expected_decision="answer", expected_value="41.6", hold_reason="")
    bucket, got, _ = rs.assign_bucket(rs.V_CONFIRMED, label, value_match, source_match)
    assert bucket == rs.B_WRONG_CONFIRMATION
    assert got == detail


@pytest.mark.parametrize("reason", list(rs.HOLD_REASONS))
def test_confirmed_against_hold_label_is_wrong_confirmation_for_every_reason(reason):
    label = _label(expected_decision="hold", hold_reason=reason)
    bucket, detail, _ = rs.assign_bucket(rs.V_CONFIRMED, label, None, None)
    assert (bucket, detail) == (rs.B_WRONG_CONFIRMATION, rs.D_MISSED_MISMATCH)


def test_confirmed_against_na_label_is_wrong_confirmation():
    label = _label(expected_decision="na", hold_reason="")
    bucket, _, _ = rs.assign_bucket(rs.V_CONFIRMED, label, None, None)
    assert bucket == rs.B_WRONG_CONFIRMATION


def test_confirmed_answer_without_a_comparison_rule_stays_unresolved():
    """판정표 **이전** 단계의 미정은 그대로 남긴다 — 임의로 정답·오답에 넣지 않는다."""
    label = _label(expected_decision="answer", expected_value="목록", hold_reason="")
    bucket, detail, reason = rs.assign_bucket(rs.V_CONFIRMED, label, None, True)
    assert (bucket, detail) == (rs.B_UNRESOLVED, "")
    assert "판정표 이전" in reason


def test_hold_verdict_against_answer_label_is_unnecessary_hold():
    label = _label(expected_decision="answer", expected_value="1", hold_reason="")
    bucket, _, _ = rs.assign_bucket(rs.V_HOLD, label, True, True)
    assert bucket == rs.B_UNNECESSARY_HOLD


def test_hold_against_hold_label_is_correct_hold_but_reason_is_separate():
    """보류 결정 일치로 집계하되, 사유는 따로 기록한다."""
    label = _label(expected_decision="hold", hold_reason="not_comparable")
    bucket, detail, _ = rs.assign_bucket(
        rs.V_HOLD, label, None, None, system_comparison="not_comparable")
    assert (bucket, detail) == (rs.B_CORRECT_HOLD, rs.D_HOLD_REASON_MATCH)

    bucket, detail, reason = rs.assign_bucket(
        rs.V_HOLD, label, None, None, system_comparison="mismatch")
    assert (bucket, detail) == (rs.B_CORRECT_HOLD, rs.D_HOLD_REASON_MISMATCH)
    assert "사유가 다르다" in reason


def test_unknown_system_hold_reason_is_never_counted_as_a_reason_match():
    """사유가 확인되지 않은 것을 사유 일치로 간주하지 않는다."""
    label = _label(expected_decision="hold", hold_reason="no_evidence")
    for comparison in ("", "compared", "알 수 없음"):
        bucket, detail, reason = rs.assign_bucket(
            rs.V_HOLD, label, None, None, system_comparison=comparison)
        assert (bucket, detail) == (rs.B_CORRECT_HOLD, rs.D_HOLD_REASON_UNKNOWN)
        assert "사유 일치로 세지 않는다" in reason


@pytest.mark.parametrize("verdict,decision", [
    (rs.V_HOLD, "na"),
    (rs.V_NOT_APPLICABLE, "hold"),
])
def test_decision_mismatch_is_neither_a_correct_hold_nor_an_answer(verdict, decision):
    label = _label(expected_decision=decision,
                   hold_reason="no_evidence" if decision == "hold" else "")
    bucket, _, reason = rs.assign_bucket(verdict, label, None, None)
    assert bucket == rs.B_DECISION_MISMATCH
    assert bucket not in (rs.B_CORRECT_HOLD, rs.B_CORRECT_ANSWER, rs.B_CORRECT_NA)
    assert "정상 보류도 정답도 아니다" in reason


def test_not_applicable_against_answer_label_is_flagged_as_wrongly_na():
    label = _label(expected_decision="answer", expected_value="1", hold_reason="")
    bucket, detail, _ = rs.assign_bucket(rs.V_NOT_APPLICABLE, label, True, True)
    assert (bucket, detail) == (rs.B_UNNECESSARY_HOLD, rs.D_WRONGLY_NOT_APPLICABLE)


def test_not_applicable_against_na_label_is_correct_na():
    label = _label(expected_decision="na", hold_reason="")
    bucket, _, _ = rs.assign_bucket(rs.V_NOT_APPLICABLE, label, None, None)
    assert bucket == rs.B_CORRECT_NA


def test_unparsed_is_not_counted_as_a_hold():
    """파싱 실패를 정상적인 보류로 바꾸지 않는다."""
    label = _label(expected_decision="hold", hold_reason="no_evidence")
    bucket, detail, reason = rs.assign_bucket(rs.V_UNPARSED, label, None)
    assert (bucket, detail) == (rs.B_UNRESOLVED, "")
    assert "읽을 수 없어" in reason and "보류로 세지 않는다" in reason


def test_undetermined_decision_stays_unresolved():
    label = _label(expected_decision="na", hold_reason="")
    bucket, _, reason = rs.assign_bucket(rs.V_UNDETERMINED, label, None)
    assert bucket == rs.B_UNRESOLVED
    assert "계약에 없다" in reason


def test_self_reported_needs_human_text_is_wrong_confirmation_conservatively():
    """§6.2 네 줄 밖이지만 2026-10-06에 **보수적 평가 정책**으로 확정됐다."""
    label = _label(expected_decision="hold", hold_reason="needs_human_text")
    bucket, detail, reason = rs.assign_bucket(rs.V_SELF_REPORTED, label, None)
    assert (bucket, detail) == (rs.B_WRONG_CONFIRMATION, rs.D_MISSED_MISMATCH)
    assert "보수적 평가 정책" in reason


def test_self_reported_against_na_label_is_wrong_confirmation():
    label = _label(expected_decision="na", hold_reason="")
    bucket, _, _ = rs.assign_bucket(rs.V_SELF_REPORTED, label, None)
    assert bucket == rs.B_WRONG_CONFIRMATION


def test_the_four_self_reported_rules_of_6_2_are_unchanged():
    """판정표를 맞추려고 §6.2 네 줄을 바꾸지 않았다."""
    answer = _label(expected_decision="answer", expected_value="41.6", hold_reason="")
    assert rs.assign_bucket(rs.V_SELF_REPORTED, answer, True)[:2] == (
        rs.B_UNNECESSARY_HOLD, rs.D_EVIDENCE_LINK_MISSING)
    assert rs.assign_bucket(rs.V_SELF_REPORTED, answer, False)[:2] == (
        rs.B_WRONG_CONFIRMATION, rs.D_MISSED_MISMATCH)
    no_ev = _label(expected_decision="hold", hold_reason="no_evidence")
    assert rs.assign_bucket(rs.V_SELF_REPORTED, no_ev, None)[:2] == (rs.B_CORRECT_HOLD, "")
    for reason in ("mismatch", "not_comparable", "scope_unconfirmed"):
        lbl = _label(expected_decision="hold", hold_reason=reason)
        assert rs.assign_bucket(rs.V_SELF_REPORTED, lbl, None)[:2] == (
            rs.B_WRONG_CONFIRMATION, rs.D_MISSED_MISMATCH), reason


def test_report_never_publishes_a_combined_score():
    """종합 점수는 **만들지 않는다.** 미완료 기능이 아니라 확정 요구사항이다."""
    rep = rs.ScoreReport()
    assert "만들지 않는다" in rep.metrics_policy["종합_점수"]
    assert not any(k.endswith(("_pct", "_rate", "_score")) for k in rep.to_dict())
    assert "산출 불가" in rep.metrics_policy["산출_불가"]


# ── 값 비교: 허용 오차·단위·0 ────────────────────────────────────────────
def test_tolerance_boundary_is_inclusive():
    # 경계가 **딱 맞는** 값을 쓴다(0.5는 이진 부동소수로 정확히 표현된다). 비교는
    # `abs(차) <= tolerance`이므로, 표현 오차가 끼는 값으로 경계를 재면 통과·실패가
    # 숫자 선택에 따라 달라진다 — 그 우연에 의존하지 않는다.
    label = _label(expected_decision="answer", expected_value="40.0", expected_unit="%",
                   tolerance="0.5", hold_reason="")
    inside, _ = rs.compare_value(label, _answer(value=40.5, unit="%"))
    outside, _ = rs.compare_value(label, _answer(value=40.75, unit="%"))
    assert inside is True
    assert outside is False


def test_default_tolerance_is_zero():
    label = _label(expected_decision="answer", expected_value="7.5", expected_unit="톤",
                   hold_reason="")
    assert label.tolerance == 0.0
    assert rs.compare_value(label, _answer(value=7.5, unit="톤"))[0] is True
    assert rs.compare_value(label, _answer(value=7.6, unit="톤"))[0] is False


def test_unit_mismatch_is_not_normalized_away():
    """단위 차이는 의미 차이다 — 환산·별칭으로 숨기지 않는다."""
    label = _label(expected_decision="answer", expected_value="7.5", expected_unit="톤",
                   hold_reason="")
    match, reason = rs.compare_value(label, _answer(value=7.5, unit="kg"))
    assert match is False
    assert "단위 불일치" in reason
    # t↔톤도 환산하지 않는다(별칭 계약이 없다).
    assert rs.compare_value(label, _answer(value=7.5, unit="t"))[0] is False


def test_notation_only_differences_are_normalized():
    """전각·공백·천 단위 구분기호는 표기 차이로 보고 정규화한다."""
    label = _label(expected_decision="answer", expected_value="3,250", expected_unit="kg",
                   hold_reason="")
    assert rs.compare_value(label, _answer(value=3250.0, unit=" KG "))[0] is True


def test_zero_value_is_compared_as_number_not_empty():
    label = _label(expected_decision="answer", expected_value="0", expected_unit="건",
                   hold_reason="")
    assert rs.compare_value(label, _answer(value=0, unit="건"))[0] is True
    assert rs.compare_value(label, _answer(value=1, unit="건"))[0] is False


@pytest.mark.parametrize("zero", [0, 0.0, False])
def test_zero_and_false_are_filled_values_not_empty(zero):
    assert af.is_filled(zero) is True
    assert rs._is_filled(zero) is True


def test_yes_no_comparison():
    label = _label(expected_decision="answer", expected_value="예", hold_reason="")
    assert rs.compare_value(label, _answer(value=True))[0] is True
    assert rs.compare_value(label, _answer(value=False))[0] is False
    # 라벨이 예/아니오인데 시스템이 수치면 일치가 아니다.
    assert rs.compare_value(label, _answer(value=1.0))[0] is False


def test_list_value_rule_is_undetermined():
    label = _label(expected_decision="answer", expected_value="가; 나", hold_reason="")
    match, reason = rs.compare_value(label, _answer(value=["가", "나"]))
    assert match is None
    assert "계약에 없다" in reason


def test_value_comparison_skipped_for_non_answer_labels():
    match, _ = rs.compare_value(_label(expected_decision="hold", hold_reason="no_evidence"),
                                _answer(value=1.0))
    assert match is None


# ── 근거 비교: 1-기준 페이지, 다중 정답 OR ──────────────────────────────
def test_any_expected_source_matching_counts_as_hit():
    label = _label(expected_sources="08_스크랩.pdf#3; 09_출석.pdf#1")
    # 두 번째 정답 근거만 맞아도 일치다.
    answer = _answer(evidence=(rs.EvidenceRef("09_출석.pdf", 1),))
    match, reason = rs.compare_sources(label, answer)
    assert match is True
    assert "09_출석.pdf#1" in reason


def test_source_page_off_by_one_is_mismatch():
    label = _label(expected_sources="08_스크랩.pdf#3")
    answer = _answer(evidence=(rs.EvidenceRef("08_스크랩.pdf", 4),))
    assert rs.compare_sources(label, answer)[0] is False


def test_source_without_page_is_mismatch_not_first_page():
    label = _label(expected_sources="08_스크랩.pdf#1")
    answer = _answer(evidence=(rs.EvidenceRef("08_스크랩.pdf", None),))
    match, reason = rs.compare_sources(label, answer)
    assert match is False
    assert "페이지가 없다" in reason


def test_no_expected_sources_is_undetermined():
    assert rs.compare_sources(_label(expected_sources=""), _answer())[0] is None


def test_value_and_source_match_are_recorded_separately(tmp_path):
    rows = [_label_row(qid="SYN-1", expected_decision="answer", hold_reason="",
                       expected_value="41.6", expected_unit="%",
                       expected_sources="08.pdf#3")]
    labels = rs.load_labels(_write_labels(tmp_path, rows))
    answers = {"initial": [_answer(qid="SYN-1", decision=rs.V_SELF_REPORTED,
                                   value=41.6, unit="%", evidence=())]}
    rep = rs.score(labels, answers, ("SYN-1",))
    (row,) = rep.rows
    assert row.value_match is True and row.source_match is False
    assert rep.value_match_counts == {"match": 1}
    assert rep.source_match_counts == {"mismatch": 1}
    assert row.bucket == rs.B_UNNECESSARY_HOLD


# ── 라벨 값 검증 ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("over, needle", [
    ({"stage": "final"}, "stage"),
    ({"qid": ""}, "qid"),
    ({"expected_decision": "answered"}, "expected_decision"),
    ({"expected_decision": "hold", "hold_reason": "typo"}, "hold_reason"),
    ({"expected_decision": "hold", "hold_reason": ""}, "hold_reason"),
    ({"expected_decision": "answer", "hold_reason": "no_evidence"}, "hold_reason"),
    ({"expected_decision": "answer", "hold_reason": "", "expected_value": ""}, "expected_value"),
    ({"expected_value": "41.6"}, "expected_value"),
    ({"tolerance": "-1"}, "tolerance"),
    ({"tolerance": "약간"}, "tolerance"),
    ({"expected_sources": "08.pdf"}, "expected_sources"),
    ({"expected_sources": "08.pdf#0"}, "1부터"),
    ({"expected_sources": "08.pdf#첫장"}, "정수"),
    ({"expected_sources": "#3"}, "파일명"),
])
def test_bad_label_values_are_rejected(over, needle):
    with pytest.raises(rs.LabelError) as err:
        _label(**over)
    assert needle in str(err.value)


def test_answer_label_requires_expected_sources():
    """`answer` 라벨에 근거 출처가 없으면 **라벨 검증 오류**다.

    비어 있는 행을 지표 분모에서 빼면 그만큼 비율이 높아진다. 그래서 조용히 넘기지
    않고 거부한다(2026-10-06 확정).
    """
    with pytest.raises(rs.LabelError) as err:
        _label(expected_decision="answer", hold_reason="", expected_value="예",
               expected_sources="")
    assert "expected_sources" in str(err.value)
    assert "분모" in str(err.value)


@pytest.mark.parametrize("decision, extra", [
    ("hold", {"hold_reason": "no_evidence"}),
    ("na", {"hold_reason": ""}),
])
def test_expected_sources_stays_optional_for_non_answer_labels(decision, extra):
    """`hold`·`na`에는 출처를 요구하지 않는다. 자료가 없어서 보류한 행이기 때문이다."""
    label = _label(expected_decision=decision, expected_sources="", **extra)
    assert label.expected_sources == ()


def test_missing_label_column_is_rejected():
    row = _label_row()
    del row["boundary_note"]
    with pytest.raises(rs.LabelError, match="열 누락"):
        rs.parse_label_row(row, "synthetic")


# ── 구성·중복·누락 검증 ─────────────────────────────────────────────────
def _full_labels(qids, stages=rs.STAGES):
    return [_label(stage=s, qid=q) for s in stages for q in qids]


def _full_answers(qids, stages=rs.STAGES):
    return {s: [_answer(stage=s, qid=q) for q in qids] for s in stages}


def test_structure_ok_for_complete_two_stage_set():
    rep = rs.check_structure(_full_labels(SYNTHETIC_QIDS), _full_answers(SYNTHETIC_QIDS),
                             SYNTHETIC_QIDS)
    assert rep.ok
    assert rep.rows_per_stage == {"initial": 3, "followup": 3}
    assert rep.total_rows == 6 == rep.expected_total_rows
    assert rep.expected_rows_per_stage == 3


def test_structure_detects_duplicates_missing_and_unexpected():
    labels = _full_labels(SYNTHETIC_QIDS)
    labels.append(_label(stage="initial", qid="SYN-1"))          # 중복 라벨
    labels.append(_label(stage="initial", qid="SYN-ZZZ"))        # 예상 외 라벨
    answers = _full_answers(SYNTHETIC_QIDS)
    answers["followup"] = [a for a in answers["followup"] if a.qid != "SYN-2"]  # 누락 응답
    answers["initial"].append(_answer(stage="initial", qid="SYN-1"))   # 중복 응답
    answers["initial"].append(_answer(stage="initial", qid="SYN-XXX"))  # 예상 외 응답

    rep = rs.check_structure(labels, answers, SYNTHETIC_QIDS)
    assert not rep.ok
    assert rep.duplicate_labels == ["initial/SYN-1"]
    assert rep.duplicate_answers == ["initial/SYN-1"]
    assert rep.missing_answers == ["followup/SYN-2"]
    assert rep.unexpected_answers == ["initial/SYN-XXX"]
    assert rep.unexpected_labels == ["initial/SYN-ZZZ"]


def test_rba42_stage_and_total_row_counts_come_from_the_framework():
    """단계별 48행·전체 96행 구성은 양식에서 읽는다(채점기에 박지 않는다)."""
    qids = rs.framework_qids("rba42")
    assert len(qids) == 48
    rep = rs.check_structure(_full_labels(qids), _full_answers(qids), qids)
    assert rep.ok
    assert rep.rows_per_stage == {"initial": 48, "followup": 48}
    assert rep.total_rows == 96


def test_unmatched_rows_are_reported_not_silently_dropped():
    labels = [_label(stage="initial", qid="SYN-1"), _label(stage="followup", qid="SYN-2")]
    answers = {"initial": [_answer(stage="initial", qid="SYN-3")]}
    rep = rs.score(labels, answers, SYNTHETIC_QIDS)
    assert rep.rows == []
    assert rep.unscored_pairs == [
        "followup/SYN-2: 대응하는 응답 행이 없다",
        "initial/SYN-1: 대응하는 응답 행이 없다",
        "initial/SYN-3: 대응하는 라벨 행이 없다",
    ]


# ── 여러 실행을 합치지 않는다 ────────────────────────────────────────────
def test_two_documents_for_one_stage_are_refused():
    """실행이 다른 응답을 한 단계로 합치면 어느 실행의 결과인지 알 수 없다."""
    docs = [_doc([af.new_answer(qid="SYN-1", decision=af.D_UNVERIFIED, value=1)],
                 run_id="RUN-A"),
            _doc([af.new_answer(qid="SYN-1", decision=af.D_UNVERIFIED, value=2)],
                 run_id="RUN-B")]
    with pytest.raises(af.FormatError, match="실행별로 따로 채점"):
        rs.score_documents([_label()], docs, SYNTHETIC_QIDS)


def test_score_documents_records_each_input_document():
    doc = _doc([af.new_answer(qid="SYN-1", decision=af.D_UNVERIFIED, value=1)],
               system="general_ai", model="some-model", run_id="RUN-A",
               data_source="촬영증빙_12건")
    rep = rs.score_documents([_label()], [doc], SYNTHETIC_QIDS)
    (meta,) = rep.documents
    assert meta["system"] == "general_ai" and meta["run_id"] == "RUN-A"
    assert meta["model"] == "some-model" and meta["data_source"] == "촬영증빙_12건"
    assert meta["answer_rows"] == 1


# ── CLI ───────────────────────────────────────────────────────────────────
def _cli_labels(tmp_path):
    rows = [_label_row(stage="initial", qid="SYN-1", expected_decision="hold",
                       hold_reason="mismatch"),
            _label_row(stage="initial", qid="SYN-2", expected_decision="answer",
                       hold_reason="", expected_value="0", expected_unit="건",
                       expected_sources="08.pdf#1"),
            _label_row(stage="initial", qid="SYN-3", expected_decision="na",
                       hold_reason="")]
    return _write_labels(tmp_path, rows)


def _cli_esgenie_result(tmp_path):
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"sheet": {"answers": [
        {"qid": "SYN-1", "status": "self_reported", "value": 63.0, "unit": "%"},
        {"qid": "SYN-2", "status": "self_reported", "value": 0, "unit": "건",
         "evidence_links": [{"file_name": "08.pdf", "page": 0}]},
        {"qid": "SYN-3", "status": "not_applicable", "value": None},
    ]}}), encoding="utf-8")
    return str(path)


def test_cli_scores_esgenie_result_through_the_adapter(tmp_path, capsys):
    out = tmp_path / "score.json"
    common_dir = tmp_path / "common"
    code = cli.main(["--labels", _cli_labels(tmp_path),
                     "--esgenie-result", f"initial={_cli_esgenie_result(tmp_path)}",
                     "--run-id", "SYN-RUN-1", "--data-source", "synthetic",
                     "--framework", "rba42", "--write-common", str(common_dir),
                     "--out", str(out)])
    # rba42 48행과 맞지 않아 형식 문제가 남는다 — 조용히 넘기지 않는다.
    assert code == 3
    assert "wrote" in capsys.readouterr().out

    report = json.loads(out.read_text(encoding="utf-8"))
    # `not_applicable × na`는 2026-10-06 확정 판정표에서 `correct_na`다(미정이 아니다).
    assert report["bucket_counts"] == {rs.B_WRONG_CONFIRMATION: 1,
                                       rs.B_UNNECESSARY_HOLD: 1,
                                       rs.B_CORRECT_NA: 1}
    assert report["detail_counts"] == {rs.D_MISSED_MISMATCH: 1,
                                       rs.D_EVIDENCE_LINK_MISSING: 1}
    assert report["verdict_counts"] == {rs.V_SELF_REPORTED: 2, rs.V_NOT_APPLICABLE: 1}
    assert report["unresolved_reasons"] == {}
    assert report["structure"]["ok"] is False
    assert len(report["structure"]["missing_answers"]) == 48
    assert "만들지 않는다" in report["metrics_policy"]["종합_점수"]
    # 보고 단위는 시스템·실행·단계별이다. 한 단계만 있으므로 합산을 만들지 않는다.
    (scope,) = report["metric_scopes"]
    assert (scope["system"], scope["run_id"], scope["stage"]) == ("esgenie", "SYN-RUN-1",
                                                                  "initial")
    assert scope["aggregated"] is False
    # M4 분모는 실행·단계별 예상 문항 전체다 — 응답이 온 행 수가 아니다.
    assert scope["metrics"][rs.M4]["denominator"] == 48
    assert {i["code"] for i in report["format_issues"]} == {"missing_qid", "unknown_qid",
                                                            "row_count"}
    # 변환 결과를 남겨 원본과 대조할 수 있다.
    saved = json.loads((common_dir / "esgenie_SYN-RUN-1_initial.json")
                       .read_text(encoding="utf-8"))
    assert saved["meta"]["source_ref"]["sha256"]


def test_cli_refuses_to_overwrite_an_existing_result(tmp_path):
    out = tmp_path / "score.json"
    out.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit):
        cli.main(["--labels", _cli_labels(tmp_path),
                  "--esgenie-result", f"initial={_cli_esgenie_result(tmp_path)}",
                  "--run-id", "SYN-RUN-1", "--data-source", "synthetic",
                  "--out", str(out)])


def test_cli_scores_a_common_format_document_from_any_system(tmp_path):
    doc_path = tmp_path / "control.json"
    af.dump_document(_doc([
        af.new_answer(qid="SYN-1", decision=af.D_UNVERIFIED, value=63.0, unit="%"),
        af.new_answer(qid="SYN-2", decision=af.D_UNVERIFIED, value=0, unit="건",
                      evidence=[{"file_name": "08.pdf", "page": 1}]),
        af.new_answer(qid="SYN-3", decision=af.D_NOT_APPLICABLE, value=None),
    ], system="general_ai", model="some-model"), doc_path)

    out = tmp_path / "score.json"
    code = cli.main(["--labels", _cli_labels(tmp_path), "--answers", str(doc_path),
                     "--out", str(out)])
    assert code == 3  # 48행 구성과 맞지 않음(형식 문제로 드러난다)
    report = json.loads(out.read_text(encoding="utf-8"))
    # ESGenie 입력과 같은 집계 — 채점 로직이 하나임을 고정한다.
    assert report["bucket_counts"] == {rs.B_WRONG_CONFIRMATION: 1,
                                       rs.B_UNNECESSARY_HOLD: 1,
                                       rs.B_CORRECT_NA: 1}
    assert report["documents"][0]["system"] == "general_ai"


def test_cli_rejects_unreadable_input_instead_of_treating_it_as_hold(tmp_path, capsys):
    bad = tmp_path / "broken.json"
    bad.write_text("{not json", encoding="utf-8")
    code = cli.main(["--labels", _cli_labels(tmp_path), "--answers", str(bad),
                     "--out", str(tmp_path / "score.json")])
    assert code == 2
    assert "입력 오류" in capsys.readouterr().err


# ── 지표(2026-10-06 확정 정책) ───────────────────────────────────────────
def _ev(file_name="SYN_합성증빙.pdf", page=1) -> rs.EvidenceRef:
    return rs.EvidenceRef(file_name, page)


def _scope(labels, answers, qids, stage="initial"):
    rep = rs.score(labels, {stage: answers}, qids)
    return rep, rep.metric_scopes[0]


def test_m1_denominator_is_every_answer_label_including_missing_and_unparsed():
    """M1 분모는 `answer` 라벨 **전체**다 — 응답 누락·파싱 실패도 들어간다.

    분자는 `confirmed`로 내고 값·근거가 **모두** 맞은 행만이다. 미검증 전달은 세지
    않는다. "제출한 답변 중 정답 비율"이 아니다.
    """
    labels = [
        _label(qid="A", expected_decision="answer", hold_reason="", expected_value="7.5",
               expected_unit="톤", expected_sources="SYN_합성증빙.pdf#1"),
        _label(qid="B", expected_decision="answer", hold_reason="", expected_value="7.5",
               expected_unit="톤", expected_sources="SYN_합성증빙.pdf#1"),
        _label(qid="C", expected_decision="answer", hold_reason="", expected_value="7.5",
               expected_unit="톤", expected_sources="SYN_합성증빙.pdf#1"),
        _label(qid="D", expected_decision="answer", hold_reason="", expected_value="7.5",
               expected_unit="톤", expected_sources="SYN_합성증빙.pdf#1"),
        _label(qid="E", expected_decision="hold", hold_reason="no_evidence"),
    ]
    answers = [
        # 정답 — 확정 + 값·근거 일치
        _answer(qid="A", decision=rs.V_CONFIRMED, value=7.5, unit="톤", evidence=(_ev(),)),
        # 값은 맞지만 **미검증 전달** → 분자 제외
        _answer(qid="B", decision=rs.V_SELF_REPORTED, value=7.5, unit="톤",
                evidence=(_ev(),)),
        # 확정이지만 근거가 틀렸다 → 분자 제외
        _answer(qid="C", decision=rs.V_CONFIRMED, value=7.5, unit="톤",
                evidence=(_ev("다른파일.pdf", 9),)),
        # D는 응답 자체가 없다 → 분모에 남고 분자에서 빠진다
    ]
    rep, scope = _scope(labels, answers, ("A", "B", "C", "D", "E"))
    m1 = scope["metrics"][rs.M1]
    assert m1["denominator"] == 4          # answer 라벨 4건. hold 라벨은 섞지 않는다
    assert m1["numerator"] == 1            # A만
    assert m1["status"] == rs.MS_AVAILABLE
    assert m1["value"] == 0.25
    assert "제출한 답변 중 정답 비율이 아니다" in m1["note"]


def test_m1_is_withheld_when_a_row_has_no_comparison_rule():
    """값 비교 규칙이 없는 행이 남으면 **공식 비율을 보류한다.**

    분모에서 빼서 비율을 만들지 않는다.
    """
    labels = [_label(qid="A", expected_decision="answer", hold_reason="",
                     expected_value="목록", expected_sources="SYN_합성증빙.pdf#1")]
    answers = [_answer(qid="A", decision=rs.V_CONFIRMED, value=["가", "나"],
                       evidence=(_ev(),))]
    _, scope = _scope(labels, answers, ("A",))
    m1 = scope["metrics"][rs.M1]
    assert m1["status"] == rs.MS_WITHHELD
    assert m1["value"] is None
    assert m1["undetermined_rows"] == 1
    assert m1["denominator"] == 1          # 분모는 그대로 남는다
    assert any("목록" in r for r in m1["reasons"])


def test_m4_is_the_confirmed_submission_share_not_a_verification_rate():
    """M4 분모는 예상 문항 전체, 분자는 `confirmed`뿐이다. '검증 확정률'이 아니다."""
    labels = [_label(qid=q, expected_decision="hold", hold_reason="no_evidence")
              for q in ("A", "B")]
    answers = [_answer(qid="A", decision=rs.V_CONFIRMED, value=1.0),
               _answer(qid="B", decision=rs.V_SELF_REPORTED, value=2.0)]
    _, scope = _scope(labels, answers, ("A", "B", "C", "D"))
    m4 = scope["metrics"][rs.M4]
    assert (m4["numerator"], m4["denominator"]) == (1, 4)
    assert "검증 확정률" in m4["note"] and "아니다" in m4["note"]
    # 값 제출률은 보조로만 낸다 — 같은 지표에 합치지 않는다.
    aux = scope["auxiliary"]["A3_값_제출률_보조"]
    assert (aux["numerator"], aux["denominator"]) == (2, 4)


def test_m5_counts_zero_as_a_filled_value_and_is_not_computable_when_empty():
    """M5 분모는 **값이 채워진** 제출 행이다. `0`·`False`를 빈 값으로 보지 않는다."""
    labels = [_label(qid=q, expected_decision="hold", hold_reason="no_evidence")
              for q in ("A", "B", "C")]
    answers = [
        _answer(qid="A", decision=rs.V_CONFIRMED, value=0, evidence=(_ev(),)),
        _answer(qid="B", decision=rs.V_CONFIRMED, value=False, evidence=()),
        _answer(qid="C", decision=rs.V_HOLD, value=None),
    ]
    _, scope = _scope(labels, answers, ("A", "B", "C"))
    m5 = scope["metrics"][rs.M5]
    assert (m5["numerator"], m5["denominator"]) == (1, 2)
    assert m5["status"] == rs.MS_AVAILABLE


def test_m5_zero_denominator_is_not_computable_not_zero_percent():
    labels = [_label(qid="A", expected_decision="hold", hold_reason="no_evidence")]
    answers = [_answer(qid="A", decision=rs.V_HOLD, value=None)]
    _, scope = _scope(labels, answers, ("A",))
    m5 = scope["metrics"][rs.M5]
    assert m5["status"] == rs.MS_NOT_COMPUTABLE
    assert m5["value"] is None and m5["denominator"] == 0
    assert "0%가 아니다" in m5["note"]


def test_m5_and_a5_are_different_metrics():
    """'자료 연결률'과 '연결한 근거가 정답인 비율'을 같은 것으로 취급하지 않는다."""
    labels = [_label(qid="A", expected_decision="answer", hold_reason="",
                     expected_value="7.5", expected_unit="톤",
                     expected_sources="SYN_합성증빙.pdf#1")]
    answers = [_answer(qid="A", decision=rs.V_CONFIRMED, value=7.5, unit="톤",
                       evidence=(_ev("엉뚱한파일.pdf", 3),))]
    _, scope = _scope(labels, answers, ("A",))
    assert scope["metrics"][rs.M5]["value"] == 1.0        # 연결은 했다
    assert scope["auxiliary"]["A5_연결한_근거가_정답인_비율"]["value"] == 0.0  # 정답은 아니다


def test_source_match_has_two_levels_and_file_only_is_auxiliary():
    """근거 일치를 파일 / 파일+쪽 두 단계로 나눈다. 공식은 파일+쪽이다."""
    labels = [_label(qid="A", expected_decision="answer", hold_reason="",
                     expected_value="7.5", expected_unit="톤",
                     expected_sources="SYN_합성증빙.pdf#3")]
    answers = [_answer(qid="A", decision=rs.V_CONFIRMED, value=7.5, unit="톤",
                       evidence=(_ev(page=1),))]
    rep, scope = _scope(labels, answers, ("A",))
    (row,) = rep.rows
    assert row.source_match is False          # 쪽이 다르다 → 공식은 불일치
    assert row.source_file_match is True      # 파일은 같다 → 보조는 일치
    a2 = scope["auxiliary"]["A2_근거_일치_건수"]
    assert a2["파일+쪽_일치(공식)"] == 0 and a2["파일_일치(보조)"] == 1
    assert "대체하지 않는다" in a2["주의"]


def test_na_labels_are_excluded_from_m1_kept_in_m4_and_reported_separately():
    """`na`는 M1 분모에서 빼고 M4 분모에는 넣는다. 판정 일치·불일치를 따로 낸다."""
    labels = [_label(qid="A", expected_decision="na", hold_reason=""),
              _label(qid="B", expected_decision="na", hold_reason="")]
    answers = [_answer(qid="A", decision=rs.V_NOT_APPLICABLE, value=None),
               _answer(qid="B", decision=rs.V_CONFIRMED, value=5.0, evidence=(_ev(),))]
    _, scope = _scope(labels, answers, ("A", "B"))
    assert scope["metrics"][rs.M1]["denominator"] == 0
    assert scope["metrics"][rs.M1]["status"] == rs.MS_NOT_COMPUTABLE
    assert scope["metrics"][rs.M4]["denominator"] == 2
    na = scope["auxiliary"]["na_라벨_판정"]
    assert (na["행"], na["일치"], na["불일치"]) == (2, 1, 1)
    # na 행도 값이 채워진 제출이면 M5 분모에 남는다 — 추가 제외하지 않는다.
    assert scope["metrics"][rs.M5]["denominator"] == 1


def test_counts_are_labelled_as_confirmed_rows_only():
    """M2·M3는 '확정된 행에서 확인된 건수'로 표시한다 — 미정 행은 빠져 있다."""
    labels = [_label(qid="A", expected_decision="hold", hold_reason="mismatch"),
              _label(qid="B", expected_decision="hold", hold_reason="no_evidence")]
    # B는 원출력을 읽을 수 없어 판정표 이전 단계에서 미정이다 — 정상 보류로 바꾸지 않는다.
    answers = [_answer(qid="A", decision=rs.V_SELF_REPORTED, value=1.0),
               _answer(qid="B", decision=rs.V_UNPARSED, value=None)]
    _, scope = _scope(labels, answers, ("A", "B"))
    m2 = scope["metrics"][rs.M2]
    assert m2["kind"] == "count" and m2["numerator"] == 1
    assert "확정된 행 1건" in m2["note"] and "미정 행 1건" in m2["note"]
    assert scope["auxiliary"]["A6_미해결_행"] == 1


def test_one_stage_alone_does_not_produce_an_aggregate_scope():
    """한 단계만 있으면 합산을 만들지 않는다 — 한 단계를 '전체'라고 부르지 않는다."""
    labels = [_label(qid="A", expected_decision="hold", hold_reason="no_evidence")]
    answers = [_answer(qid="A", decision=rs.V_HOLD, value=None)]
    rep = rs.score(labels, {"initial": answers}, ("A",))
    assert [s["stage"] for s in rep.metric_scopes] == ["initial"]
    assert all(s["aggregated"] is False for s in rep.metric_scopes)


def test_two_stages_add_a_reference_aggregate_but_keep_per_stage_first():
    labels = [_label(stage=s, qid="A", expected_decision="hold", hold_reason="no_evidence")
              for s in ("initial", "followup")]
    rep = rs.score(labels, {
        "initial": [_answer(qid="A", decision=rs.V_HOLD, value=None)],
        "followup": [_answer(stage="followup", qid="A", decision=rs.V_HOLD, value=None)],
    }, ("A",))
    scopes = {s["stage"]: s for s in rep.metric_scopes}
    assert set(scopes) == {"initial", "followup", "initial+followup"}
    assert scopes["initial"]["aggregated"] is False
    assert scopes["initial+followup"]["aggregated"] is True
    assert "참고" in scopes["initial+followup"]["label"]
    # 합산 M4 분모는 단계 수만큼 늘어난다(표본이 늘어난 것이 아니다).
    assert scopes["initial+followup"]["metrics"][rs.M4]["denominator"] == 2


def test_input_completeness_separates_missing_rows_from_holds():
    """누락·파싱 실패를 정상 보류로 바꾸지 않는다."""
    labels = [_label(qid=q, expected_decision="hold", hold_reason="no_evidence")
              for q in ("A", "B", "C")]
    answers = [_answer(qid="A", decision=rs.V_HOLD, value=None),
               _answer(qid="B", decision=rs.V_UNPARSED, value=None)]
    _, scope = _scope(labels, answers, ("A", "B", "C"))
    comp = scope["input_completeness"]
    assert comp["라벨_행"] == 3 and comp["채점된_행"] == 2
    assert comp["응답_누락_행"] == 1 and comp["파싱_실패_행"] == 1
