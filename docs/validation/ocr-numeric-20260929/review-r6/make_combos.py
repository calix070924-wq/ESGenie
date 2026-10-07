"""PR #68 6차 검토(R8-3 후속) 구현 후 대표 조합 — tests/fixtures/ocr_numeric_review_r6/combo_cases.json.

구현 뒤 만든 입력이다(구현에 쓰지 않은 자연스러운 표기). 규칙·실행기는 make_cases.py와 같다.
"""
import importlib.util
import json
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("r6_cases", Path(__file__).with_name("make_cases.py"))
mc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mc)
mc.cases.clear()
M4, gas, add, NARROW = mc.M4, mc.gas, mc.add, mc.NARROW

ACTUAL = {"예정일 납부 금액": "due_date_amount", "납부예정일 금액": "pay_due_day_amount", "당월 청구 예정 사용요금": "bill_scheduled_usage_fee",
          "자동이체 예정 금액": "autopay_scheduled", "청구예정액(VAT 포함)": "bill_scheduled_vat", "정산예정액": "settle_scheduled_amount"}
for label, key in ACTUAL.items():
    add(f"combo_actual_{key}", "납부 시점 조합", f"'{label}' — 금액 일정 표현, 사용량·열량은 실적",
        [M4, [label, "8,420", "360,772", "247,500"]], gas(360772, 8420), [(8420, "actual"), (360772, "actual")],
        evidence="360772", paths=NARROW if len(label) > 11 else mc.ALL)
# 예상(추정) 금액은 물리량도 추정일 수 있다 — 보수적으로 계획(실적 답변 없음)
PLAN = {"예상사용량및요금": ("no_space", "plan"), "납부 예정 요금(예상 사용량 기준)": ("fee_on_expected_usage", "plan"),
        "예상 사용량 및 청구 예정 금액": ("expected_and_bill_scheduled", "plan"), "사용 예정량 및 요금": ("usage_scheduled_qty", "plan"),
        "2026년 목표 사용량 및 요금": ("year_target", "target"), "계획 사용량(요금 포함)": ("plan_fee_included", "plan"),
        "예상 청구액": ("ambiguous_estimated_bill", "plan"), "납부 예상 금액": ("ambiguous_estimated_payment", "plan")}
for label, (key, basis) in PLAN.items():
    add(f"combo_qty_{key}", "물리량 예상·계획 조합", f"'{label}' — 사용량·열량 자체가 {basis}",
        [M4, [label, "8,420", "360,772", "247,500"]], gas(360772, 8420, answer=False),
        [(8420, basis), (360772, basis)], paths=NARROW if len(label) > 11 else mc.ALL)
add("combo_columns_expected_last", "구조 조합", "항목 열이 마지막 + '예상 사용량 및 요금' → plan",
    [["사용량(m3)", "사용열량(MJ)", "요금(원)", "항목"], ["8,420", "360,772", "247,500", "예상 사용량 및 요금"]],
    gas(360772, 8420, answer=False), [(8420, "plan"), (360772, "plan")])
add("combo_site_expected", "구조 조합", "사업장 열 + '계획 사용량 및 요금'(가스) → 범위 유지, plan",
    [["사업장", "항목", "사용량(m3)", "요금(원)"], ["김해 제1공장", "계획 사용량 및 요금", "8,420", "247,500"]],
    {"metrics": [[8420, "m³"]], "scope": {"site": ["김해 제1공장"]}}, [(8420, "plan")], paths=NARROW)
add("combo_site_pay_scheduled", "구조 조합", "사업장 열 + '납부예정금액'(가스) → 범위 유지, actual",
    [["사업장", "항목", "사용량(m3)", "요금(원)"], ["김해 제1공장", "납부예정금액", "8,420", "247,500"]],
    {"metrics": [[8420, "m³"]], "scope": {"site": ["김해 제1공장"]}}, [(8420, "actual")], paths=NARROW)

if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.write_text(json.dumps({
        "note": "PR #68 6차 검토(R8-3 후속) 구현 후 대표 조합 — 검증용 가상 입력, 원본 아님. 생성: review-r6/make_combos.py",
        "emission_rule": "E-4-1 TJ = MJ/1e6, E-3-1 tCO2eq = round(MJ*0.0000561, 3)",
        "cases": mc.cases}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(len(mc.cases), "cases", sum(len(c["paths"]) for c in mc.cases.values()), "observations")
