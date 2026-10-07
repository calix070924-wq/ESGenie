"""PR #68 4차 검토(R8 재보완) 짝 검사 입력 생성 — tests/fixtures/ocr_numeric_review_r4/pair_cases.json.

기대값은 원문 의미로 직접 적는다(제품 단어 목록을 읽지 않는다).
- 열량(MJ) → E-4-1 TJ = MJ / 1,000,000, E-3-1 tCO2eq = MJ × 0.0000561 을 소수 3자리 반올림(기존 예시 계수·반올림).
- 금액(원) 숫자는 어떤 물리량·답변·근거에도 나오면 안 된다(forbidden_values).
paths: table_cells(표 객체 + 같은 칸 텍스트), markdown, text_lines(칸 좌표 텍스트 줄), local_pdf(디지털 PDF).
빈 문자열 칸은 텍스트 줄·PDF에서 칸 위치가 사라지므로 table_cells·markdown만 쓴다.
"""
import json
import sys
from pathlib import Path

PERIOD = "사용 기간: 2026-04-01 ~ 2026-04-30"
H2 = ["사용량(m3)", "사용열량(MJ)"]
ALL = ["table_cells", "markdown", "text_lines", "local_pdf"]
NO_LINES = ["table_cells", "markdown"]


def gas(heat_mj=None, volume=None):
    out = {"metrics": [], "E-4-1": None, "E-3-1": None}
    if volume is not None:
        out["metrics"].append([volume, "m³"])
    if heat_mj is not None:
        out["metrics"].append([heat_mj, "MJ"])
        out["E-4-1"] = [heat_mj / 1_000_000, "TJ"]
        out["E-3-1"] = [round(heat_mj * 0.0000561, 3), "tCO2eq"]
    out["metrics"].sort()
    return out


cases = {}


def add(name, group, meaning, rows, expected, forbidden=(), paths=ALL, doc_type="gas_bill", extra=(PERIOD,),
        evidence=None, tables=None, review=None):
    if doc_type != "gas_bill":             # 전기 kWh는 E-4-1을 만든다 — 이 사례들은 추출값·범위만 비교
        expected = {k: v for k, v in expected.items() if k not in ("E-4-1", "E-3-1")}
    c = {"group": group, "meaning": meaning, "doc_type": doc_type, "extra": list(extra),
         "tables": tables or [rows], "expected": expected, "forbidden_values": list(forbidden), "paths": paths}
    if evidence:
        c["evidence_contains"] = evidence
    if review:
        c["review_reasons_any"] = review
    cases[name] = c


def paths_for(*rows_lists):
    return NO_LINES if any(cell == "" for rows in rows_lists for row in rows for cell in row) else ALL


# 1) 같은 항목명, 다른 구조 — R8-1 혼합 표(보존) vs 2칸 표 밑 별도 금액 행(제외)
M4 = ["항목", "사용량(m3)", "사용열량(MJ)", "요금(원)"]
add("r8_1_mixed_valid", "구조", "4열 혼합 표: 사용요금 행에 사용량·열량·요금이 각 열에 있음 → 사용량·열량 보존, 요금 열은 금액",
    [M4, ["사용요금", "8,420", "360,772", "247,500"]], gas(360772, 8420), [247500], evidence="360772")
add("r8_1_mixed_no_label_col", "구조", "대조군: 항목 열 없는 3열 표",
    [H2 + ["요금(원)"], ["8,420", "360,772", "247,500"]], gas(360772, 8420), [247500])
add("r8_1_fee_row_under_2col_same_label", "구조", "같은 '사용요금'이 2칸 표 밑 별도 행이면 금액 → 에너지 사용 금지",
    [H2, ["8,420", "360,772"], ["사용요금", "247,500"]], gas(360772, 8420), [247500])

# 2) 알려진 이름·다른 이름 × 정상·빈 열량
for label in ("기본요금", "기본료", "사용료", "계량기 교체요금"):
    key = {"기본요금": "basic_charge", "기본료": "basic_fee", "사용료": "usage_fee", "계량기 교체요금": "meter_replacement_fee"}[label]
    add(f"r8_2_{key}_valid", "금액 이름", f"2칸 표 밑 '{label} | 247,500' + 정상 열량 → 열량 보존, 금액 제외",
        [H2, ["8,420", "360,772"], [label, "247,500"]], gas(360772, 8420), [247500], evidence="360772")
    add(f"r8_2_{key}_empty", "금액 이름", f"2칸 표 밑 '{label} | 247,500' + 빈 열량 → 8,420 m³만",
        [H2, ["8,420", "-"], [label, "247,500"]], gas(None, 8420), [247500])
    add(f"r8_2_{key}_label_table", "금액 이름", f"구분 열이 있는 표에서 '{label}' 행(금액 열 없음) → 금액 행",
        [["구분", "사용열량(MJ)"], ["제1공장", "360,772"], [label, "247,500"]],
        {"metrics": [[360772, "MJ"]], "E-4-1": [0.360772, "TJ"], "E-3-1": [20.239, "tCO2eq"]}, [247500])

