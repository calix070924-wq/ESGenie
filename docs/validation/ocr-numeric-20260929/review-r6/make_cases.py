"""PR #68 6차 검토(R8-3 후속) 짝 검사 입력 생성 — tests/fixtures/ocr_numeric_review_r6/pair_cases.json.

같은 숫자·같은 표 구조에서 요금 명세 행의 항목명만 바꾼다.
- 납부·청구 시점만 나타내는 이름('납부예정금액'): 사용량·열량은 실적(actual), 답변 생성.
- 물리량 자체의 예상·계획·목표를 나타내는 이름('예상 사용량 및 요금'): 근거 성격 plan/target 유지,
  실제 사용량·배출량 답변(E-4-1·E-3-1) 미생성.
기대값은 원문 의미로 직접 적는다(제품 단어 목록을 읽지 않는다). 규칙은 5차(review-r5/make_cases.py)와 같다.
- 열량(MJ) → E-4-1 TJ = MJ / 1,000,000, E-3-1 tCO2eq = round(MJ × 0.0000561, 3).
- 금액(원) 숫자는 어떤 물리량·답변·근거에도 나오면 안 된다(forbidden_values).
- basis: 추출한 물리량 값별 근거 그래프 노드의 근거 성격.
"""
import json
import sys
from pathlib import Path

PERIOD = "사용 기간: 2026-04-01 ~ 2026-04-30"
M4 = ["항목", "사용량(m3)", "사용열량(MJ)", "요금(원)"]
ALL = ["table_cells", "markdown", "text_lines", "local_pdf"]
# 재현 PDF는 칸 간격 130pt·쪽 폭 595pt로 그린다. 12자 이상 항목명은 옆 칸과 겹치고 6칸 표는 쪽을 넘는다 —
# 제품 경로가 아니라 입력 생성 한계라 이 사례들은 로컬 PDF를 빼고 표·마크다운·텍스트 줄로 본다.
NARROW = ["table_cells", "markdown", "text_lines"]


def gas(heat_mj=None, volume=None, answer=True):
    out = {"metrics": [], "E-4-1": None, "E-3-1": None}
    if volume is not None:
        out["metrics"].append([volume, "m³"])
    if heat_mj is not None:
        out["metrics"].append([heat_mj, "MJ"])
        if answer:
            out["E-4-1"] = [heat_mj / 1_000_000, "TJ"]
            out["E-3-1"] = [round(heat_mj * 0.0000561, 3), "tCO2eq"]
    out["metrics"].sort()
    return out


cases = {}


def add(name, group, meaning, rows, expected, basis, forbidden=(247500,), doc_type="gas_bill", extra=(PERIOD,),
        evidence=None, paths=ALL):
    if doc_type != "gas_bill":             # 전기 kWh는 E-4-1을 만든다 — 이 사례들은 추출값·성격·범위만 비교
        expected = {k: v for k, v in expected.items() if k not in ("E-4-1", "E-3-1")}
    c = {"group": group, "meaning": meaning, "doc_type": doc_type, "extra": list(extra), "tables": [rows],
         "expected": expected, "basis": [list(b) for b in basis], "forbidden_values": list(forbidden),
         "paths": list(paths)}
    if evidence:
        c["evidence_contains"] = evidence
    cases[name] = c


# 1) 납부·청구 시점만 나타내는 항목명 — 물리량은 실적. R8-3 수정 의도 유지
TIMING = {"납부예정금액": "pay_scheduled", "청구예정금액": "bill_scheduled", "납부 예정 금액": "pay_scheduled_spaced",
          "청구 예정 금액": "bill_scheduled_spaced", "당월 납부 예정액": "month_pay_scheduled", "예정 납부액": "scheduled_payment",
          "다음 달 청구 예정 요금": "next_bill_scheduled", "정산 예정 금액": "settle_scheduled", "납부금액(예정)": "pay_paren",
          "고지 예정 요금": "notice_scheduled", "사용량 및 납부예정금액": "usage_and_pay_scheduled",
          "사용요금": "usage_fee", "당월 사용량 및 요금": "month_usage_and_fee"}
for label, key in TIMING.items():
    add(f"timing_{key}", "납부·청구 시점 항목명", f"혼합 표 항목 '{label}' — 금액 일정일 뿐, 사용량·열량은 실적",
        [M4, [label, "8,420", "360,772", "247,500"]], gas(360772, 8420), [(8420, "actual"), (360772, "actual")],
        evidence="360772", paths=NARROW if len(label) > 11 else ALL)

# 2) 물리량 자체의 예상·계획·목표 — 근거 성격 유지, 실제 사용량·배출량 답변 없음
QTY = {"예상 사용량 및 요금": ("expected_usage", "plan"), "계획 사용량 및 요금": ("planned_usage", "plan"),
       "예상 사용량·요금": ("expected_usage_dot", "plan"), "사용량(예상) 및 요금": ("usage_paren_expected", "plan"),
       "예정 사용량 및 요금": ("scheduled_usage", "plan"), "예상 사용열량 및 요금": ("expected_heat", "plan"),
       "사용 계획 및 요금": ("usage_plan", "plan"), "전망 사용량 및 요금": ("outlook_usage", "plan"),
       "예상 사용량 및 납부예정금액": ("expected_usage_and_pay_scheduled", "plan"),
       "목표 사용량 및 요금": ("target_usage", "target")}
