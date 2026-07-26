"""표 추출 방식 비교 실험 (bake-off) — L0 오염의 근본 원인 검증.

배경: 정답셋 라벨링(2026-07-19) 결과 D1 오염 30건이 전부 `__ocr_unstructured__`
노드에서 나왔고, 그 경로는 `pymupdf page.get_text()` → 평탄화 텍스트 → gpt-4.1-mini다.
표의 행·열 구조가 텍스트 단계에서 소실되는 것이 근본 원인이라는 가설을 검증한다.

비교 대상 4방식:
  1. pymupdf_plain   — 현행. page.get_text()
  2. pymupdf_dict    — page.get_text("dict")로 블록·좌표 유지 후 행 재구성
  3. pdfplumber      — extract_tables() (좌표 기반 표 인식)
  4. upstage_dp      — Upstage Document Parse (HTML 표 반환) ※ UPSTAGE_API_KEY 필요

평가 대상 페이지(실측 오염 사례가 나온 바로 그 표):
  - LG화학 p.41  용수 취수·배출·사용량 (재이용률 2.72/3.48/7.90 → E-5-1 오매핑 사례)
  - 모비스 p.49  온실가스 목표 배출량/누적 목표 감축량 (382,946 사례)
  - 모비스 p.53  에너지·재생에너지 법인별 12컬럼 (35.0/1,005 사례)

채점: 각 페이지마다 "정답 셀"(레이블+연도/법인 → 값)을 미리 정의하고,
방식별 출력에서 **레이블과 값의 연결이 복원 가능한지**를 자동 판정한다.
단순 "숫자가 존재하는가"가 아니라 **어느 행·열의 값인지 식별 가능한가**가 기준.

사용:
    python3 scripts/table_extract_bakeoff.py                # 무료 3방식
    python3 scripts/table_extract_bakeoff.py --with-upstage # Upstage 포함(과금)
    python3 scripts/table_extract_bakeoff.py --dump         # 원문 출력도 파일로 저장
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "outputs" / "table_bakeoff"

# ---------------------------------------------------------------------------
# 평가 케이스 — 실측 오염 사례에서 역산한 정답 셀
# ---------------------------------------------------------------------------

CASES = [
    {
        "id": "lgchem_water",
        "pdf": "data/real_reports/051910_lgchem_2025.pdf",
        "page": 41,
        "desc": "LG화학 용수 취수·배출·사용량 (3개년 컬럼)",
        # (행 레이블, 기대값, 이 값이 속한 열 라벨)
        "cells": [
            ("용수 취수량", "65,237", "2025"),
            ("용수 사용량", "43,947", "2025"),
            ("용수 재이용률", "7.90", "2025"),
            ("용수 재이용률", "2.72", "2023"),
        ],
        # 오염 사례: 2.72(2023 재이용률)가 E-5-1(용수 사용량)로 들어갔다
        "contamination": "재이용률 2.72를 용수 사용량으로 오매핑",
    },
    {
        "id": "mobis_ghg_target",
        "pdf": "data/real_reports/012330_mobis_2025.pdf",
        "page": 49,
        "desc": "모비스 온실가스 목표 배출량 / 누적 목표 감축량",
        "cells": [
            ("누적 온실가스 목표 감축량", "382,946", "2035"),
            ("온실가스 목표 배출량", "268,062", "2030"),
        ],
        "contamination": "목표 감축량 382,946을 실적 배출량(E-3-1)으로 오매핑",
    },
    {
        "id": "mobis_renewable_multicol",
        "pdf": "data/real_reports/012330_mobis_2025.pdf",
        "page": 53,
        "desc": "모비스 재생에너지 사용·전환율 (법인별 12컬럼)",
        "cells": [
            ("재생에너지 사용·전환율", "12.9", "합계(전사)"),
        ],
        "contamination": "12컬럼 중 전사 값 식별 실패 → 35.0 오염",
    },
    {
        "id": "mobis_energy_total",
        "pdf": "data/real_reports/012330_mobis_2025.pdf",
        "page": 52,
        "desc": "모비스 에너지 사용량 (레이블이 별도 헤더 행에 있는 표)",
        "cells": [
            ("에너지 사용량", "9,075", "합계(전사)"),
            ("TJ", "9,075", "합계(전사)"),   # 단위 셀이 행 선두인 구조 확인용
        ],
        "contamination": "총 사용량 9,075 대신 재생에너지 전환량 1,005를 E-4-1로",
    },
]


# ---------------------------------------------------------------------------
# 추출 방식
# ---------------------------------------------------------------------------

def extract_pymupdf_plain(pdf: Path, page_no: int) -> str:
    import fitz
    doc = fitz.open(pdf)
    return doc[page_no].get_text()


def extract_pymupdf_dict(pdf: Path, page_no: int) -> str:
    """블록·라인 좌표를 살려 y좌표로 행을 묶고 x순으로 정렬 — 표 형태 복원 시도."""
    import fitz
    doc = fitz.open(pdf)
    d = doc[page_no].get_text("dict")
    spans: list[tuple[float, float, str]] = []
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                txt = span.get("text", "").strip()
                if txt:
                    x0, y0 = span["bbox"][0], span["bbox"][1]
                    spans.append((round(y0, 1), x0, txt))
    # y좌표 근접(±3pt) 스팬을 같은 행으로 묶음
    spans.sort()
    rows: list[list[tuple[float, str]]] = []
    cur_y = None
    for y, x, txt in spans:
        if cur_y is None or abs(y - cur_y) > 3:
            rows.append([]); cur_y = y
        rows[-1].append((x, txt))
    lines = []
    for r in rows:
        r.sort()
        lines.append(" | ".join(t for _, t in r))
    return "\n".join(lines)


def extract_pdfplumber(pdf: Path, page_no: int) -> str:
    import pdfplumber
    out = []
    with pdfplumber.open(pdf) as doc:
        page = doc.pages[page_no]
        tables = page.extract_tables()
        for ti, tbl in enumerate(tables):
            out.append(f"--- table {ti} ---")
            for row in tbl:
                out.append(" | ".join((c or "").replace("\n", " ").strip() for c in row))
        if not tables:
            out.append("(표 인식 실패)")
            out.append(page.extract_text() or "")
    return "\n".join(out)


def extract_upstage(pdf: Path, page_no: int) -> str:
    """Upstage Document Parse — 기존 구현 재사용(HTML 표 구조 반환)."""
    from esgenie.ssot.ocr_router import _call_upstage_dp  # type: ignore
    tokens = _call_upstage_dp(str(pdf), ocr_mode="auto", pages=[page_no + 1])
    parts = []
    for t in tokens:
        parts.append(t.get("html") or t.get("text") or "")
    return "\n".join(parts)


METHODS = {
    "pymupdf_plain": extract_pymupdf_plain,
    "pymupdf_dict": extract_pymupdf_dict,
    "pdfplumber": extract_pdfplumber,
}


# ---------------------------------------------------------------------------
# 채점 — 레이블-값 연결이 복원 가능한가
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def score_cell(text: str, label: str, value: str) -> str:
    """LINKED: 레이블과 값이 같은 줄 / PRESENT: 값은 있으나 줄 분리 / MISSING: 값 없음."""
    val_variants = {value, value.replace(",", "")}
    flat = _norm(text)
    if not any(v.replace(",", "") in flat.replace(",", "") for v in val_variants):
        return "MISSING"
    lab = _norm(label)
    for line in text.splitlines():
        nl = _norm(line)
        if lab in nl and any(v.replace(",", "") in nl.replace(",", "") for v in val_variants):
            return "LINKED"
    return "PRESENT"


def main() -> None:
    ap = argparse.ArgumentParser(description="표 추출 방식 비교")
    ap.add_argument("--with-upstage", action="store_true", help="Upstage DP 포함(과금)")
    ap.add_argument("--dump", action="store_true", help="방식별 원문 출력 저장")
    args = ap.parse_args()

    methods = dict(METHODS)
    if args.with_upstage:
        if not (os.getenv("UPSTAGE_API_KEY") or os.getenv("UPSTAGE_KEY")):
            print("⚠ UPSTAGE_API_KEY 없음 — Upstage 건너뜀")
        else:
            methods["upstage_dp"] = extract_upstage

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results: dict = {}

    for case in CASES:
        pdf = ROOT / case["pdf"]
        print(f"\n{'='*78}\n[{case['id']}] {case['desc']}  (p.{case['page']})")
        print(f"  오염 사례: {case['contamination']}")
        print(f"{'-'*78}")
        print(f"  {'방식':<16}" + "".join(f"{lab[:10]+'/'+col[:6]:>20}" for lab, _v, col in case["cells"]))
        results[case["id"]] = {}
        for name, fn in methods.items():
            try:
                text = fn(pdf, case["page"])
            except Exception as exc:
                print(f"  {name:<16} ERROR: {exc}")
                results[case["id"]][name] = {"error": str(exc)}
                continue
            if args.dump:
                (OUT_DIR / f"{case['id']}__{name}.txt").write_text(text, encoding="utf-8")
            scores = [score_cell(text, lab, val) for lab, val, _col in case["cells"]]
            results[case["id"]][name] = {
                "scores": scores,
                "linked": scores.count("LINKED"),
                "chars": len(text),
            }
            print(f"  {name:<16}" + "".join(f"{s:>20}" for s in scores))

    # 종합
    print(f"\n{'='*78}\n종합 (LINKED = 레이블-값 연결 복원 성공)")
    total_cells = sum(len(c["cells"]) for c in CASES)
    for name in methods:
        linked = sum(results[c["id"]].get(name, {}).get("linked", 0) for c in CASES)
        print(f"  {name:<16} {linked}/{total_cells}  ({linked/total_cells*100:.0f}%)")

    (OUT_DIR / "bakeoff_result.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {OUT_DIR/'bakeoff_result.json'}" + ("  (원문 덤프 포함)" if args.dump else ""))


if __name__ == "__main__":
    main()
