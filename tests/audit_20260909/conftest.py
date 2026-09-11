"""Independent audit tests: prohibit network calls and isolate response caches."""
import socket

import pytest


@pytest.fixture(autouse=True)
def audit_isolation(monkeypatch, tmp_path):
    monkeypatch.setenv('ESGENIE_OCR_CACHE_DIR', str(tmp_path / 'ocr'))
    monkeypatch.setenv('ESGENIE_LLM_CACHE_DIR', str(tmp_path / 'llm'))
    monkeypatch.setenv('ESGENIE_LLM_CACHE_REFRESH', '0')
    monkeypatch.setenv('ESGENIE_LLM_CACHE', '1')

    def blocked(*args, **kwargs):
        raise AssertionError('Real network forbidden in this audit')

    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket.socket, 'connect_ex', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)
