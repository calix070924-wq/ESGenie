#!/usr/bin/env python3
"""같은 실행의 result.json(화면 원천)·응답서 Excel·응답서 PDF가 문항별로 같은 내용을 보이는지 판정한다.

두 가지를 함께 본다 — 하나만으로는 구멍이 난다.
  (1) 필드 검사: 출력 계약 §2의 6개 필드가 칸 안에 제대로 실렸는가
      (display_value · badge · comparison_label · review_note · boundary_label
       · evidence_links[].file_name)
      → 렌더러가 필드를 빠뜨린 경우를 잡는다.
  (2) 칸 전체 일치: 그 칸에 **그 밖의 내용이 없는가**
      → 칸에 내용이 더해진 경우를 잡는다. 기대 칸 문자열은 exporters가 실제로 쓰는
        render.scope_line()·note_lines()와 pdf.py의 _STATUS_STYLE·_fmt_evidence·
        _build_evidence_index·pdf_safe_text로 만든다(같은 함수를 쓰므로 표기가 갈리지 않는다).

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
from dataclasses import dataclass, field, fields as dataclass_fields
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

# 칸 → 그 칸이 싣는 6개 필드(불일치에 어느 필드 때문인지 적기 위한 대응표)
CELL_FIELDS = {
    "answer": ("display_value",),
    "scope": ("boundary_label", "comparison_label"),
    "badge": ("badge",),
    "note": ("review_note", "evidence_links[].file_name"),
}

# 응답표가 끝나고 다른 표가 시작되는 지점. 체크리스트 표에도 '문항 ID' 열이 있고
# 증빙 부록은 `{qid} {문항} -> {display_value}`를 다시 싣는다(exporters/pdf.py).
# 'ISSB'는 문항 본문에도 나와 경계로 쓸 수 없다.
PDF_BODY_END = "제출 전 증빙 체크리스트"
# 증빙 부록 제목. 이것이 있으면 exporters가 figures를 그렸다는 뜻이고, 그때만
# 근거 칸에 [E#] 상호참조가 붙는다(exporters/pdf.py:211-215, :357).
PDF_APPENDIX_MARK = "증빙 부록"
PDF_CONTINUED = "(이어서)"          # 이어지는 행의 문항 칸 (exporters/pdf.py:314)
EXCEL_SHEET = "응답서"
EXCEL_FIRST_DATA_ROW = 5          # 헤더가 4행 (exporters/excel.py:75)
EXCEL_GROUP_PREFIX = "▌"          # 섹션 그룹 헤더 행 (exporters/excel.py:115)
COL = {"qid": 1, "section": 2, "question_text": 3, "answer": 4,
       "scope": 5, "badge": 6, "note": 7}

_NUM = re.compile(r"-?\d+(?:\.\d+)?")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d\d\d)")
_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)


class InputError(Exception):
    """입력 파일이 없거나 구조가 다르다 — 종료 코드 2."""


# ── 정규화 ────────────────────────────────────────────────────────────────────
def normalize(text: object) -> str:
    """공백·줄바꿈·천 단위 구분 기호만 지운다. 그 밖의 변형은 하지 않는다."""
    return _normalize_with_offsets(text)[0]


def _normalize_with_offsets(text: object) -> tuple[str, list[int]]:
    """정규화 문자열과, 각 문자가 원문 몇 번째에서 왔는지의 목록.

    PDF 페이지 번호를 되찾으려면 원문 위치가 필요하다.
    """
    raw = str(text or "")
    out = [c for c in raw if not c.isspace()]
    offs = [i for i, c in enumerate(raw) if not c.isspace()]
    # 천 단위 구분 기호 제거는 공백을 지운 뒤에 한다 — PDF에서 '1,\n234'처럼
    # 콤마와 숫자 사이에 줄바꿈이 끼어도 같은 결과가 되게.
    joined = "".join(out)
    drop = {m.start() for m in _THOUSANDS.finditer(joined)}
    return ("".join(c for i, c in enumerate(joined) if i not in drop),
            [o for i, o in enumerate(offs) if i not in drop])


def normalize_pdf(text: object) -> str:
    """PDF에 실제로 그려지는 형태로 맞춘 뒤 정규화한다.

    `exporters/_fonts.pdf_safe_text`를 그대로 쓴다 — 번들 폰트에 없는 기호를
    ASCII로 바꾸고(→ 는 ->, ÷ 는 /, × 는 x, ↔ 는 vs …) 이모지를 지우는 함수다.
    직접 치환표를 두면 exporters가 바뀔 때 갈린다.
    `<br/>`는 근거·답변 칸의 줄 구분자로, 추출 텍스트에는 태그가 남지 않는다.
    """
    from esgenie.supplychain.exporters._fonts import pdf_safe_text

    return normalize(pdf_safe_text(_BR.sub("\n", str(text or ""))))


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
    """6개 필드 검사에 쓰는 값 + 칸 기대값을 만들 Answer 객체."""
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
    answer: object = None           # schema.Answer — 기대 칸 문자열 생성용


def _restore(cls, data: dict):
    """dataclass 필드만 골라 되살린다. to_dict가 덧붙인 파생 키(badge·display_value 등)는
    필드가 아니므로 자연히 걸러진다. schema.py·render.py는 import만 하고 고치지 않는다."""
    names = {f.name for f in dataclass_fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


def restore_sheet(result_json: dict):
    """result.json의 sheet → schema.ResponseSheet (exporters와 같은 모델)."""
    from esgenie.ssot.audit_trace import EvidenceLink
    from esgenie.supplychain.schema import Answer, ResponseSheet

    try:
        raw = result_json["sheet"]
    except (KeyError, TypeError) as exc:
        raise InputError(f"result.json에 sheet가 없다: {exc}") from exc
    answers = []
    for a in raw.get("answers") or []:
        data = dict(a)
        for key in ("evidence_links", "reference_links"):
            data[key] = [_restore(EvidenceLink, e) for e in (data.get(key) or [])]
        answers.append(_restore(Answer, data))
    sheet = _restore(ResponseSheet, {
        "framework_key": raw.get("framework_key") or "",
        "framework_label": raw.get("framework_label") or "",
        "corp_name": raw.get("corp_name") or "",
        "answers": answers,
        "gaps": raw.get("gaps") or [],
    })
    return sheet


def load_json_rows(result_json: dict) -> list[JsonRow]:
    """result.json의 `sheet.answers[]` → 비교용 행."""
    sheet = restore_sheet(result_json)
    rows = []
    for a in sheet.answers:
        rows.append(JsonRow(
            qid=str(a.qid or ""), section=str(a.section or ""),
            question_text=str(a.question_text or ""), status=str(a.status or ""),
            display_value=str(a.display_value or ""), badge=str(a.badge or ""),
            comparison_label=str(a.comparison_label or ""),
            review_note=str(a.review_note or ""),
            boundary_label=str(a.boundary_label or ""),
            evidence_file_names=[str(e.file_name or "") for e in a.evidence_links],
            answer=a,
        ))
    if not rows:
        raise InputError("result.json의 answers가 비어 있다")
    return rows


def load_excel_rows(xlsx_path: str | Path) -> dict[str, dict]:
    """응답서 Excel의 `응답서` 시트 → {qid: {칸이름: (값, "응답서!D6")}}.

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
        qid = ws.cell(row=r, column=COL["qid"]).value
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


