"""R8-3 후속 변형 입력 생성 — 검증용 가상 변형본(원본 아님). 원본 증빙·review-r3~r5 변형본은 건드리지 않는다.

형식은 review-r5 R8l~o(표선 + NotoSansKR 12pt)를 따른다 — 이 형식에서 실제 OCR이 숫자를 정상 인식했다.
항목명이 길어 첫 열만 넓힌다(190pt, 나머지 115pt — 글자가 옆 칸에 겹치지 않게).
R8p: 혼합 표 항목 '예상 사용량 및 요금' — 물리량 자체가 예상(계획) → 실적 답변 없음.
R8q: 혼합 표 항목 '계획 사용량 및 요금' — 같은 숫자·구조, 계획.
R8r: 혼합 표 두 행 '당월 사용량 및 요금'(실적) + '예상 사용량 및 요금'(8,800 · 377,000 · 258,000, 계획) → 실적 행만 답변.
'납부예정금액'(납부 시점만, 실적) 짝은 review-r5 R8o 원시 응답을 재생한다(과거 응답 — 새 실호출 아님).
사용: make_variants.py <out_dir> <NotoSansKR-Regular.ttf>"""
import sys
from pathlib import Path

import fitz

out, ttf = Path(sys.argv[1]), sys.argv[2]
HEAD = ["검증용 가상 변형본 — 원본 아님", "도시가스 요금 청구서", "사용 기간: 2026-04-01 ~ 2026-04-30"]
MIX = ["항목", "사용량(m3)", "사용열량(MJ)", "요금(원)"]
VARIANTS = (
    ("R8p_가스_혼합표_예상사용량및요금행.pdf", [MIX, ["예상 사용량 및 요금", "8,420", "360,772", "247,500"]]),
    ("R8q_가스_혼합표_계획사용량및요금행.pdf", [MIX, ["계획 사용량 및 요금", "8,420", "360,772", "247,500"]]),
    ("R8r_가스_혼합표_당월실적행_예상행.pdf", [MIX, ["당월 사용량 및 요금", "8,420", "360,772", "247,500"],
                                         ["예상 사용량 및 요금", "8,800", "377,000", "258,000"]]),
)
WIDTHS = [190, 115, 115, 115]
kw = {"fontname": "notokr", "fontfile": ttf}
for name, rows in VARIANTS:
    if (out / name).exists():
        continue                  # 이미 OCR에 보낸 입력은 다시 만들지 않는다(바이트가 달라진다)
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    for i, line in enumerate(HEAD):
        page.insert_text((60, 70 + i * 24), line, fontsize=12, **kw)
    y0, h = 150, 30
    for r, row in enumerate(rows):
        x = 30
        for c, text in enumerate(row):
            rect = fitz.Rect(x, y0 + r * h, x + WIDTHS[c], y0 + (r + 1) * h)
            page.draw_rect(rect, color=(0, 0, 0), width=0.8)
            page.insert_text((rect.x0 + 10, rect.y0 + 20), text, fontsize=12, **kw)
            x += WIDTHS[c]
    doc.save(str(out / name))
    doc.close()
    print(name)
