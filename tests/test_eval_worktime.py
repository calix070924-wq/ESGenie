"""사람 작업시간 집계 테스트 (작업지시서 A §3.3 ③).

§3.3이 요구한 세 가지 경우(겹치는 구간 / 빈 `end` / 날짜 넘는 구간)와, 지시서가 특히
금지한 **중복 합산**을 확인한다.

여기 나오는 시각은 모두 **이 테스트가 만든 합성 값**이다. 사람 리허설 기록이 아니다.
"""

from __future__ import annotations

import json

import pytest

from esgenie.eval.worktime import (
    WORKLOG_COLUMNS, aggregate, aggregate_files, machine_seconds, read_worklog,
)

HEADER = ",".join(WORKLOG_COLUMNS)


def _csv(tmp_path, *rows: str, name: str = "worklog.csv"):
    path = tmp_path / name
    path.write_text("\n".join((HEADER, *rows)) + "\n", encoding="utf-8")
    return path


def _row(step, start, end, *, session="S1", participant="P1", method="manual",
         rnd="1", qid="RBA-C-1-E-1-1", reused="", run_dir="", note=""):
    return f"{session},{participant},{method},{rnd},{qid},{step},{start},{end},{reused},{run_dir},{note}"


def _only(path, **kwargs):
    report = aggregate_files([path], **kwargs)
    assert len(report["sessions"]) == 1, report["sessions"]
    return report["sessions"][0], report


# --- §3.3 ③ 겹치는 구간 ---------------------------------------------------------

def test_overlapping_human_spans_are_unioned_not_summed(tmp_path):
    """같은 시간을 두 행에 적었으면 두 번 세지 않는다."""
    path = _csv(
        tmp_path,
        _row("원문확인", "2026-10-20T09:00:00", "2026-10-20T09:30:00"),
        _row("값입력", "2026-10-20T09:20:00", "2026-10-20T09:40:00"),
    )
    session, _ = _only(path)
    # 단순 합은 30+20=50분이지만 합집합은 09:00~09:40 = 40분이다.
    assert session["human_seconds"] == 40 * 60
    assert session["overlap_removed_seconds"] == 10 * 60
    assert session["elapsed_seconds"] == 40 * 60
    assert session["idle_seconds"] == 0


def test_wait_overlapping_human_work_is_not_counted_twice(tmp_path):
    """기다리는 동안 다른 문항을 입력했으면 그 시간은 사람 작업시간으로만 센다."""
    path = _csv(
        tmp_path,
        _row("처리대기", "2026-10-20T10:00:00", "2026-10-20T10:10:00", run_dir="run/a"),
        _row("값입력", "2026-10-20T10:02:00", "2026-10-20T10:06:00", qid="RBA-C-1-E-1-2"),
    )
    session, _ = _only(path)
    assert session["human_seconds"] == 4 * 60
    assert session["wait_seconds"] == 6 * 60          # 10분 중 겹친 4분을 뺐다
    assert session["elapsed_seconds"] == 10 * 60
    assert session["human_seconds"] + session["wait_seconds"] + session["idle_seconds"] \
        == session["elapsed_seconds"]


# --- §3.3 ③ 빈 end -------------------------------------------------------------

def test_missing_end_is_reported_not_guessed(tmp_path):
    """`end`가 비면 길이를 추정하지 않는다. 문제 행으로 적고 합산에서 뺀다."""
    path = _csv(
        tmp_path,
        _row("원문확인", "2026-10-20T09:00:00", "2026-10-20T09:10:00"),
        _row("값입력", "2026-10-20T09:10:00", ""),
    )
    session, report = _only(path)
    assert session["human_seconds"] == 10 * 60
    assert session["countable_rows"] == 1
    assert report["rows_with_issues"] == 1
    assert [i["issue"] for i in session["issues"]] == ["missing_end"]


def test_session_with_no_countable_rows_is_none_not_zero(tmp_path):
    """산출 불가를 0으로 적지 않는다."""
    path = _csv(tmp_path, _row("값입력", "2026-10-20T09:00:00", ""))
    session, _ = _only(path)
    assert session["human_seconds"] is None
    assert session["elapsed_seconds"] is None
    assert session["wait_seconds"] is None


