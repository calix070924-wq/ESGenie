"""대표 노드 선택 — 같은 K-ESG 코드의 여러 OCR 노드 중 '원장에 쓸 하나'를 고른다.

## 왜 필요한가

L0가 hint를 서술적으로 잘 뽑아준다(`용수 사용량(취수량) 합계 2024년`,
`폐기물 처리량(매립, 소각 등) 국내(별도)`, `온실가스 감축 효과`). 그런데 지금까지
대표 노드 선택은 **연도 하나만** 봤다 — 같은 연도 안에서는 사실상 임의 선택이라
정답 노드가 같은 풀에 나란히 있어도 파생값·부분값을 골랐다(현대모비스 E-3-1이
`온실가스 감축 효과` 1,161,214을 배출량으로 원장에 올린 사례).

**오염이 아니라 선택 실패다.** 그래서 이 모듈은 값이 아니라 hint 위에 규칙을 세운다.

## period를 믿지 않는다

원문 표의 연도 열이 한 period로 뭉개져 있다(동일 hint · 값만 다른 노드가 E-6-1에서
51건 초과). 연도는 **최후 tie-breaker**로만 쓴다. 표 파싱 교정은 L0 소관이므로
여기서는 "연도에 의존하지 않는다"만 보장한다.

## 우선순위 (앞 단계에서 후보가 갈리면 뒤는 안 본다)

  1) 배제(hard)  — 파생·비실적 어휘(감축·효과·예상·증감·원단위·목표·제로화·전환량 …)
  2) 지표 정합   — 코드별 negative keyword 충돌(E-3-1 ← 'Scope 3'·'1+2+3' 등)
  3) 지표 계열   — 코드가 요구하는 계열 우선(E-6-1은 '발생량' > '처리량')
  4) 세부 분해   — 조달방식·처리경로별 분해값 후순위(E-4-1의 PPA/vPPA/녹색요금제)
  5) 집계        — '합계·총계·전사·Total' > 구분어 없음 > '국내(별도)·자회사·국가명·공장'
  6) 단위 정합   — kesg_items.unit과 동일 > 환산 가능 > 그 외 (E-4-1은 TJ 우선)
  7) 연도        — 여기까지 동률일 때만 report_year 근접
  8) 그래도 동률 / 후보 0 → None (원장은 미공시 + confidence_flag)

8번이 중요하다: **잘못된 값보다 미공시가 낫다**(라벨링 §3-1 원칙).

## 두 호출부가 반드시 이 함수를 쓴다

  · `ssot_pipeline._merge_ssot_evidence` — 원장 표시값
  · `layer3_detect._score_d1_numeric`   — D1 비교 대상

둘이 다른 노드를 고르면 데이터가 옳아도 D1이 발화한다(구조적 오탐). 대칭이 핵심이다.
"""

from __future__ import annotations

from typing import Any, Iterable

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
_DERIVED_TERMS: tuple[str, ...] = (
    "감축", "효과", "예상", "증감", "원단위", "1톤당", "톤당",
    "제로화", "목표", "전환량", "절감", "누적", "집약도", "intensity",
    "전망", "계획", "예정", "로드맵", "선언", "회수량", "대비",
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


def _aggregation_rank(hint: str) -> int:
    """집계 순위 — 우선순위 5단계. 0=총량 어휘, 1=구분어 없음, 2=부분 어휘."""
    if _has_any(hint, _TOTAL_TERMS):
        return 0
    if _has_any(hint, _PARTIAL_TERMS):
        return 2
    return 1


def _unit_rank(node_unit: str | None, expected_unit: str | None) -> int:
    """단위 정합 순위 — 우선순위 6단계. 0=동일, 1=환산 가능, 2=그 외/미상.

    표기 차이('ton' vs '톤', 'tCO2 eq' vs 'tCO2eq')는 normalize_unit으로 흡수한다.
    E-4-1(TJ)에서 TJ 노드가 MWh 노드를 이기게 하는 단계다.
    """
    if not expected_unit:
        return 1
    from ..rag_gates.units import normalize_unit, units_compatible

    na, nb = normalize_unit(str(node_unit or "")), normalize_unit(str(expected_unit))
    if na is None or nb is None:
        # 정규화 사전에 없는 단위는 문자열 비교로 최선 판정(공백·대소문자 무시).
        a = _norm(node_unit).replace(" ", "")
        b = _norm(expected_unit).replace(" ", "")
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
    report_year : 최후 tie-breaker 기준 연도. None이면 최신 연도 폴백.

    Returns
    -------
    EvidenceNode | None — None이면 호출부가 미공시로 처리하고 노드는 감사추적용 보존.

    Notes
    -----
    모든 후보가 1·2단계에서 배제되면 None을 돌린다(잘못된 값보다 미공시).
    후보가 1개면 규칙과 무관하게 그것을 돌린다(과차단 방지).
    """
    pool = [n for n in nodes if n is not None]
    if not pool:
        return None
    if len(pool) == 1:
        return pool[0]

    base_code = code.split("__", 1)[0]
    expected = _expected_unit(base_code)

    # 1·2단계 — hard 배제. 남는 게 없으면 None(미공시).
    survivors = [
        n for n in pool
        if not is_derived_hint(_node_hint(n))
        and not _conflicts_metric(base_code, _node_hint(n))
    ]
    if not survivors:
        return None

    def sort_key(node: Any) -> tuple:
        hint = _node_hint(node)
        period = getattr(node, "period", 0) or 0
        # 7단계(연도)는 여기까지 동률일 때만 작동한다. report_year가 없으면 최신 우선.
        year_rank = abs(period - report_year) if report_year is not None else 0
        return (
            _family_rank(base_code, hint),                     # 3) 지표 계열
            _breakdown_rank(hint),                             # 4) 세부 분해
            _aggregation_rank(hint),                           # 5) 집계
            _unit_rank(getattr(node, "unit", None), expected),  # 6) 단위 정합
            year_rank,                                         # 7) 연도(최후)
            -period,                                           # 동률 시 최신
            -(getattr(node, "confidence", 0.0) or 0.0),         # 동률 시 고신뢰
            str(getattr(node, "id", "")),                       # 완전 결정성 보장
        )

    return min(survivors, key=sort_key)


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

    na, nb = normalize_unit(str(unit)), normalize_unit(str(expected))
    if na is None or nb is None:
        # 정규화 사전 미등재 단위 — 문자열 정규화로 표기 차이만 판정.
        if _norm(unit).replace(" ", "") == _norm(expected).replace(" ", ""):
            return (value, expected, None)
        return (value, unit, "unit_suspect")
    if na == nb:
        return (value, expected, None)          # 표기 차이만 — 값 불변, 표기 통일
    converted = convert_to_common(float(value), na, nb)
    if converted is None:
        return (value, unit, "unit_suspect")     # 다른 환산군 — 원 단위 유지
    return (round(converted, 6), expected, None)


__all__ = [
    "select_representative_node",
    "is_derived_hint",
    "normalize_to_item_unit",
]
