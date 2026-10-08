#!/usr/bin/env python3
"""`run_info=None` 동일성 기준값을 만든다 — exporters를 **고치기 전에** 한 번 실행했다.

`export_response_sheet(..., run_info=None)`·`export_response_sheet_pdf(..., run_info=None)`이
기존 출력과 셀 값·추출 텍스트가 같아야 한다(작업지시서 §3 통과 조건 1). 그걸 증명하려면
수정 전 출력을 먼저 떠 둬야 한다.

사용(저장소 루트에서):
  python tests/fixtures/make_run_info_baseline.py
→ tests/fixtures/run_info_baseline.json 을 덮어쓴다.

PDF 추출 텍스트에는 매번 바뀌는 값이 없다(제목·요약·표만 그린다 — 생성 시각을 싣지 않는다).
그래도 혹시 모를 변동을 막기 위해 날짜·시각 모양의 토큰은 자리표시자로 바꿔 저장한다.
바꾸는 규칙은 `tests/test_run_info_export_identity.py`가 같은 함수를 import해 쓴다.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

OUT = Path(__file__).resolve().parent / "run_info_baseline.json"

# 생성 시각처럼 실행마다 바뀔 수 있는 토큰. 현재 응답서에는 없지만, 들어오면
# 동일성 비교가 조용히 깨지는 대신 여기서 가려지고 제외 항목으로 기록된다.
_VOLATILE = (
    (re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2}|Z)?"),
     "<TIMESTAMP>"),
    (re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"), "<DATETIME>"),
)
VOLATILE_NOTE = "ISO 타임스탬프·'YYYY-MM-DD HH:MM:SS' 모양 토큰은 자리표시자로 치환해 비교한다"


def mask_volatile(text: str) -> str:
    out = str(text)
    for pattern, repl in _VOLATILE:
        out = pattern.sub(repl, out)
    return out


def sample_sheet():
    """작은 응답서 — 값·범위·비교 판정·근거·초안이 모두 있는 3행."""
    from esgenie.ssot.audit_trace import EvidenceLink
    from esgenie.supplychain.schema import Answer, ResponseSheet

    return ResponseSheet(
        framework_key="rba42",
        framework_label="테스트 양식",
        corp_name="테스트사",
        gaps=["보완 항목 예시"],
        answers=[
            Answer(
                qid="T-NUM-1", section="환경", question_text="에너지 사용량",
                value=0.513216, status="self_reported", unit="TJ", period=2026,
                boundary_label="2026-04-01~2026-04-30 · 월간 · 제1공장 · 부분",
                completeness="partial", comparison="scope_unconfirmed",
                comparison_reason="전사·연간 총량임이 입증되지 않았습니다",
                rationale="전력 고지서 1건으로 산정",
                evidence_links=[EvidenceLink(
                    file_name="02_전기요금청구서_2026-04.pdf",
                    relative_path="evidence_pack/02_전기요금청구서_2026-04.pdf",
                    origin="ocr_structured", page=0)],
            ),
            Answer(
                qid="T-NUM-2", section="환경", question_text="용수 사용량",
                value=1234567, status="verified", unit="m3", period=2026,
                boundary_label="2026-04-01~2026-04-30 · 월간 · 제1공장 · 상수도 · 부분",
                comparison="compared", rationale="상수도 고지서 기준",
                evidence_links=[EvidenceLink(
                    file_name="07_상수도청구서_2026-04.pdf",
                    relative_path="evidence_pack/07_상수도청구서_2026-04.pdf",
                    origin="ocr_structured", page=1)],
            ),
            Answer(
                qid="T-BOOL-1", section="경영시스템", question_text="교육 실시 여부",
                value=True, status="flagged",
                comparison="not_comparable",
                comparison_reason="대상 전원 이수가 확인되지 않았습니다",
                rationale="교육 기록 1건",
                flags=["자가주장 검토필요: 대상 50명 중 46명 출석"],
                evidence_links=[EvidenceLink(
                    file_name="09_안전보건교육실시기록_2026-04-22.pdf",
                    relative_path="evidence_pack/09_안전보건교육실시기록_2026-04-22.pdf",
                    origin="ocr_unstructured", page=0)],
            ),
        ],
    )


def snapshot(out_dir: Path, **kwargs) -> dict:
    """Excel 모든 시트의 모든 셀 값 + PDF 페이지별 추출 텍스트."""
    import openpyxl
    import pymupdf

    from esgenie.supplychain.exporters.excel import export_response_sheet
    from esgenie.supplychain.exporters.pdf import export_response_sheet_pdf

    sheet = sample_sheet()
    xlsx = export_response_sheet(sheet, out_dir, **kwargs)
    pdf = export_response_sheet_pdf(sheet, out_dir, embed_evidence=False, **kwargs)

    wb = openpyxl.load_workbook(xlsx, data_only=True)
    cells: dict[str, dict[str, str]] = {}
    for name in wb.sheetnames:
        ws = wb[name]
        cells[name] = {
            cell.coordinate: mask_volatile(cell.value)
            for row in ws.iter_rows() for cell in row
            if cell.value is not None
        }
    with pymupdf.open(pdf) as doc:
        pages = [mask_volatile(page.get_text()) for page in doc]
    return {"excel_sheets": wb.sheetnames, "excel_cells": cells, "pdf_pages": pages}


def main() -> int:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        data = snapshot(Path(td) / "response_sheet")
    OUT.write_text(json.dumps(
        {"note": "exporters에 run_info를 넣기 전의 출력. run_info=None이면 이것과 같아야 한다.",
         "volatile_note": VOLATILE_NOTE, **data},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(OUT.relative_to(ROOT)),
                      "sheets": data["excel_sheets"],
                      "cell_counts": {k: len(v) for k, v in data["excel_cells"].items()},
                      "pdf_pages": len(data["pdf_pages"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
