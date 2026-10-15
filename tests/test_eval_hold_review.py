"""보류 판단 적절성 평가(v2) — 합성 입력 테스트. 실제 라벨·응답 값을 쓰지 않는다."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from esgenie.eval import hold_review as hr


def _row(qid, bucket, label="answer", vm=True, sm=True, stage="initial"):
    return {"stage": stage, "qid": qid, "label_decision": label, "system_decision": "hold",
            "system_status": "flagged", "system_comparison": "", "value_match": vm, "source_match": sm,
            "bucket": bucket}


def _score(rows, stages=("initial",)):
    return {"rows": rows, "documents": [{"stage": s, "system": "sysA", "run_id": "R1"} for s in stages],
            "label_file": "labels.csv", "bucket_counts": {}}


def _write(tmp: Path, rows: list[dict]) -> Path:
    p = tmp / "rev.csv"
    with open(p, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=hr.REVIEW_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in hr.REVIEW_COLUMNS})
    return p


def _rev(qid, verdict, crit, approval="approved", stage="initial"):
    return {"system": "sysA", "run_id": "R1", "stage": stage, "qid": qid, "verdict": verdict,
            "criterion": crit, "system_hold_basis": "q", "assessment": "근거", "reviewer": "A",
            "approval": approval, "approved_by": "지민" if approval == "approved" else ""}


def test_only_unnecessary_hold_rows_are_reclassified(tmp_path):
    rows = [_row("S1", "unnecessary_hold"), _row("S2", "unnecessary_hold"), _row("S3", "unnecessary_hold"),
            _row("S4", "correct_answer"), _row("S5", "wrong_confirmation", label="hold"),
            _row("S6", "correct_hold", label="hold")]
    revs = hr.load_reviews(_write(tmp_path, [_rev("S1", "justified", "J1"), _rev("S2", "unjustified", "U"),
                                             _rev("S3", "pending", "P"), _rev("S4", "justified", "J2")]))
    rep = hr.apply_v2(_score(rows), revs)
    got = {r["qid"]: r["bucket_v2"] for r in rep["rows"]}
    assert got == {"S1": "justified_review_hold", "S2": "unnecessary_hold", "S3": "hold_review_pending",
                   "S4": "correct_answer", "S5": "wrong_confirmation", "S6": "correct_hold"}
    assert all(r["bucket_v1"] == b for r, b in zip(rep["rows"], ["unnecessary_hold"] * 3 +
                                                   ["correct_answer", "wrong_confirmation", "correct_hold"]))


def test_missing_review_is_pending_not_justified(tmp_path):
    rep = hr.apply_v2(_score([_row("S1", "unnecessary_hold")]), hr.load_reviews(_write(tmp_path, [])))
    assert rep["rows"][0]["bucket_v2"] == "hold_review_pending"


def test_proposed_verdict_counts_as_pending(tmp_path):
    revs = hr.load_reviews(_write(tmp_path, [_rev("S1", "justified", "J2", approval="proposed")]))
    rep = hr.apply_v2(_score([_row("S1", "unnecessary_hold")]), revs)
    assert rep["rows"][0]["bucket_v2"] == "hold_review_pending"
    assert rep["scopes"][0]["판단_적절성"]["정당한_검토_보류"] == 0


def test_value_source_accuracy_is_independent_of_confirmation(tmp_path):
    rows = [_row("S1", "correct_answer"), _row("S2", "unnecessary_hold"),
            _row("S3", "unnecessary_hold", vm=False, sm=False), _row("S4", "correct_hold", label="hold")]
    revs = hr.load_reviews(_write(tmp_path, [_rev("S2", "justified", "J1"), _rev("S3", "unjustified", "U")]))
    sc = hr.apply_v2(_score(rows), revs)["scopes"][0]
    assert (sc["A1_값근거_정확률"]["numerator"], sc["A1_값근거_정확률"]["denominator"]) == (2, 3)
    assert (sc["M1_확정정답률"]["numerator"], sc["M1_확정정답률"]["denominator"]) == (1, 3)
    j = sc["판단_적절성"]
    assert (j["정당한_검토_보류"], j["불필요한_보류"], j["판단_대기"]) == (1, 1, 0)


def test_value_match_none_is_not_counted_accurate(tmp_path):
    rep = hr.apply_v2(_score([_row("S1", "unnecessary_hold", vm=None, sm=True)]),
                      hr.load_reviews(_write(tmp_path, [])))
    assert rep["scopes"][0]["A1_값근거_정확률"]["numerator"] == 0


@pytest.mark.parametrize("bad", [
    {"verdict": "ok"}, {"criterion": "U"}, {"approval": "maybe"}, {"assessment": ""},
    {"approved_by": ""},
])
def test_bad_review_rows_are_rejected(tmp_path, bad):
    row = _rev("S1", "justified", "J1") | bad
    with pytest.raises(hr.ReviewError):
        hr.load_reviews(_write(tmp_path, [row]))


def test_duplicate_review_rejected(tmp_path):
    with pytest.raises(hr.ReviewError):
        hr.load_reviews(_write(tmp_path, [_rev("S1", "justified", "J1"), _rev("S1", "unjustified", "U")]))


def test_input_score_is_not_mutated(tmp_path):
    sc = _score([_row("S1", "unnecessary_hold")])
    before = json.dumps(sc, sort_keys=True)
    hr.apply_v2(sc, hr.load_reviews(_write(tmp_path, [_rev("S1", "justified", "J1")])))
    assert json.dumps(sc, sort_keys=True) == before


def test_reviews_for_other_runs_do_not_apply(tmp_path):
    other = _rev("S1", "justified", "J1") | {"run_id": "R2"}
    rep = hr.apply_v2(_score([_row("S1", "unnecessary_hold")]), hr.load_reviews(_write(tmp_path, [other])))
    assert rep["rows"][0]["bucket_v2"] == "hold_review_pending"


def test_aggregate_scope_only_with_two_stages(tmp_path):
    rows = [_row("S1", "correct_answer"), _row("S1", "correct_answer", stage="followup")]
    rep = hr.apply_v2(_score(rows, stages=("initial", "followup")), hr.load_reviews(_write(tmp_path, [])))
    assert [s["aggregated"] for s in rep["scopes"]] == [False, False, True]
    rep1 = hr.apply_v2(_score(rows[:1]), hr.load_reviews(_write(tmp_path, [])))
    assert [s["aggregated"] for s in rep1["scopes"]] == [False]


def test_pending_ratio_and_explanation_error_reported(tmp_path):
    rows = [_row("S1", "unnecessary_hold"), _row("S2", "unnecessary_hold"), _row("S3", "correct_answer")]
    r1 = _rev("S1", "justified", "J2") | {"explanation_error": "총량 문항에 '분모 미확인'"}
    p = tmp_path / "rev.csv"
    with open(p, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=hr.REVIEW_COLUMNS + hr.OPTIONAL_COLUMNS)
        w.writeheader(); w.writerow(r1)
    sc = hr.apply_v2(_score(rows), hr.load_reviews(p))["scopes"][0]
    assert sc["보류_검토대상"] == 2
    assert (sc["판단_대기_비율"]["numerator"], sc["판단_대기_비율"]["denominator"]) == (1, 2)
    assert sc["설명_오류"] == ["initial/S1: 총량 문항에 '분모 미확인'"]
    assert sc["판단_적절성"]["정당한_검토_보류"] == 1  # 설명 오류는 판정을 바꾸지 않는다


def test_pending_ratio_not_computed_without_review_rows(tmp_path):
    sc = hr.apply_v2(_score([_row("S1", "correct_answer")]), hr.load_reviews(_write(tmp_path, [])))["scopes"][0]
    assert sc["판단_대기_비율"]["value"] is None


def test_no_aggregate_when_stages_have_different_run_ids(tmp_path):
    rows = [_row("S1", "correct_answer"), _row("S1", "correct_answer", stage="followup")]
    sc = {"rows": rows, "label_file": "l", "bucket_counts": {},
          "documents": [{"stage": "initial", "system": "ctl", "run_id": "R_i"},
                        {"stage": "followup", "system": "ctl", "run_id": "R_f"}]}
    rep = hr.apply_v2(sc, hr.load_reviews(_write(tmp_path, [])))
    assert [(s["run_id"], s["stage"], s["aggregated"]) for s in rep["scopes"]] == [
        ("R_f", "followup", False), ("R_i", "initial", False)]
