"""증빙 부록 dedup / bbox 필터 로컬 검증 스크립트.

사용법:
    python3 scripts/verify_evidence_dedup.py          # 더미 세트로 케이스 재현
    python3 scripts/verify_evidence_dedup.py /path/to/hanul/증빙세트
"""
import os
import re
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("ESGENIE_FORCE_MOCK", "1")

import fitz  # PyMuPDF

from esgenie.ssot.audit_trace import EvidenceLink
from esgenie.supplychain.exporters.pdf import export_response_sheet_pdf
from esgenie.supplychain.schema import Answer, ResponseSheet


def _make_pdf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    pg = doc.new_page(width=600, height=800)
    pg.insert_text((72, 60), text, fontsize=14)
    doc.save(str(path))
    doc.close()


def run_dummy(base: Path) -> str:
    """더미 증빙 세트로 dedup + bbox 케이스 재현."""
    _make_pdf(base / "evidence_pack" / "policy.pdf", "환경방침서")
    _make_pdf(base / "evidence_pack" / "electric.pdf", "전기요금청구서 128,400 kWh")

    # 같은 파일+페이지를 두 Answer에서 참조 → figure 1개만 나와야 함
    ev_dup1 = EvidenceLink(
        file_name="electric.pdf", relative_path="evidence_pack/electric.pdf",
        origin="ocr", bbox=[0.1, 0.05, 0.8, 0.12], page=0, node_id="n1",
    )
    ev_dup2 = EvidenceLink(
        file_name="electric.pdf", relative_path="evidence_pack/electric.pdf",
        origin="ocr", bbox=[0.1, 0.30, 0.8, 0.40], page=0, node_id="n2",
    )
    # bbox 없는 정성 문서 → figure 제외, 표 텍스트는 유지
    ev_no_bbox = EvidenceLink(
        file_name="환경방침서.pdf", relative_path="evidence_pack/policy.pdf",
        origin="dart", bbox=[], page=0, node_id="n3",
    )

    answers = [
        Answer("E-4-1", "환경", "연간 전력 사용량(kWh)", 128400.0,
               "verified", [ev_dup1], [], "D1 통과", []),
        Answer("E-6-1", "환경", "온실가스 배출량(tCO2eq)", 45.2,
               "verified", [ev_dup2], [], "D1 통과", []),
        Answer("E-1-1", "환경", "환경방침 보유", True,
               "self_reported", [ev_no_bbox], [], "자가신고", []),
    ]
    sheet = ResponseSheet("kesg28", "K-ESG 자가진단", "한울정밀공업㈜", answers, gaps=[])
    return export_response_sheet_pdf(sheet, base, evidence_base_dir=base)


def run_real(evidence_dir: Path, out_dir: Path) -> str:
    """실제 증빙 세트 사용. build_response_sheet로 ResponseSheet 생성 후 export."""
    from esgenie.supplychain import build_response_sheet
    sheet = build_response_sheet("kesg28", corp_name="한울정밀공업")
    return export_response_sheet_pdf(sheet, out_dir, evidence_base_dir=evidence_dir)


def check(pdf_path: str) -> None:
    doc = fitz.open(pdf_path)
    text = "".join(doc.load_page(i).get_text() for i in range(doc.page_count))
    n_pages = doc.page_count
    doc.close()

    figs = sorted(set(re.findall(r'\[E(\d+)\]', text)), key=int)
    fig_labels = ["E" + n for n in figs]

    print(f"\nPDF: {pdf_path}")
    print(f"  페이지 수 : {n_pages}")
    print(f"  figure 목록: {fig_labels if fig_labels else '없음'}")
    print()

    # dedup 확인: ev_dup1/ev_dup2는 같은 파일+페이지 → E1만 있어야 함
    if "증빙 부록" in text:
        print(f"  [OK] 부록 존재 — figure {len(figs)}개")
        if len(figs) == 1 and fig_labels == ["E1"]:
            print("  [OK] dedup — 같은 파일+페이지가 E1 하나로 통합됨")
        elif len(figs) > 1:
            print(f"  [INFO] figure {len(figs)}개 — 파일/페이지가 다르면 정상, 같은 파일이면 dedup 버그")
    else:
        print("  [OK] 부록 없음 (bbox 있는 렌더 가능 figure가 없음)")

    # bbox=[] 정성 문서 표 텍스트 유지 확인
    if "환경방침" in text:
        print("  [OK] bbox=[] 링크 표 텍스트 유지됨 (환경방침서 텍스트 보임)")
    else:
        print("  [INFO] 환경방침 텍스트 없음 (더미 세트에서만 확인 가능)")


if __name__ == "__main__":
    if len(sys.argv) >= 2:
        evidence_dir = Path(sys.argv[1])
        out_dir = evidence_dir / "_pdf_out"
        out_dir.mkdir(exist_ok=True)
        print(f"실제 증빙 세트: {evidence_dir}")
        pdf = run_real(evidence_dir, out_dir)
        check(pdf)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            print("더미 증빙 세트로 검증")
            pdf = run_dummy(Path(tmp))
            check(pdf)
