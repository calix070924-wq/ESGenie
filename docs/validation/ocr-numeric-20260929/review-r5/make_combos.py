"""R8-3 구현 후 대표 조합 — tests/fixtures/ocr_numeric_review_r5/combo_cases.json.

구현·판정표 작성에 쓰지 않은 자연스러운 표현을 구현 후 골랐다: 금액 단어와 부재 단어가 한 칸에 함께
있는 항목명, 쓰지 않았던 상태 표기, 다른 금액 머리글·통화 표기, 전기 고지서 혼합 표.
기대값 규칙은 make_cases.py와 같다. 목록 밖 상태 표기는 값·답변만 보고 사유 종류는 고정하지 않는다
(부재로 건너뛰든 보류하든 금액을 물리량으로 쓰지 않고, 같은 행의 다른 확정값은 유지해야 한다).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import make_cases as M  # noqa: E402

M.cases.clear()
add, gas, H2, PERIOD = M.add, M.gas, M.H2, M.PERIOD

# 1) 금액 단어 + 부재 단어가 한 칸에 — 표 밑 금액 행
for key, label in (("pay_pending_amount", "당월 납부 예정액"), ("fee_undecided", "요금 미정"),
                   ("amount_checking", "금액 확인중"), ("later_settlement", "추후 정산 금액"),
                   ("next_bill_pending", "다음 달 청구 예정 요금")):
    add(f"combo_{key}_valid", "금액+부재 단어", f"표 밑 '{label} | 98,760', 정상 열량 219,807",
        [H2, ["5,130", "219,807"], [label, "98,760"]], gas(219807, 5130), [98760], evidence="219807")
    add(f"combo_{key}_empty", "금액+부재 단어", f"표 밑 '{label} | 98,760', 열량 '미측정'",
        [H2, ["5,130", "미측정"], [label, "98,760"]], gas(None, 5130), [98760])

# 2) 쓰지 않았던 상태 표기 × 금액 행
for key, m in (("before_reading", "검침 전"), ("measuring", "측정중"), ("unmetered", "미계량"),
               ("later_notice", "추후 통보"), ("na_lower", "n/a")):
    add(f"combo_marker_{key}_fee", "상태 표기+금액 행", f"열량 칸 '{m}' + 표 밑 '청구 예정 금액 | ₩98,760'",
        [H2, ["5,130", m], ["청구 예정 금액", "₩98,760"]], gas(None, 5130), [98760])

# 3) 다른 금액 머리글·통화 표기의 정상 혼합 표
add("combo_mixed_billed_header", "혼합 표", "머리글 '청구금액(원)', 항목 '당월 납부 예정액', 통화 '원'",
    [["항목", "사용량(m3)", "사용열량(MJ)", "청구금액(원)"], ["당월 납부 예정액", "5,130", "219,807", "98,760원"]],
    gas(219807, 5130), [98760], evidence="219807")
add("combo_mixed_empty_heat_later", "혼합 표", "항목 '추후 정산 금액', 열량 칸 '측정중'",
    [["항목", "사용량(m3)", "사용열량(MJ)", "요금(원)"], ["추후 정산 금액", "5,130", "측정중", "98,760"]],
    gas(None, 5130), [98760])
add("combo_mixed_kepco", "혼합 표", "전기 혼합 표 항목 '청구 예정 요금' → 142,560 kWh 보존",
    [["항목", "사용량(kWh)", "요금(원)"], ["청구 예정 요금", "142,560", "17,820,000"]],
    {"metrics": [[142560, "kWh"]]}, [17820000], doc_type="kepco_bill", extra=())

# 4) 첫 칸(사용량)의 글자 + 열량 숫자 — 완결된 상태 표기면 열량 보존, 뜻을 모르는 글자면 그 행만 보류.
#    '추후 통보'는 첫 구현에서 목록 밖으로 빠져 열량이 보류됐다(06a1433·95683bc는 보존) → 문법에 '통보' 추가.
for key, m in (("before_reading", "검침 전"), ("later_notice", "추후 통보"), ("notice_pending", "통보 예정")):
    add(f"combo_first_col_{key}", "첫 칸 상태 표기", f"첫 칸 '{m}' + 열량 360,772 → 열량 보존, 값 없음 기록",
        [H2, [m, "360,772"]], gas(360772, None), evidence="360772", includes=["value_absent"],
        excludes=["row_label_in_quantity_column"])
for key, m in (("unreadable", "검침불가"), ("check_request", "확인 요망")):
    add(f"combo_first_col_{key}", "첫 칸 상태 표기", f"첫 칸 '{m}'(뜻 불명) + 360,772 → 글자를 지우고 채택하지 않음, 그 행 보류",
        [H2, [m, "360,772"]], gas(None, None), [360772], includes=["row_label_in_quantity_column"])

# 5) 같은 숫자 — 열량 칸과 금액 행
add("combo_same_number_won", "같은 숫자", "열량 칸과 '추후 정산 금액 | 98,760원'이 같은 98,760 → 열량 칸만",
    [H2, ["5,130", "98,760"], ["추후 정산 금액", "98,760원"]], gas(98760, 5130), [], evidence="사용열량",
    includes=["money_row_excluded"])

if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.write_text(json.dumps({
        "note": "PR #68 5차 검토(R8-3) 구현 후 대표 조합 — 검증용 가상 입력, 원본 아님. 생성: review-r5/make_combos.py",
        "emission_rule": "E-4-1 TJ = MJ/1e6, E-3-1 tCO2eq = round(MJ*0.0000561, 3)",
        "cases": M.cases}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(len(M.cases), "cases", sum(len(c["paths"]) for c in M.cases.values()), "observations")
