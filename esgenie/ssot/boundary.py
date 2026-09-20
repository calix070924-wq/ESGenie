"""L0-B — 측정 경계(boundary) 메타데이터.

## 왜 별도 축인가

기존 노드는 `period`(연도 정수) 하나로 기간을 표현했다. 그래서 아래 세 값이
그래프에서 **구분되지 않았다**.

    2026-05 전기요금 청구서의 사용전력량      142,560 kWh   (1개월 실적)
    2026년 1~6월 월평균 총 전력 사용량           805 MWh    (6개월 월평균)
    2026년 하반기 이후 연간 태양광 발전 목표      540 MWh    (연간 목표)

세 값 모두 `period=2026`이 되어 같은 코드 풀에 나란히 놓였고, 합산·비교·비율
판정이 전부 이 구분 없이 돌았다. 연도만으로는 **월간값과 연간값을 가를 수 없다**.

경계는 다음 축으로 쪼갠다. 축 하나하나가 실제 소비 지점을 갖는다.

  | 축                        | 소비 지점                                      |
  |---------------------------|------------------------------------------------|
  | aggregation·coverage      | 합산 가능성(§summable), D1 비교 가능성          |
  | basis (실적/목표/계획)     | 합산 제외, 대표값 자격                          |
  | site·site_scope           | 합산 가능성, '범위 확인 필요' 상태               |
  | measure·measure_kind      | 이중계상 방어, 비율 분자 판정                    |
  | completeness              | 부분합 ↔ 완전성 분리(verified 자격)              |
  | denominator·denominator_kind | 비율의 분모 — 구성비를 전체비로 승격 금지      |

## 어디서 채우는가 — 새 OCR 호출 없음

원문은 이미 필요한 문구를 담고 있다(`metric_hint`, `period` 원문 문자열). 저장된
OCR 캐시를 재생해도 같은 경계가 나온다. 새 필드를 채우려고 문서를 다시 읽지 않는다.

    "태양광 월평균 사용량" / "2026년 1~6월 월평균"
      → measure_kind=electricity_renewable_solar, aggregation=monthly_average,
        coverage_months=6, basis=actual, completeness=partial

## 추론한 값은 추론이라고 적는다

원문에 없는 축은 `unknown`으로 남기고, 규칙으로 메운 축은 `inferred`에 이름을
남긴다. 하류(원장·D1·출력)는 `unknown`을 '같다'로 읽지 않는다 — '확인 필요'로 읽는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict, fields
from typing import Any, Iterable, Literal

# ====================================================================
# 축 값 집합
# ====================================================================

# 값이 시간축에서 어떻게 뭉쳐졌는가.
Aggregation = Literal[
    "annual",           # 연간 합계/연간값
    "period_total",     # 특정 구간(반기·분기 등) 합계
    "monthly",          # 단월 실적(고지서 1장)
    "monthly_average",  # 구간 내 월평균
    "daily_average",    # 일평균
    "point",            # 시점값(재고·인원 등)
    "unknown",
]

Basis = Literal["actual", "target", "plan", "unknown"]
SiteScope = Literal["entity", "site", "unknown"]
Completeness = Literal["total", "partial", "unknown"]

# ====================================================================
# 에너지원·측정 대상 사전
# ====================================================================
# node_select의 `_BREAKDOWN_TERMS`/`_PARTIAL_TERMS`와 같은 성격의 어휘 축이다.
# 여기에 모아 두는 이유: 경계 판정(measure_kind)과 대표값 판정(비율 분자)이 **같은
# 사전**을 봐야 한다. 두 곳에 나눠 적으면 '태양광 비율'이 경계상으로는 부분값인데
# 대표값 판정에서는 총량이 되는 모순이 생긴다(이번 결함의 구조).
#
# 키 이름은 (계열)_(세부) 형태다. 계열(family)이 같고 세부가 다르면 **합산 대상**,
# 계열이 같고 한쪽이 `_total`이면 **이중계상**이다(§summable).

# (a) 전력 — 조달·발전 방식별 세부. 전부 '총 전력'의 구성요소다.
_ELECTRICITY_SOURCES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("electricity_solar",        ("태양광", "태양열", "solar", "pv")),
    ("electricity_wind",         ("풍력", "wind")),
    ("electricity_hydro",        ("수력", "hydro")),
    ("electricity_geothermal",   ("지열", "geothermal")),
    ("electricity_biomass",      ("바이오매스", "바이오가스", "biomass", "biogas")),
    ("electricity_fuelcell",     ("연료전지", "fuel cell", "수소연료")),
    ("electricity_green_tariff", ("그린 프리미엄", "그린프리미엄", "green premium",
                                  "녹색요금제", "녹색전력상품")),
    ("electricity_ppa",          ("전력구매계약", "ppa", "vppa")),
    ("electricity_rec",          ("rec", "재생에너지 공급인증서", "공급인증서")),
    ("electricity_self_gen",     ("자가발전", "자체발전")),
    # '비재생에너지'는 '재생에너지'를 부분문자열로 포함한다. 더 긴 어휘를 여기 둬야
    # 최장 일치(§_match_kind)가 계열 총량이 아니라 비재생 구성요소로 판정한다.
    ("electricity_fossil",       ("화석연료 기반 전력", "화석연료기반 전력", "화석 전력",
                                  "화석연료", "비재생 전력", "비재생전력",
                                  "비재생에너지", "비재생 에너지")),
    ("electricity_nuclear",      ("원자력",)),
)

# (b) 연료·열 — 전력과 다른 계열. 전력과 합쳐 '총 에너지'가 된다.
_FUEL_SOURCES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("fuel_city_gas", ("도시가스", "city gas", "lng", "천연가스")),
    ("fuel_lpg",      ("lpg", "프로판", "부탄")),
    ("fuel_diesel",   ("경유", "디젤", "diesel")),
    ("fuel_gasoline", ("휘발유", "가솔린", "gasoline")),
    ("fuel_kerosene", ("등유", "kerosene")),
    ("fuel_coal",     ("석탄", "유연탄", "무연탄", "coal")),
    ("fuel_steam",    ("스팀", "증기", "열원 구매", "구매 열", "지역난방")),
    # 도시가스 고지서의 '사용열량(MJ)' — 연료 계열의 열량 표기. 전력과 계열이 달라
    # 합산 대상이 되고, 같은 고지서의 m³ 표기와는 같은 계열이라 이중계상으로 막힌다.
    ("fuel_heat",     ("사용열량", "열사용량", "사용 열량", "열량")),
)

# (c) 계열 총량 표현 — 구성요소가 아니라 그 계열 전체를 뜻한다.
_FAMILY_TOTALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("electricity_total", ("총 전력", "총전력", "전력 사용량", "전력사용량",
                           "사용전력량", "전기 사용량", "전기사용량", "전력 소비량",
                           "전력소비량", "소비전력", "전력량")),
    ("renewable_total",   ("재생에너지", "신재생에너지", "신재생", "재생 전력",
                           "재생전력", "re100")),
    ("energy_total",      ("총 에너지", "총에너지", "에너지 사용량", "에너지사용량",
                           "에너지 소비량", "에너지소비량")),
)

# (d) 에너지 외 측정 대상 — 합산·비교 판정에서 계열이 다르다는 사실만 쓴다.
_OTHER_MEASURES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("emission_scope1", ("scope 1", "scope1", "직접배출")),
    ("emission_scope2", ("scope 2", "scope2", "간접배출")),
    ("emission_scope3", ("scope 3", "scope3", "가치사슬")),
    ("emission_total",  ("온실가스", "탄소배출", "ghg", "tco2")),
    ("water",           ("취수", "용수", "상수도", "지하수", "공업용수")),
    ("waste",           ("폐기물",)),
)

# 계열(family) — 같은 계열 안에서 `_total`과 세부가 섞이면 이중계상이다.
_FAMILY_OF: dict[str, str] = {}
for _kind, _terms in (*_ELECTRICITY_SOURCES, ("electricity_total", ())):
    _FAMILY_OF[_kind] = "electricity"
for _kind, _terms in (*_FUEL_SOURCES, ("energy_total", ())):
    _FAMILY_OF[_kind] = "fuel" if _kind.startswith("fuel_") else "energy"
_FAMILY_OF["renewable_total"] = "electricity"
for _kind, _terms in _OTHER_MEASURES:
    _FAMILY_OF[_kind] = _kind.split("_", 1)[0]

# 계열 총량 kind — 같은 계열의 세부값과 합산 금지.
FAMILY_TOTAL_KINDS: frozenset[str] = frozenset(
    {"electricity_total", "energy_total", "renewable_total", "emission_total"}
)

# 구성요소로 확정된 에너지원 kind — 비율 분자 판정에서 '전체'가 아니라는 근거.
COMPONENT_SOURCE_KINDS: frozenset[str] = frozenset(
    kind for kind, _ in (*_ELECTRICITY_SOURCES, *_FUEL_SOURCES)
)


def kind_family(kind: str) -> str:
    """measure_kind → 계열. 사전에 없는 파생 kind('*_residual')도 접두로 해소한다."""
    if not kind or kind == "unknown":
        return ""
    known = _FAMILY_OF.get(kind)
    if known:
        return known
    for suffix in ("_total", "_residual"):
        if kind.endswith(suffix):
            return _FAMILY_OF.get(kind[: -len(suffix)] + "_total", kind[: -len(suffix)])
    return kind.split("_", 1)[0]

# 에너지원 어휘 평면 목록 — node_select의 분해 축이 재사용한다.
ENERGY_SOURCE_TERMS: tuple[str, ...] = tuple(
    term for _, terms in (*_ELECTRICITY_SOURCES, *_FUEL_SOURCES) for term in terms
)

# 잔여·일부를 뜻하는 수식어. '기타 재생에너지'는 정의상 구성요소다(표의 잔여 행).
# '… 포함'은 범위 확대이므로 호출부에서 예외 처리한다(node_select와 같은 규칙).
RESIDUAL_TERMS: tuple[str, ...] = (
    "기타", "그 외", "그외", "이외", "그 밖의", "그밖의", "일부", "나머지", "잔여",
)

# ====================================================================
# 기간·집계 어휘
# ====================================================================

_MONTHLY_AVG_RE = re.compile(r"월\s*평균")
_DAILY_AVG_RE = re.compile(r"(?:일\s*평균|1일\s*당|1일당)")
_ANNUAL_RE = re.compile(r"(?:연간|연\s*합계|1년\s*간|한\s*해|annual|/\s*년|년간)")
_HALF_RE = re.compile(r"(?:상반기|하반기|반기|1~6월|7~12월|1-6월|7-12월)")
_QUARTER_RE = re.compile(r"(?:분기|q[1-4]|[1-4]q)", re.I)
_MONTH_ONLY_RE = re.compile(r"20\d{2}\s*[-./년]\s*(1[0-2]|0?[1-9])\s*월?\s*$")
_MONTH_RANGE_RE = re.compile(r"(1[0-2]|0?[1-9])\s*[~\-–]\s*(1[0-2]|0?[1-9])\s*월")
_BILL_RANGE_RE = re.compile(
    r"(20\d{2})?[-./]?(1[0-2]|0?[1-9])[-./](3[01]|[12]\d|0?[1-9])\s*[~\-–]\s*"
    r"(?:(20\d{2})[-./])?(1[0-2]|0?[1-9])[-./](3[01]|[12]\d|0?[1-9])"
)

_TARGET_RE = re.compile(r"(?:목표|target|달성률|감축률)")
_PLAN_RE = re.compile(r"(?:계획|예정|전망|로드맵|예상)")

# 조직 범위
_ENTITY_RE = re.compile(r"(?:전사|전\s*사업장|전체\s*사업장|company[-\s]?wide|전\s*법인)")
_SITE_RE = re.compile(
    r"(제?\s*\d+\s*공장|[가-힣A-Za-z0-9]{1,12}공장|[가-힣A-Za-z0-9]{1,12}사업장"
    r"|[가-힣A-Za-z0-9]{1,12}라인|[가-힣A-Za-z0-9]{1,12}동\b)"
)

# 비율의 분모
_DENOM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("total_electricity", re.compile(r"(?:총\s*전력|전력\s*사용량\s*대비|전체\s*전력)")),
    ("total_energy", re.compile(r"(?:총\s*에너지|에너지\s*사용량\s*대비|전체\s*에너지)")),
    ("total_waste", re.compile(r"(?:총\s*폐기물|폐기물\s*대비)")),
    ("total_water", re.compile(r"(?:총\s*취수|용수\s*사용량\s*대비)")),
    ("base_year", re.compile(r"20\d{2}\s*년?\s*대비")),
)

_RATIO_UNITS: frozenset[str] = frozenset({"%", "pct", "percent", "퍼센트", "비율", "％"})


# ====================================================================
# Boundary
# ====================================================================

@dataclass(frozen=True)
class Boundary:
    """한 수치가 어느 경계에서 측정됐는가.

    모든 필드는 기본값을 갖는다 — 구버전 노드·캐시는 빈 Boundary로 읽히고,
    빈 Boundary는 "모른다"로 취급된다(같다고 취급하지 않는다).
    """
    period_text: str = ""
    aggregation: Aggregation = "unknown"
    coverage_months: int | None = None
    basis: Basis = "unknown"
    site: str = ""
    site_scope: SiteScope = "unknown"
    measure: str = ""
    measure_kind: str = "unknown"
    completeness: Completeness = "unknown"
    denominator: str = ""
    denominator_kind: str = "unknown"
    inferred: tuple[str, ...] = ()
    source_quote: str = ""

    # ── 직렬화 ───────────────────────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["inferred"] = list(self.inferred)
        return d

    @classmethod
    def from_dict(cls, d: Any) -> "Boundary":
        """모르는 키는 무시한다 — 스키마가 늘어도 구버전 캐시가 예외를 던지지 않는다."""
        if isinstance(d, Boundary):
            return d
        if not isinstance(d, dict):
            return cls()
        names = {f.name for f in fields(cls)}
        kw = {k: v for k, v in d.items() if k in names}
        if "inferred" in kw:
            kw["inferred"] = tuple(kw["inferred"] or ())
        return cls(**kw)

    # ── 조회 ─────────────────────────────────────────────────────────
    @property
    def family(self) -> str:
        return kind_family(self.measure_kind)

    @property
    def is_family_total(self) -> bool:
        return self.measure_kind in FAMILY_TOTAL_KINDS

    @property
    def is_known(self) -> bool:
        """축 하나라도 원문에서 읽혔는가 — 빈 Boundary와 구분한다."""
        return bool(self.period_text or self.measure_kind != "unknown"
                    or self.aggregation != "unknown" or self.site)

    def label(self) -> str:
        """출력용 사람 읽는 경계 요약. 모르는 축은 적지 않는다."""
        parts: list[str] = []
        if self.period_text:
            parts.append(self.period_text)
        agg = _AGG_LABEL.get(self.aggregation)
        if agg and agg not in "".join(parts):
            parts.append(agg)
        if self.basis in ("target", "plan"):
            parts.append(_BASIS_LABEL[self.basis])
        if self.site:
            parts.append(self.site)
        elif self.site_scope == "entity":
            parts.append("전사")
        if self.measure:
            parts.append(self.measure)
        if self.completeness == "partial":
            parts.append("부분")
        elif self.completeness == "total":
            parts.append("총량")
        return " · ".join(parts)

    def merged(self, **kw: Any) -> "Boundary":
        """일부 축만 덮어쓴 새 Boundary. 빈 값/unknown은 덮어쓰지 않는다."""
        data = asdict(self)
        for key, value in kw.items():
            if key not in data:
                continue
            if value in (None, "", "unknown", ()):
                continue
            data[key] = value
        data["inferred"] = tuple(data.get("inferred") or ())
        return Boundary(**data)


_AGG_LABEL: dict[str, str] = {
    "annual": "연간",
    "period_total": "기간합계",
    "monthly": "월간",
    "monthly_average": "월평균",
    "daily_average": "일평균",
    "point": "시점",
}
_BASIS_LABEL: dict[str, str] = {"target": "목표", "plan": "계획", "actual": "실적"}
_FAMILY_LABEL: dict[str, str] = {
    "electricity": "전력", "fuel": "연료", "energy": "에너지",
    "emission": "온실가스", "water": "용수", "waste": "폐기물",
}


def family_label(family: str) -> str:
    return _FAMILY_LABEL.get(family, family)


# ====================================================================
# 판정 — derive_boundary
# ====================================================================

def _norm(text: Any) -> str:
    if not text:
        return ""
    return " ".join(str(text).replace(" ", " ").split()).lower()


def _match_kind(text: str, table: Iterable[tuple[str, tuple[str, ...]]]) -> tuple[str, str]:
    """가장 긴 일치 어휘를 채택한다 — '재생 전력'이 '전력'에 먹히지 않게."""
    best_kind, best_term = "", ""
    for kind, terms in table:
        for term in terms:
            if term and term in text and len(term) > len(best_term):
                best_kind, best_term = kind, term
    return best_kind, best_term


def detect_measure(text: str) -> tuple[str, str]:
    """측정 대상 판정 → (kind, 원문 어휘). 구성요소가 계열 총량보다 우선한다.

    '태양광 월평균 사용량'은 '사용량'(계열 총량 어휘)도 갖고 있지만 태양광이
    더 구체적인 측정 대상이다. 구성요소를 먼저 보지 않으면 세부값이 총량이 된다.
    """
    normalized = _norm(text)
    kind, term = _match_kind(normalized, (*_ELECTRICITY_SOURCES, *_FUEL_SOURCES))
    if kind:
        return kind, term
    kind, term = _match_kind(normalized, _OTHER_MEASURES)
    if kind:
        return kind, term
    return _match_kind(normalized, _FAMILY_TOTALS)


def detect_aggregation(text: str, *, also: str = "") -> tuple[str, int | None]:
    """집계 방식 + 대상 구간 길이(개월).

    `text`(기간 표기)를 먼저 단독으로 본다. '2026-05'처럼 **문자열 끝 앵커**로
    단월을 판정하는 규칙이 있어, hint를 이어 붙인 문자열로는 앵커가 깨진다.
    기간 표기만으로 못 가리면 `also`(hint)까지 합쳐 다시 본다.
    """
    primary = _detect_aggregation_one(text)
    if primary[0] != "unknown":
        agg, months = primary
        if months is None:
            months = _coverage_months(_norm(f"{text} {also}"))
        return agg, months
    return _detect_aggregation_one(f"{text} {also}")


def _detect_aggregation_one(text: str) -> tuple[str, int | None]:
    normalized = _norm(text)
    months = _coverage_months(normalized)
    if _MONTHLY_AVG_RE.search(normalized):
        return "monthly_average", months
    if _DAILY_AVG_RE.search(normalized):
        return "daily_average", months
    if _ANNUAL_RE.search(normalized):
        return "annual", months or 12
    if _HALF_RE.search(normalized):
        return "period_total", months or 6
    if _QUARTER_RE.search(normalized):
        return "period_total", months or 3
    if _BILL_RANGE_RE.search(normalized) or _MONTH_ONLY_RE.search(normalized):
        return "monthly", 1
    if months:
        return "period_total", months
    return "unknown", months


def _coverage_months(normalized: str) -> int | None:
    # '2026년 하반기 이후 연간'처럼 반기어와 연간어가 함께 오면 연간이 대상 구간이다.
    if _ANNUAL_RE.search(normalized):
        return 12
    m = _MONTH_RANGE_RE.search(normalized)
    if m:
        start, end = int(m.group(1)), int(m.group(2))
        return end - start + 1 if end >= start else None
    if _HALF_RE.search(normalized):
        return 6
    if _QUARTER_RE.search(normalized):
        return 3
    if _BILL_RANGE_RE.search(normalized) or _MONTH_ONLY_RE.search(normalized):
        return 1
    return None


def detect_basis(text: str) -> str:
    normalized = _norm(text)
    if _TARGET_RE.search(normalized):
        return "target"
    if _PLAN_RE.search(normalized):
        return "plan"
    return "actual"


def detect_site(text: str) -> tuple[str, str]:
    """(원문 사업장 표기, 범위). 못 읽으면 ('', 'unknown') — '전사'로 가정하지 않는다."""
    raw = str(text or "")
    if _ENTITY_RE.search(_norm(raw)):
        return "", "entity"
    m = _SITE_RE.search(raw)
    if m:
        return m.group(1).strip(), "site"
    return "", "unknown"


def detect_denominator(text: str) -> tuple[str, str]:
    raw = str(text or "")
    for kind, pattern in _DENOM_PATTERNS:
        m = pattern.search(raw)
        if m:
            return m.group(0).strip(), kind
    return "", "unknown"


def is_ratio(unit: str | None, hint: str | None = None) -> bool:
    if str(unit or "").strip().lower() in _RATIO_UNITS:
        return True
    return bool(re.search(r"(?:비율|비중|률|율)(?:\s|$|\(|20\d{2})", _norm(hint)))


def detect_completeness(text: str, *, measure_kind: str) -> str:
    """총량인가 부분인가. 입증되지 않으면 unknown — 총량으로 가정하지 않는다."""
    normalized = _norm(text)
    if _has_residual(normalized):
        return "partial"
    if measure_kind in COMPONENT_SOURCE_KINDS:
        return "partial"
    if measure_kind in FAMILY_TOTAL_KINDS:
        return "total"
    if any(t in normalized for t in ("합계", "총계", "전사", "total", "총 ", "전체")):
        return "total"
    return "unknown"


def _has_residual(normalized: str) -> bool:
    if "포함" in normalized:          # '… 포함'은 범위 확대 — 잔여값이 아니다.
        return False
    return any(t in normalized for t in RESIDUAL_TERMS)


def covers_full_year(b: Boundary | dict | None) -> bool | None:
    """이 경계가 한 해 전체를 덮는가 → True / False / None(판단 근거 없음).

    K-ESG 정량 항목은 대부분 연간값을 묻는다. 월간·월평균·상반기 값은 숫자가
    맞아도 연간 총량의 전체성을 입증하지 못한다. None은 '모른다'이며, 구버전
    입력(빈 Boundary)을 부분값으로 일괄 강등하지 않기 위해 False와 구분한다.
    """
    boundary = Boundary.from_dict(b)
    if boundary.aggregation in ("monthly", "monthly_average", "daily_average", "point"):
        return False
    if boundary.coverage_months is not None:
        return boundary.coverage_months >= 12
    if boundary.aggregation == "annual":
        return True
    return None


def merge_boundaries(
    items: Iterable[Boundary | dict | None],
    *,
    measure: str = "",
    measure_kind: str = "",
    completeness: Completeness | None = None,
) -> Boundary:
    """여러 값을 합산한 결과의 경계 — 축마다 '전부 같을 때만' 그 값을 남긴다.

    한 축이라도 갈리면 미상으로 되돌린다. 합산값에 구성요소 하나의 경계를 그대로
    붙이면 '제1공장 도장·건조라인'이 전사 경계로 승격된다(작업지시서 §2-1 마지막 항).
    `inferred`는 합집합이다 — 어느 한쪽이 추론이면 합산값도 추론이다.
    """
    pool = [Boundary.from_dict(x) for x in items]
    if not pool:
        return Boundary()

    def _same(attr: str, empty: Any):
        values = {getattr(b, attr) for b in pool}
        return values.pop() if len(values) == 1 else empty

    period_texts = [b.period_text for b in pool if b.period_text]
    merged_period = period_texts[0] if len(set(period_texts)) == 1 else " / ".join(
        dict.fromkeys(period_texts))
    quotes = [b.source_quote for b in pool if b.source_quote]
    return Boundary(
        period_text=merged_period,
        aggregation=_same("aggregation", "unknown"),
        coverage_months=_same("coverage_months", None),
        basis=_same("basis", "unknown"),
        site=_same("site", ""),
        site_scope=_same("site_scope", "unknown"),
        measure=measure or " + ".join(dict.fromkeys(b.measure for b in pool if b.measure)),
        measure_kind=measure_kind or _same("measure_kind", "unknown"),
        completeness=completeness or _same("completeness", "unknown"),
        denominator=_same("denominator", ""),
        denominator_kind=_same("denominator_kind", "unknown"),
        inferred=tuple(sorted({axis for b in pool for axis in b.inferred})),
        source_quote=" / ".join(dict.fromkeys(quotes)),
    )


def derive_boundary(
    metric_hint: str | None,
    period_raw: str | None = "",
    *,
    unit: str | None = "",
    doc_type: str = "",
    doc_context: str = "",
    base: Boundary | dict | None = None,
) -> Boundary:
    """원문 문구(hint + 기간표기 + 문서 문맥)에서 경계를 읽어낸다.

    새 OCR/LLM 호출 없음 — 이미 추출된 문자열만 본다. 저장 캐시 재생에도 같은
    결과가 나온다. `base`가 주어지면(정형 파서가 미리 채운 경우) 그 값을 우선한다.
    """
    hint = str(metric_hint or "")
    period_text = str(period_raw or "").strip()
    context = str(doc_context or "")
    joined = f"{hint} {period_text} {context}"

    measure_kind, measure = detect_measure(f"{hint} {context}")
    # 고지서의 '사용열량'은 그 문서가 도시가스 고지서일 때 도시가스 사용량과 **같은
    # 물리량의 다른 표기**다. 같은 kind로 묶어야 m³ + MJ 이중계상이 막힌다.
    if measure_kind == "fuel_heat" and re.search(r"(?:도시가스|가스|lng|천연가스)",
                                                 _norm(f"{hint} {context}")):
        measure_kind = "fuel_city_gas"
    aggregation, months = detect_aggregation(period_text, also=hint)
    basis = detect_basis(f"{period_text} {hint}")
    site, site_scope = detect_site(f"{hint} {context}")
    ratio = is_ratio(unit, hint)
    denominator, denominator_kind = detect_denominator(joined)
    completeness = detect_completeness(f"{hint} {context}", measure_kind=measure_kind)

    # 비율은 분모가 곧 완전성 판정의 근거다. 분모를 읽지 못한 구성비는 부분이다.
    if ratio and completeness == "unknown" and measure_kind in COMPONENT_SOURCE_KINDS:
        completeness = "partial"

    # '기타 재생에너지'는 계열 이름을 쓰지만 표의 잔여 행이다. 계열 총량으로 남기면
    # 구성요소 합산 차단(§plan_sum 4단계)이 거꾸로 걸린다 — 잔여 kind로 내린다.
    if measure_kind in FAMILY_TOTAL_KINDS and _has_residual(_norm(f"{hint} {context}")):
        measure_kind = measure_kind[: -len("_total")] + "_residual"
        completeness = "partial"

    inferred: list[str] = []
    if not period_text:
        inferred.append("period_text")
    if aggregation == "unknown":
        inferred.append("aggregation")
    if site_scope == "unknown":
        inferred.append("site_scope")
    if measure_kind == "unknown":
        inferred.append("measure_kind")
    if ratio and denominator_kind == "unknown":
        inferred.append("denominator_kind")

    derived = Boundary(
        period_text=period_text,
        aggregation=aggregation,
        coverage_months=months,
        basis=basis,
        site=site,
        site_scope=site_scope,
        measure=measure,
        measure_kind=measure_kind or "unknown",
        completeness=completeness,
        denominator=denominator,
        denominator_kind=denominator_kind,
        inferred=tuple(inferred),
        source_quote=f"{hint} ({period_text})".strip() if hint else period_text,
    )
    if base is None:
        return derived
    prior = Boundary.from_dict(base)
    if not prior.is_known:
        return derived
    # 정형 파서가 직접 읽은 축이 규칙 추론을 이긴다.
    return derived.merged(**{k: v for k, v in asdict(prior).items() if k != "inferred"})


# ====================================================================
# 비교 가능성 — D1·교차검증이 '같은 것끼리' 비교하게 만든다
# ====================================================================

ComparisonStatus = Literal["compared", "mismatch", "not_comparable", "scope_unconfirmed"]

# 상태 → 사람 읽는 라벨. UI/Excel/PDF/체크리스트가 같은 라벨을 쓴다.
COMPARISON_LABEL: dict[str, str] = {
    "compared": "대조 완료",
    "mismatch": "실제 불일치",
    "not_comparable": "비교 불가",
    "scope_unconfirmed": "범위 확인 필요",
}


def comparable(a: Boundary | dict | None, b: Boundary | dict | None) -> tuple[str, str]:
    """두 경계를 수치 비교해도 되는가 → (상태, 이유).

    반환 상태는 'compared'(비교 가능) / 'not_comparable'(차원 자체가 다름) /
    'scope_unconfirmed'(같은지 확인 안 됨)뿐이다. **실제 불일치 판정은 호출부가
    수치를 비교한 뒤에 한다** — 비교 불가와 불일치를 섞으면 안 된다.
    """
    ba, bb = Boundary.from_dict(a), Boundary.from_dict(b)
    if not (ba.is_known or bb.is_known):
        return "scope_unconfirmed", "양쪽 경계 미기록 — 같은 범위인지 확인 필요"

    # (1) 실적 ↔ 목표/계획은 비교 대상이 아니다.
    if {ba.basis, bb.basis} & {"target", "plan"} and ba.basis != bb.basis:
        return "not_comparable", f"실적/목표 구분 상이({ba.basis} ↔ {bb.basis})"

    # (2) 측정 대상 계열이 다르면 비교 불가.
    if ba.measure_kind != "unknown" and bb.measure_kind != "unknown":
        if ba.measure_kind != bb.measure_kind:
            if ba.family and bb.family and ba.family != bb.family:
                return "not_comparable", f"측정 대상 상이({ba.measure or ba.measure_kind} ↔ {bb.measure or bb.measure_kind})"
            return "not_comparable", (
                f"같은 계열의 다른 범위({ba.measure or ba.measure_kind} ↔ "
                f"{bb.measure or bb.measure_kind})")

    # (3) 집계 방식/구간 길이가 다르면 비교 불가(월간값 ↔ 연간값).
    if ba.aggregation != "unknown" and bb.aggregation != "unknown":
        if ba.aggregation != bb.aggregation:
            return "not_comparable", (
                f"집계 방식 상이({_AGG_LABEL.get(ba.aggregation, ba.aggregation)} ↔ "
                f"{_AGG_LABEL.get(bb.aggregation, bb.aggregation)})")
    if ba.coverage_months and bb.coverage_months and ba.coverage_months != bb.coverage_months:
        return "not_comparable", f"대상 기간 길이 상이({ba.coverage_months}개월 ↔ {bb.coverage_months}개월)"

    # (4) 사업장 범위 — 다르면 비교 불가, 한쪽이라도 미상이면 확인 필요.
    if ba.site and bb.site and ba.site != bb.site:
        return "not_comparable", f"사업장 범위 상이({ba.site} ↔ {bb.site})"
    if ba.site_scope != "unknown" and bb.site_scope != "unknown" and ba.site_scope != bb.site_scope:
        return "scope_unconfirmed", "전사 ↔ 사업장 범위 혼재 — 확인 필요"
    if ba.site_scope == "unknown" or bb.site_scope == "unknown":
        return "scope_unconfirmed", "사업장 범위 미기록 — 같은 범위인지 확인 필요"

    # (5) 총량 ↔ 부분값.
    if {ba.completeness, bb.completeness} == {"total", "partial"}:
        return "not_comparable", "총량 ↔ 부분값"

    return "compared", "경계 일치"


# ====================================================================
# 합산 가능성 — 이중계상 방어
# ====================================================================

@dataclass
class SumDecision:
    """제한적 합산 판정 결과."""
    summable: list[Any] = field(default_factory=list)
    blocked: list[tuple[Any, str]] = field(default_factory=list)
    reference: list[tuple[Any, str]] = field(default_factory=list)
    completeness: Completeness = "unknown"
    reasons: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.summable) > 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "summable": [getattr(x, "id", str(x)) for x in self.summable],
            "blocked": [[getattr(x, "id", str(x)), why] for x, why in self.blocked],
            "reference": [[getattr(x, "id", str(x)), why] for x, why in self.reference],
            "completeness": self.completeness,
            "reasons": list(self.reasons),
        }


def _unit_groups(pool: list[tuple[Any, Boundary]], unit_of) -> list[list[tuple[Any, Boundary]]]:
    """단위 환산군별로 묶는다. 환산 불가 단위끼리는 애초에 더할 수 없다.

    도시가스 고지서의 m³와 MJ가 같은 물리량의 두 표기인 경우를 막는 1차 방어선이다
    (같은 measure_kind로도 막히지만, 단위 차원은 코드와 무관하게 성립하는 근거다).
    """
    from ..rag_gates.units import normalize_unit, units_compatible

    groups: list[list[tuple[Any, Boundary]]] = []
    for entry in pool:
        unit = normalize_unit(str(unit_of(entry[0]) or "")) or str(unit_of(entry[0]) or "")
        for group in groups:
            head = normalize_unit(str(unit_of(group[0][0]) or "")) or str(unit_of(group[0][0]) or "")
            if head == unit or (head and unit and units_compatible(head, unit)):
                group.append(entry)
                break
        else:
            groups.append([entry])
    return groups


def plan_sum(items: Iterable[Any], *, boundary_of=None, unit_of=None) -> SumDecision:
    """합산해도 되는 값만 골라낸다. 나머지는 '참고 부분값'으로 남긴다.

    방어하는 이중계상 3종(작업지시서 §2-2):
      · 같은 고지서/같은 측정 대상이 두 번 들어온 경우
      · 계열 총량과 그 구성요소를 함께 더하는 경우(총 전력 + 태양광)
      · 같은 물리량을 단위만 달리 적은 경우(도시가스 m³ + MJ)

    합산 결과의 `completeness`는 **절대 total이 되지 않는다** — 구성요소를 몇 개
    더했는지는 전체를 덮었다는 증거가 아니다. 완전성은 별도 근거로만 total이 된다.
    """
    get = boundary_of or (lambda x: getattr(x, "boundary", None))
    unit_get = unit_of or (lambda x: getattr(x, "unit", ""))
    pool = [(x, Boundary.from_dict(get(x))) for x in items]
    decision = SumDecision()
    if not pool:
        return decision

    # 0) 단위 환산군 — 가장 큰 군만 합산 대상으로 남긴다. 나머지는 참고값이다.
    groups = _unit_groups(pool, unit_get)
    if len(groups) > 1:
        groups.sort(key=len, reverse=True)
        for group in groups[1:]:
            for item, b in group:
                decision.reference.append(
                    (item, f"단위 차원 상이({unit_get(item)}) — 합산 불가"))
        decision.reasons.append(
            "단위 환산군 혼재: "
            + ", ".join(sorted({str(unit_get(i)) for g in groups for i, _ in g}))
            + " — 같은 물리량의 다른 표기일 수 있어 합산 제외")
        pool = groups[0]

    # 1) 실적이 아닌 값은 합산 제외(목표·계획).
    kept: list[tuple[Any, Boundary]] = []
    for item, b in pool:
        if b.basis in ("target", "plan"):
            decision.reference.append((item, f"{_BASIS_LABEL[b.basis]}값 — 실적 합산 제외"))
        else:
            kept.append((item, b))

    # 2) 집계 방식/구간이 섞이면 합산 불가(월간 + 연간).
    aggs = {b.aggregation for _, b in kept if b.aggregation != "unknown"}
    spans = {b.coverage_months for _, b in kept if b.coverage_months}
    if len(aggs) > 1 or len(spans) > 1:
        for item, b in kept:
            decision.reference.append((item, "집계 방식·대상 기간이 달라 합산 불가"))
        decision.reasons.append(
            "집계 방식/대상 기간 혼재: "
            + ", ".join(sorted(_AGG_LABEL.get(a, a) for a in aggs))
            + (f" · {sorted(spans)}개월" if len(spans) > 1 else ""))
        decision.completeness = "unknown"
        return decision

    # 3) 사업장 범위가 다르면 합산 불가. 미상은 확인 필요로 통과시키되 기록한다.
    sites = {b.site for _, b in kept if b.site}
    if len(sites) > 1:
        decision.reasons.append(f"사업장 범위 혼재: {', '.join(sorted(sites))} — 합산 보류")
        for item, b in kept:
            decision.reference.append((item, "사업장 범위가 달라 합산 불가"))
        return decision
    if any(b.site_scope == "unknown" for _, b in kept):
        decision.reasons.append("사업장 범위 미기록 — 같은 범위라는 확인 필요")

    # 4) 계열 총량 + 같은 계열 구성요소 → 총량만 남기고 구성요소는 참고값.
    total_families = {b.family for _, b in kept if b.is_family_total and b.family}
    staged: list[tuple[Any, Boundary]] = []
    for item, b in kept:
        if not b.is_family_total and b.family and b.family in total_families:
            decision.blocked.append((
                item,
                f"{b.measure or b.measure_kind} — {family_label(b.family)} 총량의 "
                "구성요소이므로 이중계상 방지"))
            continue
        staged.append((item, b))

    # 5) 측정 대상을 못 읽었거나 같은 대상이 두 번 이상 → 합산 제외.
    #    측정 대상 미상은 '서로 중복되지 않는 별개 에너지원'이라는 확인이 없다는 뜻이다.
    #    같은 고지서를 두 번 올린 경우와 구분할 수 없으므로 fail-closed로 막는다(§2-2 첫 항).
    #    이 규칙이 구버전 노드·경계 미기록 입력이 조용히 합산되는 경로도 함께 닫는다.
    seen: dict[str, Any] = {}
    for item, b in staged:
        if b.measure_kind == "unknown":
            decision.blocked.append(
                (item, "측정 대상 미상 — 중복 여부를 확인할 수 없어 합산 제외"))
            continue
        if b.measure_kind in seen:
            decision.blocked.append(
                (item, f"같은 측정 대상({b.measure or b.measure_kind}) 중복 — 이중계상 방지"))
            continue
        seen[b.measure_kind] = item
        decision.summable.append(item)

    # 6) 완전성 — 합산만으로는 절대 total이 되지 않는다.
    if any(Boundary.from_dict(get(x)).is_family_total for x in decision.summable) and len(decision.summable) == 1:
        decision.completeness = Boundary.from_dict(get(decision.summable[0])).completeness
    else:
        decision.completeness = "partial"
    if len(decision.summable) > 1:
        decision.reasons.append(
            "제한적 합산: " + " + ".join(
                Boundary.from_dict(get(x)).measure or Boundary.from_dict(get(x)).measure_kind
                for x in decision.summable)
            + " — 전체 에너지원을 덮었다는 근거는 없으므로 부분값")
    return decision


__all__ = [
    "Boundary",
    "Aggregation",
    "Basis",
    "Completeness",
    "SiteScope",
    "ComparisonStatus",
    "COMPARISON_LABEL",
    "ENERGY_SOURCE_TERMS",
    "RESIDUAL_TERMS",
    "COMPONENT_SOURCE_KINDS",
    "FAMILY_TOTAL_KINDS",
    "kind_family",
    "family_label",
    "derive_boundary",
    "detect_measure",
    "detect_aggregation",
    "detect_basis",
    "detect_site",
    "detect_denominator",
    "detect_completeness",
    "covers_full_year",
    "merge_boundaries",
    "is_ratio",
    "comparable",
    "plan_sum",
    "SumDecision",
]
