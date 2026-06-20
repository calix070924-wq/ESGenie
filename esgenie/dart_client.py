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
    """DART에서 K-ESG 수치 추출 — 구조화 API + 원문 텍스트(정규식) 결합.

    구조화 JSON API는 라벨-값이 분리돼 있어 표/문장 형식에 흔들리지 않는다(견고).
    따라서 구조화로 얻은 값은 정규식 추출보다 **우선** 적용한다.
    """
    kesg = _extract_kesg_from_text_sources(corp_code, year, ann_rcept_no)
    structured = _fetch_structured_esg(corp_code, year)
    kesg.update(structured)   # 구조화 API 값이 정규식 추출을 덮어씀(더 신뢰)
    return kesg


# ====================================================================
# 구조화 정기보고서 API — 거버넌스(G) 견고 추출
# ====================================================================

def _to_num(s: Any) -> float | None:
    """'9.7' / '1,234' / '-' / '' → float|None."""
    if s is None:
        return None
    t = str(s).replace(",", "").strip()
    if not t or t in {"-", "—", "N/A", "해당없음"}:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _fetch_structured_esg(corp_code: str, year: int) -> dict[str, dict]:
    """DART 정기보고서 구조화 API로 ESG 핵심 수치 추출.

    항목별 소스:
    - G-3-4 배당성향    : /alotMatter.json
    - G-1-2 사외이사비율 : /outcmpnyDrctrNdChangeSttus.json
    - G-1-4 여성이사비율 : /exctvSttus.json          [추가]
    - S-2-2 정규직비율   : /empSttus.json            [추가]
    - S-3-1 여성직원비율 : /empSttus.json            [추가]
    """
    out: dict[str, dict] = {}
    _WANT = {"G-3-4", "G-1-2", "G-1-4", "S-2-2", "S-3-1"}
    for y in (year, year - 1):       # 당해 미제출 대비 직전연도 폴백
        if "G-3-4" not in out:
            d = _dart_get("/alotMatter.json", corp_code=corp_code,
                          bsns_year=str(y), reprt_code="11011")
            payout = _pick_dividend_payout(d)
            if payout is not None:
                out["G-3-4"] = {"value": payout, "unit": "%",
                                "note": "DART 배당 구조화 API(현금배당성향)"}
        if "G-1-2" not in out:
            b = _dart_get("/outcmpnyDrctrNdChangeSttus.json", corp_code=corp_code,
                          bsns_year=str(y), reprt_code="11011")
            br = _pick_board_ratio(b)
            if br is not None:
                ratio, outside, total = br
                out["G-1-2"] = {"value": ratio, "unit": "%",
                                "note": f"DART 사외이사 구조화 API({outside}/{total}인)"}
        if "G-1-4" not in out:
            e = _dart_get("/exctvSttus.json", corp_code=corp_code,
                          bsns_year=str(y), reprt_code="11011")
            fr = _pick_female_director_ratio(e)
            if fr is not None:
                ratio, female, total = fr
                out["G-1-4"] = {"value": ratio, "unit": "%",
                                "note": f"DART 임원현황 API(여성 등기임원 {female}/{total}인)"}
        if "S-2-2" not in out or "S-3-1" not in out:
            emp = _dart_get("/empSttus.json", corp_code=corp_code,
                            bsns_year=str(y), reprt_code="11011")
            emp_data = _pick_employee_stats(emp)
            if emp_data:
                if "S-2-2" not in out and "regular_ratio" in emp_data:
                    out["S-2-2"] = {"value": emp_data["regular_ratio"], "unit": "%",
                                    "note": "DART 직원현황 API(정규직 비율)"}
                if "S-3-1" not in out and "female_ratio" in emp_data:
                    out["S-3-1"] = {"value": emp_data["female_ratio"], "unit": "%",
                                    "note": "DART 직원현황 API(여성 직원 비율)"}
        if _WANT <= out.keys():
            break
    return out


# ── 후보 없애기: 기존 함수명 별칭 유지 (하위 호환) ──────────────────────────────
_fetch_structured_governance = _fetch_structured_esg


