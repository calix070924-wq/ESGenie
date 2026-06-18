"""PDF 페이지 렌더 + bbox 박스 오버레이 단위 테스트 (합성 PDF)."""
from __future__ import annotations

import io

import pytest

fitz = pytest.importorskip("fitz")
PIL = pytest.importorskip("PIL")

from esgenie.pdf_render import render_page_png, render_page_with_box, _pixel_box, page_count


def _make_pdf(path):
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((72, 120), "사용전력량(kWh): 7,150,000", fontsize=14)
    doc.save(str(path))
    doc.close()
    return str(path)


def test_render_page_png(tmp_path):
    pdf = _make_pdf(tmp_path / "ev.pdf")
    png = render_page_png(pdf, page=0, dpi=100)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"          # PNG 시그니처
    from PIL import Image
    img = Image.open(io.BytesIO(png))
    assert img.width > 100 and img.height > 100


def test_page_count(tmp_path):
    pdf = _make_pdf(tmp_path / "ev.pdf")
    assert page_count(pdf) == 1


def test_render_with_box_draws_color(tmp_path):
    from PIL import Image
    pdf = _make_pdf(tmp_path / "ev.pdf")
    png = render_page_with_box(pdf, [0.1, 0.1, 0.6, 0.25], page=0, dpi=100)
    img = Image.open(io.BytesIO(png)).convert("RGB")
    # 박스 테두리 근처에 amber(186,117,23) 계열 픽셀이 존재해야 함
    px = img.load()
    found = False
    for y in range(img.height):
        for x in range(img.width):
            r, g, b = px[x, y]
            if abs(r - 186) < 40 and abs(g - 117) < 40 and abs(b - 23) < 40:
                found = True
                break
        if found:
            break
    assert found, "박스 색이 그려지지 않음"


def test_render_with_box_none_bbox(tmp_path):
    pdf = _make_pdf(tmp_path / "ev.pdf")
    png = render_page_with_box(pdf, None, page=0, dpi=100)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"          # 박스 없이 페이지만


def test_pixel_box():
    assert _pixel_box([0.1, 0.2, 0.5, 0.6], 1000, 1000) == (100, 200, 500, 600)
    assert _pixel_box(None, 100, 100) is None
    assert _pixel_box([10, 20, 30, 40], 1000, 1000) is None   # 정규화 아님 → None
    assert _pixel_box([0.5, 0.5, 0.1, 0.1], 100, 100) == (10, 10, 50, 50)  # 역순 정렬