# ── 층 2: 기대 칸 문자열 ─────────────────────────────────────────────────────
def expected_cells(answer, *, fig_map: dict | None = None) -> dict[str, dict[str, str]]:
    """exporters가 각 칸에 넣는 문자열. excel.py·pdf.py의 조합을 그대로 재현한다.

    Excel (exporters/excel.py:123-135)      PDF (exporters/pdf.py:291-310)
      D 답변  draft면 "\\n".join(draft_lines)  draft면 "<br/>".join(draft_lines)
              아니면 display_value             아니면 display_value
      E 범위  scope_line(a) or "—"            scope_line(a) or "—"
      F 신뢰  a.badge (이모지 포함)            _STATUS_STYLE[status] 라벨 (이모지 없음)
      G 근거  "\\n".join(note_lines(a)).strip()  "<br/>".join(note_lines(a, fig_map)) or "—"

    근거 칸만 두 쪽이 다르다 — PDF는 fig_map이 있으면 링크마다 ` → [E#]`를 덧붙인다.
    """
    from esgenie.supplychain.exporters.pdf import _STATUS_STYLE
    from esgenie.supplychain.render import draft_lines, note_lines, scope_line

    draft = draft_lines(answer) if answer.status == "draft_ready" else []
    scope = scope_line(answer) or "—"
    excel_note = "\n".join(note_lines(answer)).strip()
    pdf_note = "<br/>".join(note_lines(answer, fig_map=fig_map)) or "—"
    return {
        "answer": {"excel": "\n".join(draft) if draft else answer.display_value,
                   "pdf": ("<br/>".join(x.replace("\n", "<br/>") for x in draft)
                           if draft else answer.display_value)},
        "scope": {"excel": scope, "pdf": scope},
        "badge": {"excel": answer.badge,
                  "pdf": _STATUS_STYLE.get(answer.status, (answer.status, ""))[0]},
        "note": {"excel": excel_note, "pdf": pdf_note},
    }


