"""제출 전 증빙 체크리스트 (STEP 4).

> 계획: `docs/다음작업_ESG커버리지_계획.md` STEP 4 — 수직 슬라이스 관통.

ResponseSheet의 미해소·검토필요 답변을, 협력사가 바로 실행할 수 있는 체크리스트로
환원한다. 각 항목은 "무엇을 / 어떻게 하면 풀리는가"를 담는다.

  · insufficient   → [증빙 업로드]  evidence_needed 문서를 올리면 자동 해소
  · hitl_required  → [담당자 작성]  증빙으로는 안 풀림 — 사람이 직접 서술
  · flagged        → [검토·보완]   D1/D6/ISSB 경고 — flags를 보고 소명·보완

새 검출/계산은 없다. derive가 채워둔 status·evidence_needed·rationale·flags를 모으기만 한다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .schema import ResponseSheet

# 체크리스트에 노출하는 상태 → 행동 라벨
_ACTION: dict[str, str] = {
    "insufficient": "증빙 업로드",
    "hitl_required": "담당자 작성",
    "flagged": "검토·보완",
}


@dataclass(frozen=True)
class ChecklistItem:
    qid: str
    section: str
    question_text: str
    status: str
    action: str                  # 증빙 업로드 / 담당자 작성 / 검토·보완
    evidence_needed: tuple[str, ...]  # 올리면 풀리는 문서(있으면)
    request: str                 # 구체 안내문

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["evidence_needed"] = list(self.evidence_needed)
        return d


def build_checklist(
    sheet: ResponseSheet,
    *,
    section: str | None = None,
) -> list[ChecklistItem]:
    """제출 전 처리해야 할 항목만 추려 체크리스트로 반환한다.

    section을 주면 해당 섹션 문항만(수직 슬라이스 점검용).
    verified/self_reported/not_applicable(=이미 처리됨)는 제외한다.
    """
    items: list[ChecklistItem] = []
    for a in sheet.answers:
        if a.status not in _ACTION:
            continue
        if section is not None and a.section != section:
            continue
        detail = a.rationale
        if a.status == "flagged":
            # flagged는 rationale보다 경고(flags)가 핵심
            detail = "; ".join(a.flags) or a.rationale or "검토 필요"
        items.append(ChecklistItem(
            qid=a.qid,
            section=a.section,
            question_text=a.question_text,
            status=a.status,
            action=_ACTION[a.status],
            evidence_needed=tuple(a.evidence_needed),
            request=detail,
        ))
    return items


def checklist_rows(
    sheet: ResponseSheet,
    *,
    section: str | None = None,
) -> list[dict[str, str]]:
    """표(엑셀/UI dataframe) 출력용 평탄화 행."""
    rows: list[dict[str, str]] = []
    for it in build_checklist(sheet, section=section):
        rows.append({
            "문항 ID": it.qid,
            "섹션": it.section,
            "문항": it.question_text,
            "할 일": it.action,
            "올릴 문서 / 작성 사항": " / ".join(it.evidence_needed) if it.evidence_needed else "—",
            "안내": it.request,
        })
    return rows