def _pick_dividend_payout(data: dict | None) -> float | None:
    """alotMatter 응답에서 현금배당성향(%) 당기값. (연결) 우선, 수익률과 혼동 금지."""
    if not data:
        return None
    rows = data.get("list") or []
    # '(연결)현금배당성향' 우선 → 일반 '현금배당성향'
    for want in ("연결현금배당성향", "현금배당성향"):
        for it in rows:
            se = str(it.get("se", "")).replace(" ", "").replace("(", "").replace(")", "")
            if se.startswith(want) and "수익률" not in se:
                v = _to_num(it.get("thstrm"))
                if v is not None:
                    return v
    return None


def _pick_board_ratio(data: dict | None) -> tuple[float, int, int] | None:
    """outcmpnyDrctrNdChangeSttus 응답에서 사외이사 비율(%) = 사외이사 수 / 이사의 수."""
    if not data:
        return None
    for it in (data.get("list") or []):
        total = _to_num(it.get("drctr_co"))
        outside = _to_num(it.get("otcmp_drctr_co"))
        if total and outside is not None and total >= outside >= 0 and total > 0:
            return round(outside / total * 100, 1), int(outside), int(total)
    return None


def _pick_female_director_ratio(data: dict | None) -> tuple[float, int, int] | None:
    """exctvSttus 응답에서 여성 등기임원 비율(%) = 여성 등기임원 / 전체 등기임원."""
    if not data:
        return None
    rows = data.get("list") or []
    registered = [r for r in rows if "등기임원" in str(r.get("rgist_exctv_at", ""))]
    if not registered:
        return None
    female = sum(1 for r in registered if str(r.get("sexdstn", "")).strip() == "여")
    total = len(registered)
    if total == 0:
        return None
    return round(female / total * 100, 1), female, total


def _pick_employee_stats(data: dict | None) -> dict | None:
    """empSttus 응답에서 여성 직원 비율·정규직 비율 계산.

    반환: {"female_ratio": float, "regular_ratio": float}  — 없으면 None.
    """
    if not data:
        return None
    rows = data.get("list") or []
    if not rows:
        return None

    male_total = female_total = 0
    total_regular = total_all = 0

    for r in rows:
        sex = str(r.get("sexdstn", "")).strip()
        sm      = _to_num(r.get("sm")) or 0
        rgllbr  = _to_num(r.get("rgllbr_co")) or 0

        if sex == "여":
            female_total += sm
        elif sex == "남":
            male_total += sm
        total_regular += rgllbr
        total_all += sm

    result: dict = {}
    combined = male_total + female_total
    if combined > 0:
        result["female_ratio"] = round(female_total / combined * 100, 1)
    if total_all > 0:
        result["regular_ratio"] = round(total_regular / total_all * 100, 1)
    return result or None


