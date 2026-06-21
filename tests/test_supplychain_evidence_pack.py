"""copy_evidence_pack — 참조 증빙 원본을 out_dir/evidence_pack 로 복사하고,
그 결과 PDF 증빙 부록이 실제로 채워지는지(엔드투엔드) 검증."""
from __future__ import annotations

from pathlib import Path

import pytest

from esgenie.ssot.audit_trace import EvidenceLink
from esgenie.supplychain import copy_evidence_pack, export_response_sheet_pdf
from esgenie.supplychain.schema import Answer, ResponseSheet


def _sheet(rel: str = "evidence_pack/01.pdf") -> ResponseSheet:
    ev = EvidenceLink(
        file_name="01.pdf", relative_path=rel,
        origin="ocr", bbox=[0.10, 0.05, 0.80, 0.10], page=0, node_id="n1",
    )
    answers = [Answer("E-4-1", "환경", "연간 전력 사용량(kWh)", 128400.0,
                      "verified", [ev], [], "D1 통과", [])]
    return ResponseSheet("kesg28", "K-ESG", "한울정밀공업㈜", answers, gaps=[])


def test_copies_referenced_original(tmp_path: Path):
    src = tmp_path / "src" / "01.pdf"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"%PDF-1.4 fake")
    out = tmp_path / "out"

    copied = copy_evidence_pack(_sheet(), out, {"01.pdf": str(src)})

    assert copied == ["01.pdf"]
    assert (out / "evidence_pack" / "01.pdf").is_file()


def test_no_uploaded_files_is_noop(tmp_path: Path):
    out = tmp_path / "out"
    assert copy_evidence_pack(_sheet(), out, {}) == []
    assert not (out / "evidence_pack").exists()   # 부수효과 없음


def test_unreferenced_files_not_copied(tmp_path: Path):
    src = tmp_path / "other.pdf"
    src.write_bytes(b"%PDF-1.4")
    out = tmp_path / "out"
    # 응답서가 참조하지 않는 파일만 업로드됨 → 복사 안 함
    assert copy_evidence_pack(_sheet(), out, {"other.pdf": str(src)}) == []


def test_missing_source_path_skipped(tmp_path: Path):
    out = tmp_path / "out"
    copied = copy_evidence_pack(_sheet(), out, {"01.pdf": str(tmp_path / "nope.pdf")})
    assert copied == []


def test_end_to_end_copy_then_appendix(tmp_path: Path):
    """copy → export 순서로 부록이 실제로 채워진다(라이브 경로 재현)."""
    fitz = pytest.importorskip("fitz")

    # 원본 증빙 PDF 생성(세션 업로드 자리)
    src = tmp_path / "uploads" / "01.pdf"
    src.parent.mkdir(parents=True)
    doc = fitz.open(); pg = doc.new_page(width=600, height=800)
    pg.insert_text((72, 60), "EVIDENCE 128,400 kWh", fontsize=14)
    doc.save(str(src)); doc.close()

    out = tmp_path / "out"
    sheet = _sheet()
    copy_evidence_pack(sheet, out, {"01.pdf": str(src)})        # ← 복사 배선
    path = export_response_sheet_pdf(sheet, out, evidence_base_dir=out)

    doc = fitz.open(path)
    text = "".join(doc.load_page(i).get_text() for i in range(doc.page_count))
    n = doc.page_count
    doc.close()
    assert "증빙 부록" in text and "[E1]" in text
    assert n >= 2
