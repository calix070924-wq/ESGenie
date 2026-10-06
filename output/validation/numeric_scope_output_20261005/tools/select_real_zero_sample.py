"""§6.4 실보고서 정상 0 표본 선택 — 제품 코드를 쓰지 않고 원본 PDF 텍스트만으로 후보와 표본 쪽을 고정한다.

사용: python select_real_zero_sample.py <real_reports_dir> <out.json>
선택 규칙(수정 결과를 보기 전에 고정, `02_real_zero_sample/선택규칙.md`와 같다):
  1. 모집단: 5개 실보고서 PDF의 모든 쪽(pymupdf 텍스트).
  2. 사건성 지표 낱말 K가 있는 줄을 기준으로
     Z(숫자 0) — K 줄과 같은 줄 또는 아래 6줄 안에 단독 0 칸(`0`, `0.0`, `0건` 등)이 있는 쪽.
     N(부정 서술) — K와 부정 표현(발생하지 않·없었·없습니다·없음·미발생·전무)이 한 문장에 함께 있는 쪽.
  3. 보고서·층(Z/N)마다 후보 쪽을 쪽 번호순으로 정렬해 고르게 2쪽(1쪽뿐이면 1쪽)을 고른다.
     위치 = round(i·(n−1)/(k−1)), i=0..k−1. 같은 쪽이 Z·N 모두에 뽑히면 한 번만 쓴다.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import fitz

K = ("산업재해", "재해", "사망", "중대", "위반", "벌금", "과징금", "제재", "유출", "누출", "사고",
     "부패", "소송", "분쟁", "리콜")
ZERO_CELL = re.compile(r"^\s*[-–]?\s*0(?:\.0+)?\s*(?:건|명|회|%|원|톤|kg|ton|tCO2eq)?\s*$")
ZERO_INLINE = re.compile(r"(?<![\d.,\-/])0(?:\.0+)?\s*(?:건|명|회)(?![\d])")
NEG = re.compile(r"발생하지\s*않|없었|없습니다|없음|미발생|전무")
SENT = re.compile(r"[^.。!?\n]+[.。!?]?")


def page_candidates(lines: list[str]):
    z, n = [], []
    for i, line in enumerate(lines):
        if not any(k in line for k in K):
            continue
        if ZERO_INLINE.search(line):
            z.append({"line": i, "label": line.strip(), "zero": line.strip()})
        else:
            for j in range(i + 1, min(len(lines), i + 7)):
                if ZERO_CELL.match(lines[j]):
                    z.append({"line": i, "label": line.strip(), "zero_line": j, "zero": lines[j].strip()})
                    break
    text = " ".join(l.strip() for l in lines)
    for m in SENT.finditer(text):
        s = m.group(0).strip()
        if any(k in s for k in K) and NEG.search(s) and len(s) < 300:
            n.append({"sentence": s})
    return z, n


def spaced(pages: list[int], k: int = 2) -> list[int]:
    if not pages:
        return []
    if len(pages) == 1:
        return pages[:1]
    return sorted({pages[round(i * (len(pages) - 1) / (k - 1))] for i in range(k)})


def main() -> None:
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    report = {"rule": __doc__, "reports": []}
    for pdf in sorted(src.glob("*.pdf")):
        doc = fitz.open(pdf)
        zpages, npages, detail = [], [], {}
        for pno, page in enumerate(doc):
            lines = page.get_text().splitlines()
            z, n = page_candidates(lines)
            if z:
                zpages.append(pno)
            if n:
                npages.append(pno)
            if z or n:
                detail[pno] = {"zero": z, "negation": n}
        chosen_z, chosen_n = spaced(zpages), spaced(npages)
        chosen = sorted(set(chosen_z) | set(chosen_n))
        report["reports"].append({
            "pdf": pdf.name, "pages": doc.page_count,
            "candidate_pages": {"Z": len(zpages), "N": len(npages), "union": len(set(zpages) | set(npages))},
            "candidate_items": {"Z": sum(len(d["zero"]) for d in detail.values()),
                                "N": sum(len(d["negation"]) for d in detail.values())},
            "selected": {"Z": chosen_z, "N": chosen_n, "pages": chosen},
            "selected_items": {str(p): detail[p] for p in chosen}})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in report["reports"]:
        print(r["pdf"], r["pages"], r["candidate_pages"], r["candidate_items"], r["selected"])


if __name__ == "__main__":
    main()
