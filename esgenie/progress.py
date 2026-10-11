"""Optional execution-local observations. Never include prompts or exception text.

Observers cannot change analysis behavior. Context is local to the worker; a
future parallel worker must explicitly propagate it using copy_context().
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from threading import RLock

_session = ContextVar("analysis_progress", default=None)


@contextmanager
def observe(run_id, observer):
    token = _session.set({"run_id": run_id, "observer": observer, "sequence": 0, "lock": RLock()})
    try:
        yield
    finally:
        _session.reset(token)


def emit(kind, **fields):
    session = _session.get()
    if session is None:
        return
    # Serialize sequence and delivery, including explicitly propagated threads.
    with session["lock"]:
        session["sequence"] += 1
        event = {**fields, "kind": kind, "run_id": session["run_id"],
                 "sequence": session["sequence"], "at": datetime.now(timezone.utc).isoformat()}
        try:
            session["observer"](event)
        except Exception:
            pass  # An unavailable UI or disk must not turn a successful AI call into failure.


def observed_files(items, identify):
    """Observe a sequential consumer at its existing iteration boundaries.

    Completion means the consumer resumed after processing the whole file. A
    chunk or an individual AI request never increments this count. An exception
    or early exit leaves the file active until the execution terminates.
    """
    for item in items:
        fields = identify(item)
        emit("file_started", **fields)
        yield item
        emit("file_finished", **fields)


class EvidenceFiles(dict):
    """Pipeline's existing sequential OCR loop consumes items exactly once."""
    def __init__(self, values, documents):
        super().__init__(values)
        self.documents = documents
        self._observed = False

    def items(self):
        if self._observed:
            yield from super().items()
            return
        self._observed = True
        yield from observed_files(super().items(), lambda item: {
            "file_id": self.documents[item[0]]["id"], "file_name": item[0], "file_role": "evidence"})
        emit("stage", phase="analyzing", stage="읽은 자료를 분석하고 있어요")


class CompanyFiles(list):
    """Preserve a single parse_saq_claims call and its cross-file diagnostics."""
    def __init__(self, values, documents):
        super().__init__(values)
        self.documents = documents

    def __iter__(self):
        yield from observed_files(super().__iter__(), lambda path: {
            "file_id": self.documents[path]["id"], "file_name": self.documents[path]["name"],
            "file_role": "company_answer"})


def failure_reason(exc):
    """Safe user-facing categories; SDK messages may contain URLs or secrets."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
        name = type(exc).__name__
        body = getattr(exc, "body", None)
        detail = body.get("error", body) if isinstance(body, dict) else {}
        code = getattr(exc, "code", None) or (detail.get("code") if isinstance(detail, dict) else None)
        if code in {"insufficient_quota", "billing_hard_limit_reached", "organization_usage_limit_exceeded"}:
            return "AI 서비스의 사용 한도 또는 결제 설정을 확인해 주세요."
        if status in (401, 403):
            return "AI 서비스 인증 또는 접근 설정을 확인해 주세요."
        if status == 429:
            return "AI 서비스의 요청 한도에 도달했어요. 잠시 후 다시 시도해 주세요."
        if status == 408 or "Timeout" in name:
            return "AI 응답 대기 시간이 초과됐어요. 잠시 후 다시 시도해 주세요."
        if "Connection" in name:
            return "AI 서비스에 연결하지 못했어요. 연결 상태를 확인해 주세요."
        if isinstance(status, int) and status >= 500:
            return "AI 서비스가 일시적으로 응답하지 못했어요. 잠시 후 다시 시도해 주세요."
        exc = exc.__cause__ or exc.__context__
    return "자료 읽기를 마치지 못했어요. 연결 상태와 파일이 잘 열리는지 확인한 뒤 다시 시도해 주세요."
