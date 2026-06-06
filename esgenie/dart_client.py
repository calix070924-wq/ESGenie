"""DART OpenAPI client — 실제 API 연동 + 샘플 폴백.

우선순위:
  1) DART_API_KEY 있으면 실제 DART OpenAPI 호출
  2) 없으면 data/sample_dart/ 샘플 JSON 사용
  3) 샘플도 없으면 빈 CompanyReport 반환 (OCR 증빙만으로 동작)

회사 검색:
  search_companies(query) — DART 기업 코드 목록을 로컬 캐시로 관리,
  회사명으로 검색해 (corp_code, corp_name, industry) 목록 반환.
  캐시가 없거나 오래됐으면 DART에서 다시 다운로드.
"""
from __future__ import annotations

import io
import json
import os
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import requests

from .config import SAMPLE_DART_DIR, SETTINGS

DART_BASE   = "https://opendart.fss.or.kr/api"
CORP_CACHE  = SAMPLE_DART_DIR.parent / "_cache" / "corp_codes.json"   # 로컬 캐시
CACHE_DAYS  = 30                                                        # 갱신 주기


# ====================================================================
# 데이터클래스
# ====================================================================

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
        return entry.get("value", default) if entry else default

    def to_context_dict(self) -> dict[str, Any]:
        return {
            "corp_name":  self.corp_name,
            "corp_code":  self.corp_code,
            "industry":   self.industry,
            "year":       self.report_year,
            "kesg_data":  self.kesg_data,
        }


# ====================================================================
# 회사 검색
# ====================================================================

def search_companies(query: str, limit: int = 10) -> list[dict[str, str]]:
    """회사명으로 DART 기업 검색.

    Returns: [{"corp_code": ..., "corp_name": ..., "stock_code": ..., "industry": ...}, ...]
    """
    query = query.strip()
    if not query:
        return []

    all_corps = _load_corp_list()
    q = query.lower()
    matches = [c for c in all_corps if q in c["corp_name"].lower()]
    return matches[:limit]


def _load_corp_list() -> list[dict[str, str]]:
    """DART 기업 코드 목록 — 캐시 우선, 없으면 다운로드."""
    if CORP_CACHE.exists():
        import time
        age_days = (time.time() - CORP_CACHE.stat().st_mtime) / 86400
        if age_days < CACHE_DAYS:
            with open(CORP_CACHE, encoding="utf-8") as f:
                return json.load(f)

    if SETTINGS.use_mock_dart:
        return _sample_corp_list()

    corps = _download_corp_list()
    if corps:
        CORP_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with open(CORP_CACHE, "w", encoding="utf-8") as f:
            json.dump(corps, f, ensure_ascii=False)
        return corps

    return _sample_corp_list()


def _download_corp_list() -> list[dict[str, str]]:
    """DART에서 기업코드 ZIP(XML) 다운로드 후 파싱."""
    try:
        url = f"{DART_BASE}/corpCode.xml"
        r = requests.get(url, params={"crtfc_key": SETTINGS.dart_api_key}, timeout=15)
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            xml_name = next(n for n in z.namelist() if n.endswith(".xml"))
            xml_bytes = z.read(xml_name)
        root = ET.fromstring(xml_bytes)
        corps = []
        for item in root.findall("list"):
            corp_code  = (item.findtext("corp_code") or "").strip()
            corp_name  = (item.findtext("corp_name") or "").strip()
            stock_code = (item.findtext("stock_code") or "").strip()
            if corp_code and corp_name:
                corps.append({
                    "corp_code":  corp_code,
                    "corp_name":  corp_name,
                    "stock_code": stock_code,
                    "industry":   "",   # 개황 API에서 별도 조회
                })
        return corps
    except Exception:
        return []


