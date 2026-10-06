#!/usr/bin/env python
"""사람 작업시간 기록(worklog) 집계 CLI (작업지시서 A §3.2).

사람이 적은 CSV만 읽는다. **이 스크립트는 시간을 만들어 내지 않는다.**

    python scripts/aggregate_worktime.py data/eval/worklog/*.csv --out /tmp/worktime.json

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


def _fmt(seconds: float | None) -> str:
    if seconds is None:
        return "산출 불가"
    sign = "-" if seconds < 0 else ""
    total = int(abs(round(seconds)))
    return f"{sign}{total // 3600:d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="사람 작업시간 기록 집계 (§3.2)")
    ap.add_argument("worklog", nargs="+", type=Path, help="worklog CSV 경로")
    ap.add_argument("--out", type=Path, default=None, help="집계 JSON 저장 경로")
    ap.add_argument("--read-run-dirs", action="store_true",
                    help="run_dir의 timings.json에서 기계 시간을 읽어 **비교용으로** 함께 적는다")
    args = ap.parse_args(argv)

    from esgenie.eval.worktime import aggregate_files

    report = aggregate_files(args.worklog, read_run_dirs=args.read_run_dirs)

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
              f"{_fmt(s['wait_seconds']):>10}{_fmt(s['idle_seconds']):>10}  {len(s['qids'])}")
        for issue in s["issues"]:
            print(f"    ! {issue['issue']} (step={issue['step']} qid={issue['qid']})")
        for warning in s["warnings"]:
            print(f"    ⚠ {warning}")
        if s.get("machine_total_seconds") is not None:
            print(f"    기계 시간 {_fmt(s['machine_total_seconds'])} — 비교용이다. "
                  f"사람작업·대기에 더하지 않는다")
    print(report["double_counting_note"])
    print(report["savings_note"])

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                            encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
