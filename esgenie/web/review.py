"""Saved human answers and review progress, independent of engine assessments."""
from __future__ import annotations

from copy import deepcopy

REVIEW_LABELS = {"pending": "확인 필요", "complete": "담당자 검토 완료", "missing": "자료 필요", "again": "자료 변경 · 재검토"}


def document_version(document):
    return document.get("version", 1)


def source_from_link(link, documents):
    doc = next((d for d in documents if d["name"] == link.get("file_name")), None)
    page = link.get("page")
    return {"name": link.get("file_name", ""), "quote": link.get("quote", ""),
            "page": page, "bbox": link.get("bbox"), "independent": bool(link.get("independent")),
            "document_id": doc["id"] if doc else None, "version": document_version(doc) if doc else None,
            "preview_available": bool(doc and not doc.get("example") and page is not None and doc.get("pages", 0) > page)}


def review_rows(project, shown):
    result = project["result"]
    raw = {a["qid"]: a for a in result["sheet"]["answers"]}
    documents = result.get("documents", project["documents"])
    rows = []
    for original in shown:
        row = deepcopy(original)
        item = raw[row["id"]]
        automatic = {"answer": row["draft_text"] or ("" if item.get("value") is None else row["value_text"]),
                     "unit": item.get("unit", ""), "scope": row["scope_label"] or row["period_label"],
                     "sources": [source_from_link(link, documents) for link in item.get("evidence_links", [])],
                     "memo": "", "reason": ""}
        # Numeric unit is a separate editable field; never append it twice.
        if item.get("value") is not None and not row["draft_text"] and automatic["unit"]:
            automatic["answer"] = automatic["answer"].removesuffix(" " + automatic["unit"])
        saved = project.get("reviews", {}).get(row["id"])
        if saved and saved.get("framework", project["framework"]) != project["framework"]:
            saved = None
        effective = deepcopy(saved["values"] if saved else automatic)
        legacy = project.get("notes", {}).get(row["id"])
        if not saved and legacy:
            if legacy.get("answer"):
                effective["answer"] = legacy["answer"]
                effective["unit"] = ""
            effective["memo"] = legacy.get("text", "")
        state = saved.get("status", "pending") if saved else ("missing" if not automatic["answer"] else "pending")
        if row["id"] in project.get("affected_questions", []):
            state = "again"
        row.update(saved=effective, automatic=automatic, method=saved.get("method", "담당자 수정") if saved else "담당자 수정" if legacy and legacy.get("answer") else "자동 입력",
                   review_status=state, review_label=REVIEW_LABELS[state],
                   history=sorted((saved.get("history", []) if saved else []) + project.get("analysis_history", {}).get(row["id"], []), key=lambda h: h["at"]),
                   candidates=result.get("candidates", {}).get(row["id"], []))
        row["value_text"] = (effective["answer"] + (" " + effective["unit"] if effective["unit"] else "")) if effective["answer"].strip() else "답변 없음"
        row["scope_label"] = effective["scope"]
        row["sources"] = effective["sources"]
        row["reference_sources"] = [source_from_link(link, documents) for link in item.get("reference_links", [])]
        rows.append(row)
    return rows


def affected_by_document(project, document):
    names = {document["name"], *(v["name"] for v in document.get("versions", []))}
    affected = set()
    for answer in (project.get("result") or {}).get("sheet", {}).get("answers", []):
        links = answer.get("evidence_links", []) + answer.get("reference_links", [])
        claims = answer.get("self_reports", [])
        if any(link.get("file_name") in names for link in links) or any(
            any(name in claim.get("source", "") for name in names) for claim in claims
        ):
            affected.add(answer["qid"])
    for qid, review in project.get("reviews", {}).items():
        if any(s.get("document_id") == document["id"] for s in review["values"]["sources"]):
            affected.add(qid)
    for qid, candidates in (project.get("result") or {}).get("candidates", {}).items():
        if any(s.get("document_id") == document["id"] for c in candidates for s in c["sources"]):
            affected.add(qid)
    return affected


def invalidate(project, questions):
    project["affected_questions"] = sorted(set(project.get("affected_questions", [])) | set(questions))
    for qid in questions:
        review = project.get("reviews", {}).get(qid)
        if review:
            review["status"] = "again"


def reconcile(project, previous, result):
    """Only changed questions lose completion; human values remain untouched."""
    old = {a["qid"]: a for a in (previous or {}).get("sheet", {}).get("answers", [])}
    fields = ("value", "unit", "boundary_label", "evidence_links", "reference_links", "status", "scope_notes", "comparison_reason", "self_reports", "confidence_flags")
    changed = {a["qid"] for a in result["sheet"]["answers"] if a["qid"] in old and
               any(a.get(k) != old[a["qid"]].get(k) for k in fields)}
    invalidate(project, changed)
