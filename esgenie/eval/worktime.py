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
import math
import os
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


def _tokens(qid: str) -> list[str]:
    return [part.strip() for part in (qid or "").split(";") if part.strip()]


def has_batch_token(qid: str) -> bool:
    """`;`로 나눈 항목 중 하나라도 일괄 표시(`전체`·`…일괄`)인가 (2026-10-11 재검토 R2)."""
    return any(is_batch_qid(t) for t in _tokens(qid))


def split_qids(qid: str) -> list[str]:
    """`A;B`처럼 나열한 qid를 나눈다. **항목마다** 일괄 표시를 걸러 낸다 — 일괄 표시는
    문항이 아니다. 빈 값은 빈 목록이다."""
    return [t for t in _tokens(qid) if not is_batch_qid(t)]


def question_tokens(raw: str, where: str) -> list[str]:
    """전체 대상 목록(답안 파일·세션 메타데이터)의 qid 항목을 검증한다.

    한 항목 = 한 문항이다. 일괄 표시가 **어느 위치에든** 섞여 있으면 문항 목록으로 받지 않고
    오류를 낸다(순서 무관: `전체;SYN-1`, `SYN-1;전체`, `SYN-1;4문항일괄;SYN-2`). 같은 문항은
    호출하는 쪽에서 집합으로 한 번만 센다.
    """
    tokens = _tokens(raw)
    bad = [t for t in tokens if is_batch_qid(t)]
    if bad:
        raise ValueError(f"{where}: 일괄 표시 {bad}는 문항 ID가 아니다 — 전체 대상 목록에 넣을 수 없다")
    return tokens


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
            for line, row in enumerate(reader, start=2):
                key = tuple((row.get(c) or "").strip() for c in ANSWER_KEY_COLUMNS[:4])
                qid = (row.get("qid") or "").strip()
                if not qid:
                    continue
                # 답안 파일 한 행 = 한 문항(2026-10-11 재검토 R2). 일괄 표시·`;` 나열은 받지 않는다.
                tokens = question_tokens(qid, f"답안 파일 {path}:{line}")
                if len(tokens) != 1:
                    raise ValueError(f"답안 파일 {path}:{line}: qid '{qid}'는 한 문항이 아니다 — "
                                     "답안 파일은 한 행에 문항 하나만 적는다")
                slot = targets.setdefault(key, {"qids": set(), "sources": set()})
                slot["qids"].add(qid)
                slot["sources"].add(str(path))
    return targets


#: 세션 메타데이터(2026-10-11 T3-2·T3-3). 자료 구분·세트·조건과 **세션 전체 대상 목록**을
#: raw worklog를 고치지 않고 따로 연결한다. 서식 버전 1.
SESSION_META_VERSION = "1"
SESSION_META_COLUMNS: tuple[str, ...] = (
    "session_id", "data_kind", "set_id", "set_version", "condition", "target_qids",
)
DATA_KINDS: tuple[str, ...] = ("공개샘플", "가상BM", "합성")
CONDITIONS: tuple[str, ...] = ("신규처리", "캐시재생", "절차리허설")


class SessionMetaError(ValueError):
    """세션 메타데이터를 믿을 수 없을 때. 같은 자료로 추정하지 않고 멈춘다."""


