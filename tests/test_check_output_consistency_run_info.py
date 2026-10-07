"""출력 일치 검사기 × 실행 출처(B-2) 연동.

PDF 바닥글은 canvas에 직접 그려 추출 텍스트에 섞인다. 응답 칸의 내용이 아니므로
블록 파싱 전에 떼어내되, **기대 문자열과 정확히 같은 줄만** 지운다 — 접두어로
싹 지우면 응답 칸에 들어간 `실행 정보: …` 문구도 함께 사라져 검사를 피해 간다.

더해서 (a) 모든 쪽에 바닥글이 정확히 1번 (b) 1쪽에 요약 줄이 있는지, 그리고
Excel '실행정보' 시트가 result.json의 run_info와 같은지 검사한다.
"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import re
import sys

import pytest

from esgenie import run_info as ri
from esgenie.ssot.audit_trace import EvidenceLink
from esgenie.supplychain.exporters.excel import export_response_sheet
from esgenie.supplychain.exporters.pdf import export_response_sheet_pdf
from esgenie.supplychain.schema import Answer, ResponseSheet

ROOT = Path(__file__).resolve().parent.parent


def _load_checker():
    path = ROOT / "scripts" / "check_output_consistency.py"
    spec = importlib.util.spec_from_file_location("check_output_consistency", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()

INFO = ri.build_run_info(
    llm_stats_end={"mode": "on", "hits": 0, "misses": 17, "live_calls": 17,
                   "successes": 17, "failures": 0},
    llm_stats_start={"mode": "on", "hits": 0, "misses": 0, "live_calls": 0,
                     "successes": 0, "failures": 0},
    ocr_stats={"hits": 0, "misses": 10, "mode": "miss"},
    upstage_live_requests=4,
)


def _sheet(n: int, *, with_evidence: bool) -> ResponseSheet:
    """여러 쪽이 되는 시트. 근거 링크가 없는 행이 많은 경우가 이전에 바닥글에 걸렸다."""
    answers = []
    for i in range(1, n + 1):
        answers.append(Answer(
            qid=f"T-{i:03d}", section="환경" if i % 2 else "경영시스템",
            question_text=f"[{i}] 측정 항목 {i}",
            value=1.0 + i, status="verified", unit="TJ", period=2026,
            rationale=f"근거 {i}",
            evidence_links=[EvidenceLink(
                file_name=f"{i:02d}_청구서_2026-04.pdf",
                relative_path=f"evidence_pack/{i:02d}_청구서_2026-04.pdf",
                origin="ocr_structured", page=0)] if with_evidence else [],
        ))
    return ResponseSheet(framework_key="rba42", framework_label="테스트 양식",
                         corp_name="테스트사", answers=answers)


def _bundle(tmp_path, sheet, *, run_info):
    out = tmp_path / "response_sheet"
    xlsx = export_response_sheet(sheet, out, run_info=run_info)
    pdf = export_response_sheet_pdf(sheet, out, embed_evidence=False, run_info=run_info)
    result = {"sheet": sheet.to_dict()}
    if run_info:
        result["run_info"] = run_info
    return checker.OutputBundle(
        json_rows=checker.load_json_rows(result),
        excel_rows=checker.load_excel_rows(xlsx),
        pdf_pages=checker.extract_pdf_text(pdf),
        evidence_base_dir=str(Path(pdf).parent),
        run_info=result.get("run_info"),
        excel_run_info_rows=checker.load_excel_run_info(xlsx),
    )


@pytest.fixture
def stamped(tmp_path):
    """바닥글이 있는 다중 페이지 번들 — 근거 링크 없는 행 30개."""
    b = _bundle(tmp_path, _sheet(30, with_evidence=False), run_info=INFO)
    assert len(b.pdf_pages) >= 2, "여러 쪽이 되어야 바닥글 경계를 볼 수 있다"
    return b


# ── 정상 ─────────────────────────────────────────────────────────────────────
def test_stamped_multipage_passes(stamped):
    """바닥글이 있어도 불일치 0 — 이전 판에서는 블록 끝 잔여로 걸렸다."""
    outcome = checker.compare(stamped)
    assert outcome["mismatches"] == [], outcome["mismatches"]
    s = outcome["summary"]
    assert s["run_info_present"] is True
    # 바닥글 N쪽 + 1쪽 요약 한 줄
    assert s["run_info_stamp_lines_removed"] == len(stamped.pdf_pages) + 1
    assert s["excel_run_info_rows"] > 0


def test_stamped_with_evidence_passes(tmp_path):
    b = _bundle(tmp_path, _sheet(30, with_evidence=True), run_info=INFO)
    assert checker.compare(b)["mismatches"] == []


def test_without_run_info_behaves_as_before(tmp_path):
    """run_info가 없으면 지금과 똑같이 동작한다."""
    b = _bundle(tmp_path, _sheet(30, with_evidence=False), run_info=None)
    outcome = checker.compare(b)
    assert outcome["mismatches"] == []
    s = outcome["summary"]
    assert s["run_info_present"] is False
    assert s["run_info_stamp_lines_removed"] == 0
    assert s["excel_run_info_rows"] == 0


# ── 바닥글 검사 (a)·(b) ──────────────────────────────────────────────────────
def _drop_line(pages: list[str], needle: str, page_index: int) -> bool:
    flat = checker.normalize(needle)
    kept, hit = [], False
    for line in pages[page_index].split("\n"):
        if not hit and checker.normalize(line) == flat:
            hit = True
            continue
        kept.append(line)
    pages[page_index] = "\n".join(kept)
    return hit


def test_missing_footer_on_one_page_fails(stamped):
    b = copy.deepcopy(stamped)
    stamps = checker.run_info_stamps(b.run_info)
    target = len(b.pdf_pages) - 1
    assert _drop_line(b.pdf_pages, stamps["footer"], target)
    outcome = checker.compare(b)
    hit = [m for m in outcome["mismatches"] if m["field"] == "run_info_footer"]
    assert hit, "바닥글 누락을 놓쳤다"
    assert hit[0]["pdf_page"] == target + 1


def test_missing_summary_on_first_page_fails(stamped):
    b = copy.deepcopy(stamped)
    stamps = checker.run_info_stamps(b.run_info)
    assert _drop_line(b.pdf_pages, stamps["summary"], 0)
    outcome = checker.compare(b)
    assert any(m["field"] == "run_info_footer" and "요약" in m["note"]
               for m in outcome["mismatches"]), "요약 줄 누락을 놓쳤다"


def test_duplicated_footer_fails(stamped):
    b = copy.deepcopy(stamped)
    stamps = checker.run_info_stamps(b.run_info)
    b.pdf_pages[0] = b.pdf_pages[0] + "\n" + stamps["footer"] + "\n"
    outcome = checker.compare(b)
    hit = [m for m in outcome["mismatches"] if m["field"] == "run_info_footer"]
    assert hit and "2번" in hit[0]["pdf_value"]


def test_sha_tampered_footer_fails(stamped):
    """SHA를 바꾸면 기대 줄과 달라져 제거되지 않고, 바닥글 누락으로도 잡힌다."""
    b = copy.deepcopy(stamped)
    short = ri.short_sha(b.run_info)
    b.pdf_pages = [p.replace(short, "0000000") for p in b.pdf_pages]
    outcome = checker.compare(b)
    assert outcome["mismatches"], "SHA 변조를 놓쳤다"
    assert any(m["field"] == "run_info_footer" for m in outcome["mismatches"])


def test_processing_label_tampered_footer_fails(stamped):
    b = copy.deepcopy(stamped)
    label = b.run_info["processing"]["label"]
    b.pdf_pages = [p.replace(label, "캐시 재생" if label != "캐시 재생" else "신규 처리")
                   for p in b.pdf_pages]
    outcome = checker.compare(b)
    assert any(m["field"] == "run_info_footer" for m in outcome["mismatches"]), \
        "처리 방식 변조를 놓쳤다"


# ── 접두어로 싹 지우지 않는다 ────────────────────────────────────────────────
def test_stamp_like_text_in_answer_cell_is_not_swallowed(tmp_path):
    """응답 칸에 '실행 정보: 코드 …'로 시작하는 문구가 들어가면 잡아야 한다.

    접두어로 줄을 빼는 방식이면 이 문구가 함께 지워져 검사를 피해 간다.
    """
    sheet = _sheet(30, with_evidence=False)
    b = _bundle(tmp_path, sheet, run_info=INFO)
    assert checker.compare(b)["mismatches"] == []
    tampered = copy.deepcopy(b)
    fake = f"{ri.STAMP_PREFIX} 코드 0000000 · 캐시 재생"
    inserted = False
    for i, page in enumerate(tampered.pdf_pages):
        m = re.search(r"T-0\d\d", page)
        if m is None:
            continue
        cut = m.end()
        tampered.pdf_pages[i] = page[:cut] + f"\n{fake}\n" + page[cut:]
        inserted = True
        break
    assert inserted
    outcome = checker.compare(tampered)
    assert outcome["mismatches"], "응답 칸에 끼운 도장형 문구를 놓쳤다"
    assert any(m["cell_exact"] and m["field"].startswith("cell:")
               for m in outcome["mismatches"]), \
        "도장 줄로 오인해 통째로 지웠다(칸 불일치가 나와야 한다)"


def test_only_exact_lines_are_stripped():
    """strip_run_info_lines는 완전히 같은 줄만 지운다."""
    stamps = checker.run_info_stamps(INFO)
    pages = ["\n".join([stamps["footer"], f"{ri.STAMP_PREFIX} 코드 0000000 · 캐시 재생",
                        stamps["footer"] + " 덧붙임", "본문"])]
    out, counts = checker.strip_run_info_lines(pages, stamps)
    assert counts[0]["footer"] == 1
    kept = out[0]
    assert "0000000" in kept, "다른 내용의 도장형 줄을 지웠다"
    assert "덧붙임" in kept, "접두어가 같고 뒤가 다른 줄을 지웠다"
    assert "본문" in kept


# ── Excel 실행정보 시트 ─────────────────────────────────────────────────────
def test_excel_run_info_value_tampered_fails(stamped):
    b = copy.deepcopy(stamped)
    idx = next(i for i, (label, _) in enumerate(b.excel_run_info_rows)
               if label == "처리 방식")
    label, value = b.excel_run_info_rows[idx]
    b.excel_run_info_rows[idx] = (label, "캐시 재생" if value != "캐시 재생" else "신규 처리")
    outcome = checker.compare(b)
    hit = [m for m in outcome["mismatches"] if m["field"] == "run_info_excel"]
    assert hit, "Excel 실행정보 값 변조를 놓쳤다"
    assert hit[0]["excel_cell"].startswith("실행정보!B")


def test_excel_run_info_row_dropped_fails(stamped):
    b = copy.deepcopy(stamped)
    b.excel_run_info_rows = b.excel_run_info_rows[:-1]
    assert any(m["field"] == "run_info_excel" for m in checker.compare(b)["mismatches"])


def test_excel_run_info_missing_sheet_fails(stamped):
    b = copy.deepcopy(stamped)
    b.excel_run_info_rows = []
    hit = [m for m in checker.compare(b)["mismatches"] if m["field"] == "run_info_excel"]
    assert hit and "시트가 없다" in hit[0]["note"]


def test_excel_run_info_present_without_json_fails(tmp_path):
    """result.json에 run_info가 없는데 Excel에 실행정보가 있으면 잡는다."""
    b = _bundle(tmp_path, _sheet(3, with_evidence=False), run_info=None)
    b.excel_run_info_rows = [("코드 커밋", "d8a0de8")]
    hit = [m for m in checker.compare(b)["mismatches"] if m["field"] == "run_info_excel"]
    assert hit


def test_excel_run_info_matches_json(stamped):
    """정상일 때 Excel 값이 run_info와 같다."""
    want = ri.rows(stamped.run_info)
    assert [label for label, _ in stamped.excel_run_info_rows] == [label for label, _ in want]
    for (_, a), (_, b) in zip(stamped.excel_run_info_rows, want):
        assert checker.normalize(a) == checker.normalize(b)


# ── 적재 층 ──────────────────────────────────────────────────────────────────
def test_load_excel_run_info_empty_without_sheet(tmp_path):
    xlsx = export_response_sheet(_sheet(3, with_evidence=False), tmp_path)
    assert checker.load_excel_run_info(xlsx) == []


def test_run_info_stamps_apply_pdf_safe_text():
    """기대 문자열은 exporters가 거치는 pdf_safe_text를 적용한다."""
    from esgenie.supplychain.exporters._fonts import pdf_safe_text

    stamps = checker.run_info_stamps(INFO)
    assert stamps["footer"] == pdf_safe_text(ri.footer_line(INFO))
    assert stamps["summary"] == pdf_safe_text(ri.summary_line(INFO))


def test_run_info_stamps_empty_without_info():
    assert checker.run_info_stamps(None) == {}
    pages = ["아무 내용"]
    out, counts = checker.strip_run_info_lines(pages, {})
    assert out == pages
    assert counts == [{"footer": 0, "summary": 0}]
