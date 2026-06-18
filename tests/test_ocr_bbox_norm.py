"""Azure Document Intelligence 응답 → bbox [0,1] 정규화 + page 인덱스 검증 (HTTP 모킹)."""
from __future__ import annotations

import types

from esgenie.ssot import ocr_router


class _Resp:
    def __init__(self, *, headers=None, json_body=None):
        self.headers = headers or {}
        self._json = json_body or {}

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


def test_azure_bbox_normalized_and_page(monkeypatch, tmp_path):
    monkeypatch.setenv("AZURE_DOC_INTEL_KEY", "k")
    monkeypatch.setenv("AZURE_DOC_INTEL_ENDPOINT", "https://x.cognitiveservices.azure.com")
    monkeypatch.setattr(ocr_router.time if hasattr(ocr_router, "time") else __import__("time"),
                        "sleep", lambda *_: None, raising=False)

    # 페이지 폭 8.5 inch, 높이 11 inch. 한 줄의 polygon(인치) → 정규화 기대.
    body = {"status": "succeeded", "analyzeResult": {"pages": [{
        "pageNumber": 1, "width": 8.5, "height": 11.0, "unit": "inch",
        "lines": [{"content": "사용전력량 7,150,000 kWh",
                   "polygon": [0.85, 1.1, 4.25, 1.1, 4.25, 2.2, 0.85, 2.2]}],
    }]}}

    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda *_: None)

    def fake_post(url, headers=None, data=None, timeout=None):
        return _Resp(headers={"Operation-Location": "https://x/op/1"})

    def fake_get(url, headers=None, timeout=None):
        return _Resp(json_body=body)

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)

    f = tmp_path / "bill.pdf"
    f.write_bytes(b"%PDF-1.4 fake")
    tokens = ocr_router._call_azure_docintel(str(f))

    assert len(tokens) == 1
    t = tokens[0]
    assert t["page"] == 0                       # pageNumber 1 → 0-기준
    bb = t["bbox"]
    assert bb is not None
    # x0=0.85/8.5=0.1, y0=1.1/11=0.1, x1=4.25/8.5=0.5, y1=2.2/11=0.2
    assert abs(bb[0] - 0.1) < 1e-6 and abs(bb[1] - 0.1) < 1e-6
    assert abs(bb[2] - 0.5) < 1e-6 and abs(bb[3] - 0.2) < 1e-6
    assert all(0.0 <= v <= 1.0 for v in bb)