def _sample_corp_list() -> list[dict[str, str]]:
    """API 키 없을 때 샘플 파일 기반 목록."""
    corps = []
    for f in sorted(SAMPLE_DART_DIR.glob("*.json")):
        with open(f, encoding="utf-8") as fp:
            obj = json.load(fp)
        corps.append({
            "corp_code":  obj["corp_code"],
            "corp_name":  obj["corp_name"],
            "stock_code": obj["corp_code"],
            "industry":   obj.get("industry", ""),
        })
    return corps


# ====================================================================
# 보고서 로드
# ====================================================================

def list_sample_companies() -> list[dict[str, str]]:
    return _sample_corp_list()


def load_sample_report(corp_code: str) -> CompanyReport:
    matches = [f for f in SAMPLE_DART_DIR.glob("*.json") if f.stem.startswith(corp_code)]
    if not matches:
        raise FileNotFoundError(f"샘플 없음: {corp_code}")
    with open(matches[0], encoding="utf-8") as fp:
        obj = json.load(fp)
    return _from_json(obj, source="sample")


def load_report(corp_code: str, report_year: int | None = None) -> CompanyReport:
    """Best-effort 로드.

    DART_API_KEY 있으면 실제 API → 없으면 샘플 → 없으면 빈 보고서.
    """
    if not SETTINGS.use_mock_dart:
        try:
            return _fetch_from_dart(corp_code, report_year)
        except Exception:
            pass

    # 샘플 폴백
    try:
        return load_sample_report(corp_code)
    except FileNotFoundError:
        pass

    # 빈 보고서 (OCR 증빙만 사용)
    return _empty_report(corp_code, report_year or 2025)


# ====================================================================
# 실제 DART API 호출
# ====================================================================

def _fetch_from_dart(corp_code: str, report_year: int | None) -> CompanyReport:
    year = report_year or 2025

    # 1) 기업 개황
    info = _dart_get("/company.json", corp_code=corp_code) or {}
    corp_name = info.get("corp_name", corp_code)
    industry  = info.get("induty_code", "")

    # 2) 재무제표 (11011=사업보고서)
    fin = _dart_get(
        "/fnlttSinglAcnt.json",
        corp_code=corp_code,
        bsns_year=str(year),
        reprt_code="11011",
        fs_div="CFS",
    ) or {}
    financials, raw_snippets = _parse_financials(fin)

    # 3) ESG 수치 — DART 사업보고서 기타 공시 섹션에서 파싱
    kesg_data = _extract_kesg_from_dart(corp_code, year)

    return CompanyReport(
        corp_code=corp_code,
        corp_name=corp_name,
        industry=industry,
        report_year=year,
        financials=financials,
        kesg_data=kesg_data,
        raw_text_snippets=raw_snippets,
        source="dart_api",
    )


