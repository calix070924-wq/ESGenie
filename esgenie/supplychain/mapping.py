"""문항 → 답변 도출 규칙 (D6 게이팅 이전의 1차 응답).

3가지 도출 유형
  · 존재형(yes_no / yes_no_evidence): K-ESG 항목 공시 여부 → Yes/No
  · 수치형(numeric)                  : data_points의 확정값 + 증빙 + D1 검증
  · 체크형(multi_select)             : 보기별 K-ESG 코드 충족 → 체크

여기서는 검출을 새로 하지 않는다. 이미 산출된
ExtractionResult.mapped / missing 와 v15 DataPoint 를 양식 칸에 떨군다.
"""
from __future__ import annotations

from typing import Any

from .schema import Answer, Question

# DataPoint.verification → Answer.status 1차 매핑
_VERIF_TO_STATUS = {
    "verified": "verified",
    "estimated": "self_reported",
    "unverified": "flagged",
}


def derive_answer(
    q: Question,
    *,
    mapped: dict[str, dict[str, Any]],
    missing: set[str],
    dp_by_code: dict[str, Any],   # code → ssot.audit_trace.DataPoint
    evidence_index: dict[str, Any] | None = None,  # node_id → EvidenceLink
) -> Answer:
    if q.qtype == "numeric":
        return _derive_numeric(q, mapped, missing, dp_by_code)
    if q.qtype == "multi_select":
        return _derive_multi(q, mapped, missing, evidence_index or {})
    # yes_no / yes_no_evidence / text → 존재형
    return _derive_presence(q, mapped, missing, evidence_index or {})


def _base(q: Question, **kw: Any) -> Answer:
    return Answer(qid=q.qid, section=q.section, question_text=q.text, **kw)


def _derive_numeric(q, mapped, missing, dp_by_code) -> Answer:
    code = q.primary_code
    dp = dp_by_code.get(code)
    if dp is not None and dp.value is not None:
        status = _VERIF_TO_STATUS.get(dp.verification, "self_reported")
        return _base(
            q, value=dp.value, status=status,
            evidence_links=list(dp.evidence_files),
            rationale=f"{dp.kesg_name} = {dp.value}{dp.unit} "
                      f"(D1 위험 {dp.d1_risk}, 검증={dp.verification})",
        )
    # data_point는 없지만 정성/설문으로 값이 들어온 경우
    entry = mapped.get(code)
    if entry is not None and entry.get("value") is not None:
        return _base(
            q, value=entry.get("value"), status="self_reported",
            rationale=f"{entry.get('name', code)} = {entry.get('value')}"
                      f"{entry.get('unit', '')} (증빙 미연결, 자가신고)",
        )
    return _base(
        q, value=None, status="insufficient",
        rationale=f"{code} 증빙 없음 — 해당 수치를 입증할 고지서/명세서 업로드 필요",
    )


def _derive_presence(q, mapped, missing, evidence_index) -> Answer:
    present = [c for c in q.kesg_codes if c in mapped]
    if present:
        ev = _collect_evidence(present, mapped, evidence_index)
        if q.evidence_required and not ev:
            status = "self_reported"
            rationale = "해당 항목 공시됨 — 증빙 문서 업로드 시 '증빙검증'으로 승격"
        else:
            status = "verified" if ev else "self_reported"
            rationale = f"공시 근거: {', '.join(present)}"
        return _base(q, value=True, status=status,
                     evidence_links=ev, rationale=rationale)
    # 공시 안 됨 — 양식이 요구하는 항목이면 보완 안내
    return _base(
        q, value=None, status="insufficient",
        rationale="관련 공시/규정 미확인 — 환경방침서·인증서 등 증빙 업로드 필요",
    )


def _derive_multi(q, mapped, missing, evidence_index) -> Answer:
    ticked: list[str] = []
    not_covered: list[str] = []
    ev_codes: list[str] = []
    for label, codes in q.option_map:
        hit = [c for c in codes if c in mapped]
        if hit:
            ticked.append(label)
            ev_codes.extend(hit)
        else:
            not_covered.append(label)

    if not ticked:
        return _base(
            q, value=[], status="insufficient",
            rationale="해당 영역 공시 없음 — 증빙 업로드 후 자동 체크됨",
        )
    ev = _collect_evidence(ev_codes, mapped, evidence_index)
    status = "verified" if ev else "self_reported"
    rationale = f"{len(ticked)}개 영역 충족"
    if not_covered:
        rationale += f" · 미충족(보완 권장): {', '.join(not_covered)}"
    ans = _base(q, value=ticked, status=status, evidence_links=ev,
                rationale=rationale)
    ans.flags.extend(f"미충족: {lbl}" for lbl in not_covered)
    return ans


def _collect_evidence(codes, mapped, evidence_index):
    """mapped 항목의 evidence_node_ids를 가벼운 EvidenceLink로 변환.

    실제 파일/bbox는 numeric의 DataPoint 경로에서 채워진다. 존재형은
    노드 ID만 알 수 있는 경우가 많아, 노드 ID를 근거로 남긴다.
    """
    from ..ssot.audit_trace import EvidenceLink
    links: list[EvidenceLink] = []
    seen: set[str] = set()
    for c in codes:
        for nid in (mapped.get(c, {}) or {}).get("evidence_node_ids", []) or []:
            if nid in seen:
                continue
            seen.add(nid)
            link = evidence_index.get(nid)
            if link is not None:
                links.append(link)
                continue
            links.append(EvidenceLink(
                file_name=str(nid), relative_path="",
                origin="dart", node_id=str(nid),
            ))
    return links