def read_session_meta(paths: Sequence[Path]) -> dict[str, dict[str, Any]]:
    """세션 메타데이터 CSV를 읽는다.

    - `data_kind`·`condition`은 정한 값만 받는다. 빈 값·모르는 값은 오류다.
    - 같은 `session_id`가 다른 내용으로 두 번 나오면 **충돌 오류**다(조용히 덮어쓰지 않는다).
    - `target_qids`는 그 세션의 **전체 대상 목록**이다(`;` 나열). 비워 두면 목록을 주지
      않은 것이다. `전체`·`…일괄` 같은 일괄 표시는 목록으로 받지 않는다.
    """
    meta: dict[str, dict[str, Any]] = {}
    for path in paths:
        with Path(path).open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            missing = [c for c in SESSION_META_COLUMNS if c not in (reader.fieldnames or ())]
            if missing:
                raise SessionMetaError(f"세션 메타데이터에 열이 없다 {missing}: {path}")
            for line, row in enumerate(reader, start=2):
                sid = (row.get("session_id") or "").strip()
                if not sid:
                    raise SessionMetaError(f"{path}:{line} session_id가 비었다")
                kind = (row.get("data_kind") or "").strip()
                cond = (row.get("condition") or "").strip()
                set_id = (row.get("set_id") or "").strip()
                ver = (row.get("set_version") or "").strip()
                if kind not in DATA_KINDS:
                    raise SessionMetaError(f"{path}:{line} data_kind '{kind}'는 {DATA_KINDS} 중 하나가 아니다")
                if cond not in CONDITIONS:
                    raise SessionMetaError(f"{path}:{line} condition '{cond}'는 {CONDITIONS} 중 하나가 아니다")
                if not set_id or not ver:
                    raise SessionMetaError(f"{path}:{line} set_id·set_version은 비울 수 없다")
                raw_targets = (row.get("target_qids") or "").strip()
                try:
                    tokens = question_tokens(raw_targets, f"{path}:{line} target_qids")
                except ValueError as exc:
                    raise SessionMetaError(str(exc)) from None
                targets = frozenset(tokens) if tokens else None
                item = {"data_kind": kind, "set_id": set_id, "set_version": ver,
                        "condition": cond, "target_qids": targets}
                prev = meta.get(sid)
                if prev is not None:
                    if {k: prev[k] for k in item} != item:
                        raise SessionMetaError(
                            f"세션 메타데이터 충돌: session_id '{sid}'가 {prev['source']}와 "
                            f"{path}:{line}에서 다르게 적혔다 — 같은 자료로 추정하지 않는다")
                    continue
                item["source"] = f"{path}:{line}"
                meta[sid] = item
    return meta



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


RUN_TOTAL_ROW = "_run_total"
PIPELINE_TOTAL_ROW = "_pipeline_total"