def _extract_kesg_from_text_sources(corp_code: str, year: int, ann_rcept_no: str = "") -> dict[str, dict]:
    """사업보고서/지속가능경영보고서 '원문 텍스트'에서 정규식으로 K-ESG 수치 추출(폴백).

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

    # 4) 기업지배구조보고서 — G항목 상세 소스 (pblntf_ty 미지정, report_nm 필터링)
    #    DART 공시 특성상 유형코드가 없어 전체 공시에서 이름으로 찾음
    for try_year in [year + 1, year]:
        cg_list = _dart_get(
            "/list.json",
            corp_code=corp_code,
            bgn_de=f"{try_year}0101",
            end_de=f"{try_year}1231",
            page_count="30",
        )
        for rpt_item in (cg_list.get("list") or []) if cg_list else []:
            if "기업지배구조" in rpt_item.get("report_nm", ""):
                text = _fetch_report_zip_text(rpt_item.get("rcept_no", ""))
                if text:
                    kesg.update(_regex_extract_kesg(text))
        if any(k.startswith("G-") for k in kesg):
            break   # G항목 하나라도 찾으면 충분

    return kesg


_ESG_ZIP_KEYWORDS = ["온실가스", "tCO2", "에너지 사용", "폐기물", "용수", "재해율",
                     "Scope", "GHG", "재생에너지", "에너지소비",
                     "이사회", "사외이사", "주주총회", "감사위원회", "지배구조"]

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
    # ── G-1-4 여성 이사 수/비율 ────────────────────────────────────────────────
    ("G-1-4", "%",      r"여성\s*(?:이사|등기임원)[^\d]{0,60}([\d\.]+)\s*%", 1.0),
    ("G-1-4", "명",     r"여성\s*(?:이사|등기임원|임원)[^\d]{0,60}(\d+)\s*(?:인|명)", 1.0),
    # ── G-2-1 이사 출석률 ────────────────────────────────────────────────────
    ("G-2-1", "%",      r"출석률\s*:?\s*([\d\.]+)\s*%", 1.0),
    # ── G-2-3 이사회 안건 수 ────────────────────────────────────────────────
    ("G-2-3", "건",     r"(?:이사회\s*)?(?:안건|의안)\s*(?:처리|상정|심의)?\s*(?:건수\s*)?:?\s*(\d+)\s*건", 1.0),
    # ── G-3-1 주주총회 소집 공고 기간 ──────────────────────────────────────────
    ("G-3-1", "일",     r"(?:주주총회\s*소집\s*공고|소집\s*공고일)[^\d]{0,60}(\d+)\s*일\s*(?:전|이전)", 1.0),
    # ── G-6-1 지배구조 법규 위반 ───────────────────────────────────────────────
    ("G-6-1", "건",     r"(?:법규\s*위반|공정거래\s*위반|과징금\s*부과)[^\d]{0,60}(\d+)\s*건", 1.0),
]

# 사외이사 비율(G-1-2) — 사업보고서의 다양한 표현(표준표/문장/역순) 방어적 처리
# DART 표준표: "이사의 수  사외이사 수  사외이사 변동현황 …  5  3  - - -"
_BOARD_TABLE_RE = re.compile(r"이사의?\s*수\s*사외이사\s*수[^\d]{0,60}?(\d+)\s+(\d+)\b")
# 문장(역순): "… 3인의 사외이사 …"  /  정순: "사외이사 3인"
_OUTSIDE_REV_RE = re.compile(r"(\d+)\s*[인명]\s*의\s*사외이사")
_OUTSIDE_FWD_RE = re.compile(r"사외이사\s*(\d+)\s*[인명]")
_INSIDE_RE      = re.compile(r"(\d+)\s*[인명]\s*의\s*사내이사|사내이사\s*(\d+)\s*[인명]")
_TOTAL_RES      = [
    re.compile(r"이사회(?:는|가)?\s*총\s*(\d+)\s*[명인]"),       # "이사회는 총 5명"
    re.compile(r"(?:이사의?\s*총?\s*수|등기이사\s*수|총\s*이사\s*수)\s*[:은는]?\s*(\d+)\s*[명인]"),
]


def _extract_board_ratio(text: str) -> tuple[float, int, int] | None:
    """사외이사 비율(%) = 사외이사 / 전체이사. 표준표 → 문장 순으로 시도."""
    # 1) DART 표준표("이사의 수  사외이사 수 … 총 사외")
    tm = _BOARD_TABLE_RE.search(text)
    if tm:
        total, outside = float(tm.group(1)), float(tm.group(2))
        if total >= outside > 0:
            return round(outside / total * 100, 1), int(outside), int(total)

    # 2) 사외이사 수 (역순 우선: "3인의 사외이사", 없으면 정순 "사외이사 3인")
    om = _OUTSIDE_REV_RE.search(text) or _OUTSIDE_FWD_RE.search(text)
    if not om:
        return None
    outside = float(om.group(1))

    total: float | None = None
    for re_t in _TOTAL_RES:
        t = re_t.search(text)
        if t:
            total = float(t.group(1))
            break
    if total is None:
        im = _INSIDE_RE.search(text)
        if im:
            inside = float(im.group(1) or im.group(2))
            total = outside + inside
    if total and total >= outside > 0:
        return round(outside / total * 100, 1), int(outside), int(total)
    return None


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

    # G-1-2 후처리: 사외이사 비율(%) 재계산. 계산 가능하면 확정, 아니면 잘못된 count 제거(→ insufficient).
    br = _extract_board_ratio(text)
    if br:
        ratio, outside, total = br
        result["G-1-2"] = {
            "value": ratio,
            "unit": "%",
            "note": f"DART 사외이사 {outside}/{total}인 — 정규식 추출",
        }
    elif "G-1-2" in result:
        del result["G-1-2"]  # 비율 산출 불가 → 잘못된 카운트값 남기지 않음

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
