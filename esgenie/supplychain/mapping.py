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

from ..knowledge.kesg_evidence_requirements import requirement_for
from .schema import Answer, Question

# DataPoint.verification → Answer.status 1차 매핑
_VERIF_TO_STATUS = {
    "verified": "verified",
    "estimated": "self_reported",
    "unverified": "flagged",
}


# 자가주장 ↔ 증빙 불일치 판정 임계 (절대 %p). 이 이상 벌어지면 D1 불일치 → flagged.
_CLAIM_DISCREPANCY_PP = 10.0
# K-ESG상 단위가 '%(비율)'인 코드 — 다운스트림 단위문자열이 깨져도 비율로 취급.
_RATE_KESG_CODES = {"E-5-2", "E-6-2"}
_RATE_MAX_VALUE = 100.0


def derive_answer(
    q: Question,
    *,
    mapped: dict[str, dict[str, Any]],
    missing: set[str],
    dp_by_code: dict[str, Any],   # code → ssot.audit_trace.DataPoint
    evidence_index: dict[str, Any] | None = None,  # node_id → EvidenceLink
    claims: dict[str, Any] | None = None,  # code → supplychain.claims.SupplierClaim
) -> Answer:
    if q.qtype == "numeric":
        return _derive_numeric(q, mapped, missing, dp_by_code, claims or {})
    if q.qtype == "multi_select":
        return _derive_multi(q, mapped, missing, evidence_index or {})
    # yes_no / yes_no_evidence / text → 존재형
    return _derive_presence(q, mapped, missing, evidence_index or {})


def _base(q: Question, **kw: Any) -> Answer:
    return Answer(qid=q.qid, section=q.section, question_text=q.text, **kw)


def _unresolved(q: Question, fallback: str) -> tuple[str, str, list[str]]:
    """미해소 문항의 (status, 안내문, 올릴문서)를 데이터타입 룩업으로 결정한다(STEP 3·4).

    · 정성·서술필요(human_narrative) → hitl_required(증빙 올려도 사람이 서술해야 함)
    · 그 외(정량/공시존재형/정성-증빙형) → insufficient(증빙 올리면 풀림)
    안내문은 kesg_evidence_requirements의 구체적 request를 쓴다(없으면 fallback).
    olril문서(evidence_needed)는 체크리스트/exporter/UI가 재사용한다.
    새 검출은 하지 않는다 — 룩업 조회만.
    """
    code = q.primary_code
    if not code:
        return "insufficient", fallback, []
    req = requirement_for(code)
    status = "hitl_required" if req.human_narrative else "insufficient"
    return status, (req.request or fallback), list(req.evidence_types)


def _derive_numeric(q, mapped, missing, dp_by_code, claims=None) -> Answer:
    claims = claims or {}
    code = q.primary_code
    evid_value: Any = None
    evid_unit = ""
    dp = dp_by_code.get(code)
    if dp is not None and dp.value is not None:
        status = _VERIF_TO_STATUS.get(dp.verification, "self_reported")
        evid_value, evid_unit = dp.value, dp.unit
        ans = _base(
            q, value=dp.value, status=status,
            evidence_links=list(dp.evidence_files),
            unit=dp.unit, period=dp.period, confidence_flags=list(dp.confidence_flags),
            rationale=f"{dp.kesg_name} = {dp.value}{dp.unit} "
                      f"(보고 연도 {dp.period}, D1 위험 {dp.d1_risk}, 검증={dp.verification})"
                      + (f" · 신뢰 정보: {', '.join(dp.confidence_flags)}" if dp.confidence_flags else ""),
        )
    else:
        entry = mapped.get(code)
        if entry is not None and entry.get("value") is not None:
            evid_value, evid_unit = entry.get("value"), entry.get("unit", "")
            ans = _base(
                q, value=entry.get("value"), status="self_reported",
                rationale=f"{entry.get('name', code)} = {entry.get('value')}"
                          f"{entry.get('unit', '')} (증빙 미연결, 자가신고)",
            )
        else:
            status, request, ev_needed = _unresolved(
                q, f"{code} 증빙 없음 — 해당 수치를 입증할 고지서/명세서 업로드 필요")
            ans = _base(q, value=None, status=status, rationale=request,
                        evidence_needed=ev_needed)
    # ── 협력사 자가주장 대조 (D1) ──────────────────────────────────────────
    claim = claims.get(code)
    if claim is not None:
        ans = _reconcile_claim(ans, claim, evid_value, evid_unit, code=code)
    return ans