def test_reused_row_without_times_is_marked_reused(tmp_path):
    """`reused_from`이 있으면 시각이 없는 것이 정상이고, 합산에는 넣지 않는다."""
    path = _csv(tmp_path, _row("보완자료정리", "", "", reused="S0"))
    session, _ = _only(path)
    assert set(i["issue"] for i in session["issues"]) == {"reused_reference"}
    assert session["countable_rows"] == 0


# --- §3.3 ③ 날짜 넘는 구간 ------------------------------------------------------

def test_span_crossing_midnight_is_positive(tmp_path):
    path = _csv(tmp_path, _row("출력", "2026-10-20T23:40:00", "2026-10-21T00:20:00"))
    session, _ = _only(path)
    assert session["human_seconds"] == 40 * 60


def test_end_before_start_is_an_error_not_silently_shifted_a_day(tmp_path):
    """음수 구간에 하루를 더해 살리지 않는다 — 사람이 고쳐야 한다."""
    path = _csv(tmp_path, _row("출력", "2026-10-20T23:40:00", "2026-10-20T00:20:00"))
    session, _ = _only(path)
    assert [i["issue"] for i in session["issues"]] == ["end_before_start"]
    assert session["human_seconds"] is None


def test_time_without_date_is_rejected(tmp_path):
    path = _csv(tmp_path, _row("출력", "23:40", "00:20"))
    session, _ = _only(path)
    assert all(i["issue"].startswith("bad_") for i in session["issues"])


# --- 분리·중복 합산 금지 --------------------------------------------------------

def test_sessions_are_split_by_participant_method_and_round(tmp_path):
    path = _csv(
        tmp_path,
        _row("값입력", "2026-10-20T09:00:00", "2026-10-20T09:10:00", method="manual", rnd="1"),
        _row("값입력", "2026-10-20T10:00:00", "2026-10-20T10:02:00", method="esgenie", rnd="1"),
        _row("값입력", "2026-10-20T11:00:00", "2026-10-20T11:03:00", method="esgenie", rnd="2"),
        _row("값입력", "2026-10-20T12:00:00", "2026-10-20T12:05:00", participant="P2"),
    )
    report = aggregate_files([path])
    assert len(report["sessions"]) == 4
    # 방식별 수치를 나란히 적기만 한다 — 절감률 필드는 없다.
    assert "savings_pct" not in json.dumps(report)
    assert "절감" in report["savings_note"]


def test_machine_time_is_reported_separately_and_never_added(tmp_path):
    run_dir = tmp_path / "run" / "initial"
    run_dir.mkdir(parents=True)
    (run_dir / "timings.json").write_text(json.dumps({
        "cache_profile": {"mode": "upstage_replay"},
        "stages": [{"stage": "L1_extract", "seconds": 120.0},
                   {"stage": "_run_total", "seconds": 500.0, "staged_sum": 480.0}],
    }), encoding="utf-8")
    path = _csv(
        tmp_path,
        _row("처리대기", "2026-10-20T10:00:00", "2026-10-20T10:06:00", run_dir=str(run_dir)),
        _row("값입력", "2026-10-20T10:06:00", "2026-10-20T10:16:00"),
    )
    session, report = _only(path, read_run_dirs=True)
    assert session["human_seconds"] == 10 * 60
    assert session["wait_seconds"] == 6 * 60
    assert session["machine_total_seconds"] == 500.0
    # 기계 시간은 사람 시간 어디에도 섞이지 않는다.
    assert session["human_seconds"] + session["wait_seconds"] + session["idle_seconds"] \
        == session["elapsed_seconds"] == 16 * 60
    assert "더하지 않는다" in report["double_counting_note"]
    # 기계 시간이 사람이 적은 대기보다 길면 경고로 알린다(합산으로 메우지 않는다).
    assert any(w.startswith("pipeline_exceeds_wait") for w in session["warnings"])


def test_machine_seconds_returns_none_when_timings_missing(tmp_path):
    assert machine_seconds(tmp_path) is None


# --- 양식 검사 ------------------------------------------------------------------

def test_unknown_step_and_method_are_flagged(tmp_path):
    path = _csv(
        tmp_path,
        _row("커피", "2026-10-20T09:00:00", "2026-10-20T09:10:00"),
        _row("값입력", "2026-10-20T09:10:00", "2026-10-20T09:20:00", method="수동"),
        _row("값입력", "2026-10-20T09:20:00", "2026-10-20T09:30:00", rnd="3"),
    )
    report = aggregate_files([path])
    issues = [i["issue"] for s in report["sessions"] for i in s["issues"]]
    assert any(i.startswith("unknown_step:커피") for i in issues)
    assert any(i.startswith("unknown_method:수동") for i in issues)
    assert any(i.startswith("unknown_request_round:3") for i in issues)


