"""증빙 부록(원본 페이지 + bbox) 임베드 테스트.

exporters/pdf.py가 EvidenceLink.relative_path를 resolve해 원본 페이지를 부록에
임베드하고, 표 근거에 [E#] 상호참조를 달며, 파일이 없으면 *조용히 스킵*하고
본문은 정상 생성하는지(누락 방어) 검증한다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")

from esgenie.ssot.audit_trace import EvidenceLink
from esgenie.supplychain.exporters.pdf import export_response_sheet_pdf
from esgenie.supplychain.schema import Answer, ResponseSheet


def _make_evidence_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((72, 60), "EVIDENCE 사용전력량(kWh): 128,400", fontsize=14)
    doc.save(str(path))
    doc.close()


def _sheet_with_link(rel: str) -> ResponseSheet:
    ev = EvidenceLink(
        file_name="01_전기요금청구서.pdf", relative_path=rel,
        origin="ocr", bbox=[0.10, 0.05, 0.80, 0.10], page=0, node_id="n1",
    )
    answers = [
        Answer("E-4-1", "환경", "연간 전력 사용량(kWh)", 128400.0,
               "verified", [ev], [], "D1 통과", []),
    ]
    return ResponseSheet("kesg28", "K-ESG 자가진단", "한울정밀공업㈜", answers, gaps=[])


def _pdf_text(path: str) -> tuple[str, int]:
    doc = fitz.open(path)
    txt = "".join(doc.load_page(i).get_text() for i in range(doc.page_count))
    n = doc.page_count
    doc.close()
    return txt, n


def test_appendix_embeds_original_page(tmp_path: Path):
    """원본 PDF가 있으면 부록 페이지가 추가되고 표에 [E1] 상호참조가 붙는다."""
    _make_evidence_pdf(tmp_path / "evidence_pack" / "01.pdf")
    sheet = _sheet_with_link("evidence_pack/01.pdf")

    path = export_response_sheet_pdf(sheet, tmp_path, evidence_base_dir=tmp_path)
    text, n_pages = _pdf_text(path)

    assert "증빙 부록" in text, "부록 섹션이 없음"
    assert "[E1]" in text, "표/부록에 figure 상호참조 [E1]가 없음"
    assert n_pages >= 2, "부록 페이지(PageBreak)가 추가되지 않음"


def test_appendix_box_is_drawn_on_page(tmp_path: Path):
    """부록 페이지에 amber bbox 박스 픽셀이 실제로 존재한다."""
    from PIL import Image
    import io

    _make_evidence_pdf(tmp_path / "evidence_pack" / "01.pdf")
    sheet = _sheet_with_link("evidence_pack/01.pdf")
    path = export_response_sheet_pdf(sheet, tmp_path, evidence_base_dir=tmp_path)

    doc = fitz.open(path)
    found = False
    for i in range(doc.page_count):
        png = doc.load_page(i).get_pixmap(dpi=80).tobytes("png")
        img = Image.open(io.BytesIO(png)).convert("RGB")
        px = img.load()
        for y in range(0, img.height, 2):
            for x in range(0, img.width, 2):
                r, g, b = px[x, y]
                if abs(r - 186) < 45 and abs(g - 117) < 45 and abs(b - 23) < 45:
                    found = True
                    break
            if found:
                break
        if found:
            break
    doc.close()
    assert found, "부록에서 amber bbox 박스를 찾지 못함"


def test_missing_evidence_file_is_graceful(tmp_path: Path):
    """원본 파일이 없으면 부록 없이도 본문 PDF는 정상 생성되고 [E1] 태그도 안 단다."""
    sheet = _sheet_with_link("evidence_pack/없는파일.pdf")  # 파일 미존재
    path = export_response_sheet_pdf(sheet, tmp_path, evidence_base_dir=tmp_path)

    text, n_pages = _pdf_text(path)
    assert Path(path).exists()
    assert "연간 전력 사용량" in text          # 본문은 정상
    assert "증빙 부록" not in text             # 부록 섹션 없음
    assert "[E1]" not in text                  # 상호참조도 없음


def test_embed_evidence_false_skips_appendix(tmp_path: Path):
    """embed_evidence=False면 파일이 있어도 부록을 생략한다."""
    _make_evidence_pdf(tmp_path / "evidence_pack" / "01.pdf")
    sheet = _sheet_with_link("evidence_pack/01.pdf")
    path = export_response_sheet_pdf(
        sheet, tmp_path, evidence_base_dir=tmp_path, embed_evidence=False)
    text, _ = _pdf_text(path)
    assert "증빙 부록" not in text
    assert "[E1]" not in text


# ── 부록 dedup 테스트 ──────────────────────────────────────────────────────

def test_appendix_dedup_same_file_same_page(tmp_path: Path):
    """같은 파일+같은 페이지를 가리키는 복수 링크가 하나의 [E#]로 통합된다."""
    _make_evidence_pdf(tmp_path / "evidence_pack" / "01.pdf")
    ev1 = EvidenceLink(
        file_name="01.pdf", relative_path="evidence_pack/01.pdf",
        origin="ocr", bbox=[0.10, 0.05, 0.50, 0.10], page=0, node_id="n1",
    )
    ev2 = EvidenceLink(
        file_name="01.pdf", relative_path="evidence_pack/01.pdf",
        origin="ocr", bbox=[0.20, 0.30, 0.80, 0.40], page=0, node_id="n2",
    )
    answers = [
        Answer("E-4-1", "환경", "전력 사용량(kWh)", 128400.0,
               "verified", [ev1], [], "D1 통과", []),
        Answer("E-3-1", "환경", "GHG 배출량(tCO2eq)", 45.2,
               "verified", [ev2], [], "D1 통과", []),
    ]
    sheet = ResponseSheet("kesg28", "K-ESG 자가진단", "한울정밀㈜", answers, gaps=[])
    path = export_response_sheet_pdf(sheet, tmp_path, evidence_base_dir=tmp_path)
    text, _ = _pdf_text(path)

    assert "[E1]" in text, "첫 번째 figure 참조가 있어야 함"
    assert "[E2]" not in text, "같은 파일+페이지는 중복 figure를 만들지 않아야 함"


def test_appendix_dedup_different_pages_get_separate_figures(tmp_path: Path):
    """같은 파일이라도 다른 페이지를 가리키면 별도 [E#]이다."""
    ev_path = tmp_path / "evidence_pack" / "multi.pdf"
    ev_path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    p1 = doc.new_page(width=600, height=800)
    p1.insert_text((72, 60), "PAGE 1", fontsize=14)
    p2 = doc.new_page(width=600, height=800)
    p2.insert_text((72, 60), "PAGE 2", fontsize=14)
    doc.save(str(ev_path))
    doc.close()

    ev1 = EvidenceLink(
        file_name="multi.pdf", relative_path="evidence_pack/multi.pdf",
        origin="ocr", bbox=[0.1, 0.05, 0.8, 0.1], page=0, node_id="n1",
    )
    ev2 = EvidenceLink(
        file_name="multi.pdf", relative_path="evidence_pack/multi.pdf",
        origin="ocr", bbox=[0.1, 0.05, 0.8, 0.1], page=1, node_id="n2",
    )
    answers = [
        Answer("E-4-1", "환경", "전력", 100.0, "verified", [ev1], [], "", []),
        Answer("E-3-1", "환경", "GHG", 50.0, "verified", [ev2], [], "", []),
    ]
    sheet = ResponseSheet("kesg28", "K-ESG", "Corp", answers, gaps=[])
    path = export_response_sheet_pdf(sheet, tmp_path, evidence_base_dir=tmp_path)
    text, _ = _pdf_text(path)

    assert "[E1]" in text
    assert "[E2]" in text, "다른 페이지는 별도 figure여야 함"
