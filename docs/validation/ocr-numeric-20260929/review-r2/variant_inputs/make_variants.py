"""2차 검토 보완 R6·R7 확인용 변형 입력(가상). 원본 PDF는 읽기만 하고 이 폴더에 새 파일을 만든다.

R6a·b: 전기 `사용량(kWh)|전월지침|당월지침` / `<빈칸>|1,000|1,250` (배율 없음), 빈칸 = '-' 또는 공백.
R6c·d: 가스 `가스사용량(MJ)|전월지침(m3)` / `<빈칸>|31,580`, 빈칸 = '-' 또는 공백.
R6e:   BM 02 전기요금청구서의 '당월 전력 사용량' 표 칸만 142,560 kWh → '-' (지침·배율·계산 줄·본문은 원문 그대로).
R7a:   같은 쪽 표 두 개 — 김해 제1공장 2026년 4월·5월 각 1,000 kWh.
R7b:   같은 쪽 표 두 개 — 김해 제1공장 전력계 A 1,000 kWh·전력계 B 1,200 kWh(사용 기간 2026-04).
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


def _writer(page):
    def t(xy, s, size=9):
        # 숫자·영문은 helv, 한글은 korea — korea 글꼴의 ASCII 전각 간격이 OCR을 흐리지 않게.
        x, y = xy
        for run in re.findall(r"[\x20-\x7e]+|[^\x20-\x7e]+", s):
            font = "helv" if run.isascii() else "korea"
            page.insert_text((x, y), run, fontsize=size, fontname=font)
            x += fitz.get_text_length(run, fontname=font, fontsize=size)
    return t


def _bill(name, title, lines, tables, width=150):
    doc = fitz.open()
    page = doc.new_page(width=595.28, height=841.89)
    t = _writer(page)
    t((60, 95), title, 18)
    t((300, 60), LABEL, 8)
    for i, line in enumerate(lines):
        t((60, 125 + i * 18), line)
    y0 = 150 + len(lines) * 18
    for i, (caption, rows) in enumerate(tables):
        y = y0 + i * 120
        t((60, y - 10), caption, 11)
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                box = fitz.Rect(60 + c * width, y + r * 26, 60 + (c + 1) * width, y + 26 + r * 26)
                page.draw_rect(box, color=(0, 0, 0), width=0.7)
                if text:
                    t((box.x0 + 8, box.y1 - 9), text)
    out = OUT / name
    doc.save(out)
    return out, {"tables": [rows for _, rows in tables], "lines": lines}


def r6_elec(empty, tag):
    return _bill(f"R6{tag}_전기_사용량칸{'대시' if empty else '공백'}_배율없음.pdf", "전기요금 청구서",
                 ["한빛전력서비스(가상) / 2026년 4월 사용분", "사업장: 김해 제1공장"],
                 [("검침과 사용량", [["사용량(kWh)", "전월지침", "당월지침"], [empty, "1,000", "1,250"]])])


def r6_gas(empty, tag):
    return _bill(f"R6{tag}_가스_열량칸{'대시' if empty else '공백'}_전월지침만.pdf", "도시가스 요금 고지서",
                 ["한빛도시가스(가상) / 2026년 4월 사용분", "사업장: 김해 제1공장"],
                 [("검침과 사용량", [["가스사용량(MJ)", "전월지침(m3)"], [empty, "31,580"]])], width=200)


def r6_bm():
    doc = fitz.open(SRC)
    page = doc[0]
    hits = [r for r in page.search_for("142,560 kWh") if 320 < r.y0 and r.y1 < 345 and r.x0 > 370]
    assert len(hits) == 1, hits           # 표의 사용량 칸 하나만
    rect = hits[0]
    page.add_redact_annot(rect, fill=(1, 1, 1))
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
    page.insert_text((rect.x0, rect.y1 - 1.6), "-", fontsize=8.6, fontname="helv")
    page.insert_text((300, 60), LABEL, fontsize=8, fontname="korea", color=(0.8, 0, 0))
    out = OUT / "R6e변형_02_전기요금청구서_사용량칸대시.pdf"
    doc.save(out)
    return out, {"source": SRC.name, "changed": "당월 전력 사용량 표 칸 '142,560 kWh' → '-'", "rect": list(rect)}


def r7_period():
    head = ["사업장", "기간", "사용량(kWh)"]
    return _bill("R7a_전기_같은쪽_4월5월표.pdf", "전기요금 청구서", ["한빛전력서비스(가상)"],
                 [("월별 사용전력량 1", [head, ["김해 제1공장", "2026년 4월", "1,000"]]),
                  ("월별 사용전력량 2", [head, ["김해 제1공장", "2026년 5월", "1,000"]])])


def r7_meter():
    head = ["사업장", "계량기", "사용량(kWh)"]
    return _bill("R7b_전기_같은쪽_계량기AB표.pdf", "전기요금 청구서",
                 ["한빛전력서비스(가상)", "사용 기간: 2026-04-01 ~ 2026-04-30"],
                 [("계량기별 사용전력량 1", [head, ["김해 제1공장", "전력계 A", "1,000"]]),
                  ("계량기별 사용전력량 2", [head, ["김해 제1공장", "전력계 B", "1,200"]])])


record = {"source": str(SRC), "source_sha256": hashlib.sha256(SRC.read_bytes()).hexdigest(), "label": LABEL, "variants": []}
jobs = [lambda: r6_elec("-", "a"), lambda: r6_elec("", "b"), lambda: r6_gas("-", "c"), lambda: r6_gas("", "d"),
        r6_bm, r7_period, r7_meter]
for fn in jobs:
    path, info = fn()
    record["variants"].append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), **info})
(OUT / "variants.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps([v["file"] for v in record["variants"]], ensure_ascii=False))