# 3) 실제 범위·비용 문구
add("scope_meter_ab_with_meter_fee", "범위", "계량기 열의 전력계 A/B는 범위, '계량기 교체요금' 행은 금액",
    [["계량기", "사용량(kWh)"], ["전력계 A", "1,000"], ["전력계 B", "1,200"], ["계량기 교체요금", "33,000"]],
    {"metrics": [[1000, "kWh"], [1200, "kWh"]], "E-4-1": None, "E-3-1": None,
     "scope": {"meter": ["전력계 A", "전력계 B"]}}, [33000], doc_type="kepco_bill", extra=())
add("scope_periods_with_fee", "범위", "기간 열 4월·5월은 범위, '기본료' 행은 금액",
    [["기간", "사용량(kWh)"], ["2026년 4월", "1,000"], ["2026년 5월", "1,200"], ["기본료", "7,300"]],
    {"metrics": [[1000, "kWh"], [1200, "kWh"]], "E-4-1": None, "E-3-1": None,
     "scope": {"period": ["2026년 4월", "2026년 5월"]}}, [7300], doc_type="kepco_bill", extra=())
add("scope_period_value_mentions_charge", "범위", "기간 값 '2026년 5월 요금 청구기간'은 금액이 아니라 기간",
    [["기간", "사용량(kWh)"], ["2026년 4월", "1,000"], ["2026년 5월 요금 청구기간", "1,200"]],
    {"metrics": [[1000, "kWh"], [1200, "kWh"]], "E-4-1": None, "E-3-1": None}, [], doc_type="kepco_bill", extra=())
add("scope_site_meter_columns_with_money_col", "범위", "사업장·계량기·사용량·요금 열 — 범위 보존, 요금 열 제외",
    [["사업장", "계량기", "사용량(kWh)", "요금(원)"], ["김해 제1공장", "전력계 A", "1,000", "150,000"],
     ["김해 제1공장", "전력계 B", "1,200", "180,000"]],
    {"metrics": [[1000, "kWh"], [1200, "kWh"]], "E-4-1": None, "E-3-1": None,
     "scope": {"meter": ["전력계 A", "전력계 B"]}}, [150000, 180000], doc_type="kepco_bill", extra=(PERIOD,))

# 4) 열 위치(원문 좌표도 함께 이동)
for i, (head, row) in enumerate([
        (["항목", "요금(원)", "사용량(m3)", "사용열량(MJ)"], ["사용요금", "247,500", "8,420", "360,772"]),
        (["요금(원)", "항목", "사용열량(MJ)", "사용량(m3)"], ["247,500", "사용요금", "360,772", "8,420"]),
        (["사용량(m3)", "사용열량(MJ)", "요금(원)", "항목"], ["8,420", "360,772", "247,500", "사용요금"])]):
    add(f"columns_mixed_{i}", "열 위치", f"혼합 표 열 재배치 {i}", [head, row], gas(360772, 8420), [247500], evidence="360772")
add("columns_swapped_2col_fee", "열 위치", "2칸 표 열 순서 바뀜 + 기본료 행",
    [["사용열량(MJ)", "사용량(m3)"], ["360,772", "8,420"], ["기본료", "247,500"]], gas(360772, 8420), [247500])

# 5) 값 상태
for tag, v in (("blank", ""), ("dash", "-"), ("emdash", "—"), ("pending", "검침 예정")):
    add(f"state_mixed_{tag}", "값 상태", f"혼합 표 열량 칸 '{v}' → 8,420 m³만",
        [M4, ["사용요금", "8,420", v, "247,500"]], gas(None, 8420), [247500], paths=paths_for([[v]]))
    add(f"state_2col_{tag}_basic_fee", "값 상태", f"2칸 표 열량 '{v}' + 기본료 행 → 8,420 m³만",
        [H2, ["8,420", v], ["기본료", "247,500"]], gas(None, 8420), [247500], paths=paths_for([[v]]))
add("state_mixed_zero", "값 상태", "혼합 표 명시 0 MJ는 유효",
    [M4, ["사용요금", "8,420", "0", "247,500"]], {"metrics": [[0, "MJ"], [8420, "m³"]]}, [247500])
