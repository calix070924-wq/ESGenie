"""ResponseSheet → PDF exporter 단위 테스트.

증명 목표: excel와 동일한 산출물(응답표·증빙 체크리스트·보완검토)을 PDF로 내며,
한글이 깨지지 않게 임베드되고(번들 NotoSansKR), 빈 입력에서도 안전하다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from esgenie.ssot.audit_trace import EvidenceLink
from esgenie.supplychain import (
    build_response_sheet,
    export_response_sheet_pdf,
)
from esgenie.supplychain.exporters._fonts import resolve_korean_font
from esgenie.supplychain.schema import Answer, ResponseSheet


def _full_sheet() -> ResponseSheet:
    """6가지 status를 모두 포함한 응답서(렌더 경로 전수 점검)."""
    ev = EvidenceLink(
        file_name="01_전기요금청구서.pdf", relative_path="set/01.pdf",
        origin="ocr", bbox=[0.084, 0.234, 0.306, 0.247], page=0, node_id="n1",
    )
    answers = [
        Answer("E-4-1", "환경", "연간 전력 사용량(kWh)", 128400.0, "verified", [ev], [], "D1 통과", []),
        Answer("E-6-2", "환경", "폐기물 재활용률(%)", 29.3, "flagged", [ev],
               ["자가신고 92% vs 증빙 29.3%"], "괴리", []),
        Answer("S-5-1", "사회", "인권정책 보유", None, "insufficient", [], [],
               "인권정책서 업로드 시 해소", ["인권정책서", "인권헌장"]),
        Answer("G-4-1", "지배구조", "윤리경영 서술", None, "hitl_required", [], [],
               "담당자 작성 필요", ["윤리강령 운영현황 서술"]),
        Answer("S-7-1", "사회", "지역사회 공헌", None, "not_applicable", [], [], "해당 없음", []),
        Answer("P-1-1", "정보공시", "공시 방식", "웹사이트", "self_reported", [], [], "자가신고", []),
    ]
    return ResponseSheet(
        "kesg28", "K-ESG 자가진단(SME 28)", "한울정밀공업㈜", answers,
        gaps=["[검토] 폐기물 재활용률 불일치", "[작성] 윤리경영 서술 필요"],
    )


def _is_pdf(path: str) -> bool:
    with open(path, "rb") as f:
        return f.read(5) == b"%PDF-"


# ── 1. 전 status 응답서 → 유효한 PDF 생성 ──
def test_export_full_sheet_pdf(tmp_path: Path):
    path = export_response_sheet_pdf(_full_sheet(), tmp_path)
    assert Path(path).exists()
    assert path.endswith(".pdf")
    assert _is_pdf(path)
    assert Path(path).stat().st_size > 5000  # 표·체크리스트 포함 → 비자명한 크기


# ── 2. 빈 입력에서도 예외 없이 생성(회귀 가드) ──
def test_export_empty_sheet_is_safe(tmp_path: Path):
    empty = ResponseSheet("saq5_env", "SAQ 5.0 환경", "빈회사", [], [])
    path = export_response_sheet_pdf(empty, tmp_path)
    assert _is_pdf(path)


# ── 3. 한글 폰트가 실제로 임베드된다(번들 NotoSansKR) ──
def test_korean_font_is_embedded():
    font = resolve_korean_font()
    assert font.embedded is True
    assert font.regular != "Helvetica"  # 폴백이 아니라 한글 폰트


# ── 4. build_response_sheet 산출물도 그대로 PDF로 나간다(엔드투엔드) ──
def test_pdf_from_build_response_sheet(tmp_path: Path):
    sheet = build_response_sheet("saq5", corp_name="한울정밀")
    path = export_response_sheet_pdf(sheet, tmp_path)
    assert _is_pdf(path)


# ── 5. PDF 본문에 한글·배지·체크리스트 텍스트가 실제로 박힌다 ──
def test_pdf_contains_korean_text(tmp_path: Path):
    fitz = pytest.importorskip("fitz")
    path = export_response_sheet_pdf(_full_sheet(), tmp_path)
    doc = fitz.open(path)
    text = "".join(doc.load_page(i).get_text() for i in range(doc.page_count))
    doc.close()
    for kw in ("한울정밀공업", "폐기물 재활용률", "검토필요", "증빙 체크리스트", "담당자 작성"):
        assert kw in text, f"PDF 본문에 '{kw}' 없음"
    # 이모지 대신 텍스트 라벨 — 이모지가 본문에 없어야(폰트에 글리프 없음)
    assert "🚩" not in text and "✅" not in text


# ── 6. 파일명은 양식키·기업명을 반영 ──
def test_pdf_filename(tmp_path: Path):
    path = export_response_sheet_pdf(_full_sheet(), tmp_path)
    name = Path(path).name
    assert name.startswith("공시응답서_kesg28_")  # kesg28 은 disclosure → 공시응답서 prefix
    assert name.endswith(".pdf")
