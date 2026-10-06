"""scripts/check_output_consistency.py — 출력 일치 검사기 단위 테스트.

저장소 안에서 작은 ResponseSheet를 만들고 **실제 exporters**로 tmp_path에 Excel·PDF를
생성해 검사한다. 외부 PDF나 개인 절대 경로에 의존하지 않는다.

주입 검사(값·상태·범위를 바꾸면 반드시 실패해야 한다)는 메모리 사본만 바꾼다 —
`probe_output_checker_second_review_29defa0.py`가 쓴 방식이다.
"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import re
import sys

import pytest

from esgenie.ssot.audit_trace import EvidenceLink
from esgenie.supplychain.exporters.excel import export_response_sheet
from esgenie.supplychain.exporters.pdf import export_response_sheet_pdf
from esgenie.supplychain.schema import Answer, ResponseSheet

ROOT = Path(__file__).resolve().parent.parent


def _load_checker():
    """scripts/는 패키지가 아니라 파일 경로로 불러온다."""
    path = ROOT / "scripts" / "check_output_consistency.py"
    spec = importlib.util.spec_from_file_location("check_output_consistency", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


# ── 검사 대상 시트 ────────────────────────────────────────────────────────────
def _sheet() -> ResponseSheet:
    """3행: 소수 수치 / 천 단위 수치 / 불리언."""
    return ResponseSheet(
        framework_key="rba42",
        framework_label="테스트 양식",
        corp_name="테스트사",
        answers=[
            Answer(
                qid="T-NUM-1", section="환경", question_text="에너지 사용량",
                value=0.513216, status="self_reported", unit="TJ", period=2026,
                boundary_label="2026-04-01~2026-04-30 · 월간 · 제1공장 · 사용전력량 · 부분",
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
                comparison="not_comparable", comparison_reason="대상 전원 이수가 확인되지 않았습니다",
                rationale="교육 기록 1건",
                evidence_links=[EvidenceLink(
                    file_name="09_안전보건교육실시기록_2026-04-22.pdf",
                    relative_path="evidence_pack/09_안전보건교육실시기록_2026-04-22.pdf",
                    origin="ocr_unstructured", page=0)],
            ),
        ],
    )


@pytest.fixture
def bundle(tmp_path: Path) -> object:
    """실제 exporters로 Excel·PDF를 만들고 읽어 둔 사본."""
    sheet = _sheet()
    out = tmp_path / "response_sheet"
    xlsx = export_response_sheet(sheet, out)
    pdf = export_response_sheet_pdf(sheet, out, embed_evidence=False)
    result = {"sheet": sheet.to_dict()}
    return checker.OutputBundle(
        json_rows=checker.load_json_rows(result),
        excel_rows=checker.load_excel_rows(xlsx),
        pdf_pages=checker.extract_pdf_text(pdf),
        paths={"result_json": "memory", "sheet_xlsx": str(xlsx), "sheet_pdf": str(pdf)},
    )


def _mismatch_fields(outcome: dict) -> set[str]:
    return {m["field"] for m in outcome["mismatches"]}


# ── 정상 ─────────────────────────────────────────────────────────────────────
def test_clean_outputs_pass(bundle):
    outcome = checker.compare(bundle)
    assert outcome["mismatches"] == [], outcome["mismatches"]
    assert outcome["summary"]["json_rows"] == 3
    assert outcome["summary"]["excel_rows"] == 3
    assert outcome["summary"]["pdf_rows"] == 3
    assert outcome["summary"]["checked_field_values"] > 0


# ── Excel 주입: 값 / 상태 / 범위 문구 ────────────────────────────────────────
def test_excel_value_change_fails(bundle):
    b = copy.deepcopy(bundle)
    b.excel_rows["T-NUM-1"]["display_value"] = ("0.6 TJ (2026년)", "응답서!D6")
    outcome = checker.compare(b)
    assert "display_value" in _mismatch_fields(outcome)


def test_excel_status_change_fails(bundle):
    b = copy.deepcopy(bundle)
    b.excel_rows["T-NUM-1"]["badge"] = ("✅ 증빙검증", "응답서!F6")
    outcome = checker.compare(b)
    assert "badge" in _mismatch_fields(outcome)


def test_excel_scope_change_fails(bundle):
    b = copy.deepcopy(bundle)
    b.excel_rows["T-NUM-1"]["scope"] = (
        "측정 범위: 2026-01-01~2026-12-31 · 연간 · 전사 · 전체 · 범위 확인 필요", "응답서!E6")
    outcome = checker.compare(b)
    assert "boundary_label" in _mismatch_fields(outcome)


def test_excel_review_note_removed_fails(bundle):
    b = copy.deepcopy(bundle)
    b.excel_rows["T-NUM-1"]["note"] = ("전력 고지서 1건으로 산정", "응답서!G6")
    outcome = checker.compare(b)
    assert "review_note" in _mismatch_fields(outcome)


def test_excel_evidence_file_name_change_fails(bundle):
    b = copy.deepcopy(bundle)
    row = b.excel_rows["T-NUM-1"]
    row["note"] = (str(row["note"][0]).replace("02_전기요금청구서_2026-04.pdf",
                                               "99_다른문서_2026-04.pdf"), row["note"][1])
    outcome = checker.compare(b)
    assert "evidence_links[].file_name" in _mismatch_fields(outcome)


def test_excel_row_missing_fails(bundle):
    b = copy.deepcopy(bundle)
    del b.excel_rows["T-BOOL-1"]
    outcome = checker.compare(b)
    assert any(m["field"] == "row" and m["excel_value"] == "없음"
               for m in outcome["mismatches"])


# ── PDF 주입: 추출 텍스트 단계에서 값 / 상태 / 범위 ──────────────────────────
def _sub_pdf(b, old: str, new: str) -> int:
    """추출된 페이지 텍스트에서만 치환한다 — 원본 PDF는 건드리지 않는다.

    PDF는 표 셀을 폭에 맞춰 줄바꿈하므로 글자 사이에 공백이 끼어도 찾아야 한다.
    """
    pattern = re.compile(r"\s*".join(re.escape(ch) for ch in old))
    n = 0
    for i, page in enumerate(b.pdf_pages):
        replaced, count = pattern.subn(new, page)
        if count:
            b.pdf_pages[i] = replaced
            n += count
    return n


def test_pdf_value_change_fails(bundle):
    b = copy.deepcopy(bundle)
    assert _sub_pdf(b, "0.513216", "0.6") > 0
    outcome = checker.compare(b)
    assert "display_value" in _mismatch_fields(outcome)
    assert any(m["pdf_page"] for m in outcome["mismatches"] if m["field"] == "display_value")


def test_pdf_status_change_fails(bundle):
    b = copy.deepcopy(bundle)
    assert _sub_pdf(b, "자가신고", "증빙검증") > 0
    outcome = checker.compare(b)
    assert "badge" in _mismatch_fields(outcome)


def test_pdf_scope_change_fails(bundle):
    b = copy.deepcopy(bundle)
    assert _sub_pdf(b, "사용전력량", "전사합산") > 0
    outcome = checker.compare(b)
    assert "boundary_label" in _mismatch_fields(outcome)


def test_pdf_review_note_removed_fails(bundle):
    b = copy.deepcopy(bundle)
    assert _sub_pdf(b, "전사·연간 총량임이 입증되지 않았습니다", "") > 0
    outcome = checker.compare(b)
    assert "review_note" in _mismatch_fields(outcome)


def test_pdf_evidence_file_name_change_fails(bundle):
    b = copy.deepcopy(bundle)
    assert _sub_pdf(b, "02_전기요금청구서", "99_다른문서") > 0
    outcome = checker.compare(b)
    assert "evidence_links[].file_name" in _mismatch_fields(outcome)


def test_pdf_row_missing_fails(bundle):
    b = copy.deepcopy(bundle)
    assert _sub_pdf(b, "T-BOOL-1", "T-ZZZZ-9") > 0
    outcome = checker.compare(b)
    assert any(m["field"] == "row" and m["pdf_value"] == "없음"
               for m in outcome["mismatches"])


# ── 수치 비교 규칙 ───────────────────────────────────────────────────────────
def test_rounded_number_is_mismatch():
    """0.513216을 0.5로 줄여 표시하면 통과하지 않는다."""
    assert not checker.values_match(checker.normalize("0.513216 TJ"),
                                    checker.normalize("0.5 TJ"))


def test_thousands_separator_is_equal():
    """'1,234'와 '1234'는 같다 — 천 단위 구분 기호만 지운다."""
    assert checker.values_match(checker.normalize("1,234 m3"), checker.normalize("1234 m3"))
    assert checker.normalize("1,234,567") == "1234567"


def test_whitespace_and_newline_removed():
    assert checker.normalize("0.513216 TJ\n(2026년)") == "0.513216TJ(2026년)"


def test_glyph_substitution():
    """PDF 글꼴에 없는 ÷·×·→를 /·x·->로 맞춘다 (check_outputs.py glyph_flat)."""
    assert checker.normalize("11,500 ÷ 39,200 × 100") == "11500/39200x100"
    assert checker.normalize("대상 → 참석") == "대상->참석"


def test_ordinary_comma_is_kept():
    """문장의 일반 콤마는 지우지 않는다 — 천 단위만 지운다."""
    assert checker.normalize("가, 나, 다") == "가,나,다"


def test_numbers_parsed_as_decimal():
    from decimal import Decimal
    assert checker.numbers("0.513216 TJ (2026년)") == [Decimal("0.513216"), Decimal("2026")]


# ── 자릿수 축약 표시 (규칙은 완화하지 않는다) ────────────────────────────────
def test_digit_truncation_flagged_on_excel(bundle):
    """0.513216을 0.5로 줄여 표시하면 불일치로 두고 비고만 붙인다."""
    b = copy.deepcopy(bundle)
    b.excel_rows["T-NUM-1"]["display_value"] = ("0.5 TJ (2026년)", "응답서!D6")
    outcome = checker.compare(b)
    note = next(m["note"] for m in outcome["mismatches"]
                if m["field"] == "display_value" and m["excel_cell"])
    assert "digit_truncation" in note


def test_digit_truncation_flagged_on_pdf(bundle):
    b = copy.deepcopy(bundle)
    assert _sub_pdf(b, "0.513216", "0.5") > 0
    outcome = checker.compare(b)
    note = next(m["note"] for m in outcome["mismatches"]
                if m["field"] == "display_value" and m["pdf_page"])
    assert "digit_truncation" in note


def test_unrelated_value_change_not_flagged_as_truncation(bundle):
    """값이 아예 다른 경우는 축약으로 표시하지 않는다."""
    b = copy.deepcopy(bundle)
    b.excel_rows["T-NUM-1"]["display_value"] = ("9.999 TJ (2026년)", "응답서!D6")
    outcome = checker.compare(b)
    note = next(m["note"] for m in outcome["mismatches"]
                if m["field"] == "display_value" and m["excel_cell"])
    assert "digit_truncation" not in note


# ── PDF 파싱 층 ──────────────────────────────────────────────────────────────
def test_parse_pdf_rows_handles_wrapped_qid():
    """PDF는 표 셀을 폭에 맞춰 줄바꿈한다 — qid가 쪼개져도 찾아야 한다."""
    pages = ["RBA-C-4-E-6-\n2\n환경\n문항\n29.3 %\n"]
    rows = checker.parse_pdf_rows(pages, ["RBA-C-4-E-6-2"])
    assert "RBA-C-4-E-6-2" in rows
    assert rows["RBA-C-4-E-6-2"].page == 1


def test_parse_pdf_rows_prefers_longer_qid():
    """접두가 겹치는 qid(RBA-C-4-E-6 / RBA-C-4-E-6-2)를 혼동하지 않는다."""
    pages = ["RBA-C-4-E-6-2\n값A\nRBA-C-4-E-6\n값B\n"]
    rows = checker.parse_pdf_rows(pages, ["RBA-C-4-E-6", "RBA-C-4-E-6-2"])
    assert set(rows) == {"RBA-C-4-E-6", "RBA-C-4-E-6-2"}
    assert "값A" in rows["RBA-C-4-E-6-2"].text
    assert "값B" in rows["RBA-C-4-E-6"].text


def test_parse_pdf_rows_merges_continued_rows():
    """'(이어서)' 행은 같은 qid 블록으로 합친다 (exporters/pdf.py:312-316)."""
    pages = ["T-1\n환경\n문항\n값\n범위\n신뢰\n앞부분\n",
             "T-1\n환경\n(이어서)\n뒷부분\n"]
    rows = checker.parse_pdf_rows(pages, ["T-1"])
    assert rows["T-1"].blocks == 2
    assert "앞부분" in rows["T-1"].text and "뒷부분" in rows["T-1"].text
    assert rows["T-1"].page == 1


def test_parse_pdf_rows_stops_at_checklist():
    """체크리스트 표와 증빙 부록의 qid를 응답표 행으로 세지 않는다."""
    pages = ["T-1\n환경\n문항\n값\n",
             f"{checker.PDF_BODY_END}\nT-9\n환경\n문항\n값\n"]
    rows = checker.parse_pdf_rows(pages, ["T-1", "T-9"])
    assert set(rows) == {"T-1"}


# ── 적재 층 ──────────────────────────────────────────────────────────────────
def test_load_excel_rows_skips_group_header(tmp_path):
    """섹션 그룹 헤더 행(▌…)을 문항 행으로 세지 않는다."""
    sheet = _sheet()
    xlsx = export_response_sheet(sheet, tmp_path)
    rows = checker.load_excel_rows(xlsx)
    assert set(rows) == {"T-NUM-1", "T-NUM-2", "T-BOOL-1"}
    assert not any(q.startswith(checker.EXCEL_GROUP_PREFIX) for q in rows)


def test_load_excel_rows_records_cell_address(tmp_path):
    sheet = _sheet()
    xlsx = export_response_sheet(sheet, tmp_path)
    rows = checker.load_excel_rows(xlsx)
    cell = rows["T-NUM-1"]["display_value"][1]
    assert cell.startswith("응답서!D")


def test_load_json_rows_rejects_empty():
    with pytest.raises(checker.InputError):
        checker.load_json_rows({"sheet": {"answers": []}})


def test_load_bundle_rejects_missing_dir(tmp_path):
    with pytest.raises(checker.InputError):
        checker.load_bundle(tmp_path / "없는폴더")


def test_load_bundle_rejects_missing_result_json(tmp_path):
    (tmp_path / "exports" / "response_sheet").mkdir(parents=True)
    with pytest.raises(checker.InputError):
        checker.load_bundle(tmp_path)


# ── 보고 ─────────────────────────────────────────────────────────────────────
def test_report_and_markdown(tmp_path, bundle):
    payload = checker.report(tmp_path, bundle, checker.compare(bundle))
    assert payload["checked_at"].endswith("+09:00")        # KST
    assert payload["fields"] == list(checker.FIELDS)
    assert set(payload["inputs"]) == {"result_json", "sheet_xlsx", "sheet_pdf"}
    assert payload["inputs"]["sheet_xlsx"]["sha256"]
    md = checker.to_markdown(payload)
    assert "출력 일치 검사" in md
    assert "없음." in md


def test_markdown_lists_mismatch(bundle):
    b = copy.deepcopy(bundle)
    b.excel_rows["T-NUM-1"]["display_value"] = ("0.6 TJ (2026년)", "응답서!D6")
    payload = checker.report(Path("."), b, checker.compare(b))
    md = checker.to_markdown(payload)
    assert "display_value" in md
    assert "응답서!D6" in md


# ── CLI 종료 코드 ────────────────────────────────────────────────────────────
def _write_run(tmp_path: Path, sheet: ResponseSheet) -> Path:
    import json
    run = tmp_path / "stage"
    out = run / "exports" / "response_sheet"
    export_response_sheet(sheet, out)
    export_response_sheet_pdf(sheet, out, embed_evidence=False)
    (run / "result.json").write_text(
        json.dumps({"sheet": sheet.to_dict()}, ensure_ascii=False, default=str),
        encoding="utf-8")
    return run


def test_cli_exit_zero_on_clean(tmp_path, monkeypatch, capsys):
    run = _write_run(tmp_path, _sheet())
    monkeypatch.setattr(sys, "argv", ["check_output_consistency.py",
                                      "--run-dir", str(run), "--out", str(tmp_path / "out")])
    assert checker.main() == 0
    capsys.readouterr()
    assert (tmp_path / "out" / "consistency.json").exists()
    assert (tmp_path / "out" / "consistency.md").exists()


def test_cli_exit_two_on_input_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["check_output_consistency.py",
                                      "--run-dir", str(tmp_path / "없음"),
                                      "--out", str(tmp_path / "out")])
    assert checker.main() == 2
    capsys.readouterr()