def _as_number(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _looks_like_rate_unit(unit: str) -> bool:
    unit = unit or ""
    return "%" in unit or "비율" in unit or "이용률" in unit or unit.lower() == "pct"


def _reconcile_claim(
    ans: Answer,
    claim: Any,
    evid_value: Any,
    evid_unit: str,
    *,
    code: str = "",
) -> Answer:
    """자가주장값을 증빙값과 대조해 신뢰상태를 덧씌운다.

    · 증빙 있음 + 괴리 큼 → flagged (자가신고 과장 의심, D1 불일치)
    · 증빙 있음 + 일치     → 기존 상태 유지(자가신고 일치 메모)
    · 증빙 없음            → self_reported (응답은 있으나 증빙 미연결)
    """
    cval = _as_number(getattr(claim, "value", None))
    cunit = getattr(claim, "unit", "") or ""
    craw = getattr(claim, "raw", "") or ""
    csrc = getattr(claim, "source", "") or ""
    if cval is None:
        return ans

    evid_num = _as_number(evid_value)
    if evid_num is None:
        # 증빙 없음 → 자가신고만 기록
        ans.value = cval
        ans.status = "self_reported"
        ans.flags.append(f"자가신고(증빙 미연결): {craw} [{csrc}]")
        ans.rationale = (ans.rationale + f" · 자가주장 {cval}{cunit} 입력됨(증빙 없음)").strip()
        return ans

    # K-ESG상 '비율(%)' 코드는 다운스트림 단위문자열이 ton 등으로 와도 본질적으로 비율로 본다.
    ccode = code or getattr(claim, "code", "") or ""
    claim_is_rate = _looks_like_rate_unit(cunit) or ccode in _RATE_KESG_CODES
    evid_is_rate = _looks_like_rate_unit(evid_unit)

    # E-6-2 같은 '본질적으로 비율' 코드면 단위 문자열이 깨져도 값 자체를 %로 본다.
    # 다만 0~100 범위를 벗어나면 비율값이 아니라 오추출로 보고 즉시 flagged 처리한다.
    if ccode in _RATE_KESG_CODES and not evid_is_rate:
        if 0.0 <= evid_num <= _RATE_MAX_VALUE:
            evid_is_rate = True
            evid_unit = "%"
        else:
            ans.status = "flagged"
            ans.flags.append(
                f"D1 불일치: {ccode}는 비율 코드인데 증빙값 {evid_num}{evid_unit}이 "
                "비율 범위(0~100%)를 벗어남"
            )
            ans.rationale = (
                ans.rationale
                + f" · ⚠ {ccode}는 비율 코드인데 증빙 추출값 {evid_num}{evid_unit}이 "
                  "0~100% 범위를 벗어남 — OCR/매핑 보정 필요."
            ).strip()
            return ans

    # 단위 불일치(주장은 비율% / 증빙은 톤 등) → %p 비교 불가. 정직하게 '비율 증빙 미확보'로 flagged.
    if claim_is_rate and not evid_is_rate:
        ans.status = "flagged"
        ans.flags.append(
            f"D1 불일치: 자가신고 {cval}{cunit}(비율) ↔ 증빙은 비율(%) 미확보"
            f"(추출 {evid_num}{evid_unit}, {csrc})"
        )
        ans.rationale = (
            ans.rationale
            + f" · ⚠ 자가주장은 재활용 '비율'({cval}{cunit})인데 증빙에서 비율(%)이 확보되지 않음"
              f"(추출값 {evid_num}{evid_unit}) — 재활용 비율 증빙 보완·소명 필요."
        ).strip()
        return ans

    diff = round(abs(cval - evid_num), 1)
    if diff >= _CLAIM_DISCREPANCY_PP:
        ans.status = "flagged"
        ans.flags.append(
            f"D1 불일치: 자가신고 {cval}{cunit} ↔ 증빙 {evid_num}{evid_unit} "
            f"(Δ{diff}%p, {csrc})"
        )
        ans.rationale = (
            ans.rationale
            + f" · ⚠ 자가주장({cval}{cunit})이 증빙({evid_num}{evid_unit})과 {diff}%p 괴리 "
              "— 과장 의심, 실사 시 소명 필요."
        ).strip()
    else:
        ans.flags.append(f"자가신고 일치: {cval}{cunit} ≈ 증빙 {evid_num}{evid_unit}")
    return ans


def _entry_presence(entry):
    from ..survey import presence_value
    survey = entry.get("survey_answer") or {}
    if survey:
        return presence_value(survey.get("yn"))
    value = entry.get("value")
    explicit = presence_value(value)
    if explicit is not None:
        return explicit
    if value is None or str(value).strip() in ("", "미입력"):
        return None
    return True


def _valid_link(link, code):
    return (link is not None and link.resolved and link.independent
            and code in link.kesg_codes and bool(link.quote.strip()))


def _derive_presence(q, mapped, missing, evidence_index) -> Answer:
    present = [c for c in q.kesg_codes if c in mapped
               and _entry_presence(mapped[c]) is not None]
    if present:
        ev = _collect_evidence(present, mapped, evidence_index)
        values = [_entry_presence(mapped[c]) for c in present]
        surveys = [mapped[c]["survey_answer"] for c in present if mapped[c].get("survey_answer")]
        # 명시적인 부정은 항상 보존한다. 독립 문서와의 모순은 별도 검토 대상이다.
        value = False if False in values else True
        conflict = (value is False and bool(ev)) or (False in values and True in values)
        status = "flagged" if conflict else "verified" if ev else "self_reported"
        rationale = f"응답: {'예' if value else '아니오'}"
        if surveys:
            rationale += " · 설문 자가신고: " + " / ".join(
                f"{x['yn']} {x.get('text', '')} [survey_form]".strip() for x in surveys)
        if ev:
            rationale += " · 독립 증빙: " + " / ".join(
                f"{e.file_name}: {e.quote}" for e in ev)
        else:
            rationale += " · 관련 독립 증빙 미확인"
        flags = _evidence_diagnostics(present, mapped, evidence_index)
        if conflict:
            flags.append("설문/응답과 독립 문서 내용이 충돌함 — 원문 검토 필요")
        return _base(q, value=value, status=status, evidence_links=ev,
                     rationale=rationale, flags=flags, self_reports=surveys)
    status, request, ev_needed = _unresolved(
        q, "관련 공시/규정 미확인 — 환경방침서·인증서 등 증빙 업로드 필요")
    return _base(q, value=None, status=status, rationale=request,
                 evidence_needed=ev_needed)


def _derive_multi(q, mapped, missing, evidence_index) -> Answer:
    ticked, not_covered, unproven, links, flags = [], [], [], [], []
    details = {}
    conflict = False
    for label, codes in q.option_map:
        hit = [c for c in codes if c in mapped and _entry_presence(mapped[c]) is True]
        ev = _collect_evidence(hit, mapped, evidence_index)
        negative = [c for c in codes if c in mapped and _entry_presence(mapped[c]) is False]
        if _collect_evidence(negative, mapped, evidence_index):
            conflict = True
            flags.append(f"{label}: 부정 응답과 문서 증빙 충돌")
        if hit:
            ticked.append(label)
            links.extend(ev)
            if not ev:
                unproven.append(label)
        else:
            not_covered.append(label)
        details[label] = {"selected": bool(hit), "verified": bool(ev),
                          "evidence_node_ids": [e.node_id for e in ev]}
        flags.extend(_evidence_diagnostics(hit, mapped, evidence_index))
    if not ticked and not conflict:
        return _base(q, value=[], status="insufficient", option_evidence=details,
                     rationale="해당 영역 공시 없음 — 증빙 업로드 후 자동 체크됨")
    status = "flagged" if conflict else "self_reported" if unproven else "verified"
    rationale = f"{len(ticked)}개 영역 응답 · {len(ticked) - len(unproven)}개 영역 증빙 확인"
    if unproven:
        rationale += " · 부분 충족, 미입증: " + ", ".join(unproven)
    if not_covered:
        rationale += " · 미충족(보완 권장): " + ", ".join(not_covered)
    flags.extend(f"미입증: {label}" for label in unproven)
    flags.extend(f"미충족: {label}" for label in not_covered)
    unique = {e.node_id: e for e in links}
    return _base(q, value=ticked, status=status, evidence_links=list(unique.values()),
                 rationale=rationale, flags=flags, option_evidence=details)


def _evidence_diagnostics(codes, mapped, evidence_index):
    diagnostics = []
    for code in codes:
        for nid in mapped[code].get("evidence_node_ids", []) or []:
            if not _valid_link(evidence_index.get(nid), code):
                diagnostics.append(f"증빙 미확인: {nid} ({code}, 노드/주제/독립 출처 확인 필요)")
    return diagnostics


def _collect_evidence(codes, mapped, evidence_index):
    links = {}
    for code in codes:
        for nid in mapped.get(code, {}).get("evidence_node_ids", []) or []:
            link = evidence_index.get(nid)
            if _valid_link(link, code):
                links[nid] = link
    return list(links.values())
