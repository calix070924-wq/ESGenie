"""L0-B — OCR 일관성 검증 (Tier A: 문서 내부 산술 정합).

중소 협력사 증빙엔 DART 같은 외부 비교 기준이 없다(기존 cross_check 무용).
그래서 '문서가 스스로 만족해야 하는 산술 항등식'으로 OCR 오인식을 잡는다.

  · 전기: 4개 요금 합=전기요금계, ×10%/×3.7%+10원절사=청구금액
  · 가스: 기본+사용요금=공급가액계, ×10%+절사=청구금액
  · 폐기물: 재활용+소각+매립=총배출량, 재활용량/총량=재활용비율

기본 동작은 '탐지+플래깅'이다. 잔차가 콤마/자릿수로 결정적으로 설명되는
케이스만 auto-correct하고, 나머지는 confidence 강등 → HITL 검증 큐로 보낸다.

설계: docs/OCR일관성검증_TierA_설계.md
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Callable

# ---- 값 추출 헬퍼 -----------------------------------------------------------

_PAREN_RE = re.compile(r"\([^)]*\)")
_NUM_RE = re.compile(r"\d[\d,]*\.?\d*")


def _strip_parens(text: str) -> str:
    """괄호 그룹 제거 — '기본요금 (800kW × 8,320원) 6,656,000' → 라벨 옆 금액만 남김."""
    return _PAREN_RE.sub(" ", text)


def _value_after(text: str, label: str, *, window: int = 48) -> float | None:
    """괄호 제거된 텍스트에서 라벨 직후 첫 숫자를 반환(요약줄·라벨인접 금액용)."""
    i = text.find(label)
    if i < 0:
        return None
    seg = text[i + len(label): i + len(label) + window]
    m = _NUM_RE.search(seg)
    if not m:
        return None
    try:
        return float(m.group().replace(",", ""))
    except ValueError:
        return None


# ---- 데이터 구조 ------------------------------------------------------------

@dataclass(frozen=True)
class ConsistencyRule:
    """문서 내부 산술 항등식 1개."""
    rule_id: str
    doc_type: str
    description: str
    output_key: str                       # 대조 대상(라벨/특수키)
    compute: Callable[[dict[str, float]], float | None]  # 기대값(입력 부족 시 None)
    tolerance_abs: float = 1.0
    tolerance_rel: float = 0.0
    truncate_won10: bool = False          # 10원 미만 절사 규칙


@dataclass
class ConsistencyFinding:
    rule_id: str
    severity: str                         # "ok" | "fail" | "skipped"
    expected: float | None
    observed: float | None
    residual: float | None = None
    residual_rel: float | None = None
    suggested_fix: float | None = None
    auto_corrected: bool = False
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---- 라벨 카탈로그 (라벨인접 금액으로 신뢰 추출되는 항목) ---------------------

_LABELS: dict[str, tuple[str, ...]] = {
    "kepco_bill": (
        "기본요금", "전력량요금", "기후환경요금", "연료비조정요금",
        "전기요금계", "부가가치세", "전력산업기반기금", "청구금액",
    ),
    "gas_bill": ("기본요금", "사용요금", "공급가액 계", "부가가치세", "청구금액"),
    "waste_ledger": ("폐기물 총배출량", "재활용량", "재활용 비율", "소각·매립 비율"),
}

# 폐기물 ※주석의 처리방법별 수량(헤더/값 분리 표 대신 결정적 추출)
_WASTE_NOTE_RE = re.compile(
    r"재활용\s*([\d,]+)\s*kg\s*/\s*소각\s*([\d,]+)\s*kg\s*/\s*매립\s*([\d,]+)\s*kg"
)


def _collect_values(raw_text: str, doc_type: str) -> dict[str, float]:
    """raw_text에서 규칙 입력값을 추출한다(라벨인접 + 폐기물 주석 정규식)."""
    stripped = _strip_parens(raw_text)
    vals: dict[str, float] = {}
    for label in _LABELS.get(doc_type, ()):
        v = _value_after(stripped, label)
        if v is not None:
            vals[label] = v
    if doc_type == "waste_ledger":
        m = _WASTE_NOTE_RE.search(raw_text)
        if m:
            vals["_재활용"], vals["_소각"], vals["_매립"] = (
                float(x.replace(",", "")) for x in m.groups()
            )
    return vals


# ---- 규칙 카탈로그 (모든 식은 한울정밀 01/02/03 실측으로 성립 확인) ------------

def _sum(*keys: str) -> Callable[[dict[str, float]], float | None]:
    def f(v: dict[str, float]) -> float | None:
        if all(k in v for k in keys):
            return sum(v[k] for k in keys)
        return None
    return f


def _ratio(num: str, den: str, *, pct: bool = True) -> Callable[[dict[str, float]], float | None]:
    def f(v: dict[str, float]) -> float | None:
        if num in v and den in v and v[den]:
            return v[num] / v[den] * (100.0 if pct else 1.0)
        return None
    return f


def _scaled(key: str, factor: float) -> Callable[[dict[str, float]], float | None]:
    def f(v: dict[str, float]) -> float | None:
        return v[key] * factor if key in v else None
    return f


RULES: list[ConsistencyRule] = [
    # ── 전기 ──
    ConsistencyRule(
        "kepco.subtotal", "kepco_bill", "기본+전력량+기후환경+연료비조정 = 전기요금계",
        "전기요금계", _sum("기본요금", "전력량요금", "기후환경요금", "연료비조정요금"),
        tolerance_abs=1.0),
    ConsistencyRule(
        "kepco.vat", "kepco_bill", "전기요금계 × 10% = 부가가치세",
        "부가가치세", _scaled("전기요금계", 0.10), tolerance_abs=1.0),
    ConsistencyRule(
        "kepco.fund", "kepco_bill", "전기요금계 × 3.7% (10원 절사) = 전력산업기반기금",
        "전력산업기반기금", _scaled("전기요금계", 0.037),
        tolerance_abs=1.0, truncate_won10=True),
    ConsistencyRule(
        "kepco.total", "kepco_bill", "전기요금계+부가세+기금 (10원 절사) = 청구금액",
        "청구금액", _sum("전기요금계", "부가가치세", "전력산업기반기금"),
        tolerance_abs=1.0, truncate_won10=True),
    # ── 가스 ──
    ConsistencyRule(
        "gas.supply_total", "gas_bill", "기본요금 + 사용요금 = 공급가액 계",
        "공급가액 계", _sum("기본요금", "사용요금"), tolerance_abs=1.0),
    ConsistencyRule(
        "gas.vat", "gas_bill", "공급가액 계 × 10% = 부가가치세",
        "부가가치세", _scaled("공급가액 계", 0.10), tolerance_abs=1.0),
    ConsistencyRule(
        "gas.total", "gas_bill", "공급가액 계 + 부가세 (10원 절사) = 청구금액",
        "청구금액", _sum("공급가액 계", "부가가치세"),
        tolerance_abs=1.0, truncate_won10=True),
    # ── 폐기물 ──
    ConsistencyRule(
        "waste.method_split", "waste_ledger", "재활용+소각+매립 = 폐기물 총배출량",
        "폐기물 총배출량", _sum("_재활용", "_소각", "_매립"), tolerance_abs=1.0),
    ConsistencyRule(
        "waste.recycle_rate", "waste_ledger", "재활용량 ÷ 총배출량 = 재활용 비율(%)",
        "재활용 비율", _ratio("재활용량", "폐기물 총배출량"), tolerance_abs=0.1),
    ConsistencyRule(
        "waste.disposal_complement", "waste_ledger", "재활용 비율 + 소각·매립 비율 = 100%",
        "소각·매립 비율",
        lambda v: (100.0 - v["재활용 비율"]) if "재활용 비율" in v else None,
        tolerance_abs=0.1),
]


def _truncate10(x: float) -> float:
    return math.floor(x / 10.0) * 10.0


# ---- 검증 엔진 --------------------------------------------------------------

def validate_consistency(ext: Any) -> list[ConsistencyFinding]:
    """OcrExtraction의 raw_text에 doc_type 규칙을 적용해 findings를 만든다.

    입력값이 추출 안 된 규칙은 'skipped'(거짓경보 방지). 식이 안 맞으면 'fail'.
    findings를 ext.router_meta['consistency_findings']에 부착하고 반환한다.
    """
    raw_text = getattr(ext, "raw_text", "") or ""
    doc_type = getattr(ext, "doc_type", "") or ""
    values = _collect_values(raw_text, doc_type)

    findings: list[ConsistencyFinding] = []
    for rule in RULES:
        if rule.doc_type != doc_type:
            continue
        expected = rule.compute(values)
        observed = values.get(rule.output_key)
        if expected is None or observed is None:
            findings.append(ConsistencyFinding(
                rule.rule_id, "skipped", expected, observed,
                detail="입력값 추출 실패 — 규칙 적용 보류"))
            continue
        if rule.truncate_won10:
            expected = _truncate10(expected)
        residual = abs(expected - observed)
        residual_rel = residual / expected if expected else float("inf")
        ok = residual <= rule.tolerance_abs or (
            rule.tolerance_rel and residual_rel <= rule.tolerance_rel)
        findings.append(ConsistencyFinding(
            rule.rule_id, "ok" if ok else "fail", expected, observed,
            round(residual, 4), round(residual_rel, 6),
            detail=rule.description))

    _apply_findings(ext, findings, values)
    if hasattr(ext, "router_meta"):
        ext.router_meta["consistency_findings"] = [f.to_dict() for f in findings]
        if any(f.severity == "fail" and not f.auto_corrected for f in findings):
            ext.router_meta["hitl_required"] = True
    return findings


# ---- 보정 + confidence/HITL 연동 --------------------------------------------

def _looks_like_digit_error(observed: float, expected: float) -> bool:
    """observed가 expected의 콤마/자릿수 단일 오인식(×10^k)으로 설명되는가."""
    if observed == 0 or expected == 0:
        return False
    ratio = expected / observed
    for k in (10, 100, 1000, 0.1, 0.01, 0.001):
        if abs(ratio - k) / k <= 0.01:
            return True
    return False


def _apply_findings(ext: Any, findings: list[ConsistencyFinding],
                    values: dict[str, float]) -> None:
    """fail 규칙에 대해: 매칭 metric confidence 강등, 결정적 콤마오류면 auto-correct."""
    metrics = list(getattr(ext, "metrics", []) or [])
    for f in findings:
        if f.severity != "fail" or f.observed is None or f.expected is None:
            continue
        # 결정적 콤마/자릿수 오류 → output 값을 기대값으로 보정(매칭 metric에 한해 적용)
        digit_err = _looks_like_digit_error(f.observed, f.expected)
        if digit_err:
            f.suggested_fix = f.expected
        for m in metrics:
            mv = getattr(m, "value", None)
            if mv is None or abs(float(mv) - f.observed) > 1e-6:
                continue
            if digit_err:
                m.value = f.expected
                f.auto_corrected = True
                f.detail += f" · auto-correct: {f.observed}→{f.expected}(콤마/자릿수)"
            else:
                m.confidence = min(getattr(m, "confidence", 1.0), 0.3)
                f.detail += " · confidence 강등→HITL"
