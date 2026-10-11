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

#: 일괄 구간 표시. `qid`가 이 값이거나 이 말로 끝나면 여러 문항을 한 구간에 적은 것이다.
#: 일괄 구간의 시간은 **문항별로 나누지 않는다**(2026-10-11 T3). 대상 문항 목록은
#: 답안 파일이나 `;`로 나열한 qid 같은 **추적 가능한 입력**에서만 읽는다.
BATCH_MARKERS: tuple[str, ...] = ("전체",)
BATCH_SUFFIX = "일괄"
#: 사람이 시각을 기억으로 적었다고 note에 남긴 행. note 문구에 이 말이 있으면 표시한다.
MEMORY_MARKER = "기억"

ANSWER_KEY_COLUMNS: tuple[str, ...] = ("session_id", "participant", "method",
                                       "request_round", "qid")


def is_batch_qid(qid: str) -> bool:
    q = (qid or "").strip()
    return q in BATCH_MARKERS or q.endswith(BATCH_SUFFIX)


def split_qids(qid: str) -> list[str]:
    """`A;B`처럼 나열한 qid를 나눈다. 일괄 표시·빈 값은 빈 목록이다."""
    q = (qid or "").strip()
    if not q or is_batch_qid(q):
        return []
    return [part.strip() for part in q.split(";") if part.strip()]


def read_answer_targets(paths: Sequence[Path]) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    """답안 파일(리허설 답안 양식)에서 세션별 실제 대상 qid를 읽는다.

    값을 채웠는지와 무관하게 **행이 있는 qid**를 대상으로 본다(값 없음도 대상이다).
    """
    targets: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for path in paths:
        with Path(path).open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            missing = [c for c in ANSWER_KEY_COLUMNS if c not in (reader.fieldnames or ())]
            if missing:
                raise ValueError(f"답안 파일에 열이 없다 {missing}: {path}")
            for row in reader:
                key = tuple((row.get(c) or "").strip() for c in ANSWER_KEY_COLUMNS[:4])
                qid = (row.get("qid") or "").strip()
                if not qid:
                    continue
                slot = targets.setdefault(key, {"qids": set(), "sources": set()})
                slot["qids"].add(qid)
                slot["sources"].add(str(path))
    return targets


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


