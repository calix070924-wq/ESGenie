"""공통 OpenAI 클라이언트 생성 — Azure v1과 기존 엔드포인트를 구분한다."""
from __future__ import annotations

from urllib.parse import urlsplit

from .config import Settings


def create_openai_client(settings: Settings):
    from openai import AzureOpenAI, OpenAI

    ep = (settings.azure_openai_endpoint or "").strip().rstrip("/")
    common = {"api_key": settings.openai_api_key, "max_retries": 0}
    if not ep:
        return OpenAI(**common)

    endpoint = urlsplit(ep)
    # v1은 완성된 base URL이다. Foundry 호스트여도 /models를 덧붙이지 않는다.
    if endpoint.path.rstrip("/") == "/openai/v1":
        return OpenAI(base_url=f"{ep}/", **common)

    if (endpoint.hostname or "").endswith(".services.ai.azure.com"):
        return OpenAI(base_url=f"{ep}/models/", **common)

    return AzureOpenAI(
        azure_endpoint=ep,
        api_version=settings.azure_api_version,
        **common,
    )
