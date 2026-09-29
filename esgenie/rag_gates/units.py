"""Unit and numeric normalization utilities for grounding gate checks."""
from __future__ import annotations

import math
import re

# ── Number parsing ──────────────────────────────────────────────────────────

_COMMA_NUM_RE = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})*(?:\.\d+)?$")
_SCIENTIFIC_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?[eE][+-]?\d+$")
_PLAIN_NUM_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")

_KR_MULTIPLIERS = {"만": 1e4, "억": 1e8, "조": 1e12}
_KR_SUFFIX_RE = re.compile(
    r"^([+-]?\d[\d,]*(?:\.\d+)?)\s*(만|억|조)$"
)


def parse_number(text: str) -> float | None:
    """Parse a numeric string into float, handling commas, scientific notation, and Korean multipliers."""
    s = text.strip()
    if not s:
        return None

    # Korean multiplier suffix (e.g. "1.5만", "3억")
    m = _KR_SUFFIX_RE.match(s)
    if m:
        base = _parse_bare(m.group(1))
        if base is None:
            return None
        return base * _KR_MULTIPLIERS[m.group(2)]

    return _parse_bare(s)


def _parse_bare(s: str) -> float | None:
    """Parse plain, comma-separated, or scientific notation number."""
    if _COMMA_NUM_RE.match(s):
        return float(s.replace(",", ""))
    if _SCIENTIFIC_RE.match(s):
        return float(s)
    if _PLAIN_NUM_RE.match(s):
        return float(s)
    return None


# ── Unit normalization ──────────────────────────────────────────────────────

# 2026-09-29: 두 보고서 지표 7,201건의 단위를 전수로 넣어 보니 사전 밖이 모비스 603건
# (15.0%)·삼성전기 897건(28.3%)이었다. 그 중 금액 단위만 251건이고 **`천 원` 230건·`조 원`
# 11건이 사전에 없었다** — `억 원`·`백만 원`은 있는데 빠진 칸이다. 사전 밖 단위는 대표값
# 선택의 단위 축에서 최하 순위(2=그 외/미상)로 떨어지고 환산 경로도 타지 못한다.
#
# 계열을 갈라 넣는 이유(합치면 사고가 난다):
#   - 원화와 달러는 **다른 군**이다. 환율을 모르므로 섞으면 없는 환산이 생긴다.
#   - 부피(m³·L)와 질량(t)은 **다른 군**이다. 물만 밀도 1 t/m³로 같이 보는데, 그 예외는
#     `node_select._water_mass_volume_pair`가 E-5-1·E-5-2에만 적용한다. 전역에서 합치면
#     폐기물 톤이 m³로 환산된다.
#   - `tCO2eq`는 질량군과 별개다(배출량과 무게는 같은 축이 아니다).
_UNIT_ALIASES: dict[str, str] = {
    # Mass / emissions
    "t": "t",
    "ton": "t",
    "tons": "t",
    "톤": "t",
    "kg": "kg",
    "킬로그램": "kg",
    "g": "g",
    "그램": "g",
    "kt": "kt",
    "킬로톤": "kt",
    "만톤": "만t",           # 실측 5건 — 배율을 못 읽으면 값이 10,000배 작게 실린다
    "만t": "만t",
    "tco2eq": "tCO2eq",
    "tco2": "tCO2eq",
    "tco2e": "tCO2eq",       # 삼성전기 97건이 이 표기다(e 하나라 사전 밖이었다)
    "tco₂eq": "tCO2eq",      # 아래 첨자 표기(모비스 2건) — 전각표로는 안 잡힌다
    "tco₂": "tCO2eq",
    "tonco2eq": "tCO2eq",
    "tonco2e": "tCO2eq",
    "톤co2eq": "tCO2eq",
    "톤co2": "tCO2eq",
    "톤co2e": "tCO2eq",
    "kgco2eq": "kgCO2eq",
    "kgco2e": "kgCO2eq",
    "kgco2": "kgCO2eq",
    "ktco2eq": "ktCO2eq",
    "ktco2e": "ktCO2eq",
    # Energy — TJ·GJ·MWh·kWh·GWh (E-4-1 에너지 사용량이 TJ 정의라 필수)
    "kwh": "kWh",
    "킬로와트시": "kWh",
    "mwh": "MWh",
    "메가와트시": "MWh",
    "gwh": "GWh",
    "기가와트시": "GWh",
    "tj": "TJ",
    "gj": "GJ",
    "mj": "MJ",
    "toe": "toe",            # 석유환산톤 — 국내 에너지 공시에 자주 쓴다
    "석유환산톤": "toe",
    "gcal": "Gcal",
    "기가칼로리": "Gcal",
    # Volume — 물 사용량·폐수에 쓴다. 질량군과 합치지 않는다(위 주석 참고).
    "l": "L",
    "리터": "L",
    "kl": "kL",
    "킬로리터": "kL",
    "m3": "m3",
    "m³": "m3",
    "㎥": "m3",
    "m^3": "m3",
    "세제곱미터": "m3",
    # Percentage / permille — ‰(퍼밀)은 %와 다른 단위(산업재해율 오매핑 차단용)
    "%": "%",
    "퍼센트": "%",
    "percent": "%",
    "‰": "‰",
    "퍼밀": "‰",
    # Currency — 원화. `천원`·`조원`·`십억원`이 빠져 있었다(실측 251건).
    "원": "원",
    "krw": "원",
    "₩": "원",
    "천원": "천원",
    "만원": "만원",
    "백만원": "백만원",
    "십억원": "십억원",
    "억원": "억원",
    "조원": "조원",
    # Currency — 달러. **원화군과 절대 합치지 않는다**(환율을 모른다).
    "$": "달러",
    "usd": "달러",
    "us$": "달러",
    "달러": "달러",
    "미국달러": "달러",
    "천달러": "천달러",
    "천usd": "천달러",
    "천$": "천달러",
    "백만달러": "백만달러",
    "백만usd": "백만달러",
    "백만$": "백만달러",
    "억달러": "억달러",
    "억usd": "억달러",
    "억$": "억달러",
    # Time — 분·초는 데이터에 없어 환산군을 만들지 않았다.
    "시간": "시간",
    "hr": "시간",
    "hour": "시간",
    "hours": "시간",
    # People / count — 환산군이 없으므로 **순위 판정은 지금과 같다**(같은 표기는 이전에도
    # `_relaxed_unit` 비교로 0위였다). 사전에 넣는 이유는 '사전 밖'이 진짜 미상 단위만
    # 가리키게 하는 것이다. `주`(주식/주간)·`일`·`년`·`점`·`배`·`수`는 뜻이 갈려 넣지 않았다.
    "명": "명",
    "인": "명",
    "만명": "만명",          # 실측 2건 — 인원은 아래 환산군에서 명과 이어 둔다

    "건": "건",
    "건수": "건",
    "개": "개",
    "개소": "개소",
    "대": "대",
    "종": "종",
    "팀": "팀",
    "회": "회",
    "개사": "개사",
    "업체수": "개사",
}

