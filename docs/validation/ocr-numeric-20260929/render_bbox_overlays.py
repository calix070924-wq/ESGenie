"""수정 전(2026-09-28 리허설)·수정 후(2026-09-29 실제 코어 실행) 수치의 bbox를 원본 PDF 위에 그린다.

빨강 = 수정 전 결과, 초록 = 수정 후 결과. 원본 PDF와 과거 결과는 읽기만 한다.
사용: python render_bbox_overlays.py <before_pipeline.json> <after_pipeline.json> <pack_dir> <out_dir>
"""
import json
import sys
from pathlib import Path

import fitz
from PIL import Image, ImageDraw, ImageFont

DOCS = ("02_", "03_", "04_", "06_", "07_")
FONT = "/System/Library/Fonts/AppleSDGothicNeo.ttc"


def metrics(path: Path) -> dict:
    p = json.loads(path.read_text(encoding="utf-8"))
    return {x["source_file"]: x.get("metrics", []) for x in p["ocr_extractions"] if x["source_file"].startswith(DOCS)}


def main(before: Path, after: Path, pack: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    b, a = metrics(before), metrics(after)
    font = ImageFont.truetype(FONT, 18)
    for name in sorted(set(b) | set(a)):
        pdf = next(pack.glob(f"*/{name}"))
        doc = fitz.open(pdf)
        pix = doc[0].get_pixmap(dpi=110)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        banner = 30 * (2 + len(b.get(name, [])) + len(a.get(name, [])))
        canvas = Image.new("RGB", (img.width, img.height + banner), "white")
        canvas.paste(img, (0, banner))
        draw = ImageDraw.Draw(canvas)
        y = 6
        for tag, color, items in (("수정 전", (210, 30, 30), b.get(name, [])), ("수정 후", (20, 150, 60), a.get(name, []))):
            if not items:
                draw.text((10, y), f"{tag}: 추출 값 없음", fill=color, font=font)
                y += 30
            for m in items:
                prec = (m.get("source_detail") or {}).get("precision") or "-"
                label = f"{tag}: {m.get('metric_hint')} = {m.get('value'):g} {m.get('unit')} " \
                        f"[{m.get('kesg_code_guess') or '코드 없음'}] 위치 정밀도={prec if m.get('bbox') else 'bbox 없음'}"
                draw.text((10, y), label, fill=color, font=font)
                y += 30
                if m.get("bbox") and (m.get("page") or 0) == 0:  # 1쪽 문서 — page 누락은 0쪽
                    x0, y0, x1, y1 = m["bbox"]
                    box = (x0 * img.width - 3, y0 * img.height + banner - 3, x1 * img.width + 3, y1 * img.height + banner + 3)
                    draw.rectangle(box, outline=color, width=3)
        canvas.save(out / f"{name[:2]}_bbox_before_after.png")
        print(out / f"{name[:2]}_bbox_before_after.png")


if __name__ == "__main__":
    main(*(Path(v) for v in sys.argv[1:5]))
