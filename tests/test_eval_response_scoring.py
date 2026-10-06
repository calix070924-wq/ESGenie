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
    doc = _doc([af.new_answer(qid="SYN-1", decision=af.D_CONFIRMED, value=18.4, unit="톤",
                              evidence=[{"file_name": "08.pdf", "page": 3, "quote": None},
                                        {"file_name": "09.pdf", "page": None}])])
    (answer,) = rs.answers_from_document(doc)
    assert answer.stage == "initial"
    assert [e.page_1based for e in answer.evidence] == [3, None]


def test_source_fields_are_preserved_for_inspection():
    doc = _doc([af.new_answer(qid="SYN-1", decision=af.D_UNVERIFIED, value=92.0,
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
    label = _label(expected_decision="answer", expected_value="29.3", hold_reason="")
    bucket, detail, _ = rs.assign_bucket(rs.V_SELF_REPORTED, label, False)
    assert (bucket, detail) == (rs.B_WRONG_CONFIRMATION, rs.D_MISSED_MISMATCH)


def test_self_reported_answer_value_match_is_unnecessary_hold():
    label = _label(expected_decision="answer", expected_value="29.3", hold_reason="")
    bucket, detail, _ = rs.assign_bucket(rs.V_SELF_REPORTED, label, True)
    assert (bucket, detail) == (rs.B_UNNECESSARY_HOLD, rs.D_EVIDENCE_LINK_MISSING)


# ── §6.3 미정 조합을 임의로 처리하지 않는다 ─────────────────────────────
@pytest.mark.parametrize("verdict", [rs.V_CONFIRMED, rs.V_HOLD, rs.V_NOT_APPLICABLE])
def test_non_self_reported_combinations_stay_unresolved(verdict):
    """확정·보류·해당없음 × 라벨의 집계 규칙은 아직 계약에 없다."""
    for label in (_label(expected_decision="hold", hold_reason="no_evidence"),
                  _label(expected_decision="answer", expected_value="1", hold_reason=""),
                  _label(expected_decision="na", hold_reason="")):
        bucket, detail, reason = rs.assign_bucket(verdict, label, True)
        assert bucket == rs.B_UNRESOLVED, (verdict, label.expected_decision)
        assert detail == ""
        assert "계약에 없다" in reason


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


def test_self_reported_needs_human_text_and_na_are_unresolved():
    """§6.2가 다루지 않는 조합 — 정답·오답에 임의 배정하지 않는다."""
    for label in (_label(expected_decision="hold", hold_reason="needs_human_text"),
                  _label(expected_decision="na", hold_reason="")):
        bucket, _, reason = rs.assign_bucket(rs.V_SELF_REPORTED, label, None)
        assert bucket == rs.B_UNRESOLVED
        assert "계약에 없다" in reason


def test_report_refuses_to_publish_metrics():
    rep = rs.ScoreReport()
    assert "산출하지 않는다" in rep.metrics_blocked_reason
    assert not any(k.endswith(("_pct", "_rate", "_score")) for k in rep.to_dict())


# ── 값 비교: 허용 오차·단위·0 ────────────────────────────────────────────
def test_tolerance_boundary_is_inclusive():
    label = _label(expected_decision="answer", expected_value="29.3", expected_unit="%",
                   tolerance="0.1", hold_reason="")
    inside, _ = rs.compare_value(label, _answer(value=29.4, unit="%"))
    outside, _ = rs.compare_value(label, _answer(value=29.41, unit="%"))
    assert inside is True
    assert outside is False


def test_default_tolerance_is_zero():
    label = _label(expected_decision="answer", expected_value="18.4", expected_unit="톤",
                   hold_reason="")
    assert label.tolerance == 0.0
    assert rs.compare_value(label, _answer(value=18.4, unit="톤"))[0] is True
    assert rs.compare_value(label, _answer(value=18.5, unit="톤"))[0] is False


def test_unit_mismatch_is_not_normalized_away():
    """단위 차이는 의미 차이다 — 환산·별칭으로 숨기지 않는다."""
    label = _label(expected_decision="answer", expected_value="18.4", expected_unit="톤",
                   hold_reason="")
    match, reason = rs.compare_value(label, _answer(value=18.4, unit="kg"))
    assert match is False
    assert "단위 불일치" in reason
    # t↔톤도 환산하지 않는다(별칭 계약이 없다).
    assert rs.compare_value(label, _answer(value=18.4, unit="t"))[0] is False


def test_notation_only_differences_are_normalized():
    """전각·공백·천 단위 구분기호는 표기 차이로 보고 정규화한다."""
    label = _label(expected_decision="answer", expected_value="12,500", expected_unit="kg",
                   hold_reason="")
    assert rs.compare_value(label, _answer(value=12500.0, unit=" KG "))[0] is True


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
                       expected_value="29.3", expected_unit="%",
                       expected_sources="08.pdf#3")]
    labels = rs.load_labels(_write_labels(tmp_path, rows))
    answers = {"initial": [_answer(qid="SYN-1", decision=rs.V_SELF_REPORTED,
                                   value=29.3, unit="%", evidence=())]}
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
    ({"expected_value": "29.3"}, "expected_value"),
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
        {"qid": "SYN-1", "status": "self_reported", "value": 92.0, "unit": "%"},
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
    assert report["bucket_counts"] == {rs.B_WRONG_CONFIRMATION: 1,
                                       rs.B_UNNECESSARY_HOLD: 1,
                                       rs.B_UNRESOLVED: 1}
    assert report["detail_counts"] == {rs.D_MISSED_MISMATCH: 1,
                                       rs.D_EVIDENCE_LINK_MISSING: 1}
    assert report["verdict_counts"] == {rs.V_SELF_REPORTED: 2, rs.V_NOT_APPLICABLE: 1}
    assert sum(report["unresolved_reasons"].values()) == 1
    assert report["structure"]["ok"] is False
    assert len(report["structure"]["missing_answers"]) == 48
    assert "산출하지 않는다" in report["metrics_blocked_reason"]
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
        af.new_answer(qid="SYN-1", decision=af.D_UNVERIFIED, value=92.0, unit="%"),
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
                                       rs.B_UNRESOLVED: 1}
    assert report["documents"][0]["system"] == "general_ai"


def test_cli_rejects_unreadable_input_instead_of_treating_it_as_hold(tmp_path, capsys):
    bad = tmp_path / "broken.json"
    bad.write_text("{not json", encoding="utf-8")
    code = cli.main(["--labels", _cli_labels(tmp_path), "--answers", str(bad),
                     "--out", str(tmp_path / "score.json")])
    assert code == 2
    assert "입력 오류" in capsys.readouterr().err
