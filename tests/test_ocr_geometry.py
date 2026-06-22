"""OCR 좌표 배선 — pymupdf 줄 토큰 bbox + LLM 정규화 후 bbox 재결합."""
from __future__ import annotations

import os

import pytest

fitz = pytest.importorskip("fitz")

from esgenie.ssot import ocr_router
from esgenie.ssot.ocr_router import (
    ExtractedMetric,
    _attach_geometry,
    _pin_rates_from_raw,
    _pymupdf_line_tokens,
)

_KEPCO = os.path.join(os.path.dirname(__file__), "..", "data", "test_docs",
                      "kepco_bill_2025_12.pdf")


def _make_pdf(path):
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((72, 200), "사용전력량(kWh): 128,400", fontsize=14)
    doc.save(str(path))
    doc.close()
    return str(path)


def _force_pymupdf(monkeypatch):
    """모든 OCR/LLM 키 게터를 비활성화 → pymupdf+규칙 경로 확정."""
    monkeypatch.setattr(ocr_router, "_get_upstage_key", lambda: None)
    monkeypatch.setattr(ocr_router, "_get_openai_key", lambda: None)
    monkeypatch.setattr(ocr_router, "_get_anthropic_key", lambda: None)


def test_pymupdf_line_tokens_have_normalized_bbox(tmp_path):
    pdf = _make_pdf(tmp_path / "bill.pdf")
    toks = _pymupdf_line_tokens(pdf)
    assert toks, "줄 토큰이 추출돼야 함"
    t = toks[0]
    assert t["page"] == 0
    assert t["bbox"] is not None
    assert all(0.0 <= v <= 1.0 for v in t["bbox"])     # [0,1] 정규화


def test_pymupdf_path_metric_carries_bbox(monkeypatch):
    if not os.path.exists(_KEPCO):
        pytest.skip("샘플 kepco PDF 없음")
    _force_pymupdf(monkeypatch)
    ext = ocr_router.extract_structured(_KEPCO, doc_type="kepco_bill")
    hit = [m for m in ext.metrics if abs(m.value - 128400.0) < 1e-6]
    assert hit, "사용전력량 추출 실패"
    assert hit[0].bbox is not None and hit[0].page == 0
    assert all(0.0 <= v <= 1.0 for v in hit[0].bbox)


def test_attach_geometry_by_metric_hint():
    kv = {"사용전력량": {"value": 128400.0, "bbox": [0.1, 0.2, 0.3, 0.25], "page": 0}}
    m = ExtractedMetric(metric_hint="사용전력량", value=128400.0, unit="kWh", period="")
    _attach_geometry([m], kv)
    assert m.bbox == [0.1, 0.2, 0.3, 0.25] and m.page == 0


def test_attach_geometry_by_value_fallback():
    kv = {"전력": {"value": 128400.0, "bbox": [0.1, 0.2, 0.3, 0.25], "page": 1}}
    m = ExtractedMetric(metric_hint="energy_use", value=128400.0, unit="kWh", period="")
    _attach_geometry([m], kv)
    assert m.bbox == [0.1, 0.2, 0.3, 0.25] and m.page == 1


def test_attach_geometry_keeps_existing():
    kv = {"x": {"value": 5.0, "bbox": [0.9, 0.9, 0.95, 0.95], "page": 2}}
    m = ExtractedMetric(metric_hint="x", value=5.0, unit="", period="",
                        bbox=[0.1, 0.1, 0.2, 0.2], page=0)
    _attach_geometry([m], kv)
    assert m.bbox == [0.1, 0.1, 0.2, 0.2]   # 이미 있으면 덮어쓰지 않음


def test_pin_rates_from_raw_overrides_existing_e62_metric():
    metrics = [
        ExtractedMetric(
            metric_hint="재활용량",
            value=5400.0,
            unit="ton",
            period="",
            kesg_code_guess="E-6-2",
        )
    ]
    tokens = [
        {"text": "재활용 비율", "bbox": [0.1, 0.2, 0.2, 0.25], "page": 0},
        {"text": "29.3 %", "bbox": [0.3, 0.2, 0.35, 0.25], "page": 0},
    ]

    pinned = _pin_rates_from_raw(metrics, tokens)
    hits = [m for m in pinned if m.kesg_code_guess == "E-6-2"]

    assert len(hits) == 1
    assert hits[0].value == 29.3
    assert hits[0].unit == "%"
    assert hits[0].bbox == [0.3, 0.2, 0.35, 0.25]
