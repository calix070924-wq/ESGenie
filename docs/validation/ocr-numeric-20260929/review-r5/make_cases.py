"""PR #68 5차 검토(R8-3) 판정 충돌 검사 입력 생성 — tests/fixtures/ocr_numeric_review_r5/pair_cases.json.

부재 표시(값 없음)와 금액 항목명이 같은 단어('예정'·'추후'·'미정'·'미확인'·'확인중')를 나눠 쓰는 입력이다.
기대값은 원문 의미로 직접 적는다(제품 단어 목록을 읽지 않는다). 규칙은 4차(review-r4/make_cases.py)와 같다.
- 열량(MJ) → E-4-1 TJ = MJ / 1,000,000, E-3-1 tCO2eq = round(MJ × 0.0000561, 3).
- 금액(원) 숫자는 어떤 물리량·답변·근거에도 나오면 안 된다(forbidden_values). 같은 숫자가 실제 열량 칸에
  있는 사례는 금지값으로 두지 않고 근거 칸으로 가른다.
- review_includes / review_excludes: 표 해석 검토 기록에 있어야 할·없어야 할 사유(보류·제외가 기록으로 남는지).
paths: table_cells(표 객체 + 같은 칸 텍스트), markdown, text_lines(칸 좌표 텍스트 줄), local_pdf(디지털 PDF).
빈 문자열 칸은 텍스트 줄·PDF에서 칸 위치가 사라지므로 table_cells·markdown만 쓴다.
"""
import json
import sys
from pathlib import Path

PERIOD = "사용 기간: 2026-04-01 ~ 2026-04-30"
H2 = ["사용량(m3)", "사용열량(MJ)"]
M4 = ["항목", "사용량(m3)", "사용열량(MJ)", "요금(원)"]
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


def add(name, group, meaning, rows, expected, forbidden=(), paths=None, doc_type="gas_bill", extra=(PERIOD,),
        evidence=None, tables=None, includes=(), excludes=()):
    tables = tables or [rows]
    if paths is None:
        paths = NO_LINES if any(cell == "" for t in tables for row in t for cell in row) else ALL
    if doc_type != "gas_bill":             # 전기 kWh는 E-4-1을 만든다 — 이 사례들은 추출값·범위만 비교
        expected = {k: v for k, v in expected.items() if k not in ("E-4-1", "E-3-1")}
    c = {"group": group, "meaning": meaning, "doc_type": doc_type, "extra": list(extra), "tables": tables,
         "expected": expected, "forbidden_values": list(forbidden), "paths": paths}
    if evidence:
        c["evidence_contains"] = evidence
    if includes:
        c["review_includes"] = list(includes)
    if excludes:
        c["review_excludes"] = list(excludes)
    cases[name] = c


# 1) 알려진 회귀와 대조군 — 검토자 최소 입력(2칸 표 + 표 밑 별도 금액 행) × 정상·빈 열량
FEE = {"납부예정금액": "pay_scheduled", "청구예정금액": "bill_scheduled", "추후청구요금": "later_billing",
       "납부금액": "paid_amount"}
for label, key in FEE.items():
    add(f"known_{key}_valid", "알려진 회귀·대조군", f"2칸 표 + '{label} | 247,500', 정상 열량 → 열량 보존, 금액 제외",
        [H2, ["8,420", "360,772"], [label, "247,500"]], gas(360772, 8420), [247500], evidence="360772",
        includes=["money_row_excluded"])
    add(f"known_{key}_empty", "알려진 회귀·대조군", f"2칸 표 + '{label} | 247,500', 열량 '-' → 8,420 m³만",
        [H2, ["8,420", "-"], [label, "247,500"]], gas(None, 8420), [247500],
        includes=["money_row_excluded", "value_absent"])

# 2) 실제 부재 표시 — 칸 전체가 상태 표현. 값 미생성·보류 사유·기존 계산 유지
MARKERS = {"blank": "", "dash": "-", "emdash": "—", "na": "N/A", "pending": "검침 예정", "measure_pending": "측정 예정",
           "unread": "미검침", "undecided": "미정", "paren_pending": "(검침 예정)", "later": "추후 기재",
           "none": "해당 없음"}
