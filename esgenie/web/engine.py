"""Adapter to the existing pipeline, with separate evidence and company answers."""
from __future__ import annotations

from pathlib import Path

from .presenter import present_sheet, timestamp


def analysis_available() -> bool:
    from esgenie.config import SETTINGS
    return not SETTINGS.use_mock_llm


def run_analysis(project: dict, directory: Path) -> dict:
    from esgenie.pipeline import run
    from esgenie.supplychain import parse_saq_claims, respond_from_pipeline
    from esgenie.config import SETTINGS

    evidence, company_files = {}, []
    for document in project["documents"]:
        if not document.get("included", True) or document.get("error"):
            continue
        path = directory / (document.get("path") or str(Path("uploads") / document["id"] / document["name"]))
        if document["role"] == "company_answer":
            company_files.append(str(path))
        else:
            evidence[document["name"]] = str(path)
    claims = parse_saq_claims(company_files)
    # One worker owns the process-wide engine. Failure must not become a mock success.
    previous_strict = SETTINGS.strict_llm
    SETTINGS.strict_llm = True
    try:
        output = run(
            project["id"], areas=["E", "S", "G"], corp_name=project["company_name"],
            industry=project["industry"], report_year=project["year"], use_dart=False,
            evidence_files=evidence, demo_greenwash=False, save_traces=False,
            export_outputs=False, export_report=False, profile="sme",
        )
        sheet = respond_from_pipeline(output, project["framework"], supplier_claims=claims, enable_drafts=False)
        sheet.corp_name = project["company_name"]
    finally:
        SETTINGS.strict_llm = previous_strict
    pending = {ext.source_file for ext in output.ocr_extractions if ext.router_meta.get("table_gate_pending")}
    successful = {ext.source_file for ext in output.ocr_extractions
                  if (ext.router_meta or {}).get("extraction_status") != "failed" and getattr(ext, "doc_type", "") != "extraction_failed"}
    limitations = []
    missing = sorted(set(evidence) - successful)
    if missing:
        limitations.append("읽지 못한 자료: " + ", ".join(missing))
    if pending:
        limitations.append("표를 읽은 결과에 확인이 필요한 자료가 있어요.")
    unavailable = [area for area, section in output.sections.items() if section.final_score is None]
    if unavailable:
        labels = {"E": "환경", "S": "사람·안전", "G": "회사 운영"}
        limitations.append("자료만으로 평가하지 못한 영역: " + ", ".join(labels[a] for a in unavailable))
    if not output.evidence_graph.nodes and not output.evidence_graph.text_nodes:
        limitations.append("연결할 수 있는 자료 내용을 찾지 못했어요. 문서가 잘 보이는지 확인해 주세요.")
    raw = sheet.to_dict()
    from esgenie.ui.tabs import ocr_upload_statuses, ocr_upload_messages
    statuses = ocr_upload_statuses(output, list(evidence))
    errors = {message.split(" — ", 1)[0]: message for _, message in ocr_upload_messages(output, list(evidence))}
    candidates = build_candidates(output, sheet, project["documents"])
    return {"sheet": raw, "answers": present_sheet(raw, project["documents"], pending), "candidates": candidates,
            "document_statuses": {d["id"]: {"complete": "완료", "failed": "실패", "partial": "일부 처리 · 확인 필요", "mock": "시연 결과", "unknown": "처리 상태 미확인"}.get(statuses.get(d["name"]), "완료" if d["role"] == "company_answer" else "읽기 실패") for d in project["documents"] if d.get("included", True)},
            "document_errors": errors, "limitations": limitations, "generated_at": timestamp(), "mode": "live", "pending_files": sorted(pending)}


def build_candidates(output, sheet, documents):
    """Offer only question-mapped actual graph nodes, including their measurement scope."""
    from esgenie.supplychain.frameworks import get_framework
    from esgenie.ssot.audit_trace import evidence_link
    from esgenie.supplychain.schema import format_amount
    from .review import source_from_link
    if not hasattr(sheet, "framework_key"):
        return {}
    questions = {q.qid: q for q in get_framework(sheet.framework_key).questions}
    candidates = {}
    nodes = getattr(output.evidence_graph, "nodes", {})
    if not isinstance(nodes, dict):
        return candidates
    for answer in sheet.answers:
        question = questions.get(answer.qid)
        if not question:
            continue
        options = []
        for node in nodes.values():
            if node.metric not in question.kesg_codes or not node.source_file or not node.quote:
                continue
            boundary = getattr(node, "boundary", None)
            scope = boundary.label() if boundary and hasattr(boundary, "label") else ""
            link = evidence_link(node)
            link.quote = node.quote
            options.append({"id": node.id, "answer": format_amount(node.value), "unit": node.unit,
                            "scope": scope or (f"{node.period}년" + (" · 추정" if node.period_inferred else "")),
                            "sources": [source_from_link(link.to_dict(), documents)],
                            "notices": ["원문의 기간·사업장·대상을 확인한 후 적용하세요."] +
                                       (["원문 연도가 확인되지 않아 추정한 값입니다."] if node.period_inferred else [])})
        candidates[answer.qid] = options
    return candidates
