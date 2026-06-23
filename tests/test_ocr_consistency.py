"""OCR 일관성 검증(Tier A 산술 정합) 회귀 테스트.

규칙 식은 한울정밀 01/02/03 실측으로 성립. 여기서는 정상통과·콤마오인식
auto-correct·입력누락 skip·절사경계 false-positive·탐지한계를 고정한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from esgenie.ssot.ocr_consistency import validate_consistency


@dataclass
class FakeMetric:
    metric_hint: str
    value: float
    unit: str = ""
    confidence: float = 0.9
    kesg_code_guess: str | None = None


@dataclass
class FakeExt:
    raw_text: str
    doc_type: str
    metrics: list = field(default_factory=list)
    router_meta: dict = field(default_factory=dict)


# 실측 기반 정상 본문 ---------------------------------------------------------
KEPCO_OK = (
    "기본요금 (800kW × 8,320원) 6,656,000 전력량요금 (142,560kWh) 15,752,880 "
    "기후환경요금 1,283,040 연료비조정요금 712,800 전기요금계 24,404,720 "
    "부가가치세 (10%) 2,440,472 전력산업기반기금 (3.7%) 902,970 "
    "청구금액 (납기일 2026-06-25) 27,748,160 원"
)
WASTE_OK = (
    "합 계 총 위탁량 18,400 폐기물 총배출량 18,400 kg 재활용량 5,400 kg "
    "재활용 비율 (순환이용률) 29.3 % 소각·매립 비율 70.7 % "
    "※ 당월 총 위탁량 18,400kg 중 재활용 5,400kg / 소각 6,100kg / 매립 6,900kg."
)


def _by_id(findings):
    return {f.rule_id: f for f in findings}


def test_kepco_all_ok():
    ext = FakeExt(KEPCO_OK, "kepco_bill")
    fs = _by_id(validate_consistency(ext))
    assert all(f.severity == "ok" for f in fs.values()), fs
    assert ext.router_meta.get("hitl_required") is not True


def test_waste_all_ok():
    fs = _by_id(validate_consistency(FakeExt(WASTE_OK, "waste_ledger")))
    for rid in ("waste.method_split", "waste.recycle_rate", "waste.disposal_complement"):
        assert fs[rid].severity == "ok", fs[rid]


def test_truncation_boundary_no_false_positive():
    """기금/청구금액의 10원 절사 — 절사 전 정확값이 들어와도 ok여야 한다."""
    fs = _by_id(validate_consistency(FakeExt(KEPCO_OK, "kepco_bill")))
    assert fs["kepco.fund"].severity == "ok"     # 902,974.6 → 절사 902,970
    assert fs["kepco.total"].severity == "ok"    # 27,748,162 → 절사 27,748,160


def test_total_misread_flags_fail():
    """청구금액을 변조하면 합계 규칙이 fail."""
    bad = KEPCO_OK.replace("27,748,160", "27,748,999")
    fs = _by_id(validate_consistency(FakeExt(bad, "kepco_bill")))
    assert fs["kepco.total"].severity == "fail"
    assert fs["kepco.total"].expected == 27748160.0


def test_comma_misread_autocorrect_on_metric():
    """청구금액이 ×0.1 콤마오인식(2,774,816) + 동일 metric 존재 → auto-correct."""
    bad = KEPCO_OK.replace("27,748,160", "2,774,816")
    m = FakeMetric("청구금액", 2774816.0, unit="원")
    ext = FakeExt(bad, "kepco_bill", metrics=[m])
    fs = _by_id(validate_consistency(ext))
    f = fs["kepco.total"]
    assert f.severity == "fail"
    assert f.suggested_fix == 27748160.0
    assert f.auto_corrected is True
    assert m.value == 27748160.0          # metric이 결정적으로 보정됨


def test_non_digit_error_demotes_confidence_not_correct():
    """콤마/자릿수로 설명 안 되는 불일치 → 보정 안 하고 confidence 강등."""
    bad = KEPCO_OK.replace("27,748,160", "27,000,000")
    m = FakeMetric("청구금액", 27000000.0, unit="원", confidence=0.9)
    ext = FakeExt(bad, "kepco_bill", metrics=[m])
    fs = _by_id(validate_consistency(ext))
    assert fs["kepco.total"].severity == "fail"
    assert fs["kepco.total"].auto_corrected is False
    assert m.value == 27000000.0          # 보정하지 않음
    assert m.confidence <= 0.3            # HITL로 강등
    assert ext.router_meta.get("hitl_required") is True


def test_missing_inputs_skipped_not_failed():
    """입력값이 없으면 거짓경보 대신 skipped."""
    fs = _by_id(validate_consistency(FakeExt("내용 없음", "kepco_bill")))
    assert all(f.severity == "skipped" for f in fs.values())


def test_partial_tamper_detected():
    """총량만 18,400→18,500으로 바뀌면 항목합(=18,400)과 불일치 → fail."""
    sneaky = WASTE_OK.replace("폐기물 총배출량 18,400", "폐기물 총배출량 18,500")
    fs = _by_id(validate_consistency(FakeExt(sneaky, "waste_ledger")))
    assert fs["waste.method_split"].severity == "fail"


def test_consistent_tamper_is_blind_spot():
    """한계 명시: 항목·총량이 함께 일관되게 틀리면(전부 변조) 탐지 불가."""
    blind = (
        "폐기물 총배출량 9,200 kg 재활용량 2,700 kg 재활용 비율 (순환이용률) 29.3 % "
        "소각·매립 비율 70.7 % "
        "※ 당월 총 위탁량 9,200kg 중 재활용 2,700kg / 소각 3,050kg / 매립 3,450kg."
    )
    fs = _by_id(validate_consistency(FakeExt(blind, "waste_ledger")))
    assert fs["waste.method_split"].severity == "ok"   # 2700+3050+3450=9,200 (자기일관)
    assert fs["waste.recycle_rate"].severity == "ok"   # 2700/9200=29.3% (자기일관)
