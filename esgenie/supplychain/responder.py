"""공급망 실사 응답 엔진.

(ExtractionResult + DisclosureReport + v15 DataPoint) × Framework → ResponseSheet

새 검출 없음 — 기존 파이프라인 산출물을 양식 칸에 재조립한다.
"""
from __future__ import annotations

from typing import Any

from .frameworks import get_framework
from .gating import apply_gating
from .mapping import derive_answer
from .schema import Answer, Framework, ResponseSheet


def build_response_sheet(
    framework: Framework | str,
    *,
    corp_name: str = "",
    extraction: Any | None = None,        # layer1_extract.ExtractionResult
    disclosure: Any | None = None,        # layer3_disclosure.DisclosureReport
    issb_gap: Any | None = None,          # issb_gap.ISSBGapReport
    data_points: list[Any] | None = None,  # ssot.audit_trace.DataPoint
    evidence_graph: Any | None = None,    # ssot.evidence_graph.EvidenceGraph
    supplier_claims: dict[str, Any] | None = None,  # code → claims.SupplierClaim
) -> ResponseSheet:
    """양식을 ESGenie 산출물로 자동 응답한다.

    인자가 모두 None이어도 동작한다(전부 '데이터부족' → 회귀 가드/빈 입력 안전).
    supplier_claims가 주어지면 협력사 자가주장 ↔ 증빙 대조(D1)를 수행해 과장을 flagged 처리.
    """
    fw = get_framework(framework) if isinstance(framework, str) else framework

    mapped: dict[str, dict[str, Any]] = getattr(extraction, "mapped", {}) or {}
    missing: set[str] = set(getattr(extraction, "missing", []) or [])
    dp_by_code = {dp.kesg_code: dp for dp in (data_points or [])}
    evidence_index = _build_evidence_index(evidence_graph)

    answers: list[Answer] = []
    for q in fw.questions:
        ans = derive_answer(
            q,
            mapped=mapped,
            missing=missing,
            dp_by_code=dp_by_code,
            evidence_index=evidence_index,
            claims=supplier_claims or {},
        )
        ans = apply_gating(ans, q, disclosure=disclosure, issb_gap=issb_gap)
        answers.append(ans)

    sheet = ResponseSheet(
        framework_key=fw.key,
        framework_label=fw.label,
        corp_name=corp_name or getattr(extraction, "corp_name", "") or "",
        answers=answers,
        gaps=_build_gaps(answers),
    )
    return sheet


def respond_from_pipeline(
    pipeline_output: Any,
    framework: str | Framework,
    *,
    supplier_claims: dict[str, Any] | None = None,
) -> ResponseSheet:
    """PipelineOutput에서 필요한 산출물을 뽑아 응답서를 만든다(편의 함수).

    pipeline.run()이 반환한 객체를 그대로 넘기면 된다. pipeline.run 시그니처는
    건드리지 않으므로 기존 동작에 회귀 없음.
    supplier_claims(선택): 협력사 자가주장 {code: SupplierClaim} — 증빙과 대조해 과장 검출.
    """
    v15 = getattr(pipeline_output, "v15_trace", None)
    data_points = list(getattr(v15, "data_points", []) or []) if v15 else []
    return build_response_sheet(
        framework,
        corp_name=getattr(getattr(pipeline_output, "report", None), "corp_name", "")
                  or getattr(pipeline_output, "corp_name", "") or "",
        extraction=getattr(pipeline_output, "extraction", None),
        disclosure=getattr(pipeline_output, "disclosure", None),
        issb_gap=getattr(pipeline_output, "issb_gap", None),
        data_points=data_points,
        evidence_graph=getattr(pipeline_output, "evidence_graph", None),
        supplier_claims=supplier_claims,
    )


def _build_gaps(answers: list[Answer]) -> list[str]:
    gaps: list[str] = []
    for a in answers:
        if a.status == "insufficient":
            detail = a.rationale or "증빙 업로드 필요"
            gaps.append(f"[보완] {a.question_text} — {detail}")
        elif a.status == "hitl_required":
            detail = a.rationale or "담당자 직접 작성 필요"
            gaps.append(f"[작성] {a.question_text} — {detail}")
        elif a.status == "flagged":
            why = "; ".join(a.flags) or "검토 필요"
            gaps.append(f"[검토] {a.question_text} — {why}")
        else:
            # 체크형의 부분 미충족도 보완 대상으로 노출
            for f in a.flags:
                if f.startswith("미충족"):
                    gaps.append(f"[보완] {a.question_text} — {f}")
    return gaps


def _build_evidence_index(evidence_graph: Any | None) -> dict[str, Any]:
    """node_id → EvidenceLink 메타 인덱스.

    존재형 답변은 extraction.mapped.evidence_node_ids만 가지고 있으므로, responder에서
    SSOT graph를 조회해 파일명/페이지/bbox를 다시 붙인다.
    """
    if evidence_graph is None:
        return {}

    from ..ssot.audit_trace import EvidenceLink

    index: dict[str, EvidenceLink] = {}
    for node in getattr(evidence_graph, "nodes", {}).values():
        file_name = node.source_file or node.source or node.id
        relative_path = f"evidence_pack/{node.source_file}" if node.source_file else ""
        index[node.id] = EvidenceLink(
            file_name=file_name,
            relative_path=relative_path,
            origin=node.origin,
            bbox=node.bbox,
            page=node.page,
            node_id=node.id,
        )

    for node in getattr(evidence_graph, "text_nodes", {}).values():
        relative_path = f"evidence_pack/{node.source_file}" if node.source_file else ""
        index[node.id] = EvidenceLink(
            file_name=node.source_file or node.id,
            relative_path=relative_path,
            origin=node.origin,
            page=node.page,
            node_id=node.id,
        )

    return index