def aggregate(entries: Sequence[WorkEntry], *, read_run_dirs: bool = False,
              answer_targets: dict[tuple[str, str, str, str], dict[str, Any]] | None = None,
              ) -> dict[str, Any]:
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
            "_listed": set(), "_batch_rows": 0, "_marker_rows": 0, "_by_step": {}, "_by_qid": {},
            "_reuse": [], "_memory_rows": 0, "_missing_time_rows": 0, "_unfinished_rows": 0,
        })
        bucket["rows"] += 1
        raw_qid = (entry.row.get("qid") or "").strip()
        if raw_qid:
            bucket["qids"].add(raw_qid)
        listed = split_qids(raw_qid)
        bucket["_listed"].update(listed)
        if is_batch_qid(raw_qid) or len(listed) > 1:
            bucket["_batch_rows"] += 1
        if is_batch_qid(raw_qid):
            bucket["_marker_rows"] += 1     # 목록 없이 '전체'·'…일괄'로만 적은 행
        reused = (entry.row.get("reused_from") or "").strip()
        if reused:
            bucket["_reuse"].append((reused, raw_qid))
        if MEMORY_MARKER in (entry.row.get("note") or ""):
            bucket["_memory_rows"] += 1
        if any(i.startswith("missing_start") or i.startswith("missing_end") for i in entry.issues):
            bucket["_missing_time_rows"] += 1
        if entry.start is not None and "missing_end" in entry.issues:
            bucket["_unfinished_rows"] += 1     # 시작은 적었고 끝을 적지 않았다
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
        bucket["_by_step"].setdefault(entry.step, []).append(span)
        if not entry.is_wait and len(listed) == 1:
            # 문항 하나에 대한 관측 구간만 문항별 시간에 쓴다. 일괄 구간은 나누지 않는다.
            bucket["_by_qid"].setdefault(listed[0], []).append(span)

    out: list[dict[str, Any]] = []
    for bucket in sessions.values():
        human_raw, wait_raw = bucket.pop("_human"), bucket.pop("_wait")
        _finish_extras(bucket, answer_targets or {})
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
        if read_run_dirs:
            bucket["machine_status"] = ("관측" if machine else
                                        "미측정 — run_dir에 timings.json이 없다(0초로 적지 않는다)"
                                        if bucket["run_dirs"] else "미측정 — run_dir 기록 없음")
        if bucket["idle_seconds"] < -1.0:
            warnings.append("negative_idle: 구간 합이 경과시간을 넘는다 — 기록을 확인한다")
        bucket["warnings"] = warnings
        out.append(bucket)

    out.sort(key=lambda b: (b["session_id"], b["participant"], b["method"], b["request_round"]))
    return {
        "sessions": out,
        "by_method_round": _by_method_round(out),
        "rows": len(entries),
        "countable_rows": sum(1 for e in entries if e.countable),
        "rows_with_issues": sum(1 for e in entries if e.issues),
        "relation": "elapsed_seconds = human_seconds + wait_seconds + idle_seconds",
        "double_counting_note":
            "파이프라인 기계 시간(run_timing/timings.json)은 사람이 적은 `처리대기` 구간 "
            "안에서 흐른다. machine_* 값은 비교용으로만 적고 human/wait에 더하지 않는다.",
        "savings_note":
            "절감률·단일 점수는 계산하지 않는다. manual/esgenie는 나란히 보고한다.",
        "completeness_note":
            "유효 구간의 합은 관측된 구간의 합이다. 시각 누락·기억 기반·미완결 행이 있으면 "
            "완전한 전체 작업시간으로 읽지 않는다.",
        "batch_note":
            "일괄 구간(qid가 '전체'이거나 '…일괄')의 시간은 문항별로 나누지 않는다. "
            "대상 문항 수는 답안 파일이나 ';'로 나열한 qid에서만 읽고, 없으면 미확인이다.",
    }


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _finish_extras(bucket: dict[str, Any],
                   answer_targets: dict[tuple[str, str, str, str], dict[str, Any]]) -> None:
    """대상 문항·단계별 합계·문항별 중앙값·재사용·기록 완전성 (2026-10-11 T3)."""
    key = (bucket["session_id"], bucket["participant"], bucket["method"], bucket["request_round"])
    listed: set[str] = bucket.pop("_listed")
    batch_rows: int = bucket.pop("_batch_rows")
    marker_rows: int = bucket.pop("_marker_rows")
    from_answers = answer_targets.get(key)

    target = set(listed)
    sources: list[str] = []
    if listed:
        sources.append("worklog qid")
    if from_answers:
        target |= set(from_answers["qids"])
        sources.extend(sorted(from_answers["sources"]))
    if marker_rows and not from_answers:
        bucket["target_qids"] = sorted(target)
        bucket["target_qid_count"] = None
        bucket["target_status"] = ("미확인 — 일괄 구간이 있으나 대상 문항 목록(답안 파일·"
                                   "';' 나열)이 없다")
    else:
        bucket["target_qids"] = sorted(target)
        bucket["target_qid_count"] = len(target)
        bucket["target_status"] = "확인"
    bucket["target_sources"] = sources
    bucket["observation_unit"] = "일괄 포함" if batch_rows else "문항별"

    # 단계별 — 같은 단계 안의 겹침은 합집합. 단계끼리 겹치면 표시한다.
    by_step: dict[str, list] = bucket.pop("_by_step")
    step_seconds = {step: round(_length(_merge(spans)), 3) for step, spans in by_step.items()}
    merged_steps = [(s, _merge(sp)) for s, sp in by_step.items()]
    overlap = False
    for i, (_, a_spans) in enumerate(merged_steps):
        for _, b_spans in merged_steps[i + 1:]:
            if any(a0 < b1 and b0 < a1 for a0, a1 in a_spans for b0, b1 in b_spans):
                overlap = True
    bucket["step_seconds"] = step_seconds
    bucket["steps_overlap"] = overlap
    if overlap:
        bucket["step_note"] = "단계끼리 겹치는 구간이 있다 — 단계별 합의 단순 합은 사람작업+대기와 다를 수 있다"

    # 문항별 — 문항 하나에 대한 관측 구간만. 일괄 구간은 배분하지 않는다.
    by_qid: dict[str, list] = bucket.pop("_by_qid")
    per_qid = {q: round(_length(_merge(spans)), 3) for q, spans in by_qid.items()}
    unobserved = sorted(set(bucket["target_qids"]) - set(per_qid)) \
        if bucket["target_qid_count"] is not None else None
    bucket["per_qid_human_seconds"] = per_qid
    bucket["per_qid_median"] = {
        "value_seconds": None if not per_qid else round(_median(list(per_qid.values())), 3),
        "valid_n": len(per_qid),
        "unobserved_qids": unobserved,
        "status": "산출" if per_qid else "산출 불가 — 문항별로 관측한 구간이 없다",
    }

    # 재사용 — reused_from의 출처·대상 기준. 같은 (출처, 대상)은 한 번만 센다.
    reuse = bucket.pop("_reuse")
    unique = sorted(set(reuse))
    bucket["reuse"] = {"rows": len(reuse), "unique": len(unique),
                       "duplicates": len(reuse) - len(unique),
                       "items": [{"reused_from": r, "qid": q} for r, q in unique],
                       "rule": "reused_from가 적힌 행만 센다. 방식을 두 번째로 수행한 것만으로 "
                               "재사용으로 보지 않는다"}

    # 기록 완전성
    memory_rows = bucket.pop("_memory_rows")
    missing_rows = bucket.pop("_missing_time_rows")
    open_rows = bucket.pop("_unfinished_rows")
    bucket["completeness"] = {
        "missing_time_rows": missing_rows,
        "memory_based_rows": memory_rows,
        "unfinished_rows": open_rows,
        "complete": not (missing_rows or memory_rows or open_rows or bucket["issues"]),
    }


