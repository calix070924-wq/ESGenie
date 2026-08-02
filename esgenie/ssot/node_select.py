"""대표 노드 선택 — 같은 K-ESG 코드의 여러 OCR 노드 중 '원장에 쓸 하나'를 고른다.

## 왜 필요한가

L0가 hint를 서술적으로 잘 뽑아준다(`용수 사용량(취수량) 합계 2024년`,
`폐기물 처리량(매립, 소각 등) 국내(별도)`, `온실가스 감축 효과`). 그런데 지금까지
대표 노드 선택은 **연도 하나만** 봤다 — 같은 연도 안에서는 사실상 임의 선택이라
정답 노드가 같은 풀에 나란히 있어도 파생값·부분값을 골랐다(현대모비스 E-3-1이
`온실가스 감축 효과` 1,161,214을 배출량으로 원장에 올린 사례).

**오염이 아니라 선택 실패다.** 그래서 이 모듈은 값이 아니라 hint 위에 규칙을 세운다.

## period를 믿지 않는다 — 단, 값 최빈보다는 믿는다

원문 표의 연도 열이 한 period로 뭉개져 있다(동일 hint · 값만 다른 노드가 E-6-1에서
51건 초과). 그래서 연도는 **후순위 tie-breaker**로만 쓴다. 표 파싱 교정은 L0 소관이므로
여기서는 "연도에 hint 축을 양보하지 않는다"만 보장한다.

다만 **최빈 축(8)보다는 앞이다**(2026-07-29). period 폴백이 v3에서 48%→9%로 줄었고,
아래 시계열 정체 효과 때문에 최빈은 연도보다 덜 믿을 만하다는 게 실측 결론이다.

## 값 최빈 — 반복 언급을 세되, 연도를 먼저 좁힌다 (2026-07-29)

같은 지표는 보고서 여러 곳(표·본문·요약)에서 반복 언급돼 같은 값이 여러 번 추출된다.
전 축이 동률이면 종전에는 `-period → -confidence → str(id)`로 떨어져 **id 문자열
순서가 답을 정했다** — 현대모비스 E-4-2가 12.9%(3회 등장, 정답) 대신 10.0%(1회)를
골랐다.

**연도를 먼저 좁힌 뒤 그 안에서만 세는 것이 핵심이다.** 저장된 5개사 덤프로 세 형태를
전부 돌린 실측(docs/동률해소_결과_2026-07-29.md):

  ㄱ) 정렬 키에 전역 빈도 `-freq[value]`  → 값이 연도를 넘어 우연히 반복되면 옛 값이 이긴다
  ㄴ) 동률 그룹 안에서만 최빈              → 삼성전기 E-6-2가 99.0 → 97.0으로 회귀
  ㄷ) 연도를 먼저 좁힌 뒤 그 안에서만 최빈  → 목표 1건만 수정, 나머지 35개 항목 불변 ✅

ㄴ)이 깨지는 이유가 **시계열 정체 효과**다. 삼성전기 E-6-2 풀에는 같은 hint
(`폐기물 재활용률`)로 서로 다른 두 시계열이 섞여 있다:

  계열 A: 2021 84 → 2022 89 → 2023 96 → 2024 99 → 2025 99   (상승)
  계열 B: 2021 93 → 2022 97 → 2023 97 → 2024 97 → 2025 97   (정체)

연도 구분 없이 세면 97.0이 4회로 이긴다 — 값이 반복 언급돼서가 아니라 **값이 안
변해서**다. 최빈이 '반복 언급'이 아니라 '정체'를 집는다. 연도를 먼저 좁히면 사라진다.
그래서 축 순서는 반드시 **연도(7) → 최빈(8)**이다. 뒤집지 마라.

또한 최빈은 **최빈값이 유일할 때만** 개입한다. 빈도가 동률인데 답을 바꾸면 근거 없는
변경이므로, 그때는 아무것도 하지 않고 9단계(최신 → 고신뢰 → id)에 맡긴다.

## 우선순위 (앞 단계에서 후보가 갈리면 뒤는 안 본다)

  1) 배제(hard)  — 파생·비실적 어휘(감축·효과·예상·증감·원단위·목표·제로화·전환량 …)
  2) 지표 정합   — 코드별 negative keyword 충돌(E-3-1 ← 'Scope 3'·'1+2+3' 등)
  3) 지표 계열   — 코드가 요구하는 계열 우선(E-6-1은 '발생량' > '처리량')
  4) 세부 분해   — 조달방식·처리경로별 분해값 후순위(E-4-1의 PPA/vPPA/녹색요금제)
  5) 집계        — '합계·총계·전사·Total' > 구분어 없음 > '국내(별도)·자회사·국가명·공장'
  6) 단위 정합   — kesg_items.unit과 동일 > 환산 가능 > 그 외 (E-4-1은 TJ 우선)
  7) 연도        — 여기까지 동률일 때만 report_year 근접
  8) 값 최빈     — 같은 연도 안에서 여러 번 추출된 값 우선(최빈값이 유일할 때만)
  9) 최신 → 고신뢰 → str(id)  — 완전 결정성 보장
  후보 0 / 1·2단계 전멸 → None (원장은 미공시 + confidence_flag)

미공시 폴백이 중요하다: **잘못된 값보다 미공시가 낫다**(라벨링 §3-1 원칙).

1단계(hard 배제)는 **후보가 1개여도 적용된다**(2026-07-28). 종전에는 과차단 방지로
유일 후보를 무조건 채택했는데, 그 우회로 LG화학 E-5-1 '일평균 산업용수 공급량'
540,000 ton이 연간 사용량 자리에 실렸다(365배). 3~7단계는 후보 1개면 결과가 같다.

## 총량 후보가 없는 풀 — 값은 싣고 부분값임을 표기한다 (2026-07-28)

5단계는 **후순위 축이지 배제가 아니다.** 후보가 전부 부분값이면 그중 하나가 이긴다
(LG화학 E-4-1은 36노드에 '합계/총계'가 0개, NAVER E-3-2는 노드 1개가 Scope 3 카테고리 1).
미공시로 버리지 않는다 — 커버리지가 이미 5~7항목/17이고, 값 자체는 실제 공시값이다.
대신 `is_partial_aggregate`로 조회해 `partial_value` 플래그 → 원장 표 '·부분값'까지
노출한다. **D1은 이 오류를 못 잡는다**(원장·노드가 같은 값이라 Δ=0) — 표기가 유일한
방어선이다.

## 두 호출부가 반드시 이 함수를 쓴다

  · `ssot_pipeline._merge_ssot_evidence` — 원장 표시값
  · `layer3_detect._score_d1_numeric`   — D1 비교 대상

둘이 다른 노드를 고르면 데이터가 옳아도 D1이 발화한다(구조적 오탐). 대칭이 핵심이다.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Literal

ValueRole = Literal["total", "component", "target", "unknown"]

# ====================================================================
# 어휘 사전 — 2026-07-26 hint 전수 조사(docs/집계어휘_실태_2026-07-26.md) 결과로 확정.
# 190개 노드 / 고유 hint 106개에서 실제 관측된 표현을 축별로 모았고,
# 관측되지 않았지만 동종 표현인 것(전사·Total·원단위 등)은 이식성을 위해 남긴다.
# ====================================================================

# (1) 파생·비실적 어휘 — 실적 총량 자리에 오면 안 되는 값.
# G1 가드어휘(_GUARD_TERMS)를 '노드 선택' 단계로 확장한 것. G1은 코드 부여 시점에
# 걸러 노드를 metric_hint로 보존하지만, 이미 코드가 붙은 노드는 여기서 다시 걸러야 한다.
# 실측 근거: '온실가스 감축 효과'(E-3-1 오선택 주범), '2023년 대비 에너지 사용량 증감',
#            '알루미늄 1톤당 …', '폐기물 매립 제로화(재활용률)', '… 회수량'
# 시간 원단위(일평균·월평균)는 2026-07-28 5개사 실측에서 추가됐다. LG화학 E-5-1이
# '일평균 산업용수 공급량' 540,000 ton을 연간 사용량 자리에 올렸다(365배 오류).
# '평균' 단독은 넣지 않는다 — '평균 근속연수'처럼 정상 지표가 걸린다.
_DERIVED_TERMS: tuple[str, ...] = (
    "감축", "효과", "예상", "증감", "증감률", "증가", "감소",
    "전년 대비", "전년대비", "원단위", "1톤당", "톤당",
    "제로화", "목표", "전환량", "절감", "누적", "집약도", "intensity",
    "전망", "계획", "예정", "로드맵", "선언", "회수량", "대비",
    "일평균", "일 평균", "월평균", "월 평균", "1일당", "1일 당", "1개월당",
)

# (2) 집계-총량 어휘 — 전사/총량 표지. 실측: '합계' 50건 · '총계' 9건 · '총 ' 6건.
_TOTAL_TERMS: tuple[str, ...] = ("합계", "총계", "전사", "total", "총 ")

# (3) 집계-부분 어휘 — 조직 단위 한정. 실측 23종 전부 등장.
# 국가명·법인명은 회사마다 달라 일반화가 안 되므로, 이식되는 '구조 표현'만 사전에 두고
# 개별 고유명사는 넣지 않는다(과차단 방지). 대신 국가명은 '국가별' 접두로 잡힌다.
_PARTIAL_TERMS: tuple[str, ...] = (
    "국내(별도)", "국내 (별도)", "별도", "국내 자회사", "해외 자회사", "자회사",
    "국내 사업장", "해외 사업장", "사업장", "국가별", "공장", "연결(일부)",
)

# (3b) 단독 지역어 — 2026-07-28 5개사 실측 보강. LG화학 E-4-1이
# '비재생 전력 소비량 **해외** 2025' 5,104 TJ를 전사 총량 자리에 올렸다. 사전에
# '해외 자회사'·'해외 사업장'만 있어 조직어 없는 단독 지역어는 안 걸렸다.
_REGION_PARTIAL_TERMS: tuple[str, ...] = ("해외", "국내")

# 단독 지역어의 예외 — '국내외'는 전 범위를 뜻하므로 부분값이 아니다.
# '국내'가 부분문자열로 걸리는 것을 막는다(과차단 방지).
_REGION_WHOLE_TERMS: tuple[str, ...] = ("국내외", "국내 및 해외", "국내·해외", "국내 해외")

# '국내(지역 기반)'·'국내(시장 기반)'은 조직 범위가 아니라 Scope 2 산정 방법 표지다.
# 단독 '국내' 부분문자열 예외로 두지 않으면 신한 E-3-1 총배출량이 Scope 1 부분값과 동률이 된다.
_REGION_METHOD_TERMS: tuple[str, ...] = (
    "국내(지역 기반)", "국내 (지역 기반)", "국내(지역기반)", "국내 (지역기반)",
    "국내(시장 기반)", "국내 (시장 기반)", "국내(시장기반)", "국내 (시장기반)",
)

# (4) 코드별 negative keyword — 지표 정합 축. 충돌하면 후보에서 제외.
# 실측 근거는 docs/집계어휘_실태_2026-07-26.md 참조.
#   E-3-1(Scope1+2)  ← 같은 풀에 Scope 3 · 1+2+3 노드가 섞여 있다(3,560,074 오독 사례)
#   E-3-2(Scope3)    ← Scope 1/2 단독 노드
#   E-4-2(재생 비율) ← '비재생에너지 사용률 합계' 95.3%가 총량 어휘를 갖고 있어 이기고 있었다
#   E-5-1(용수 취수) ← '용수 재활용·재사용량 합계'
#   E-6-1(폐기물)    ← '재활용/재사용'(= E-6-2 소관), '방사성'(하위 분류)
# 주의: negative는 자기 코드에만 적용된다. E-6-1의 '재활용'이 E-6-2로 새면
#       '재활용률' 항목이 통째로 막힌다(음성 테스트로 고정).
_NEGATIVE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "E-3-1": ("scope 3", "scope3", "1+2+3", "가치사슬"),
    "E-3-2": ("scope 1", "scope1", "scope 2", "scope2"),
    "E-4-2": ("비재생",),
    "E-5-1": ("재활용", "재사용"),
    "E-6-1": ("재활용", "재사용", "방사성"),
}

# (5) 코드별 지표 계열 우선순위 — 같은 코드 안의 '다른 개념' 정렬.
# 낮은 인덱스가 우선. 어느 계열에도 안 걸리면 중립(len 값)으로 취급한다.
#   E-6-1: 항목 정의가 '연간 폐기물 배출량(총량)'이므로 발생량 > 처리량.
#          처리량은 매립·소각 등 처분 경로별 부분값이다(실측: 발생량 72,463 ≈
#          처리량 17,694 + 미폐기처리량 52,806). 2026-07-26 사용자 확정.
#   E-4-1: 총 에너지/전력 사용량 > 구매 전력량(조달 내역)
#   E-3-1: Scope 2 이중보고에서 **지역 기반(location-based)이 GHG Protocol 필수 기준**이고
#          시장 기반은 선택적 병기다. 실측에서 두 합계(396,152 지역 / 389,933 시장)가
#          다른 축 전부 동률이라 이 계열 구분 없이는 id 문자열로 갈렸다.
#   E-6-2: 항목 정의가 '총 폐기물 대비 재활용 비율'이므로 폐기물 재활용률 > 자재별 재활용률
#          (실측: '플라스틱 재활용률' 56.9%가 구분어가 없어 '국내 사업장 …' 92.9%를 이겼다).
_METRIC_FAMILY: dict[str, tuple[str, ...]] = {
    "E-6-1": ("발생량", "배출량", "처리량"),
    "E-4-1": ("에너지 사용량", "전력 사용량", "사용량", "구매한 전력량"),
    "E-3-1": ("지역 기반", "지역기반"),
    # 실측 시계열(연결 일부)과 연도 없는 각주 예상치가 함께 있으면 실측 계열을 우선한다.
    # 현대모비스: 2024 실측 3,136,024 vs 각주 예상치 14,160,000.
    "E-3-2": ("연결(일부)", "연결 일부"),
    "E-6-2": ("폐기물 재활용률", "폐기물 재활용", "재활용 비율", "순환이용률"),
}

# (6) 세부 분해 어휘 — 조달 방식·처리 경로·종류별 내역. 총량이 아니다.
# E-4-1은 총량 어휘('합계')가 hint에 아예 없어(24개 중 0개) 집계 축만으로는 안 갈린다.
# 실측에서 오선택된 4,654 MWh가 '전력구매계약(On-site PPA)'였으므로 이 축이 필요하다.
_BREAKDOWN_TERMS: tuple[str, ...] = (
    # 조달 방식별(E-4-1)
    "전력구매계약", "ppa", "녹색요금제", "녹색전력상품", "자가발전",
    "구매한 전력", "비재생 전력", "재생 전력",
    # 처리 경로별(E-6-1)
    "매립", "소각", "미폐기", "폐기·처리 과정 알 수 없음",
    # 종류별 — '일반/지정' 폐기물 구분, '방사성' 등은 총량의 하위 분류다.
    # 실측에서 '일반 폐기물 발생량' 57,719가 총량 '폐기물 발생량' 72,463과
    # 다른 축 전부 동률이라 이 축 없이는 id 문자열로 갈렸다.
    "일반 폐기물", "지정 폐기물", "방사성",
    # 지리적 부분집합 — '물 위험/스트레스 지역'은 전사 취수량의 부분이다.
    "물 위험", "스트레스 지역", "플라스틱", "알루미늄",
    # Scope 3 카테고리별(E-3-2) — 2026-07-28 5개사 실측 보강. GHG Protocol 15개
    # 카테고리는 Scope 3 총합의 하위 분해다. NAVER E-3-2가 카테고리 1 하나
    # ('Upstream 구매 제품 및 서비스' 71,385)를 Scope 3 총합 자리에 올렸다.
    "upstream", "downstream", "category", "카테고리",
)


def _norm(text: str | None) -> str:
    """hint 정규화 — 소문자화 + 공백 축약. 괄호는 남긴다('국내(별도)' 판정에 필요)."""
    if not text:
        return ""
    return " ".join(str(text).replace(" ", " ").split()).lower()


def _node_hint(node: Any) -> str:
    """노드에서 hint 문자열을 복원.

    EvidenceNode는 hint 필드를 따로 갖지 않고 raw_text에
    `"{metric_hint}={value}{unit} ({source_file})"` 형태로 담는다(merge_ocr_extraction).
    '=' 앞부분이 hint다. 형식이 다르면 raw_text 전체를 쓴다(정보 손실 방지).
    """
    hint = getattr(node, "hint", None)
    if hint:
        return _norm(hint)
    raw = getattr(node, "raw_text", "") or ""
    return _norm(raw.split("=", 1)[0] if "=" in raw else raw)


def _has_any(hint: str, terms: Iterable[str]) -> bool:
    return any(t in hint for t in terms)


_TARGET_TERMS: tuple[str, ...] = ("목표", "예상", "전망", "계획", "예정")
_COMPONENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Scope 3 카테고리·상하류는 어떤 총량 코드에서도 구성요소다.
    re.compile(r"(?:category|카테고리)\s*\d+", re.I),
    re.compile(r"(?:upstream|downstream|업스트림|다운스트림)", re.I),
    # 괄호 안 물질기호: (SS), (NOx), (COD), (T-N), (Dust) 등.
    re.compile(r"\((?:[A-Z][A-Za-z0-9]*(?:-[A-Z])?|Dust|VOCs?|HAPs?)\)", re.I),
    # 공통적인 수식어+지표명 구조. 코드별 단어 목록이 아니라 지표의 부분집합 문법이다.
    re.compile(r"재생\s*원(?:부)?자재"),
    re.compile(r"주요\s+원(?:부)?자재"),
    re.compile(r"(?:재생|비재생|화석|원자력)\s*(?:에너지|전력)"),
    re.compile(r"에너지원을\s*알\s*수\s*없는\s*에너지"),
    re.compile(r"(?:도시가스|천연가스|휘발유|경유|lng|lpg|석탄)\s*(?:사용량|소비량)", re.I),
    re.compile(r"(?:지표수|지하수|상수도?|해수)\s*(?:취수량|사용량)"),
    re.compile(r"(?:국가|사업장|법인|지역|제품|원료|수원|에너지원)별"),
)


def _future_hint_year(hint: str, report_year: int | None) -> int | None:
    """hint의 미래연도를 돌려준다. 보고연도+1은 최신 증빙이므로 실적으로 둔다."""
    if report_year is None:
        return None
    years = [int(y) for y in re.findall(r"20\d{2}", hint)]
    future = [y for y in years if y - report_year >= 2]
    return min(future) if future else None


def _is_pollutant_component(hint: str) -> bool:
    """오염물질 지표에서 특정 물질명 하나가 붙은 구조를 일반 문법으로 잡는다."""
    for stem in ("대기오염물질", "대기오염", "수질오염물질", "수질오염"):
        if stem not in hint:
            continue
        tail = hint.split(stem, 1)[1].strip()
        if tail.startswith("배출량"):
            detail = tail[len("배출량"):]
        elif "배출량" in tail:
            detail = tail.split("배출량", 1)[0]
        else:
            continue
        detail = re.sub(r"20\d{2}년?", "", detail)
        detail = re.sub(r"(?:합계|총계|전체|전사|total)", "", detail, flags=re.I)
        return bool(detail.strip(" \t()·-/"))
    return False


def classify_common_value_role(
    hint: str | None, *, report_year: int | None = None,
) -> ValueRole:
    """코드와 무관한 공통 문법으로 값의 역할을 판정한다.

    우선순위는 target → component → total이다. 따라서 '지표수 취수량 합계'처럼
    구성요소 안의 합계가 전체 총량으로 승격되지 않는다.
    """
    normalized = _norm(hint)
    if _has_any(normalized, _TARGET_TERMS) or _future_hint_year(normalized, report_year):
        return "target"
    # 기존 분해·조직범위 축도 역할 하나로 수렴시킨다. '열회수소각 포함'처럼 범위를
    # 넓히는 표현은 종전 예외를 유지한다.
    if (_has_any(normalized, _BREAKDOWN_TERMS) and "포함" not in normalized):
        return "component"
    if _has_any(normalized, _PARTIAL_TERMS) or _has_region_partial(normalized):
        return "component"
    # 비율 지표의 분자는 지표 정의 자체다. 미래목표·지역/카테고리 분해가 아니라면
    # '재생/재사용/재활용'이라는 말만으로 구성요소 취급하지 않는다.
    if re.search(r"(?:비율|비중|률|율)(?:\s|$|\(|20\d{2})", normalized):
        return "total"
    if any(p.search(normalized) for p in _COMPONENT_PATTERNS):
        return "component"
    if _is_pollutant_component(normalized):
        return "component"
    if _has_any(normalized, _TOTAL_TERMS) or "전체" in normalized:
        return "total"
    return "unknown"


def classify_value_role(
    code: str, node_or_hint: Any, *, report_year: int | None = None,
) -> ValueRole:
    """공통 역할 판정에 최소 코드 예외를 적용한다.

    5개사 실측 6개 회귀 유형 중 공통 문법이 5개를 해결했다. 이후 전수 풀 회귀에서
    필요한 예외는 3개 계열뿐이었다: E-3-1 Scope 결합/단독, E-4-1 범위가 소실된
    에너지값, E-7 물질 alias. E-7 어휘는 새 사전이 아니라 kesg_items를 재사용한다.
    """
    hint = node_or_hint if isinstance(node_or_hint, str) else _node_hint(node_or_hint)
    normalized = _norm(hint)
    role = classify_common_value_role(normalized, report_year=report_year)
    base_code = code.split("__", 1)[0]
    if base_code != "E-3-1":
        if role != "unknown":
            return role
        # Scope 3 무수식 추론값은 모비스 각주 예상치 실측이라 총량으로 승격하지 않는다.
        if (base_code == "E-3-2" and not isinstance(node_or_hint, str)
                and getattr(node_or_hint, "period_inferred", False)):
            return "unknown"
        # 확장 풀의 '에너지 사용량'은 조직 범위가 소실된 값이 다수라 판정보류다.
        # 반면 기존 모비스 기준값인 정확한 전력 사용량은 검증된 총량 표현이다.
        if base_code == "E-4-1":
            return "total" if normalized == "전력 사용량" else "unknown"
        # K-ESG 단일 출처의 항목명/충분히 긴 alias가 그대로 등장하면 총량 지표로 본다.
        # NOx·SS 같은 짧은 물질 alias는 제외해 오염물질 하나가 총량으로 승격되지 않는다.
        from ..knowledge.kesg_items import by_code

        item = by_code(base_code)
        if base_code in {"E-7-1", "E-7-2"} and item and any(
            _norm(term) in normalized for term in item.search_terms[1:]
        ):
            return "component"
        semantic_terms = (item.name, *item.search_terms) if item else ()
        if any(_norm(term) in normalized for term in semantic_terms if len(_norm(term)) >= 5):
            return "total"
        # 0건이 완전한 공시인 위반·사고 지표는 부분값으로 낮추지 않는다.
        if item and item.unit == "건" and any(
            term in f"{item.name} {item.description}"
            for term in ("위반", "사고", "침해", "제재", "민원")
        ):
            return "total"
        return role

    compact = re.sub(r"\s+", "", normalized).replace("scope", "s")
    has_scope1 = bool(re.search(r"s1", compact))
    has_scope2 = bool(re.search(r"s2", compact) or re.search(r"s1[,/·+&]2", compact))
    combined = has_scope1 and has_scope2 and "외" not in normalized
    if combined:
        return "total"
    if re.search(r"s[123]", compact) or re.search(r"(?:category|카테고리)\s*\d+", normalized):
        return "component"
    # Scope 표지가 없는 '온실가스 배출량 총계'는 자회사/국가 부분합일 수 있어 총량을
    # 입증하지 못한다. 확장 풀 실측에서 이 값들이 결합 Scope1+2 총량을 밀어냈다.
    return "unknown" if role == "total" else role


def is_derived_hint(hint: str | None) -> bool:
    """파생·비실적 hint 판정 — 우선순위 1단계(hard 배제). 공개(테스트·감사용)."""
    return _has_any(_norm(hint), _DERIVED_TERMS)


def _conflicts_metric(code: str, hint: str) -> bool:
    """코드별 negative keyword 충돌 — 우선순위 2단계."""
    return _has_any(hint, _NEGATIVE_KEYWORDS.get(code, ()))


def _family_rank(code: str, hint: str) -> int:
    """지표 계열 순위 — 우선순위 3단계. 낮을수록 우선, 미해당은 중립."""
    families = _METRIC_FAMILY.get(code)
    if not families:
        return 0
    for i, fam in enumerate(families):
        if fam in hint:
            return i
    return len(families)


def _breakdown_rank(hint: str) -> int:
    """세부 분해 여부 — 우선순위 4단계. 0=총량성, 1=분해값."""
    return 1 if _has_any(hint, _BREAKDOWN_TERMS) else 0


def _has_region_partial(hint: str) -> bool:
    """단독 지역어('해외'·'국내')로 범위가 한정됐는가 — 2026-07-28 보강.

    '국내외'·'국내 및 해외'처럼 전 범위를 뜻하는 표현은 제외한다('국내'가
    부분문자열로 걸려 정상 총량이 부분값으로 강등되는 것을 막는다).
    '국내(별도)'·'해외 자회사'는 이미 _PARTIAL_TERMS가 잡으므로 중복 판정은 무해하다.
    """
    if _has_any(hint, _REGION_WHOLE_TERMS) or _has_any(hint, _REGION_METHOD_TERMS):
        return False
    return _has_any(hint, _REGION_PARTIAL_TERMS)


def _aggregation_rank(hint: str) -> int:
    """집계 순위 — 우선순위 5단계. 0=총량 어휘, 1=구분어 없음, 2=부분 어휘.

    부분 어휘는 총량 어휘보다 더 구체적인 범위 한정이다. 지표명 자체에 '합계'가 들어간
    '온실가스 배출량 합계 (...) 해외 자회사'를 전사 총량으로 오인하지 않도록 먼저 본다.
    """
    if _has_any(hint, _PARTIAL_TERMS) or _has_region_partial(hint):
        return 2
    if _has_any(hint, _TOTAL_TERMS):
        return 0
    return 1


def is_partial_aggregate(
    node: Any, code: str | None = None, *, report_year: int | None = None,
) -> bool:
    """선택된 노드가 **전사 총량이 아닌 부분값**인가 — 원장 표기용 조회 함수.

    2026-07-28 5개사 일반화에서 드러난 결함 (a): `_PARTIAL_TERMS`는 후순위 축이지
    배제가 아니라, 후보가 전부 부분값이면 그중 하나가 이긴다. 총량 후보가 아예
    없는 풀이 실제로 있었다:

      · LG화학 E-4-1 — 36노드에 '합계/총계' 어휘가 0개('비재생 전력 소비량 해외')
      · NAVER  E-3-2 — 노드 1개, 그게 Scope 3 카테고리 1('구매 제품 및 서비스')

    **값은 버리지 않고 싣되 부분값임을 표기한다**(2026-07-28 사용자 확정). 배제하면
    커버리지가 더 떨어지고(이미 5~7항목/17), D1은 이 오류를 못 잡는다(원장·노드가
    같은 값이라 Δ=0). 표기가 유일한 방어선이므로 원장 표까지 노출된다.

    판정 축은 선택 규칙의 두 축을 그대로 쓴다 — 둘 다 코드와 무관하므로 인자는 노드뿐이다.
      · 4단계 세부 분해(`_BREAKDOWN_TERMS`) — 조달방식·처리경로·Scope 3 카테고리별
      · 5단계 집계 부분(`_PARTIAL_TERMS` + 단독 지역어)

    예외 — '… 포함'은 범위 **확대**다. 실측 오표기: LG화학 E-6-2 '폐기물 재활용률
    (열회수소각 포함)' 91%가 `_BREAKDOWN_TERMS`의 '소각'에 걸려 부분값으로 표기됐다.
    랭킹 축(4단계)은 그대로 두고 표기만 예외 처리한다 — 우선순위 구조는 건드리지 않는다.
    """
    if node is None:
        return False
    if code is not None:
        # 판정 불가는 총량임을 입증하지 못한 상태이므로 D6에는 부분 공시로 보수적으로 전달한다.
        return classify_value_role(code, node, report_year=report_year) in (
            "component", "unknown")
    hint = _node_hint(node)
    if "포함" in hint:
        return False
    return _breakdown_rank(hint) == 1 or _aggregation_rank(hint) == 2


def _water_mass_volume_pair(code: str, unit_a: str | None, unit_b: str | None) -> bool:
    """물 지표에서만 m³와 ton을 동등 취급한다(물의 밀도 약 1 t/m³)."""
    if code.split("__", 1)[0] not in {"E-5-1", "E-5-2"}:
        return False
    volume = {"m3", "m³", "㎥", "m^3"}
    mass = {"ton", "톤", "t"}
    a = str(unit_a or "").lower().replace(" ", "")
    b = str(unit_b or "").lower().replace(" ", "")
    return (a in volume and b in mass) or (a in mass and b in volume)


def _unit_rank(
    node_unit: str | None, expected_unit: str | None, code: str = "",
) -> int:
    """단위 정합 순위 — 우선순위 6단계. 0=동일, 1=환산 가능, 2=그 외/미상.

    표기 차이('ton' vs '톤', 'tCO2 eq' vs 'tCO2eq')는 normalize_unit으로 흡수한다.
    E-4-1(TJ)에서 TJ 노드가 MWh 노드를 이기게 하는 단계다.
    """
    if not expected_unit:
        return 1
    if _water_mass_volume_pair(code, node_unit, expected_unit):
        return 1
    from ..rag_gates.units import normalize_unit, units_compatible

    na, nb = normalize_unit(str(node_unit or "")), normalize_unit(str(expected_unit))
    if na is None or nb is None:
        # 정규화 사전에 없는 단위는 문자열 비교로 최선 판정. 2026-07-28: 아래
        # normalize_to_item_unit과 같은 판정을 써야 한다 — 선택 축과 저장 축이 'ton CO2 eq'를
        # 다르게 보면 랭킹은 2(그 외)인데 저장은 동일 단위로 통일되는 모순이 생긴다.
        from ..layer1_extract import _relaxed_unit

        a, b = _relaxed_unit(str(node_unit or "")), _relaxed_unit(str(expected_unit))
        if a and a == b:
            return 0
        return 2
    if na == nb:
        return 0
    return 1 if units_compatible(na, nb) else 2


def _expected_unit(code: str) -> str | None:
    """K-ESG 항목 정의 단위. 코드가 사전에 없으면 None(단위 축 중립)."""
    from ..knowledge.kesg_items import by_code

    item = by_code(code.split("__", 1)[0])
    return item.unit if item else None


def _keep_min(candidates: list[Any], rank: Any) -> list[Any]:
    """rank가 최소인 후보만 남긴다 — 단계별 필터의 단위 연산.

    사전식 비교(`min(key=...)`)와 동치다: 상위 축에서 최소가 아닌 후보는 어떤 하위 축
    값을 가져도 이길 수 없으므로 미리 떨어뜨려도 결과가 같다. 단계별로 쪼개는 이유는
    8단계(값 최빈)가 **그 시점의 생존 집합**을 봐야 하기 때문이다 — 정렬 키에 넣으면
    집합을 볼 수 없다(§값 최빈의 ㄱ 형태가 이 실패다).
    """
    best = min(rank(c) for c in candidates)
    return [c for c in candidates if rank(c) == best]


def _keep_value_mode(candidates: list[Any]) -> list[Any]:
    """값 최빈 필터 — 우선순위 8단계. **최빈값이 유일할 때만** 좁힌다.

    호출 시점이 중요하다: 7단계(연도)까지 이미 좁혀진 집합에만 적용된다. 연도를 먼저
    좁히지 않으면 시계열 정체를 최빈으로 오독한다(모듈 docstring §값 최빈).

    빈도 1위가 둘 이상이면 **아무것도 하지 않는다** — 근거 없이 답을 바꾸지 않고
    9단계(최신 → 고신뢰 → id)에 맡긴다.
    """
    freq: dict[Any, int] = {}
    for c in candidates:
        value = getattr(c, "value", None)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            freq[float(value)] = freq.get(float(value), 0) + 1
    if not freq:
        return candidates
    top = max(freq.values())
    winners = [v for v, n in freq.items() if n == top]
    if len(winners) != 1:
        return candidates                        # 빈도 동률 → 무개입
    mode = winners[0]
    narrowed = [c for c in candidates
                if isinstance(getattr(c, "value", None), (int, float))
                and float(c.value) == mode]
    return narrowed or candidates


def select_representative_node(
    code: str,
    nodes: Iterable[Any],
    *,
    report_year: int | None = None,
) -> Any | None:
    """코드의 대표 노드 하나를 결정적으로 고른다. 후보가 없으면 None(→ 미공시).

    Parameters
    ----------
    code        : K-ESG 코드("E-3-1"). '{code}__projection'도 받아 기본 코드로 해석한다.
    nodes       : 같은 코드의 EvidenceNode들(원장·D1 양쪽에서 같은 풀을 넘긴다).
    report_year : 7단계(연도 근접) 기준 연도. None이면 이 축을 건너뛴다(최신 우선 폴백).

    Returns
    -------
    EvidenceNode | None — None이면 호출부가 미공시로 처리하고 노드는 감사추적용 보존.

    Notes
    -----
    축 순서는 모듈 docstring §우선순위 그대로다 — 계열(3) → 분해(4) → 집계(5) →
    단위(6) → **연도(7) → 값 최빈(8)** → 최신·고신뢰·id(9).

    모든 후보가 1·2단계에서 배제되면 None을 돌린다(잘못된 값보다 미공시).
    **후보가 1개여도 hard 배제는 적용된다**(2026-07-28 변경). 종전에는 과차단 방지를
    위해 유일 후보를 무조건 채택했지만, 그 우회로 LG화학 E-5-1의 '일평균 산업용수
    공급량' 540,000 ton이 연간 사용량 자리에 실렸다(노드 1개 → 규칙 미개입, 365배 오류).
    3~9단계(순위 축)는 후보가 1개면 결과가 같으므로 우회를 없애도 순위 판정은 불변이다.
    """
    pool = [n for n in nodes if n is not None]
    if not pool:
        return None

    base_code = code.split("__", 1)[0]
    expected = _expected_unit(base_code)

    def _zero_is_valid() -> bool:
        """0건 자체가 완전한 공시인 위반·사고 계열만 0을 대표값으로 허용한다."""
        from ..knowledge.kesg_items import by_code

        item = by_code(base_code)
        if not item or item.unit != "건":
            return False
        basis = f"{item.name} {item.description}"
        return any(term in basis for term in ("위반", "사고", "침해", "제재", "민원"))

    if not _zero_is_valid():
        pool = [n for n in pool if getattr(n, "value", None) != 0]
        if not pool:
            return None

    # 1·2단계 — hard 배제. 남는 게 없으면 None(미공시).
    survivors = [
        n for n in pool
        if not is_derived_hint(_node_hint(n))
        and not _conflicts_metric(base_code, _node_hint(n))
        and ("__projection" in code or classify_value_role(
            base_code, n, report_year=report_year) != "target")
    ]
    if not survivors:
        return None
    if len(survivors) == 1:
        survivors[0].value_role = classify_value_role(
            base_code, survivors[0], report_year=report_year)
        return survivors[0]

    def _period(node: Any) -> int:
        return getattr(node, "period", 0) or 0

    def _inferred(node: Any) -> int:
        """period가 폴백값이면 1(후순위). 필드 없는 노드는 0 — 기존 동작 보존."""
        return 1 if getattr(node, "period_inferred", False) else 0

    # 3~7단계 — 순위 축을 단계별로 좁힌다. 사전식 비교와 동치이지만(_keep_min 주석),
    # 8단계가 '그 시점의 생존 집합'을 봐야 해서 정렬 키 한 방으로는 안 된다.
    def _role_rank(node: Any) -> int:
        role = classify_value_role(base_code, node, report_year=report_year)
        if role == "total":
            return 0
        if role == "unknown" and not getattr(node, "period_inferred", False):
            return 1
        if role == "component":
            return 2
        if role == "unknown":
            return 3
        return 4

    survivors = _keep_min(survivors, _role_rank)
    survivors = _keep_min(survivors, lambda n: _family_rank(base_code, _node_hint(n)))
    survivors = _keep_min(survivors, lambda n: _breakdown_rank(_node_hint(n)))
    survivors = _keep_min(survivors, lambda n: _aggregation_rank(_node_hint(n)))
    survivors = _keep_min(
        survivors, lambda n: _unit_rank(getattr(n, "unit", None), expected, base_code))
    # 7) 연도 — **최빈보다 앞이어야 한다**: 연도를 먼저 좁히지 않으면 최빈이
    #    '반복 언급'이 아니라 '시계열 정체'를 집는다(모듈 docstring §값 최빈의
    #    삼성전기 E-6-2 두 계열). 되돌리면 회귀가 난다.
    #    report_year가 없으면 최신 연도로 좁혀 같은 불변식을 지킨다 — 종전 정렬 키의
    #    `-period` 항과 동치이고(그 항이 연도 다음이었다), 그 자리를 8단계보다 앞으로
    #    끌어올리지 않으면 report_year=None 경로만 정체 효과에 노출된다.
    #    period_inferred(2026-07-29)는 **같은 순위 안에서만** 후순위다(튜플 2번째 항).
    #    원문에서 연도를 못 읽어 report_year로 채운 노드는 근접도가 0으로 나와 확정 노드와
    #    동률이 되는데, 그 동률을 근거 없이 이기면 안 된다. 축 순서는 그대로다 — 연도는
    #    여전히 8단계(값 최빈)보다 앞이다.
    if report_year is not None:
        survivors = _keep_min(
            survivors, lambda n: (abs(_period(n) - report_year), _inferred(n)))
    else:
        survivors = _keep_min(survivors, lambda n: (-_period(n), _inferred(n)))

    # 8) 값 최빈 — 최빈값이 유일할 때만 좁힌다. 동률이면 무개입.
    survivors = _keep_value_mode(survivors)

    # 9) 최신 → 고신뢰 → str(id). 마지막 항이 완전 결정성 장치다 — 제거하지 마라.
    selected = min(survivors, key=lambda n: (
        -_period(n),
        -(getattr(n, "confidence", 0.0) or 0.0),
        str(getattr(n, "id", "")),
    ))
    selected.value_role = classify_value_role(base_code, selected, report_year=report_year)
    return selected


def normalize_to_item_unit(
    code: str,
    value: float,
    unit: str | None,
) -> tuple[float, str, str | None]:
    """노드 값을 K-ESG 항목 정의 단위로 환산해 (값, 단위, 플래그)를 돌린다.

    기존 `units_compatible`은 '비교 가능한가'만 판정하고 환산은 하지 않아, 원장에
    ton 값이 kg 항목(E-7-1 대기오염물질·E-7-2 수질오염물질)에 그대로 들어가 1,000배
    어긋났다. E-4-1도 항목 단위가 TJ인데 MWh 값이 실렸다. 여기서 실제로 환산한다.

    반환 플래그:
      · None            — 환산 완료 또는 이미 동일 단위(표기 차이 흡수: 'ton'→'톤')
      · 'unit_suspect'  — 환산 불가(다른 환산군) → 원 단위·원 값 유지(기존 동작)

    표기만 다른 경우(`tCO2 eq` vs `tCO2eq`, `ton` vs `톤`)는 값을 건드리지 않고
    항목 단위 표기로 통일한다(무해한 불일치 제거).
    """
    from ..rag_gates.units import convert_to_common, normalize_unit

    expected = _expected_unit(code)
    if not expected or not isinstance(value, (int, float)) or isinstance(value, bool):
        return (value, unit or (expected or ""), None)
    if not unit:
        return (value, expected, None)
    if _water_mass_volume_pair(code, unit, expected):
        # 물은 밀도 약 1 t/m³이므로 수치가 같다. 다른 코드에는 절대 적용하지 않는다.
        return (value, expected, None)

    na, nb = normalize_unit(str(unit)), normalize_unit(str(expected))
    if na is None or nb is None:
        # 정규화 사전 미등재 단위 — 표기 차이만 판정. 2026-07-28: 종전엔 공백·대소문자만
        # 지웠기 때문에 신한 실측 'ton CO2 eq'가 'tCO2eq'와 다르다고 판정돼 값이 맞는데도
        # unit_suspect가 붙었다('tCO2 eq'는 사전에 있어 통과). layer1_extract._relaxed_unit이
        # 이미 ton→t 별칭 축약을 갖고 있으므로 중복 구현하지 않고 그것을 쓴다.
        from ..layer1_extract import _relaxed_unit

        if _relaxed_unit(str(unit)) == _relaxed_unit(str(expected)):
            return (value, expected, None)
        return (value, unit, "unit_suspect")
    if na == nb:
        return (value, expected, None)          # 표기 차이만 — 값 불변, 표기 통일
    converted = convert_to_common(float(value), na, nb)
    if converted is None:
        return (value, unit, "unit_suspect")     # 다른 환산군 — 원 단위 유지
    return (round(converted, 6), expected, None)


__all__ = [
    "ValueRole",
    "classify_common_value_role",
    "classify_value_role",
    "select_representative_node",
    "is_derived_hint",
    "is_partial_aggregate",
    "normalize_to_item_unit",
]
