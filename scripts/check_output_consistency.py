#!/usr/bin/env python3
"""같은 실행의 result.json(화면 원천)·응답서 Excel·응답서 PDF가 문항별로 같은 내용을 보이는지 판정한다.

기준 필드는 `docs/UI연결용_수치범위_출력계약_2026-10-05.md` §2를 따른다:
  display_value · badge · comparison_label · review_note · boundary_label · evidence_links[].file_name

사용:
  python scripts/check_output_consistency.py --run-dir <run>/<stage> --out <dir>

종료 코드: 0 불일치 없음 / 1 불일치 있음 / 2 입력 오류.

층을 나눈 이유: 주입 검사와 단위 테스트가 파일을 거치지 않고 메모리 사본만 바꿀 수 있어야 한다
(`probe_output_checker_second_review_29defa0.py`가 `load`/`check`를 나눠 쓴 방식).
  load_json_rows / load_excel_rows / extract_pdf_text → parse_pdf_rows → compare
`parse_pdf_rows`는 '추출된 텍스트'를 입력으로 받는다 — PDF 주입 검사를 텍스트 단계에서 하기 때문이다.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import glob
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # repo 루트에서 `esgenie` 패키지 import (scripts/ 컨벤션)

KST = timezone(timedelta(hours=9))

FIELDS = ("display_value", "badge", "comparison_label", "review_note",
          "boundary_label", "evidence_links[].file_name")

# 응답표가 끝나고 다른 표가 시작되는 지점. 체크리스트 표에도 '문항 ID' 열이 있고
# 증빙 부록은 `{qid} {문항} -> {display_value}`를 다시 싣는다(exporters/pdf.py).
# 'ISSB'는 문항 본문에도 나와 경계로 쓸 수 없다.
PDF_BODY_END = "제출 전 증빙 체크리스트"
EXCEL_SHEET = "응답서"
EXCEL_FIRST_DATA_ROW = 5          # 헤더가 4행 (exporters/excel.py)
EXCEL_GROUP_PREFIX = "▌"          # 섹션 그룹 헤더 행 (exporters/excel.py)
COL = {"qid": 1, "section": 2, "question_text": 3, "display_value": 4,
       "scope": 5, "badge": 6, "note": 7}

_NUM = re.compile(r"-?\d+(?:\.\d+)?")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d\d\d)")


class InputError(Exception):
    """입력 파일이 없거나 구조가 다르다 — 종료 코드 2."""


# ── 정규화 ────────────────────────────────────────────────────────────────────
# 글리프 치환은 `output/validation/numeric_scope_output_20261005/tools/check_outputs.py`의
# `glyph_flat`(69-73행)에서 가져왔다: PDF 내보내기는 글꼴에 없는 ÷·×·→를 /·x·->로 그린다.
_GLYPHS = (("÷", "/"), ("×", "x"), ("→", "->"))


def normalize(text: object) -> str:
    """공백·줄바꿈·천 단위 구분 기호만 지운다. 그 밖의 변형은 하지 않는다."""
    return _normalize_with_offsets(text)[0]


def _normalize_with_offsets(text: object) -> tuple[str, list[int]]:
    """정규화 문자열과, 각 문자가 원문 몇 번째에서 왔는지의 목록.

    PDF 페이지 번호를 되찾으려면 원문 위치가 필요하다. 글리프 치환은 길이가 바뀌므로
    (→ 는 -> 로 2자가 된다) 치환된 문자 전체가 같은 원문 위치를 가리키게 둔다.
    """
    out: list[str] = []
    offsets: list[int] = []
    raw = str(text or "")
    for i, ch in enumerate(raw):
        if ch.isspace():
            continue
        for src, dst in _GLYPHS:
            if ch == src:
                ch = dst
                break
        out.extend(ch)
        offsets.extend([i] * len(ch))
    # 천 단위 구분 기호 제거는 공백을 지운 뒤에 한다 — PDF에서 '1,\n234'처럼
    # 콤마와 숫자 사이에 줄바꿈이 끼어도 같은 결과가 되게.
    joined = "".join(out)
    keep = [True] * len(joined)
    for m in _THOUSANDS.finditer(joined):
        keep[m.start()] = False
    return ("".join(c for c, k in zip(joined, keep) if k),
            [o for o, k in zip(offsets, keep) if k])


def numbers(text: str) -> list[Decimal]:
    """문자열의 수치 토큰. 문자열 유사도 대신 이 값으로 비교한다(0.513216 ≠ 0.5)."""
    out: list[Decimal] = []
    for tok in _NUM.findall(text):
        try:
            out.append(Decimal(tok))
        except InvalidOperation:
            continue
    return out


def values_match(expected: str, actual: str) -> bool:
    """정규화 후 완전 일치. 수치가 있으면 Decimal 값도 같아야 한다(반올림 허용 없음)."""
    if expected != actual:
        return False
    return numbers(expected) == numbers(actual)


# ── 층 1: 읽기 ────────────────────────────────────────────────────────────────
@dataclass
class JsonRow:
    qid: str
    section: str
    question_text: str
    status: str
    display_value: str
    badge: str
    comparison_label: str
    review_note: str
    boundary_label: str
    evidence_file_names: list[str] = field(default_factory=list)


def load_json_rows(result_json: dict) -> list[JsonRow]:
    """result.json의 `sheet.answers[]` → 비교용 행."""
    try:
        answers = result_json["sheet"]["answers"]
    except (KeyError, TypeError) as exc:
        raise InputError(f"result.json에 sheet.answers가 없다: {exc}") from exc
    rows = []
    for a in answers:
        rows.append(JsonRow(
            qid=str(a.get("qid") or ""),
            section=str(a.get("section") or ""),
            question_text=str(a.get("question_text") or ""),
            status=str(a.get("status") or ""),
            display_value=str(a.get("display_value") or ""),
            badge=str(a.get("badge") or ""),
            comparison_label=str(a.get("comparison_label") or ""),
            review_note=str(a.get("review_note") or ""),
            boundary_label=str(a.get("boundary_label") or ""),
            evidence_file_names=[str(e.get("file_name") or "")
                                 for e in (a.get("evidence_links") or [])],
        ))
    if not rows:
        raise InputError("result.json의 answers가 비어 있다")
    return rows


def load_excel_rows(xlsx_path: str | Path) -> dict[str, dict]:
    """응답서 Excel의 `응답서` 시트 → {qid: {열이름: (값, 셀주소)}}.

    읽기 관례(시트명·시작 행·qid 열)는 check_outputs.py:125-129에서 가져왔다.
    섹션 그룹 헤더 행은 그쪽과 달리 명시적으로 걸러낸다.
    """
    import openpyxl

    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    if EXCEL_SHEET not in wb.sheetnames:
        raise InputError(f"Excel에 '{EXCEL_SHEET}' 시트가 없다: {wb.sheetnames}")
    ws = wb[EXCEL_SHEET]
    rows: dict[str, dict] = {}
    for r in range(EXCEL_FIRST_DATA_ROW, ws.max_row + 1):
        qid_cell = ws.cell(row=r, column=COL["qid"])
        qid = qid_cell.value
        if qid is None or str(qid).startswith(EXCEL_GROUP_PREFIX):
            continue
        rows[str(qid)] = {
            name: (ws.cell(row=r, column=col).value,
                   f"{EXCEL_SHEET}!{ws.cell(row=r, column=col).coordinate}")
            for name, col in COL.items()
        }
    if not rows:
        raise InputError(f"Excel '{EXCEL_SHEET}' 시트에서 문항 행을 찾지 못했다")
    return rows


def extract_pdf_text(pdf_path: str | Path) -> list[str]:
    """응답서 PDF → 페이지별 텍스트(1-기준 페이지 = 인덱스 + 1).

    check_outputs.py:99-104의 `pdf_text`를 변형했다 — 그쪽은 전부 이어 붙이지만
    여기서는 불일치에 페이지 번호를 적어야 하므로 페이지별로 둔다.
    """
    import pymupdf

    with pymupdf.open(pdf_path) as doc:
        return [page.get_text() for page in doc]


# ── 층 2: PDF 텍스트 → 행 ────────────────────────────────────────────────────
@dataclass
class PdfRow:
    qid: str
    text: str          # 정규화된 블록(같은 qid의 '이어지는 행'을 모두 이은 것)
    page: int          # 1-기준, 첫 블록이 있는 페이지
    blocks: int = 1    # 합친 블록 수 — '(이어서)' 행이 있으면 2 이상


def parse_pdf_rows(pages: list[str], qids: list[str]) -> dict[str, PdfRow]:
    """추출된 페이지 텍스트에서 qid별 응답표 블록을 뽑는다.

    qid는 JSON에서 받는다 — PDF 텍스트는 셀을 폭에 맞춰 줄바꿈해서
    `RBA-C-4-E-6-2`가 `RBA-C-4-E-6-\n2`로 쪼개지고, `RBA-C-4-E-6`처럼 접두가
    겹치는 qid도 실제로 있어 정규식으로는 어느 쪽인지 가를 수 없다.
    """
    joined: list[str] = []
    page_of: list[int] = []
    for i, text in enumerate(pages):
        joined.append(text)
        page_of.extend([i + 1] * len(text))
    raw = "".join(joined)

    end = raw.find(PDF_BODY_END)
    body_raw = raw[:end] if end != -1 else raw
    body, offsets = _normalize_with_offsets(body_raw)

    # 긴 qid를 먼저 찾아 접두가 겹치는 짧은 qid가 자리를 먼저 차지하지 않게 한다.
    taken: list[tuple[int, int]] = []
    hits: list[tuple[int, str]] = []
    for qid in sorted(set(qids), key=len, reverse=True):
        needle = normalize(qid)
        if not needle:
            continue
        start = 0
        while True:
            pos = body.find(needle, start)
            if pos == -1:
                break
            if not any(s <= pos < e for s, e in taken):
                hits.append((pos, qid))
                taken.append((pos, pos + len(needle)))
            start = pos + 1
    hits.sort()

    out: dict[str, PdfRow] = {}
    for i, (pos, qid) in enumerate(hits):
        stop = hits[i + 1][0] if i + 1 < len(hits) else len(body)
        chunk = body[pos:stop]
        page = page_of[offsets[pos]] if pos < len(offsets) else 0
        if qid in out:
            # '(이어서)' 행 — 같은 qid가 쪼개져 여러 행에 그려졌다(exporters/pdf.py:312-316).
            prev = out[qid]
            out[qid] = PdfRow(qid, prev.text + chunk, prev.page, prev.blocks + 1)
        else:
            out[qid] = PdfRow(qid, chunk, page)
    return out


# ── 층 3: 비교 ────────────────────────────────────────────────────────────────
@dataclass
class OutputBundle:
    """검사 대상 산출물의 메모리 사본. 주입 검사는 이 사본만 바꾼다.

    check_outputs.py:75-93의 `Bundle`과 같은 역할이다.
    """
    json_rows: list[JsonRow]
    excel_rows: dict[str, dict]
    pdf_pages: list[str]
    paths: dict[str, str] = field(default_factory=dict)


def _pdf_status_label(status: str) -> str:
    """PDF 신뢰 칸에 그려지는 라벨. 이모지가 없고 draft_ready 라벨도 badge와 다르다."""
    from esgenie.supplychain.exporters.pdf import _STATUS_STYLE

    return _STATUS_STYLE.get(status, (status, ""))[0]


@dataclass
class Mismatch:
    qid: str
    field: str
    json_value: str
    excel_value: str | None = None
    excel_cell: str | None = None
    pdf_value: str | None = None
    pdf_page: int | None = None
    note: str = ""

    def to_dict(self) -> dict:
        return {"qid": self.qid, "field": self.field, "json_value": self.json_value,
                "excel_value": self.excel_value, "excel_cell": self.excel_cell,
                "pdf_value": self.pdf_value, "pdf_page": self.pdf_page, "note": self.note}


class _Cursor:
    """블록 텍스트를 앞에서부터 차례로 소비한다 — 값만 어딘가 있으면 통과하는 것이 아니라
    표의 칸 순서(qid·섹션·문항·답변·범위·신뢰·근거)대로 나와야 통과한다."""

    def __init__(self, text: str):
        self.text = text
        self.pos = 0

    def consume(self, needle: str) -> bool:
        if not needle:
            return True
        found = self.text.find(needle, self.pos)
        if found == -1:
            return False
        self.pos = found + len(needle)
        return True


def compare(bundle: OutputBundle) -> dict:
    """행·필드별로 JSON ↔ Excel ↔ PDF를 대조한다."""
    pdf_rows = parse_pdf_rows(bundle.pdf_pages, [r.qid for r in bundle.json_rows])
    mismatches: list[Mismatch] = []
    unchecked: list[dict] = []
    checked_fields = 0

    for row in bundle.json_rows:
        xl = bundle.excel_rows.get(row.qid)
        pdf = pdf_rows.get(row.qid)
        if xl is None:
            mismatches.append(Mismatch(row.qid, "row", "있음", excel_value="없음"))
        if pdf is None:
            mismatches.append(Mismatch(row.qid, "row", "있음", pdf_value="없음"))
        if xl is None or pdf is None:
            continue

        cur = _Cursor(pdf.text)
        # 표 칸 순서를 맞추기 위해 앞쪽 칸부터 소비한다(이 칸들은 검사 대상이 아니다).
        for anchor in (row.qid, row.section, row.question_text):
            cur.consume(normalize(anchor))

        draft = row.status == "draft_ready"

        # ── display_value ──
        if draft:
            unchecked.append({"qid": row.qid, "field": "display_value",
                              "reason": "status=draft_ready — 답변 칸에 draft_lines가 들어간다"
                                        " (exporters/excel.py:126-129, exporters/pdf.py:292-296)"})
        else:
            checked_fields += 1
            want = normalize(row.display_value)
            got_xl = normalize(xl["display_value"][0])
            if not values_match(want, got_xl):
                mismatches.append(Mismatch(
                    row.qid, "display_value", row.display_value,
                    excel_value=str(xl["display_value"][0]), excel_cell=xl["display_value"][1],
                    note=_digit_note(want, got_xl)))
            if not cur.consume(want):
                mismatches.append(Mismatch(
                    row.qid, "display_value", row.display_value,
                    pdf_value="해당 위치에 없음", pdf_page=pdf.page,
                    note=_digit_note_in(want, pdf.text)))

        # ── boundary_label / comparison_label (둘 다 E열 scope_line 안) ──
        for fname, value, prefix in (("boundary_label", row.boundary_label, "측정 범위:"),
                                     ("comparison_label", row.comparison_label, "")):
            if not value:
                unchecked.append({"qid": row.qid, "field": fname,
                                  "reason": "JSON 값이 빈 문자열 — 내보내기에 실리지 않는다"})
                continue
            checked_fields += 1
            want = normalize(prefix + value)
            got_xl = normalize(xl["scope"][0])
            if want not in got_xl:
                mismatches.append(Mismatch(
                    row.qid, fname, prefix + value,
                    excel_value=str(xl["scope"][0]), excel_cell=xl["scope"][1]))
            if not cur.consume(want):
                mismatches.append(Mismatch(
                    row.qid, fname, prefix + value,
                    pdf_value="해당 위치에 없음", pdf_page=pdf.page))

        # ── badge ──
        checked_fields += 1
        want_xl = normalize(row.badge)
        got_xl = normalize(xl["badge"][0])
        if want_xl != got_xl:
            mismatches.append(Mismatch(
                row.qid, "badge", row.badge,
                excel_value=str(xl["badge"][0]), excel_cell=xl["badge"][1]))
        pdf_label = _pdf_status_label(row.status)
        if not cur.consume(normalize(pdf_label)):
            mismatches.append(Mismatch(
                row.qid, "badge", row.badge, pdf_value="해당 위치에 없음", pdf_page=pdf.page,
                note=f"PDF는 이모지 없이 '{pdf_label}'만 그린다(exporters/pdf.py:35-43)"))
        unchecked.append({"qid": row.qid, "field": "badge(이모지)",
                          "reason": "PDF 신뢰 칸은 _STATUS_STYLE 라벨만 그린다 — 이모지가 없다"})

        # ── review_note ──
        if not row.review_note:
            unchecked.append({"qid": row.qid, "field": "review_note",
                              "reason": "JSON 값이 빈 문자열 — 내보내기에 실리지 않는다"})
        else:
            checked_fields += 1
            want = normalize(row.review_note)
            got_xl = normalize(xl["note"][0])
            if want not in got_xl:
                mismatches.append(Mismatch(
                    row.qid, "review_note", row.review_note,
                    excel_value=str(xl["note"][0])[:200], excel_cell=xl["note"][1]))
            if not cur.consume(want):
                mismatches.append(Mismatch(
                    row.qid, "review_note", row.review_note,
                    pdf_value="해당 위치에 없음", pdf_page=pdf.page))

        # ── evidence_links[].file_name ──
        if not row.evidence_file_names:
            unchecked.append({"qid": row.qid, "field": "evidence_links[].file_name",
                              "reason": "근거 링크가 없다"})
        else:
            got_xl = normalize(xl["note"][0])
            for name in row.evidence_file_names:
                checked_fields += 1
                want = normalize(name)
                if want not in got_xl:
                    mismatches.append(Mismatch(
                        row.qid, "evidence_links[].file_name", name,
                        excel_value=str(xl["note"][0])[:200], excel_cell=xl["note"][1]))
                if not cur.consume(want):
                    mismatches.append(Mismatch(
                        row.qid, "evidence_links[].file_name", name,
                        pdf_value="해당 위치에 없음", pdf_page=pdf.page))

    extra_excel = sorted(set(bundle.excel_rows) - {r.qid for r in bundle.json_rows})
    extra_pdf = sorted(set(pdf_rows) - {r.qid for r in bundle.json_rows})
    for qid in extra_excel:
        mismatches.append(Mismatch(qid, "row", "없음", excel_value="있음"))
    for qid in extra_pdf:
        mismatches.append(Mismatch(qid, "row", "없음", pdf_value="있음"))

    continued = {q: r.blocks for q, r in pdf_rows.items() if r.blocks > 1}
    return {
        "summary": {
            "json_rows": len(bundle.json_rows),
            "excel_rows": len(bundle.excel_rows),
            "pdf_rows": len(pdf_rows),
            "fields": len(FIELDS),
            "checked_field_values": checked_fields,
            "mismatches": len(mismatches),
            "unchecked": len(unchecked),
            "pdf_continued_rows": continued,
        },
        "mismatches": [m.to_dict() for m in mismatches],
        "unchecked": unchecked,
    }


_TRUNCATED = "digit_truncation 의심: 표시 자릿수가 줄었다(규칙을 완화하지 않고 불일치로 둔다)"


def _digit_note(want: str, got: str) -> str:
    """자릿수를 줄여 표시한 경우를 표시만 한다 — 비교 규칙은 바꾸지 않는다."""
    wn, gn = numbers(want), numbers(got)
    if not wn or not gn or wn == gn or len(wn) != len(gn):
        return ""
    # 기대값의 글자 앞부분이 실제값인 쌍이 하나라도 있으면 축약으로 본다(0.513216 → 0.5).
    if any(w != g and str(w).startswith(str(g)) for w, g in zip(wn, gn)):
        return _TRUNCATED
    return ""


def _digit_note_in(want: str, haystack: str) -> str:
    """PDF 쪽 — 전체 값은 없는데 그 앞부분만 보이면 축약으로 본다."""
    wn = numbers(want)
    if not wn:
        return ""
    text = str(wn[0])
    for cut in range(len(text) - 1, 0, -1):
        head = text[:cut]
        if head.endswith("."):
            continue
        if head in haystack:
            return _TRUNCATED
    return ""


# ── 적재 / 보고 ───────────────────────────────────────────────────────────────
def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _only(pattern: str, what: str) -> Path:
    found = sorted(glob.glob(pattern))
    if not found:
        raise InputError(f"{what}를 찾지 못했다: {pattern}")
    return Path(found[0])


def load_bundle(run_dir: Path) -> OutputBundle:
    """<run>/<stage> 폴더에서 result.json·응답서 Excel·응답서 PDF를 읽는다."""
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise InputError(f"--run-dir가 폴더가 아니다: {run_dir}")
    result_path = run_dir / "result.json"
    if not result_path.exists():
        raise InputError(f"result.json이 없다: {result_path}")
    sheet_dir = run_dir / "exports" / "response_sheet"
    xlsx = _only(str(sheet_dir / "*.xlsx"), "응답서 Excel")
    pdf = _only(str(sheet_dir / "*.pdf"), "응답서 PDF")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    return OutputBundle(
        json_rows=load_json_rows(result),
        excel_rows=load_excel_rows(xlsx),
        pdf_pages=extract_pdf_text(pdf),
        paths={"result_json": str(result_path), "sheet_xlsx": str(xlsx), "sheet_pdf": str(pdf)},
    )


def report(run_dir: Path, bundle: OutputBundle, outcome: dict) -> dict:
    inputs = {}
    for key, raw in bundle.paths.items():
        p = Path(raw)
        inputs[key] = {"path": str(p), "sha256": _sha256(p) if p.exists() else None}
    return {
        "run_dir": str(run_dir),
        "checked_at": datetime.now(KST).isoformat(),
        "contract": "docs/UI연결용_수치범위_출력계약_2026-10-05.md §2",
        "fields": list(FIELDS),
        "inputs": inputs,
        **outcome,
    }


def to_markdown(payload: dict) -> str:
    s = payload["summary"]
    lines = [
        "# 출력 일치 검사 (result.json ↔ 응답서 Excel ↔ 응답서 PDF)",
        "",
        f"- 실행 폴더: `{payload['run_dir']}`",
        f"- 검사 시각(KST): {payload['checked_at']}",
        f"- 기준: {payload['contract']}",
        "",
        "## 요약",
        "",
        "| 항목 | 값 |",
        "|---|---|",
        f"| JSON 행 수 | {s['json_rows']} |",
        f"| Excel 행 수 | {s['excel_rows']} |",
        f"| PDF 행 수 | {s['pdf_rows']} |",
        f"| 대조 필드 수 | {s['fields']} |",
        f"| 실제 비교한 필드 값 수 | {s['checked_field_values']} |",
        f"| **불일치** | **{s['mismatches']}** |",
        f"| 검사 불가 | {s['unchecked']} |",
        f"| PDF 이어지는 행 | {s['pdf_continued_rows'] or '없음'} |",
        "",
    ]
    if payload["mismatches"]:
        lines += ["## 불일치", "",
                  "| qid | 필드 | JSON | Excel | Excel 셀 | PDF | PDF 쪽(1-기준) | 비고 |",
                  "|---|---|---|---|---|---|---|---|"]
        for m in payload["mismatches"]:
            lines.append("| {qid} | {field} | {j} | {e} | {c} | {p} | {pg} | {n} |".format(
                qid=m["qid"], field=m["field"], j=_cell(m["json_value"]), e=_cell(m["excel_value"]),
                c=m["excel_cell"] or "—", p=_cell(m["pdf_value"]), pg=m["pdf_page"] or "—",
                n=m["note"] or ""))
        lines.append("")
    else:
        lines += ["## 불일치", "", "없음.", ""]
    if payload["unchecked"]:
        from collections import Counter
        counts = Counter((u["field"], u["reason"]) for u in payload["unchecked"])
        lines += ["## 검사 불가 (임의로 빼지 않고 이유를 적는다)", "",
                  "| 필드 | 이유 | 행 수 |", "|---|---|---|"]
        for (fld, reason), n in sorted(counts.items()):
            lines.append(f"| {fld} | {reason} | {n} |")
        lines.append("")
    return "\n".join(lines)


def _cell(value: object) -> str:
    text = str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ")
    return text[:80] + "…" if len(text) > 80 else text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", type=Path, required=True, help="<run>/<stage> 폴더")
    ap.add_argument("--out", type=Path, required=True, help="consistency.json·.md를 쓸 폴더")
    args = ap.parse_args()
    try:
        bundle = load_bundle(args.run_dir)
        payload = report(args.run_dir, bundle, compare(bundle))
    except InputError as exc:
        print(f"입력 오류: {exc}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "consistency.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out / "consistency.md").write_text(to_markdown(payload), encoding="utf-8")
    s = payload["summary"]
    print(json.dumps({"mismatches": s["mismatches"], "checked_field_values":
                      s["checked_field_values"], "unchecked": s["unchecked"],
                      "out": str(args.out)}, ensure_ascii=False))
    return 1 if s["mismatches"] else 0


if __name__ == "__main__":
    sys.exit(main())
