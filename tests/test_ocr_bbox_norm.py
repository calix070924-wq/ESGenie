"""Upstage Document Parse 응답 → bbox [0,1] 정규화 + page 인덱스 + 표 HTML 파싱 검증 (HTTP 모킹)."""
from __future__ import annotations

from esgenie.ssot import ocr_router


class _Resp:
    def __init__(self, *, json_body=None):
        self._json = json_body or {}

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


def _patch_post(monkeypatch, body):
    captured: dict = {}

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["data"] = data
        captured["files"] = files
        return _Resp(json_body=body)

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    return captured


def test_upstage_bbox_from_points_and_page(monkeypatch, tmp_path):
    monkeypatch.setenv("UPSTAGE_API_KEY", "k")
    # coordinates는 이미 페이지 기준 0~1 정규화된 네 꼭짓점.
    body = {
        "content": {"text": "사용전력량 142,560 kWh"},
        "elements": [
            {
                "id": 0,
                "category": "paragraph",
                "page": 1,
                "content": {"text": "사용전력량 142,560 kWh"},
                "coordinates": [
                    {"x": 0.1, "y": 0.1},
                    {"x": 0.5, "y": 0.1},
                    {"x": 0.5, "y": 0.2},
                    {"x": 0.1, "y": 0.2},
                ],
            }
        ],
    }
    captured = _patch_post(monkeypatch, body)

    f = tmp_path / "bill.pdf"
    f.write_bytes(b"%PDF-1.4 fake")
    tokens = ocr_router._call_upstage_dp(str(f))

    assert len(tokens) == 1
    t = tokens[0]
    assert t["page"] == 0                       # page 1 → 0-기준
    bb = t["bbox"]
    assert bb is not None
    assert abs(bb[0] - 0.1) < 1e-6 and abs(bb[1] - 0.1) < 1e-6
    assert abs(bb[2] - 0.5) < 1e-6 and abs(bb[3] - 0.2) < 1e-6
    assert all(0.0 <= v <= 1.0 for v in bb)
    # multipart 전송 + Bearer 인증 확인
    assert captured["headers"]["Authorization"] == "Bearer k"
    assert "document" in captured["files"]
    assert captured["data"]["model"] == "document-parse"


def test_upstage_table_html_parsed_to_cells(monkeypatch, tmp_path):
    monkeypatch.setenv("UPSTAGE_API_KEY", "k")
    html = (
        "<table>"
        "<tr><th>항목</th><th>값</th></tr>"
        "<tr><td>전력</td><td>100</td></tr>"
        "<tr><td>합계</td><td>180</td></tr>"
        "</table>"
    )
    body = {
        "content": {"text": "..."},
        "elements": [
            {
                "id": 0,
                "category": "table",
                "page": 1,
                "content": {"text": "항목 값 전력 100 합계 180", "html": html},
                "coordinates": [
                    {"x": 0.1, "y": 0.1},
                    {"x": 0.9, "y": 0.1},
                    {"x": 0.9, "y": 0.6},
                    {"x": 0.1, "y": 0.6},
                ],
            }
        ],
    }
    _patch_post(monkeypatch, body)

    f = tmp_path / "table.pdf"
    f.write_bytes(b"%PDF-1.4 fake")
    payload = ocr_router._call_upstage_dp_payload(str(f), ocr_mode="force")

    assert len(payload["tables"]) == 1
    table = payload["tables"][0]
    assert table.table_id == "upstage_table_0"
    assert table.source == "upstage_dp"
    assert table.row_count == 3 and table.column_count == 2
    # 헤더 셀 kind
    header = next(c for c in table.cells if c.row_index == 0 and c.column_index == 0)
    assert header.kind == "columnHeader"
    assert header.content == "항목"
    # 표 전체 외접 bbox가 셀에 공유되고 page는 0-기준
    assert table.cells[0].page == 0
    assert table.cells[0].bbox is not None
    # Upstage는 셀 confidence를 안 주므로 None (게이트 C1/C2는 스킵)
    assert table.cells[0].confidence is None


def test_html_table_handles_rowspan_colspan():
    html = (
        "<table>"
        "<tr><td rowspan='2'>A</td><td>B</td><td>C</td></tr>"
        "<tr><td colspan='2'>D</td></tr>"
        "</table>"
    )
    table = ocr_router._parse_html_table(html, table_id="t", page=0, bbox=None)
    assert table is not None
    # A는 (0,0) rowspan=2, B(0,1) C(0,2), 둘째 행 D는 점유격자상 (1,1) colspan=2
    by_pos = {(c.row_index, c.column_index): c for c in table.cells}
    assert by_pos[(0, 0)].content == "A" and by_pos[(0, 0)].row_span == 2
    assert by_pos[(0, 1)].content == "B"
    assert by_pos[(1, 1)].content == "D" and by_pos[(1, 1)].column_span == 2
    assert table.row_count == 2 and table.column_count == 3