def _valid_seconds(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def machine_seconds(run_dir: str | Path) -> dict[str, Any]:
    """`run_dir`의 `timings.json`에서 **실행 전체 기계 시간**을 읽는다 (2026-10-11 T3-1).

    읽기 계약:

    - 실행 전체 = `_run_total` 행의 `seconds` 하나. 호출 스크립트가 실행 끝에 붙인다.
    - `_pipeline_total`은 **코어 파이프라인 부분 시간**이다. `pipeline_total_seconds`로 따로
      적고 실행 전체로 승격하지 않는다. 두 합계 행을 더하지 않는다.
    - 단계 행(`_`로 시작하지 않는 행)을 더해 합계를 만들지 않는다.
    - 파일 없음·파싱 오류·합계 없음·합계 무효는 각각 사유와 함께 돌려준다. **0초로 바꾸지
      않는다.** 실제로 기록된 유효 합계 0초는 0초다.
    """
    rec: dict[str, Any] = {
        "run_dir": str(run_dir), "file_status": "읽음", "total_status": "미측정",
        "total_seconds": None, "total_scope": None, "pipeline_total_seconds": None,
        "stage_rows": 0, "reason": "", "cache_profile": None,
    }
    path = Path(run_dir) / "timings.json"
    if not path.is_file():
        rec.update(file_status="없음", reason="timings.json 없음")
        return rec
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        rec.update(file_status="파싱 오류", reason=f"timings.json을 읽지 못했다: {exc}")
        return rec
    stages = payload.get("stages") if isinstance(payload, dict) else None
    if not isinstance(stages, list):
        rec.update(file_status="파싱 오류", reason="timings.json에 stages 목록이 없다")
        return rec
    rec["cache_profile"] = payload.get("cache_profile")
    rows = [r for r in stages if isinstance(r, dict)]
    rec["stage_rows"] = sum(1 for r in rows if not str(r.get("stage", "")).startswith("_"))

    pipe = [r for r in rows if r.get("stage") == PIPELINE_TOTAL_ROW]
    if len(pipe) == 1 and _valid_seconds(pipe[0].get("seconds")):
        rec["pipeline_total_seconds"] = float(pipe[0]["seconds"])

    run = [r for r in rows if r.get("stage") == RUN_TOTAL_ROW]
    if len(run) > 1:
        rec.update(total_status="합계 무효", reason=f"{RUN_TOTAL_ROW} 행이 {len(run)}개다 — 어느 것도 고르지 않는다")
    elif run:
        value = run[0].get("seconds")
        if _valid_seconds(value):
            rec.update(total_status="확인", total_seconds=float(value),
                       total_scope=f"실행 전체({RUN_TOTAL_ROW})")
        else:
            rec.update(total_status="합계 무효",
                       reason=f"{RUN_TOTAL_ROW}.seconds가 없거나 유효하지 않다: {value!r}")
    else:
        why = f"{RUN_TOTAL_ROW} 행이 없다"
        if pipe:
            why += f" — {PIPELINE_TOTAL_ROW}(코어 파이프라인 부분 시간)만 있다. 실행 전체로 승격하지 않는다"
        elif rec["stage_rows"]:
            why += " — 단계 행만 있다. 단계 합으로 전체를 만들지 않는다"
        rec.update(total_status="합계 없음", reason=why)
    return rec


def _machine_block(run_dirs: Sequence[str], wait_seconds: float | None) -> dict[str, Any]:
    """세션의 기계 시간 — 완전 관측·부분 관측·미측정을 가른다. 사람 시간에 더하지 않는다."""
    unique = sorted({os.path.normpath(d) for d in run_dirs if d})
    block: dict[str, Any] = {"machine_total_seconds": None, "machine_partial": None,
                             "machine_runs": [], "machine_seconds_observed": None,
                             "machine_warnings": []}
    if not unique:
        block["machine_status"] = "미측정 — run_dir 기록 없음"
        return block
    recs = [machine_seconds(d) for d in unique]
    ok = [r for r in recs if r["total_status"] == "확인"]
    block["machine_runs"] = recs
    block["machine_seconds_observed"] = ok or None
    observed = round(sum(r["total_seconds"] for r in ok), 3)
    missing = [{"run_dir": r["run_dir"], "status": r["total_status"],
                "file_status": r["file_status"], "reason": r["reason"]}
               for r in recs if r["total_status"] != "확인"]
    if len(ok) == len(recs):
        block["machine_total_seconds"] = observed
        block["machine_status"] = f"완전 관측 — 실행 {len(recs)}건 모두 {RUN_TOTAL_ROW} 확인"
        if wait_seconds is not None and observed > wait_seconds + 1.0:
            block["machine_warnings"].append(
                "pipeline_exceeds_wait: 기계 시간이 사람이 적은 처리대기보다 길다. "
                "기록 누락일 수 있다 — 두 값을 더하지 말고 기록을 확인한다")
    elif ok:
        block["machine_status"] = f"부분 관측 — 실행 {len(recs)}건 중 {len(ok)}건만 합계 확인"
        block["machine_partial"] = {"observed_seconds": observed, "observed_runs": len(ok),
                                    "total_runs": len(recs), "missing_runs": missing,
                                    "note": "관측된 실행만의 합이다. 전체 기계 시간이 아니다"}
    else:
        block["machine_status"] = "미측정 — " + "; ".join(
            f"{m['run_dir']}: {m['reason']}" for m in missing)
        block["machine_partial"] = {"observed_seconds": None, "observed_runs": 0,
                                    "total_runs": len(recs), "missing_runs": missing}
    return block


def aggregate(entries: Sequence[WorkEntry], *, read_run_dirs: bool = False,
              answer_targets: dict[tuple[str, str, str, str], dict[str, Any]] | None = None,
              session_meta: dict[str, dict[str, Any]] | None = None,
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
        if has_batch_token(raw_qid) or len(listed) > 1:
            bucket["_batch_rows"] += 1
        if has_batch_token(raw_qid):
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
        if not entry.is_wait and len(listed) == 1 and not has_batch_token(raw_qid):
            # 문항 하나에 대한 관측 구간만 문항별 시간에 쓴다. 일괄 구간은 나누지 않는다.
            bucket["_by_qid"].setdefault(listed[0], []).append(span)

    out: list[dict[str, Any]] = []
    for bucket in sessions.values():
        human_raw, wait_raw = bucket.pop("_human"), bucket.pop("_wait")
        _attach_identity(bucket, session_meta or {})
        _finish_extras(bucket, answer_targets or {}, session_meta or {})
        bucket["qids"] = sorted(bucket["qids"])
        bucket["run_dirs"] = sorted(bucket["run_dirs"])
        if not human_raw and not wait_raw:
            # 합산 가능한 행이 없다 — 0초가 아니라 "산출 불가"다.
            bucket.update({"elapsed_seconds": None, "human_seconds": None,
                           "wait_seconds": None, "idle_seconds": None,
                           "overlap_removed_seconds": None, "warnings": []})
            if read_run_dirs:
                block = _machine_block(bucket["run_dirs"], None)
                bucket["warnings"].extend(block.pop("machine_warnings"))
                bucket.update(block)
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
        if read_run_dirs:
            block = _machine_block(bucket["run_dirs"], wait_s)
            warnings.extend(block.pop("machine_warnings"))
            bucket.update(block)
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
        "machine_note":
            "machine_total_seconds는 모든 run_dir의 _run_total이 확인될 때만 낸다. 일부만 확인되면 "
            "machine_partial에 관측분·관측 실행 수·누락 실행을 적고 전체는 null이다. 0초로 메우지 않는다.",
        "grouping_rule":
            "요약은 자료 구분·세트 ID·버전·조건·방식·회차가 같은 세션끼리만 묶는다. 세션 메타데이터가 "
            "없는 세션은 같은 자료로 추정하지 않고 세션별로 따로 둔다.",
        "target_rule":
            "세션 전체 대상 목록은 답안 파일 또는 세션 메타데이터 target_qids에서만 인정한다. worklog 각 행의 "
            "qid는 그 구간의 목록(부분 목록)이다. 둘이 어긋나면 충돌로 표시한다.",
        "batch_note":
            "일괄 구간(qid가 '전체'이거나 '…일괄')의 시간은 문항별로 나누지 않는다. "
            "일괄 표시만으로 대상 수를 만들지 않는다.",
    }


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _attach_identity(bucket: dict[str, Any], session_meta: dict[str, dict[str, Any]]) -> None:
    """자료 구분·세트·조건을 세션에 붙인다 (2026-10-11 T3-2). 없으면 추정하지 않는다."""
    meta = session_meta.get(bucket["session_id"])
    if meta is None:
        bucket["identity"] = {"status": "미확인 — 세션 메타데이터 없음", "data_kind": None,
                              "set_id": None, "set_version": None, "condition": None,
                              "source": None}
    else:
        bucket["identity"] = {"status": "확인", "data_kind": meta["data_kind"],
                              "set_id": meta["set_id"], "set_version": meta["set_version"],
                              "condition": meta["condition"], "source": meta["source"]}


def _finish_extras(bucket: dict[str, Any],
                   answer_targets: dict[tuple[str, str, str, str], dict[str, Any]],
                   session_meta: dict[str, dict[str, Any]] | None = None) -> None:
    """대상 문항·단계별 합계·문항별 중앙값·재사용·기록 완전성 (2026-10-11 T3, T3-3)."""
    key = (bucket["session_id"], bucket["participant"], bucket["method"], bucket["request_round"])
    listed: set[str] = bucket.pop("_listed")
    batch_rows: int = bucket.pop("_batch_rows")
    marker_rows: int = bucket.pop("_marker_rows")
    from_answers = answer_targets.get(key)
    meta = (session_meta or {}).get(bucket["session_id"])

    # 세션 전체 대상 목록: 답안 파일 / 세션 메타데이터 target_qids. worklog 행의 qid는 부분 목록.
    full_lists: list[tuple[str, frozenset[str]]] = []
    if from_answers:
        full_lists.append(("답안 파일: " + ", ".join(sorted(from_answers["sources"])),
                           frozenset(from_answers["qids"])))
    if meta and meta.get("target_qids"):
        full_lists.append(("세션 메타데이터: " + meta["source"], meta["target_qids"]))
    sources = [label for label, _ in full_lists]
    if listed:
        sources.append("worklog 구간 qid(부분 목록)")
    conflict: list[str] = []
    distinct = {q for _, q in full_lists}
    if len(distinct) > 1:
        conflict.append("전체 대상 목록끼리 다르다: " + " / ".join(
            f"{label} → {sorted(q)}" for label, q in full_lists))
    if len(distinct) == 1:
        full = next(iter(distinct))
        outside = sorted(listed - full)
        if outside:
            conflict.append(f"worklog 구간 qid {outside}가 전체 대상 목록 밖에 있다")
    if conflict:
        bucket["target_qids"] = sorted(set().union(listed, *distinct))
        bucket["target_qid_count"] = None
        bucket["target_status"] = "충돌 — " + " / ".join(conflict)
    elif full_lists:
        full = next(iter(distinct))
        bucket["target_qids"] = sorted(full)
        bucket["target_qid_count"] = len(full)
        bucket["target_status"] = "확인 — 전체 대상 목록(" + ", ".join(
            label.split(":")[0] for label, _ in full_lists) + ")"
    elif listed:
        bucket["target_qids"] = sorted(listed)
        bucket["target_qid_count"] = None
        bucket["target_status"] = ("부분 목록 — worklog 구간 qid만 있다. 전체 대상 수 미확인"
                                   + (" (일괄 표시 행도 있다)" if marker_rows else ""))
    else:
        bucket["target_qids"] = []
        bucket["target_qid_count"] = None
        bucket["target_status"] = ("미확인 — 일괄 표시만 있고 대상 문항 목록이 없다" if marker_rows
                                   else "미확인 — 대상 문항 목록이 없다")
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


def _group_key(s: dict[str, Any]) -> tuple:
    ident = s.get("identity") or {}
    if ident.get("status") != "확인":
        # 메타데이터가 없으면 같은 자료로 추정하지 않는다 — 세션마다 따로 둔다.
        return ("미확인", s["session_id"], "", "", s["method"], s["request_round"])
    return (ident["data_kind"], ident["set_id"], ident["set_version"], ident["condition"],
            s["method"], s["request_round"])


def _by_method_round(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """자료 구분·세트·버전·조건·방식·회차별 요약 (2026-10-11 T3-2).

    서로 다른 참가자의 시간을 하나의 벽시계 경과로 합치지 않는다. 자료나 조건이 다른 세션은
    같은 요약에 넣지 않는다.
    """
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for s in sessions:
        groups.setdefault(_group_key(s), []).append(s)
    out = []
    for key, items in sorted(groups.items()):
        unknown = any(s["target_qid_count"] is None for s in items)
        targets = sorted(set().union(*(set(s["target_qids"]) for s in items)))
        identified = key[0] != "미확인"
        out.append({
            "identity_status": "확인" if identified else "미확인 — 세션 메타데이터 없음(세션별로만 집계)",
            "data_kind": key[0] if identified else None,
            "set_id": key[1] if identified else None,
            "set_version": key[2] if identified else None,
            "condition": key[3] if identified else None,
            "session_ids": sorted({s["session_id"] for s in items}),
            "method": key[4], "request_round": key[5],
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
                    answer_paths: Sequence[Path] = (),
                    session_meta_paths: Sequence[Path] = ()) -> dict[str, Any]:
    entries: list[WorkEntry] = []
    for path in paths:
        entries.extend(read_worklog(Path(path)))
    targets = read_answer_targets([Path(p) for p in answer_paths]) if answer_paths else {}
    meta = read_session_meta([Path(p) for p in session_meta_paths]) if session_meta_paths else {}
    report = aggregate(entries, read_run_dirs=read_run_dirs, answer_targets=targets,
                       session_meta=meta)
    report["sources"] = [str(p) for p in paths]
    report["answer_sources"] = [str(p) for p in answer_paths]
    report["session_meta_sources"] = [str(p) for p in session_meta_paths]
    report["session_meta_version"] = SESSION_META_VERSION if session_meta_paths else None
    return report
