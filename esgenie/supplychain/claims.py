"""협력사 자가주장(self-claim) 로딩 — 증빙과 대조할 '주장 채널'.

공급망 실사에서 핵심은 **협력사가 스스로 보고한 값 ↔ 증빙에서 검증된 값**의 대조다.
상장사 파이프라인의 D1(보고서 주장 vs 증빙)에 대응하는, SME용 주장 채널을 제공한다.

두 경로를 병합한다.
  1) 업로드한 OEM SAQ(협력사가 기입해 제출) 텍스트에서 자가응답 수치 파싱
  2) 설문/입력 필드로 직접 주입한 수치

여기서는 검출/판정을 하지 않는다 — 주장값을 K-ESG 코드에 실어 mapping 으로 넘길 뿐.
대조(불일치 → flagged)는 mapping._reconcile_claim 이 담당한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SupplierClaim:
    """협력사가 자가보고한 단일 수치."""
    code: str                 # K-ESG 코드 (예: "E-6-2")
    value: float | None       # 주장값 (비율이면 % 단위 숫자)
    unit: str = "%"
    raw: str = ""             # 원문 (예: "재활용률 92% 달성")
    source: str = "manual"    # "saq:파일명" | "manual"
    period: int | None = None
    page: int | None = None
    position: int | None = None
    status: str = "reported"
    diagnostics: list[str] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    boundary: dict[str, Any] = field(default_factory=dict)
    # 답변 행의 문맥(2026-10-05 §3 05): 문항 ID·요청 내용·작성 메모·메모가 가리킨 문서 ID, 그리고
    # 응답서 생성 직전에 붙이는 값 추적(`trace_claim_values`). 대조 단계가 원문 근거를 들어 설명한다.
    context: dict[str, Any] = field(default_factory=dict)


class ClaimSet(dict):
    """선택된 주장과, 실적으로 채택하지 않은 원문의 진단 기록."""
    def __init__(self, *args, diagnostics=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.diagnostics = list(diagnostics or [])


# ── SAQ 자가응답 → K-ESG 코드 파싱 규칙 ──────────────────────────────────────
# (정규식, 코드, 단위, value 변환). value 변환은 매치 그룹(float)을 받아 최종값 반환.
_SIGNED_RATE = r"([+\-−]?\d+(?:\.\d+)?)\s*%"
_CLAIM_PATTERNS = [
    (re.compile(r"재활용[^0-9%+\-−\n]{0,24}?" + _SIGNED_RATE), lambda v: v),
    (re.compile(r"(?:매립[·ㆍ\s]*소각|소각[·ㆍ\s]*매립)[^0-9%+\-−\n]{0,12}?" + _SIGNED_RATE),
     lambda v: 100.0 - v),
]
_TARGET = re.compile(r"목표|계획|전망|예정|지향|추진|target|plan|forecast", re.I)
_BOUNDARY = re.compile(r"[\n\f;/|]+|\.(?=\s|$)|(?=20\d{2}\s*년)")
_QUESTION = re.compile(r"[?？]|(?:인가|하는가|되는가|있는가|있습니까|합니까)\s*$")
_YEAR = re.compile(r"(20\d{2})\s*년")
_QUESTION_ID = re.compile(r"^\s*([A-Z]{1,3}-\d{1,3})\s*$")
_DOCUMENT_ID = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2,}-[A-Z]{2,}-\d{4,}(?!\d)")
_NOTE_WORDS = re.compile(r"메모|근거|출처|옮겨")
# 회사 답변 값을 다른 지표 근거와 잇는 낱말(재활용률 문항). 메모가 문서를 가리키지 않을 때만 쓴다.
_RECYCLE_VOCAB = re.compile(r"재활용|재투입|재사용|스크랩|폐기물|순환")

_SAQ_FILENAME_HINTS = (
    "saq",
    "자가진단",
    "설문",
    "questionnaire",
    "self-assessment",
    "self_assessment",
)
_SAQ_TEXT_HINTS = (
    "자가진단",
    "questionnaire",
    "self-assessment",
    "drive sustainability",
    "supplier sustainability",
)


def _extract_text(pdf_path: str) -> str:
    """PDF 1차 텍스트 추출 (pymupdf 우선, 없으면 pdftotext, 그것도 없으면 빈 문자열)."""
    try:
        import fitz  # PyMuPDF
        with fitz.open(pdf_path) as doc:
            return "\f".join(pg.get_text() for pg in doc)
    except Exception:
        pass
    try:
        import subprocess
        out = subprocess.run(["pdftotext", "-layout", pdf_path, "-"],
                             capture_output=True, text=True, timeout=30)
        if out.returncode == 0:
            return out.stdout
    except Exception:
        pass
    return ""


def is_saq_upload(file_path: str, *, file_name: str = "") -> bool:
    """업로드 파일이 OEM/협력사 SAQ(자가진단 설문)인지 가볍게 판별한다.

    1) 파일명 힌트 우선
    2) PDF면 임베디드 텍스트를 읽어 SAQ 시그니처 재확인
    """
    if Path(file_path).suffix.lower() != ".pdf":
        return False

    haystack = f"{Path(file_name or file_path).name} {Path(file_path).stem}".lower()
    if any(hint in haystack for hint in _SAQ_FILENAME_HINTS):
        return True

    text = _extract_text(file_path).lower()
    if not text:
        return False
    return any(hint in text for hint in _SAQ_TEXT_HINTS)


def parse_saq_claims(pdf_paths: list[str]) -> ClaimSet:
    """재활용 비율의 실적만 선택한다. 목표·오류·상충하는 실적은 진단을 남긴다."""
    claims = ClaimSet()
    candidates = []
    for path in pdf_paths:
        text = _extract_text(path)
        if not text:
            claims.diagnostics.append({"code": "E-6-2", "source": f"saq:{Path(path).name}",
                                       "reason": "text_unavailable"})
            continue
        offset = 0
        for boundary in list(_BOUNDARY.finditer(text)) + [None]:
            end = boundary.start() if boundary else len(text)
            segment = text[offset:end]
            matches = sorted([(m, conv) for pat, conv in _CLAIM_PATTERNS
                              for m in pat.finditer(segment)], key=lambda pair: pair[0].start())
            for i, (match, convert) in enumerate(matches):
                # 숫자 뒤 목표 표기도 이 행/문장 안에서 확인. 다음 실적 행에는 전파하지 않는다.
                stop = matches[i + 1][0].start() if i + 1 < len(matches) else len(segment)
                context = segment[(0 if i == 0 else match.start()):stop].strip()
                year = _YEAR.search(context)
                raw_value = float(match.group(1).replace("−", "-"))
                record = {"code": "E-6-2", "raw": context, "source": f"saq:{Path(path).name}",
                          "period": int(year.group(1)) if year else None,
                          "page": text[:offset + match.start()].count("\f"),
                          "position": offset + match.start(), "input_value": raw_value,
                          "boundary": _stated_scope(context),
                          "context": _answer_context(text, offset + match.start())}
                if _QUESTION.search(context):
                    claims.diagnostics.append(dict(record, reason="question_not_answer"))
                elif _TARGET.search(context):
                    claims.diagnostics.append(dict(record, reason="target_not_actual"))
                elif not 0 <= raw_value <= 100:
                    claims.diagnostics.append(dict(record, reason="invalid_rate"))
                else:
                    candidates.append(dict(record, value=convert(raw_value)))
            offset = boundary.end() if boundary else len(text)
    if candidates:
        identities = {(c["value"], c["period"]) for c in candidates}
        # 여러 연도 또는 서로 다른 실적은 보고기간 선택 없이 첫 값을 확정하지 않는다.
        ambiguous = len(identities) > 1
        first = candidates[0]
        claims["E-6-2"] = SupplierClaim(
            "E-6-2", None if ambiguous else first["value"], "%",
            raw=" / ".join(dict.fromkeys(c["raw"] for c in candidates)),
            source=" / ".join(dict.fromkeys(c["source"] for c in candidates)),
            period=None if ambiguous else first["period"], page=first["page"], position=first["position"],
            status="ambiguous" if ambiguous else "reported",
            diagnostics=["여러 실적 주장값/연도가 상충하여 확정 불가"] if ambiguous else [],
            candidates=candidates,
            boundary={} if ambiguous else first["boundary"],
            context={} if ambiguous else first["context"])
    return claims


def _stated_scope(raw: str) -> dict[str, Any]:
    """답변 문장에 **직접 적힌** 기간·사업장·분모만 담은 경계. 추정한 값은 넣지 않는다."""
    from ..ssot.boundary import derive_boundary
    b = derive_boundary(raw, raw).to_dict()
    inferred = set(b.get("inferred") or ())
    keep = {}
    if b.get("period_start") and b.get("period_end"):
        keep.update({k: b[k] for k in ("period_year", "period_start", "period_end", "aggregation",
                                        "coverage_months") if b.get(k)})
    if b.get("site") and b.get("site_scope") not in (None, "", "unknown"):
        keep.update({k: b[k] for k in ("site", "site_scope", "site_path") if b.get(k)})
    if b.get("denominator_kind") not in (None, "", "unknown") and "denominator_kind" not in inferred:
        keep.update(denominator=b.get("denominator"), denominator_kind=b["denominator_kind"])
    return keep


def _answer_context(text: str, position: int) -> dict[str, Any]:
    """답변이 놓인 표 행의 문항 ID·요청 내용과, 같은 문항을 가리키는 작성 메모(문서 ID 포함)."""
    before = [line.strip() for line in text[:position].splitlines()]
    while before and not before[-1]:
        before.pop()
    if before:
        before.pop()                        # 답변 자신이 시작된 줄(같은 칸의 앞부분)
    qid, request = "", []
    for line in reversed(before[-4:]):
        m = _QUESTION_ID.match(line)
        if m:
            qid = m.group(1)
            break
        if line:
            request.insert(0, line)
    if not qid:
        return {"scope_from": "answer_text"}
    notes = [line.strip() for line in text.splitlines()
             if qid in line and _NOTE_WORDS.search(line) and not _QUESTION_ID.match(line)]
    return {"scope_from": "answer_text", "question_id": qid, "request": " ".join(request) if len(request) <= 2 else "",
            "source_note": notes[0] if notes else "",
            "source_ids": sorted({i for note in notes for i in _DOCUMENT_ID.findall(note)})}


def trace_claim_values(claims: "ClaimSet | dict[str, SupplierClaim] | None", pipeline_output: Any):
    """회사 답변 값과 **같은 값**을 가진 다른 지표 근거를 찾아 답변 문맥에 붙인다(새 판정 없음).

    같은 요청 지표에 다른 분모의 값을 옮긴 경우(한울정밀 05: 외부 위탁 폐기물 재활용률 칸에 공정 스크랩
    내부 재투입률 92%)를 원문 근거로 설명하기 위한 재료다. 요청 지표의 대표 근거에 적힌 계산식·분모 설명도
    함께 담는다.

    PR71 검토 R5: 값·단위·재활용 낱말만 보고 다른 기간·사업장·목표값이나, 작성 메모가 가리킨 문서를 찾지
    못했을 때 무관한 문서의 같은 92%를 '옮긴 출처'로 지목했다. 값이 같다는 것은 탐색 단서일 뿐이다.
      - `value_trace`(옮긴 출처): 작성 메모의 문서 ID가 **그 근거 문서 원문에** 있는 후보만. 후보의
        기간·사업장·실적/목표 대조(`scope_check`)를 함께 싣는다 — 다른 범위면 호출부가 그 불일치를 설명한다.
      - `value_leads`(탐색 단서): 직접 연결 없이 값만 같은 재활용 관련 후보. 출처로 설명하지 않는다.
      - `note_link`: 메모의 문서 ID와 찾은 문서·찾지 못한 ID. 찾지 못했으면 다른 문서로 대신하지 않는다.
    같은 92%인 교육 참석률은 단서로도 고르지 않는다.
    """
    from dataclasses import replace
    if not claims:
        return claims
    graph = getattr(pipeline_output, "evidence_graph", None)
    if graph is None:
        return claims
    texts = {ext.source_file: str(getattr(ext, "raw_text", "") or "")
             for ext in getattr(pipeline_output, "ocr_extractions", []) or []}
    out = ClaimSet(diagnostics=getattr(claims, "diagnostics", []))
    for code, claim in claims.items():
        value = getattr(claim, "value", None)
        context = dict(getattr(claim, "context", {}) or {})
        if value is None:
            out[code] = claim
            continue
        ids = context.get("source_ids") or []
        linked_files = sorted(f for f, text in texts.items() if any(i in text for i in ids))
        if ids:
            context["note_link"] = {"source_ids": list(ids), "found_in": linked_files,
                                    "missing": [i for i in ids if not any(i in t for t in texts.values())]}
        matches = []
        for node in graph.nodes.values():
            if node.metric == code or str(node.unit).strip() not in ("%", "％"):
                continue
            try:
                if abs(float(node.value) - float(value)) >= 0.05:
                    continue
            except (TypeError, ValueError):
                continue
            matches.append({"node_id": node.id, "source_file": node.source_file, "metric": node.metric,
                            "value": node.value, "unit": node.unit, "page": node.page,
                            "quote": _basis_line(node), "linked_by_note": node.source_file in linked_files,
                            "scope_check": _trace_scope(node, getattr(claim, "boundary", None) or {})})
        context["value_trace"] = [m for m in matches if m["linked_by_note"]]
        leads = [m for m in matches if not m["linked_by_note"] and _RECYCLE_VOCAB.search(m["metric"])]
        if leads:
            context["value_leads"] = leads
        fact = (getattr(graph, "resolved_facts", {}) or {}).get(code)
        rep = [graph.nodes[n] for n in (fact.representative_node_ids if fact else []) if n in graph.nodes]
        if rep:
            context["evidence_basis"] = {"source_file": rep[0].source_file, "value": rep[0].value,
                                         "unit": rep[0].unit, "basis": _basis_line(rep[0])}
        out[code] = replace(claim, context=context)
    return out


def _trace_scope(node: Any, stated: dict[str, Any]) -> dict[str, str]:
    """같은 값 후보의 기간·사업장·실적/목표를 회사 답변에 적힌 범위와 대조한다.

    period·site: same(같음) | different(다름) | not_stated(어느 한쪽에 없음). 기간은 원문 구간이 같을 때만
    same이다 — 겹치기만 하면(연간 ↔ 4월) different. role: actual | target(목표·계획) | unknown.
    """
    b = getattr(node, "boundary", None)
    get = (lambda k: getattr(b, k, None)) if b is not None else (lambda k: None)
    start, end = str(get("period_start") or ""), str(get("period_end") or "")
    s_start, s_end = str(stated.get("period_start") or ""), str(stated.get("period_end") or "")
    year = get("period_year") or getattr(node, "period", None)
    if start and end and s_start and s_end:
        period = "same" if (start, end) == (s_start, s_end) else "different"
    elif year and s_start and str(year) != s_start[:4]:
        period = "different"
    else:
        period = "not_stated"
    site, s_site = re.sub(r"\s+", "", str(get("site") or "")), re.sub(r"\s+", "", str(stated.get("site") or ""))
    if site and s_site:
        # 지역을 뺀 표기(`제1공장`)는 지역이 붙은 같은 사업장(`김해제1공장`)과 같다고 본다 — 번호·종류는 같아야 한다.
        same = site == s_site or (site.endswith(s_site) or s_site.endswith(site)) and \
            re.search(r"제?\d+(?:공장|사업장)$", min(site, s_site, key=len))
        site_state = "same" if same else "different"
    else:
        site_state = "not_stated"
    basis = str(get("basis") or "unknown")
    role = "target" if getattr(node, "value_role", "") == "target" or basis in ("target", "plan") \
        else "actual" if basis == "actual" else "unknown"
    return {"period": period, "site": site_state, "role": role,
            "period_text": f"{start}~{end}" if start and end else (str(year) if year else ""),
            "site_text": str(get("site") or "")}


def _basis_line(node: Any) -> str:
    """근거의 계산식·분모 설명 줄(`계산: … ÷ …`). 없으면 인용 첫 줄."""
    quotes = [str(getattr(node, "quote", "") or "")]
    quotes += [str(p.get("quote") or "") for p in (getattr(node.boundary, "provenance", ()) or ())
               if isinstance(p, dict)]
    lines = [line.strip() for q in quotes for line in q.splitlines() if line.strip()]
    calc = [line for line in lines if "÷" in line or line.startswith("계산")]
    # 분자·분모 정의 줄은 완결된 문장까지만 싣는다 — 줄바꿈에서 끊긴 꼬리를 인용처럼 보이지 않게 한다.
    denominators = [line[:line.rfind(".") + 1] for line in lines
                    if line.startswith(("분자", "분모")) and "." in line]
    picked = list(dict.fromkeys(calc[:1] + denominators[:1]))
    return " / ".join(picked) or (lines[0] if lines else "")


def merge_claims(*sources: dict[str, SupplierClaim] | None) -> ClaimSet:
    """뒤쪽(수동입력) 우선 정책을 유지하고 파서 진단도 보존한다."""
    merged = ClaimSet()
    for src in sources:
        if src is not None:
            merged.update(src)
            merged.diagnostics.extend(getattr(src, "diagnostics", []))
    return merged


def manual_claims(values: dict[str, float], unit: str = "%") -> dict[str, SupplierClaim]:
    """수동 입력 {code: value} → SupplierClaim 맵."""
    return {
        code: SupplierClaim(code=code, value=float(v), unit=unit,
                            raw=f"{v}{unit} (수동입력)", source="manual")
        for code, v in values.items()
    }
