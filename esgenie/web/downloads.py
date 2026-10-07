"""Exports include both engine provenance and separately labeled human notes."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import fields
from io import BytesIO
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from zipfile import ZipFile, ZIP_DEFLATED

from .store import WorkspaceError
from .presenter import presented_answers


def build_download(project: dict, directory: Path, kind: str) -> tuple[bytes, str, str]:
    from esgenie.supplychain.schema import Answer, ResponseSheet
    from esgenie.ssot.audit_trace import EvidenceLink
    from esgenie.supplychain import copy_evidence_pack, export_response_sheet, export_response_sheet_pdf
    from openpyxl import load_workbook

    result = project.get("result")
    if not result:
        raise WorkspaceError("서류를 읽은 뒤 응답서를 받을 수 있어요.", 409)
    if project["input_revision"] != project["result_revision"]:
        raise WorkspaceError("자료가 바뀌었어요. 다시 분석한 뒤 최신 응답서를 받아 주세요.", 409)
    if project["job"]["status"] in {"queued", "running"}:
        raise WorkspaceError("자료 읽기가 끝난 뒤 응답서를 받아 주세요.", 409)
    raw = deepcopy(result["sheet"])
    answer_fields = {f.name for f in fields(Answer)}
    link_fields = {f.name for f in fields(EvidenceLink)}
    answers = []
    presented = {a["id"]: a for a in presented_answers(project)}
    status_labels = {qid: answer["status_label"] for qid, answer in presented.items()}
    attention_count = sum(a["review_status"] != "complete" for a in presented.values())
    summary_text = (f"기업: {project['company_name']} · 전체 {len(presented)}개 문항 · 확인할 항목 {attention_count}개 · "
                    "자료 연결은 최종 승인이 아닙니다. 담당자 검토 후 제출해 주세요.")
    review_status = {"linked": "verified", "review": "flagged", "missing": "insufficient", "write": "hitl_required",
                     "unconfirmed": "self_reported", "draft": "draft_ready", "excluded": "not_applicable"}
    for item in raw["answers"]:
        item = {k: v for k, v in item.items() if k in answer_fields}
        item["evidence_links"] = [EvidenceLink(**{k: v for k, v in link.items() if k in link_fields}) for link in item.get("evidence_links", [])]
        shown = presented[item["qid"]]
        item["status"] = review_status[shown["status"]]
        item["flags"] = list(dict.fromkeys(list(item.get("flags", [])) + shown["notices"] + [shown["why"], shown["next_step"]]))
        item["reference_links"] = [EvidenceLink(**{k: v for k, v in link.items() if k in link_fields}) for link in item.get("reference_links", [])]
        answers.append(Answer(**item))
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", project["company_name"])[:80]
    prefix = "사용법 예시" if project["mode"] == "example" else "검토용 초안"
    sheet = ResponseSheet(raw["framework_key"], f"[{prefix}] {raw['framework_label']}", name, answers, raw.get("gaps", []))
    notes = [f"# {prefix} · {project['company_name']}", "", "직접 작성한 답변과 메모는 검증된 사실로 자동 변경되지 않습니다.", ""]
    for answer in answers:
        note = project["notes"].get(answer.qid)
        if note:
            old = note["revision"] != project["result_revision"]
            notes += [f"## {answer.question_text}", "", "이전 분석에서 작성한 기록입니다. 다시 확인해 주세요." if old else "담당자가 직접 작성한 내용 · 확인 전",
                      "", "직접 작성한 답변: " + note.get("answer", ""), "", "검토 메모: " + note.get("text", ""), ""]
    notes += ["## 추가 확인 사항", ""] + [f"- {message}" for message in result.get("limitations", [])]
    with TemporaryDirectory(prefix="download-", dir=directory) as temporary:
        out = Path(temporary)
        excel_path = Path(export_response_sheet(sheet, out, status_labels=status_labels, summary_text=summary_text))
        workbook = load_workbook(excel_path)
        review = workbook.create_sheet("담당자 작성")
        review.append(["질문", "직접 작성한 답변 (확인 전)", "검토 메모", "기록 기준"])
        for answer in answers:
            note = project["notes"].get(answer.qid)
            if note:
                review.append([answer.question_text, note.get("answer", ""), note.get("text", ""),
                               "이전 분석 기록 · 재확인 필요" if note["revision"] != project["result_revision"] else "현재 분석"])
        review.column_dimensions["A"].width = 45
        review.column_dimensions["B"].width = 55
        review.column_dimensions["C"].width = 55
        # Uploaded text and human answers are text, never spreadsheet formulas.
        for worksheet in workbook:
            for row in worksheet:
                for cell in row:
                    if isinstance(cell.value, str) and cell.value.startswith(("=", "+", "-", "@")):
                        cell.value = "'" + cell.value
        ws = workbook["응답서"]
        extra = ["담당자 검토 상태", "입력 방식", "메모 / 자료 부족 사유", "수정 이유"]
        from openpyxl.styles import Alignment, Font, PatternFill
        for col, title in enumerate(extra, 8):
            ws.cell(4, col, title).font = Font(bold=True, color="FFFFFF")
            ws.cell(4, col).fill = PatternFill("solid", fgColor="244F40")
        for cells in ws:
            qid = cells[0].value
            if qid not in presented:
                continue
            shown = presented[qid]
            values = shown["saved"]
            row = cells[0].row
            ws.cell(row, 4, shown["value_text"])
            ws.cell(row, 5, shown["scope_label"] or "범위 미확인")
            evidence = [f"{s['name']} · {(str(s['page'] + 1) + '쪽') if s['page'] is not None else '페이지 미확인'} · 버전 {s.get('version') or 1}\n{s['quote']}" for s in shown["sources"]]
            refs = [f"보완 대상(값 산정 미사용): {s['name']}" for s in shown["reference_sources"]]
            ws.cell(row, 7, "\n".join(evidence + refs + shown["notices"] + [shown["why"], shown["next_step"]]))
            for col, value in enumerate([shown["review_label"], shown["method"], values["memo"], values["reason"]], 8):
                ws.cell(row, col, value)
            for col in range(1, 12):
                cell = ws.cell(row, col)
                if isinstance(cell.value, str) and cell.value.startswith(("=", "+", "-", "@")):
                    cell.value = "'" + cell.value
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            ws.row_dimensions[row].height = min(280, max(60, 14 * max(len(str(ws.cell(row, col).value or "")) // 36 + str(ws.cell(row, col).value or "").count("\n") + 1 for col in range(3, 12))))
        for column in ("H", "I", "J", "K"):
            ws.column_dimensions[column].width = 28 if column in ("J", "K") else 22
        ws.auto_filter.ref = f"A4:K{ws.max_row}"
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.orientation = "landscape"
        ws.page_setup.paperSize = ws.PAPERSIZE_A3
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        workbook.save(excel_path)
        if kind == "xlsx":
            return excel_path.read_bytes(), f"{prefix}_{name}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        paths = {doc["name"]: str(directory / (doc.get("path") or str(Path("uploads") / doc["id"] / doc["name"])))
                 for doc in project["documents"] if not doc.get("example")}
        pdf_path = canonical_pdf(project, list(presented.values()), out)
        if kind == "pdf":
            return pdf_path.read_bytes(), f"{prefix}_{name}.pdf", "application/pdf"
        copy_evidence_pack(sheet, out, paths)
        (out / "담당자_작성_및_확인사항.md").write_text("\n".join(notes), encoding="utf-8")
        # Include exact archived originals used by saved answers, not only current names.
        import shutil
        originals = out / "원본_버전"
        for row in presented.values():
            for source in row["sources"]:
                document = next((d for d in project["documents"] if d["id"] == source.get("document_id")), None)
                if not document or document.get("example"):
                    continue
                version = source.get("version") or 1
                selected = document if document.get("version", 1) == version else next((v for v in document.get("versions", []) if v["version"] == version), None)
                if selected:
                    original = directory / (selected.get("path") or str(Path("uploads") / document["id"] / selected["name"]))
                    if original.is_file():
                        dest = originals / document["id"] / str(version) / selected["name"]
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(original, dest)
        if (directory / "analyses").is_dir():
            shutil.copytree(directory / "analyses", out / "이전_분석_기록")
        (out / "검증_근거.json").write_text(json.dumps({**result, "answers": list(presented.values())}, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        archive = BytesIO()
        with ZipFile(archive, "w", ZIP_DEFLATED) as bundle:
            for path in sorted(out.rglob("*")):
                if path.is_file():
                    bundle.write(path, path.relative_to(out).as_posix())
        return archive.getvalue(), f"{prefix}_{name}_제출준비.zip", "application/zip"


def canonical_pdf(project, rows, out):
    """Print exactly the effective saved rows also used by the screen and workbook."""
    from html import escape
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, LongTable, TableStyle
    from esgenie.supplychain.exporters._fonts import resolve_korean_font, pdf_safe_text
    font = resolve_korean_font()
    style = ParagraphStyle("response", fontName=font.regular, fontSize=8, leading=12, wordWrap="CJK")
    title = ParagraphStyle("title", parent=style, fontName=font.bold, fontSize=16, leading=23)
    def para(text):
        return Paragraph(escape(pdf_safe_text(str(text))).replace("\n", "<br/>"), style)
    path = out / "응답서.pdf"
    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), rightMargin=24, leftMargin=24, topMargin=28, bottomMargin=28)
    remaining = sum(r["review_status"] != "complete" for r in rows)
    label = "사용법 예시" if project["mode"] == "example" else "검토용" if remaining else "담당자 검토 완료"
    story = [Paragraph(escape(f"{project['company_name']} ESG 응답서 · {label}"), title), Spacer(1, 8),
             para(f"보고 연도 {project['year']} · 양식 {project['framework']} · 전체 {len(rows)}문항 · 남은 검토 {remaining}문항"),
             para("담당자 검토 완료는 검토 작업의 완료 표시입니다. 자료의 사실성·충분성에 대한 판단은 아래 근거 판단과 확인 사유에 유지됩니다."), Spacer(1, 12)]
    data = [[para(h) for h in ["문항", "저장된 답변", "측정 범위 / 기준", "담당자 검토", "근거", "근거 판단 / 확인 사유"]]]
    for row in rows:
        sources = "\n\n".join(f"{s['name']} · {str(s['page'] + 1) + '쪽' if s['page'] is not None else '페이지 미확인'} · 버전 {s.get('version') or 1}\n{s['quote']}" for s in row["sources"]) or "연결된 근거 없음"
        sources += "".join(f"\n보완 대상(값 산정 미사용): {s['name']}" for s in row["reference_sources"])
        notes = "\n".join(dict.fromkeys([row["status_label"], row["why"], row["next_step"], *row["notices"],
                                         "메모 / 자료 부족 사유: " + row["saved"]["memo"], "수정 이유: " + row["saved"]["reason"]]))
        data.append([para(t) for t in [f"{row['id']}\n{row['question']}", f"{row['value_text']}\n{row['method']}", row["scope_label"] or "범위 미확인", row["review_label"], sources, notes]])
    table = LongTable(data, colWidths=[125, 85, 110, 75, 195, 203], repeatRows=1, hAlign="LEFT", splitInRow=1)
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E4EDE6")),
                               ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), .4, colors.HexColor("#D8DFD9")),
                               ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                               ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    story.append(table)
    if project.get("notes"):
        story += [Spacer(1, 12), para("이전 담당자 작성 기록")]
        for qid, note in project["notes"].items():
            story.append(para(f"{qid}: {note.get('answer', '')}\n{note.get('text', '')}"))
    for limitation in project["result"].get("limitations", []):
        story += [Spacer(1, 8), para(limitation)]
    def footer(canvas, document):
        canvas.setFont(font.regular, 8)
        canvas.drawRightString(815, 14, f"ESGenie · {document.page}")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return path
