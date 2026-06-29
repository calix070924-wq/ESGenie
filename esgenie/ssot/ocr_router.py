"""L0-A — 증빙 문서 OCR 하이브리드 라우터.

중소기업이 업로드한 '날것의 증빙 파일'을 두 채널로 자동 분기한다.

  ┌ 정형(structured)  : 한전 전기요금 고지서, 도시가스 영수증, 올바로 폐기물 대장 …
  │                     → 전통 OCR(레이아웃 보존) + LLM 후처리(키-값 정규화)
  │                       비용 저렴 · 표/숫자 정확도 높음
  │
  └ 비정형(unstructured): 안전보건위원회 회의록, 비상대응 매뉴얼, 사내 규정집 …
                          → VLM 우선(GPT-4o Vision 등) 통째 의미 추출
                            레이아웃 자유도 높고 서술형 정성 데이터에 강함

라우팅 판단 신뢰도가 낮으면(애매하면) 안전하게 VLM 채널로 보낸다.
모든 채널은 동일한 `OcrExtraction` 스키마를 반환 → 하위(evidence_graph)는 채널을 신경 쓰지 않는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


# ====================================================================
# 공통 출력 스키마 (두 채널이 모두 이 형태로 반환)
# ====================================================================

class DocChannel(str, Enum):
    STRUCTURED = "structured"      # 전통 OCR + LLM 후처리
    UNSTRUCTURED = "unstructured"  # VLM 우선


@dataclass
class ExtractedMetric:
    """증빙에서 추출한 단일 정량 수치 (→ EvidenceNode 후보)."""
    metric_hint: str       # 원문 라벨 (예: "사용전력량", "폐기물_소각")
    value: float
    unit: str              # "kWh", "MJ", "ton", "원" …
    period: str            # "2025-12" 또는 "2025" (정규화 전 raw)
    kesg_code_guess: str | None = None   # LLM 후처리가 제안한 K-ESG 코드 (예: "E-4-1")
    bbox: list[float] | None = None      # [x0,y0,x1,y1] 정규화 위치(0~1, 감사 추적용)
    page: int | None = None              # 0-기준 페이지 인덱스 (원본 렌더용)
    confidence: float = 0.0


@dataclass
class ExtractedClause:
    """비정형 문서에서 추출한 정성 텍스트 단위 (→ 텍스트 노드 후보)."""
    section: str           # "비상대응 절차", "근로자 대표 참여" …
    text: str
    kesg_code_guess: str | None = None
    page: int | None = None
    rba_code_guess: str | None = None    # RBA 자가진단 substrate 매칭(고유 조항용)


@dataclass
class TableCell:
    """표 셀 원문 + 위치 메타데이터."""
    row_index: int
    column_index: int
    content: str
    row_span: int = 1
    column_span: int = 1
    kind: str | None = None
    bbox: list[float] | None = None
    page: int | None = None
    confidence: float | None = None


@dataclass
class ExtractedTable:
    """OCR가 복원한 표 구조. Tier 0 게이트와 후속 복원기의 공통 입력."""
    table_id: str
    row_count: int
    column_count: int
    cells: list[TableCell] = field(default_factory=list)
    source: str = ""
    page: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class OcrExtraction:
    """OCR 채널의 통합 산출물."""
    source_file: str                       # 원본 파일명 (감사 증빙 하드링크 키)
    channel: DocChannel
    doc_type: str                          # "kepco_bill" | "waste_ledger" | "safety_minutes" | ...
    metrics: list[ExtractedMetric] = field(default_factory=list)
    clauses: list[ExtractedClause] = field(default_factory=list)
    tables: list[ExtractedTable] = field(default_factory=list)
    raw_text: str = ""                     # 전체 OCR 텍스트 (디버그/재처리용)
    router_meta: dict[str, Any] = field(default_factory=dict)  # 라우팅 근거 기록

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["channel"] = self.channel.value
        return d


# ====================================================================
# 라우터 — 문서 타입 판별
# ====================================================================

# 정형 문서 시그니처: 발급기관/양식 키워드 → doc_type
_STRUCTURED_SIGNATURES: dict[str, list[str]] = {
    "kepco_bill":   ["한국전력", "한전", "전기요금", "청구금액", "사용전력량", "kWh"],
    "gas_bill":     ["도시가스", "가스요금", "사용량", "MJ", "m3"],
    "water_bill":   ["상수도", "수도요금", "급수", "ton", "m3"],
    "waste_ledger": ["올바로", "폐기물", "인계", "처리량", "지정폐기물", "배출자"],
    "fuel_receipt": ["주유", "경유", "휘발유", "리터", "L", "충전"],
}

# 비정형 문서 시그니처: 서술형 문서 → doc_type
_UNSTRUCTURED_SIGNATURES: dict[str, list[str]] = {
    "safety_minutes":  ["산업안전보건위원회", "회의록", "안건", "심의", "근로자 대표"],
    "emergency_manual":["비상대응", "매뉴얼", "대피", "절차", "시나리오"],
    "policy_manual":   ["규정", "방침", "선언", "내규", "준수", "윤리강령"],
    "hr_policy":       ["인권", "차별 금지", "고충처리", "노사", "취업규칙"],
}

# 라우팅 confidence가 이 값보다 낮으면 안전하게 비정형(VLM)로 폴백
_ROUTE_FALLBACK_THRESHOLD = 0.30


@dataclass
class RouteDecision:
    channel: DocChannel
    doc_type: str
    confidence: float
    matched_keywords: list[str]
    rationale: str


def route_document(
    file_path: str,
    *,
    preview_text: str | None = None,
    layout_features: dict[str, Any] | None = None,
) -> RouteDecision:
    """업로드 문서를 정형/비정형 채널로 분기.

    판별 신호(우선순위):
      1) 빠른 텍스트 프리뷰(preview_text): 1페이지 OCR/임베디드 텍스트의 키워드 매칭
      2) 레이아웃 특징(layout_features): 표 비율·셀 격자 밀도 등 (정형일수록 높음)
      3) 확장자/파일명 힌트

    Returns: RouteDecision (채널 + 추정 doc_type + 신뢰도 + 근거)

    NOTE: 실제 키워드 추출은 _quick_preview()를 통해 1페이지만 싸게 처리한다.
    """
    text = (preview_text or _quick_preview(file_path)).lower()
    fname = Path(file_path).name.lower()

    # 표비율은 정형 판별의 핵심 신호다. 명시 주입이 없고 실파일이 있으면 자동 추정한다.
    # preview_text를 직접 준 호출(단위테스트 등)은 자동 추정을 건너뛰어 결정성·비용을 유지한다.
    if layout_features is None and preview_text is None:
        layout_features = estimate_layout_features(file_path)

    structured_hits = _score_signatures(text, fname, _STRUCTURED_SIGNATURES)
    unstructured_hits = _score_signatures(text, fname, _UNSTRUCTURED_SIGNATURES)

    s_best = max(structured_hits.items(), key=lambda kv: kv[1]["score"], default=(None, {"score": 0, "kw": []}))
    u_best = max(unstructured_hits.items(), key=lambda kv: kv[1]["score"], default=(None, {"score": 0, "kw": []}))

    # 레이아웃 표 비율 가산점 (정형 문서는 표 격자가 촘촘)
    table_ratio = float((layout_features or {}).get("table_area_ratio", 0.0))
    s_score = s_best[1]["score"] + 0.4 * table_ratio
    u_score = u_best[1]["score"]

    if s_score >= u_score and s_score >= _ROUTE_FALLBACK_THRESHOLD:
        return RouteDecision(
            channel=DocChannel.STRUCTURED,
            doc_type=s_best[0] or "structured_unknown",
            confidence=round(min(s_score, 1.0), 3),
            matched_keywords=s_best[1]["kw"],
            rationale=f"정형 시그니처 우세(table_ratio={table_ratio:.2f})",
        )
    if u_score >= _ROUTE_FALLBACK_THRESHOLD:
        return RouteDecision(
            channel=DocChannel.UNSTRUCTURED,
            doc_type=u_best[0] or "unstructured_unknown",
            confidence=round(min(u_score, 1.0), 3),
            matched_keywords=u_best[1]["kw"],
            rationale="비정형 시그니처 우세",
        )
    # 애매 → 안전 폴백(VLM)
    return RouteDecision(
        channel=DocChannel.UNSTRUCTURED,
        doc_type="ambiguous_fallback_vlm",
        confidence=round(max(s_score, u_score), 3),
        matched_keywords=(s_best[1]["kw"] + u_best[1]["kw"]),
        rationale="신뢰도 미달 → VLM 폴백",
    )


def extract_document(file_path: str, decision: RouteDecision | None = None) -> OcrExtraction:
    """라우팅 결정에 따라 적절한 채널 추출기를 호출하는 단일 진입점.

    하위(evidence_graph)는 이 함수만 호출하면 채널을 몰라도 된다.
    """
    decision = decision or route_document(file_path)
    if decision.channel is DocChannel.STRUCTURED:
        ext = extract_structured(file_path, doc_type=decision.doc_type)
    else:
        ext = extract_unstructured(file_path, doc_type=decision.doc_type)
    # 채널 추출기가 기록한 router_meta(engine/upstage_error 등)를 보존하고 라우팅 정보만 병합
    ext.router_meta.update({
        "route_confidence": decision.confidence,
        "matched_keywords": decision.matched_keywords,
        "rationale": decision.rationale,
    })
    # 동의어 해소 backstop — 코드 미부여 metric을 사전 매칭으로 채움(전 채널 공통 합류점).
    _backfill_kesg_codes(ext)
    return ext


def _backfill_kesg_codes(ext: OcrExtraction) -> None:
    """kesg_code_guess가 비어 있는 metric을 라벨 동의어 사전으로 결정적 보강한다.

    하이브리드 1단계(결정적 사전)다. 사전이 못 잡으면 코드를 비워 두어 상위 LLM
    폴백/HITL이 처리하게 한다. fuzzy로만 걸린 건 confidence를 낮춰 검증 큐로 보낸다.

    중복 가드: 이미 다른 metric이 점유한 코드(템플릿/본문확정 등 권위 있는 산출물)는
    backfill이 다시 붙이지 않는다. 예) 보조수치 '지정폐기물'(template code=None)이
    E-6-1로 해소돼 본문확정 18.4t와 1000× 어긋난 유령 중복노드를 만드는 사례 차단.
    """
    from ..knowledge.kesg_items import resolve_kesg_code

    taken_codes = {m.kesg_code_guess for m in ext.metrics if m.kesg_code_guess}
    resolved: list[dict[str, Any]] = []
    for m in ext.metrics:
        if m.kesg_code_guess:
            continue
        code, score, method = resolve_kesg_code(m.metric_hint)
        if not code:
            continue
        if code in taken_codes:
            continue  # 이미 점유된 코드 → 중복노드 방지(권위 산출물 우선)
        m.kesg_code_guess = code
        taken_codes.add(code)
        if method == "fuzzy":
            m.confidence = min(m.confidence, 0.5)  # 불확실 → HITL 검증 큐
        resolved.append({"metric_hint": m.metric_hint, "code": code,
                         "score": score, "method": method})
    if resolved:
        ext.router_meta["alias_backfill"] = resolved


def tag_rba_codes(ext: OcrExtraction) -> None:
    """clause에 RBA 코드를 태깅한다(K-ESG 크로스워크 없는 RBA 고유 조항 대응).

    RBA 자가진단 substrate의 고유 항목(근로시간·유해물질·분쟁광물·IP·개인정보 등)은
    K-ESG 증빙풀에 안 걸려 항상 'insufficient'였다. 업로드 규정/매뉴얼의 조항 텍스트를
    RBA search_terms로 결정적 매칭해 코드를 부여 → responder가 해당 칸을 채울 수 있게.
    이미 rba_code_guess가 있으면 존중. 매칭 실패는 None(insufficient 유지 — 거짓경보 방지).
    """
    from ..knowledge.rba_items import resolve_rba_code

    tagged: list[dict[str, Any]] = []
    for c in ext.clauses:
        if c.rba_code_guess:
            continue
        code, score, method = resolve_rba_code(f"{c.section} {c.text}")
        if code:
            c.rba_code_guess = code
            tagged.append({"section": c.section, "rba_code": code,
                           "score": score, "method": method})
    if tagged:
        ext.router_meta["rba_tagging"] = tagged


def ocr_health_report(
    extractions: list["OcrExtraction"],
    evidence_names: list[str],
    *,
    upstage_key_present: bool,
) -> list[tuple[str, str]]:
    """업로드 증빙별 OCR 무음 실패를 (level, message) 목록으로 보고한다.

    extract_structured는 Upstage 호출이 실패하면 pymupdf로 조용히 폴백하고 사유를
    router_meta['upstage_error']에 숨긴다. 키/텍스트가 없으면 mock으로 떨어진다.
    파싱 예외가 나면 _collect_ocr_extractions가 해당 파일을 통째로 누락시킨다.
    이 함수는 그 세 흔적을 모아 UI가 경고를 띄울 수 있게 한다. 정상 추출은
    메시지를 만들지 않는다(노이즈 억제) → '안 도는 것처럼 보이는' 무음 실패만 표면화.

    level: 'error'(추출 실패/폴백) | 'warning'(키 미설정/mock).
    evidence_names: OCR 대상 업로드 증빙 파일명(자가주장 SAQ 제외).
    """
    msgs: list[tuple[str, str]] = []
    if not evidence_names:
        return msgs

    by_file: dict[str, OcrExtraction] = {}
    for e in extractions or []:
        sf = getattr(e, "source_file", None)
        if sf and sf != "survey_form":
            by_file[sf] = e

    if not upstage_key_present:
        msgs.append((
            "warning",
            "UPSTAGE_API_KEY 미설정 — 정형 증빙이 로컬 파서로 폴백됩니다(표·수치 정확도 저하).",
        ))

    for name in evidence_names:
        e = by_file.get(name)
        if e is None:
            msgs.append((
                "error",
                f"{name} — OCR 추출 실패(파싱 예외로 제외). 터미널 로그 확인 필요.",
            ))
            continue
        meta = getattr(e, "router_meta", {}) or {}
        err = meta.get("upstage_error")
        if err:
            short = str(err)
            short = short if len(short) <= 200 else short[:200] + "…"
            msgs.append((
                "error",
                f"{name} — Upstage OCR 실패 → 로컬 폴백. 사유: {short}",
            ))
        elif meta.get("mock"):
            msgs.append((
                "warning",
                f"{name} — Mock 추출(실 OCR 미수행). API 키·네트워크 확인.",
            ))
    return msgs


# ====================================================================
# 채널 A — 정형: 전통 OCR + LLM 후처리   (STUB)
# ====================================================================

def extract_structured(file_path: str, *, doc_type: str) -> OcrExtraction:
    """정형 문서 채널 — Upstage Document Parse + 템플릿 매칭 + LLM 후처리.

    엔진 우선순위:
      1) Upstage Document Parse (UPSTAGE_API_KEY) — 한국어·표(HTML 복원)·좌표
      2) pymupdf + 정규식 (키 불필요) — 디지털 PDF
      3) mock (데모)
    공통 후처리:
      · doc_type 템플릿/키워드로 라벨↔값 1차 추출
      · LLM(gpt-4.1-mini via Azure OpenAI) 단위 정규화 + K-ESG 코드 추정
    """
    if _get_upstage_key():
        try:
            payload = _call_upstage_dp_payload(file_path, ocr_mode="force")
            return _tokens_to_extraction(
                payload["tokens"],
                doc_type=doc_type,
                file_path=file_path,
                engine="upstage_dp",
                tables=payload.get("tables") or [],
                engine_meta={"upstage_model": "document-parse"},
            )
        except Exception as exc:
            # Upstage 실패 → 디지털 PDF 폴백 (데모 안정성)
            ext = _extract_structured_no_llm(file_path, doc_type=doc_type)
            ext.router_meta["upstage_error"] = str(exc)
            return ext

    # OCR 키 없음 → pymupdf + 정규식, 스캔본이면 VLM 에스컬레이션
    return _extract_structured_no_llm(file_path, doc_type=doc_type)


def _tokens_to_extraction(
    tokens: list[dict[str, Any]],
    *,
    doc_type: str,
    file_path: str,
    engine: str,
    tables: list[ExtractedTable] | None = None,
    engine_meta: dict[str, Any] | None = None,
) -> OcrExtraction:
    """OCR 토큰[{text,bbox}] → 템플릿/키워드 추출 → LLM/규칙 정규화 → OcrExtraction."""
    if engine == "upstage_dp":
        raw_parts: list[str] = []
        for t in tokens:
            if t.get("html"):
                raw_parts.append(t["html"])
            elif t.get("text"):
                raw_parts.append(t["text"])
        raw_text = "\n".join(raw_parts)
    else:
        raw_text = " ".join(t["text"] for t in tokens)
    openai_key = _get_openai_key()

    try:
        template = _load_template(doc_type)
        kv_pairs = _apply_template(tokens, template)
    except NotImplementedError:
        kv_pairs = _keyword_extract(tokens, doc_type)

    if openai_key and kv_pairs:
        metrics = _llm_normalize(kv_pairs, doc_type=doc_type, api_key=openai_key)
        _attach_geometry(metrics, kv_pairs)   # LLM이 떨군 bbox/page 재결합
        metrics = _enforce_pinned_rates(metrics, kv_pairs)  # 비율(%) 코드는 템플릿값 고정
    else:
        metrics = _rule_normalize(kv_pairs, doc_type=doc_type)

    # 비율(%) 항목은 표 토큰 인접매칭이 깨지기 쉬워, raw 텍스트 정규식으로 결정적 고정
    metrics = _pin_rates_from_raw(metrics, tokens)
    # 대표 사용량·총량(전력·가스·폐기물)도 본문 명시값으로 결정적 고정
    metrics = _pin_totals_from_raw(metrics, tokens, doc_type)

    return OcrExtraction(
        source_file=Path(file_path).name,
        channel=DocChannel.STRUCTURED,
        doc_type=doc_type,
        metrics=metrics,
        tables=list(tables or []),
        raw_text=raw_text,
        router_meta={"engine": engine, **(engine_meta or {})},
    )


def _attach_geometry(metrics: list["ExtractedMetric"], kv_pairs: dict[str, Any]) -> None:
    """LLM 정규화가 응답에 싣지 않은 bbox/page를 원본 kv_pairs에서 다시 붙인다.

    매칭: ① metric_hint == kv 라벨키 ② 실패 시 value 일치(미사용 항목 중).
    LLM은 위치 정보를 보존하지 못하므로 추출 단계의 좌표를 결정적으로 복원.
    """
    items = list(kv_pairs.items())
    used = [False] * len(items)
    for m in metrics:
        if getattr(m, "bbox", None) is not None:
            continue
        info = kv_pairs.get(m.metric_hint)
        if info is None:
            for idx, (_k, v) in enumerate(items):
                if used[idx]:
                    continue
                try:
                    if abs(float(v.get("value")) - float(m.value)) < 1e-6:
                        info, used[idx] = v, True
                        break
                except (TypeError, ValueError):
                    continue
        if info:
            if m.bbox is None:
                m.bbox = info.get("bbox")
            if getattr(m, "page", None) is None:
                m.page = info.get("page")


def _enforce_pinned_rates(
    metrics: list["ExtractedMetric"], kv_pairs: dict[str, Any]
) -> list["ExtractedMetric"]:
    """템플릿이 단위 '%'로 못박은 비율 코드는 LLM이 톤/kg로 덮어쓰지 못하게 고정한다.

    LLM 정규화가 '재활용 비율(%)'을 '재활용량(톤)'으로 오치환하는 사례를 결정적으로 교정.
    템플릿 KV에 unit=='%' & kesg_code 가 있으면, 해당 코드는 그 비율값으로 확정하고
    같은 코드를 비-% 단위로 단 LLM 산출물은 제거한다. (비율 외 항목은 손대지 않음)
    """
    pinned: dict[str, dict[str, Any]] = {
        info["kesg_code"]: {**info, "label": label}
        for label, info in kv_pairs.items()
        if str(info.get("unit", "")) == "%" and info.get("kesg_code")
    }
    if not pinned:
        return metrics

    out: list[ExtractedMetric] = []
    for m in metrics:
        code = m.kesg_code_guess
        if code in pinned and str(m.unit) != "%":
            continue  # 같은 코드를 비-% 단위로 단 LLM 결과는 폐기
        out.append(m)

    for code, info in pinned.items():
        if any(mm.kesg_code_guess == code and str(mm.unit) == "%" for mm in out):
            continue  # 이미 비율값이 살아있으면 유지
        out.append(ExtractedMetric(
            metric_hint=info.get("label", code),
            value=float(info.get("value", 0)),
            unit="%",
            period="",
            kesg_code_guess=code,
            bbox=info.get("bbox"),
            page=info.get("page"),
            confidence=0.85,
        ))
    return out


# 비율(%) 항목 raw-텍스트 규칙. (키워드 정규식, K-ESG 코드, 라벨, 키워드-값 허용거리)
_RATE_RAW_PATTERNS: list[tuple[str, str, str, int]] = [
    (r"재활용\s*비율|순환\s*이용\s*률|재활용\s*률|순환이용률", "E-6-2", "재활용 비율", 60),
]


def _pin_rates_from_raw(
    metrics: list["ExtractedMetric"], tokens: list[dict[str, Any]]
) -> list["ExtractedMetric"]:
    """OCR raw 텍스트에서 비율(%) 항목을 직접 잡아 해당 코드를 %값으로 결정적 고정.

    표 셀이 여러 토큰으로 쪼개지거나(인접매칭 실패) 키워드와 값 사이에 다른 숫자가
    끼어도, 'NN%' 출현마다 앞쪽 윈도우에 비율 키워드가 있는지 보고 채택한다.
    같은 코드를 비-% 단위로 단 LLM 산출물은 제거.
    """
    import re
    raw = " ".join(str(t.get("text", "")) for t in tokens)
    for kw_pat, code, label, window in _RATE_RAW_PATTERNS:
        kw_re = re.compile(kw_pat)
        val = None
        for nm in re.finditer(r"(\d{1,3}(?:\.\d+)?)\s*%", raw):
            head = raw[max(0, nm.start() - window):nm.start()]
            if kw_re.search(head):
                val = float(nm.group(1))
                numstr = nm.group(1)
                break
        if val is None:
            continue
        # geometry 최선복원: 매칭 숫자(+%)를 품은 토큰의 bbox/page
        bbox = page = None
        for t in tokens:
            txt = str(t.get("text", ""))
            if numstr in txt and "%" in txt:
                bbox, page = t.get("bbox"), t.get("page"); break
        if bbox is None:
            for t in tokens:
                if numstr in str(t.get("text", "")):
                    bbox, page = t.get("bbox"), t.get("page"); break
        # raw 스캔이 비율 코드에 대해 '권위' — 같은 코드 기존 산출물(값/단위 무관)을 전부 폐기하고
        # 텍스트에서 직접 잡은 비율값으로 확정. (템플릿 인접매칭이 엉뚱한 숫자를 박는 사례 차단)
        metrics = [mm for mm in metrics if mm.kesg_code_guess != code]
        metrics.append(ExtractedMetric(
            metric_hint=label, value=val, unit="%", period="",
            kesg_code_guess=code, bbox=bbox, page=page, confidence=0.9,
        ))
    return metrics


# doc_type별 '대표 사용량/총량' raw-텍스트 고정 규칙.
# (정규식, K-ESG코드, 단위, 값배율) — 본문 명시값 × 배율 = 확정값.
# 표 키워드 인접매칭이 옆 칸(전월지침 등)을 잘못 집는 사례를 청구서 본문값으로 결정적 교정.
_TOTAL_RAW_PATTERNS: dict[str, list[tuple[str, str, str, float]]] = {
    # 전력량요금 (142,560kWh) → 실사용량. '사용전력량'이 전월지침(48,210)을 잡던 것 교정.
    "kepco_bill": [(r"\(([\d,]+)\s*kWh\)", "E-4-1", "kWh", 1.0)],
    # 사용요금 (360,772MJ × …) → 가스 사용열량(MJ). 2.0 오추출 교정.
    "gas_bill": [(r"\(([\d,]+)\s*MJ", "E-4-1", "MJ", 1.0)],
    # 올바로 위탁수량은 kg 단위. '총 위탁량 18,400' → kg→ton(÷1000) = 18.4톤.
    "waste_ledger": [(r"총\s*위탁량\s*([\d,]+)", "E-6-1", "ton", 0.001)],
}


def _pin_totals_from_raw(
    metrics: list["ExtractedMetric"], tokens: list[dict[str, Any]], doc_type: str
) -> list["ExtractedMetric"]:
    """청구서/명세서 본문에 명시된 대표 사용량·총량을 raw에서 직접 집어 결정적 고정.

    표 키워드 인접매칭이 옆 칸(전월지침·보조계수 등)을 잘못 집는 사례를 교정한다.
    같은 코드의 기존 산출물은 폐기하고 본문 명시값으로 확정한다(비율 고정과 동일 전략).
    """
    import re
    rules = _TOTAL_RAW_PATTERNS.get(doc_type)
    if not rules:
        return metrics
    raw = " ".join(str(t.get("text", "")) for t in tokens)
    for pat, code, unit, factor in rules:
        mt = re.search(pat, raw)
        if not mt:
            continue
        try:
            val = float(mt.group(1).replace(",", "")) * factor
        except ValueError:
            continue
        numstr = mt.group(1)
        bbox = page = None
        for t in tokens:
            if numstr in str(t.get("text", "")):
                bbox, page = t.get("bbox"), t.get("page"); break
        metrics = [mm for mm in metrics if mm.kesg_code_guess != code]
        metrics.append(ExtractedMetric(
            metric_hint=f"{code} 본문확정", value=round(val, 3), unit=unit,
            period="", kesg_code_guess=code, bbox=bbox, page=page, confidence=0.92,
        ))
    return metrics


# ---- 정형 채널 내부 헬퍼 ------------------------------------------------------

def _get_upstage_key() -> str | None:
    """Upstage API 키 조회 (UPSTAGE_API_KEY)."""
    import os
    return os.getenv("UPSTAGE_API_KEY") or None


# Upstage Document Parse 엔드포인트 (환경변수로 오버라이드 가능)
_UPSTAGE_DP_DEFAULT_URL = "https://api.upstage.ai/v1/document-digitization"


def _upstage_dp_url() -> str:
    import os
    return os.getenv("UPSTAGE_DP_URL", _UPSTAGE_DP_DEFAULT_URL)


def _norm_bbox_from_points(points: list[dict[str, Any]] | None) -> list[float] | None:
    """Upstage coordinates(정규화 0~1 꼭짓점 리스트) → [x0,y0,x1,y1] bbox.

    points = [{"x":0.07,"y":0.15}, {"x":..}, {"x":..}, {"x":..}] (네 꼭짓점).
    Upstage는 이미 페이지 기준 0~1로 정규화된 좌표를 준다 → 외접 사각형만 취한다.
    """
    pts = points or []
    xs = [float(pt["x"]) for pt in pts if isinstance(pt, dict) and pt.get("x") is not None]
    ys = [float(pt["y"]) for pt in pts if isinstance(pt, dict) and pt.get("y") is not None]
    if not xs or not ys:
        return None
    clamp = lambda v: max(0.0, min(1.0, v))
    return [clamp(min(xs)), clamp(min(ys)), clamp(max(xs)), clamp(max(ys))]


def _slice_first_page_pdf(file_path: str) -> bytes | None:
    """PDF 1페이지만 떼어 bytes 반환 (라우팅 프리뷰 과금 최소화).

    Upstage DP는 Azure 같은 `pages` 파라미터가 없어, 1페이지만 보내려면 문서를 직접
    잘라야 한다. fitz(pymupdf)로 첫 장만 새 PDF로 만든다.
    비PDF·단일페이지·fitz 미설치·실패 시 None → 호출부가 전체 파일을 전송.
    """
    p = Path(file_path)
    if p.suffix.lower() != ".pdf":
        return None
    try:
        import fitz  # pymupdf
        src = fitz.open(str(p))
        if src.page_count <= 1:
            return None
        out = fitz.open()
        out.insert_pdf(src, from_page=0, to_page=0)
        return out.tobytes()
    except Exception:
        return None


def _call_upstage_dp(
    file_path: str, *, ocr_mode: str = "force", pages: str | None = None
) -> list[dict[str, Any]]:
    """Upstage Document Parse 호출 → 요소 단위 토큰 [{text, bbox, page}]."""
    return _call_upstage_dp_payload(file_path, ocr_mode=ocr_mode, pages=pages)["tokens"]


def _call_upstage_dp_payload(
    file_path: str,
    *,
    ocr_mode: str = "force",
    pages: str | None = None,
) -> dict[str, Any]:
    """Upstage Document Parse REST 호출 → 토큰 + 표(HTML 복원) 메타데이터.

    POST multipart/form-data:
      files: document=<파일 bytes>
      data : model=document-parse, ocr=force|auto, output_formats=['html','text'],
             coordinates=true, base64_encoding=[]
    응답 JSON: {content, elements:[{id,category,content:{html,text},page,coordinates}], usage}
      · 텍스트 토큰: 모든 요소의 content.text + coordinates(외접 bbox) + page(0-기준 변환)
      · 표: category=='table' 요소의 content.html을 셀 그리드로 파싱 → ExtractedTable
    ocr_mode 'force'는 텍스트 레이어 유무와 무관하게 항상 OCR(정확도 우선, 스캔본 안전).
    pages="1"이면 PDF 첫 장만 잘라 전송(라우팅 프리뷰 비용 최소화).
    """
    import requests
    key = _get_upstage_key()
    if not key:
        raise RuntimeError("UPSTAGE_API_KEY 미설정")

    doc_bytes: bytes | None = None
    if pages == "1":
        doc_bytes = _slice_first_page_pdf(file_path)
    if doc_bytes is None:
        doc_bytes = Path(file_path).read_bytes()

    headers = {"Authorization": f"Bearer {key}"}
    data = {
        "model": "document-parse",
        "ocr": ocr_mode,
        "output_formats": "['html', 'text']",
        "coordinates": "true",
        "base64_encoding": "[]",
    }
    files = {"document": (Path(file_path).name, doc_bytes)}

    resp = requests.post(_upstage_dp_url(), headers=headers, data=data, files=files, timeout=120)
    resp.raise_for_status()
    body = resp.json()
    elements = body.get("elements", []) or []

    tokens: list[dict[str, Any]] = []
    tables: list[ExtractedTable] = []
    for el in elements:
        content = el.get("content") or {}
        text = str(content.get("text") or "").strip()
        page0 = int(el.get("page", 1) or 1) - 1   # 1-기준 → 0-기준
        bbox = _norm_bbox_from_points(el.get("coordinates"))
        if text:
            tokens.append({"text": text, "bbox": bbox, "page": page0})
        if el.get("category") == "table":
            html = str(content.get("html") or "")
            table = _parse_html_table(
                html,
                table_id=f"upstage_table_{len(tables)}",
                page=page0,
                bbox=bbox,
            )
            if table is not None:
                tables.append(table)

    return {"tokens": tokens, "tables": tables}


class _HTMLTableParser(HTMLParser):
    """Upstage 표 요소의 content.html(<table>)을 행×셀 구조로 파싱."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[dict[str, Any]]] = []
        self._row: list[dict[str, Any]] | None = None
        self._cell: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v for k, v in attrs}
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            def _span(name: str) -> int:
                try:
                    return max(1, int(a.get(name) or 1))
                except (TypeError, ValueError):
                    return 1
            self._cell = {
                "text": "",
                "rowspan": _span("rowspan"),
                "colspan": _span("colspan"),
                "is_header": tag == "th",
            }

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell["text"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None:
            if self._row is None:
                self._row = []
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def _parse_html_table(
    html: str,
    *,
    table_id: str,
    page: int | None,
    bbox: list[float] | None,
) -> ExtractedTable | None:
    """<table> HTML → ExtractedTable. rowspan/colspan을 점유 격자로 풀어 셀 좌표를 부여.

    Upstage는 셀별 좌표/confidence를 주지 않으므로 bbox는 표 전체 외접 사각형(전 셀 공유),
    confidence는 None(게이트 C1/C2 신호는 confidence 부재 시 자동 스킵).
    """
    if not html or "<" not in html:
        return None
    parser = _HTMLTableParser()
    try:
        parser.feed(html)
    except Exception:
        return None
    if not parser.rows:
        return None

    occupied: dict[tuple[int, int], bool] = {}
    cells: list[TableCell] = []
    max_row = 0
    max_col = 0
    for r, row in enumerate(parser.rows):
        c = 0
        for raw in row:
            while occupied.get((r, c)):
                c += 1
            rs = int(raw["rowspan"])
            cs = int(raw["colspan"])
            cells.append(TableCell(
                row_index=r,
                column_index=c,
                content=raw["text"].strip(),
                row_span=rs,
                column_span=cs,
                kind="columnHeader" if raw["is_header"] and r == 0 else None,
                bbox=bbox,
                page=page,
                confidence=None,
            ))
            for dr in range(rs):
                for dc in range(cs):
                    occupied[(r + dr, c + dc)] = True
            max_row = max(max_row, r + rs)
            max_col = max(max_col, c + cs)
            c += cs

    return ExtractedTable(
        table_id=table_id,
        row_count=max_row,
        column_count=max_col,
        cells=cells,
        source="upstage_dp",
        page=page,
    )


_DIGIT_SEP_RE = __import__("re").compile(r"(?<=\d)[,\s]+(?=\d)")
_NUMBER_TOKEN_RE = __import__("re").compile(r"\d+(?:[,\s]+\d{3})*(?:\.\d+)?")
_NUMBER_RE = __import__("re").compile(r"\d+\.?\d*")


def _find_number_tokens(text: str) -> list[str]:
    """텍스트 안 숫자 토큰들을 개별적으로 정규화해 반환한다."""
    return [_DIGIT_SEP_RE.sub("", m.group(0)) for m in _NUMBER_TOKEN_RE.finditer(text)]


def _find_single_number(text: str) -> str | None:
    """텍스트에 숫자 토큰이 정확히 하나일 때만 그 값을 반환한다."""
    nums = _find_number_tokens(text)
    if len(nums) != 1:
        return None
    return nums[0]


def _find_number(text: str):
    """텍스트에서 첫 숫자를 추출. 천 단위 콤마·공백 구분자 정규화.

    OCR이 '128, 400'처럼 콤마 뒤 공백을 넣어도 128400으로 합친다.
    """
    nums = _find_number_tokens(text)
    if not nums:
        return None
    return _NUMBER_RE.search(nums[0])


_HEADER_UNIT_RE = __import__("re").compile(r"\(\s*(kWh|MWh|MJ|GJ|TJ|kW|ton|t|m3|㎥|L|원|%)\s*\)", __import__("re").IGNORECASE)


def _x_center(bbox: list[float] | None) -> float | None:
    """bbox 가로 중심(0~1). 컬럼 정렬 판정용."""
    if not bbox or len(bbox) < 4:
        return None
    return (float(bbox[0]) + float(bbox[2])) / 2.0


def _y_top(bbox: list[float] | None) -> float | None:
    """bbox 상단 y(0~1). 행 순서 판정용."""
    if not bbox or len(bbox) < 4:
        return None
    return float(bbox[1])


def _match_column_value(
    header_tok: dict[str, Any], tokens: list[dict[str, Any]], *, x_tol: float = 0.06
) -> dict[str, Any] | None:
    """헤더 토큰과 같은 컬럼(x중심 근접)·아래 행의 숫자 토큰을 값으로 채택.

    표에서 단위가 헤더 셀('사용량(kWh)')에, 값이 데이터 행에 분리돼 있고 데이터 행
    첫 컬럼(전월지침)을 인접매칭이 잘못 집던 사례를 컬럼 정렬로 교정한다.
    헤더 bbox가 없으면(좌표 미상) None → 호출부가 기존 인접매칭으로 폴백.
    """
    hx = _x_center(header_tok.get("bbox"))
    hy = _y_top(header_tok.get("bbox"))
    header_page = header_tok.get("page")
    if hx is None or hy is None:
        return None
    best: dict[str, Any] | None = None
    best_dy: float | None = None
    for t in tokens:
        ty = _y_top(t.get("bbox"))
        tx = _x_center(t.get("bbox"))
        if t.get("page") != header_page:
            continue  # 페이지 경계 넘김 금지
        if ty is None or tx is None or ty <= hy:
            continue  # 헤더보다 위/같은 행 제외(데이터 행만)
        if abs(tx - hx) > x_tol:
            continue  # 다른 컬럼
        num = _find_single_number(t.get("text", ""))
        if num is None:
            continue
        dy = ty - hy
        if best_dy is None or dy < best_dy:   # 헤더 바로 아래(첫 데이터 행) 우선
            best, best_dy = t, dy
    if best is None:
        return None
    return {
        "value": float(_find_single_number(best["text"])),
        "bbox": best.get("bbox"),
        "page": best.get("page"),
    }


def _apply_template(tokens: list[dict[str, Any]], template: dict[str, Any]) -> dict[str, Any]:
    """템플릿의 라벨 키워드와 토큰 텍스트를 매칭해 {라벨: {value, unit, bbox}} 추출.

    전략(우선순위):
      1) 헤더 토큰과 같은 컬럼(bbox x중심)·아래 행의 값 — 표에서 단위가 헤더 셀에,
         값이 데이터 행에 분리된 경우 첫 숫자 컬럼(전월지침 등) 오집을 방지.
      2) bbox가 없거나 컬럼 매칭 실패 시 — 기존 인접(오른쪽/아래) 숫자 토큰 폴백.
    단위는 헤더 텍스트의 괄호 단위('사용량(kWh)'→kWh)를 우선, 없으면 템플릿 unit.
    """
    number_re = None  # _find_number 사용
    result: dict[str, Any] = {}

    for label_key, label_info in template.items():
        keywords: list[str] = label_info.get("keywords", [])
        unit: str = label_info.get("unit", "")
        kesg: str | None = label_info.get("kesg_code")

        for i, tok in enumerate(tokens):
            if any(kw in tok["text"] for kw in keywords):
                # 헤더 셀 괄호 단위가 있으면 그것을 우선(템플릿 기본단위·K-ESG 라벨 덮어쓰기 방지)
                hu = _HEADER_UNIT_RE.search(tok["text"])
                eff_unit = hu.group(1) if hu else unit
                # 현재 토큰에 숫자가 정확히 하나면 우선 사용 (예: "사용전력량(kWh): 128,400")
                num = _find_single_number(tok["text"])
                if num is not None:
                    result[label_key] = {
                        "value": float(num),
                        "unit": eff_unit,
                        "kesg_code": kesg,
                        "bbox": tok.get("bbox"),
                        "page": tok.get("page"),
                        "raw_label": tok["text"],
                    }
                    break
                # ① 컬럼 정렬 매칭(헤더와 같은 x, 아래 행) — 첫 숫자 컬럼 오집 방지
                col = _match_column_value(tok, tokens)
                if col is not None:
                    result[label_key] = {
                        "value": col["value"],
                        "unit": eff_unit,
                        "kesg_code": kesg,
                        "bbox": col["bbox"],
                        "page": col["page"],
                        "raw_label": tok["text"],
                    }
                    break
                # ② 폴백: 현재 토큰에 숫자 없으면 인접 토큰(최대 5개) 탐색
                for j in range(i + 1, min(i + 6, len(tokens))):
                    num = _find_single_number(tokens[j]["text"])
                    if num is not None:
                        result[label_key] = {
                            "value": float(num),
                            "unit": eff_unit,
                            "kesg_code": kesg,
                            "bbox": tokens[j].get("bbox"),
                            "page": tokens[j].get("page"),
                            "raw_label": tok["text"],
                        }
                        break
                if label_key in result:
                    break
    return result


def _keyword_extract(tokens: list[dict[str, Any]], doc_type: str) -> dict[str, Any]:
    """템플릿 미정의 시 — 알려진 ESG 키워드 근방 숫자 추출 폴백."""
    import re
    _KW_MAP = {
        "사용전력량": {"unit": "kWh", "kesg_code": "E-4-1"},
        "전력사용량": {"unit": "kWh", "kesg_code": "E-4-1"},
        "가스사용량": {"unit": "MJ",  "kesg_code": "E-4-1"},
        "폐기물":    {"unit": "ton", "kesg_code": "E-6-1"},
        "용수":      {"unit": "ton", "kesg_code": "E-5-1"},
        "배출량":    {"unit": "tCO2eq", "kesg_code": "E-3-1"},
    }
    number_re = re.compile(r"[\d,]+\.?\d*")
    result: dict[str, Any] = {}

    for i, tok in enumerate(tokens):
        for kw, info in _KW_MAP.items():
            if kw in tok["text"] and kw not in result:
                for j in range(i + 1, min(i + 6, len(tokens))):
                    m = _find_number(tokens[j]["text"])
                    if m:
                        result[kw] = {
                            "value": float(m.group()),
                            **info,
                            "bbox": tokens[j].get("bbox"),
                            "page": tokens[j].get("page"),
                            "raw_label": tok["text"],
                        }
                        break
    return result


def _candidate_codes_block() -> str:
    """LLM 정규화 프롬프트용 후보 K-ESG 코드 목록(정량 항목 위주, 'code — name (unit)')."""
    from ..knowledge.kesg_items import ALL_ITEMS
    lines = [
        f"- {it.code} — {it.name}" + (f" ({it.unit})" if it.unit else "")
        for it in ALL_ITEMS
        if it.area == "E" or it.data_type in ("정량", "혼합")
    ]
    return "\n".join(lines)


def _llm_normalize(
    kv_pairs: dict[str, Any],
    *,
    doc_type: str,
    api_key: str,
) -> list[ExtractedMetric]:
    """LLM(gpt-4.1-mini via Azure)으로 추출 KV 쌍을 ExtractedMetric[]으로 정규화."""
    import json as _json, re
    from ..llm import LLMClient
    from .prompts import STRUCTURED_NORMALIZE_SYSTEM, STRUCTURED_NORMALIZE_PROMPT

    tokens_str = _json.dumps(kv_pairs, ensure_ascii=False, indent=2)
    prompt = STRUCTURED_NORMALIZE_PROMPT.format(
        doc_type=doc_type, ocr_tokens=tokens_str, candidate_codes=_candidate_codes_block(),
    )

    resp = LLMClient().complete(
        system=STRUCTURED_NORMALIZE_SYSTEM,
        user=prompt,
        json_mode=True,
        temperature=0.0,
        mock_hint="ocr_normalize",
    )
    m = re.search(r'\{.*\}', resp.content, re.DOTALL)
    data = _json.loads(m.group() if m else "{}")
    return _parse_normalize_response(data)


def _rule_normalize(kv_pairs: dict[str, Any], *, doc_type: str) -> list[ExtractedMetric]:
    """LLM 없이 규칙 기반으로 KV → ExtractedMetric[] 변환."""
    metrics = []
    for label, info in kv_pairs.items():
        metrics.append(ExtractedMetric(
            metric_hint=label,
            value=float(info.get("value", 0)),
            unit=str(info.get("unit", "")),
            period="",
            kesg_code_guess=info.get("kesg_code"),
            bbox=info.get("bbox"),
            page=info.get("page"),
            confidence=0.80,
        ))
    return metrics


def _parse_normalize_response(data: dict[str, Any]) -> list[ExtractedMetric]:
    """LLM normalize 응답 JSON → ExtractedMetric[]."""
    metrics = []
    for m in data.get("metrics", []):
        try:
            metrics.append(ExtractedMetric(
                metric_hint=str(m.get("metric_hint", "")),
                value=float(m.get("value", 0)),
                unit=str(m.get("unit", "")),
                period=str(m.get("period", "")),
                kesg_code_guess=m.get("kesg_code") or None,
                bbox=m.get("bbox"),
                page=m.get("page"),
                confidence=float(m.get("confidence", 0.85)),
            ))
        except (TypeError, ValueError):
            continue
    return metrics


def _extract_structured_gpt_fallback(file_path: str, *, doc_type: str, api_key: str) -> OcrExtraction:
    """OCR 키 없을 때 pymupdf 텍스트 추출 + 규칙 정규화 폴백."""
    return _extract_structured_no_llm(file_path, doc_type=doc_type)


def _extract_structured_no_llm(file_path: str, *, doc_type: str) -> OcrExtraction:
    """Upstage/LLM 없이 pymupdf + 정규식으로 디지털 PDF 처리.

    한전 전기요금·올바로 폐기물 대장 같은 텍스트 임베딩 PDF는 이 경로로 충분.
    스캔 이미지 PDF는 텍스트가 비어 → VLM 채널로 에스컬레이션.
    """
    raw_text = _extract_text_pymupdf(file_path, max_pages=5)
    if not raw_text.strip():
        # 스캔본(임베딩 텍스트 없음) → VLM 키가 있으면 비정형 채널로 에스컬레이션.
        # VLM 키도 없으면 정형 mock 반환 (doc_type별 샘플 수치 — 데모 보장).
        if _get_openai_key() or _get_anthropic_key():
            return extract_unstructured(file_path, doc_type=doc_type)
        return _mock_structured(file_path, doc_type)

    # 줄 단위 토큰(+좌표) — 디지털 PDF면 pymupdf가 줄 bbox를 제공(정규화).
    tokens = _pymupdf_line_tokens(file_path, max_pages=5) or [
        {"text": line.strip(), "bbox": None, "page": None}
        for line in raw_text.splitlines() if line.strip()
    ]

    try:
        template = _load_template(doc_type)
        kv_pairs = _apply_template(tokens, template)
    except NotImplementedError:
        kv_pairs = _keyword_extract(tokens, doc_type)

    metrics = _rule_normalize(kv_pairs, doc_type=doc_type)
    metrics = _pin_rates_from_raw(metrics, tokens)  # 비율(%) 결정적 고정 (Upstage 경로와 동일)
    metrics = _pin_totals_from_raw(metrics, tokens, doc_type)  # 대표 사용량·총량 고정

    return OcrExtraction(
        source_file=Path(file_path).name,
        channel=DocChannel.STRUCTURED,
        doc_type=doc_type,
        metrics=metrics,
        raw_text=raw_text,
        router_meta={"fallback": "pymupdf+regex", "upstage": False},
    )


def _pymupdf_line_tokens(file_path: str, max_pages: int = 5) -> list[dict[str, Any]]:
    """pymupdf로 span 우선 토큰 추출 [{text, bbox(0~1 정규화), page}].

    표/명세서의 숫자 셀은 span 단위가 line 단위보다 안정적이다. 한 줄 전체를 토큰화하면
    '48,210 50,586 60 142,560' 같은 다중 수치 행에서 첫 값만 집는 오집이 생길 수 있어,
    span이 있으면 그것을 우선 사용하고 span이 없을 때만 line 폴백한다.
    """
    out: list[dict[str, Any]] = []
    try:
        import fitz
        with fitz.open(file_path) as doc:
            for i, page in enumerate(doc):
                if i >= max_pages:
                    break
                pw, ph = page.rect.width, page.rect.height
                if not pw or not ph:
                    continue
                data = page.get_text("dict")
                for blk in data.get("blocks", []):
                    for line in blk.get("lines", []):
                        emitted = False
                        for span in line.get("spans", []):
                            text = str(span.get("text", "")).strip()
                            if not text:
                                continue
                            x0, y0, x1, y1 = span.get("bbox", line.get("bbox", (0, 0, 0, 0)))
                            bbox = [x0 / pw, y0 / ph, x1 / pw, y1 / ph]
                            out.append({"text": text, "bbox": bbox, "page": i})
                            emitted = True
                        if emitted:
                            continue
                        text = "".join(s.get("text", "") for s in line.get("spans", []))
                        if not text.strip():
                            continue
                        x0, y0, x1, y1 = line.get("bbox", (0, 0, 0, 0))
                        bbox = [x0 / pw, y0 / ph, x1 / pw, y1 / ph]
                        out.append({"text": text.strip(), "bbox": bbox, "page": i})
    except Exception:
        return []
    return out


def _extract_text_pymupdf(file_path: str, max_pages: int = 5) -> str:
    """pymupdf로 PDF 임베딩 텍스트 추출. 없으면 빈 문자열."""
    try:
        import fitz
        doc = fitz.open(file_path)
        pages_text = []
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            pages_text.append(page.get_text())
        return "\n".join(pages_text)
    except Exception:
        return ""


def _mock_structured(file_path: str, doc_type: str) -> OcrExtraction:
    """OCR 키 없을 때 데모용 Mock 반환."""
    source_file = Path(file_path).name

    _MOCK_METRICS: dict[str, list[ExtractedMetric]] = {
        "kepco_bill": [
            ExtractedMetric(
                metric_hint="사용전력량", value=128400.0, unit="kWh",
                period="2025-12", kesg_code_guess="E-4-1",
                bbox=[120, 340, 280, 360], confidence=0.97,
            ),
            ExtractedMetric(
                metric_hint="청구금액", value=18540000.0, unit="원",
                period="2025-12", kesg_code_guess=None,
                confidence=0.95,
            ),
        ],
        "gas_bill": [
            ExtractedMetric(
                metric_hint="가스사용량", value=4820.0, unit="MJ",
                period="2025-12", kesg_code_guess="E-4-1",
                confidence=0.93,
            ),
        ],
        "waste_ledger": [
            ExtractedMetric(
                metric_hint="폐기물처리량", value=12.5, unit="ton",
                period="2025-12", kesg_code_guess="E-6-1",
                confidence=0.90,
            ),
        ],
    }

    metrics = _MOCK_METRICS.get(doc_type, [
        ExtractedMetric(
            metric_hint=f"[MOCK] {doc_type} 수치", value=0.0, unit="",
            period="", confidence=0.5,
        )
    ])
    return OcrExtraction(
        source_file=source_file,
        channel=DocChannel.STRUCTURED,
        doc_type=doc_type,
        metrics=metrics,
        raw_text=f"[MOCK] {doc_type} 데모 데이터",
        router_meta={"mock": True},
    )


# ====================================================================
# 채널 B — 비정형: VLM 우선   (STUB)
# ====================================================================

def extract_unstructured(file_path: str, *, doc_type: str) -> OcrExtraction:
    """비정형 문서 채널 — 텍스트 추출 후 LLM(gpt-4.1-mini)로 정량·정성 동시 추출.

    파이프라인:
      1) 디지털 PDF → pymupdf 텍스트 추출 (정확·저렴)
      2) 스캔본(임베딩 텍스트 없음) → Upstage Document Parse로 OCR 텍스트화
      3) 텍스트 → LLM(VLM_EXTRACT_PROMPT)로 metrics + clauses JSON 추출
      텍스트도 키도 없으면 Mock 폴백.
    """
    openai_key = _get_openai_key()
    if not openai_key:
        return _mock_unstructured(file_path, doc_type)

    # 1) 디지털 PDF 텍스트
    raw_text = _extract_text_pymupdf(file_path, max_pages=10)

    # 2) 스캔본 → Upstage Document Parse OCR로 텍스트화
    if not raw_text.strip():
        if _get_upstage_key():
            try:
                tokens = _call_upstage_dp(file_path, ocr_mode="force")
                raw_text = "\n".join(t["text"] for t in tokens)
            except Exception:
                raw_text = ""

    if not raw_text.strip():
        return _mock_unstructured(file_path, doc_type)

    return _extract_unstructured_text(file_path, doc_type=doc_type, raw_text=raw_text)


def _extract_unstructured_text(
    file_path: str, *, doc_type: str, raw_text: str
) -> OcrExtraction:
    """텍스트 비정형 문서 → LLM(gpt-4.1-mini via Azure)으로 정량·정성 추출."""
    import json as _json, re
    from ..llm import LLMClient
    from .prompts import VLM_EXTRACT_SYSTEM, VLM_EXTRACT_PROMPT

    prompt = VLM_EXTRACT_PROMPT.format(doc_type=doc_type) + f"\n\n문서 텍스트:\n{raw_text[:4000]}"
    resp = LLMClient().complete(
        system=VLM_EXTRACT_SYSTEM,
        user=prompt,
        json_mode=True,
        temperature=0.0,
        mock_hint="ocr_unstructured",
    )
    m = re.search(r'\{.*\}', resp.content, re.DOTALL)
    data = _json.loads(m.group() if m else "{}")
    metrics, clauses = _map_vlm_json(data)
    clauses = _augment_unstructured_clauses(
        clauses,
        raw_text=raw_text,
        doc_type=doc_type,
    )

    return OcrExtraction(
        source_file=Path(file_path).name,
        channel=DocChannel.UNSTRUCTURED,
        doc_type=doc_type,
        metrics=metrics,
        clauses=clauses,
        raw_text=raw_text,
        router_meta={"engine": "gpt-4.1-mini-text", "vision": False},
    )


# ---- VLM 내부 헬퍼 -----------------------------------------------------------

def _render_pages_b64(file_path: str, max_pages: int = 10) -> list[str]:
    """PDF/이미지 파일 → base64 인코딩 PNG 리스트.

    pymupdf(fitz) 우선, 없으면 pdf2image 폴백, 둘 다 없으면 빈 리스트.
    """
    import base64, io
    p = Path(file_path)
    if not p.exists():
        return []

    # 이미지 파일 직접 처리
    if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
        raw = p.read_bytes()
        return [base64.b64encode(raw).decode()]

    # PDF → 페이지 이미지
    try:
        import fitz  # pymupdf
        doc = fitz.open(str(p))
        results = []
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            mat = fitz.Matrix(1.5, 1.5)   # 1.5× 해상도 (VLM 인식 품질 ↑)
            pix = page.get_pixmap(matrix=mat)
            img_bytes = pix.tobytes("png")
            results.append(base64.b64encode(img_bytes).decode())
        return results
    except ImportError:
        pass

    try:
        from pdf2image import convert_from_path
        import io
        pages = convert_from_path(str(p), dpi=150, first_page=1, last_page=max_pages)
        results = []
        for img in pages:
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            results.append(base64.b64encode(buf.getvalue()).decode())
        return results
    except ImportError:
        return []


def _map_vlm_json(data: dict[str, Any], *, page_no: int = 1) -> tuple[list[ExtractedMetric], list[ExtractedClause]]:
    """VLM 응답 JSON → ExtractedMetric[] + ExtractedClause[]."""
    metrics: list[ExtractedMetric] = []
    clauses: list[ExtractedClause] = []

    for m in data.get("metrics", []):
        try:
            metrics.append(ExtractedMetric(
                metric_hint=str(m.get("metric_hint", "")),
                value=float(m.get("value", 0)),
                unit=str(m.get("unit", "")),
                period=str(m.get("period", "")),
                kesg_code_guess=m.get("kesg_code") or None,
                confidence=0.75,   # VLM 추출 기본 신뢰도
            ))
        except (TypeError, ValueError):
            continue

    for c in data.get("clauses", []):
        try:
            clauses.append(ExtractedClause(
                section=str(c.get("section", "")),
                text=str(c.get("text", "")),
                kesg_code_guess=c.get("kesg_code") or None,
                page=int(c.get("page", page_no)),
            ))
        except (TypeError, ValueError):
            continue

    return metrics, clauses


def _augment_unstructured_clauses(
    clauses: list[ExtractedClause],
    *,
    raw_text: str,
    doc_type: str,
) -> list[ExtractedClause]:
    """LLM이 놓친 존재형 조항을 원문 키워드로 보강한다."""
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if not lines:
        return clauses

    existing_codes = {clause.kesg_code_guess for clause in clauses if clause.kesg_code_guess}
    heuristics: dict[str, tuple[tuple[str, ...], str]] = {}
    if doc_type == "policy_manual":
        heuristics = {
            "E-1-1": (("환경경영", "환경법규 준수", "환경영향", "기본방침", "목표"), "환경경영 방침"),
            "E-1-2": (("ESG경영팀", "환경안전팀", "주관 부서", "추진체계", "전담"), "환경경영 추진체계"),
            "S-4-1": (("안전보건", "산업안전보건", "위험성평가", "중대재해", "안전교육"), "안전보건 체계"),
            "S-5-1": (("인권", "아동노동", "강제노동"), "인권 정책"),
            "S-6-1": (("협력업체", "협력사", "공급망", "ESG 기준"), "협력사 ESG 관리"),
            "G-4-1": (("윤리", "행동강령", "공정·윤리"), "윤리경영"),
        }
    elif doc_type == "safety_minutes":
        heuristics = {
            "S-4-1": (("산업안전보건위원회", "안전보건", "위험성평가", "근로자 대표"), "안전보건 운영"),
        }

    augmented = list(clauses)
    for code, (keywords, section) in heuristics.items():
        if code in existing_codes:
            continue
        matched = [line for line in lines if any(keyword in line for keyword in keywords)]
        if not matched:
            continue
        augmented.append(ExtractedClause(
            section=section,
            text=" ".join(matched[:2]),
            kesg_code_guess=code,
            page=1,
        ))
    return augmented


def _mock_unstructured(file_path: str, doc_type: str) -> OcrExtraction:
    """API 키 없을 때 데모용 Mock 반환."""
    source_file = Path(file_path).name

    _MOCK_BY_TYPE: dict[str, OcrExtraction] = {
        "safety_minutes": OcrExtraction(
            source_file=source_file,
            channel=DocChannel.UNSTRUCTURED,
            doc_type=doc_type,
            metrics=[],
            clauses=[
                ExtractedClause(
                    section="산업안전보건위원회 운영",
                    text="제1조 본 위원회는 분기 1회 정기 개최한다. "
                         "단, 중대 재해 발생 시 즉시 소집한다.",
                    kesg_code_guess="S-3-1",
                    page=1,
                ),
                ExtractedClause(
                    section="위험성 평가",
                    text="제2조 연 1회 이상 전 공정 위험성 평가를 실시한다.",
                    kesg_code_guess="S-3-1",
                    page=2,
                ),
            ],
            raw_text="[MOCK] 안전보건위원회 회의록 데모 데이터",
            router_meta={"mock": True},
        ),
        "policy_manual": OcrExtraction(
            source_file=source_file,
            channel=DocChannel.UNSTRUCTURED,
            doc_type=doc_type,
            metrics=[],
            clauses=[
                ExtractedClause(
                    section="환경경영 방침",
                    text="당사는 온실가스 배출 감축을 위해 [○○]% 절감 목표를 설정하고 "
                         "매년 달성 현황을 공개한다.",
                    kesg_code_guess="E-1-1",
                    page=1,
                ),
                ExtractedClause(
                    section="환경경영 추진체계",
                    text="주관 부서 ESG경영팀 / 환경안전팀",
                    kesg_code_guess="E-1-2",
                    page=1,
                ),
                ExtractedClause(
                    section="윤리경영",
                    text="회사는 공정·윤리 원칙을 준수하고 관련 기준을 전사에 배포한다.",
                    kesg_code_guess="G-4-1",
                    page=2,
                ),
            ],
            raw_text="[MOCK] 사내 규정집 데모 데이터",
            router_meta={"mock": True},
        ),
    }

    return _MOCK_BY_TYPE.get(
        doc_type,
        OcrExtraction(
            source_file=source_file,
            channel=DocChannel.UNSTRUCTURED,
            doc_type=doc_type,
            clauses=[
                ExtractedClause(
                    section="[MOCK] 일반 조항",
                    text=f"{doc_type} 문서의 데모 조항입니다.",
                    kesg_code_guess=None,
                    page=1,
                )
            ],
            raw_text=f"[MOCK] {doc_type} 데모",
            router_meta={"mock": True},
        ),
    )


# ====================================================================
# 내부 헬퍼 (STUB)
# ====================================================================

def _quick_preview(file_path: str, max_chars: int = 1500) -> str:
    """1페이지만 싸게 텍스트화 (라우팅 판단용).

    PDF: pymupdf page[0].get_text() — 임베디드 텍스트 우선(스캔본은 빈 문자열).
    이미지: 파일명 힌트만 사용(OCR 비용 절약).
    둘 다 실패 시 파일명 stem으로 폴백.
    """
    p = Path(file_path)
    if not p.exists():
        return ""

    if p.suffix.lower() == ".pdf":
        try:
            import fitz  # pymupdf
            doc = fitz.open(str(p))
            text = doc[0].get_text() if len(doc) > 0 else ""
            if text.strip():
                return text[:max_chars]
            # 임베디드 텍스트 없음 = 스캔본 → Upstage DP 1p OCR 에스컬레이션(정확 라우팅).
            ocr_text = _ocr_preview_first_page(str(p), max_chars=max_chars)
            if ocr_text.strip():
                return ocr_text
        except Exception:
            # pymupdf 미설치/파일 손상 → Upstage DP 1p로라도 본문 신호 확보 시도.
            ocr_text = _ocr_preview_first_page(str(p), max_chars=max_chars)
            if ocr_text.strip():
                return ocr_text

    # 이미지(스캔 jpg/png) → Upstage DP 1p 시도 후, 실패 시 파일명만 신호로 사용
    if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".tif", ".tiff"):
        ocr_text = _ocr_preview_first_page(str(p), max_chars=max_chars)
        if ocr_text.strip():
            return ocr_text
    return p.stem


