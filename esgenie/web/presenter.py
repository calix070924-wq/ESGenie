"""Plain-language presentation of engine results; never upgrades trust states."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


FLAG_HELP = {
    "period_inferred": "서류에서 연도를 확인하지 못해 추정한 값이에요.",
    "derived": "다른 수치에서 계산한 값이에요. 계산에 쓴 자료도 확인해 주세요.",
    "partial_value": "일부 기간 또는 일부 자료의 값이에요. 전체 실적으로 쓰기 전에 확인해 주세요.",
    "partial_aggregate": "일부 자료를 합친 값이에요. 빠진 기간이나 자료가 있는지 확인해 주세요.",
    "unit_suspect": "숫자의 단위를 다시 확인해야 해요.",
    "no_representative_node": "이 수치와 직접 연결되는 자료가 부족해요.",
}
STATUS = {
    "verified": ("linked", "자료 연결됨", "답변과 연결된 자료를 찾았어요.", "제출 전에 답변이 회사 상황에 맞는지 읽어 주세요."),
    "flagged": ("review", "확인 필요", "답변과 자료에서 다시 확인할 내용이 있어요.", "오른쪽 자료와 답변을 비교하고, 확인한 내용을 남겨 주세요."),
    "insufficient": ("missing", "자료 필요", "답변을 확인할 자료가 아직 부족해요.", "아래 안내에 맞는 서류가 있는지 찾아 주세요. 지금 없다면 나중에 이어갈 수 있어요."),
    "hitl_required": ("write", "답변 작성 필요", "회사가 실제로 하는 일을 직접 설명해야 해요.", "현재 운영하는 방법과 담당자를 아는 범위에서 적어 주세요."),
    "self_reported": ("unconfirmed", "자료 확인 전", "답은 있지만 자료로 확인이 끝나지 않았어요.", "답변을 뒷받침하는 고지서나 규정 등이 있는지 확인해 주세요."),
    "draft_ready": ("draft", "초안 읽어보기", "자료를 바탕으로 답변 초안을 만들었어요.", "회사 상황과 다른 표현이 있는지 읽고, 직접 작성한 답변에 수정 내용을 남겨 주세요."),
    "not_applicable": ("excluded", "해당 없음", "이 질문은 이번 평가 대상에 포함되지 않아요.", "제외된 이유가 회사 상황과 맞는지 확인해 주세요."),
}


def present_sheet(sheet: dict, documents: list[dict], pending_files: set[str] | None = None) -> list[dict]:
    by_name = {d["name"]: d for d in documents}
    pending_files = pending_files or set()
    result = []
    for answer in sheet["answers"]:
        original = answer["status"]
        state, label, why, action = STATUS.get(original, STATUS["flagged"])
        notices = [FLAG_HELP[f] for f in answer.get("confidence_flags", []) if f in FLAG_HELP]
        sources = []
        for link in answer.get("evidence_links", []):
            doc = by_name.get(link.get("file_name"))
            page = link.get("page")
            sources.append({
                "name": link.get("file_name") or "연결된 자료", "quote": link.get("quote", ""),
                "page": page, "bbox": link.get("bbox"),
                "independent": bool(link.get("independent")),
                "document_id": doc["id"] if doc else None,
                "preview_available": bool(doc and not doc.get("example") and page is not None),
            })
            if link.get("file_name") in pending_files:
                notices.append("표를 읽은 결과에 확인할 부분이 있어요. 원본의 행과 열을 대조해 주세요.")
        flags = answer.get("flags", [])
        if any("D1 불일치" in f for f in flags):
            why = "회사가 적은 답변과 자료에서 계산한 값이 달라요."
        if any("단위" in f and any(s in f for s in ("불가", "검토", "범위")) for f in flags):
            notices.append("숫자를 비교하기 전에 단위와 값의 범위를 확인해야 해요.")
        if any("조항 검사 미충족" in f for f in flags):
            why = "규정은 있지만 질문에서 요구하는 내용이 일부 빠져 있어요."
        if notices and state in {"linked", "unconfirmed"}:
            state, label = "review", "확인 필요"
            why = notices[0]
        if original == "verified" and not any(s["independent"] and s["quote"] for s in sources):
            state, label, why, action = STATUS["self_reported"]
        value = answer.get("value")
        if value is None:
            value_text = "아직 확인하지 못했어요"
        elif isinstance(value, bool):
            value_text = "예" if value else "아니오"
        elif isinstance(value, list):
            value_text = ", ".join(str(v) for v in value) or "선택된 항목 없음"
        else:
            value_text = str(value) + (f" {answer['unit']}" if answer.get("unit") else "")
        period = answer.get("period")
        period_label = f"{period}년" if period is not None else ""
        if period_label and "period_inferred" in answer.get("confidence_flags", []):
            period_label += " · 추정"
        result.append({
            "id": answer["qid"], "question": answer["question_text"], "section": answer["section"],
            "status": state, "status_label": label, "original_status": original,
            "needs_attention": state not in {"linked", "excluded"},
            "why": why, "next_step": action, "value_text": value_text, "period_label": period_label,
            "draft_text": answer.get("draft_text", ""), "notices": list(dict.fromkeys(notices)),
            "evidence_needed": answer.get("evidence_needed", []), "sources": sources,
            "company_answers": answer.get("self_reports", []), "flags": flags,
            "technical_reason": answer.get("rationale", ""),
        })
    return result


def public_project(project: dict[str, Any]) -> dict[str, Any]:
    result = project.get("result")
    public = {key: value for key, value in project.items() if key != "result"}
    public["stale"] = bool(result and project["input_revision"] != project.get("result_revision"))
    public["result"] = {key: value for key, value in result.items() if key != "sheet"} if result else None
    return public


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()