# Groups of compatible units with conversion factor TO the base unit.
# Base unit is the first entry (factor=1).
# 에너지군: kWh 기준. 1 kWh = 3.6 MJ, 1 GJ = 1000 MJ = 277.778 kWh, 1 TJ = 1e6 MJ.
_UNIT_GROUPS: list[dict[str, float]] = [
    {
        "kWh": 1.0,
        "MWh": 1_000.0,
        "GWh": 1_000_000.0,
        "MJ": 1.0 / 3.6,               # 3.6 MJ = 1 kWh
        "GJ": 1_000.0 / 3.6,           # 1 GJ = 277.78 kWh
        "TJ": 1_000_000.0 / 3.6,       # 1 TJ = 277,778 kWh
        "Gcal": 1_163.0,               # 1 kcal = 1.163 Wh → 1 Gcal = 1,163 kWh
        "toe": 11_630.0,               # 1 toe = 10 Gcal = 11.63 MWh
    },
    {
        "원": 1.0, "천원": 1_000.0, "만원": 10_000.0, "백만원": 1_000_000.0,
        "십억원": 1_000_000_000.0, "억원": 100_000_000.0, "조원": 1_000_000_000_000.0,
    },
    # 달러군은 원화군과 분리한다 — 환율은 이 모듈이 알 수 없고, 알 수 없는 환산을
    # 만들면 G4가 "단위 호환"이라고 판정한 뒤 값이 엉뚱하게 환산된다.
    {"달러": 1.0, "천달러": 1_000.0, "백만달러": 1_000_000.0, "억달러": 100_000_000.0},
    # 질량: 1 t = 1000 kg. `만t`은 배율이 붙은 표기라 같은 축에 둔다(1만 t = 10,000 t).
    {"t": 1.0, "kg": 0.001, "g": 0.000_001, "kt": 1_000.0, "만t": 10_000.0},
    {"명": 1.0, "만명": 10_000.0},                             # 인원(배율 표기만 이어 둔다)
    {"tCO2eq": 1.0, "kgCO2eq": 0.001, "ktCO2eq": 1_000.0},    # 배출량(질량군과 별개)
    # 부피: 1 m³ = 1,000 L. **질량군과 합치지 않는다** — 물만 밀도 1 t/m³로 같이 보고,
    # 그 예외는 `node_select._water_mass_volume_pair`가 물 지표에만 적용한다.
    {"L": 1.0, "kL": 1_000.0, "m3": 1_000.0},
]

