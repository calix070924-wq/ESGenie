"""증빙 bbox 좌표계 정합 회귀 테스트.

ocr_router가 저장하는 정규화 bbox(top-left 원점, Y 하향)와 pdf_render의 역변환
(norm × image_size)이 같은 좌표계를 쓰는지 *실증*한다. 단순히 "박스 색이 그려졌나"가
아니라 "박스가 그 텍스트 *위에* 찍혔나"를 픽셀로 확인 → Y축 뒤집힘(flip) 회귀 차단.

PDF/Azure/PyMuPDF 모두 top-left 원점·Y하향이므로 렌더도 뒤집지 않아야 한다.
이 가정이 깨지면(예: 누가 1-y 로 '고치면') 이 테스트가 즉시 실패한다.
"""
from __future__ import annotations

import io

import pytest

fitz = pytest.importorskip("fitz")
pytest.importorskip("PIL")

from esgenie.pdf_render import render_page_with_box


def _norm_line_bboxes(pdf_path: str, page: int = 0):
    """ocr_router PyMuPDF 경로와 동일하게 줄 bbox를 [0,1] 정규화해서 반환."""
    with fitz.open(pdf_path) as doc:
        pg = doc.load_page(page)
        pw, ph = pg.rect.width, pg.rect.height
        out = []
        for block in pg.get_text("dict")["blocks"]:
            for ln in block.get("lines", []):
                txt = "".join(s["text"] for s in ln["spans"])
                x0, y0, x1, y1 = ln["bbox"]
                out.append((txt, [x0 / pw, y0 / ph, x1 / pw, y1 / ph]))
    return out


def _amber_bbox_pixels(png: bytes):
    """렌더 PNG에서 amber(186,117,23) 박스 픽셀의 (xs, ys, w, h)."""
    from PIL import Image

    img = Image.open(io.BytesIO(png)).convert("RGB")
    px = img.load()
    xs, ys = [], []
    for y in range(img.height):
        for x in range(img.width):
            r, g, b = px[x, y]
            if abs(r - 186) < 40 and abs(g - 117) < 40 and abs(b - 23) < 40:
                xs.append(x)
                ys.append(y)
    return xs, ys, img.width, img.height


@pytest.fixture
def marker_pdf(tmp_path):
    """상단·하단에 마커 텍스트를 박은 합성 PDF (위치를 알고 있는 fixture)."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((72, 60), "TOP-MARKER 7150000 kWh", fontsize=14)    # y≈상단 7.5%
    page.insert_text((72, 740), "BOTTOM-MARKER footer", fontsize=14)     # y≈하단 92%
    path = tmp_path / "marker.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


def test_box_lands_on_top_text_not_flipped(marker_pdf):
    """상단 텍스트 bbox → 박스가 이미지 *상단*에 찍혀야 한다 (Y-flip 회귀 차단)."""
    lines = _norm_line_bboxes(marker_pdf)
    txt, bb = next((t, b) for t, b in lines if "TOP" in t)
    assert bb[1] < 0.2, f"fixture 가정 깨짐: 상단 텍스트 y0={bb[1]:.3f}"

    png = render_page_with_box(marker_pdf, bb, page=0, dpi=100)
    xs, ys, w, h = _amber_bbox_pixels(png)
    assert ys, "박스가 그려지지 않음"
    # 상단 텍스트 → 박스는 이미지 위쪽 40% 안에 있어야 함 (뒤집혔다면 아래쪽에 찍힘)
    assert max(ys) < h * 0.4, f"상단 텍스트인데 박스가 아래쪽({max(ys)}/{h})에 찍힘 → Y축 뒤집힘"


def test_box_lands_on_bottom_text(marker_pdf):
    """하단 텍스트 bbox → 박스가 이미지 *하단*에 찍혀야 한다."""
    lines = _norm_line_bboxes(marker_pdf)
    txt, bb = next((t, b) for t, b in lines if "BOTTOM" in t)
    assert bb[1] > 0.8, f"fixture 가정 깨짐: 하단 텍스트 y0={bb[1]:.3f}"

    png = render_page_with_box(marker_pdf, bb, page=0, dpi=100)
    xs, ys, w, h = _amber_bbox_pixels(png)
    assert ys, "박스가 그려지지 않음"
    assert min(ys) > h * 0.6, f"하단 텍스트인데 박스가 위쪽({min(ys)}/{h})에 찍힘 → Y축 뒤집힘"


def test_box_pixel_matches_expected_norm(marker_pdf):
    """박스 픽셀 범위가 norm×image_size 기댓값과 ±2% 안에서 일치(원점·축 동일 증명)."""
    lines = _norm_line_bboxes(marker_pdf)
    _, bb = next((t, b) for t, b in lines if "TOP" in t)

    png = render_page_with_box(marker_pdf, bb, page=0, dpi=100)
    xs, ys, w, h = _amber_bbox_pixels(png)

    exp_y0, exp_y1 = bb[1] * h, bb[3] * h
    exp_x0, exp_x1 = bb[0] * w, bb[2] * w
    tol_y, tol_x = h * 0.02, w * 0.02
    assert abs(min(ys) - exp_y0) < tol_y, f"y0 불일치: 실제 {min(ys)} vs 기대 {exp_y0:.0f}"
    assert abs(max(ys) - exp_y1) < tol_y, f"y1 불일치: 실제 {max(ys)} vs 기대 {exp_y1:.0f}"
    assert abs(min(xs) - exp_x0) < tol_x, f"x0 불일치: 실제 {min(xs)} vs 기대 {exp_x0:.0f}"
    assert abs(max(xs) - exp_x1) < tol_x, f"x1 불일치: 실제 {max(xs)} vs 기대 {exp_x1:.0f}"
