"""라이브 LLM 완성 응답 캐시.

키는 provider·model·온도·JSON 모드와 실제 전송 프롬프트 전문으로 만든다. 라이브
성공 응답만 저장하며 mock/fallback 응답은 이 모듈에 도달하지 않는다. 테스트의
``ESGENIE_FORCE_MOCK=1``에서는 읽기·쓰기를 모두 끈다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_stats = {"hits": 0, "misses": 0, "live_calls": 0}


def _force_mock() -> bool:
    from .config import SETTINGS
    return SETTINGS.force_mock or os.getenv("ESGENIE_FORCE_MOCK", "0") == "1"


def cache_mode() -> str:
    if _force_mock() or os.getenv("ESGENIE_LLM_CACHE", "1") in ("0", "false", "False"):
        return "disabled"
    if os.getenv("ESGENIE_LLM_CACHE_REFRESH", "0") == "1":
        return "refresh"
    return "on"


def cache_dir() -> Path:
    override = os.getenv("ESGENIE_LLM_CACHE_DIR")
    if override:
        return Path(override)
    from .config import DATA_DIR
    return DATA_DIR / "_cache" / "llm"


def make_key(
    *, provider: str, model: str, system: str, user: str,
    temperature: float, json_mode: bool,
) -> str:
    h = hashlib.sha256()
    for part in (provider, model, temperature, json_mode, system, user):
        h.update(str(part).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def lookup(key: str) -> str | None:
    mode = cache_mode()
    if mode == "disabled":
        return None
    if mode == "refresh":
        _stats["misses"] += 1
        return None
    path = cache_dir() / f"{key}.json"
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _stats["misses"] += 1
        return None
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("LLM 캐시 파일 손상 — 재호출 [%s]: %s", path.name, exc)
        _stats["misses"] += 1
        return None
    content = entry.get("content") if isinstance(entry, dict) else None
    if (
        not isinstance(entry, dict)
        or entry.get("schema") != SCHEMA_VERSION
        or not isinstance(content, str)
    ):
        logger.warning("LLM 캐시 스키마 불일치 — 재호출 [%s]", path.name)
        _stats["misses"] += 1
        return None
    _stats["hits"] += 1
    return content


def store(key: str, content: str, *, provider: str, model: str) -> None:
    if cache_mode() == "disabled":
        return
    path = cache_dir() / f"{key}.json"
    entry = {
        "schema": SCHEMA_VERSION,
        "meta": {
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "provider": provider,
            "model": model,
        },
        "content": content,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entry, ensure_ascii=False, indent=1), encoding="utf-8")
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("LLM 캐시 저장 실패 — 무시 [%s]: %s", path.name, exc)


def record_live_call() -> None:
    _stats["live_calls"] += 1


def reset_stats() -> None:
    for key in _stats:
        _stats[key] = 0


def stats() -> dict[str, int | str]:
    return {"mode": cache_mode(), **_stats}
