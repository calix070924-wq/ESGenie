"""독립 검토 인정 테스트 — 확정된 제외 규칙이 지켜지는지 본다.

입력은 전부 합성이다. 실제 노출 기록·라벨을 쓰지 않는다.
"""
from __future__ import annotations

import csv

import pytest

from esgenie.eval import independence as ind

ROWS = [("initial", "SYN-1"), ("followup", "SYN-1"),
        ("initial", "SYN-2"), ("followup", "SYN-2")]


def _exp(**kw):
    base = dict(labeler="B", stage="initial", qid="SYN-1", exposure="expected_value")
    base.update(kw)
    return ind.Exposure(**base)


def test_unreported_rows_are_pending_not_independent():
    """회신이 없으면 독립으로 세지 않는다 — 분모에 넣지 않는다."""
    rep = ind.classify(ROWS, [], labeler="B")
    assert rep.sample_rows == 4
    assert rep.pending_rows == 4
    assert rep.independent_rows == 0
    assert rep.excluded_rows == 0
    assert rep.agreement_denominator == 0
    assert "산출하지 않는다" in rep.agreement_blocked_reason


def test_exposed_row_is_excluded_from_numerator_and_denominator():
    rep = ind.classify(ROWS, [_exp(), _exp(stage="followup")], labeler="B")
    assert rep.excluded_rows == 2
    assert rep.excluded_qids == ["SYN-1"]
    assert all(r.status == ind.EXCLUDED for r in rep.rows if r.qid == "SYN-1")
    # 제외는 집계에서만 한다 — 표본 행 수는 그대로다.
    assert rep.sample_rows == 4


def test_exposure_does_not_depend_on_whether_the_label_matched():
    """일치·불일치를 입력으로 받지 않는다 — 받을 수 있는 통로 자체가 없다."""
    import inspect
    params = inspect.signature(ind.classify).parameters
    assert set(params) == {"sample_rows", "exposures", "labeler", "set_id"}


def test_cleared_rows_are_independent_and_counted():
    cleared = [_exp(exposure="none", stage=ind.ALL_STAGES, qid=q) for q in ("SYN-1", "SYN-2")]
    rep = ind.classify(ROWS, cleared, labeler="B")
    assert rep.independent_rows == 4
    assert rep.pending_rows == 0
    assert rep.agreement_denominator == 4
    assert rep.agreement_blocked_reason == ""


def test_whole_pr_access_does_not_exclude_every_question():
    """전체 PR 접근만으로 일괄 제외하지 않는다 — 적힌 문항만 제외된다."""
    rep = ind.classify(ROWS, [_exp(stage=ind.ALL_STAGES, source="PR #72 전체 열람")],
                       labeler="B")
    assert rep.excluded_qids == ["SYN-1"]
    assert rep.excluded_rows == 2
    assert [r.status for r in rep.rows if r.qid == "SYN-2"] == [ind.PENDING, ind.PENDING]


def test_exposure_of_one_labeler_does_not_affect_another():
    rep = ind.classify(ROWS, [_exp(labeler="A", stage=ind.ALL_STAGES)], labeler="B")
    assert rep.excluded_rows == 0
    assert rep.pending_rows == 4


def test_system_answer_exposure_also_excludes():
    rep = ind.classify(ROWS, [_exp(exposure="system_answer", stage=ind.ALL_STAGES)],
                       labeler="B")
    assert rep.excluded_rows == 2
    assert "system_answer" in rep.rows[0].reason


def test_pending_rows_are_not_filled_into_the_denominator():
    rep = ind.classify(ROWS, [_exp(exposure="none", stage=ind.ALL_STAGES, qid="SYN-1")],
                       labeler="B")
    assert rep.independent_rows == 2 and rep.pending_rows == 2
    assert rep.agreement_denominator == 2          # 대기 2행을 넣지 않는다
    assert rep.agreement_blocked_reason           # 대기가 있으면 산출하지 않는다


def test_exposure_csv_round_trip(tmp_path):
    path = tmp_path / "exposure.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(ind.EXPOSURE_COLUMNS)
        w.writerow(["B", "all", "SYN-1", "expected_value", "문서 X", "본인 신고", ""])
    loaded = ind.load_exposures(path)
    assert loaded == [ind.Exposure("B", "all", "SYN-1", "expected_value",
                                   "문서 X", "본인 신고", "")]


def test_bad_exposure_rows_are_rejected(tmp_path):
    bad_rows = [
        ["", "all", "SYN-1", "expected_value", "", "", ""],          # labeler 없음
        ["B", "", "SYN-1", "expected_value", "", "", ""],            # stage 없음
        ["B", "all", "", "expected_value", "", "", ""],               # qid 없음
        ["B", "2026", "SYN-1", "expected_value", "", "", ""],        # stage 값 오류
        ["B", "all", "SYN-1", "maybe", "", "", ""],                   # exposure 값 오류
    ]
    for i, row in enumerate(bad_rows):
        path = tmp_path / f"bad{i}.csv"
        with path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(ind.EXPOSURE_COLUMNS)
            w.writerow(row)
        with pytest.raises(ind.ExposureError):
            ind.load_exposures(path)


def test_missing_exposure_column_is_rejected(tmp_path):
    path = tmp_path / "short.csv"
    path.write_text("labeler,stage,qid\nB,all,SYN-1\n", encoding="utf-8")
    with pytest.raises(ind.ExposureError):
        ind.load_exposures(path)


def test_report_has_no_agreement_rate():
    """일치율을 내지 않는다 — 비율 키가 아예 없다."""
    rep = ind.classify(ROWS, [], labeler="B").to_dict()
    assert not [k for k in rep if "rate" in k or "ratio" in k or "accuracy" in k]
    assert "agreement_denominator" in rep


def test_excluded_labels_are_kept_not_discarded():
    note = ind.label_usability_note()
    assert "폐기하지 않는다" in note
    assert "별개 판단" in note
