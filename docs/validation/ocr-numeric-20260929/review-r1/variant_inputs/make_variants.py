"""검토 보완 R2·R4 화면 확인용 변형 입력(가상). 원본 PDF는 읽기만 하고 이 폴더에 새 파일을 만든다.

R2: 02 전기요금청구서의 '당월 전력 사용량' 표 칸만 142,560 → 150,000 (지침·배율·계산 줄·청구 내역은 원문 그대로).
R4: 같은 사용량 1,000 kWh를 사업장별 한 행짜리 표 두 개로 적은 가상 청구서.
"""
import hashlib
import json
import re
import sys
from pathlib import Path

import fitz

PACK = Path(sys.argv[1])
OUT = Path(__file__).parent
SRC = PACK / "01_처음업로드_12건" / "02_전기요금청구서_2026-04.pdf"
LABEL = "검토 보완 검증용 변형본(가상) — 원본 아님"


def r2():
    doc = fitz.open(SRC)
    page = doc[0]
    hits = [r for r in page.search_for("142,560") if r.y0 > 320 and r.y1 < 345 and r.x0 > 370]
    assert len(hits) == 1, hits           # 표의 사용량 칸 하나만
    rect = hits[0]
    page.add_redact_annot(rect, fill=(1, 1, 1))
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
    page.insert_text((rect.x0, rect.y1 - 1.6), "150,000", fontsize=8.6, fontname="helv")
    page.insert_text((300, 60), LABEL, fontsize=8, fontname="korea", color=(0.8, 0, 0))
    out = OUT / "R2변형_02_전기요금청구서_사용량칸150000.pdf"
    doc.save(out)
    return out, {"changed": "당월 전력 사용량 표 칸 142,560 → 150,000", "rect": list(rect)}


def r4():
    doc = fitz.open()
    page = doc.new_page(width=595.28, height=841.89)

    def t(xy, s, size=9):
        # 숫자·영문은 helv, 한글은 korea — korea 글꼴의 ASCII 전각 간격이 OCR을 흐리지 않게.
        x, y = xy
        for run in re.findall(r"[\x20-\x7e]+|[^\x20-\x7e]+", s):
            font = "helv" if run.isascii() else "korea"
            page.insert_text((x, y), run, fontsize=size, fontname=font)
            x += fitz.get_text_length(run, fontname=font, fontsize=size)
    t((60, 95), "전기요금 청구서", 18)
    t((60, 120), "한빛전력서비스(가상) / 2026년 4월 사용분")
    t((300, 60), LABEL, 8)
    t((60, 150), "사용 기간: 2026-04-01 ~ 2026-04-30")
    for i, site in enumerate(["김해 제1공장", "양산 제2공장"]):
        y = 190 + i * 110
        t((60, y - 10), f"사업장별 사용량 {i + 1}", 11)
        rows = [["사업장", "사용량(kWh)"], [site, "1,000"]]
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                box = fitz.Rect(60 + c * 200, y + r * 26, 260 + c * 200, y + 26 + r * 26)
                page.draw_rect(box, color=(0, 0, 0), width=0.7)
                t((box.x0 + 8, box.y1 - 9), text)
    out = OUT / "R4변형_전기요금청구서_사업장별표2개.pdf"
    doc.save(out)
    return out, {"tables": 2, "rows": "사업장 | 사용량(kWh) / 김해 제1공장 | 1,000 ; 양산 제2공장 | 1,000"}


record = {"source": str(SRC), "source_sha256": hashlib.sha256(SRC.read_bytes()).hexdigest(), "variants": []}
for fn in (r2, r4):
    path, info = fn()
    record["variants"].append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), **info})
(OUT / "variants.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(record, ensure_ascii=False, indent=1))
