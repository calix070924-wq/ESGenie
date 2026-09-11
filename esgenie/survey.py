"""설문 응답은 증빙과 별도로 보존한다. 구버전 survey_form 경로도 식별한다."""
from __future__ import annotations

import re
from typing import Any


def is_survey(node: Any) -> bool:
    return (getattr(node, "origin", "") == "survey"
            or str(getattr(node, "source_file", "")) == "survey_form"
            or str(getattr(node, "id", "")).startswith("survey_"))


def presence_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = re.sub(r"^\[설문\]\s*", "", str(value or "").strip())
    if re.match(r"^(아니오|아니요|no|false)(?:\b|\s|:|$)", text, re.I):
        return False
    if re.match(r"^(예|네|yes|true)(?:\b|\s|:|$)", text, re.I):
        return True
    return None


def apply_survey_answers(extraction, answers) -> None:
    if extraction is None or not answers:
        return
    from .knowledge.kesg_items import by_code

    injected = 0
    for code, answer in answers.items():
        yn = answer.get("yn", "미입력")
        if presence_value(yn) is None:
            continue
        item = by_code(code)
        if item is None:
            continue
        survey = {"yn": yn, "text": answer.get("text", ""),
                  "source": "survey_form", "node_ids": answer.get("node_ids", [])}
        entry = extraction.mapped.get(code)
        if entry is not None:
            # 문서에서 읽은 값과 근거는 그대로 두고, 사용자 응답을 별도로 전달한다.
            entry["survey_answer"] = survey
            continue
        in_profile = code in extraction.missing
        extraction.mapped[code] = {
            "code": code, "name": item.name, "area": item.area,
            "category": item.category, "data_type": item.data_type,
            "value": yn, "unit": "", "note": survey["text"] or None,
            "source_tier": "survey", "survey_answer": survey,
            "evidence_node_ids": [], "survey_node_ids": survey["node_ids"],
            "beyond_profile": not in_profile,
        }
        if in_profile:
            extraction.missing.remove(code)
            injected += 1
            if item.area in extraction.by_area:
                extraction.by_area[item.area]["present"] += 1
        elif code not in extraction.beyond_profile:
            extraction.beyond_profile.append(code)
    if injected:
        n = sum(c not in extraction.beyond_profile for c in extraction.mapped)
        denominator = n + len(extraction.missing)
        extraction.coverage_pct = 100.0 * n / denominator if denominator else 0.0
