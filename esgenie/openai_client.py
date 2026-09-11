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


def normalized_endpoint(endpoint: str) -> str:
    """동등한 주소 표기를 통일한다. 인증정보와 쿼리의 키는 식별자에 포함하지 않는다."""
    from urllib.parse import urlunsplit
    parsed = urlsplit(str(endpoint).strip())
    host = (parsed.hostname or '').lower()
    port = parsed.port
    if port and not ((parsed.scheme.lower() == 'https' and port == 443)
                     or (parsed.scheme.lower() == 'http' and port == 80)):
        host += f':{port}'
    return urlunsplit((parsed.scheme.lower(), host, parsed.path.rstrip('/'), '', ''))


def configured_connection(settings: Settings, provider: str) -> dict[str, str]:
    """SDK 정보가 없는 테스트 대역용 생성 시점 스냅샷. 사용하지 않는 제공자 설정은 제외."""
    import os
    if provider == 'anthropic':
        return {'endpoint': os.getenv('ANTHROPIC_BASE_URL', 'https://api.anthropic.com'),
                'api_style': 'anthropic-messages', 'api_version': '2023-06-01'}
    ep = (settings.azure_openai_endpoint or '').strip().rstrip('/')
    if not ep:
        return {'endpoint': os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1'),
                'api_style': 'openai-chat', 'api_version': ''}
    parsed = urlsplit(ep)
    if parsed.path.rstrip('/') == '/openai/v1':
        return {'endpoint': ep, 'api_style': 'openai-chat', 'api_version': ''}
    if (parsed.hostname or '').endswith('.services.ai.azure.com'):
        return {'endpoint': ep + '/models', 'api_style': 'openai-chat', 'api_version': ''}
    return {'endpoint': ep + '/openai', 'api_style': 'azure-chat',
            'api_version': settings.azure_api_version}


def connection_identity(client, provider: str, fallback: dict[str, str]) -> dict[str, str]:
    """실제 클라이언트의 주소·API 방식을 사용한다. 전역 설정 변경과 연결을 혼동하지 않는다."""
    import hashlib
    endpoint = str(getattr(client, 'base_url', '') or fallback['endpoint'])
    api_version = getattr(client, '_api_version', None)
    if api_version is None:
        query = getattr(client, 'default_query', {})
        api_version = query.get('api-version') if isinstance(query, dict) else None
    if provider == 'anthropic':
        headers = getattr(client, 'default_headers', {})
        api_version = headers.get('anthropic-version', fallback['api_version'])
        style = 'anthropic-messages'
    elif api_version:
        style = 'azure-chat'
    else:
        style = 'openai-chat' if getattr(client, 'base_url', None) else fallback['api_style']
        api_version = '' if getattr(client, 'base_url', None) else fallback['api_version']
    return {'endpoint_sha256': hashlib.sha256(normalized_endpoint(endpoint).encode()).hexdigest(),
            'api_style': style, 'api_version': str(api_version or '')}
