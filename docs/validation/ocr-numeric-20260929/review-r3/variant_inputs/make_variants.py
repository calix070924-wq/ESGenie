"""R8 변형 입력 생성 — 검증용 가상 변형본(원본 아님). 원본 증빙은 건드리지 않는다.

R8a·R8b: 검토 파일 repro_fee.py와 같은 배치(글자만, 10pt). before/head_dfef4f7의 PDF와 바이트가 같다.
R8c·R8d: 같은 내용을 선이 있는 표(12pt)로 그린 판 — 실제 OCR이 표 객체로 읽는지 보기 위한 추가 변형.
R8e·R8f: R8c·R8d와 같은 표를 BM 세트와 같은 NotoSansKR(esgenie/assets/fonts)로 찍은 판 — 'korea' 내장
         글꼴에서 실제 OCR이 숫자를 한 글자씩 띄워 읽어('8 , 4 2 0') 수치 칸이 성립하지 않아 추가했다.
사용: make_variants.py <out_dir> [NotoSansKR-Regular.ttf]"""
import sys
from pathlib import Path

import fitz

out = Path(sys.argv[1])
HEAD = ["검증용 가상 변형본 — 원본 아님", "도시가스 요금 청구서", "사용 기간: 2026-04-01 ~ 2026-04-30"]
font_file = sys.argv[2] if len(sys.argv) > 2 else None
for name, heat, ttf in (("R8c_가스_표선_정상열량_아래기본요금.pdf", "360,772", None),
                        ("R8d_가스_표선_열량칸대시_아래기본요금.pdf", "-", None),
                        ("R8e_가스_표선NotoKR_정상열량_아래기본요금.pdf", "360,772", font_file),
                        ("R8f_가스_표선NotoKR_열량칸대시_아래기본요금.pdf", "-", font_file)):
    if (ttf is None and name[:3] in ("R8e", "R8f")) or (out / name).exists():
        continue                  # 이미 OCR에 보낸 입력은 다시 만들지 않는다(바이트가 달라진다)
    kw = {"fontname": "notokr", "fontfile": ttf} if ttf else {"fontname": "korea"}
    rows = [["사용량(m3)", "사용열량(MJ)"], ["8,420", heat], ["기본요금", "247,500"]]
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    for i, line in enumerate(HEAD):
        page.insert_text((60, 70 + i * 24), line, fontsize=12, **kw)
    x0, y0, w, h = 60, 150, 200, 30
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            rect = fitz.Rect(x0 + c * w, y0 + r * h, x0 + (c + 1) * w, y0 + (r + 1) * h)
            page.draw_rect(rect, color=(0, 0, 0), width=0.8)
            page.insert_text((rect.x0 + 10, rect.y0 + 20), text, fontsize=12, **kw)
    doc.save(str(out / name))
    doc.close()
    print(name)
