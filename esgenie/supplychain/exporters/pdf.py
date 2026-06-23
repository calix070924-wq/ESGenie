"""ResponseSheet → .pdf (OEM 제출본 / 인쇄·공유용).

excel.py와 같은 산출물을 PDF로 낸다. 협력사가 대기업에 그대로 제출하거나
출력·메일 첨부하기 좋은 형태. 구성:

  1. 표지 요약(기업·양식·자동/작성/대기 % · 검토필요 건수)
  2. 자가진단 응답표 — 문항·답변·신뢰배지·근거(→ 증빙부록 [E#] 상호참조)
  3. 제출 전 증빙 체크리스트(checklist.py 재사용)
  4. 보완·검토 항목(gaps)
  5. 증빙 부록 — 원본 페이지 이미지 + bbox 박스(감사 대조용)

설계 메모
--------
* 신규 계산 없음 — excel.py와 동일하게 ResponseSheet/checklist 결과만 렌더.
* 한글 폰트는 `_fonts.resolve_korean_font()`가 해석(번들 NotoSansKR → 시스템 폰트).
  reportlab은 CFF 임베드 불가라 glyf TTF 필요(자세한 건 _fonts.py).
* CJK 폰트엔 이모지 글리프가 없으므로 배지는 **텍스트 라벨 + 색상 셀**로 표현
  (excel은 이모지 배지, PDF는 라벨+색 — 동일 의미를 깨지지 않게).
* 증빙 부록: EvidenceLink.relative_path(evidence_pack/...)를 evidence_base_dir
  기준으로 resolve → pdf_render.render_page_with_box로 페이지+bbox PNG를 임베드.
  파일 없음·렌더 실패·bbox 없음은 *부록에서 조용히 스킵*하고 표의 텍스트 근거는 유지
  (원본 PDF가 없는 dart 출처 등에서도 export 전체가 죽지 않게).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..schema import ResponseSheet
from ._fonts import resolve_korean_font

# status → (라벨, 셀 배경 hex). excel.py status_fill과 색을 맞춤.
_STATUS_STYLE: dict[str, tuple[str, str]] = {
    "verified":       ("증빙검증", "#E2EFDA"),
    "self_reported":  ("자가신고", "#FFF2CC"),
    "insufficient":   ("데이터부족", "#F2F2F2"),
    "flagged":        ("검토필요", "#FCE4E4"),
    "hitl_required":  ("작성필요", "#DDEBF7"),
    "not_applicable": ("해당없음", "#EAEAEA"),
}
_HEADER_BG = "#1F4E78"
_CHECK_ACTION_BG: dict[str, str] = {
    "증빙 업로드": "#F2F2F2",
    "담당자 작성": "#DDEBF7",
    "검토·보완":   "#FCE4E4",
}


def _fmt_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) if value else "—"
    return str(value)


def _fmt_evidence(answer, fig_map: dict[int, str] | None = None) -> str:
    """근거 셀 텍스트. fig_map에 든 링크는 부록 figure 번호(→ [E3])를 덧붙인다."""
    parts: list[str] = []
    for e in answer.evidence_links:
        loc = ""
        if e.page is not None:
            loc = f" p.{e.page + 1}"
        if e.bbox:
            loc += f" bbox{[round(x, 3) for x in e.bbox]}"
        tag = ""
        if fig_map and id(e) in fig_map:
            tag = f" → [{fig_map[id(e)]}]"
        parts.append(f"{e.file_name}{loc}{tag}".strip())
    ev = " / ".join(parts)
    rationale = answer.rationale
    if ev and rationale:
        return f"{rationale}<br/>근거: {ev}"
    return ev or rationale


def _resolve_evidence_path(base_dir: Path, link) -> Path | None:
    """EvidenceLink → 원본 파일 절대경로. 못 찾으면 None.

    relative_path('evidence_pack/foo.pdf')를 base_dir 기준으로 먼저 찾고,
    실패 시 base_dir/evidence_pack/file_name, base_dir/file_name 순으로 폴백.
    """
    rel = getattr(link, "relative_path", "") or ""
    fname = getattr(link, "file_name", "") or ""
    candidates = []
    if rel:
        candidates.append(base_dir / rel)
    if fname:
        candidates.append(base_dir / "evidence_pack" / fname)
        candidates.append(base_dir / fname)
    for c in candidates:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


def _build_evidence_index(sheet, base_dir: Path) -> tuple[list[dict], dict[int, str]]:
    """렌더 가능한 증빙(원본 파일 존재 + PDF)만 골라 figure 목록과 링크→figure 맵을 만든다.

    반환: (figures, fig_map)
      figures = [{"fig_id","answer","link","path"}], fig_map = {id(link): "E3"}
    렌더 불가(파일 없음 등)는 figure에 넣지 않음 → 표 근거는 텍스트로만 남는다.

    Dedup: 같은 (해석된 파일 경로, page) 조합은 한 번만 임베드하고, 그 조합을
    가리키는 모든 링크가 동일한 [E#]를 공유한다.
    # bbox가 다른 경우: 같은 파일+페이지면 첫 번째 링크의 bbox 기준으로 렌더하고
    # 나머지 링크도 동일 figure를 참조한다. 페이지 전체 맥락을 보여주는 것이 목적이므로
    # 개별 bbox마다 별도 figure를 만들지 않는다.
    """
    figures: list[dict] = []
    fig_map: dict[int, str] = {}
    # (resolved_path_str, page) → fig_id: 중복 검출용
    seen: dict[tuple[str, int | None], str] = {}
    n = 0
    for a in sheet.answers:
        for e in a.evidence_links:
            if e.page is None and not e.bbox:
                continue
            path = _resolve_evidence_path(base_dir, e)
            if path is None or path.suffix.lower() != ".pdf":
                continue
            dedup_key = (str(path.resolve()), e.page)
            existing_fid = seen.get(dedup_key)
            if existing_fid is not None:
                fig_map[id(e)] = existing_fid
                continue
            n += 1
            fid = f"E{n}"
            seen[dedup_key] = fid
            fig_map[id(e)] = fid
            figures.append({"fig_id": fid, "answer": a, "link": e, "path": path})
    return figures, fig_map


def export_response_sheet_pdf(
    sheet: ResponseSheet,
    out_dir: str | Path,
    *,
    evidence_base_dir: str | Path | None = None,
    embed_evidence: bool = True,
) -> str:
    """응답서를 PDF로 저장하고 경로를 반환한다.

    evidence_base_dir: EvidenceLink.relative_path를 resolve할 기준 폴더(증빙 원본·
        evidence_pack 위치). None이면 out_dir 사용.
    embed_evidence: False면 증빙 부록(원본 페이지+bbox 임베드)을 생략.
    """
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    from ..checklist import checklist_rows

    font = resolve_korean_font()

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base_dir = Path(evidence_base_dir) if evidence_base_dir else out_dir
    out_path = out_dir / f"실사응답서_{sheet.framework_key}_{sheet.corp_name or 'corp'}.pdf"

    # 증빙 부록 인덱스 먼저 — 표의 근거 셀에 [E#] 상호참조를 달기 위해 선행 계산.
    figures: list[dict] = []
    fig_map: dict[int, str] = {}
    if embed_evidence:
        try:
            figures, fig_map = _build_evidence_index(sheet, base_dir)
        except Exception:  # noqa: BLE001 — 부록 실패가 응답서 본문을 막지 않게
            figures, fig_map = [], {}

    styles = getSampleStyleSheet()
    base = ParagraphStyle("kr", parent=styles["Normal"], fontName=font.regular,
                          fontSize=8.5, leading=11, alignment=TA_LEFT)
    cell = ParagraphStyle("cell", parent=base, wordWrap="CJK")
    cell_c = ParagraphStyle("cellc", parent=cell, alignment=TA_CENTER)
    head_cell = ParagraphStyle("hcell", parent=cell_c, fontName=font.bold,
                               textColor=colors.white)
    title = ParagraphStyle("title", parent=base, fontName=font.bold, fontSize=15,
                           leading=19)
    subtitle = ParagraphStyle("subtitle", parent=base, fontSize=9,
                              textColor=colors.HexColor("#555555"), leading=13)
    section = ParagraphStyle("section", parent=base, fontName=font.bold, fontSize=12,
                            leading=16, spaceBefore=6, spaceAfter=4)

    doc = SimpleDocTemplate(
        str(out_path), pagesize=landscape(A4),
        leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=12 * mm, bottomMargin=12 * mm,
        title=f"실사 응답서 {sheet.corp_name or ''}".strip(),
    )
    page_w = landscape(A4)[0] - 24 * mm
    elements: list[Any] = []

    # ── 표지 요약 ──
    elements.append(Paragraph(sheet.framework_label, title))
    elements.append(Paragraph(
        f"기업: {sheet.corp_name or '—'} &nbsp;|&nbsp; "
        f"자동응답 {sheet.auto_pct}% · 작성필요 {sheet.hitl_pct}% · 증빙대기 {sheet.pending_pct}% "
        f"&nbsp;|&nbsp; 검토필요 {sheet.flagged_count}건 &nbsp;|&nbsp; 문항 {len(sheet.answers)}개",
        subtitle))
    if not font.embedded:
        elements.append(Paragraph(
            "⚠ 한글 폰트 미발견 — 텍스트가 깨질 수 있습니다(ESGENIE_PDF_FONT 설정 권장).",
            subtitle))
    elements.append(Spacer(1, 6))

    # ── 응답표 ──
    header = ["문항 ID", "섹션", "문항", "답변", "신뢰", "근거 / 비고"]
    col_w = [w / 100.0 * page_w for w in (9, 11, 27, 16, 9, 28)]
    # 섹션(현대차 영역)별 집계 — 그룹 헤더 요약용.
    from collections import defaultdict
    sec_total: dict[str, int] = defaultdict(int)
    sec_auto: dict[str, int] = defaultdict(int)
    sec_flag: dict[str, int] = defaultdict(int)
    for a in sheet.answers:
        if a.status == "not_applicable":
            continue
        sec_total[a.section] += 1
        if a.status in ("verified", "self_reported", "flagged"):
            sec_auto[a.section] += 1
        if a.status == "flagged":
            sec_flag[a.section] += 1

    group_style = ParagraphStyle("group", parent=base, fontName=font.bold, fontSize=9.5,
                                 textColor=colors.HexColor("#1F4E78"))
    data = [[Paragraph(h, head_cell) for h in header]]
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_HEADER_BG)),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#BBBBBB")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    ri = 0
    cur_section: str | None = None
    for a in sheet.answers:
        # 영역이 바뀌면 전폭 그룹 헤더 행을 끼운다.
        if a.section != cur_section:
            cur_section = a.section
            ri += 1
            flag_note = f" · 검토필요 {sec_flag[a.section]}건" if sec_flag[a.section] else ""
            glabel = (f"▌ {a.section}    "
                      f"(자동응답 {sec_auto[a.section]}/{sec_total[a.section]}{flag_note})")
            data.append([Paragraph(glabel, group_style), "", "", "", "", ""])
            style_cmds.append(("SPAN", (0, ri), (-1, ri)))
            style_cmds.append(("BACKGROUND", (0, ri), (-1, ri), colors.HexColor("#D9E1F2")))
        ri += 1
        label, bg = _STATUS_STYLE.get(a.status, (a.status, "#FFFFFF"))
        data.append([
            Paragraph(a.qid, cell),
            Paragraph(a.section, cell),
            Paragraph(a.question_text, cell),
            Paragraph(_fmt_value(a.value), cell),
            Paragraph(label, cell_c),
            Paragraph(_fmt_evidence(a, fig_map) or "—", cell),
        ])
        style_cmds.append(("BACKGROUND", (0, ri), (-1, ri), colors.HexColor(bg)))
    table = Table(data, colWidths=col_w, repeatRows=1)
    table.setStyle(TableStyle(style_cmds))
    elements.append(table)

    # ── 증빙 체크리스트 ──
    rows = checklist_rows(sheet)
    if rows:
        elements.append(Spacer(1, 10))
        elements.append(Paragraph("제출 전 증빙 체크리스트", section))
        elements.append(Paragraph(
            "증빙 업로드=문서 올리면 자동 해소 / 담당자 작성=사람이 직접 서술 / 검토·보완=경고 소명",
            subtitle))
        elements.append(Spacer(1, 3))
        ch_header = ["문항 ID", "섹션", "문항", "할 일", "올릴 문서 / 작성 사항", "안내"]
        ch_w = [w / 100.0 * page_w for w in (9, 11, 24, 9, 22, 25)]
        ch_data = [[Paragraph(h, head_cell) for h in ch_header]]
        ch_style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_HEADER_BG)),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#BBBBBB")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        for ri, row in enumerate(rows, start=1):
            ch_data.append([Paragraph(str(row[k]), cell) for k in ch_header])
            bg = _CHECK_ACTION_BG.get(row["할 일"])
            if bg:
                ch_style.append(("BACKGROUND", (0, ri), (-1, ri), colors.HexColor(bg)))
        ch_table = Table(ch_data, colWidths=ch_w, repeatRows=1)
        ch_table.setStyle(TableStyle(ch_style))
        elements.append(ch_table)

    # ── 보완·검토(gaps) ──
    if sheet.gaps:
        elements.append(Spacer(1, 10))
        elements.append(Paragraph("제출 전 보완·검토 항목", section))
        for g in sheet.gaps:
            elements.append(Paragraph(f"• {g}", cell))

    # ── 증빙 부록(원본 페이지 + bbox 박스) ──
    if figures:
        from reportlab.platypus import Image as RLImage, KeepTogether, PageBreak

        from ...pdf_render import render_page_with_box

        cap = ParagraphStyle("fig_cap", parent=subtitle, fontName=font.bold,
                             textColor=colors.HexColor("#1F4E78"), spaceBefore=4)
        # 가로 A4 기준 그림 최대 크기(여백 안). 한 페이지에 1~2개 들어가게 보수적으로.
        max_w = page_w * 0.62
        max_h = (landscape(A4)[1] - 24 * mm) * 0.74
        rendered = 0
        flow: list[Any] = [PageBreak(), Paragraph("증빙 부록 — 원본 대조", section),
                           Paragraph("각 그림은 답변의 출처 원본 페이지이며, 주황 박스가 "
                                     "해당 수치/문장의 위치입니다. 응답표 근거의 [E#]와 대응.",
                                     subtitle), Spacer(1, 4)]
        for fig in figures:
            e = fig["link"]
            try:
                png = render_page_with_box(str(fig["path"]), e.bbox, page=e.page or 0, dpi=120)
            except Exception:  # noqa: BLE001 — 개별 그림 실패는 건너뛰고 계속
                continue
            from io import BytesIO
            from reportlab.lib.utils import ImageReader

            iw, ih = ImageReader(BytesIO(png)).getSize()
            scale = min(max_w / iw, max_h / ih)
            w, h = iw * scale, ih * scale
            a = fig["answer"]
            loc = f" p.{(e.page or 0) + 1}" if e.page is not None else ""
            caption = (f"[{fig['fig_id']}] {e.file_name}{loc} · "
                       f"{a.qid} {a.question_text} → {_fmt_value(a.value)}")
            flow.append(KeepTogether([
                Paragraph(caption, cap),
                RLImage(BytesIO(png), width=w, height=h),
                Spacer(1, 8),
            ]))
            rendered += 1
        if rendered:
            elements.extend(flow)

    doc.build(elements)
    return str(out_path)
