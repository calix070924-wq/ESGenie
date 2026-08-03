"""LLM 클라이언트 — mock 폴백 재시도/로깅 테스트 (실 네트워크 호출 없음).

conftest.py가 ESGENIE_FORCE_MOCK=1을 강제하므로 LLMClient() 생성자는 실 SDK
클라이언트를 만들지 않는다. 여기서는 인스턴스 생성 후 `_openai_client` /
`_anthropic_client`에 가짜(fake) 클라이언트를 직접 주입해 재시도 분기만 검증한다.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from esgenie import llm as llm_module, llm_cache
from esgenie.llm import LLM_MAX_ATTEMPTS, LLMClient, LLMUnavailableError


class _RetryableError(Exception):
    """429 성격의 일시 오류."""
    status_code = 429


class _AuthError(Exception):
    """401 성격의 인증 오류 — 재시도 대상 아님."""
    status_code = 401


def _openai_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def _anthropic_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


class _FakeOpenAI:
    """`chat.completions.create(**kwargs)` 호출마다 큐에서 하나씩 꺼내 반환/raise."""

    def __init__(self, sequence: list):
        self._sequence = list(sequence)
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        item = self._sequence.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeAnthropic:
    def __init__(self, sequence: list):
        self._sequence = list(sequence)
        self.calls = 0
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls += 1
        item = self._sequence.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _no_backoff_delay(monkeypatch):
    """지수 백오프 sleep을 제거해 테스트를 즉시 실행한다."""
    monkeypatch.setattr(llm_module.time, "sleep", lambda *_a, **_kw: None)


@pytest.fixture(autouse=True)
def _no_pii_mask(monkeypatch):
    monkeypatch.setattr(llm_module.SETTINGS, "pii_mask", False)


def _client() -> LLMClient:
    return LLMClient()


# ---- 1) 일시 오류 → 재시도 후 성공 ------------------------------------------

def test_openai_retry_then_success():
    fake = _FakeOpenAI([_RetryableError("rate limited"), _openai_response("실제 응답")])
    client = _client()
    client._openai_client = fake

    resp = client.complete(system="s", user="u")

    assert fake.calls == 2
    assert resp.used_mock is False
    assert resp.content == "실제 응답"


def test_live_response_cache_replays_without_second_call(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_module.SETTINGS, "force_mock", False)
    monkeypatch.delenv("ESGENIE_FORCE_MOCK", raising=False)
    monkeypatch.setenv("ESGENIE_LLM_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("ESGENIE_LLM_CACHE_REFRESH", raising=False)
    llm_cache.reset_stats()
    fake = _FakeOpenAI([_openai_response("캐시할 라이브 응답")])
    client = _client()
    client._openai_client = fake

    first = client.complete(system="same system", user="same user")
    second = client.complete(system="same system", user="same user")

    assert first.content == second.content == "캐시할 라이브 응답"
    assert fake.calls == 1
    assert first.meta["cache"] == "miss"
    assert second.meta["cache"] == "hit"
    assert llm_cache.stats() == {
        "mode": "on", "hits": 1, "misses": 1, "live_calls": 1,
    }


def test_live_response_cache_recovers_from_non_object_json(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_module.SETTINGS, "force_mock", False)
    monkeypatch.delenv("ESGENIE_FORCE_MOCK", raising=False)
    monkeypatch.setenv("ESGENIE_LLM_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("ESGENIE_LLM_CACHE_REFRESH", raising=False)
    llm_cache.reset_stats()
    key = llm_cache.make_key(
        provider="openai", model=llm_module.SETTINGS.openai_model,
        system="same system", user="same user", temperature=0.0, json_mode=False,
    )
    (tmp_path / f"{key}.json").write_text("[]", encoding="utf-8")
    fake = _FakeOpenAI([_openai_response("복구 응답")])
    client = _client()
    client._openai_client = fake

    response = client.complete(system="same system", user="same user")

    assert response.content == "복구 응답"
    assert fake.calls == 1
    assert llm_cache.stats() == {
        "mode": "on", "hits": 0, "misses": 1, "live_calls": 1,
    }


def test_anthropic_retry_then_success():
    fake = _FakeAnthropic([_RetryableError("rate limited"), _anthropic_response("실제 응답")])
    client = _client()
    client._openai_client = None
    client._anthropic_client = fake

    resp = client.complete(system="s", user="u")

    assert fake.calls == 2
    assert resp.used_mock is False
    assert resp.content == "실제 응답"


# ---- 2) 재시도 소진, non-strict → mock 폴백 + 경고 로그 ---------------------

def test_openai_retries_exhausted_non_strict_falls_back_to_mock(monkeypatch, caplog):
    monkeypatch.setattr(llm_module.SETTINGS, "strict_llm", False)
    fake = _FakeOpenAI([_RetryableError("boom")] * LLM_MAX_ATTEMPTS)
    client = _client()
    client._openai_client = fake

    with caplog.at_level(logging.WARNING, logger="esgenie.llm"):
        resp = client.complete(system="s", user="영역: E\n보고서 작성")

    assert fake.calls == LLM_MAX_ATTEMPTS
    assert resp.used_mock is True
    assert "boom" in resp.meta["error"]
    messages = "\n".join(r.message for r in caplog.records)
    assert "재시도" in messages
    assert "mock 폴백" in messages


# ---- 3) 재시도 소진, strict → LLMUnavailableError raise ---------------------

def test_openai_retries_exhausted_strict_raises(monkeypatch):
    monkeypatch.setattr(llm_module.SETTINGS, "strict_llm", True)
    fake = _FakeOpenAI([_RetryableError("boom")] * LLM_MAX_ATTEMPTS)
    client = _client()
    client._openai_client = fake

    with pytest.raises(LLMUnavailableError):
        client.complete(system="s", user="u")

    assert fake.calls == LLM_MAX_ATTEMPTS


# ---- 4) 비재시도 오류(인증/400성격) → 재시도 없이 즉시 폴백/ raise ----------

def test_openai_non_retryable_error_skips_retry(monkeypatch):
    monkeypatch.setattr(llm_module.SETTINGS, "strict_llm", False)
    fake = _FakeOpenAI([_AuthError("invalid api key")])
    client = _client()
    client._openai_client = fake

    resp = client.complete(system="s", user="u")

    assert fake.calls == 1
    assert resp.used_mock is True
    assert "invalid api key" in resp.meta["error"]


def test_openai_non_retryable_error_strict_raises_immediately(monkeypatch):
    monkeypatch.setattr(llm_module.SETTINGS, "strict_llm", True)
    fake = _FakeOpenAI([_AuthError("invalid api key")])
    client = _client()
    client._openai_client = fake

    with pytest.raises(LLMUnavailableError):
        client.complete(system="s", user="u")

    assert fake.calls == 1


# ---- 5) Anthropic 폴백 경로 — OpenAI와 동일한 대칭 케이스 -------------------

def test_anthropic_retries_exhausted_non_strict_falls_back_to_mock(monkeypatch, caplog):
    monkeypatch.setattr(llm_module.SETTINGS, "strict_llm", False)
    fake = _FakeAnthropic([_RetryableError("boom")] * LLM_MAX_ATTEMPTS)
    client = _client()
    client._openai_client = None
    client._anthropic_client = fake

    with caplog.at_level(logging.WARNING, logger="esgenie.llm"):
        resp = client.complete(system="s", user="영역: E\n보고서 작성")

    assert fake.calls == LLM_MAX_ATTEMPTS
    assert resp.used_mock is True
    assert "boom" in resp.meta["error"]
    messages = "\n".join(r.message for r in caplog.records)
    assert "재시도" in messages
    assert "mock 폴백" in messages


def test_anthropic_retries_exhausted_strict_raises(monkeypatch):
    monkeypatch.setattr(llm_module.SETTINGS, "strict_llm", True)
    fake = _FakeAnthropic([_RetryableError("boom")] * LLM_MAX_ATTEMPTS)
    client = _client()
    client._openai_client = None
    client._anthropic_client = fake

    with pytest.raises(LLMUnavailableError):
        client.complete(system="s", user="u")

    assert fake.calls == LLM_MAX_ATTEMPTS


def test_anthropic_non_retryable_error_skips_retry(monkeypatch):
    monkeypatch.setattr(llm_module.SETTINGS, "strict_llm", False)
    fake = _FakeAnthropic([_AuthError("invalid api key")])
    client = _client()
    client._openai_client = None
    client._anthropic_client = fake

    resp = client.complete(system="s", user="u")

    assert fake.calls == 1
    assert resp.used_mock is True
    assert "invalid api key" in resp.meta["error"]