add("state_2col_zero_basic_fee", "값 상태", "2칸 표 명시 0 MJ + 기본료 행",
    [H2, ["8,420", "0"], ["기본료", "247,500"]], {"metrics": [[0, "MJ"], [8420, "m³"]]}, [247500])

# 6) 금액 표기
for tag, fee in (("won", "247,500원"), ("krw", "₩247,500")):
    add(f"notation_2col_{tag}", "금액 표기", f"2칸 표 밑 기본료 '{fee}' + 빈 열량",
        [H2, ["8,420", "-"], ["기본료", fee]], gas(None, 8420), [247500])
    add(f"notation_mixed_{tag}", "금액 표기", f"혼합 표 요금 칸 '{fee}'",
        [M4, ["사용요금", "8,420", "360,772", fee]], gas(360772, 8420), [247500])

# 7) 금액 삽입·제거 — 모두 같은 답
add("insert_base", "삽입·제거", "금액 없는 기본 2칸 표", [H2, ["8,420", "360,772"]], gas(360772, 8420))
add("insert_fee_rows", "삽입·제거", "금액 행 3개 추가", [H2, ["8,420", "360,772"], ["기본료", "7,300"],
                                                ["사용요금", "240,200"], ["부가가치세", "24,750"]],
    gas(360772, 8420), [7300, 240200, 24750])
add("insert_fee_col", "삽입·제거", "금액 열 추가", [H2 + ["청구금액"], ["8,420", "360,772", "272,250"]],
    gas(360772, 8420), [272250])

# 8) 숫자 교체·일치
add("numbers_other", "숫자", "다른 사용량·열량·금액", [H2, ["5,130", "219,807"], ["기본료", "98,760"]],
    gas(219807, 5130), [98760], evidence="219807")
add("numbers_other_mixed", "숫자", "혼합 표 다른 숫자", [M4, ["사용요금", "5,130", "219,807", "98,760"]],
    gas(219807, 5130), [98760], evidence="219807")
add("numbers_same_in_heat_and_fee", "숫자", "열량 칸과 요금 칸에 같은 247,500 → 열량 칸만 근거",
    [H2, ["8,420", "247,500"], ["기본료", "247,500"]], gas(247500, 8420), [], evidence="사용열량")

# 9) 다른 정상 근거
add("other_source_table", "다른 근거", "빈 열량 + 기본료 행, 다른 표(2쪽)에 360,772 MJ",
    None, gas(360772, 8420), [247500], paths=["table_cells"],
    tables=[[H2, ["8,420", "-"], ["기본료", "247,500"]], [["사용열량(MJ)"], ["360,772"]]])
add("other_source_body", "다른 근거", "빈 열량 + 기본료 행, 본문 '도시가스 사용열량 360,772 MJ'",
    [H2, ["8,420", "-"], ["기본료", "247,500"]], gas(360772, 8420), [247500],
    extra=(PERIOD, "도시가스 사용열량 360,772 MJ"), paths=["table_cells", "markdown"])

# 10) 기존 표 구조
add("existing_label_total_money_col", "기존 구조", "구분·사용량·요금 열, 1·2공장·합계 → 합계만",
    [["구분", "사용량(kWh)", "요금(원)"], ["1공장", "1,000", "120,000"], ["2공장", "2,000", "240,000"],
     ["합계", "3,000", "360,000"]], {"metrics": [[3000, "kWh"]]}, [120000, 240000, 360000],
    doc_type="kepco_bill", extra=(PERIOD,))

# 11) 판정 근거 부족 → 해당 후보만 보류
add("hold_unknown_label_in_quantity_column", "보류", "2칸 표 밑 '비고 | 247,500' — 열 관계 미확정 → 보류, 정상값 유지",
    [H2, ["8,420", "360,772"], ["비고", "247,500"]], gas(360772, 8420), [247500])
add("hold_money_label_without_money_value", "보류", "혼합 표에서 금액 이름 행의 금액 칸이 비고 사용량 칸에만 숫자 → 보류",
    [M4, ["사용요금", "8,420", "360,772", "247,500"], ["기본료", "7,300", "", ""]], gas(360772, 8420), [7300],
    paths=NO_LINES)

if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.write_text(json.dumps({
        "note": "PR #68 4차 검토(R8 재보완) 짝 검사 입력 — 검증용 가상 입력, 원본 아님. 생성: review-r4/make_cases.py",
        "emission_rule": "E-4-1 TJ = MJ/1e6, E-3-1 tCO2eq = round(MJ*0.0000561, 3)",
        "cases": cases}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(len(cases), "cases")
