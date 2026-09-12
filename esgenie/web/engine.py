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
        path = directory / "uploads" / document["id"] / document["name"]
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
    successful = {ext.source_file for ext in output.ocr_extractions}
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
    return {"sheet": raw, "answers": present_sheet(raw, project["documents"], pending),
            "limitations": limitations, "generated_at": timestamp(), "mode": "live"}