def build_fig_map(sheet, pdf_pages: list[str], base_dir: Path | None):
    """PDF 근거 칸의 [E#] 상호참조 맵. 부록이 없는 PDF면 비어 있다.

    exporters는 `embed_evidence`가 참이고 렌더 가능한 증빙이 있을 때만 figures를
    만들고, figures가 있을 때만 부록을 그린다(pdf.py:211-215, :357). 그래서
    산출물에 부록 제목이 있는지로 둘을 가른다 — 산출물에서 역으로 읽은 근거다.
    """
    if base_dir is None:
        return {}
    mark = normalize(PDF_APPENDIX_MARK)
    if not any(mark in normalize(p) for p in pdf_pages):
        return {}
    from esgenie.supplychain.exporters.pdf import _build_evidence_index

    try:
        return _build_evidence_index(sheet, Path(base_dir))[1]
    except Exception:  # noqa: BLE001 — exporters와 같은 방어(pdf.py:214-215)
        return {}


@dataclass
class PdfRow:
    qid: str
    text: str          # 정규화된 블록(같은 qid의 '이어지는 행'을 모두 이은 것)
    page: int          # 1-기준, 첫 블록이 있는 페이지
    blocks: int = 1    # 합친 블록 수 — '(이어서)' 행이 있으면 2 이상


def _table_header() -> str:
    """응답표의 페이지 반복 머리글. `Table(..., repeatRows=1)`이라 쪽마다 다시 그려진다.

    pdf.py:251의 `header`는 함수 안 지역 변수라 import할 수 없고, excel.py:14의
    `_HEADER`와 같은 7개다(두 exporter가 같은 표를 그린다). 하드코딩 대신 그쪽을 쓴다.
    """
    from esgenie.supplychain.exporters.excel import _HEADER

    return normalize_pdf("".join(_HEADER))


def _group_header_re(sections: set[str]) -> re.Pattern | None:
    """섹션 그룹 헤더 행. `▌ {섹션}    (자동응답 n/m[ · 검토필요 k건])` (pdf.py:284-286).

    `▌`(U+258C)는 pdf_safe_text가 지운다. 섹션 이름은 JSON에서 받는다.
    """
    alts = [re.escape(normalize_pdf(s)) for s in sorted(sections, key=len, reverse=True) if s]
    if not alts:
        return None
    return re.compile(r"(?:" + "|".join(alts) + r")\(자동응답\d+/\d+(?:·검토필요\d+건)?\)")


