"""ReportDoc → .pdf (통합 ESG 보고서 / 인쇄·제출용).

layer6_report.assemble_report() 결과(ReportDoc)를 PDF로 낸다. 본문 위주 문서라
세로 A4를 쓴다(공급망 응답서는 표 위주라 가로였음).

설계 메모
--------
* 단일 소스: ReportDoc.to_markdown()의 마크다운을 그대로 렌더한다. 별도 계산 없음.
* 마크다운 서브셋 파서(_md_to_flowables)가 다음을 처리한다:
    # 제목 / ## 섹션 / ### 소제목 / > 인용 / - 불릿 / | pipe 표 | / 일반 문단
    인라인: **굵게**, _기울임_, `코드`.
* 한글 폰트는 supplychain.exporters._fonts.resolve_korean_font()가 해석(번들 NotoSansKR
  → 시스템 폰트 → Helvetica 폴백). reportlab은 CFF 임베드 불가라 glyf TTF 필요.
"""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

from ..supplychain.exporters._fonts import resolve_korean_font


def _inline(text: str) -> str:
    """마크다운 인라인 → reportlab 마크업. HTML 이스케이프 후 태그만 복원."""
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"`(.+?)`", r"<font face='Courier'>\1</font>", text)
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<i>\1</i>", text)
    return text


def _split_row(line: str) -> list[str]:
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


def _is_sep_row(line: str) -> bool:
    return bool(re.fullmatch(r"\s*\|?[:\- |]+\|?\s*", line)) and "-" in line


def _md_to_flowables(md: str, styles: dict[str, Any], font: Any) -> list[Any]:
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    flow: list[Any] = []
    lines = md.split("\n")
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # ── 표 (연속된 pipe 줄) ──
        if stripped.startswith("|") and "|" in stripped[1:]:
            tbl: list[list[str]] = []
            while i < n and lines[i].strip().startswith("|"):
                row_line = lines[i].strip()
                if not _is_sep_row(row_line):
                    tbl.append(_split_row(row_line))
                i += 1
            if tbl:
                flow.append(_build_table(tbl, styles, font, colors, Table, TableStyle, Paragraph))
                flow.append(Spacer(1, 6))
            continue

        # ── 제목/소제목 ──
        if stripped.startswith("### "):
            flow.append(Paragraph(_inline(stripped[4:]), styles["h3"]))
        elif stripped.startswith("## "):
            flow.append(Paragraph(_inline(stripped[3:]), styles["h2"]))
        elif stripped.startswith("# "):
            flow.append(Paragraph(_inline(stripped[2:]), styles["h1"]))
        # ── 인용(리드 노트) ──
        elif stripped.startswith(">"):
            flow.append(Paragraph(_inline(stripped.lstrip("> ").strip()), styles["note"]))
        # ── 불릿 ──
        elif stripped.startswith("- ") or stripped.startswith("* "):
            flow.append(Paragraph("• " + _inline(stripped[2:]), styles["bullet"]))
        # ── 일반 문단 ──
        else:
            flow.append(Paragraph(_inline(stripped), styles["body"]))
        i += 1

    return flow


def _build_table(rows, styles, font, colors, Table, TableStyle, Paragraph):
    header, *data_rows = rows
    ncol = len(header)
    cell = styles["cell"]
    head_cell = styles["head_cell"]

    table_data = [[Paragraph(_inline(c), head_cell) for c in header]]
    for r in data_rows:
        # 열 수 보정
        r = (r + [""] * ncol)[:ncol]
        table_data.append([Paragraph(_inline(c), cell) for c in r])

    tbl = Table(table_data, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E78")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#BBBBBB")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F6F9")]),
    ]))
    return tbl


def export_report_pdf(doc: Any, out_dir: str | Path) -> str:
    """ReportDoc를 PDF로 저장하고 경로를 반환한다.

    Args:
        doc: layer6_report.ReportDoc
        out_dir: 출력 디렉터리
    """
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate

    font = resolve_korean_font()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_corp = (doc.corp_name or "corp").replace("/", "_")
    out_path = out_dir / f"ESG보고서_{safe_corp}_{doc.report_year or ''}.pdf"

    base_ss = getSampleStyleSheet()
    base = ParagraphStyle("kr", parent=base_ss["Normal"], fontName=font.regular,
                          fontSize=9.5, leading=14, alignment=TA_LEFT)
    styles = {
        "body": base,
        "cell": ParagraphStyle("cell", parent=base, fontSize=8.5, leading=11, wordWrap="CJK"),
        "head_cell": ParagraphStyle("hcell", parent=base, fontName=font.bold, fontSize=8.5,
                                    leading=11, textColor=colors.white, wordWrap="CJK"),
        "h1": ParagraphStyle("h1", parent=base, fontName=font.bold, fontSize=18, leading=23,
                             spaceBefore=4, spaceAfter=8),
        "h2": ParagraphStyle("h2", parent=base, fontName=font.bold, fontSize=13.5, leading=18,
                             spaceBefore=12, spaceAfter=5, textColor=colors.HexColor("#1F4E78")),
        "h3": ParagraphStyle("h3", parent=base, fontName=font.bold, fontSize=11, leading=15,
                             spaceBefore=6, spaceAfter=3),
        "note": ParagraphStyle("note", parent=base, fontSize=8.8, leading=12,
                               textColor=colors.HexColor("#555555"), leftIndent=6,
                               spaceBefore=2, spaceAfter=4),
        "bullet": ParagraphStyle("bullet", parent=base, leftIndent=10, spaceAfter=2),
    }

    elements: list[Any] = []
    if not font.embedded:
        elements.append(Paragraph(
            "⚠ 한글 폰트 미발견 — 텍스트가 깨질 수 있습니다(ESGENIE_PDF_FONT 설정 권장).",
            styles["note"]))
    elements += _md_to_flowables(doc.to_markdown(), styles, font)

    pdf = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"{doc.corp_name} ESG 공시 신뢰성 보고서",
    )
    pdf.build(elements)
    return str(out_path)
