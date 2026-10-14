"""ResponseSheet → .xlsx (OEM 제출본).

협력사가 대기업에 제출하는 자가진단 응답서. 각 행 = 문항 1개,
답변 옆에 신뢰 배지·근거·플래그를 함께 실어 '증빙 연결된 응답'임을 드러낸다.
"""
from __future__ import annotations

from pathlib import Path

from ..frameworks import get_framework
from ..render import draft_lines, note_lines, scope_line, summary_line
from ..schema import ResponseSheet

_HEADER = ["문항 ID", "섹션", "문항", "답변", "측정 범위 / 검토", "신뢰", "근거 / 비고"]


def _fmt_evidence(answer) -> str:
    # 표기 규칙은 render.py 한 곳에 둔다 — 화면과 제출본이 같은 문장을 쓰게(§5-1).
    return "\n".join(note_lines(answer)).strip()


def _issb_followup_rows(answers) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for answer in answers:
        issue = ""
        remediation = ""
        for flag in getattr(answer, "flags", []) or []:
            if flag.startswith("ISSB "):
                issue = flag
            elif flag.startswith("보완 증빙: "):
                remediation = flag.removeprefix("보완 증빙: ").strip()
        if not issue or not remediation:
            continue
        rows.append({
            "문항": answer.question_text,
            "ISSB 이슈": issue,
            "권장 증빙": remediation,
            "비고": answer.rationale or "",
        })
    return rows


