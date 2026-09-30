"""R8-3 변형 입력 생성 — 검증용 가상 변형본(원본 아님). 원본 증빙·review-r3·r4 변형본은 건드리지 않는다.

형식은 review-r4 R8g~k(표선 + BM 세트와 같은 NotoSansKR 12pt)를 따른다 — 이 형식에서 실제 OCR이 숫자를 정상 인식했다.
R8l·R8m: 2칸 표 + 표 아래 행 '납부예정금액 | 247,500' — 정상 열량 / 열량 칸 '-'(검토자 최소 재현).
R8n:     2칸 표, 열량 칸 '검침 예정'(실제 부재 표시) + 표 아래 '청구예정금액 | 247,500'.
R8o:     혼합 표(항목 | 사용량(m3) | 사용열량(MJ) | 요금(원)), 항목 '납부예정금액' — 정상 열량 보존.
사용: make_variants.py <out_dir> <NotoSansKR-Regular.ttf>"""
import sys
from pathlib import Path

import fitz

out, ttf = Path(sys.argv[1]), sys.argv[2]
HEAD = ["검증용 가상 변형본 — 원본 아님", "도시가스 요금 청구서", "사용 기간: 2026-04-01 ~ 2026-04-30"]
MIX = ["항목", "사용량(m3)", "사용열량(MJ)", "요금(원)"]
TWO = ["사용량(m3)", "사용열량(MJ)"]
VARIANTS = (
    ("R8l_가스_정상열량_아래납부예정금액.pdf", [TWO, ["8,420", "360,772"], ["납부예정금액", "247,500"]], 200),
    ("R8m_가스_열량칸대시_아래납부예정금액.pdf", [TWO, ["8,420", "-"], ["납부예정금액", "247,500"]], 200),
    ("R8n_가스_열량칸검침예정_아래청구예정금액.pdf", [TWO, ["8,420", "검침 예정"], ["청구예정금액", "247,500"]], 200),
    ("R8o_가스_혼합표_납부예정금액행_정상열량.pdf", [MIX, ["납부예정금액", "8,420", "360,772", "247,500"]], 130),
)
kw = {"fontname": "notokr", "fontfile": ttf}
for name, rows, w in VARIANTS:
    if (out / name).exists():
        continue                  # 이미 OCR에 보낸 입력은 다시 만들지 않는다(바이트가 달라진다)
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    for i, line in enumerate(HEAD):
        page.insert_text((60, 70 + i * 24), line, fontsize=12, **kw)
    x0, y0, h = 50, 150, 30
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            rect = fitz.Rect(x0 + c * w, y0 + r * h, x0 + (c + 1) * w, y0 + (r + 1) * h)
            page.draw_rect(rect, color=(0, 0, 0), width=0.8)
            page.insert_text((rect.x0 + 10, rect.y0 + 20), text, fontsize=12, **kw)
    doc.save(str(out / name))
    doc.close()
    print(name)
