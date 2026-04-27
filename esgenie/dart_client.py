"""DART OpenAPI client with offline-sample fallback.

키가 있으면 DART API를 호출, 없으면 `data/sample_dart/`의 캐시된 JSON을 사용.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .config import SAMPLE_DART_DIR, SETTINGS


@dataclass
class CompanyReport:
    corp_code: str
    corp_name: str
    industry: str
    report_year: int
    financials: dict[str, Any]
    kesg_data: dict[str, dict[str, Any]]
    raw_text_snippets: list[str]
    source: str

    def kesg_value(self, code: str, default: Any = None) -> Any:
        entry = self.kesg_data.get(code)
        if not entry:
            return default
        return entry.get("value", default)

    def to_context_dict(self) -> dict[str, Any]:
        return {
            "corp_name": self.corp_name,
            "corp_code": self.corp_code,
            "industry": self.industry,
            "year": self.report_year,
            "kesg_data": self.kesg_data,
        }


def list_sample_companies() -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for f in sorted(SAMPLE_DART_DIR.glob("*.json")):
        with open(f, encoding="utf-8") as fp:
            obj = json.load(fp)
        out.append({
            "corp_code": obj["corp_code"],
            "corp_name": obj["corp_name"],
            "industry": obj["industry"],
            "path": str(f),
        })
    return out


def load_sample_report(corp_code: str) -> CompanyReport:
    matches = [f for f in SAMPLE_DART_DIR.glob("*.json") if f.stem.startswith(corp_code)]
    if not matches:
        raise FileNotFoundError(f"샘플 데이터를 찾을 수 없음: {corp_code}")
    with open(matches[0], encoding="utf-8") as fp:
        obj = json.load(fp)
    return CompanyReport(
        corp_code=obj["corp_code"],
        corp_name=obj["corp_name"],
        industry=obj["industry"],
        report_year=obj["report_year"],
        financials=obj.get("financials", {}),
        kesg_data=obj.get("kesg_data", {}),
        raw_text_snippets=obj.get("raw_text_snippets", []),
        source=obj.get("source", "sample"),
    )


def fetch_dart_corp_info(corp_code: str) -> dict[str, Any] | None:
    """Live DART 기업 개황 조회. 실패 시 None."""
    if SETTINGS.use_mock_dart:
        return None
    try:
        url = "https://opendart.fss.or.kr/api/company.json"
        r = requests.get(
            url,
            params={"crtfc_key": SETTINGS.dart_api_key, "corp_code": corp_code},
            timeout=5,
        )
        if r.status_code == 200:
            return r.json()
    except Exception:
        return None
    return None


def load_report(corp_code: str) -> CompanyReport:
    """Best-effort load — prefers sample data for deterministic demo."""
    return load_sample_report(corp_code)