def _trim_block(text: str, header: str, group_re: re.Pattern | None) -> str:
    """블록 뒤에 붙은 그룹 헤더·반복 머리글을 떼어낸다.

    블록은 '다음 qid 앞까지'로 자르므로, 그 사이에 끼는 섹션 그룹 헤더 행과 쪽마다
    다시 그려지는 표 머리글이 함께 들어온다. 응답 칸의 내용이 아니다.
    """
    cuts = []
    if header:
        pos = text.find(header)
        if pos > 0:
            cuts.append(pos)
    if group_re is not None:
        m = group_re.search(text)
        if m and m.start() > 0:
            cuts.append(m.start())
    return text[:min(cuts)] if cuts else text


def parse_pdf_rows(pages: list[str], qids: list[str],
                   sections: set[str] | None = None) -> dict[str, PdfRow]:
    """추출된 페이지 텍스트에서 qid별 응답표 블록을 뽑는다.

    qid는 JSON에서 받는다 — PDF 텍스트는 셀을 폭에 맞춰 줄바꿈해서
    `RBA-C-4-E-6-2`가 `RBA-C-4-E-6-\\n2`로 쪼개지고, `RBA-C-4-E-6`처럼 접두가
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

    header = _table_header()
    group_re = _group_header_re(sections or set())
    out: dict[str, PdfRow] = {}
    for i, (pos, qid) in enumerate(hits):
        stop = hits[i + 1][0] if i + 1 < len(hits) else len(body)
        chunk = _trim_block(body[pos:stop], header, group_re)
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
    evidence_base_dir: str | None = None   # PDF [E#] 맵 재현용(응답서 PDF가 있는 폴더)

    def sheet(self):
        from esgenie.supplychain.schema import ResponseSheet

        return ResponseSheet(framework_key="", framework_label="", corp_name="",
                             answers=[r.answer for r in self.json_rows])


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
    cell_exact: bool = False               # 칸 전체 일치 검사에서 나온 불일치인가
    related_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"qid": self.qid, "field": self.field, "json_value": self.json_value,
                "excel_value": self.excel_value, "excel_cell": self.excel_cell,
                "pdf_value": self.pdf_value, "pdf_page": self.pdf_page,
                "note": self.note, "cell_exact": self.cell_exact,
                "related_fields": self.related_fields}


class _Cursor:
    """필드 검사용 — 표의 칸 순서대로 앞에서부터 찾아 소비한다."""

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


def _blame(want: str, got: str, cell: str, row: JsonRow, *, pdf: bool) -> list[str]:
    """칸 불일치가 6개 필드 중 어느 것 때문인지 — 그 칸에 실린 값이 실제 칸에 없으면 지목한다."""
    norm = normalize_pdf if pdf else normalize
    out = []
    for name in CELL_FIELDS.get(cell, ()):
        if name == "evidence_links[].file_name":
            values = row.evidence_file_names
        elif name == "boundary_label":
            values = [f"측정 범위: {row.boundary_label}"] if row.boundary_label else []
        elif name == "badge":
            values = [row.badge] if not pdf else []
        else:
            values = [getattr(row, name, "")]
        for value in values:
            if value and norm(value) not in got:
                out.append(name)
                break
    return out


def compare(bundle: OutputBundle) -> dict:
    """행·필드별로 JSON ↔ Excel ↔ PDF를 대조한다."""
    pdf_rows = parse_pdf_rows(bundle.pdf_pages, [r.qid for r in bundle.json_rows],
                              {r.section for r in bundle.json_rows})
    fig_map = build_fig_map(bundle.sheet(), bundle.pdf_pages, bundle.evidence_base_dir)
    mismatches: list[Mismatch] = []
    unchecked: list[dict] = []
    checked_fields = 0
    checked_cells = 0

    for row in bundle.json_rows:
        xl = bundle.excel_rows.get(row.qid)
        pdf = pdf_rows.get(row.qid)
        if xl is None:
            mismatches.append(Mismatch(row.qid, "row", "있음", excel_value="없음"))
        if pdf is None:
            mismatches.append(Mismatch(row.qid, "row", "있음", pdf_value="없음"))
        if xl is None or pdf is None:
            continue

        want = expected_cells(row.answer, fig_map=fig_map)

        # ── (2) 칸 전체 일치 — 칸에 그 밖의 내용이 없는지 ──────────────────
        for cell in ("answer", "scope", "badge", "note"):
            checked_cells += 1
            exp = normalize(want[cell]["excel"])
            got = normalize(xl[cell][0])
            if not values_match(exp, got):
                mismatches.append(Mismatch(
                    row.qid, f"cell:{cell}", want[cell]["excel"],
                    excel_value=str(xl[cell][0]), excel_cell=xl[cell][1],
                    note=_cell_note(exp, got), cell_exact=True,
                    related_fields=_blame(exp, got, cell, row, pdf=False)))

        # PDF는 블록 안에서 칸이 커서 위치에서 '바로 시작'해야 한다 — find로 뒤를
        # 훑으면 다음 칸의 내용을 소비해 변조를 놓친다(검사자 재현: 답변 '—'→'7').
        pos = 0
        ok = True
        for cell in ("qid", "section", "question_text", "answer", "scope", "badge"):
            exp = normalize_pdf(getattr(row, cell, None) if cell in
                                ("qid", "section", "question_text") else want[cell]["pdf"])
            if cell in ("answer", "scope", "badge"):
                checked_cells += 1
            if not pdf.text.startswith(exp, pos):
                tail = pdf.text[pos:pos + max(len(exp) + 20, 40)]
                detail = _cell_note(exp, pdf.text[pos:pos + len(exp)])
                mismatches.append(Mismatch(
                    row.qid, f"cell:{cell}", exp,
                    pdf_value=tail, pdf_page=pdf.page, cell_exact=True,
                    note=f"커서 위치에서 바로 시작하지 않는다. {detail}".strip(),
                    related_fields=_blame(exp, pdf.text[pos:], cell, row, pdf=True)))
                ok = False
                break
            pos += len(exp)
        if ok:
            checked_cells += 1
            rest = pdf.text[pos:]
            # 이어지는 행의 머리(qid·섹션·'(이어서)')는 같은 문항의 연속 표시다.
            head = normalize_pdf(row.qid) + normalize_pdf(row.section) + normalize_pdf(PDF_CONTINUED)
            rest = rest.replace(head, "")
            exp_note = normalize_pdf(want["note"]["pdf"])
            if not values_match(exp_note, rest):
                mismatches.append(Mismatch(
                    row.qid, "cell:note", want["note"]["pdf"],
                    pdf_value=rest, pdf_page=pdf.page, cell_exact=True,
                    note=_cell_note(exp_note, rest),
                    related_fields=_blame(exp_note, rest, "note", row, pdf=True)))

        # ── (1) 필드 검사 — 6개 필드가 칸에 실렸는지 ───────────────────────
        cur = _Cursor(pdf.text)
        for anchor in (row.qid, row.section, row.question_text):
            cur.consume(normalize_pdf(anchor))

        if row.status == "draft_ready":
            unchecked.append({"qid": row.qid, "field": "display_value",
                              "reason": "status=draft_ready — 답변 칸에 draft_lines가 들어간다"
                                        " (excel.py:126-129, pdf.py:292-296). 칸 전체 일치로는 검사된다"})
        else:
            checked_fields += 1
            exp = normalize(row.display_value)
            got = normalize(xl["answer"][0])
            if not values_match(exp, got):
                mismatches.append(Mismatch(
                    row.qid, "display_value", row.display_value,
                    excel_value=str(xl["answer"][0]), excel_cell=xl["answer"][1],
                    note=_cell_note(exp, got)))
            if not cur.consume(normalize_pdf(row.display_value)):
                mismatches.append(Mismatch(
                    row.qid, "display_value", row.display_value,
                    pdf_value="해당 위치에 없음", pdf_page=pdf.page))

        for fname, value, prefix in (("boundary_label", row.boundary_label, "측정 범위: "),
                                     ("comparison_label", row.comparison_label, "")):
            if not value:
                continue   # 빈 값은 칸 전체 일치가 '아무것도 더 찍히지 않았는지'로 검사한다
            checked_fields += 1
            exp = normalize(prefix + value)
            got = normalize(xl["scope"][0])
            if exp not in got:
                mismatches.append(Mismatch(
                    row.qid, fname, prefix + value,
                    excel_value=str(xl["scope"][0]), excel_cell=xl["scope"][1]))
            if not cur.consume(normalize_pdf(prefix + value)):
                mismatches.append(Mismatch(
                    row.qid, fname, prefix + value,
                    pdf_value="해당 위치에 없음", pdf_page=pdf.page))

        checked_fields += 1
        if normalize(row.badge) != normalize(xl["badge"][0]):
            mismatches.append(Mismatch(
                row.qid, "badge", row.badge,
                excel_value=str(xl["badge"][0]), excel_cell=xl["badge"][1]))
        pdf_label = want["badge"]["pdf"]
        if not cur.consume(normalize_pdf(pdf_label)):
            mismatches.append(Mismatch(
                row.qid, "badge", row.badge, pdf_value="해당 위치에 없음", pdf_page=pdf.page,
                note=f"PDF는 이모지 없이 '{pdf_label}'만 그린다(pdf.py:35-43)"))
        unchecked.append({"qid": row.qid, "field": "badge(이모지)",
                          "reason": "PDF 신뢰 칸은 _STATUS_STYLE 라벨만 그린다 — 이모지가 없다"})

        if row.review_note:
            checked_fields += 1
            exp = normalize(row.review_note)
            if exp not in normalize(xl["note"][0]):
                mismatches.append(Mismatch(
                    row.qid, "review_note", row.review_note,
                    excel_value=str(xl["note"][0])[:200], excel_cell=xl["note"][1]))
            if not cur.consume(normalize_pdf(row.review_note)):
                mismatches.append(Mismatch(
                    row.qid, "review_note", row.review_note,
                    pdf_value="해당 위치에 없음", pdf_page=pdf.page))

        got_note = normalize(xl["note"][0])
        for name in row.evidence_file_names:
            checked_fields += 1
            if normalize(name) not in got_note:
                mismatches.append(Mismatch(
                    row.qid, "evidence_links[].file_name", name,
                    excel_value=str(xl["note"][0])[:200], excel_cell=xl["note"][1]))
            if not cur.consume(normalize_pdf(name)):
                mismatches.append(Mismatch(
                    row.qid, "evidence_links[].file_name", name,
                    pdf_value="해당 위치에 없음", pdf_page=pdf.page))

    for qid in sorted(set(bundle.excel_rows) - {r.qid for r in bundle.json_rows}):
        mismatches.append(Mismatch(qid, "row", "없음", excel_value="있음"))
    for qid in sorted(set(pdf_rows) - {r.qid for r in bundle.json_rows}):
        mismatches.append(Mismatch(qid, "row", "없음", pdf_value="있음"))

    continued = {q: r.blocks for q, r in pdf_rows.items() if r.blocks > 1}
    return {
        "summary": {
            "json_rows": len(bundle.json_rows),
            "excel_rows": len(bundle.excel_rows),
            "pdf_rows": len(pdf_rows),
            "fields": len(FIELDS),
            "checked_field_values": checked_fields,
            "checked_cells": checked_cells,
            "mismatches": len(mismatches),
            "cell_exact_mismatches": sum(1 for m in mismatches if m.cell_exact),
            "unchecked": len(unchecked),
            "pdf_evidence_figures": len(fig_map),
            "pdf_continued_rows": continued,
        },
        "mismatches": [m.to_dict() for m in mismatches],
        "unchecked": unchecked,
    }


_TRUNCATED = "digit_truncation 의심: 표시 자릿수가 줄었다(규칙을 완화하지 않고 불일치로 둔다)"


def _cell_note(want: str, got: str) -> str:
    """불일치의 성질을 한 줄로 — 규칙을 완화하지 않고 표시만 한다."""
    if want and got and got.startswith(want) and len(got) > len(want):
        return f"칸에 내용이 더해졌다: …{got[len(want):][:60]}"
    if want and got and want.startswith(got) and len(want) > len(got):
        return "칸의 내용이 잘렸다"
    wn, gn = numbers(want), numbers(got)
    if wn and gn and wn != gn and len(wn) == len(gn):
        if any(w != g and str(w).startswith(str(g)) for w, g in zip(wn, gn)):
            return _TRUNCATED
    if wn and gn and wn != gn:
        return f"수치가 다르다: {[str(x) for x in wn]} ↔ {[str(x) for x in gn]}"
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
        # exporters는 evidence_base_dir 기본값으로 out_dir(= 응답서 폴더)을 쓴다.
        evidence_base_dir=str(pdf.parent),
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
        "checks": ["필드 검사(6필드가 칸에 실렸는가)", "칸 전체 일치(칸에 그 밖의 내용이 없는가)"],
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
        f"- 검사: {' + '.join(payload['checks'])}",
        "",
        "## 요약",
        "",
        "| 항목 | 값 |",
        "|---|---|",
        f"| JSON 행 수 | {s['json_rows']} |",
        f"| Excel 행 수 | {s['excel_rows']} |",
        f"| PDF 행 수 | {s['pdf_rows']} |",
        f"| 대조 필드 수 | {s['fields']} |",
        f"| 필드 검사 비교 수 | {s['checked_field_values']} |",
        f"| 칸 전체 일치 비교 수 | {s['checked_cells']} |",
        f"| **불일치** | **{s['mismatches']}** (칸 전체 일치 {s['cell_exact_mismatches']}) |",
        f"| 검사 불가 | {s['unchecked']} |",
        f"| PDF 증빙 그림([E#]) | {s['pdf_evidence_figures']} |",
        f"| PDF 이어지는 행 | {s['pdf_continued_rows'] or '없음'} |",
        "",
    ]
    if payload["mismatches"]:
        lines += ["## 불일치", "",
                  "| qid | 항목 | 칸전체 | 관련 필드 | 기대(JSON) | Excel | Excel 셀 | PDF | 쪽 | 비고 |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for m in payload["mismatches"]:
            lines.append("| {q} | {f} | {ce} | {rf} | {j} | {e} | {c} | {p} | {pg} | {n} |".format(
                q=m["qid"], f=m["field"], ce="예" if m["cell_exact"] else "",
                rf=", ".join(m["related_fields"]) or "", j=_cell(m["json_value"]),
                e=_cell(m["excel_value"]), c=m["excel_cell"] or "—",
                p=_cell(m["pdf_value"]), pg=m["pdf_page"] or "—", n=_cell(m["note"])))
        lines.append("")
    else:
        lines += ["## 불일치", "", "없음.", ""]
    if payload["unchecked"]:
        from collections import Counter
        counts = Counter((u["field"], u["reason"]) for u in payload["unchecked"])
        lines += ["## 검사 불가 (정말 비교할 수 없는 것만)", "",
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
    print(json.dumps({"mismatches": s["mismatches"],
                      "cell_exact_mismatches": s["cell_exact_mismatches"],
                      "checked_field_values": s["checked_field_values"],
                      "checked_cells": s["checked_cells"],
                      "unchecked": s["unchecked"], "out": str(args.out)},
                     ensure_ascii=False))
    return 1 if s["mismatches"] else 0


if __name__ == "__main__":
    sys.exit(main())
