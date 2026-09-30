"""구현 후 자체 검토용 대표 조합 — tests/fixtures/ocr_numeric_review_r4/combo_cases.json.

짝 검사(make_cases.py)에서 한 요소씩 바꾼 것과 달리, 항목명·열 위치·값 상태·금액 표기·범위를 동시에 바꾼다.
구현 뒤에 만들었으므로 '수정 전 기록'이 아니라 자체 검토 입력이다. 기대값 규칙은 make_cases.py와 같다.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from make_cases import PERIOD, gas, NO_LINES, ALL  # noqa: E402

cases = {}


def add(name, meaning, tables, expected, forbidden=(), paths=ALL, doc_type="gas_bill", extra=(PERIOD,), evidence=None):
    if doc_type != "gas_bill":
        expected = {k: v for k, v in expected.items() if k not in ("E-4-1", "E-3-1")}
    cases[name] = {"group": "추가 대표 조합(구현 후)", "meaning": meaning, "doc_type": doc_type, "extra": list(extra),
                   "tables": tables, "expected": expected, "forbidden_values": list(forbidden), "paths": paths,
                   **({"evidence_contains": evidence} if evidence else {})}


add("combo_label_moved_money_col_empty_heat_won", "항목명 사용료 + 금액 열 둘째 + 열량 '-' + '원' 표기 + 열 순서 바뀜",
    [[["구분", "요금(원)", "사용열량(MJ)", "사용량(m3)"], ["사용료", "247,500원", "-", "8,420"]]], gas(None, 8420), [247500])
add("combo_mixed_rows_and_meter_fee_row", "혼합 표 정상 행 + 금액 칸만 있는 '계량기 교체요금' 행",
    [[["항목", "사용량(m3)", "사용열량(MJ)", "요금(원)"], ["사용요금", "8,420", "360,772", "240,200"],
      ["계량기 교체요금", "", "", "7,300"]]], gas(360772, 8420), [240200, 7300], paths=NO_LINES, evidence="360772")
add("combo_money_first_col_label_row_below", "첫 열이 요금(원)인 표 밑 '기본료 | 7,300 |' 행 — 금액 열에 이름",
    [[["요금(원)", "사용량(m3)", "사용열량(MJ)"], ["247,500", "8,420", "360,772"], ["기본료", "7,300", ""]]],
    gas(360772, 8420), [247500, 7300], paths=NO_LINES)
add("combo_same_number_heat_and_money_col", "열량 칸과 요금 열에 같은 247,500 — 열량 칸만 근거",
    [[["사용량(m3)", "사용열량(MJ)", "요금(원)"], ["8,420", "247,500", "247,500"]]], gas(247500, 8420), [],
    evidence="사용열량")
add("combo_meter_ab_money_col_meter_fee", "계량기 A/B + 요금 열 + 금액만 있는 계량기 교체요금 행",
    [[["계량기", "사용량(kWh)", "요금(원)"], ["전력계 A", "1,000", "150,000"], ["전력계 B", "1,200", "180,000"],
      ["계량기 교체요금", "", "33,000"]]],
    {"metrics": [[1000, "kWh"], [1200, "kWh"]], "scope": {"meter": ["전력계 A", "전력계 B"]}},
    [150000, 180000, 33000], doc_type="kepco_bill", extra=(), paths=NO_LINES)
add("combo_period_item_money_cols", "기간·항목(사용요금)·사용량·요금 — 두 기간 모두 보존",
    [[["기간", "항목", "사용량(kWh)", "요금(원)"], ["2026년 4월", "사용요금", "1,000", "150,000"],
      ["2026년 5월", "사용요금", "1,200", "180,000"]]],
    {"metrics": [[1000, "kWh"], [1200, "kWh"]], "scope": {"period": ["2026년 4월", "2026년 5월"]}},
    [150000, 180000], doc_type="kepco_bill", extra=())
add("combo_fee_krw_and_body_same_number", "빈 열량 + '기본료 | ₩247,500' + 본문 '사용열량 247,500 MJ' — 본문 근거 보존",
    [[["사용량(m3)", "사용열량(MJ)"], ["8,420", "-"], ["기본료", "₩247,500"]]], gas(247500, 8420), [],
    extra=(PERIOD, "도시가스 사용열량 247,500 MJ"), paths=["table_cells", "markdown"])
add("combo_unknown_label_swapped_cols_zero", "열 순서 바뀜 + 명시 0 MJ + 목록에 없는 '기타 청구' 행",
    [[["사용열량(MJ)", "사용량(m3)"], ["0", "8,420"], ["기타 청구", "98,760"]]],
    {"metrics": [[0, "MJ"], [8420, "m³"]]}, [98760])
add("combo_fuel_item_not_money", "항목 '연료'는 금액이 아니다 — 구분 열 표",
    [[["구분", "사용열량(MJ)"], ["연료", "360,772"], ["기본료", "7,300"]]],
    {"metrics": [[360772, "MJ"]], "E-4-1": [0.360772, "TJ"], "E-3-1": [20.239, "tCO2eq"]}, [7300])
add("combo_note_col_mentions_fee", "비고 열 '기본요금 별도'는 행 이름이 아니다",
    [[["구분", "사용량(m3)", "사용열량(MJ)", "비고"], ["제1공장", "8,420", "360,772", "기본요금 별도"]]],
    gas(360772, 8420), [])
add("combo_other_page_fee_same_number", "2쪽 금액 표 '기본료 | 360,772'(같은 숫자) — 1쪽 열량은 보존",
    None and [], gas(360772, 8420), [], paths=["table_cells"])
cases["combo_other_page_fee_same_number"]["tables"] = [
    [["사용량(m3)", "사용열량(MJ)"], ["8,420", "360,772"]], [["항목", "금액(원)"], ["기본료", "360,772"]]]

Path(sys.argv[1]).write_text(json.dumps({
    "note": "PR #68 4차 검토 — 구현 후 자체 검토 대표 조합. 검증용 가상 입력, 원본 아님. 생성: review-r4/make_combos.py",
    "cases": cases}, ensure_ascii=False, indent=1), encoding="utf-8")
print(len(cases), "combos")