def _ocr_preview_first_page(file_path: str, *, max_chars: int = 1500) -> str:
    """스캔본 라우팅용 — Upstage DP로 1페이지만 OCR해 본문 텍스트 확보.

    디지털 텍스트가 없는 스캔본은 라우팅이 파일명에만 의존하게 돼 오분류 위험이 크다
    (정형 고지서가 비정형 VLM으로 새는 등). PDF 첫 장만 잘라(pages="1") 보내 과금을
    최소화하면서 키워드 신호를 살린다. 정확도 우선 정책.
    Upstage 키 미설정·망 차단·실패 시 빈 문자열 → 호출부가 파일명으로 안전 폴백.
    """
    if not _get_upstage_key():
        return ""
    try:
        tokens = _call_upstage_dp(file_path, ocr_mode="force", pages="1")
        return " ".join(t.get("text", "") for t in tokens)[:max_chars]
    except Exception:
        return ""


def estimate_layout_features(file_path: str) -> dict[str, float]:
    """1페이지 표 면적 비율 추정 — 정형 판별의 보조 신호(table_area_ratio).

    pymupdf find_tables()로 감지된 표 bbox 합면적 / 페이지 면적(0~1).
    고지서·명세서처럼 표 격자가 촘촘한 정형 문서일수록 값이 높다.
    pymupdf 미설치·스캔본(표 미검출)·PDF 외·실패 시 빈 dict(=신호 없음, 안전 폴백).
    """
    p = Path(file_path)
    if not p.exists() or p.suffix.lower() != ".pdf":
        return {}
    try:
        import fitz  # pymupdf
    except ImportError:
        return {}
    try:
        doc = fitz.open(str(p))
        if len(doc) == 0:
            return {}
        page = doc[0]
        page_area = abs(page.rect.width * page.rect.height)
        if page_area <= 0:
            return {}
        finder = page.find_tables()
        table_area = 0.0
        for t in getattr(finder, "tables", []):
            x0, y0, x1, y1 = t.bbox
            table_area += abs((x1 - x0) * (y1 - y0))
        return {"table_area_ratio": round(min(table_area / page_area, 1.0), 4)}
    except Exception:
        return {}


