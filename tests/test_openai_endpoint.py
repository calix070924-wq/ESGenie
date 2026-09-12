"""실 SDK + 메모리 HTTP transport로 최종 호출 URL을 검증한다(외부 호출 없음)."""
from dataclasses import replace

import httpx
import openai
import pytest

from esgenie import llm


@pytest.fixture
def captured_requests(monkeypatch):
    requests = []
    clients = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={
            "id": "audit", "object": "chat.completion", "created": 0,
            "model": "gpt-4.1-mini",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"},
                         "finish_reason": "stop"}],
        })

    for name in ("OpenAI", "AzureOpenAI"):
        real_class = getattr(openai, name)

        def construct(*args, _class=real_class, **kwargs):
            transport = httpx.Client(transport=httpx.MockTransport(respond))
            client = _class(*args, http_client=transport, **kwargs)
            clients.append(client)
            return client

        monkeypatch.setattr(openai, name, construct)

    monkeypatch.setenv("ESGENIE_LLM_CACHE", "0")
    # 외부 환경 설정이 공개 OpenAI 경로 테스트에 개입하지 않도록 격리.
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    yield requests
    for client in clients:
        client.close()


@pytest.mark.parametrize("endpoint,expected_path", [
    ("https://example.services.ai.azure.com/openai/v1", "/openai/v1/chat/completions"),
    ("https://example.services.ai.azure.com/openai/v1/", "/openai/v1/chat/completions"),
    ("https://example.openai.azure.com/openai/v1", "/openai/v1/chat/completions"),
    ("https://example.openai.azure.com/openai/v1/", "/openai/v1/chat/completions"),
    ("https://example.services.ai.azure.com", "/models/chat/completions"),
    ("https://example.services.ai.azure.com/", "/models/chat/completions"),
    ("https://example.openai.azure.com", "/openai/deployments/gpt-4.1-mini/chat/completions"),
    (None, "/v1/chat/completions"),
])
def test_runtime_request_url(monkeypatch, captured_requests, endpoint, expected_path):
    settings = replace(llm.SETTINGS, openai_api_key="test-key", anthropic_api_key=None,
                       azure_openai_endpoint=endpoint, openai_model="gpt-4.1-mini",
                       force_mock=False, strict_llm=True)
    monkeypatch.setattr(llm, "SETTINGS", settings)
    result = llm.LLMClient().complete("system", "test")
    assert result.content == "OK" and not result.used_mock
    assert len(captured_requests) == 1
    request = captured_requests[0]
    assert request.url.path == expected_path
    if endpoint and "/openai/v1" in endpoint:
        assert "api-version" not in request.url.params
    elif endpoint and "openai.azure.com" in endpoint:
        assert request.url.params["api-version"] == settings.azure_api_version


@pytest.mark.parametrize("suffix", ["", "/"])
def test_probe_uses_same_v1_route(monkeypatch, captured_requests, suffix):
    from scripts import probe_judge_models as probe

    settings = replace(llm.SETTINGS, openai_api_key="test-key",
                       azure_openai_endpoint="https://example.services.ai.azure.com/openai/v1" + suffix)
    monkeypatch.setattr(probe, "SETTINGS", settings)
    monkeypatch.setattr(probe, "CANDIDATES", ["test-deployment"])
    probe.main()
    assert len(captured_requests) == 1
    assert captured_requests[0].url.path == "/openai/v1/chat/completions"
