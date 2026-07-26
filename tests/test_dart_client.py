from __future__ import annotations

import json
import logging
from types import SimpleNamespace

from esgenie import dart_client

_LOGGER_NAME = "esgenie.dart_client"


class _FakeResponse:
    """requests.Response 최소 대역 — status_code + json() 만 필요."""

    def __init__(self, status_code: int = 200, json_data: dict | None = None):
        self.status_code = status_code
        self._json_data = json_data or {}

    def json(self):
        return self._json_data


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


# ---- 침묵 폴백 개선 — 로그 구분 + fetch_error ------------------------------

def test_download_corp_list_network_error_logs_warning_and_returns_empty(monkeypatch, caplog) -> None:
    """148라인 except Exception 블록이 실제로 실행되는지 확인 (함수 자체를 우회하지 않음)."""
    def _raise(*_a, **_kw):
        raise ConnectionError("network down")

    monkeypatch.setattr(dart_client.requests, "get", _raise)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = dart_client._download_corp_list()

    assert result == []
    assert any("다운로드 실패" in r.message for r in caplog.records)


def test_dart_get_network_error_logs_warning(monkeypatch, caplog) -> None:
    def _raise(*_a, **_kw):
        raise TimeoutError("timed out")

    monkeypatch.setattr(dart_client.requests, "get", _raise)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = dart_client._dart_get("/company.json", corp_code="005930")

    assert result is None
    assert any("DART API 호출 실패" in r.message for r in caplog.records)


def test_dart_get_non_200_status_logs_warning(monkeypatch, caplog) -> None:
    monkeypatch.setattr(
        dart_client.requests, "get",
        lambda *_a, **_kw: _FakeResponse(status_code=500),
    )

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = dart_client._dart_get("/company.json", corp_code="005930")

    assert result is None
    assert any("비정상 HTTP" in r.message for r in caplog.records)


def test_dart_get_status_013_no_data_is_not_a_warning(monkeypatch, caplog) -> None:
    """status=013(조회된 데이터 없음)은 정상 빈값 — warning이 아니라 debug로만 남는다."""
    monkeypatch.setattr(
        dart_client.requests, "get",
        lambda *_a, **_kw: _FakeResponse(
            status_code=200, json_data={"status": "013", "message": "조회된 데이터가 없습니다"},
        ),
    )

    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        result = dart_client._dart_get("/company.json", corp_code="005930")

    assert result is None
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings == []
    assert any(r.levelno == logging.DEBUG for r in caplog.records)


def test_dart_get_auth_error_status_logs_warning(monkeypatch, caplog) -> None:
    """status=020(인증/사용한도 오류 등) 같은 진짜 오류는 warning으로 구분."""
    monkeypatch.setattr(
        dart_client.requests, "get",
        lambda *_a, **_kw: _FakeResponse(
            status_code=200, json_data={"status": "020", "message": "사용한도 초과"},
        ),
    )

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = dart_client._dart_get("/company.json", corp_code="005930")

    assert result is None
    assert any("DART API 오류" in r.message for r in caplog.records)


def test_load_report_dart_failure_falls_back_with_fetch_error(monkeypatch, caplog) -> None:
    monkeypatch.setattr(dart_client, "SETTINGS", SimpleNamespace(use_mock_dart=False, dart_api_key="fake-key"))

    def _raise(*_a, **_kw):
        raise RuntimeError("DART 기업 개황 조회 실패: 005930")

    monkeypatch.setattr(dart_client, "_fetch_from_dart", _raise)

    fake_sample = dart_client.CompanyReport(
        corp_code="005930", corp_name="삼성전자", industry="전자",
        report_year=2024, financials={}, kesg_data={}, raw_text_snippets=[],
        source="sample",
    )
    monkeypatch.setattr(dart_client, "load_sample_report", lambda corp_code: fake_sample)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = dart_client.load_report("005930")

    assert result.source != "dart_api"
    assert result.fetch_error == "DART 기업 개황 조회 실패: 005930"
    assert any("대체 데이터로 폴백" in r.message for r in caplog.records)


def test_load_report_dart_success_keeps_fetch_error_none(monkeypatch) -> None:
    monkeypatch.setattr(dart_client, "SETTINGS", SimpleNamespace(use_mock_dart=False, dart_api_key="fake-key"))

    fake_success = dart_client.CompanyReport(
        corp_code="005930", corp_name="삼성전자", industry="전자",
        report_year=2024, financials={}, kesg_data={}, raw_text_snippets=[],
        source="dart_api",
    )
    monkeypatch.setattr(dart_client, "_fetch_from_dart", lambda corp_code, report_year: fake_success)

    result = dart_client.load_report("005930")

    assert result.source == "dart_api"
    assert result.fetch_error is None


def test_company_report_fetch_error_defaults_to_none() -> None:
    report = dart_client.CompanyReport(
        corp_code="005930", corp_name="삼성전자", industry="전자",
        report_year=2024, financials={}, kesg_data={}, raw_text_snippets=[],
        source="dart_api",
    )
    assert report.fetch_error is None