for label, (key, basis) in QTY.items():
    add(f"qty_{key}", "물리량 예상·계획 항목명", f"혼합 표 항목 '{label}' — 사용량·열량 자체가 {basis}, 실적 답변 없음",
        [M4, [label, "8,420", "360,772", "247,500"]], gas(360772, 8420, answer=False),
        [(8420, basis), (360772, basis)])

# 3) 무엇을 수식하는지 원문만으로 가를 수 없는 이름 — 실적으로 단정하지 않는다
for label, key in (("예상 요금", "expected_fee"), ("요금(예상)", "fee_paren_expected")):
    add(f"ambiguous_{key}", "모호한 예상 표현", f"혼합 표 항목 '{label}' — 사용량이 실적인지 원문으로 확정 불가, 실적 답변 안 만듦",
        [M4, [label, "8,420", "360,772", "247,500"]], gas(360772, 8420, answer=False),
        [(8420, "plan"), (360772, "plan")])

# 4) 같은 구조 변형에서 두 종류를 짝으로 — 빈 열량, 열 재배치, 범위 열, 전기, 실적·예상 두 행
for label, key, basis in (("납부예정금액", "timing", "actual"), ("예상 사용량 및 요금", "qty", "plan")):
    add(f"pair_{key}_empty_heat", "구조 짝", f"'{label}' + 열량 '-' → 8,420 m³({basis})만",
        [M4, [label, "8,420", "-", "247,500"]], gas(None, 8420), [(8420, basis)])
    add(f"pair_{key}_columns", "구조 짝", f"열 재배치(요금·항목·열량·사용량) + '{label}'",
        [["요금(원)", "항목", "사용열량(MJ)", "사용량(m3)"], ["247,500", label, "360,772", "8,420"]],
        gas(360772, 8420, answer=basis == "actual"), [(8420, basis), (360772, basis)],
        evidence="360772" if basis == "actual" else None)
    add(f"pair_{key}_kepco", "구조 짝", f"전기 혼합 표 '{label}' 행 1,000 kWh({basis})",
        [["항목", "사용량(kWh)", "요금(원)"], [label, "1,000", "150,000"]], {"metrics": [[1000, "kWh"]]},
        [(1000, basis)], forbidden=(150000,), doc_type="kepco_bill", extra=())
    add(f"pair_{key}_scoped", "구조 짝", f"사업장·기간·계량기 + '{label}' → 범위 유지, 성격 {basis}",
        [["사업장", "기간", "계량기", "항목", "사용량(kWh)", "요금(원)"],
         ["김해 제1공장", "2026년 4월", "전력계 A", label, "1,000", "150,000"]],
        {"metrics": [[1000, "kWh"]], "scope": {"site": ["김해 제1공장"], "period": ["2026년 4월"], "meter": ["전력계 A"]}},
        [(1000, basis)], forbidden=(150000,), doc_type="kepco_bill", extra=(), paths=NARROW)
add("pair_two_rows_actual_and_expected", "구조 짝", "당월 실적 행 + 예상 사용량 행(요금 명세) → 실적 행만 답변 근거",
    [M4, ["당월 사용량 및 요금", "8,420", "360,772", "247,500"], ["예상 사용량 및 요금", "8,800", "377,000", "258,000"]],
    {"metrics": sorted([[8420, "m³"], [360772, "MJ"], [8800, "m³"], [377000, "MJ"]]), "E-4-1": [0.360772, "TJ"],
     "E-3-1": [20.239, "tCO2eq"]},
    [(8420, "actual"), (360772, "actual"), (8800, "plan"), (377000, "plan")], forbidden=(247500, 258000),
    evidence="360772")
add("pair_two_rows_pay_scheduled_twice", "구조 짝", "두 행 모두 납부 시점 이름(당월·다음 달 납부예정금액) → 둘 다 실적",
    [M4, ["당월 납부예정금액", "8,420", "360,772", "247,500"], ["전월 납부예정금액", "8,100", "347,000", "238,000"]],
    None, [(8420, "actual"), (360772, "actual"), (8100, "actual"), (347000, "actual")], forbidden=(247500, 238000))
cases["pair_two_rows_pay_scheduled_twice"]["expected"] = {
    "metrics": sorted([[8420, "m³"], [360772, "MJ"], [8100, "m³"], [347000, "MJ"]])}

if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.write_text(json.dumps({
        "note": "PR #68 6차 검토(R8-3 후속) 짝 검사 입력 — 검증용 가상 입력, 원본 아님. 생성: review-r6/make_cases.py",
        "emission_rule": "E-4-1 TJ = MJ/1e6, E-3-1 tCO2eq = round(MJ*0.0000561, 3)",
        "cases": cases}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(len(cases), "cases", sum(len(c["paths"]) for c in cases.values()), "observations")