def _dart_get(endpoint: str, **params) -> dict[str, Any] | None:
    try:
        r = requests.get(
            DART_BASE + endpoint,
            params={"crtfc_key": SETTINGS.dart_api_key, **params},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "000":
                return data
    except Exception:
        pass
    return None


def _parse_financials(fin_data: dict) -> tuple[dict, list[str]]:
    """DART 재무제표 응답 → financials dict + raw_text_snippets."""
    financials: dict[str, Any] = {}
    snippets: list[str] = []
    for item in (fin_data.get("list") or []):
        account = item.get("account_nm", "")
        amount  = item.get("thstrm_amount", "")
        if account and amount:
            financials[account] = amount
            snippets.append(f"{account}: {amount}원")
    return financials, snippets


def _extract_kesg_from_dart(corp_code: str, year: int) -> dict[str, dict]:
    """DART 공시(지속가능경영보고서·사업보고서 ESG 섹션)에서 K-ESG 수치 추출.

    현재는 에너지·온실가스·용수·폐기물 4개 항목을 DART 공시 텍스트에서 정규식으로 추출.
    공시가 없거나 파싱 실패 시 빈 dict 반환 → OCR 채널로 보완.
    """
    kesg: dict[str, dict] = {}

    # 환경 공시 보고서 목록 조회 (지속가능경영보고서: pblntf_ty=F)
    rpt = _dart_get(
        "/list.json",
        corp_code=corp_code,
        bgn_de=f"{year}0101",
        end_de=f"{year}1231",
        pblntf_ty="F",          # 기타공시 (지속가능경영보고서 포함)
        page_count="5",
    )
    if not rpt:
        return kesg

    # 보고서 원문 텍스트 파싱 (간략 버전 — 실제는 rcp_no로 원문 조회)
    for rpt_item in (rpt.get("list") or [])[:2]:
        rcp_no = rpt_item.get("rcept_no", "")
        text   = _fetch_report_text(rcp_no)
        if not text:
            continue
        kesg.update(_regex_extract_kesg(text))

    return kesg


def _fetch_report_text(rcept_no: str) -> str:
    """DART 보고서 원문(HTML) 텍스트 다운로드 (간략)."""
    try:
        # 보고서 문서 목록
        doc_list = _dart_get("/document.json", rcept_no=rcept_no)
        if not doc_list:
            return ""
        # 첫 번째 문서 텍스트
        docs = doc_list.get("list") or []
        if not docs:
            return ""
        dcm_no = docs[0].get("dcm_no", "")
        r = requests.get(
            f"https://dart.fss.or.kr/report/viewer.do",
            params={"rcpNo": rcept_no, "dcmNo": dcm_no, "eleId": "0", "offset": "0",
                    "length": "0", "dtd": "dart3.xsd"},
            timeout=10,
        )
        # HTML 태그 제거
        return re.sub(r"<[^>]+>", " ", r.text)
    except Exception:
        return ""


# K-ESG 핵심 수치 정규식 패턴
_KESG_PATTERNS: list[tuple[str, str, str]] = [
    # (kesg_code, unit, regex)
    ("E-3-1", "tCO2eq", r"(온실가스|Scope\s*1\+2|GHG)[^\d]{0,30}([\d,\.]+)\s*(?:천\s*)?tCO2"),
    ("E-4-1", "kWh",    r"(에너지\s*사용|전력\s*사용)[^\d]{0,30}([\d,\.]+)\s*(?:천\s*)?kWh"),
    ("E-5-1", "m³",     r"(용수\s*사용|취수)[^\d]{0,30}([\d,\.]+)\s*(?:천\s*)?m³"),
    ("E-6-1", "ton",    r"(폐기물\s*발생|총\s*폐기물)[^\d]{0,30}([\d,\.]+)\s*(?:천\s*)?(?:톤|ton)"),
]

def _regex_extract_kesg(text: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for code, unit, pattern in _KESG_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            raw = m.group(2).replace(",", "")
            try:
                value = float(raw)
                result[code] = {"value": value, "unit": unit, "note": f"DART 원문 정규식 추출"}
            except ValueError:
                pass
    return result


# ====================================================================
# 헬퍼
# ====================================================================

def _from_json(obj: dict, source: str) -> CompanyReport:
    return CompanyReport(
        corp_code=obj["corp_code"],
        corp_name=obj["corp_name"],
        industry=obj.get("industry", ""),
        report_year=obj.get("report_year", 2025),
        financials=obj.get("financials", {}),
        kesg_data=obj.get("kesg_data", {}),
        raw_text_snippets=obj.get("raw_text_snippets", []),
        source=source,
    )


def _empty_report(corp_code: str, report_year: int) -> CompanyReport:
    """DART 없는 비상장 중소기업용 — OCR 증빙만으로 동작."""
    return CompanyReport(
        corp_code=corp_code,
        corp_name=corp_code,
        industry="",
        report_year=report_year,
        financials={},
        kesg_data={},
        raw_text_snippets=[],
        source="manual",
    )


def fetch_dart_corp_info(corp_code: str) -> dict[str, Any] | None:
    if SETTINGS.use_mock_dart:
        return None
    return _dart_get("/company.json", corp_code=corp_code)
