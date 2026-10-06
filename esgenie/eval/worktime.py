"""사람 작업시간 기록 집계 (작업지시서 A §3.2).

**사람이 적은 기록만 읽는다. 시간을 만들어 내지 않는다.** 이 모듈에는 기본값으로
채우는 소요 시간이 없다. `end`가 비어 있으면 비어 있는 채로 "미완결"로 센다.

핵심은 **중복 합산을 막는 것**이다. 세 가지는 서로 다른 양이다:

    실제 경과시간(elapsed)  = 그 세션의 마지막 end − 첫 start  (벽시계)
    사람 작업시간(human)    = `처리대기`가 아닌 구간들의 **합집합** 길이
    대기시간(wait)          = `처리대기` 구간의 합집합에서 사람 작업 구간을 뺀 길이

    elapsed = human + wait + idle            (idle = 기록되지 않은 빈 시간)

겹친 구간을 그냥 더하면 사람 작업시간이 실제 경과시간보다 커진다. 그래서 합이 아니라
**합집합**을 쓰고, 겹쳐서 덜어낸 양을 `overlap_removed_seconds`로 함께 보고한다.

**파이프라인 시간은 여기 더하지 않는다.** `esgenie/run_timing.py`가 재는 기계 시간은
사람이 `처리대기`로 적은 구간 **안에서** 흐른다. 둘을 더하면 같은 시간을 두 번 센다.
`run_dir`의 `timings.json`을 주면 `machine_seconds_observed`로 **따로** 적고, 대기시간과
비교만 한다(`pipeline_exceeds_wait` 경고).

절감률·단일 점수는 계산하지 않는다. `manual`/`esgenie`를 나란히 적을 뿐이다
(§3.2: "1명 리허설 결과로 절감률을 주장하지 않는다").
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

WORKLOG_COLUMNS: tuple[str, ...] = (
    "session_id", "participant", "method", "request_round", "qid", "step",
    "start", "end", "reused_from", "run_dir", "note",
)
METHODS: tuple[str, ...] = ("manual", "esgenie")
STEPS: tuple[str, ...] = ("원문확인", "값입력", "수정", "보완자료정리", "출력", "처리대기")
#: 사람이 손을 놓고 기계를 기다린 구간. 사람 작업시간에 넣지 않는다.
WAIT_STEPS: tuple[str, ...] = ("처리대기",)
REQUEST_ROUNDS: tuple[str, ...] = ("1", "2")


def parse_time(raw: str) -> datetime:
    """ISO 8601 날짜+시각만 받는다.

    시각만(`14:03`) 적으면 날짜를 넘는 구간을 복원할 수 없고, 자정을 넘긴 기록이
    음수가 되거나 "다음 날이겠지" 하고 **임의로 하루를 더하는** 보정이 필요해진다.
    그 보정은 하지 않는다 — 양식에서 날짜를 요구한다.
    """
    text = (raw or "").strip().replace(" ", "T")
    if not text:
        raise ValueError("빈 시각")
    value = datetime.fromisoformat(text)
    if value.tzinfo is not None:
        value = value.astimezone().replace(tzinfo=None)
    return value


@dataclass
class WorkEntry:
    """기록 한 행. 문제가 있으면 `issues`에 남기고 **버리지 않는다.**"""

    row: dict[str, str]
    start: datetime | None = None
    end: datetime | None = None
    issues: list[str] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.row.get("session_id", ""), self.row.get("participant", ""),
                self.row.get("method", ""), self.row.get("request_round", ""))

    @property
    def step(self) -> str:
        return (self.row.get("step") or "").strip()

    @property
    def is_wait(self) -> bool:
        return self.step in WAIT_STEPS

    @property
    def countable(self) -> bool:
        """시간 합산에 넣을 수 있는 행인가. 문제가 하나라도 있으면 넣지 않는다."""
        return not self.issues and self.start is not None and self.end is not None


def _validate(row: dict[str, str]) -> WorkEntry:
    entry = WorkEntry(row=row)
    method = (row.get("method") or "").strip()
    step = (row.get("step") or "").strip()
    rnd = (row.get("request_round") or "").strip()
    reused = (row.get("reused_from") or "").strip()
    if method not in METHODS:
        entry.issues.append(f"unknown_method:{method or '(빈값)'}")
    if step not in STEPS:
        entry.issues.append(f"unknown_step:{step or '(빈값)'}")
    if rnd not in REQUEST_ROUNDS:
        entry.issues.append(f"unknown_request_round:{rnd or '(빈값)'}")
    if not (row.get("session_id") or "").strip():
        entry.issues.append("missing_session_id")
    if not (row.get("participant") or "").strip():
        entry.issues.append("missing_participant")

    for name in ("start", "end"):
        raw = (row.get(name) or "").strip()
        if not raw:
            # 재사용 표시 행은 시각이 없는 것이 정상이다 — 그래도 합산에는 넣지 않는다.
            entry.issues.append("reused_reference" if reused else f"missing_{name}")
            continue
        try:
            setattr(entry, name, parse_time(raw))
        except ValueError as exc:
            entry.issues.append(f"bad_{name}:{exc}")
    if entry.start and entry.end and entry.end < entry.start:
        # 자정을 넘긴 기록은 날짜가 있으면 저절로 양수다. 음수는 **오기**이므로
        # 하루를 더해 살리지 않는다. 사람이 고쳐야 한다.
        entry.issues.append("end_before_start")
    return entry


def read_worklog(path: Path) -> list[WorkEntry]:
    """CSV를 읽는다. 열이 양식과 다르면 바로 예외를 올린다."""
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        header = tuple(reader.fieldnames or ())
        if header != WORKLOG_COLUMNS:
            raise ValueError(f"worklog 열이 양식과 다르다.\n  기대: {WORKLOG_COLUMNS}\n  실제: {header}")
        return [_validate({k: (v or "") for k, v in row.items()}) for row in reader]


def _merge(spans: Sequence[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """겹치거나 붙은 구간을 합집합으로 만든다."""
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _length(spans: Iterable[tuple[datetime, datetime]]) -> float:
    return sum((end - start).total_seconds() for start, end in spans)


def _subtract(base: Sequence[tuple[datetime, datetime]],
              cut: Sequence[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """`base`에서 `cut`과 겹치는 부분을 덜어낸다."""
    result = list(base)
    for cut_start, cut_end in cut:
        nxt: list[tuple[datetime, datetime]] = []
        for start, end in result:
            if cut_end <= start or cut_start >= end:
                nxt.append((start, end))
                continue
            if start < cut_start:
                nxt.append((start, cut_start))
            if cut_end < end:
                nxt.append((cut_end, end))
        result = nxt
    return result


def machine_seconds(run_dir: str | Path) -> dict[str, Any] | None:
    """`run_dir`의 `timings.json`에서 기계 시간을 읽는다. 없으면 None(0이 아니다)."""
    path = Path(run_dir) / "timings.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    stages = payload.get("stages") or []
    totals = [r for r in stages if str(r.get("stage", "")).startswith("_")]
    return {
        "run_dir": str(run_dir),
        "total_seconds": totals[-1].get("seconds") if totals else None,
        "staged_sum": totals[-1].get("staged_sum") if totals else None,
        "cache_profile": payload.get("cache_profile"),
    }


def aggregate(entries: Sequence[WorkEntry], *, read_run_dirs: bool = False) -> dict[str, Any]:
    """세션·참가자·방식·회차별 집계.

    산출할 수 없는 값은 `None`으로 둔다. **0으로 적지 않는다.**
    """
    sessions: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for entry in entries:
        bucket = sessions.setdefault(entry.key, {
            "session_id": entry.key[0], "participant": entry.key[1],
            "method": entry.key[2], "request_round": entry.key[3],
            "rows": 0, "countable_rows": 0, "qids": set(), "run_dirs": set(),
            "_human": [], "_wait": [], "issues": [],
        })
        bucket["rows"] += 1
        if entry.row.get("qid"):
            bucket["qids"].add(entry.row["qid"])
        if entry.row.get("run_dir"):
            bucket["run_dirs"].add(entry.row["run_dir"])
        for issue in entry.issues:
            bucket["issues"].append({"issue": issue, "step": entry.step,
                                     "qid": entry.row.get("qid", ""),
                                     "start": entry.row.get("start", ""),
                                     "end": entry.row.get("end", "")})
        if not entry.countable:
            continue
        bucket["countable_rows"] += 1
        span = (entry.start, entry.end)
        bucket["_wait" if entry.is_wait else "_human"].append(span)

    out: list[dict[str, Any]] = []
    for bucket in sessions.values():
        human_raw, wait_raw = bucket.pop("_human"), bucket.pop("_wait")
        bucket["qids"] = sorted(bucket["qids"])
        bucket["run_dirs"] = sorted(bucket["run_dirs"])
        if not human_raw and not wait_raw:
            # 합산 가능한 행이 없다 — 0초가 아니라 "산출 불가"다.
            bucket.update({"elapsed_seconds": None, "human_seconds": None,
                           "wait_seconds": None, "idle_seconds": None,
                           "overlap_removed_seconds": None,
                           "machine_seconds_observed": None, "warnings": []})
            out.append(bucket)
            continue
        human = _merge(human_raw)
        wait = _subtract(_merge(wait_raw), human)
        all_spans = human_raw + wait_raw
        elapsed = (max(e for _, e in all_spans) - min(s for s, _ in all_spans)).total_seconds()
        human_s, wait_s = _length(human), _length(wait)
        bucket["elapsed_seconds"] = round(elapsed, 3)
        bucket["human_seconds"] = round(human_s, 3)
        bucket["wait_seconds"] = round(wait_s, 3)
        bucket["idle_seconds"] = round(elapsed - human_s - wait_s, 3)
        bucket["overlap_removed_seconds"] = round(_length(human_raw) + _length(wait_raw) - human_s - wait_s, 3)

        warnings: list[str] = []
        machine = None
        if read_run_dirs:
            found = [m for m in (machine_seconds(d) for d in bucket["run_dirs"]) if m]
            machine = found or None
            total = sum(m["total_seconds"] or 0.0 for m in found) if found else None
            if total is not None:
                bucket["machine_total_seconds"] = round(total, 3)
                if total > wait_s + 1.0:
                    warnings.append(
                        "pipeline_exceeds_wait: 기계 시간이 사람이 적은 처리대기보다 길다. "
                        "기록 누락일 수 있다 — 두 값을 더하지 말고 기록을 확인한다")
        bucket["machine_seconds_observed"] = machine
        if bucket["idle_seconds"] < -1.0:
            warnings.append("negative_idle: 구간 합이 경과시간을 넘는다 — 기록을 확인한다")
        bucket["warnings"] = warnings
        out.append(bucket)

    out.sort(key=lambda b: (b["session_id"], b["participant"], b["method"], b["request_round"]))
    return {
        "sessions": out,
        "rows": len(entries),
        "countable_rows": sum(1 for e in entries if e.countable),
        "rows_with_issues": sum(1 for e in entries if e.issues),
        "relation": "elapsed_seconds = human_seconds + wait_seconds + idle_seconds",
        "double_counting_note":
            "파이프라인 기계 시간(run_timing/timings.json)은 사람이 적은 `처리대기` 구간 "
            "안에서 흐른다. machine_* 값은 비교용으로만 적고 human/wait에 더하지 않는다.",
        "savings_note":
            "절감률·단일 점수는 계산하지 않는다. manual/esgenie는 나란히 보고한다.",
    }


def aggregate_files(paths: Sequence[Path], *, read_run_dirs: bool = False) -> dict[str, Any]:
    entries: list[WorkEntry] = []
    for path in paths:
        entries.extend(read_worklog(Path(path)))
    report = aggregate(entries, read_run_dirs=read_run_dirs)
    report["sources"] = [str(p) for p in paths]
    return report
