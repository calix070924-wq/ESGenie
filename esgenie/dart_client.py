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

    정렬 우선순위: 정확 일치 → 시작 일치 → 부분 포함, 같은 그룹 내에서는 상장사 우선.

    Returns: [{"corp_code": ..., "corp_name": ..., "stock_code": ..., "industry": ...}, ...]
    """
    query = query.strip()
    if not query:
        return []

    all_corps = _load_corp_list()
    q = query.lower()
    matches = [c for c in all_corps if q in c["corp_name"].lower()]

    def _sort_key(c: dict) -> tuple:
        name = c["corp_name"].lower()
        exact = 0 if name == q else 1
        starts = 0 if name.startswith(q) else 1
        listed = 0 if c.get("stock_code") else 1
        return (exact, starts, listed, name)

    matches.sort(key=_sort_key)
    return matches[:limit]


def _load_corp_list() -> list[dict[str, str]]:
    """DART 기업 코드 목록 — 캐시 우선, 없으면 다운로드.

    샘플 기업(data/sample_dart/*.json)은 항상 목록 앞에 포함.
    """
    samples = _sample_corp_list()
    sample_codes = {s["corp_code"] for s in samples}

    if CORP_CACHE.exists():
        import time
        age_days = (time.time() - CORP_CACHE.stat().st_mtime) / 86400
        if age_days < CACHE_DAYS:
            with open(CORP_CACHE, encoding="utf-8") as f:
                cached = json.load(f)
            # 샘플 기업을 앞에 붙이되 중복 제거
            extra = [s for s in samples if s["corp_code"] not in {c["corp_code"] for c in cached}]
            return extra + cached

    if SETTINGS.use_mock_dart:
        return samples

    corps = _download_corp_list()
    if corps:
        CORP_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with open(CORP_CACHE, "w", encoding="utf-8") as f:
            json.dump(corps, f, ensure_ascii=False)
        extra = [s for s in samples if s["corp_code"] not in {c["corp_code"] for c in corps}]
        return extra + corps

    return samples


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

    # 1) 기업 개황 — API 응답 없으면 샘플 폴백을 위해 예외 발생
    info = _dart_get("/company.json", corp_code=corp_code)
    if info is None:
        raise RuntimeError(f"DART 기업 개황 조회 실패: {corp_code}")
    corp_name = info.get("corp_name", corp_code)
    industry  = info.get("induty_code", "")

    # 2) 재무제표 (11011=사업보고서) — rcept_no도 함께 추출
    fin = _dart_get(
        "/fnlttSinglAcnt.json",
        corp_code=corp_code,
        bsns_year=str(year),
        reprt_code="11011",
        fs_div="CFS",
    ) or {}
    financials, raw_snippets = _parse_financials(fin)

    # 재무제표 응답에서 사업보고서 rcept_no 추출 (원문 다운로드에 재사용)
    fin_list = fin.get("list") or []
    ann_rcept_no = fin_list[0].get("rcept_no", "") if fin_list else ""

    # 3) ESG 수치 — DART 사업보고서 원문 ZIP에서 파싱
    kesg_data = _extract_kesg_from_dart(corp_code, year, ann_rcept_no)

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


def _extract_kesg_from_dart(corp_code: str, year: int, ann_rcept_no: str = "") -> dict[str, dict]:
    """DART 공시에서 K-ESG 수치 최대한 추출.

    우선순위:
      1) 재무제표에서 이미 얻은 rcept_no로 사업보고서 원문 직접 다운로드
      2) 지속가능경영보고서 (pblntf_ty=F) 검색 — 있는 기업은 여기서 상세 ESG 데이터
      3) 공시 목록 검색으로 사업보고서 rcept_no 확보 후 다운로드
    """
    kesg: dict[str, dict] = {}

    # 1) 재무제표와 동일한 사업보고서 원문 직접 다운로드 (가장 신뢰성 높음)
    if ann_rcept_no:
        text = _fetch_report_zip_text(ann_rcept_no)
        if text:
            kesg.update(_regex_extract_kesg(text))
        if kesg:
            return kesg

    # 2) 지속가능경영보고서 우선 시도 (접수일 기준: year+1년 상반기)
    for try_year in [year + 1, year]:
        rpt = _dart_get(
            "/list.json",
            corp_code=corp_code,
            bgn_de=f"{try_year}0101",
            end_de=f"{try_year}0630",
            pblntf_ty="F",
            page_count="5",
        )
        for rpt_item in (rpt.get("list") or [])[:2] if rpt else []:
            text = _fetch_report_zip_text(rpt_item.get("rcept_no", ""))
            if text:
                kesg.update(_regex_extract_kesg(text))
        if kesg:
            return kesg

    # 3) 공시 목록에서 사업보고서 찾아 다운로드 (접수일 기준으로 검색)
    for try_year in [year + 1, year]:
        ann = _dart_get(
            "/list.json",
            corp_code=corp_code,
            bgn_de=f"{try_year}0101",
            end_de=f"{try_year}0630",
            pblntf_ty="A",
            page_count="10",
        )
        for rpt_item in (ann.get("list") or [] if ann else []):
            if "사업보고서" in rpt_item.get("report_nm", ""):
                text = _fetch_report_zip_text(rpt_item.get("rcept_no", ""))
                if text:
                    kesg.update(_regex_extract_kesg(text))
                if kesg:
                    return kesg

    return kesg


_ESG_ZIP_KEYWORDS = ["온실가스", "tCO2", "에너지 사용", "폐기물", "용수", "재해율",
                     "Scope", "GHG", "재생에너지", "에너지소비"]

def _fetch_report_zip_text(rcept_no: str, max_chars: int = 800000) -> str:
    """DART 공시 원문 ZIP 다운로드 (/document.xml) → XML/HTML 텍스트 추출.

    /document.json 엔드포인트는 존재하지 않음(status 101).
    올바른 엔드포인트는 /document.xml (ZIP 반환).
    ESG 키워드가 포함된 문서를 우선 처리하고, 없으면 전체 문서를 순서대로 처리.
    """
    if not rcept_no:
        return ""
    try:
        r = requests.get(
            DART_BASE + "/document.xml",
            params={"crtfc_key": SETTINGS.dart_api_key, "rcept_no": rcept_no},
            timeout=30,
        )
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            docs: list[tuple[str, str]] = []  # (name, text)
            for name in sorted(z.namelist()):
                if not name.lower().endswith((".xml", ".htm", ".html")):
                    continue
                raw = z.read(name).decode("utf-8", errors="ignore")
                text = re.sub(r"<[^>]+>", " ", raw)
                text = re.sub(r"\s+", " ", text).strip()
                if text:
                    docs.append((name, text))

            # ESG 키워드 포함 문서 우선 → 나머지 순으로 합산
            priority = [t for _, t in docs if any(k in t for k in _ESG_ZIP_KEYWORDS)]
            rest     = [t for _, t in docs if not any(k in t for k in _ESG_ZIP_KEYWORDS)]
            combined = "\n".join(priority + rest)
            return combined[:max_chars]
    except Exception:
        return ""


# K-ESG 핵심 수치 정규식 패턴 (사업보고서 + 지속가능경영보고서 다양한 표현 커버)
# tuple: (kesg_code, unit, regex, scale)  scale: 실제 단위로 환산 배수 (예: 만톤→tCO2eq = 10000)
_KESG_PATTERNS: list[tuple[str, str, str, float]] = [
    # ── E-3-1 온실가스 ──────────────────────────────────────────────────
    # 사업보고서 배출권 공시 형식: 배출량 추정치(단위: 만톤(tCO2-eq)) 1,827
    # 배출량 추정치(실제 배출량)를 우선 매칭 — 할당량보다 신뢰도 높음
    ("E-3-1", "tCO2eq", r"배출량\s*추정치\s*\([^\)]+\(tCO2[^\)]+\)\s*\)\s*([\d,\.]+)", 10000.0),
    # 지속가능경영보고서 형식
    ("E-3-1", "tCO2eq", r"(?:온실가스|GHG|Scope\s*1\s*[\+&]\s*2|탄소배출)[^\d]{0,60}([\d,\.]+)\s*(?:천\s*)?tCO2", 1.0),
    ("E-3-1", "tCO2eq", r"([\d,\.]+)\s*(?:천\s*)?tCO2eq", 1.0),
    ("E-3-1", "tCO2eq", r"([\d,\.]+)\s*만톤\s*(?:CO2|tCO2|\(tCO2)", 10000.0),
    # ── E-4-1 에너지 ────────────────────────────────────────────────────
    ("E-4-1", "kWh",    r"(?:에너지\s*사용|전력\s*사용|전력소비|에너지소비)[^\d]{0,40}([\d,\.]+)\s*(?:천\s*)?(?:kWh|KWh)", 1.0),
    ("E-4-1", "TJ",     r"(?:에너지\s*사용|총\s*에너지)[^\d]{0,40}([\d,\.]+)\s*TJ", 1.0),
    ("E-4-1", "kWh",    r"([\d,\.]+)\s*(?:천\s*)?kWh", 1.0),
    # ── E-5-1 용수 ──────────────────────────────────────────────────────
    ("E-5-1", "m³",     r"(?:용수\s*사용|취수량|물\s*사용)[^\d]{0,40}([\d,\.]+)\s*(?:천\s*)?m[³3]", 1.0),
    ("E-5-1", "ton",    r"(?:용수\s*사용|취수량)[^\d]{0,40}([\d,\.]+)\s*(?:천\s*)?(?:톤|ton)", 1.0),
    # ── E-6-1 폐기물 ────────────────────────────────────────────────────
    ("E-6-1", "ton",    r"(?:폐기물\s*발생|총\s*폐기물|폐기물\s*배출)[^\d]{0,40}([\d,\.]+)\s*(?:천\s*)?(?:톤|ton)", 1.0),
    ("E-6-1", "ton",    r"(?:매립|소각|재활용)[^\d]{0,40}([\d,\.]+)\s*(?:천\s*)?(?:톤|ton)", 1.0),
    # ── E-4-2 재생에너지 ─────────────────────────────────────────────────
    ("E-4-2", "%",      r"(?:재생\s*에너지|재생에너지\s*비율|신재생)[^\d]{0,40}([\d,\.]+)\s*%", 1.0),
    # ── S-3-1 안전 (재해율) ───────────────────────────────────────────────
    ("S-3-1", "%",      r"(?:산재율|재해율|사고율)[^\d]{0,30}([\d,\.]+)\s*%", 1.0),
    ("S-3-1", "건",     r"(?:산재|재해|사고\s*건수)[^\d]{0,30}([\d,\.]+)\s*건", 1.0),
    # ── G-3-4 배당성향 (사업보고서에서 추출 가능) ───────────────────────────
    ("G-3-4", "%",      r"(?:연결)?현금배당성향\s*\(?\s*%?\s*\)?\s*([\d\.]+)", 1.0),
    # ── G-1-2 사외이사 비율 ─────────────────────────────────────────────────
    # "사내이사 N인...사외이사 M인...총 T인" 형식에서 비율 계산
    ("G-1-2", "%",      r"사외이사\s*(\d+)\s*인", 1.0),   # count만 추출, 비율은 후처리
    # ── G-1-4 여성 이사 수 ──────────────────────────────────────────────────
    ("G-1-4", "명",     r"여성\s*(?:이사|임원)[^\d]{0,50}(\d+)\s*(?:인|명|개)", 1.0),
    # ── G-2-1 이사 출석률 ────────────────────────────────────────────────────
    ("G-2-1", "%",      r"출석률\s*:?\s*([\d\.]+)\s*%", 1.0),
]

_BOARD_RATIO_PATTERN = re.compile(
    r"사외이사\s*(\d+)\s*인.*?총\s*(\d+)\s*인", re.DOTALL
)

def _regex_extract_kesg(text: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for code, unit, pattern, scale in _KESG_PATTERNS:
        if code in result:
            continue
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            raw = m.group(1).replace(",", "")
            try:
                value = float(raw) * scale
                result[code] = {"value": value, "unit": unit, "note": "DART 원문 정규식 추출"}
            except ValueError:
                pass

    # G-1-2 후처리: 사외이사 수 / 전체 이사 수 → 비율(%)
    if "G-1-2" in result:
        bm = _BOARD_RATIO_PATTERN.search(text)
        if bm:
            outside = float(bm.group(1))
            total   = float(bm.group(2))
            if total > 0:
                result["G-1-2"] = {
                    "value": round(outside / total * 100, 1),
                    "unit": "%",
                    "note": f"DART 사외이사 {int(outside)}/{int(total)}인 — 정규식 추출",
                }

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
