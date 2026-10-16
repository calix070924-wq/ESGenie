#!/usr/bin/env python
"""사람 작업시간 기록(worklog) 집계 CLI (작업지시서 A §3.2).

사람이 적은 CSV만 읽는다. **이 스크립트는 시간을 만들어 내지 않는다.**

    python scripts/aggregate_worktime.py data/eval/worklog/*.csv --out /tmp/worktime.json
    # 일괄 구간의 대상 문항 수는 답안 파일에서 읽는다
    python scripts/aggregate_worktime.py data/eval/worklog/세션_리허설02_2026-10-09.csv \
        --answers data/eval/rehearsal_answers/세션_리허설02_2026-10-09.csv --out /tmp/r02.json
    # 자료 구분·세트·조건과 세션 전체 대상 목록은 세션 메타데이터로 연결한다(T3-2·T3-3)
    python scripts/aggregate_worktime.py <worklog.csv> --sessions <세션메타.csv> --out <결과.json>

입력을 믿을 수 없으면(세션 메타데이터 충돌·값 오류, worklog 열 불일치) 종료 코드 2로 멈추고
결과 파일을 쓰지 않는다.

저장소 루트를 import 경로에 스스로 넣는다. `PYTHONPATH`나 다른 checkout의 설치에
기대지 않는다(2026-10-11 T2).

출력에서 꼭 같이 읽을 것:

    elapsed_seconds = human_seconds + wait_seconds + idle_seconds

`machine_*`(파이프라인 기계 시간)은 **비교용**이다. 사람이 적은 `처리대기` 구간 안에서
흐른 시간이므로 `human`/`wait`에 더하면 같은 시간을 두 번 센다.

절감률은 계산하지 않는다. `manual`/`esgenie`를 나란히 적는 것까지만 한다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _fmt(seconds: float | None) -> str:
    if seconds is None:
        return "산출 불가"
    sign = "-" if seconds < 0 else ""
    total = int(abs(round(seconds)))
    return f"{sign}{total // 3600:d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def _json_default(value):
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    return str(value)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="사람 작업시간 기록 집계 (§3.2)")
    ap.add_argument("worklog", nargs="+", type=Path, help="worklog CSV 경로")
    ap.add_argument("--out", type=Path, default=None, help="집계 JSON 저장 경로")
    ap.add_argument("--answers", nargs="*", type=Path, default=[],
                    help="답안 파일(리허설 답안 양식). 일괄 구간의 실제 대상 qid를 여기서 읽는다")
    ap.add_argument("--sessions", nargs="*", type=Path, default=[],
                    help="세션 메타데이터 CSV(session_id,data_kind,set_id,set_version,condition,"
                         "target_qids). 자료 구분·세트·조건이 다른 세션을 같은 요약에 섞지 않는다")
    ap.add_argument("--read-run-dirs", action="store_true",
                    help="run_dir의 timings.json에서 기계 시간을 읽어 **비교용으로** 함께 적는다")
    args = ap.parse_args(argv)

    from esgenie.eval.worktime import aggregate_files

    try:
        report = aggregate_files(args.worklog, read_run_dirs=args.read_run_dirs,
                                 answer_paths=args.answers, session_meta_paths=args.sessions)
    except (ValueError, OSError) as exc:
        # SessionMetaError도 ValueError다. 결과 파일을 쓰지 않는다.
        print(f"집계 중단: {exc}", file=sys.stderr)
        return 2

    print(f"행 {report['rows']}건 (합산 가능 {report['countable_rows']}건, "
          f"문제 있는 행 {report['rows_with_issues']}건)")
    print(f"관계: {report['relation']}")
    header = f"{'session':<22}{'참가자':<10}{'방식':<9}{'회차':<5}" \
             f"{'경과':>10}{'사람작업':>11}{'대기':>10}{'빈시간':>10}  문항"
    print(header)
    print("-" * len(header))
    for s in report["sessions"]:
        print(f"{s['session_id']:<22}{s['participant']:<10}{s['method']:<9}{s['request_round']:<5}"
              f"{_fmt(s['elapsed_seconds']):>10}{_fmt(s['human_seconds']):>11}"
              f"{_fmt(s['wait_seconds']):>10}{_fmt(s['idle_seconds']):>10}  "
              f"{s['target_qid_count'] if s['target_qid_count'] is not None else '미확인'}")
        if s["target_qid_count"] is None:
            print(f"    대상 문항: {s['target_status']}")
        c = s["completeness"]
        if not c["complete"]:
            print(f"    기록 불완전: 시각 누락 {c['missing_time_rows']} · 기억 기반 "
                  f"{c['memory_based_rows']} · 미완결 {c['unfinished_rows']}")
        for issue in s["issues"]:
            print(f"    ! {issue['issue']} (step={issue['step']} qid={issue['qid']})")
        for warning in s["warnings"]:
            print(f"    ⚠ {warning}")
        if "machine_status" in s:
            if s.get("machine_total_seconds") is not None:
                print(f"    기계 시간 {_fmt(s['machine_total_seconds'])} ({s['machine_status']}) — "
                      f"비교용이다. 사람작업·대기에 더하지 않는다")
            else:
                print(f"    기계 시간: {s['machine_status']}")
                part = s.get("machine_partial") or {}
                if part.get("observed_runs"):
                    print(f"      관측분 {_fmt(part['observed_seconds'])} · 실행 "
                          f"{part['observed_runs']}/{part['total_runs']} — 전체 기계 시간이 아니다")
    for g in report["by_method_round"]:
        ident = (f"{g['data_kind']}·{g['set_id']}@{g['set_version']}·{g['condition']}"
                 if g["data_kind"] else f"자료 미확인·세션 {', '.join(g['session_ids'])}")
        print(f"[{ident} / {g['method']} / 회차 {g['request_round']}] 참가자 {g['participant_count']}명 · "
              f"대상 문항 {g['target_qid_count'] if g['target_qid_count'] is not None else '미확인'}")
    print(report["double_counting_note"])
    print(report["savings_note"])

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        tmp = args.out.with_name(args.out.name + ".tmp")
        tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=_json_default),
                       encoding="utf-8")
        tmp.replace(args.out)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
