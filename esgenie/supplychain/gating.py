"""신뢰 게이팅 — 검출 결과(D6/D1)를 1차 답변에 덧씌운다.

검출 점수를 '출력물의 신뢰 근거'로 합치는 지점.
  · D6 고아 비율: 분모 없는 유리 비율 답변 → flagged + 누락 분모 안내
  · D6 민감 누락: 양식이 요구하는 민감 항목이 누락 → 보완 경고
  · D1 불일치  : (이미 mapping에서 verification=unverified → flagged 반영)
"""
from __future__ import annotations

from typing import Any

from ..issb_gap import remediation_text_for
from .schema import Answer, Question


def apply_gating(
    answer: Answer,
    q: Question,
    *,
    disclosure: Any | None,
    issb_gap: Any | None,
) -> Answer:
    if disclosure is None and issb_gap is None:
        return answer

    code = q.primary_code
    if not code:
        return answer

    # ── D6 고아 비율: 유리 비율만 공시·분모 누락 → 선택적 공시 위험 ──
    for orphan in getattr(disclosure, "orphan_ratios", []) or []:
        if orphan.ratio_code == code:
            answer.status = "flagged"
            answer.flags.append(f"D6 선택적 공시: {orphan.detail}")
            answer.rationale += (
                " ⚠ 분모 항목 누락 — 이대로 제출 시 실사 감점 위험. "
                "분모(총량) 증빙을 함께 업로드하세요."
            )
            return answer

    if disclosure is not None:
        # ── D6 민감 항목 누락: 이 문항이 요구하는 코드가 '숨긴 항목'이면 경고 ──
        for omit in getattr(disclosure, "omitted_sensitive", []) or []:
            if omit.code == code and answer.status == "insufficient":
                answer.flags.append(
                    f"D6 민감 항목 누락: {omit.name}(민감도 {omit.sensitivity}) — 보완 권장"
                )
                return answer

    if issb_gap is not None:
        row = _issb_row_for_code(issb_gap, code)
        if row is not None and row.scope == "in_profile" and row.status == "missing":
            remediation = remediation_text_for(row.kesg_code)
            issue = (
                f"ISSB {'/'.join(row.standards)} 연계 누락: {row.name}"
                f" — {' / '.join(row.requirements)}"
            )
            if any(anchor in ("climate", "greenwash_defense") for anchor in row.anchors):
                answer.status = "flagged"
                answer.flags.append(issue)
                answer.rationale += (
                    " ⚠ ISSB 기후/방어 항목 누락 — 실사 대응 시 관련 공시와 증빙을 함께 보완하세요."
                )
                if remediation:
                    answer.flags.append(f"보완 증빙: {remediation}")
                    answer.rationale += f" 권장 증빙: {remediation}."
                return answer
            if answer.status == "insufficient":
                answer.flags.append(issue)
                if remediation:
                    answer.flags.append(f"보완 증빙: {remediation}")

    return answer


def _issb_row_for_code(issb_gap: Any, code: str):
    for row in getattr(issb_gap, "rows", ()) or ():
        if row.kesg_code == code:
            return row
    return None
