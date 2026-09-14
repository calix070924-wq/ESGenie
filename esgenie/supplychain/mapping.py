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
import re

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
        return _derive_numeric(q, mapped, missing, dp_by_code, claims if claims is not None else {}, evidence_index)
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


def _derive_numeric(q, mapped, missing, dp_by_code, claims=None, evidence_index=None) -> Answer:
    claims = claims if claims is not None else {}
    code = q.primary_code
    evid_value: Any = None
    evid_unit = ""
    dp = dp_by_code.get(code)
    if dp is not None and dp.value is not None:
        from ..schemas import AxisScore, format_score
        status = _VERIF_TO_STATUS.get(dp.verification, "self_reported")
        evid_value, evid_unit = dp.value, dp.unit
        ans = _base(
            q, value=dp.value, status=status,
            evidence_links=list(dp.evidence_files),
            unit=dp.unit, period=dp.period, confidence_flags=list(dp.confidence_flags),
            rationale=f"{dp.kesg_name} = {dp.value}{dp.unit} "
                      f"(보고 연도 {dp.period}, D1 위험 {format_score(dp.d1_risk)}, 검증={dp.verification})"
                      + (f" · {AxisScore(0, evaluation=dp.d1_evaluation).coverage_label}" if dp.d1_evaluation else "")
                      + (f" · 신뢰 정보: {', '.join(dp.confidence_flags)}" if dp.confidence_flags else ""),
        )
    else:
        entry = mapped.get(code)
        if entry is not None and entry.get("value") is not None:
            evid_value, evid_unit = entry.get("value"), entry.get("unit", "")
            ans = _base(
                q, value=entry.get("value"), status="self_reported",
                unit=entry.get("unit", ""), period=entry.get("period"),
                rationale=f"{entry.get('name', code)} = {entry.get('value')}"
                          f"{entry.get('unit', '')} (증빙 미연결, 자가신고)",
            )
        else:
            status, request, ev_needed = _unresolved(
                q, f"{code} 증빙 없음 — 해당 수치를 입증할 고지서/명세서 업로드 필요")
            ans = _base(q, value=None, status=status, rationale=request,
                        evidence_needed=ev_needed)
    if dp is not None:
        links = [evidence_index.get(e.node_id) if evidence_index is not None else e
                 for e in dp.evidence_files]
        valid_links = [e for e in links if _valid_link(e, code)]
        ans.evidence_links = valid_links
        if ans.status == "verified" and not valid_links:
            ans.status = "self_reported"
            ans.flags.append("증빙 미확인: 실제 노드·주제·원문·독립 출처 확인 필요")
    evid_num = _as_number(evid_value)
    if evid_value is not None:
        invalid = _invalid_number_reason(evid_value, evid_unit, code)
        if invalid:
            ans.status = "flagged"
            ans.flags.append(f"증빙값 검토필요: {invalid}")
        # 구버전 비율 단위 복구는 실제 원문에 같은 값의 % 표기가 있을 때만 허용한다.
        if code in _RATE_KESG_CODES and not _looks_like_rate_unit(evid_unit) and evid_num is not None and 0 <= evid_num <= 100:
            entry = mapped.get(code, {})
            recovery = entry.get("unit_recovery") or {}
            quotes = [e.quote for e in ans.evidence_links] + [recovery.get("raw", "")]
            if any(_quote_confirms_rate(raw, evid_num) for raw in quotes):
                evid_unit = "%"
                ans.unit = "%"
                ans.flags.append(f"단위 복구: 원문의 {evid_num}% 표기 확인 (입력 단위 {getattr(dp, 'unit', entry.get('unit', ''))})")
    for diagnostic in getattr(claims, "diagnostics", []):
        if diagnostic.get("code") == code:
            ans.flags.append(f"자가주장 제외: {diagnostic.get('reason')} · {diagnostic.get('raw', '')} [{diagnostic.get('source')}]")
    # ── 협력사 자가주장 대조 (D1) ──────────────────────────────────────────
    claim = claims.get(code)
    if claim is not None:
        ans = _reconcile_claim(ans, claim, evid_value, evid_unit, code=code)
    return ans


def _as_number(v: Any) -> float | None:
    from ..ssot.selection import finite_number
    from ..rag_gates.units import parse_number
    if isinstance(v, str):
        parsed = parse_number(v)
        return finite_number(parsed)
    return finite_number(v)


def _looks_like_rate_unit(unit: str) -> bool:
    return str(unit or "").strip().lower() in {"%", "pct", "percent", "퍼센트", "비율", "이용률"}