for tag, m in MARKERS.items():
    add(f"marker_heat_{tag}", "실제 부재 표시", f"열량 칸 '{m}' → 8,420 m³만, 값 없음 기록",
        [H2, ["8,420", m]], gas(None, 8420), includes=["value_absent"])
    add(f"marker_heat_{tag}_fee_row", "실제 부재 표시", f"열량 칸 '{m}' + 표 밑 '납부예정금액 | 247,500' → 8,420 m³만",
        [H2, ["8,420", m], ["납부예정금액", "247,500"]], gas(None, 8420), [247500],
        includes=["value_absent", "money_row_excluded"])
    add(f"marker_usage_{tag}", "실제 부재 표시", f"첫 칸(사용량) '{m}' + 열량 360,772 → 열량만, 첫 칸은 행 이름 아님",
        [H2, [m, "360,772"]], gas(360772, None), evidence="360772", includes=["value_absent"],
        excludes=["row_label_in_quantity_column", "money_row_excluded"])
for tag, m in (("pending", "검침 예정"), ("unread", "미검침"), ("undecided", "미정")):
    add(f"marker_reading_computed_{tag}", "실제 부재 표시",
        f"사용량 칸 '{m}' + 전월·당월 지침 + 배율 1 → (1,250 − 1,000) × 1 = 250 kWh 계산 유지",
        [["사용량(kWh)", "전월지침", "당월지침", "배율"], [m, "1,000", "1,250", "1"]],
        {"metrics": [[250, "kWh"]]}, doc_type="kepco_bill", extra=())
    add(f"marker_reading_no_multiplier_{tag}", "실제 부재 표시",
        f"사용량 칸 '{m}' + 지침만(배율 없음) → 배율을 가정하지 않고 보류",
        [["사용량(kWh)", "전월지침", "당월지침"], [m, "1,000", "1,250"]],
        {"metrics": []}, doc_type="kepco_bill", extra=(), includes=["multiplier_missing"])

# 3) 겹치는 단어 — 부재 단어가 붙은 자연스러운 금액 표현(‘예정’ 한 단어 예외가 아님)
OVERLAP = {"확인중인 청구금액": "checking_billed", "미확인 청구액": "unconfirmed_billed", "미정산 요금": "unsettled_fee",
           "예정 납부액": "scheduled_payment", "추후 납부금액": "later_payment", "납부금액 미정": "amount_undecided"}
for label, key in OVERLAP.items():
    add(f"overlap_{key}_valid", "겹치는 단어", f"2칸 표 + '{label} | 247,500', 정상 열량",
        [H2, ["8,420", "360,772"], [label, "247,500"]], gas(360772, 8420), [247500], evidence="360772",
        includes=["money_row_excluded"])
    add(f"overlap_{key}_empty", "겹치는 단어", f"2칸 표 + '{label} | 247,500', 열량 '-'",
        [H2, ["8,420", "-"], [label, "247,500"]], gas(None, 8420), [247500], includes=["money_row_excluded"])

# 4) 상태 표현과 항목명 — '검침 예정' ↔ '검침예정일', '미정' ↔ '납부금액 미정'
add("name_meter_reading_date_row", "상태와 항목명", "표 밑 '검침예정일 | 20'(날짜) — 부재 아님, 역할 불명 → 그 행만 보류",
    [H2, ["8,420", "-"], ["검침예정일", "20"]], gas(None, 8420), [20], includes=["row_label_in_quantity_column"])
add("name_meter_reading_date_row_valid", "상태와 항목명", "정상 열량 + 표 밑 '검침예정일 | 2026-05-12' → 정상값 유지, 그 행 보류",
    [H2, ["8,420", "360,772"], ["검침예정일", "2026-05-12"]], gas(360772, 8420), evidence="360772",
    includes=["row_label_in_quantity_column"])
add("name_state_pending_vs_date_pair", "상태와 항목명", "열량 칸 '검침 예정'(부재) + 표 밑 '검침예정일 | 20'(항목명)",
    [H2, ["8,420", "검침 예정"], ["검침예정일", "20"]], gas(None, 8420), [20],
    includes=["value_absent", "row_label_in_quantity_column"])