def _by_method_round(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """방식·회차별 요약. 서로 다른 참가자의 시간을 하나의 벽시계 경과로 합치지 않는다."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for s in sessions:
        groups.setdefault((s["method"], s["request_round"]), []).append(s)
    out = []
    for (method, rnd), items in sorted(groups.items()):
        unknown = any(s["target_qid_count"] is None for s in items)
        targets = sorted(set().union(*(set(s["target_qids"]) for s in items)))
        out.append({
            "method": method, "request_round": rnd,
            "participants": sorted({s["participant"] for s in items}),
            "participant_count": len({s["participant"] for s in items}),
            "target_qid_count": None if unknown else len(targets),
            "target_qids": targets,
            "per_participant": [{k: s[k] for k in ("session_id", "participant", "elapsed_seconds",
                                                   "human_seconds", "wait_seconds", "idle_seconds")}
                                for s in items],
            "note": "참가자별로 따로 적는다. 서로 다른 참가자의 경과시간을 합치지 않는다",
        })
    return out


def aggregate_files(paths: Sequence[Path], *, read_run_dirs: bool = False,
                    answer_paths: Sequence[Path] = ()) -> dict[str, Any]:
    entries: list[WorkEntry] = []
    for path in paths:
        entries.extend(read_worklog(Path(path)))
    targets = read_answer_targets([Path(p) for p in answer_paths]) if answer_paths else {}
    report = aggregate(entries, read_run_dirs=read_run_dirs, answer_targets=targets)
    report["sources"] = [str(p) for p in paths]
    report["answer_sources"] = [str(p) for p in answer_paths]
    return report
