from __future__ import annotations

import json
from types import SimpleNamespace

from esgenie import dart_client


def test_load_corp_list_live_mode_uses_cached_list_without_samples(monkeypatch, tmp_path) -> None:
    cache_path = tmp_path / "corp_codes.json"
    cached = [
        {"corp_code": "00126380", "corp_name": "삼성전자", "stock_code": "005930", "industry": "전자"},
    ]
    cache_path.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(dart_client, "SETTINGS", SimpleNamespace(use_mock_dart=False))
    monkeypatch.setattr(dart_client, "CORP_CACHE", cache_path)
    monkeypatch.setattr(
        dart_client,
        "_sample_corp_list",
        lambda: [{"corp_code": "005930", "corp_name": "삼성전자", "stock_code": "005930", "industry": ""}],
    )

    assert dart_client._load_corp_list() == cached


def test_load_corp_list_live_mode_uses_downloaded_list_without_samples(monkeypatch, tmp_path) -> None:
    cache_path = tmp_path / "corp_codes.json"
    downloaded = [
        {"corp_code": "00126380", "corp_name": "삼성전자", "stock_code": "005930", "industry": "전자"},
    ]

    monkeypatch.setattr(dart_client, "SETTINGS", SimpleNamespace(use_mock_dart=False))
    monkeypatch.setattr(dart_client, "CORP_CACHE", cache_path)
    monkeypatch.setattr(
        dart_client,
        "_sample_corp_list",
        lambda: [{"corp_code": "005930", "corp_name": "삼성전자", "stock_code": "005930", "industry": ""}],
    )
    monkeypatch.setattr(dart_client, "_download_corp_list", lambda: downloaded)

    assert dart_client._load_corp_list() == downloaded
    assert json.loads(cache_path.read_text(encoding="utf-8")) == downloaded


def test_load_corp_list_live_mode_returns_empty_when_download_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(dart_client, "SETTINGS", SimpleNamespace(use_mock_dart=False))
    monkeypatch.setattr(dart_client, "CORP_CACHE", tmp_path / "corp_codes.json")
    monkeypatch.setattr(
        dart_client,
        "_sample_corp_list",
        lambda: [{"corp_code": "005930", "corp_name": "삼성전자", "stock_code": "005930", "industry": ""}],
    )
    monkeypatch.setattr(dart_client, "_download_corp_list", lambda: [])

    assert dart_client._load_corp_list() == []


def test_load_corp_list_mock_mode_keeps_sample_fallback(monkeypatch, tmp_path) -> None:
    samples = [
        {"corp_code": "005930", "corp_name": "삼성전자", "stock_code": "005930", "industry": ""},
    ]

    monkeypatch.setattr(dart_client, "SETTINGS", SimpleNamespace(use_mock_dart=True))
    monkeypatch.setattr(dart_client, "CORP_CACHE", tmp_path / "corp_codes.json")
    monkeypatch.setattr(dart_client, "_sample_corp_list", lambda: samples)

    assert dart_client._load_corp_list() == samples