_UNIT_TO_GROUP: dict[str, dict[str, float]] = {}
for _group in _UNIT_GROUPS:
    for _unit in _group:
        _UNIT_TO_GROUP[_unit] = _group


def normalize_unit(raw: str) -> str | None:
    """Return canonical unit string, or None if unrecognized.

    내부 공백을 제거한다(2026-08-02). OCR이 뽑는 단위는 `'백만 원'`처럼 배율과 단위
    사이에 공백이 들어오는데, 사전 키는 `'백만원'`이라 매칭에 실패해 `unit_suspect`가
    붙고 값이 환산되지 않았다(모비스 S-2-4 교육훈련비 21,116 백만 원 → 21,116원,
    10억 배 축소). 별칭 30개를 공백 제거 후 대조했을 때 충돌하는 키가 없어 안전하다.
    """
    from ..layer1_extract import _FULLWIDTH_TO_ASCII

    # 전각 표기도 같은 단위로 본다(layer1_extract._relaxed_unit과 같은 표를 쓴다).
    # 두 경로가 갈리면 `_unit_suspect`는 통과하는데 환산은 실패해 값이 안 실린다.
    key = re.sub(r"\s+", "", raw).translate(_FULLWIDTH_TO_ASCII).lower()
    return _UNIT_ALIASES.get(key)


def units_compatible(u1: str, u2: str) -> bool:
    """Check whether two canonical units are in the same conversion group or identical.

    NOTE: layer3_detect.py에도 동명 함수가 있으나 시맨틱이 다름.
    여기는 '같은 환산 그룹(kWh↔MWh↔GWh)' 판정(근거 게이트용 환산 비교),
    layer3_detect 쪽은 '동일 단위 or 미상' 판정(탐지기용 보수적 비교).
    """
    if u1 == u2:
        return True
    g1 = _UNIT_TO_GROUP.get(u1)
    g2 = _UNIT_TO_GROUP.get(u2)
    if g1 is None or g2 is None:
        return False
    return g1 is g2


def convert_to_common(value: float, from_unit: str, to_unit: str) -> float | None:
    """Convert value from from_unit to to_unit if they are compatible."""
    if not math.isfinite(value):
        return None
    if from_unit == to_unit:
        return value
    group = _UNIT_TO_GROUP.get(from_unit)
    if group is None or to_unit not in group:
        return None
    # value is in from_unit; convert to base then to target
    base_value = value * group[from_unit]
    return base_value / group[to_unit]


def numeric_equal(a: float, b: float, rel_tol: float = 0.01) -> bool:
    """Compare two numbers with relative tolerance (default 1%)."""
    return math.isclose(a, b, rel_tol=rel_tol)


# ── Extraction helper for (number, unit) pairs ─────────────────────────────

_NUM_UNIT_RE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*"
    r"(톤CO2eq|톤CO2|tCO2eq|tCO2|톤|ton|tons|t|"
    r"킬로와트시|kWh|메가와트시|MWh|기가와트시|GWh|"
    r"퍼센트|%|백만원|억원|원|명|인)",
    re.IGNORECASE,
)


def extract_number_unit_pairs(text: str) -> list[tuple[float, str]]:
    """Extract (numeric_value, canonical_unit) pairs from text."""
    pairs: list[tuple[float, str]] = []
    for m in _NUM_UNIT_RE.finditer(text):
        num = parse_number(m.group(1))
        unit = normalize_unit(m.group(2))
        if num is not None and unit is not None:
            pairs.append((num, unit))
    return pairs


__all__ = [
    "convert_to_common",
    "extract_number_unit_pairs",
    "normalize_unit",
    "numeric_equal",
    "parse_number",
    "units_compatible",
]