def test_header_mismatch_raises(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("session_id,participant\nS1,P1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="worklog 열이 양식과 다르다"):
        read_worklog(path)


def test_template_header_matches_contract():
    from pathlib import Path
    template = Path(__file__).resolve().parents[1] / "data" / "eval" / "worklog_template.csv"
    assert template.read_text(encoding="utf-8").strip() == HEADER


def test_empty_entry_list_aggregates_to_nothing():
    report = aggregate([])
    assert report["sessions"] == [] and report["rows"] == 0


# --- 2026-10-11 T2·T3: CLI import, 대상 문항 수와 지시서 집계 항목 ------------------
# 시각은 모두 이 테스트가 만든 합성 값이다.

import os
import subprocess
import sys
from pathlib import Path

ANSWER_HEADER = ("session_id,participant,method,request_round,qid,measure,unit,value,"
                 "evidence_doc,evidence_locator,needs_more_data,note")


def _answers(tmp_path, *qids, method="esgenie", rnd="2", session="S1", participant="P1"):
    path = tmp_path / "answers.csv"
    rows = [f"{session},{participant},{method},{rnd},{q},합성,,,,,," for q in qids]
    path.write_text("\n".join((ANSWER_HEADER, *rows)) + "\n", encoding="utf-8")
    return path


def test_batch_row_is_one_row_but_targets_come_from_answer_file(tmp_path):
    wl = _csv(tmp_path, _row("값입력", "2026-10-10T09:00:00", "2026-10-10T09:10:00",
                             method="esgenie", rnd="2", qid="4문항일괄"))
    ans = _answers(tmp_path, "SYN-A", "SYN-B", "SYN-C", "SYN-D")
    s, _ = _only(wl, answer_paths=[ans])
    assert s["target_qid_count"] == 4
    assert s["observation_unit"] == "일괄 포함"
    # 일괄 구간을 4로 나눠 문항별 시간을 만들지 않는다
    assert s["per_qid_human_seconds"] == {}
    assert s["per_qid_median"]["value_seconds"] is None
    assert s["per_qid_median"]["unobserved_qids"] == ["SYN-A", "SYN-B", "SYN-C", "SYN-D"]


def test_batch_row_without_target_list_is_unconfirmed_not_one(tmp_path):
    wl = _csv(tmp_path, _row("값입력", "2026-10-10T09:00:00", "2026-10-10T09:10:00",
                             method="esgenie", rnd="2", qid="전체"))
    s, report = _only(wl)
    assert s["target_qid_count"] is None
    assert s["target_status"].startswith("미확인")
    assert report["by_method_round"][0]["target_qid_count"] is None


def test_semicolon_listed_qids_count_as_targets(tmp_path):
    wl = _csv(tmp_path, _row("값입력", "2026-10-10T09:00:00", "2026-10-10T09:10:00",
                             qid="SYN-A;SYN-B"))
    s, _ = _only(wl)
    assert s["target_qid_count"] == 2
    assert s["per_qid_human_seconds"] == {}       # 두 문항 구간도 나누지 않는다


def test_per_qid_median_uses_only_single_question_spans(tmp_path):
    wl = _csv(tmp_path,
              _row("원문확인", "2026-10-09T09:00:00", "2026-10-09T09:10:00", qid="SYN-A"),
              _row("값입력", "2026-10-09T09:10:00", "2026-10-09T09:20:00", qid="SYN-A"),
              _row("원문확인", "2026-10-09T09:20:00", "2026-10-09T09:25:00", qid="SYN-B"),
              _row("원문확인", "2026-10-09T09:25:00", "2026-10-09T09:55:00", qid="SYN-C"))
    s, _ = _only(wl)
    assert s["per_qid_human_seconds"] == {"SYN-A": 1200.0, "SYN-B": 300.0, "SYN-C": 1800.0}
    assert s["per_qid_median"]["value_seconds"] == 1200.0
    assert s["per_qid_median"]["valid_n"] == 3


def test_step_totals_and_cross_step_overlap_flag(tmp_path):
    wl = _csv(tmp_path,
              _row("원문확인", "2026-10-09T09:00:00", "2026-10-09T09:30:00"),
              _row("값입력", "2026-10-09T09:20:00", "2026-10-09T09:40:00"))
    s, _ = _only(wl)
    assert s["step_seconds"] == {"원문확인": 1800.0, "값입력": 1200.0}
    assert s["steps_overlap"] is True
    assert s["human_seconds"] == 2400.0              # 합집합 — 단계 합(3000)과 다르다


def test_reuse_counts_unique_source_target_pairs(tmp_path):
    wl = _csv(tmp_path,
              _row("값입력", "", "", rnd="2", qid="SYN-A", reused="S0:SYN-A"),
              _row("값입력", "", "", rnd="2", qid="SYN-A", reused="S0:SYN-A"),
              _row("값입력", "", "", rnd="2", qid="SYN-B", reused="S0:SYN-B"),
              _row("원문확인", "2026-10-09T09:00:00", "2026-10-09T09:05:00", rnd="2", qid="SYN-C"))
    s, _ = _only(wl)
    assert s["reuse"]["rows"] == 3 and s["reuse"]["unique"] == 2 and s["reuse"]["duplicates"] == 1


def test_second_round_alone_is_not_reuse(tmp_path):
    wl = _csv(tmp_path, _row("값입력", "2026-10-09T09:00:00", "2026-10-09T09:05:00", rnd="2"))
    s, _ = _only(wl)
    assert s["reuse"]["rows"] == 0


def test_completeness_flags_missing_memory_and_unfinished(tmp_path):
    wl = _csv(tmp_path,
              _row("원문확인", "2026-10-09T09:00:00", "2026-10-09T09:05:00", note="start는 기억 기반"),
              _row("값입력", "2026-10-09T09:05:00", ""),
              _row("수정", "", ""))
    s, _ = _only(wl)
    c = s["completeness"]
    assert c["memory_based_rows"] == 1
    assert c["unfinished_rows"] == 1                 # 시작만 있는 행
    assert c["missing_time_rows"] == 2               # 끝 없음 1 + 둘 다 없음 1
    assert c["complete"] is False


def test_participants_are_not_merged_into_one_wall_clock(tmp_path):
    wl = _csv(tmp_path,
              _row("원문확인", "2026-10-09T09:00:00", "2026-10-09T09:10:00", participant="P1"),
              _row("원문확인", "2026-10-09T13:00:00", "2026-10-09T13:10:00", participant="P2"))
    report = aggregate_files([wl])
    g = report["by_method_round"][0]
    assert g["participant_count"] == 2
    assert "elapsed_seconds" not in g                 # 합친 벽시계 경과를 만들지 않는다
    assert sorted(p["elapsed_seconds"] for p in g["per_participant"]) == [600.0, 600.0]


def test_machine_time_missing_is_unmeasured_not_zero(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    wl = _csv(tmp_path, _row("처리대기", "2026-10-09T09:00:00", "2026-10-09T09:01:00",
                             method="esgenie", run_dir=str(run_dir)))
    s, _ = _only(wl, read_run_dirs=True)
    assert s["machine_seconds_observed"] is None
    assert s["machine_status"].startswith("미측정")
    assert "machine_total_seconds" not in s


@pytest.mark.parametrize("script, args", [
    ("aggregate_worktime.py", ["{wl}", "--out", "{out}"]),
    ("verify_timing_cache_modes.py", ["--help"]),
    ("verify_timing_ocr_live.py", ["--help"]),
    ("verify_timing_noop.py", ["compare", "--help"]),
])
def test_cli_runs_without_pythonpath_from_another_directory(tmp_path, script, args):
    """문서의 명령이 PYTHONPATH 없이, 저장소 밖 작업 디렉터리에서 그대로 돈다."""
    repo = Path(__file__).resolve().parents[1]
    wl = _csv(tmp_path, _row("원문확인", "2026-10-09T09:00:00", "2026-10-09T09:05:00"))
    out = tmp_path / "out.json"
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    proc = subprocess.run(
        [sys.executable, str(repo / "scripts" / script),
         *[a.format(wl=wl, out=out) for a in args]],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "ModuleNotFoundError" not in proc.stderr
    if script == "aggregate_worktime.py":
        assert json.loads(out.read_text(encoding="utf-8"))["countable_rows"] == 1
