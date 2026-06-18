"""원본 증빙 PDF를 이미지로 렌더하고, 정규화 bbox를 박스로 오버레이.

provenance(감사추적) 뷰의 phase-2 — "이 수치가 원본 문서 *여기* 있다"를 시각화.
PyMuPDF(fitz)로 페이지를 래스터화하고 PIL로 박스를 그린다. bbox는 [0,1] 정규화
좌표(ocr_router가 페이지 크기로 나눠 저장)이므로 픽셀 = 좌표 × 이미지 크기.
"""
from __future__ import annotations

import io
from typing import Optional

_BOX_RGB = (186, 117, 23)   # amber 600 — provenance 강조색과 통일


def page_count(pdf_path: str) -> int:
    import fitz
    with fitz.open(pdf_path) as doc:
        return doc.page_count


def render_page_png(pdf_path: str, page: int = 0, dpi: int = 120) -> bytes:
    """PDF 한 페이지 → PNG 바이트."""
    import fitz
    with fitz.open(pdf_path) as doc:
        idx = max(0, min(page or 0, doc.page_count - 1))
        pix = doc.load_page(idx).get_pixmap(dpi=dpi)
        return pix.tobytes("png")


def render_page_with_box(
    pdf_path: str,
    bbox_norm: Optional[list[float]],
    *,
    page: int = 0,
    dpi: int = 120,
    color: tuple[int, int, int] = _BOX_RGB,
) -> bytes:
    """페이지를 렌더하고 정규화 bbox([0,1] x0,y0,x1,y1)를 반투명 박스로 오버레이 → PNG.

    bbox_norm 이 None/형식오류면 박스 없이 페이지만 렌더.
    """
    from PIL import Image, ImageDraw

    png = render_page_png(pdf_path, page=page, dpi=dpi)
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    box = _pixel_box(bbox_norm, img.width, img.height)
    if box:
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)
        x0, y0, x1, y1 = box
        d.rectangle([x0, y0, x1, y1], fill=color + (60,), outline=color + (255,), width=3)
        img = Image.alpha_composite(img, overlay)
    out = io.BytesIO()
    img.convert("RGB").save(out, format="PNG")
    return out.getvalue()


def _pixel_box(bbox_norm: Optional[list[float]], w: int, h: int) -> Optional[tuple[int, int, int, int]]:
    """정규화 bbox → 픽셀 좌표(정렬·클램프). 유효하지 않으면 None."""
    if not bbox_norm or len(bbox_norm) != 4:
        return None
    try:
        xs = [float(bbox_norm[0]), float(bbox_norm[2])]
        ys = [float(bbox_norm[1]), float(bbox_norm[3])]
    except (TypeError, ValueError):
        return None
    # [0,1] 범위가 아니면(이미 픽셀 등) 안전하게 처리 불가 → None
    if not all(0.0 <= v <= 1.0 for v in xs + ys):
        return None
    x0 = int(max(0, min(w, round(min(xs) * w))))
    x1 = int(max(0, min(w, round(max(xs) * w))))
    y0 = int(max(0, min(h, round(min(ys) * h))))
    y1 = int(max(0, min(h, round(max(ys) * h))))
    if x1 <= x0:
        x1 = min(w, x0 + 1)
    if y1 <= y0:
        y1 = min(h, y0 + 1)
    return x0, y0, x1, y1