def _quote_confirms_rate(raw, value):
    return any(_as_number(m.group(1).replace("−", "-")) == value
               for m in re.finditer(r"([+\-−]?\d+(?:\.\d+)?)\s*%", raw))


def _invalid_number_reason(value, unit, code):
    number = _as_number(value)
    if number is None:
        return f"유한한 수치가 아님: {value!r}"
    if (_looks_like_rate_unit(unit) or code in _RATE_KESG_CODES) and not 0 <= number <= 100:
        return f"비율 범위(0~100%)를 벗어남: {value}{unit}"
    return ""


def _reconcile_claim(ans: Answer, claim: Any, evid_value: Any, evid_unit: str, *, code: str = "") -> Answer:
    """양쪽 값·차원을 검증한 뒤 비교한다. 비율은 10%p, 그 외는 D1 상대오차 기준."""
    from ..config import D1_THRESHOLD
    from ..rag_gates.units import normalize_unit, convert_to_common
    cunit = getattr(claim, "unit", "") or ""
    cvalue = getattr(claim, "value", None)
    craw, csrc = getattr(claim, "raw", ""), getattr(claim, "source", "")
    ans.self_reports.append(vars(claim).copy())
    if getattr(claim, "status", "reported") == "ambiguous":
        ans.status = "flagged"
        ans.flags.extend(getattr(claim, "diagnostics", []) or ["상충하는 실적 주장 — 확정 불가"])
        ans.rationale += f" · 자가주장 검토필요: {craw} [{csrc}]"
        return ans
    reason = _invalid_number_reason(cvalue, cunit, code)
    if reason:
        ans.status = "flagged"
        ans.flags.append(f"자가주장 검토필요: {reason} · {craw} [{csrc}]")
        return ans
    cval = _as_number(cvalue)
    if evid_value is None:
        ans.value, ans.unit, ans.period = cval, cunit, getattr(claim, "period", None)
        ans.status = "self_reported"
        ans.flags.append(f"자가신고(증빙 미연결): {craw} [{csrc}]")
        ans.rationale += f" · 자가주장 {cval}{cunit} (독립 증빙 없음)"
        return ans
    reason = _invalid_number_reason(evid_value, evid_unit, code)
    if reason:
        ans.status = "flagged"
        ans.flags.append(f"증빙값 검토필요: {reason}")
        return ans
    evid_num = _as_number(evid_value)
    claim_is_rate, evid_is_rate = _looks_like_rate_unit(cunit), _looks_like_rate_unit(evid_unit)
    if code in _RATE_KESG_CODES and not (claim_is_rate and evid_is_rate):
        ans.status = "flagged"
        ans.flags.append(f"비율(%) 미확보: 자가신고 {cval}{cunit} ↔ 증빙 {evid_num}{evid_unit} — 단위 비교 불가")
        return ans
    cu = "%" if claim_is_rate else normalize_unit(cunit) or cunit.strip()
    eu = "%" if evid_is_rate else normalize_unit(evid_unit) or evid_unit.strip()
    converted = convert_to_common(cval, cu, eu) if cu and eu else None
    if converted is None or _as_number(converted) is None:
        ans.status = "flagged"
        ans.flags.append(f"D1 비교 불가: 단위 {cunit or '미상'} ↔ {evid_unit or '미상'} (차원/환산 확인 필요)")
        return ans
    if claim_is_rate and evid_is_rate:
        difference = abs(converted - evid_num)
        mismatch = difference >= _CLAIM_DISCREPANCY_PP
        description = f"Δ{difference:.2f}%p, 기준 ≥{_CLAIM_DISCREPANCY_PP:g}%p"
    else:
        relative = abs(converted - evid_num) / abs(evid_num) if evid_num else (0.0 if converted == 0 else float("inf"))
        mismatch = relative >= D1_THRESHOLD
        description = (f"상대오차 {relative:.2%}, 기준 ≥{D1_THRESHOLD:.0%} (분모=|증빙값|; "
                       "증빙 0이면 주장 0만 일치)")
    if mismatch:
        ans.status = "flagged"
        ans.flags.append(f"D1 불일치: 자가신고 {cval}{cunit} ↔ 증빙 {evid_num}{evid_unit} ({description}, {csrc})")
        ans.rationale += f" · 자가주장과 증빙 불일치 ({description}) — 소명 필요"
    else:
        ans.flags.append(f"자가신고 일치: {cval}{cunit} ≈ 증빙 {evid_num}{evid_unit} ({description})")
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
