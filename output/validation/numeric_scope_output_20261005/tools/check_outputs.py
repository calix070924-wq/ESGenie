"""BM 개편 세트 실행 결과(그래프·응답서·LLM 본문·Excel·PDF)를 원문 정답과 대조한다(제품 코드 비의존).

사용: python check_outputs.py <run>/<stage> [--json out.json]
정답은 원본 PDF를 직접 읽어 고정한 값이다(`00_baseline/expected_answers.md`). 제품 함수의 반환값을 정답으로 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import glob
import json
import re
from pathlib import Path

import fitz
import openpyxl

KEY = {
    "electricity_kwh": 142560, "gas_m3": 8420, "water_m3": 680,
    "waste_total_kg": 18400, "waste_recycled_kg": 5400, "waste_rate_pct": 29.3,
    "scrap_generated_kg": 12500, "scrap_reused_kg": 11500, "scrap_exported_kg": 1000, "scrap_reuse_pct": 92.0,
    "training_target": 50, "training_attended": 46, "training_absent": 4, "training_rate_pct": 92.0,
    "followup_attended": 4, "unique_total": 50,
}
MARK = "[검토:"


def load(stage: Path):
    pipeline = json.loads((stage / "pipeline.json").read_text(encoding="utf-8"))
    sheet = json.loads((stage / "result.json").read_text(encoding="utf-8"))["sheet"]
    exports = stage / "exports"
    md = next(iter(glob.glob(str(exports / "*" / "ESG보고서_*.md"))), None)
    report_pdf = next(iter(glob.glob(str(exports / "*" / "ESG보고서_*.pdf"))), None)
    datasheet = next(iter(glob.glob(str(exports / "*" / "ESG_DataSheet_*.xlsx"))), None)
    sheet_xlsx = next(iter(glob.glob(str(exports / "response_sheet" / "*.xlsx"))), None)
    sheet_pdf = next(iter(glob.glob(str(exports / "response_sheet" / "*.pdf"))), None)
    return pipeline, sheet, md, report_pdf, datasheet, sheet_xlsx, sheet_pdf


def nodes_of(pipeline, prefix):
    return [n for n in pipeline["evidence_graph"]["nodes"] if (n.get("source_file") or "").startswith(prefix)]


def zero_status(node):
    for p in (node.get("boundary") or {}).get("provenance", []):
        if isinstance(p, dict) and p.get("source") == "zero_evidence":
            return f"{p.get('status')}/{p.get('cause')}"
    return ""


def pdf_text(path):
    if not path:
        return "", 0
    doc = fitz.open(path)
    return "\n".join(page.get_text() for page in doc), doc.page_count


def check(stage: Path) -> dict:
    pipeline, sheet, md, report_pdf, datasheet, sheet_xlsx, sheet_pdf = load(stage)
    followup = stage.name == "followup"
    rows = []

    def add(item, ok, observed, expected, where):
        rows.append({"item": item, "pass": bool(ok), "observed": observed, "expected": expected, "where": where})

    # ── 그래프·추출 ─────────────────────────────────────────────
    e02 = [n for n in nodes_of(pipeline, "02_") if n["metric"] == "E-4-1"]
    add("02 전기 142,560 kWh(E-4-1)", any(n["value"] == KEY["electricity_kwh"] and n["unit"] == "kWh" for n in e02),
        [(n["value"], n["unit"]) for n in e02], "142560 kWh", "graph")
    g03 = nodes_of(pipeline, "03_")
    add("03 가스 8,420 m³, 열량·배출량 미생성", any(n["value"] == KEY["gas_m3"] and n["unit"] in ("m³", "m3") for n in g03)
        and not any(n["unit"] in ("MJ", "GJ", "TJ") or n["metric"] in ("E-4-1", "E-3-1") for n in g03),
        [(n["metric"], n["value"], n["unit"]) for n in g03], "8420 m³ only", "graph")
    g07 = nodes_of(pipeline, "07_")
    add("07 수도 680 m³", any(n["value"] == KEY["water_m3"] for n in g07), [(n["metric"], n["value"], n["unit"]) for n in g07],
        "680 m³", "graph")
    g04 = nodes_of(pipeline, "04_")
    vals04 = {(n["metric"], n["value"], n["unit"]) for n in g04}
    add("04 총 위탁량 18,400 kg·재활용량 5,400 kg·재활용률 29.3%",
        any(v == KEY["waste_total_kg"] and u == "kg" for _, v, u in vals04)
        and any(v == KEY["waste_recycled_kg"] and u == "kg" for _, v, u in vals04)
        and any(v == KEY["waste_rate_pct"] and u == "%" for _, v, u in vals04), sorted(vals04), "3값", "graph")
    add("04 29.3% 반올림 정합(5,400÷18,400=29.3478…%)", round(5400 / 18400 * 100, 1) == 29.3, "29.3478 → 29.3", "29.3", "key")
    add("06 규정에서 수량 미생성(3톤 없음)", not nodes_of(pipeline, "06_"),
        [(n["metric"], n["value"], n["unit"]) for n in nodes_of(pipeline, "06_")], "노드 0", "graph")
    g08 = nodes_of(pipeline, "08_")
    rate08 = [n for n in g08 if n["unit"] == "%"]
    add("08 내부 재투입률 92%는 E-2-2·E-6-2가 아님", rate08 and all(n["metric"] not in ("E-2-2", "E-6-2") for n in rate08),
        [(n["metric"], n["value"]) for n in rate08], "코드 없음(원문 라벨)", "graph")
    totals = {n["value"]: n["metric"] for n in g08 if n["value"] in (12500, 11500, 1000)}
    add("08 발생 12,500·재투입 11,500·반출 1,000 라벨 구분",
        "발생" in totals.get(12500, "") and "재투입" in totals.get(11500, "") and "반출" in totals.get(1000, ""),
        totals, "발생/재투입/반출 라벨", "graph")
    g09 = nodes_of(pipeline, "09_")
    t50 = [n["metric"] for n in g09 if n["value"] == 50]
    add("09 50명은 대상(참석·출석 라벨 아님)", t50 and all("대상" in m and not re.search(r"(?<!미)(참석|출석)\s*인원$", m) for m in t50),
        t50, "대상 50", "graph")
    add("09 참석 46·미참석 4·92%", any(n["value"] == 46 for n in g09) and any(n["value"] == 4 for n in g09)
        and any(n["value"] == 92 and n["unit"] == "%" for n in g09),
        sorted({(n["metric"], n["value"]) for n in g09}), "46/4/92%", "graph")
    g11 = nodes_of(pipeline, "11_")
    isms = [n for n in g11 if n["value"] == 0]
    # 2026-10-05 R2 관찰 뒤 확장: 모델이 같은 원문을 수치 0이 아니라 조항으로 보고할 수 있다(실행마다 0/null/조항).
    # 수치 0(보존 판정) 또는 기준일이 붙은 '미보유' 조항 원문이면 원문 사실 보존으로 본다. 미확인 표시는 실패다.
    clauses = [t for t in pipeline["evidence_graph"]["text_nodes"]
               if (t.get("source_file") or "").startswith("11_") and "ISMS" in t.get("text", "")
               and "미보유" in t.get("text", "") and "2026-04-30" in t.get("text", "")]
    unread = [f for f in pipeline.get("review_findings") or []
              if "인증 보유" in str(f.get("fact") or "") and "읽지 못한" in str(f.get("title") or "")]
    add("11 ISMS 인증 미보유·기준일 2026-04-30 보존(수치 0 또는 원문 조항)",
        (bool(isms) and all(zero_status(n).split("/")[0] in ("CONFIRMED", "SOURCE_ONLY") for n in isms) or bool(clauses))
        and not unread,
        {"zero_nodes": [(n["metric"], n["value"], zero_status(n)) for n in isms], "clauses": [t["text"][:60] for t in clauses],
         "unread_findings": len(unread)}, "0 또는 미보유 조항, 미확인 표시 없음", "graph")
    if followup:
        g13 = nodes_of(pipeline, "13_")
        m13 = {n["value"]: n["metric"] for n in g13}
        add("13 추가 참석 4·중복 제외 합계 50·22일 46", 4 in m13 and 50 in m13 and 46 in m13, m13, "4/50/46", "graph")
        add("13 50명 라벨이 '대상자 합계'가 아님(원문: 중복 제외 합계)", "중복" in m13.get(50, "") or "고유" in m13.get(50, ""),
            m13.get(50), "중복 제외 합계", "graph")
    all_values = [n["value"] for n in pipeline["evidence_graph"]["nodes"]]
    add("이전 세트 360,772 MJ 미유입", 360772 not in all_values, "", "없음", "graph")

    # ── 응답서 ─────────────────────────────────────────────────
    answers = {a["qid"]: a for a in sheet["answers"]}
    e62 = answers.get("RBA-C-4-E-6-2", {})
    reason = (e62.get("comparison_reason") or "") + " " + " ".join(e62.get("flags") or [])
    add("응답서 E-6-2 29.3% · 4월 제1공장 범위", e62.get("value") == 29.3 and "제1공장" in (e62.get("boundary_label") or ""),
        (e62.get("value"), e62.get("boundary_label")), "29.3 · 부분", "answer")
    add("응답서 05 회사 답변 92%를 일치·동일 범위 충돌로 확정하지 않음", e62.get("comparison") not in ("compared", "mismatch"),
        (e62.get("status"), e62.get("comparison")), "not_comparable 또는 scope_unconfirmed", "answer")
    add("응답서 05 설명: 92%의 원문 출처(08 내부 재투입률)와 29.3% 근거", "08_" in reason and "재투입" in reason and "29.3" in reason,
        reason[:300], "08 재투입률 + 29.3% 계산식", "answer")
    add("응답서 05 설명이 기간·사업장을 '미확인'으로 잘못 적지 않음", "기간·사업장·분모 미확인" not in reason, reason[:120],
        "기간·사업장 같음 명시", "answer")
    e41 = answers.get("RBA-C-8-E-4-1", {})
    add("응답서 E-4-1 0.513216 TJ 부분값(전력만)", e41.get("value") == 0.513216 and "부분" in (e41.get("boundary_label") or ""),
        (e41.get("value"), e41.get("boundary_label")), "0.513216 TJ · 부분", "answer")
    c2 = answers.get("RBA-C-2", {})
    add("응답서 C-2가 재생 원부자재 92%를 근거로 쓰지 않음", "재생 원부자재" not in (c2.get("rationale") or ""),
        (c2.get("status"), (c2.get("rationale") or "")[:80]), "E-2-2 92% 미사용", "answer")

    # ── LLM 본문(보고서 Markdown) ───────────────────────────────
    body = Path(md).read_text(encoding="utf-8") if md else ""
    narrative = "\n".join(line for line in body.splitlines() if not line.startswith("|"))
    add("본문·요약에 '재생 원부자재 비율 92%' 없음", not re.search(r"재생\s*원부자재[^.\n]{0,20}92", narrative),
        re.findall(r"[^.\n]*재생\s*원부자재[^.\n]*", narrative)[:2], "없음", "report_md")
    unmarked = []
    for sentence in re.split(r"(?<=[.!?])\s+", narrative):
        if re.search(r"(?:사업장\s*전체|전사|합산)[^.]*0\.513216|0\.513216[^.]*(?:사업장\s*전체|전사|합산)", sentence) and MARK not in sentence:
            unmarked.append(sentence.strip()[:160])
    add("본문 E-4-1을 전체·합산으로 넓힌 문장은 표시됨", not unmarked, unmarked, "표시 또는 없음", "report_md")
    wrong_heads = [s.strip()[:160] for s in re.split(r"(?<=[.!?])\s+", narrative)
                   if re.search(r"정규직\s*39\s*명|기간제\s*6\s*명이\s*출석|파견직\s*2\s*명은\s*미참석", s) and MARK not in s]
    add("본문 교육 인원 오계산(39명 등) 단정 문장 없음", not wrong_heads, wrong_heads, "없음 또는 표시", "report_md")

    claims_isms = [a["qid"] for a in sheet["answers"]
                   if re.search(r"ISMS[^.]{0,20}(?:보유|취득)(?!\s*하지|하지)", (a.get("rationale") or "") + " ".join(a.get("flags") or []))
                   and "미보유" not in (a.get("rationale") or "")]
    widened = re.findall(r"[^.\n]*ISMS[^.\n]*(?:취득|보유하고)[^.\n]*", narrative)
    add("본문·응답서에 ISMS 인증 보유·취득 주장 없음", not claims_isms and not [w for w in widened if "미보유" not in w],
        {"answers": claims_isms, "body": widened[:2]}, "없음", "answer+report_md")

    # ── 응답서 Excel ──────────────────────────────────────────
    xrow = {}
    if sheet_xlsx:
        ws = openpyxl.load_workbook(sheet_xlsx)["응답서"]
        for r in ws.iter_rows(min_row=5, values_only=True):
            if r and r[0]:
                xrow[str(r[0])] = [str(c) if c is not None else "" for c in r]
    x62 = " | ".join(xrow.get("RBA-C-4-E-6-2", []))
    add("Excel E-6-2 행: 29.3%·범위·회사 답변 92%·출처", "29.3" in x62 and "제1공장" in x62 and "92" in x62 and "08_" in x62,
        x62[:400], "값·범위·회사 답변·출처", "excel")
    x41 = " | ".join(xrow.get("RBA-C-8-E-4-1", []))
    add("Excel E-4-1 행: 부분값 범위", "0.513216" in x41 and "부분" in x41, x41[:200], "부분", "excel")
    ds = {}
    if datasheet:
        ws = openpyxl.load_workbook(datasheet)["DataSheet"]
        head = [c.value for c in ws[1]]
        for r in ws.iter_rows(min_row=2, values_only=True):
            ds[str(r[0])] = dict(zip(head, r))
    add("데이터시트 E-5-1·E-6-1 측정 범위 표시", all("제1공장" in str((ds.get(c) or {}).get("측정 범위") or "") for c in ("E-5-1", "E-6-1")),
        {c: (ds.get(c) or {}).get("측정 범위") for c in ("E-5-1", "E-6-1")}, "4월·제1공장", "datasheet")

    # ── PDF ───────────────────────────────────────────────────
    stext, spages = pdf_text(sheet_pdf)
    flat = re.sub(r"\s+", "", stext)
    add("응답서 PDF 생성·E-6-2 값과 회사 답변 출처", spages > 0 and "29.3" in flat and "08_공정스크랩관리대장" in flat,
        {"pages": spages}, "29.3·08 출처", "sheet_pdf")
    rtext, rpages = pdf_text(report_pdf)
    rflat = re.sub(r"\s+", "", rtext)
    add("보고서 PDF에 '재생원부자재비율92' 없음", rpages > 0 and not re.search(r"재생원부자재비율[^。]{0,8}92", rflat),
        {"pages": rpages}, "없음", "report_pdf")
    add("보고서 PDF 본문 표시가 Markdown과 같음", (body.count(MARK) > 0) == (rtext.count(MARK) > 0) if body else False,
        {"md_marks": body.count(MARK), "pdf_marks": rtext.count(MARK)}, "같음", "report_pdf")
    return {"stage": str(stage), "passed": sum(r["pass"] for r in rows), "total": len(rows), "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", type=Path)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    result = check(args.stage)
    for r in result["rows"]:
        print(f"{'PASS' if r['pass'] else 'FAIL'} [{r['where']}] {r['item']} :: {str(r['observed'])[:180]}")
    print(f"passed {result['passed']}/{result['total']}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
