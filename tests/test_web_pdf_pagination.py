"""The saved web PDF retains text and identifies questions across page breaks."""
from copy import deepcopy
import re
import unicodedata

import fitz
import pytest

from esgenie.web.downloads import canonical_pdf


def compact(text):
    return re.sub(r"\s+", "", text)


def row(qid="RBA-E-10"):
    return {
        "id": qid, "question": "시정조치 <원문> & 확인", "value_text": "예",
        "method": "담당자 수정", "scope_label": "2026년 · 제1공장",
        "review_status": "complete", "review_label": "담당자 검토 완료",
        "status_label": "확인 필요", "why": "원문 대조 필요", "next_step": "보완 요청",
        "notices": ["92%와 29.3%의 분모는 다름"],
        "saved": {"memo": "첫 줄\n둘째 줄 <검토> & 확인", "reason": "저장한 수정 이유"},
        "sources": [{"name": "한울㈜_시정조치_원문_<검토>&보관.pdf", "page": 2,
                     "version": 7, "quote": "근거 46+4=50 · 안내 수령 73명"}],
        "reference_sources": [{"name": "보완_참고.pdf", "page": 4, "version": 3,
                               "quote": "참고 인용은 값 산정에 사용하지 않음"}],
    }


def project():
    return {"company_name": "한울정밀공업㈜ <본사> & 공장", "mode": "real", "year": 2026,
            "framework": "rba42", "notes": {"RBA-E-10": {"answer": "이전 답변㈜", "text": "이전 메모\n둘째 줄"}},
            "result": {"limitations": ["부가 안내㈜ <보류> & 재확인"]}}


def test_title_body_and_supplement_use_the_same_safe_text(tmp_path):
    data = project()
    rows = [row()]
    original = deepcopy((data, rows))
    with fitz.open(canonical_pdf(data, rows, tmp_path)) as pdf:
        text = compact("\n".join(page.get_text() for page in pdf))
        for expected in ["한울정밀공업(주) <본사> & 공장", "담당자 검토 완료", "2026년 · 제1공장",
                         "한울(주)_시정조치_원문_<검토>&보관.pdf", "3쪽 · 버전 7",
                         "근거 46+4=50 · 안내 수령 73명", "5쪽 · 버전 3",
                         "보완 대상(값 산정 미사용)", "참고 인용은 값 산정에 사용하지 않음",
                         "첫 줄 둘째 줄 <검토> & 확인", "저장한 수정 이유",
                         "이전 답변(주)", "이전 메모 둘째 줄", "부가 안내(주) <보류> & 재확인"]:
            assert compact(expected) in text
        assert "㈜" not in text and "\ufffd" not in text
    assert (data, rows) == original


def test_macos_decomposed_source_and_reference_names_are_readable_in_downloaded_pdf(tmp_path):
    rows = [row()]
    names = ['16_ESG의사소통절차및전달확인기록_2026-04.pdf',
             '13_안전보건추가교육실시기록_2026-04-27.pdf']
    for source, name in zip([rows[0]['sources'][0], rows[0]['reference_sources'][0]], names):
        source['name'] = unicodedata.normalize('NFD', name)
    original = deepcopy(rows)
    with fitz.open(canonical_pdf(project(), rows, tmp_path)) as pdf:
        text = compact('\n'.join(page.get_text() for page in pdf))
        for name in names:
            assert compact(name) in text
        assert not any('\u1100' <= c <= '\u11ff' for c in text)
    # Presentation normalizes only what is drawn, never archived file identities.
    assert rows == original


@pytest.mark.parametrize("field", ["quote", "memo", "value_text", "scope_label", "question"])
def test_every_long_cell_continues_with_question_id_without_losing_text(tmp_path, field):
    data = project()
    data["notes"] = {}
    data["result"]["limitations"] = []
    long_row = row()
    tokens = [f"{field}_{i:04d} 확인한 원문 줄" for i in range(170)]
    text = "\n".join(tokens)
    if field == "quote":
        long_row["sources"][0]["quote"] = text
    elif field == "memo":
        long_row["saved"]["memo"] = text
    else:
        long_row[field] = text
    rows = [row("RBA-E-9"), long_row, row("RBA-E-11")]
    with fitz.open(canonical_pdf(data, rows, tmp_path)) as pdf:
        assert len(pdf) >= 3
        pages = [page.get_text() for page in pdf]
        combined = compact("\n".join(pages))
        # Compare the entire body column across pages. A header/other column
        # between two halves of a wrapped line is layout, not lost content.
        column = {"quote": 4, "memo": 5, "value_text": 1, "scope_label": 2, "question": 0}[field]
        weights = [125, 85, 110, 75, 195, 203]
        body = []
        for page in pdf:
            width = page.rect.width - 60
            left = 30 + sum(weights[:column]) * width / sum(weights)
            right = left + weights[column] * width / sum(weights)
            top = page.search_for("저장된 답변")[0].y1 + 20
            body.append(page.get_text(clip=fitz.Rect(left, top, right, page.rect.height - 28)))
        column_text = compact("\n".join(body))
        for token in tokens:
            assert compact(token) in column_text
        assert "RBA-E-11" in combined
        continued = [page for page in pages if "(계속)" in page]
        assert len(continued) >= 2
        assert all("RBA-E-10 (계속)" in page for page in continued)
        for page, text in zip(pdf, pages):
            # No repeat header halfway down a page after splitting a cell.
            assert text.count("저장된 답변") == 1
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        x0, y0, x1, y1 = span["bbox"]
                        assert 24 <= x0 < x1 <= page.rect.width - 24 + 0.5
                        assert 0 <= y0 < y1 <= page.rect.height - 9