def export_response_sheet(sheet: ResponseSheet, out_dir: str | Path, *, status_labels: dict[str, str] | None = None, summary_text: str | None = None) -> str:
    """응답서를 xlsx로 저장하고 경로를 반환한다."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        _pillar = get_framework(sheet.framework_key).pillar
    except KeyError:
        _pillar = "due_diligence"
    _prefix = "공시응답서" if _pillar == "disclosure" else "실사응답서"
    out_path = out_dir / f"{_prefix}_{sheet.framework_key}_{sheet.corp_name or 'corp'}.xlsx"

    wb = Workbook()
    ws = wb.active
    ws.title = "응답서"

    # ── 제목/요약 ──
    ws["A1"] = f"{sheet.framework_label}"
    ws["A1"].font = Font(size=13, bold=True)
    # 4분할(자동응답/AI초안/작성필요/증빙대기)을 빠짐없이 적는다 — AI초안이 빠져
    # 합이 100%에 못 미치던 헤더를 고정한다(§5-1).
    ws["A2"] = summary_text if summary_text is not None else summary_line(sheet)
    ws["A2"].font = Font(size=10, color="555555")
    for row in (1, 2):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=7)
        ws.cell(row, 1).alignment = Alignment(wrap_text=True, vertical="center")
    ws.row_dimensions[1].height = 26
    ws.row_dimensions[2].height = 36

    # ── 헤더 ──
    header_row = 4
    fill = PatternFill("solid", fgColor="1F4E78")
    for col, name in enumerate(_HEADER, start=1):
        c = ws.cell(row=header_row, column=col, value=name)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = fill
        c.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)

    # ── 섹션(현대차 영역)별 집계 — 그룹 헤더 요약용 ──
    from collections import defaultdict
    sec_total: dict[str, int] = defaultdict(int)
    sec_auto: dict[str, int] = defaultdict(int)
    sec_flag: dict[str, int] = defaultdict(int)
    for a in sheet.answers:
        if a.status == "not_applicable":
            continue
        sec_total[a.section] += 1
        if a.answered:
            sec_auto[a.section] += 1
        if a.status == "flagged":
            sec_flag[a.section] += 1

    # ── 데이터 행 (영역 그룹 헤더 + 문항) ──
    status_fill = {
        "verified":      PatternFill("solid", fgColor="E2EFDA"),
        "self_reported": PatternFill("solid", fgColor="FFF2CC"),
        "insufficient":  PatternFill("solid", fgColor="F2F2F2"),
        "flagged":       PatternFill("solid", fgColor="FCE4E4"),
        "hitl_required": PatternFill("solid", fgColor="DDEBF7"),  # 작성필요 — 연한 파랑
        "not_applicable": PatternFill("solid", fgColor="EAEAEA"),  # 해당없음 — 회색
        "draft_ready":   PatternFill("solid", fgColor="E8DAEF"),  # AI초안 — 연보라
    }
    group_fill = PatternFill("solid", fgColor="D9E1F2")
    r = header_row + 1
    cur_section: str | None = None
    for a in sheet.answers:
        # 영역이 바뀌면 그룹 헤더 행을 끼운다(영역명 + 영역 요약).
        if a.section != cur_section:
            cur_section = a.section
            flag_note = f" · 🚩 검토필요 {sec_flag[a.section]}건" if sec_flag[a.section] else ""
            label = (f"▌ {a.section}    "
                     f"(자동응답 {sec_auto[a.section]}/{sec_total[a.section]}{flag_note})")
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=len(_HEADER))
            gc = ws.cell(row=r, column=1, value=label)
            gc.font = Font(bold=True, size=11, color="1F4E78")
            gc.fill = group_fill
            gc.alignment = Alignment(vertical="center", horizontal="left")
            r += 1
        ws.cell(row=r, column=1, value=a.qid)
        ws.cell(row=r, column=2, value=a.section)
        ws.cell(row=r, column=3, value=a.question_text).alignment = Alignment(wrap_text=True)
        draft = draft_lines(a) if a.status == "draft_ready" else []
        if draft:
            # 본문 인용은 [1]·문서명·실제 페이지로 — 내부 노드 ID를 제출본에 싣지 않는다(§5-3).
            ws.cell(row=r, column=4, value="\n".join(draft)).alignment = Alignment(wrap_text=True)
        else:
            ws.cell(row=r, column=4, value=a.display_value).alignment = Alignment(wrap_text=True)
        ws.cell(row=r, column=5, value=scope_line(a) or "—").alignment = Alignment(wrap_text=True)
        badge = ws.cell(row=r, column=6, value=(status_labels or {}).get(a.qid, a.badge))
        badge.alignment = Alignment(horizontal="center")
        ws.cell(row=r, column=7, value=_fmt_evidence(a)).alignment = Alignment(wrap_text=True)
        f = status_fill.get(a.status)
        if f:
            for col in range(1, len(_HEADER) + 1):
                ws.cell(row=r, column=col).fill = f
        r += 1

    # 헤더 행 고정 — 스크롤해도 열 제목이 보이게.
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)

    # ── 증빙 체크리스트 시트 (STEP 4: 제출 전 실행 항목) ──
    from ..checklist import checklist_rows
    rows = checklist_rows(sheet)
    if rows:
        cw = wb.create_sheet("증빙 체크리스트")
        cw["A1"] = "제출 전 증빙 체크리스트"
        cw["A1"].font = Font(size=12, bold=True)
        cw["A2"] = "증빙 업로드=문서 올리면 자동 해소 / 담당자 작성=사람이 서술 / 검토·보완=경고 소명"
        cw["A2"].font = Font(size=10, color="555555")
        cw.merge_cells("A1:F1")
        cw.merge_cells("A2:F2")
        cw["A2"].alignment = Alignment(wrap_text=True, vertical="center")
        cw.row_dimensions[2].height = 30
        headers = ["문항 ID", "섹션", "문항", "할 일", "올릴 문서 / 작성 사항", "안내"]
        cfill = PatternFill("solid", fgColor="1F4E78")
        for col, name in enumerate(headers, start=1):
            c = cw.cell(row=4, column=col, value=name)
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = cfill
            c.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)
        action_fill = {
            "증빙 업로드": PatternFill("solid", fgColor="F2F2F2"),
            "담당자 작성": PatternFill("solid", fgColor="DDEBF7"),
            "검토·보완":   PatternFill("solid", fgColor="FCE4E4"),
            "초안 검토":   PatternFill("solid", fgColor="E8DAEF"),
            "범위 확인·보완": PatternFill("solid", fgColor="FFF2CC"),
        }
        for ridx, row in enumerate(rows, start=5):
            for col, key in enumerate(headers, start=1):
                cell = cw.cell(row=ridx, column=col, value=row[key])
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            f = action_fill.get(row["할 일"])
            if f:
                for col in range(1, len(headers) + 1):
                    cw.cell(row=ridx, column=col).fill = f
        for col, width in enumerate((22, 20, 52, 20, 44, 80), start=1):
            cw.column_dimensions[cw.cell(row=4, column=col).column_letter].width = width

    # ── 보완/검토 목록 시트 ──
    if sheet.gaps:
        gw = wb.create_sheet("보완·검토")
        gw["A1"] = "제출 전 보완·검토 항목"
        gw["A1"].font = Font(size=12, bold=True)
        for i, g in enumerate(sheet.gaps, start=3):
            gw.cell(row=i, column=1, value=g).alignment = Alignment(wrap_text=True)
        gw.column_dimensions["A"].width = 90

    issb_rows = _issb_followup_rows(sheet.answers)
    if issb_rows:
        iw = wb.create_sheet("ISSB 보완")
        iw["A1"] = "ISSB/KSSB 보완 항목"
        iw["A1"].font = Font(size=12, bold=True)
        iw["A2"] = "실사 응답서에서 ISSB 기후·그린워싱 방어 관점으로 추가 보완이 필요한 항목"
        iw["A2"].font = Font(size=10, color="555555")
        headers = ["문항", "ISSB 이슈", "권장 증빙", "비고"]
        fill = PatternFill("solid", fgColor="2E7D32")
        for col, name in enumerate(headers, start=1):
            c = iw.cell(row=4, column=col, value=name)
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = fill
            c.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)
        for row_idx, row in enumerate(issb_rows, start=5):
            iw.cell(row=row_idx, column=1, value=row["문항"]).alignment = Alignment(wrap_text=True)
            iw.cell(row=row_idx, column=2, value=row["ISSB 이슈"]).alignment = Alignment(wrap_text=True)
            iw.cell(row=row_idx, column=3, value=row["권장 증빙"]).alignment = Alignment(wrap_text=True)
            iw.cell(row=row_idx, column=4, value=row["비고"]).alignment = Alignment(wrap_text=True)
        for col, width in enumerate((48, 54, 54, 60), start=1):
            iw.column_dimensions[iw.cell(row=4, column=col).column_letter].width = width

    widths = [22, 20, 52, 34, 44, 15, 80]
    for col, w in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=header_row, column=col).column_letter].width = w

    # Excel은 저장 파일의 줄바꿈 행 높이를 자동 계산하지 않는다. 한글 폭과
    # 명시적 줄바꿈으로 높이를 확보해 긴 검토 사유/인용이 셀 아래에서 잘리지 않게 한다.
    import math
    import unicodedata
    for tab in (ws, wb["증빙 체크리스트"] if "증빙 체크리스트" in wb.sheetnames else ws):
        merged_rows = {r.min_row for r in tab.merged_cells.ranges}
        for cells in tab.iter_rows(min_row=4):
            if cells[0].row in merged_rows:
                tab.row_dimensions[cells[0].row].height = 24
                continue
            lines = 1
            for cell in cells:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
                width = tab.column_dimensions[cell.column_letter].width - 2
                count = sum(max(1, math.ceil(sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in line) / width))
                            for line in str(cell.value or "").splitlines())
                lines = max(lines, count)
            tab.row_dimensions[cells[0].row].height = min(409.5, 15 * lines + 8)

    wb.save(out_path)
    return str(out_path)
