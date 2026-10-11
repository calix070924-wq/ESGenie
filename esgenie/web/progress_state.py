"""Persist only the public, bounded progress event contract."""
from copy import deepcopy

PHASES = {"reading", "analyzing", "organizing"}
FIELDS = {"kind", "run_id", "sequence", "at", "phase", "stage", "file_id", "file_name", "file_role",
          "total_files", "request_id", "provider", "attempt", "max_attempts", "delay_seconds", "reason"}


def apply_event(job, event):
    if (job.get("status") != "running" or job.get("run_id") != event.get("run_id")
            or event.get("sequence", 0) <= job.get("sequence", 0)):
        return False
    kind = event.get("kind")
    if kind not in {"targets", "stage", "file_started", "file_finished", "request_started", "request_retry", "request_finished"}:
        return False
    event = {k: deepcopy(v) for k, v in event.items() if k in FIELDS}
    job["sequence"] = event["sequence"]
    job["event_at"] = event["at"]
    job["events"] = (job.get("events", []) + [event])[-100:]
    if kind == "stage" and event.get("phase") in PHASES:
        job.update(phase=event["phase"], stage=event["stage"], stage_started_at=event["at"])
    elif kind == "targets":
        job["total_files"] = event["total_files"]
    elif kind == "file_started":
        job["active_files"] = [f for f in job.get("active_files", []) if f["id"] != event["file_id"]] + [
            {"id": event["file_id"], "name": event["file_name"], "role": event["file_role"]}]
    elif kind == "file_finished":
        job["active_files"] = [f for f in job.get("active_files", []) if f["id"] != event["file_id"]]
        job["completed_file_ids"] = list(dict.fromkeys(job.get("completed_file_ids", []) + [event["file_id"]]))
        job["completed_files"] = len(job["completed_file_ids"])
    elif kind.startswith("request_"):
        requests = [r for r in job.get("requests", []) if r["id"] != event["request_id"]]
        if kind != "request_finished":
            requests.append({"id": event["request_id"], "provider": event["provider"],
                             "state": "retrying" if kind == "request_retry" else "waiting",
                             "attempt": event["attempt"], "max_attempts": event["max_attempts"],
                             "started_at": event["at"], "delay_seconds": event.get("delay_seconds"),
                             "reason": event.get("reason", "")})
        job["requests"] = requests
    return True


def finish(job, status, stage, at, error=""):
    job.update(status=status, phase=status, stage=stage, error=error, ended_at=at,
               event_at=at, active_files=[], requests=[])