add("name_undecided_vs_amount_undecided", "상태와 항목명", "열량 칸 '미정'(부재) + 표 밑 '납부금액 미정 | 247,500'(금액)",
    [H2, ["8,420", "미정"], ["납부금액 미정", "247,500"]], gas(None, 8420), [247500],
    includes=["value_absent", "money_row_excluded"])
add("name_unknown_text_in_heat", "상태와 항목명", "열량 칸 '검침불가'(목록 밖 문구) — 값 아님, 부재로 단정하지 않고 사유 남김",
    [H2, ["8,420", "검침불가"]], gas(None, 8420), includes=["value_not_numeric"], excludes=["value_absent"])
add("name_unknown_text_first_col_row", "상태와 항목명", "표 밑 '검침불가 | 12,000' — 첫 칸을 지우고 12,000을 채택하지 않음, 그 행만 보류",
    [H2, ["8,420", "360,772"], ["검침불가", "12,000"]], gas(360772, 8420), [12000], evidence="360772",
    includes=["row_label_in_quantity_column"])

# 5) 정상 혼합 표 — 항목명이 금액 관련이어도 열 관계대로 물리량 보존, 금액 칸만 제외
MIX = {"납부예정금액": "pay_scheduled", "청구예정금액": "bill_scheduled", "추후청구요금": "later_billing",
       "미확인 청구액": "unconfirmed_billed"}
for label, key in MIX.items():
    add(f"mixed_{key}_valid", "정상 혼합 표", f"혼합 표 항목 '{label}', 정상 열량",
        [M4, [label, "8,420", "360,772", "247,500"]], gas(360772, 8420), [247500], evidence="360772")
    add(f"mixed_{key}_empty", "정상 혼합 표", f"혼합 표 항목 '{label}', 열량 '-'",
        [M4, [label, "8,420", "-", "247,500"]], gas(None, 8420), [247500], includes=["value_absent"])
    add(f"mixed_{key}_pending", "정상 혼합 표", f"혼합 표 항목 '{label}', 열량 '검침 예정'",
        [M4, [label, "8,420", "검침 예정", "247,500"]], gas(None, 8420), [247500], includes=["value_absent"])
    add(f"mixed_{key}_zero", "정상 혼합 표", f"혼합 표 항목 '{label}', 명시 0 MJ",
        [M4, [label, "8,420", "0", "247,500"]], {"metrics": [[0, "MJ"], [8420, "m³"]]}, [247500])

# 6) 단어·열 위치의 결합 — 띄어쓰기 변경 + 열 재배치(원문 좌표도 함께 이동)
for i, (head, row) in enumerate([
        (["요금(원)", "항목", "사용열량(MJ)", "사용량(m3)"], ["247,500", "납부 예정 금액", "360,772", "8,420"]),
        (["사용량(m3)", "요금(원)", "사용열량(MJ)", "항목"], ["8,420", "247,500", "360,772", "추후 청구 요금"]),
        (["항목", "사용열량(MJ)", "요금(원)", "사용량(m3)"], ["청구 예정 금액", "-", "247,500", "8,420"])]):
    add(f"columns_mixed_{i}", "단어·열 위치", f"혼합 표 열 재배치 {i} + 항목명 띄어쓰기 변경", [head, row],
        gas(None if row[head.index("사용열량(MJ)")] == "-" else 360772, 8420), [247500],
        evidence=None if row[head.index("사용열량(MJ)")] == "-" else "360772")
add("columns_swapped_2col_spaced_fee", "단어·열 위치", "2칸 표 열 순서 바뀜 + 표 밑 '납부 예정 금액'",
    [["사용열량(MJ)", "사용량(m3)"], ["360,772", "8,420"], ["납부 예정 금액", "247,500"]], gas(360772, 8420), [247500])
add("columns_swapped_2col_empty_later_fee", "단어·열 위치", "2칸 표 열 순서 바뀜 + 열량 '미검침' + 표 밑 '추후청구요금'",
    [["사용열량(MJ)", "사용량(m3)"], ["미검침", "8,420"], ["추후청구요금", "247,500"]], gas(None, 8420), [247500])