def _score_signatures(text: str, fname: str, table: dict[str, list[str]]) -> dict[str, dict[str, Any]]:
    """시그니처 사전 대비 키워드 매칭 점수(0~1 근사) 계산."""
    out: dict[str, dict[str, Any]] = {}
    haystack = f"{text} {fname}"
    for doc_type, kws in table.items():
        matched = [kw for kw in kws if kw.lower() in haystack]
        score = len(matched) / max(len(kws), 1)
        out[doc_type] = {"score": score, "kw": matched}
    return out


def _get_openai_key() -> str | None:
    """공유 설정(SETTINGS)에서 OpenAI API 키 조회 (force_mock 시 None)."""
    from ..config import SETTINGS
    if SETTINGS.force_mock:
        return None
    return SETTINGS.openai_api_key


def _get_anthropic_key() -> str | None:
    """공유 설정(SETTINGS)에서 Anthropic API 키 조회 (force_mock 시 None)."""
    from ..config import SETTINGS
    if SETTINGS.force_mock:
        return None
    return SETTINGS.anthropic_api_key


def _load_template(doc_type: str) -> dict[str, Any]:
    """doc_type별 키-값 추출 템플릿 반환.

    각 항목: {label_key: {keywords, unit, kesg_code}}
    keywords — OCR 토큰에서 이 키워드가 발견되면 인접 숫자를 값으로 채택.
    """
    _TEMPLATES: dict[str, dict[str, Any]] = {
        "kepco_bill": {
            "사용전력량": {
                "keywords": ["사용전력량", "사용량(kWh)", "당월사용량"],
                "unit": "kWh",
                "kesg_code": "E-4-1",
            },
            "최대수요전력": {
                "keywords": ["최대수요전력", "최대전력"],
                "unit": "kW",
                "kesg_code": None,
            },
            "청구금액": {
                "keywords": ["청구금액", "납부금액", "요금합계"],
                "unit": "원",
                "kesg_code": None,
            },
        },
        "gas_bill": {
            "가스사용량": {
                "keywords": ["사용량", "가스사용량", "당월사용"],
                "unit": "MJ",
                "kesg_code": "E-4-1",
            },
            "열량": {
                "keywords": ["열량", "발열량"],
                "unit": "MJ",
                "kesg_code": "E-4-1",
            },
        },
        "water_bill": {
            "사용량": {
                "keywords": ["사용량", "급수량", "당월사용"],
                "unit": "ton",
                "kesg_code": "E-5-1",
            },
        },
        "waste_ledger": {
            # 재활용 '비율(%)' = E-6-2 (K-ESG 정의). 배출량(톤)과 구분, 비율 라벨을 먼저 둔다.
            "재활용비율": {
                "keywords": ["재활용 비율", "순환이용률", "재활용률"],
                "unit": "%",
                "kesg_code": "E-6-2",
            },
            "폐기물처리량": {
                "keywords": ["총배출량", "처리량", "배출량", "폐기물량", "인계량"],
                "unit": "ton",
                "kesg_code": "E-6-1",
            },
            # 지정폐기물은 총 배출량(E-6-1)의 하위 분류일 뿐 총량이 아니다.
            # E-6-1로 잡으면 '폐기물 처리량'과 노드가 중복되므로 보조수치(코드 None)로 둔다.
            "지정폐기물": {
                "keywords": ["지정폐기물"],
                "unit": "ton",
                "kesg_code": None,
            },
            # 재활용 '량(톤)'은 비율과 별개 보조수치 — 표의 'R-1' 등에 오매칭되지 않게 키워드 한정
            "재활용량": {
                "keywords": ["재활용량", "재생이용량"],
                "unit": "ton",
                "kesg_code": None,
            },
        },
        "fuel_receipt": {
            "주유량": {
                "keywords": ["주유량", "급유량", "리터", "충전량"],
                "unit": "L",
                "kesg_code": "E-4-1",
            },
        },
    }

    if doc_type not in _TEMPLATES:
        raise NotImplementedError(f"템플릿 미정의 doc_type: {doc_type}")
    return _TEMPLATES[doc_type]
