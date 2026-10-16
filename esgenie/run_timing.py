"""파이프라인 단계별 소요 시간·캐시 적중 계측 (작업지시서 A §3.1).

**동작을 바꾸지 않는다.** 이 모듈은 재는 일만 한다. 어떤 단계도 건너뛰지 않고,
어떤 값도 고치지 않으며, 예외를 삼키지도 않는다. `result.json`은 계측 전후가
동일해야 한다(§3.3 ①).

측정 단위는 `time.perf_counter()`다. 벽시계(`time.time()`)는 시스템 시각 조정에
흔들려 음수 구간이 나올 수 있으므로 쓰지 않는다.

UI 타이머는 만들지 않는다(§3.2).
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator


def _llm_cache_counters() -> dict[str, int]:
    """llm_cache의 누적 카운터 스냅샷. 모드 같은 비수치 키는 뺀다."""
    try:
        from esgenie import llm_cache
        raw = llm_cache.stats()
    except Exception:
        return {}
    return {k: int(v) for k, v in raw.items() if isinstance(v, int)}


def _delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    """단계 전후 차이. 전역 누적값을 그대로 적으면 단계별 해석이 불가능하다."""
    return {k: after.get(k, 0) - before.get(k, 0) for k in sorted(set(before) | set(after))}


@dataclass
class RunTiming:
    """한 실행의 단계별 계측 기록.

    `records`는 `PipelineOutput.timings`에 그대로 들어간다. 호출 스크립트(응답서
    생성·엑셀/PDF 내보내기)도 `append()`로 **같은 모양**을 덧붙인다.
    """

    records: list[dict[str, Any]] = field(default_factory=list)
    _run_start: float = field(default_factory=time.perf_counter)

    def start(self) -> tuple[float, dict[str, int]]:
        """재기 시작. `with`로 감싸기 어려운(이미 try/except로 묶인) 구간용."""
        return time.perf_counter(), _llm_cache_counters()

    def stop(self, token: tuple[float, dict[str, int]], name: str, **extra: Any) -> dict[str, Any]:
        """`start()`로 받은 토큰으로 구간을 닫는다."""
        start, before = token
        record: dict[str, Any] = {"stage": name, **extra}
        record["seconds"] = round(time.perf_counter() - start, 6)
        record["llm_cache_delta"] = _delta(before, _llm_cache_counters())
        self.records.append(record)
        return record

    @contextmanager
    def stage(self, name: str, **extra: Any) -> Iterator[dict[str, Any]]:
        """단계 하나를 잰다. 예외가 나도 기록을 남기고 **그대로 다시 올린다.**"""
        token = self.start()
        record: dict[str, Any] = {}
        try:
            yield record
        finally:
            extra.update(record)
            closed = self.stop(token, name, **extra)
            record.clear()
            record.update(closed)

    def append(self, stage: str, seconds: float, **extra: Any) -> None:
        """이미 잰 구간을 같은 모양으로 덧붙인다(호출 스크립트용)."""
        self.records.append({"stage": stage, "seconds": round(float(seconds), 6), **extra})

    def elapsed(self) -> float:
        """실행 시작부터 지금까지의 전체 경과 시간."""
        return round(time.perf_counter() - self._run_start, 6)

    def finish(self, name: str = "_total") -> list[dict[str, Any]]:
        """전체 경과 시간 행을 마지막에 붙이고 기록을 돌려준다.

        단계 시간의 합은 전체 경과 시간보다 **클 수 없다**(§3.3 ②). 단계가 겹치지
        않게 잡았으면 합 ≤ 전체이고, 차이는 단계 밖 구간이다.

        합계 행(`_`로 시작하는 stage)은 합산에서 **제외한다.** 포함하면 뒤에 붙는
        합계가 앞의 합계를 다시 더해 두 배로 부푼다.
        """
        total = self.elapsed()
        staged = sum(float(r.get("seconds", 0.0)) for r in self.records
                     if not str(r.get("stage", "")).startswith("_"))
        self.records.append({
            "stage": name,
            "seconds": total,
            "staged_sum": round(staged, 6),
            "unstaged_seconds": round(total - staged, 6),
        })
        return self.records