# 7) 금액 표기·숫자
for tag, fee in (("won", "247,500원"), ("krw", "₩247,500")):
    add(f"notation_{tag}_valid", "금액 표기·숫자", f"표 밑 '납부예정금액 | {fee}', 정상 열량",
        [H2, ["8,420", "360,772"], ["납부예정금액", fee]], gas(360772, 8420), [247500], evidence="360772")
    add(f"notation_{tag}_empty", "금액 표기·숫자", f"표 밑 '청구예정금액 | {fee}', 열량 '-'",
        [H2, ["8,420", "-"], ["청구예정금액", fee]], gas(None, 8420), [247500])
add("numbers_other", "금액 표기·숫자", "다른 사용량·열량·금액 + '추후청구요금'",
    [H2, ["5,130", "219,807"], ["추후청구요금", "98,760"]], gas(219807, 5130), [98760], evidence="219807")
add("numbers_other_empty", "금액 표기·숫자", "다른 숫자 + 빈 열량 + '납부예정금액'",
    [H2, ["5,130", "측정 예정"], ["납부예정금액", "98,760"]], gas(None, 5130), [98760])
add("numbers_same_in_heat_and_fee", "금액 표기·숫자", "열량 칸과 '납부예정금액' 칸에 같은 247,500 → 열량 칸만 근거",
    [H2, ["8,420", "247,500"], ["납부예정금액", "247,500"]], gas(247500, 8420), [], evidence="사용열량",
    includes=["money_row_excluded"])

# 8) 기존 보존 — 범위 식별값에 '예정'이 있어도 범위, 다른 근거 유지
add("scope_meter_with_pending_word", "기존 보존", "계량기 열 '전력계 B(교체 예정)'는 범위 식별값",
    [["계량기", "사용량(kWh)"], ["전력계 A", "1,000"], ["전력계 B(교체 예정)", "1,200"]],
    {"metrics": [[1000, "kWh"], [1200, "kWh"]], "scope": {"meter": ["전력계 A", "전력계 B(교체 예정)"]}},
    doc_type="kepco_bill", extra=())
add("scope_period_with_pending_and_absent", "기존 보존", "기간 '2026년 5월(검침 예정)' 행의 사용량 '-' → 4월 1,000 kWh만",
    [["기간", "사용량(kWh)"], ["2026년 4월", "1,000"], ["2026년 5월(검침 예정)", "-"]],
    {"metrics": [[1000, "kWh"]], "scope": {"period": ["2026년 4월"]}}, doc_type="kepco_bill", extra=(),
    includes=["value_absent"])
add("scope_period_second_col", "기존 보존", "사용량·기간 순서(항목 열 없음) — '2026년 5월 예정분'도 기간",
    [["사용량(kWh)", "기간"], ["1,000", "2026년 4월"], ["1,200", "2026년 5월 예정분"]],
    {"metrics": [[1000, "kWh"], [1200, "kWh"]], "scope": {"period": ["2026년 4월", "2026년 5월 예정분"]}},
    doc_type="kepco_bill", extra=())
add("other_source_body", "기존 보존", "빈 열량 + '납부예정금액' 행, 본문 '도시가스 사용열량 360,772 MJ' 보존",
    [H2, ["8,420", "-"], ["납부예정금액", "247,500"]], gas(360772, 8420), [247500],
    extra=(PERIOD, "도시가스 사용열량 360,772 MJ"), paths=["table_cells", "markdown"])
add("other_source_table", "기존 보존", "빈 열량 + '청구예정금액' 행, 다른 표(2쪽)의 360,772 MJ 보존",
    None, gas(360772, 8420), [247500], paths=["table_cells"],
    tables=[[H2, ["8,420", "-"], ["청구예정금액", "247,500"]], [["사용열량(MJ)"], ["360,772"]]])

if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.write_text(json.dumps({
        "note": "PR #68 5차 검토(R8-3) 판정 충돌 검사 입력 — 검증용 가상 입력, 원본 아님. 생성: review-r5/make_cases.py",
        "emission_rule": "E-4-1 TJ = MJ/1e6, E-3-1 tCO2eq = round(MJ*0.0000561, 3)",
        "cases": cases}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(len(cases), "cases", sum(len(c["paths"]) for c in cases.values()), "observations")
