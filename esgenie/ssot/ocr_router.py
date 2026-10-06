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

import os
import re
import logging
import math
from dataclasses import dataclass, field, fields, asdict
from enum import Enum
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

# Upstage Document Parse 모델.
# document-parse-260630 명시 pin은 2026-11-02(KST) 지원 종료 — Upstage 권고에 따라 alias로 전환.
# 2026-10-02 확인: alias "document-parse" → document-parse-260930(샘플 01~03 추출 결과 260630과 동일).
# alias는 신버전을 자동 추종하므로 실제 버전은 response_meta.returned_model로 기록하고,
# 롤백·비교 실험은 UPSTAGE_DP_MODEL 환경변수로 오버라이드한다.
UPSTAGE_DP_MODEL: str = os.getenv("UPSTAGE_DP_MODEL", "document-parse")

# 모듈 로거 — 청크 JSON 파싱 실패 경고가 이미 참조하고 있었으나 정의가 없었다(NameError).
logger = logging.getLogger(__name__)


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
    quote: str = ""                     # 추출 입력에서 확인한 원문 인용
    page_source: str = ""               # chunk(실제 페이지 입력) | quote(원본 대조)
    # 측정 경계(ssot.boundary.Boundary의 dict 표현) — 정형 파서가 표 제목·행/열에서
    # 직접 읽은 축만 담는다. 비어 있으면 merge_ocr_extraction이 hint·기간 원문에서
    # 규칙으로 도출한다. dict로 두는 이유는 캐시 JSON 왕복 호환이다(from_dict는
    # 모르는 키를 무시하므로 구버전 캐시는 빈 dict로 읽힌다).
    boundary: dict[str, Any] = field(default_factory=dict)
    # 표 추출기(ocr_table_metrics)가 남기는 원문 근거 — 셀 원문·머리글·원 단위·단위 출처·
    # 지침 검산·계산식·입력 칸·위치 정밀도("cell"|"table"|"pdf_text"|"text_block"). 비어 있으면
    # 템플릿/LLM 산출. 본문 비율 고정(_pin_rates_from_raw)도 위치 정밀도를 남긴다.
    source_detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractedClause:
    """비정형 문서에서 추출한 정성 텍스트 단위 (→ 텍스트 노드 후보)."""
    section: str           # "비상대응 절차", "근로자 대표 참여" …
    text: str
    kesg_code_guess: str | None = None
    page: int | None = None
    rba_code_guess: str | None = None    # RBA 자가진단 substrate 매칭(고유 조항용)
    quote: str = ""
    page_source: str = ""


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

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "OcrExtraction":
        """to_dict()의 역변환 — 중첩 dataclass(metric/clause/table/cell)까지 복원한다.

        캐시(ocr_cache) 복원용. **모르는 키는 무시한다** — 스키마가 앞으로 늘어나도
        구버전 필드만 채우고 조용히 지나가게(캐시가 예외를 던지면 안 된다).
        """
        def _pick(dc: type, raw: Any) -> dict[str, Any]:
            names = {f.name for f in fields(dc)}
            return {k: v for k, v in (raw or {}).items() if k in names}

        tables: list[ExtractedTable] = []
        for t in d.get("tables") or []:
            kw = _pick(ExtractedTable, t)
            kw["cells"] = [TableCell(**_pick(TableCell, c)) for c in (t.get("cells") or [])]
            tables.append(ExtractedTable(**kw))

        return cls(
            source_file=str(d.get("source_file", "")),
            channel=DocChannel(d.get("channel") or DocChannel.UNSTRUCTURED.value),
            doc_type=str(d.get("doc_type", "")),
            metrics=[ExtractedMetric(**_pick(ExtractedMetric, m)) for m in (d.get("metrics") or [])],
            clauses=[ExtractedClause(**_pick(ExtractedClause, c)) for c in (d.get("clauses") or [])],
            tables=tables,
            raw_text=str(d.get("raw_text", "") or ""),
            router_meta=dict(d.get("router_meta") or {}),
        )


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
    unit_guard = _apply_unit_evidence_guard(structured_hits, text)

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
            rationale=f"정형 시그니처 우세(table_ratio={table_ratio:.2f})" + unit_guard,
        )
    if u_score >= _ROUTE_FALLBACK_THRESHOLD:
        return RouteDecision(
            channel=DocChannel.UNSTRUCTURED,
            doc_type=u_best[0] or "unstructured_unknown",
            confidence=round(min(u_score, 1.0), 3),
            matched_keywords=u_best[1]["kw"],
            rationale="비정형 시그니처 우세" + unit_guard,
        )
    # 애매 → 안전 폴백(VLM)
    return RouteDecision(
        channel=DocChannel.UNSTRUCTURED,
        doc_type="ambiguous_fallback_vlm",
        confidence=round(max(s_score, u_score), 3),
        matched_keywords=(s_best[1]["kw"] + u_best[1]["kw"]),
        rationale="신뢰도 미달 → VLM 폴백" + unit_guard,
    )


# 정형 실적 문서는 본문에 해당 수량 단위가 있다. 본문이 충분히 긴데 단위가 한 번도 없으면
# 키워드('폐기물'·'인계' 등)만 겹친 규정·회사 소개로 보고 그 정형 유형 점수를 0으로 둔다.
_ROUTE_UNIT_EVIDENCE: dict[str, str] = {
    "kepco_bill": r"kwh|mwh",
    "gas_bill": r"m3|m³|㎥|mj",
    "water_bill": r"m3|m³|㎥|톤|ton",
    # '18,400 kg'·'(kg)'·'(단위: ton)'처럼 단위어가 독립해 있으면 된다(영단어 안의 ton 제외).
    "waste_ledger": r"(?<![a-z])(?:kg|㎏|tons?|톤)(?![a-z])|\d\s*t\b|\(t\)",
    "fuel_receipt": r"\d\s*(?:l\b|ℓ|리터)",
}
_ROUTE_UNIT_MIN_CHARS = 120   # 짧은 프리뷰(파일명·스캔 1p 일부)는 단위 부재를 판단하지 않는다


def _apply_unit_evidence_guard(hits: dict[str, dict[str, Any]], text: str) -> str:
    import re
    if len(re.sub(r"\s+", "", text)) < _ROUTE_UNIT_MIN_CHARS:
        return ""
    dropped = []
    for doc_type, info in hits.items():
        pat = _ROUTE_UNIT_EVIDENCE.get(doc_type)
        if info["score"] > 0 and pat and not re.search(pat, text, re.IGNORECASE):
            if info["score"] >= _ROUTE_FALLBACK_THRESHOLD:
                dropped.append(doc_type)   # 근거에는 실제로 순위를 잃은 유형만 남긴다
            info["score"] = 0.0
    return f" · 수량 단위 없음으로 제외: {', '.join(dropped)}" if dropped else ""


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
    _resolve_clause_pages(ext, file_path)
    if Path(file_path).is_file():
        import hashlib
        ext.router_meta["source_sha256"] = hashlib.sha256(Path(file_path).read_bytes()).hexdigest()
    return ext


def _resolve_clause_pages(ext: OcrExtraction, file_path: str) -> None:
    """Use actual PDF pages, not the LLM's ambiguous 1-based page numbers."""
    if not ext.clauses or Path(file_path).suffix.lower() != ".pdf":
        return
    try:
        import fitz
        import re
        with fitz.open(file_path) as doc:
            pages = [re.sub(r"\s+", "", p.get_text()) for p in doc]
        if not pages:
            return
        unresolved = 0
        for clause in ext.clauses:
            # 요약문은 PDF 전문의 부분 문자열이 아닐 수 있다. 실제 단일 페이지에서
            # 만든 호출의 출처를 모델이 적은 페이지 번호와 구분해 보존한다.
            if clause.page_source == "chunk" and clause.page is not None and 0 <= clause.page < len(pages):
                continue
            quote = re.sub(r"\s+", "", clause.quote or clause.text)
            matches = [i for i, text in enumerate(pages) if quote and quote in text]
            # 2026-09-29 머지: 이 브랜치에는 "한 쪽 문서면 무조건 page=0" 분기가 있었는데
            # main이 그것을 결함으로 판정해 고쳤다(`page is None`). 쪽이 하나뿐이어도 그
            # 인용이 원문에 없으면 위치를 만들어 주는 것이므로 지어낸 근거에 페이지가 붙는다.
            # main의 계약(`test_R27_single_page_still_requires_located_quote`)을 따른다.
            if len(matches) == 1:
                clause.page = matches[0]
                clause.page_source = "quote"
            else:
                # Unknown/ambiguous location must not become a fabricated page link.
                clause.page = None
                clause.page_source = ""
                unresolved += 1
        ext.router_meta["clause_page_resolution"] = {
            "source": "actual_pdf", "page_count": len(pages), "unresolved": unresolved}
        ext.router_meta["unresolved_clause_pages"] = unresolved
    except (ImportError, OSError, RuntimeError, ValueError):
        return


def _backfill_kesg_codes(ext: OcrExtraction) -> None:
    """kesg_code_guess가 비어 있는 metric을 라벨 동의어 사전으로 결정적 보강한다.

    하이브리드 1단계(결정적 사전)다. 사전이 못 잡으면 코드를 비워 두어 상위 LLM
    폴백/HITL이 처리하게 한다. fuzzy 후보는 코드 배정 단계에서 거부한다.

    중복 가드: 이미 **다른 지표 본체의** metric이 점유한 코드(템플릿/본문확정 등 권위 있는
    산출물)는 backfill이 다시 붙이지 않는다. 예) 보조수치 '지정폐기물'(template code=None)이
    E-6-1로 해소돼 본문확정 18.4t와 1000× 어긋난 유령 중복노드를 만드는 사례 차단.

    단, **같은 지표 본체**는 예외다(2026-08-02). 표의 다연도 열·다중 행은 집계수준과
    연도가 달라도 정상적인 별도 노드다. '대기오염물질 배출량 국내(별도) 2022'가 먼저
    E-7-1을 받아도 '해외 자회사 2022'와 '합계 2024'를 막으면 안 된다. 집계·연도
    수식어만 제거한 본체가 같을 때 코드 재사용을 허용한다.

    같은 라벨 예외(2026-07-26)도 포함된다. 표의 다연도 열·다중 행은 같은 hint로 여러
    metric이 되는데, 선착순 점유가 두 번째부터 코드를 못 받게 만들어 동일 hint가 연도마다
    다른 코드로 흘렀다(현대모비스 'Scope 3 온실가스 배출량 연결(일부)' → 2022는 E-3-2,
    2023·2024는 코드 미부여 후 일반 온실가스 별칭에 걸려 E-3-1로 흘렀다. Scope3 값이
    Scope1+2 후보 풀을 오염시키는 직접 원인이라, 라벨이 같으면
    점유 여부와 무관하게 같은 코드를 준다 — 동일 hint → 동일 코드(결정적).
    """
    import re

    from ..knowledge.kesg_items import _normalize_label, by_code, resolve_kesg_code
    from ..layer1_extract import _unit_suspect
    from ..rag_gates.units import normalize_unit

    def _metric_body(label: str) -> str:
        """집계수준·연도만 떼어 코드 점유를 비교할 지표 본체를 만든다.

        '총탄화수소' 같은 실제 지표명을 훼손하지 않도록 단독 '총'은 제거하지 않는다.
        조직 고유명사도 지우지 않아 서로 다른 하위 지표가 같은 코드로 합쳐지는 것을 막는다.
        """
        body = (label or "").lower()
        body = re.sub(r"20\d{2}(?:\s*년)?", " ", body)
        body = re.sub(r"국내\s*\(\s*별도\s*\)", " ", body)
        body = re.sub(
            r"국내\s*자회사|해외\s*자회사|국내\s*사업장|해외\s*사업장",
            " ", body,
        )
        body = re.sub(r"연결\s*\(\s*일부\s*\)", " ", body)
        body = re.sub(r"(?<![0-9a-z가-힣])(합계|총계|전사|total)(?![0-9a-z가-힣])", " ", body)
        return _normalize_label(body)

    # 코드 → 그 코드를 점유한 라벨/지표 본체. 같은 본체의 재사용은 중복이 아니다.
    taken_by_label: dict[str, set[str]] = {}
    taken_by_body: dict[str, set[str]] = {}
    for m in ext.metrics:
        if m.kesg_code_guess:
            taken_by_label.setdefault(m.kesg_code_guess, set()).add(
                _normalize_label(m.metric_hint))
            body = _metric_body(m.metric_hint)
            if body:
                taken_by_body.setdefault(m.kesg_code_guess, set()).add(body)
    from .evidence_graph import _ASSIGNMENT_NEGATIVE_KEYWORDS

    resolved: list[dict[str, Any]] = []
    for m in ext.metrics:
        if m.kesg_code_guess:
            continue
        code, score, method = resolve_kesg_code(m.metric_hint)
        if not code or method != "exact":
            continue
        # 병합 단계(`evidence_graph._resolve_kesg_code`)가 거부할 충돌 어휘면 여기서도 붙이지 않는다 —
        # 추출 기록(`alias_backfill`)과 그래프의 코드가 갈리지 않게 한다.
        compact = m.metric_hint.lower().replace(" ", "")
        if any(term in compact for term in _ASSIGNMENT_NEGATIVE_KEYWORDS.get(code, ())):
            continue
        # 단위가 항목 정의와 다르면(도시가스 m³ → E-4-1 TJ) 라벨만으로 코드를 붙이지 않는다.
        # 병합 단계(_resolve_kesg_code G3)와 같은 판정이며 용수 m³↔ton 동치만 허용한다.
        item = by_code(code)
        water_volume = code in {"E-5-1", "E-5-2"} and normalize_unit(str(m.unit or "")) == "m³"
        if item and item.unit and not water_volume and _unit_suspect(m.unit, item.unit):
            continue
        label = _normalize_label(m.metric_hint)
        holders = taken_by_label.get(code)
        body = _metric_body(m.metric_hint)
        same_body = bool(body and body in taken_by_body.get(code, set()))
        if holders and label not in holders and not same_body:
            # Scope 3는 evidence_graph 사전에도 키가 있어, 여기서 막은 카테고리별
            # 보조수치가 merge 때 다시 E-3-2로 살아날 수 있다. 해당 코드만 차단 결정을
            # 내부 표식으로 전달한다(외부 dataclass 스키마는 바꾸지 않음).
            if code == "E-3-2":
                m._kesg_backfill_blocked = True
            continue  # 다른 지표 본체가 점유한 코드 → 권위 산출물 우선
        m.kesg_code_guess = code
        taken_by_label.setdefault(code, set()).add(label)
        if body:
            taken_by_body.setdefault(code, set()).add(body)
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
                engine_meta={"upstage_model": UPSTAGE_DP_MODEL,
                             "dp_response": payload.get("response_meta", {})},
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
    # 표 셀(행·열·머리글·단위)로 먼저 읽고, 표가 차지한 역할의 템플릿 라벨은 버린다.
    table_result = _table_metric_pass(tokens, tables, doc_type, kv_pairs)

    if openai_key and kv_pairs:
        metrics = _llm_normalize(kv_pairs, doc_type=doc_type, api_key=openai_key)
        _attach_geometry(metrics, kv_pairs)   # LLM이 떨군 bbox/page 재결합
        metrics = _enforce_pinned_rates(metrics, kv_pairs)  # 비율(%) 코드는 템플릿값 고정
    else:
        metrics = _rule_normalize(kv_pairs, doc_type=doc_type)
    metrics = _merge_table_metrics(metrics, table_result)

    # 비율(%) 항목은 표 토큰 인접매칭이 깨지기 쉬워, raw 텍스트 정규식으로 결정적 고정
    metrics = _pin_rates_from_raw(metrics, tokens)
    # 대표 사용량·총량(전력·가스·폐기물)도 본문 명시값으로 결정적 고정
    metrics = _pin_totals_from_raw(metrics, tokens, doc_type)
    if engine == "upstage_dp":
        # Upstage 셀은 표 외접 bbox만 공유한다 — PDF 문자 좌표로 좁힐 수 있을 때만 좁힌다.
        from .ocr_table_metrics import refine_bboxes_with_pdf
        refine_bboxes_with_pdf(metrics, file_path)

    router_meta = {"engine": engine, **(engine_meta or {})}
    _finish_table_meta(router_meta, table_result, metrics)
    return OcrExtraction(
        source_file=Path(file_path).name,
        channel=DocChannel.STRUCTURED,
        doc_type=doc_type,
        metrics=metrics,
        tables=list(tables or []),
        raw_text=raw_text,
        router_meta=router_meta,
    )


def _table_metric_pass(
    tokens: list[dict[str, Any]], tables: list[ExtractedTable] | None, doc_type: str,
    kv_pairs: dict[str, Any],
):
    """표 격자 추출 — 역할(사용량·총량 등)을 차지하면 같은 역할의 템플릿 KV를 제거한다."""
    from .ocr_table_metrics import (TEMPLATE_LABELS_BY_ROLE, drop_template_candidates_from_money_cells,
                                    drop_template_candidates_in_absent_tables, extract_table_metrics)
    result = extract_table_metrics(tokens, tables, doc_type=doc_type)
    for role in result.claimed_roles:
        for label in TEMPLATE_LABELS_BY_ROLE.get(doc_type, {}).get(role, ()):
            kv_pairs.pop(label, None)
    # 사용량 칸이 빈 표에서 인접 숫자(지침 등)를 사용량으로 되살리지 않는다.
    drop_template_candidates_in_absent_tables(result, kv_pairs, doc_type)
    # 금액 행(기본요금 등)에서 뺀 숫자도 템플릿·LLM 후보로 되살리지 않는다.
    drop_template_candidates_from_money_cells(result, kv_pairs, doc_type)
    return result


def _merge_table_metrics(metrics: list["ExtractedMetric"], result) -> list["ExtractedMetric"]:
    """표 산출물을 우선한다 — 같은 코드 또는 같은 값·단위의 템플릿/LLM 산출물은 버린다."""
    if not result.metrics:
        return metrics
    codes = {m.kesg_code_guess for m in result.metrics if m.kesg_code_guess}
    keys = {(float(m.value), m.unit) for m in result.metrics}
    kept = [m for m in metrics
            if m.kesg_code_guess not in codes and (float(m.value), m.unit) not in keys]
    return kept + list(result.metrics)


def _finish_table_meta(router_meta: dict[str, Any], result, metrics: list["ExtractedMetric"]) -> None:
    """폐기물 재활용률 검산 + 표 검산 기록. 불일치·상충은 HITL 검토로 올린다."""
    from .ocr_table_metrics import check_recycling_rate
    check_recycling_rate(result, metrics)
    if not (result.metrics or result.review or result.checks):
        return
    router_meta["table_metrics"] = result.meta()
    review_reasons = {"explicit_vs_index_mismatch", "conflicting_values"}
    if any(c["status"] == "mismatch" for c in result.checks) or any(
            r["reason"] in review_reasons for r in result.review):
        router_meta["hitl_required"] = True


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
                rawstr = nm.group(0)
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
        pinned = ExtractedMetric(
            metric_hint=label, value=val, unit="%", period="",
            kesg_code_guess=code, bbox=bbox, page=page, confidence=0.9,
        )
        # 원문 비율 문자열('29%'·'29.30%')은 표시 자릿수 검산 근거라 위치와 무관하게 보존한다.
        pinned.source_detail = {"extractor": "raw_rate_pin", "raw_text": rawstr,
                                "display_decimals": len(numstr.split(".")[1]) if "." in numstr else 0}
        if bbox is not None:
            # 위치는 값을 품은 텍스트 요소(줄·문단)의 외접 사각형이다 — 셀 위치로 표시하지 않는다.
            pinned.source_detail.update({
                "precision": "text_block",
                "cells": [{"text": rawstr, "bbox": bbox, "page": page, "precision": "text_block"}],
                "precision_note": "값을 품은 OCR·PDF 텍스트 요소의 외접 위치(셀 단위 아님)"})
        metrics.append(pinned)
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
        # 표 추출기가 같은 코드로 같은 양을 이미 읽었다면 그 셀 근거를 유지한다.
        # 단위만 다르면(kg ↔ ton) 고정 단위로 값을 두되 셀 위치·원문 근거를 옮긴다.
        same = [mm for mm in metrics if mm.kesg_code_guess == code and mm.source_detail
                and _same_quantity(mm.value, mm.unit, round(val, 3), unit)]
        if same and same[0].unit == unit:
            continue
        bbox = page = None
        for t in tokens:
            if numstr in str(t.get("text", "")):
                bbox, page = t.get("bbox"), t.get("page"); break
        metrics = [mm for mm in metrics if mm.kesg_code_guess != code]
        label = {"kepco_bill": "사용전력량", "gas_bill": "도시가스 사용열량", "waste_ledger": "총 위탁량"}.get(doc_type, code)
        pinned = ExtractedMetric(
            metric_hint=label, value=round(val, 3), unit=unit,
            period="", kesg_code_guess=code, bbox=bbox, page=page, confidence=0.92,
        )
        if same:
            pinned.bbox, pinned.page = same[0].bbox, same[0].page
            pinned.source_detail = dict(same[0].source_detail, pinned_unit=unit,
                                        pinned_by="본문 명시값 고정(_TOTAL_RAW_PATTERNS)")
        metrics.append(pinned)
    return metrics


def _same_quantity(v1: float, u1: str, v2: float, u2: str) -> bool:
    from ..rag_gates.units import convert_to_common, normalize_unit
    a, b = normalize_unit(u1 or ""), normalize_unit(u2 or "")
    if not a or not b:
        return False
    conv = convert_to_common(float(v1), a, b)
    return conv is not None and abs(conv - float(v2)) <= 1e-6 * max(1.0, abs(float(v2)))


# ---- 정형 채널 내부 헬퍼 ------------------------------------------------------

def _get_upstage_key() -> str | None:
    """Upstage API 키 조회 (UPSTAGE_API_KEY, force_mock 시 None)."""
    import os
    from ..config import SETTINGS
    if SETTINGS.force_mock:
        return None
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
    file_path: str, *, ocr_mode: str = "force", pages: str | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """Upstage Document Parse 호출 → 요소 단위 토큰 [{text, bbox, page}]."""
    return _call_upstage_dp_payload(
        file_path, ocr_mode=ocr_mode, pages=pages, model=model)["tokens"]


def _call_upstage_dp_payload(
    file_path: str,
    *,
    ocr_mode: str = "force",
    pages: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Upstage Document Parse REST 호출 → 토큰 + 표(HTML 복원) 메타데이터.

    POST multipart/form-data:
      files: document=<파일 bytes>
      data : model=UPSTAGE_DP_MODEL(기본 document-parse alias), ocr=force|auto, output_formats=['html','text'],
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
        "model": model or UPSTAGE_DP_MODEL,
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

    response_meta = {
        "status_code": getattr(resp, "status_code", None), "requested_model": model or UPSTAGE_DP_MODEL,
        "returned_model": body.get("model"), "usage": body.get("usage"),
        "request_id": getattr(resp, "headers", {}).get("x-request-id") or getattr(resp, "headers", {}).get("request-id"),
        "token_count": len(tokens), "table_count": len(tables),
        "coordinate_count": sum(t.get("bbox") is not None for t in tokens),
    }
    return {"tokens": tokens, "tables": tables, "response_meta": response_meta}


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


# 템플릿 인접·컬럼 매칭이 값으로 받아들일 토큰: '숫자 [단위]' 한 덩어리(공백 없는 단위어).
_VALUE_LIKE_RE = __import__("re").compile(
    r"^\s*[+-]?(?:\d{1,3}(?:,\s?\d{3})+|\d+)(?:\.\d+)?\s*[^\d\s.,:~\-/][^\d\s]{0,7}\s*$"
    r"|^\s*[+-]?(?:\d{1,3}(?:,\s?\d{3})+|\d+)(?:\.\d+)?\s*$")
_HEADING_ORDINAL_RE = __import__("re").compile(r"^\s*\d{1,2}[.)]\s+")
_OWN_UNIT_RE = __import__("re").compile(
    r"\d\s*(kWh|MWh|MJ|GJ|TJ|m3|m³|㎥|kg|㎏|톤|ton|%)(?![A-Za-z가-힣])", __import__("re").IGNORECASE)


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
        if not _VALUE_LIKE_RE.match(t.get("text", "")):
            continue  # 값 셀이 아닌 문장·제목
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
                # 제목 번호('3. 현장 안전과 교육')는 수치가 아니다 — 번호를 떼고 숫자를 찾는다.
                # 머리글 단위 괄호('사용량(m3)'의 3)도 수치가 아니다.
                own_text = _HEADER_UNIT_RE.sub("", _HEADING_ORDINAL_RE.sub("", tok["text"]))
                # 현재 토큰에 숫자가 정확히 하나면 우선 사용 (예: "사용전력량(kWh): 128,400")
                num = _find_single_number(own_text)
                if num is not None:
                    # 토큰 안에 단위가 적혀 있으면 템플릿 기본 단위보다 우선한다('재활용량 5,400 kg').
                    ou = None if hu else _OWN_UNIT_RE.search(own_text)
                    result[label_key] = {
                        "value": float(num),
                        "unit": ou.group(1) if ou else eff_unit,
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
                # ② 폴백: 현재 토큰에 숫자 없으면 인접 토큰(최대 5개) 탐색.
                #    이웃은 값 셀처럼 생긴 토큰('18,400 kg')만 — 문장·제목 번호·날짜 제외.
                for j in range(i + 1, min(i + 6, len(tokens))):
                    if not _VALUE_LIKE_RE.match(tokens[j]["text"]):
                        continue
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
    table_result = _table_metric_pass(tokens, None, doc_type, kv_pairs)

    metrics = _rule_normalize(kv_pairs, doc_type=doc_type)
    metrics = _merge_table_metrics(metrics, table_result)
    metrics = _pin_rates_from_raw(metrics, tokens)  # 비율(%) 결정적 고정 (Upstage 경로와 동일)
    metrics = _pin_totals_from_raw(metrics, tokens, doc_type)  # 대표 사용량·총량 고정

    router_meta = {"fallback": "pymupdf+regex", "upstage": False}
    _finish_table_meta(router_meta, table_result, metrics)
    return OcrExtraction(
        source_file=Path(file_path).name,
        channel=DocChannel.STRUCTURED,
        doc_type=doc_type,
        metrics=metrics,
        raw_text=raw_text,
        router_meta=router_meta,
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


# 같은 행(row)으로 묶을 span y좌표 근접 임계값(pt).
# 실측(모비스 p.52·53 표): 표 데이터 행 높이 8.4~9.0pt, 행 간격 13~15pt.
# 3pt면 같은 행 span(오차 <1pt)은 묶고 다음 행(간격 13pt+)과는 분리된다.
# bake-off(scripts/table_extract_bakeoff.py)에서 이 값으로 8/9 검증됨.
_ROW_Y_TOLERANCE_PT = 3.0


def _extract_text_pymupdf(file_path: str, max_pages: int = 5) -> str:
    """pymupdf로 PDF 임베딩 텍스트 추출 — 좌표 기반 행 복원(표 구조 보존).

    현행 page.get_text()는 표 셀을 각각 독립 line으로 취급해 세로로 평탄화한다
    ("용수 재이용률 / % / 2.72 / 3.48 / 7.90"). 레이블-값 연결이 소실돼 하위
    gpt-4.1-mini가 어느 숫자가 어느 지표인지 알 수 없다(L0 오염의 근본 원인).

    대신 span(+bbox)을 모아 y좌표 근접 span을 같은 행으로 묶고 x순 정렬 후 ' | '로
    조인해 표의 행 구조를 복원한다. 산문은 원래 한 line이 한 span으로 나오므로
    영향이 거의 없다(실측 라인내 최대 gap <2pt).

    예외 시 기존 page.get_text()로 폴백 — 회귀 안전장치.
    """
    return "\n".join(_extract_pages_pymupdf(file_path, max_pages=max_pages))


def _extract_pages_pymupdf(file_path: str, max_pages: int) -> list[str]:
    """원본의 빈 페이지도 유지해 실제 0-기준 페이지와 텍스트를 연결한다."""
    try:
        import fitz
        with fitz.open(file_path) as doc:
            pages_text = []
            for i, page in enumerate(doc):
                if i >= max_pages:
                    break
                try:
                    pages_text.append(_reconstruct_rows_from_dict(page))
                except Exception:
                    # 페이지 단위 폴백 — 한 페이지 파싱 실패가 전체를 버리지 않게.
                    pages_text.append(page.get_text())
            return pages_text
    except (ImportError, OSError, RuntimeError, ValueError):
        return []


def _reconstruct_rows_from_dict(page: Any) -> str:
    """page.get_text("dict")의 span을 y좌표로 행 재구성해 ' | ' 조인 텍스트로 반환.

    구현:
      1) 블록(block)별로 span을 모아 y좌표 근접(_ROW_Y_TOLERANCE_PT) span을 같은 행으로
         묶는다. **블록 단위로 묶는 이유**: 2단 편집 레이아웃(회사 소개 등)에서 좌/우 단이
         같은 y에 있어 전역으로 묶으면 좌우 문장이 ' | '로 뒤섞인다(실측 p.6). 블록은
         보통 단을 분리하므로 블록 내부에서만 행을 재구성하면 산문 읽기 순서가 보존된다.
         표는 대개 한 블록이라 표 행 복원 효과는 그대로다(bake-off 동일 점수 확인).
      2) 폰트가 큰 문서에서 행이 뭉치지 않도록, 행 대표 높이가 임계값보다 작으면 동적으로
         좁힌다(min(고정, 높이*0.5)) — 큰 제목 span이 아래 본문을 흡수하는 것 방지.
      3) 행 내부 x중심 순 정렬 후 ' | '로 조인. 블록 순서대로 이어 붙인다(문서 순서 보존).
    작업2(헤더 행 상속)·작업3(컬럼 헤더 매핑) 후처리를 전체 행 목록에 순서대로 적용한다.
    """
    d = page.get_text("dict")
    rows: list[list[tuple[float, str]]] = []   # 행별 [(x중심, text), ...]  블록·y 순
    for block in d.get("blocks", []):
        spans: list[tuple[float, float, float, str]] = []  # (y0, x중심, height, text)
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                txt = str(span.get("text", "")).strip()
                if not txt:
                    continue
                x0, y0, x1, y1 = span["bbox"]
                # x중심 사용: 값이 우측정렬이라 x0(좌변)는 컬럼 헤더 매칭이 한 칸씩
                # 밀린다(실측). 중심끼리 최근접이면 12/12 정확(작업3).
                spans.append((round(y0, 1), round((x0 + x1) / 2, 1), round(y1 - y0, 1), txt))
        if not spans:
            continue
        spans.sort()
        cur_y: float | None = None
        cur_tol: float = _ROW_Y_TOLERANCE_PT
        for y, x, height, txt in spans:
            tol = min(_ROW_Y_TOLERANCE_PT, height * 0.5) if height else _ROW_Y_TOLERANCE_PT
            if cur_y is None or abs(y - cur_y) > cur_tol:
                rows.append([])
                cur_y = y
                cur_tol = tol
            rows[-1].append((x, txt))

    if not rows:
        return page.get_text()

    for r in rows:
        r.sort()

    rows = _inherit_label_rows(rows)       # 작업2: 별도 레이블 행을 인접 값 행에 상속
    rows = _attach_column_headers(rows)    # 작업3: 다중 컬럼 표에 헤더 라벨 부착
    return "\n".join(" | ".join(t for _x, t in r) for r in rows)


# 표 셀에서 숫자 값을 식별하는 정규식(천단위 콤마·소수·음수 허용). '~'(빈칸 표기)는 값 아님.
_TABLE_NUM_RE = __import__("re").compile(r"^-?[\d,]+(?:\.\d+)?$")
# 행 선두가 단위 셀인지 판정(레이블이 별도 행에 있는 표 감지용). 예: 'TJ | 1,918 | ...'
_UNIT_LEAD_RE = __import__("re").compile(
    r"^(TJ|GJ|MJ|MWh|kWh|GWh|kW|MW|t?CO2eq?|ton|t|kg|m3|㎥|ML|L|원|%|명|건|억\s*원|백만\s*원)$",
    __import__("re").IGNORECASE,
)
# 각주 마커 셀('4)', '1)' 등) — 컬럼 헤더/값에서 제외.
_FOOTNOTE_MARK_RE = __import__("re").compile(r"^\d+\)$")
# 컬럼 헤더 행으로 인식할 라벨 키워드(연도는 4자리 숫자로 별도 판정).
_COL_HEADER_KEYWORDS = ("합계", "전사", "국내", "해외", "자회사", "별도", "본사", "연결")
# 2단 헤더의 연도 셀. 목표/실적 혼합 표도 매핑하되 성격을 버리지 않는다.
# 예: '2030 목표'를 단순 '2030'으로 바꾸지 않고 '연결|2030 목표'로 보존한다.
_YEAR_HEADER_RE = re.compile(r"20\d{2}(?:년)?(?:\s*(?:목표|실적|계획|전망))?")
# 레이블 상속이 건너뛸 수 있는 최대 행 수(과잉 상속 방지).
_LABEL_INHERIT_MAX_SPAN = 2


def _is_number_cell(text: str) -> bool:
    return bool(_TABLE_NUM_RE.match(text.strip()))


def _inherit_label_rows(rows: list[list[tuple[float, str]]]) -> list[list[tuple[float, str]]]:
    """지표명이 별도 행에 있는 표에서, 레이블 행을 인접 값 행에 접두로 상속한다.

    실측(모비스 p.52): 지표명 '에너지 사용량'이 값 행들(TJ|…|9,075 / MWh|…) *사이*에
    단독 셀로 놓인다(rowspan 중앙배치). 값 행은 단위(TJ·MWh 등)로 시작하고 지표명이
    없어, 행 복원만으로는 어느 지표의 값인지 알 수 없다(PRESENT).

    규칙:
      · '레이블 행' = 각주 마커를 제외한 셀 1개, 숫자 없음, 단위도 아님.
      · '단위 선두 값 행' = 첫 셀이 단위이고 숫자 셀을 포함(예: 'TJ | 1,918 | …').
      · 레이블 행을 기준으로 위·아래 _LABEL_INHERIT_MAX_SPAN 행 내의 '단위 선두 값 행'에
        `[레이블]`을 접두 상속. (rowspan 레이블이 값 행들 사이에 오는 구조 대응)
    과잉 상속 방지:
      · 상속 대상은 '단위 선두 값 행'으로 한정(일반 텍스트·다른 레이블 행 제외).
      · 가까운 레이블을 우선한다. 서로 다른 이름이 같은 거리에 있으면 상속하지 않는다.
    부작용 억제: 이미 지표명이 붙은 값 행(첫 셀이 단위가 아닌 행)은 건드리지 않는다.
    """
    def is_label_row(cells: list[str]) -> bool:
        cells = [c for c in cells if not _FOOTNOTE_MARK_RE.fullmatch(c.strip())]
        if len(cells) != 1:
            return False
        c = cells[0].strip()
        if not c or _is_number_cell(c) or _UNIT_LEAD_RE.match(c) or _FOOTNOTE_MARK_RE.match(c):
            return False
        return True

    def is_unit_led_value_row(cells: list[str]) -> bool:
        if len(cells) < 2:
            return False
        if not _UNIT_LEAD_RE.match(cells[0].strip()):
            return False
        return any(_is_number_cell(c) for c in cells[1:])

    texts = [[t for _x, t in r] for r in rows]
    prefixes: list[str | None] = [None] * len(rows)
    for j, cells in enumerate(texts):
        if not is_unit_led_value_row(cells):
            continue
        candidates = [
            (abs(i - j), next(t.strip() for t in texts[i]
                             if not _FOOTNOTE_MARK_RE.fullmatch(t.strip())))
            for i in range(max(0, j - _LABEL_INHERIT_MAX_SPAN), min(len(rows), j + _LABEL_INHERIT_MAX_SPAN + 1))
            if i != j and is_label_row(texts[i]) and rows[i][0][0] < rows[j][0][0]
        ]
        if candidates:
            distance = min(d for d, _label in candidates)
            labels = {label for d, label in candidates if d == distance}
            if len(labels) == 1:
                prefixes[j] = labels.pop()

    out: list[list[tuple[float, str]]] = []
    for i, r in enumerate(rows):
        if prefixes[i] is not None and r:
            # 레이블을 첫 셀 x좌표 바로 앞(-1)에 삽입 — 정렬·컬럼 매핑에 영향 없게.
            out.append([(r[0][0] - 1.0, f"[{prefixes[i]}]")] + r)
        else:
            out.append(r)
    return out


def _attach_column_headers(rows: list[list[tuple[float, str]]]) -> list[list[tuple[float, str]]]:
    """다중 컬럼 표에서 각 값에 컬럼 헤더 라벨을 부착한다(전사/합계 식별).

    실측(모비스 p.53): '재생에너지 사용·전환율 | 1) | % | 0.2 | … | 12.9'는 법인별 12컬럼.
    행은 복원되나 12개 중 어느 것이 전사(합계)인지 알 수 없어 35.0/12.9 오염이 났다.
    헤더 행('… 합계 | 국내(별도) | …')의 각 셀 x중심과 값의 x중심을 최근접 매칭해
    값 뒤에 `(합계)` 등 컬럼 라벨을 붙인다 → mini가 전사 값을 식별할 수 있다.

    실측 검증: 값이 우측정렬이라 헤더보다 ~15pt 우측이나, 컬럼 피치(~46pt)가 훨씬 커
    최근접 중심 매칭이 12/12 정확(12.9→합계).

    **2단 헤더(연도 위 / 집계 아래)** — 2026-07-29 추가.
    실측(모비스 p.70)에서 연도 행과 집계 행이 분리된 표가 75개 발견됐다:

        2022 | 2023 | 2024                                   ← ① 연도 행 (3열)
        국내(별도) | 국내 자회사 | 해외 자회사 | 합계  × 3세트     ← ② 집계 행 (12열)
        폐기물 처리량 | ton | 1,693(국내(별도)) … 17,694(합계) | 1,208(…) … 17,129(합계) | …

    종전에는 ②만 부착해 `(합계)`가 세 번 똑같이 붙었다. 값 12개가 전부 살아 있는데도
    라벨이 같아 **하류 LLM이 3세트를 1세트로 접고 첫 세트만 뽑은 뒤 `period='미상'`을
    달았다**(근거: `docs/연도미상_원인조사_2026-07-29.md`). 데이터 소실이 아니라 라벨
    모호가 원인이므로 연도를 붙이면 셋이 갈린다.

    형식은 `17,694(합계|2024)` — 구분자 '|'로 **집계와 연도를 분리**한다. 하류
    `_map_vlm_json`이 연도는 `period`, 집계는 `metric_hint`로 보내야 하기 때문이다.
    연도를 hint에 섞으면 node_select의 수식어·집계 판정이 오작동한다.

    연도↔집계 매핑은 **x중심 최근접**이다. 연도 라벨은 자기가 관장하는 집계 그룹의
    중앙에 놓인다(실측: 2022@333.1이 264.0~402.2의 4개를, 2023@517.3이 448.2~586.4를
    덮는다. 그룹내 최대거리 69.1 vs 차선 연도 최소거리 115.1 — 여유 1.67배).

    안전장치(틀린 라벨 부착 방지). 기존 3종을 그대로 두고 연도용 2종을 더한다:
      · 헤더 행을 못 찾으면 그 표 구간은 원본 유지(부착 생략).
      · 헤더가 값보다 위 행에 있어야 하고, 값 셀 수가 헤더 컬럼 수를 넘으면 부착 생략.
      · 매칭 거리가 컬럼 간격의 절반을 넘으면 그 값은 부착 생략(경계 밖).
      · **연도 그룹이 균등하지 않으면 연도 부착만 생략**(집계 부착은 종전대로 유지).
        집계 컬럼 수가 연도 수로 나눠지지 않거나 그룹 크기가 서로 다르면 매핑을
        신뢰할 수 없다 — 틀린 연도는 미상보다 나쁘다.
      · **연도 전용 헤더 행이 없으면 아무것도 붙지 않는다.** 열이 연도가 아닌 표
        (신한 p.160 Scope 구분)를 건드리지 않기 위한 조건이다.
    """
    def is_header_row(cells: list[str]) -> bool:
        kw = sum(1 for c in cells if any(k in c for k in _COL_HEADER_KEYWORDS))
        yr = sum(1 for c in cells if _YEAR_HEADER_RE.fullmatch(c.strip()))
        return (kw + yr) >= 2  # 컬럼 라벨/연도가 2개 이상이면 헤더 행

    # 헤더 컬럼: (x중심, 라벨). 각주 마커·빈 셀 제외.
    def header_cols(row: list[tuple[float, str]]) -> list[tuple[float, str]]:
        cols = [(x, t.strip()) for x, t in row
                if t.strip() and not _FOOTNOTE_MARK_RE.match(t.strip())
                and any(k in t for k in _COL_HEADER_KEYWORDS)]
        return cols

    # 연도 헤더 행인가 — 연도(목표/실적 수식어 보존) 2개 이상 + 집계 키워드 0개.
    # 집계 키워드가 섞인 행('지표 | 단위 | 2023 | 2024')은 1단 헤더이므로 대상이 아니다.
    def year_cols(row: list[tuple[float, str]]) -> list[tuple[float, str]]:
        years = [(x, t.strip()) for x, t in row if _YEAR_HEADER_RE.fullmatch(t.strip())]
        if len(years) < 2:
            return []
        if any(any(k in t for k in _COL_HEADER_KEYWORDS) for _x, t in row):
            return []
        return years

    def map_years(
        cols: list[tuple[float, str]],
        years: list[tuple[float, str]],
    ) -> dict[int, str] | None:
        """집계 컬럼 인덱스 → 연도 라벨. 매핑이 균등하지 않으면 None(연도 부착 생략)."""
        if not years or len(cols) < len(years) or len(cols) % len(years) != 0:
            return None
        assigned: dict[int, str] = {}
        counts: dict[str, int] = {}
        for idx, (cx, _label) in enumerate(cols):
            yx, ylabel = min(years, key=lambda y: abs(y[0] - cx))
            assigned[idx] = ylabel
            counts[ylabel] = counts.get(ylabel, 0) + 1
        # 모든 연도가 같은 개수의 집계 컬럼을 관장해야 한다(3연도 × 4집계 = 12).
        expected = len(cols) // len(years)
        if len(counts) != len(years) or any(c != expected for c in counts.values()):
            return None
        return assigned

    out = [list(r) for r in rows]
    cur_cols: list[tuple[float, str]] = []
    col_pitch = 0.0
    cur_years: dict[int, str] = {}                # 집계 컬럼 idx → 연도 라벨
    pending_years: list[tuple[float, str]] = []   # 직전에 본 연도 전용 헤더 행
    pending_at = -99                              # 그 행의 인덱스(신선도 판정용)
    for i, r in enumerate(rows):
        cells = [t for _x, t in r]
        if is_header_row(cells):
            # 연도 전용 행이면 다음 집계 헤더에 넘길 연도로 보류한다. cur_cols 초기화는
            # 종전과 동일하게 수행한다(연도 행은 종전에도 헤더 행으로 판정돼
            # header_cols()가 []를 돌려 부착을 끊었다) — 잘 되던 표의 동작 보존.
            yrs = year_cols(r)
            if yrs:
                pending_years, pending_at = yrs, i
            cur_cols = header_cols(r)
            cur_years = {}
            if len(cur_cols) >= 2:
                diffs = [cur_cols[k + 1][0] - cur_cols[k][0] for k in range(len(cur_cols) - 1)]
                col_pitch = min(d for d in diffs if d > 0) if any(d > 0 for d in diffs) else 0.0
                # 연도 행이 **바로 위 구간**에 있을 때만 2단 헤더로 본다. 오래된 연도
                # 행을 무관한 표에 물리면 틀린 연도가 붙는다.
                if pending_years and (i - pending_at) <= _LABEL_INHERIT_MAX_SPAN:
                    cur_years = map_years(cur_cols, pending_years) or {}
                pending_years = []
            continue
        if not cur_cols or col_pitch <= 0:
            continue
        # 값 셀만 골라 최근접 헤더 컬럼 라벨 부착
        val_idxs = [k for k, (_x, t) in enumerate(r) if _is_number_cell(t.strip())]
        if len(val_idxs) > len(cur_cols):
            continue  # 값이 헤더 컬럼 수보다 많음 → 정렬 신뢰 불가, 부착 생략
        new_row = list(r)
        for k in val_idxs:
            vx, vt = r[k]
            best_idx = min(range(len(cur_cols)), key=lambda c: abs(cur_cols[c][0] - vx))
            best_x, best_label = cur_cols[best_idx]
            if abs(best_x - vx) <= col_pitch * 0.5:
                year = cur_years.get(best_idx)
                label = f"{best_label}|{year}" if year else best_label
                new_row[k] = (vx, f"{vt}({label})")
        out[i] = new_row
    return out


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

# 비정형 텍스트 처리 상한 — 지속가능경영보고서(100p+) 전량 처리를 위해 상향.
# 기존 max_pages=10 + 프롬프트 4,000자 컷으로는 대형 보고서의 내용 대부분이 유실됐다
# (2026-07-15 실보고서 배치 검증에서 발견 — 삼성전기 130p 중 앞 10p만 처리).
_UNSTRUCTURED_MAX_PAGES = 300
# 청크당 글자 수 — 기존 단일 호출의 프롬프트 예산(4,000자)을 그대로 청크 단위로 유지.
_UNSTRUCTURED_CHUNK_CHARS = 4000


def _split_text_chunks(text: str, chunk_chars: int) -> list[str]:
    """줄 경계를 지키며 chunk_chars 이하 청크로 분할한다."""
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for _line_no, line in _bounded_text_lines(text, chunk_chars):
        if size + len(line) + 1 > chunk_chars and buf:
            chunks.append("\n".join(buf))
            buf, size = [], 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        chunks.append("\n".join(buf))
    return chunks or [text]


def _bounded_text_lines(text: str, limit: int):
    """긴 산문은 공백에서 나누며 숫자 토큰 중간은 자르지 않는다."""
    if limit <= 0:
        raise ValueError("OCR chunk limit must be positive")
    for line_no, line in enumerate(text.splitlines(), 1):
        while len(line) > limit:
            spaces = list(re.finditer(r"\s+", line[:limit + 1]))
            end = spaces[-1].start() if spaces else limit
            if end == 0:
                end = limit
            # 공백 없는 문장도 처리하되 숫자를 두 호출의 별개 값으로 바꾸지 않는다.
            if end < len(line) and re.fullmatch(r"[-+\d,.]{2}", line[end - 1:end + 1]):
                number = re.search(r"[-+\d,.]+$", line[:end])
                if number:
                    end = number.start()
            if end <= 0:
                raise ValueError(f"OCR line {line_no}: numeric token exceeds chunk limit")
            yield line_no, line[:end]
            line = line[end:]
        yield line_no, line


def _unstructured_chunks(raw_text: str, page_texts: list[tuple[int, str]] | None = None) -> list[dict[str, Any]]:
    """실제 페이지 경계에서 나누고, 큰 표의 다음 청크에는 헤더만 반복한다."""
    limit = _UNSTRUCTURED_CHUNK_CHARS
    result = []
    for page, text in page_texts if page_texts is not None else [(None, raw_text)]:
        headers: list[str] = []
        buf: list[str] = []
        context = ""
        start = end = 0
        last_table_line = -_LABEL_INHERIT_MAX_SPAN - 1

        def flush():
            if buf:
                body = "\n".join(buf)
                result.append({"text": context + body, "body": body, "page": page,
                               "line_start": start, "line_end": end,
                               "context_repeated": bool(context)})

        for line_no, line in _bounded_text_lines(text, limit):
            cells = [c.strip() for c in line.split(" | ")]
            years = sum(bool(_YEAR_HEADER_RE.fullmatch(c)) for c in cells)
            scopes = sum(any(k in c for k in _COL_HEADER_KEYWORDS) and not re.search(r"\d", c)
                         for c in cells)
            header = years >= 2 or scopes >= 2
            data_row = len(cells) > 1 and any(re.match(r"^-?[\d,]+(?:\.\d+)?(?:\(|$)", c)
                                             for c in cells) and not header
            size = len(context) + len("\n".join(buf)) + (1 if buf else 0) + len(line)
            if buf and size > limit:
                flush()
                buf, context = [], ""
                if data_row and headers and line_no - last_table_line <= _LABEL_INHERIT_MAX_SPAN + 1:
                    prefix = "[표 머리글 문맥: 아래 본문 값 해석에만 사용]\n" + "\n".join(headers) + "\n[본문]\n"
                    if len(prefix) + len(line) <= limit:
                        context = prefix
            if not buf:
                start = line_no
            buf.append(line)
            end = line_no
            if years >= 2:
                headers = [line]
            elif header:
                # 연도·구분·집계의 최대 3줄만 문맥으로 반복하며 수치 행은 복제하지 않는다.
                headers = (headers + [line])[-(_LABEL_INHERIT_MAX_SPAN + 1):]
            elif headers and set(cells) <= {"구분", "단위", "기준연도", ""}:
                headers.append(line)
            if header or data_row:
                last_table_line = line_no
        flush()
    return result


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
        return _unstructured_fallback(file_path, doc_type, "missing_api_key")

    # 1) 디지털 PDF 텍스트
    pages = _extract_pages_pymupdf(file_path, max_pages=_UNSTRUCTURED_MAX_PAGES)
    page_texts = list(enumerate(pages)) if pages else None
    raw_text = "\n".join(pages)
    raw_text_source = "pymupdf" if raw_text.strip() else None
    upstage_error = None

    # 2) 스캔본 → Upstage Document Parse OCR로 텍스트화
    if not raw_text.strip():
        if _get_upstage_key():
            try:
                tokens = _call_upstage_dp(file_path, ocr_mode="force")
                raw_text = "\n".join(t["text"] for t in tokens)
                # DP가 준 실제 page 메타데이터가 모두 유효할 때만 페이지 출처로 신뢰한다.
                page_texts = None
                if tokens and all(type(t.get("page")) is int and t["page"] >= 0 for t in tokens):
                    grouped: dict[int, list[str]] = {}
                    for token in tokens:
                        grouped.setdefault(token["page"], []).append(token["text"])
                    page_texts = [(page, "\n".join(lines)) for page, lines in grouped.items()]
                raw_text_source = "upstage"
            except Exception as e:
                upstage_error = str(e)
                raw_text = ""

    if not raw_text.strip():
        return _unstructured_fallback(file_path, doc_type, "text_extraction_failed", upstage_error)

    return _extract_unstructured_text(
        file_path, doc_type=doc_type, raw_text=raw_text,
        raw_text_source=raw_text_source, upstage_error=upstage_error,
        page_texts=page_texts,
    )


def _unstructured_fallback(file_path: str, doc_type: str, reason: str, detail: str | None = None) -> OcrExtraction:
    from ..config import SETTINGS
    from ..llm import LLMUnavailableError
    if SETTINGS.strict_llm:
        raise LLMUnavailableError(f"OCR extraction failed ({reason}): {detail or Path(file_path).name}")
    ext = _mock_unstructured(file_path, doc_type)
    ext.router_meta.update({"extraction_status": "mock", "failure_reason": reason})
    if detail:
        ext.router_meta["failure_detail"] = detail
    return ext


def _extract_unstructured_text(
    file_path: str, *, doc_type: str, raw_text: str,
    raw_text_source: str | None = None, upstage_error: str | None = None,
    page_texts: list[tuple[int, str]] | None = None,
) -> OcrExtraction:
    """텍스트 비정형 문서 → LLM(gpt-4.1-mini via Azure)으로 정량·정성 추출.

    대형 문서(지속가능경영보고서 등)는 청크로 나눠 전량 순회한다.
    실제 페이지별로 4,000자 이내에서 읽고 페이지 출처를 각 수치·조항에 붙인다.
    """
    import json as _json, re
    from . import ocr_cache
    from ..llm import LLMClient
    from .prompts import VLM_EXTRACT_SYSTEM, VLM_EXTRACT_PROMPT

    from ..config import SETTINGS
    from ..llm import LLMUnavailableError
    try:
        chunks = _unstructured_chunks(raw_text, page_texts)
    except ValueError as exc:
        if SETTINGS.strict_llm:
            raise LLMUnavailableError(str(exc)) from exc
        return OcrExtraction(
            source_file=Path(file_path).name, channel=DocChannel.UNSTRUCTURED,
            doc_type=doc_type, raw_text=raw_text,
            router_meta={"extraction_status": "failed", "failure_reason": "chunking_failed",
                         "failure_detail": str(exc), "chunks": 0})
    client = LLMClient()
    metrics: list = []
    clauses: list[ExtractedClause] = []

    # 청크별 LLM 응답 캐시(2026-07-27). 키는 **실제 LLM 입력의 해시**다 —
    # 전처리(_reconstruct_rows_from_dict 등)를 고치면 입력이 바뀌어 자동 무효화된다.
    #
    # 캐시가 담는 건 **LLM 원본 응답 JSON까지다.** _map_vlm_json(G6 각주 마커 배제 포함)은
    # 히트에서도 항상 재실행된다 — 결정적 후처리를 캐시에 굳히면 G6를 손봤을 때 히트 청크가
    # 옛 필터 결과를 돌려줘 수정이 무효가 된다(전처리 함정의 후처리판). 조항 보강과
    # extract_document의 _backfill_kesg_codes도 마찬가지로 캐시 밖이다.
    mode = ocr_cache.cache_mode()
    cache_model = ocr_cache.model_name()
    cache_connection = client.cache_connection() if hasattr(client, "cache_connection") else {"provider": "test-double"}
    cache_prompt = VLM_EXTRACT_SYSTEM + "\n" + VLM_EXTRACT_PROMPT
    hits = misses = 0
    failures: list[dict[str, Any]] = []
    chunk_runs: list[dict[str, Any]] = []
    # 모델이 라벨만 읽고 수치를 보고하지 못한 행 — 실패가 아니라 미확인으로 남긴다.
    unvalued_records: list[dict[str, Any]] = []
    reconciled_records: list[dict[str, Any]] = []
    used_mock = False

    for chunk_index, chunk in enumerate(chunks):
        page = chunk["page"]
        page_label = str(page) if page is not None else "미상"
        prompt = (VLM_EXTRACT_PROMPT.format(doc_type=doc_type)
                  + f"\n\n[원본 페이지 인덱스(0부터): {page_label}]\n문서 텍스트:\n{chunk['text']}")
        key = ""
        if mode != ocr_cache.MODE_DISABLED:
            key = ocr_cache.make_key(
                model=cache_model, prompt=cache_prompt,
                doc_type=doc_type, llm_input=prompt, connection=cache_connection,
            )
        data: dict | None = None
        cache_write = False
        if mode == ocr_cache.MODE_ON and key:
            data = ocr_cache.load_response(key)
            if data is not None:
                hits += 1

        if data is None:
            misses += 1
            try:
                resp = client.complete(
                    system=VLM_EXTRACT_SYSTEM,
                    user=prompt,
                    json_mode=True,
                    temperature=0.0,
                    mock_hint="ocr_unstructured",
                )
                response_mock = bool(resp.used_mock)
                used_mock |= response_mock
                chunk_runs.append({"chunk_index": chunk_index, "page": page, "cache_hit": False,
                                   "used_mock": response_mock, "model": resp.meta.get("model"),
                                   "provider": resp.meta.get("provider")})
                if response_mock and (SETTINGS.strict_llm or not SETTINGS.force_mock):
                    raise LLMUnavailableError("OCR LLM returned a mock fallback")
                m = re.search(r'\{.*\}', resp.content, re.DOTALL)
                data = _json.loads(m.group() if m else resp.content)
                if not isinstance(data, dict) or not all(isinstance(data.get(k), list) for k in ("metrics", "clauses")):
                    raise ValueError("OCR response must contain metrics and clauses arrays")
            except Exception as exc:
                if SETTINGS.strict_llm:
                    raise LLMUnavailableError(f"OCR chunk {chunk_index} failed: {exc}") from exc
                # 청크 하나의 JSON이 깨져도 문서 전체를 버리지 않는다.
                # 깨진 응답은 캐시하지 않는다 — 재시도 여지를 남긴다.
                reason = "invalid_response" if isinstance(exc, (ValueError, TypeError)) else "llm_failed"
                failures.append({"chunk_index": chunk_index, "page": page,
                                 "reason": reason, "detail": str(exc)})
                logger.warning("비정형 청크 추출 실패 — 건너뜀 [%s]: %s", Path(file_path).name, exc)
                continue
            cache_write = bool(key and not response_mock)
        else:
            chunk_runs.append({"chunk_index": chunk_index, "page": page, "cache_hit": True})

        # 히트·미스 공통 경로 — 결정적 후처리는 캐시에 굳히지 않는다(G6 등이 여기 있다).
        if not isinstance(data, dict) or not all(isinstance(data.get(k), list) for k in ("metrics", "clauses")):
            if SETTINGS.strict_llm:
                raise LLMUnavailableError(f"OCR cached chunk {chunk_index}: invalid response schema")
            failures.append({"chunk_index": chunk_index, "page": page, "reason": "invalid_cached_response"})
            continue
        record_issues: list[dict[str, Any]] = []
        chunk_metrics, chunk_clauses = _map_vlm_json(
            data, page_no=page, source_text=chunk["body"], issues=record_issues)
        # 깨진 레코드(스키마 위반·비유한 수·불리언·라벨 없음)와 "수치를 보고하지 못한
        # 레코드"를 구분한다. 후자까지 strict 중단으로 처리하면 라벨만 있는 표 행 한 줄이
        # 176청크 문서 전체를 실패시켜 strict 모드로 실제 보고서를 처리할 수 없다(실측).
        fatal_issues = [issue for issue in record_issues if issue.get("fatal", True)]
        # 값이 실린 채 원문 표기와 대조된 행은 '수치 미보고'가 아니므로 버킷을 나눈다.
        non_fatal = [issue for issue in record_issues if not issue.get("fatal", True)]
        reconciled_issues = [issue for issue in non_fatal
                             if issue.get("reason") in _VALUE_RECONCILED_REASONS]
        unvalued_issues = [issue for issue in non_fatal
                           if issue.get("reason") not in _VALUE_RECONCILED_REASONS]
        if unvalued_issues:
            unvalued_records.append({"chunk_index": chunk_index, "page": page,
                                     "records": unvalued_issues})
        if reconciled_issues:
            reconciled_records.append({"chunk_index": chunk_index, "page": page,
                                       "records": reconciled_issues})
        if fatal_issues:
            if SETTINGS.strict_llm:
                raise LLMUnavailableError(f"OCR chunk {chunk_index}: invalid records {fatal_issues}")
            failures.append({"chunk_index": chunk_index, "page": page,
                             "reason": "invalid_records", "records": fatal_issues})
        elif cache_write:
            ocr_cache.store_response(
                key, data,
                model=cache_model, prompt=cache_prompt, doc_type=doc_type,
                source_file=Path(file_path).name, llm_input=prompt, connection=cache_connection,
            )
        metrics.extend(chunk_metrics)
        clauses.extend(chunk_clauses)

    # 존재형 조항은 문서의 기존 코드 여부로 중복을 막되 실제 페이지 출처를 유지한다.
    if page_texts is None:
        clauses = _augment_unstructured_clauses(clauses, raw_text=raw_text, doc_type=doc_type)
    else:
        for page, text in page_texts:
            before = len(clauses)
            clauses = _augment_unstructured_clauses(clauses, raw_text=text, doc_type=doc_type)
            for clause in clauses[before:]:
                clause.page, clause.page_source = page, "chunk"
    # 반복되는 머리글은 추출 대상이 아니다. 동일 페이지·인용의 완전 중복만 제거한다.
    seen_metrics: set[tuple] = set()
    unique_metrics = []
    for metric in metrics:
        key = (metric.metric_hint, metric.value, metric.unit, metric.period,
               metric.kesg_code_guess, metric.page, metric.quote)
        if metric.page is None or not metric.quote or key not in seen_metrics:
            unique_metrics.append(metric)
        seen_metrics.add(key)
    metrics = unique_metrics
    seen_clauses: set[tuple] = set()
    unique_clauses = []
    for clause in clauses:
        key = (clause.section, clause.text, clause.kesg_code_guess, clause.page, clause.quote)
        if clause.page is None or not clause.quote or key not in seen_clauses:
            unique_clauses.append(clause)
        seen_clauses.add(key)
    clauses = unique_clauses

    if mode != ocr_cache.MODE_DISABLED:
        logger.info("[OCR] 캐시 %s — hit %d / miss %d [%s]",
                    mode, hits, misses, Path(file_path).name)

    # 히트/미스 라벨 — 부분 히트는 'miss'로 본다(한 청크라도 라이브 호출이 있었다는 뜻).
    if mode == ocr_cache.MODE_DISABLED:
        cache_state = "disabled"
    elif mode == ocr_cache.MODE_REFRESH:
        cache_state = "refresh"
    else:
        cache_state = "hit" if (hits and not misses) else "miss"

    meta: dict = {
        "engine": "gpt-4.1-mini-text",
        "vision": False,
        "raw_text_source": raw_text_source or "unknown",
        "raw_text_len": len(raw_text),
        "chunks": len(chunks),
        "extraction_status": ("mock" if used_mock and SETTINGS.force_mock else
                              "partial" if failures and (metrics or clauses or len(failures) < len(chunks)) else
                              "failed" if failures or not chunks else "complete"),
        "chunk_failures": failures,
        # 수치 미보고 행은 성공/실패와 별도로 보고한다. 문서를 전부 읽었다는 인상을 주지
        # 않도록 개수와 라벨을 남기되, 추출 상태를 partial/failed로 바꾸지는 않는다.
        "unvalued_records": unvalued_records,
        "unvalued_record_count": sum(len(entry["records"]) for entry in unvalued_records),
        # 값이 **실린** 채 원문 표기와 대조해 되잡거나 사람 검토로 남긴 행. 값이 실리지 않은
        # `unvalued_records`와 섞으면 "수치를 보고하지 못한 행" 수가 부풀려지므로 따로 센다.
        "value_reconciliations": reconciled_records,
        "value_reconciliation_count": sum(len(entry["records"]) for entry in reconciled_records),
        "chunk_sources": [{k: chunk[k] for k in ("page", "line_start", "line_end", "context_repeated")}
                          | {"chunk_index": i} for i, chunk in enumerate(chunks)],
        "chunk_runs": chunk_runs,
        "unresolved_metric_pages": sum(m.page is None for m in metrics),
        "unresolved_clause_pages": sum(c.page is None for c in clauses),
        "missing_metric_quotes": sum(not m.quote for m in metrics),
        "missing_clause_quotes": sum(not c.quote for c in clauses),
        "source_page_count": len(page_texts) if page_texts is not None else None,
        "empty_source_pages": [page for page, text in (page_texts or []) if not text.strip()],
        # 캐시 히트를 감추지 않는다 — 원장 스크립트 헤더·pipeline 로그가 이걸 읽는다.
        "ocr_cache": cache_state,
        "ocr_cache_hits": hits,
        "ocr_cache_misses": misses,
    }
    if upstage_error:
        meta["upstage_error"] = upstage_error
    if used_mock:
        meta["mock"] = True

    return OcrExtraction(
        source_file=Path(file_path).name,
        channel=DocChannel.UNSTRUCTURED,
        doc_type=doc_type,
        metrics=metrics,
        clauses=clauses,
        raw_text=raw_text,
        router_meta=meta,
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


# G6. 각주 마커 정규식 — 지표명 말미의 'N)' (예: '재해율 4)', '도수율6)').
_FOOTNOTE_MARK_TAIL_RE = re.compile(r"(\d+)\s*\)\s*$")


def _is_footnote_marker_value(metric_hint: str, value: float) -> bool:
    """지표명이 각주 마커 'N)'로 끝나고 값이 그 마커 숫자와 같으면 True(오파싱).

    표에서 '재해율 4)' 같은 각주 마커의 번호(4)를 지표 값(4.0)으로 잘못 읽는 사례를
    배제한다(삼성전기 재해율 4.0 오염 — 실제 0.033%). 마커 번호와 값이 다르면(정상값이
    우연히 각주 붙은 항목) 건드리지 않는다 — 오검출보다 미검출 리스크를 최소화."""
    m = _FOOTNOTE_MARK_TAIL_RE.search(metric_hint or "")
    if not m:
        return False
    try:
        return float(m.group(1)) == float(value)
    except (TypeError, ValueError):
        return False


# 2단 헤더 부착 라벨의 연도 꼬리 — '합계|2024', '국내(별도)|2022'.
# `_attach_column_headers`가 만든 형식이다(그 함수 docstring §2단 헤더).
_HINT_YEAR_TAIL_RE = re.compile(r"\|\s*(20\d{2})(?:년)?\s*(목표|실적|계획|전망)?\s*\)?\s*$")


def _split_hint_year(hint: str, period: str) -> tuple[str, str]:
    """hint 말미의 `|연도`를 떼어내 (집계만 남은 hint, 연도) 로 가른다 — 2026-07-29.

    재구성 텍스트가 `17,694(합계|2024)`를 주면 LLM은 보통 연도를 period로 옮기지만,
    라벨을 통째로 metric_hint에 복사하는 경우가 있다. 그러면 hint에 '2024'가 섞여
    node_select의 수식어·집계 판정이 오작동한다(예: '목표'·'대비' 어휘 판정 문맥이
    흐려진다). 프롬프트만으로 보장하지 않고 파싱에서도 갈라 둔다.

    period가 이미 채워져 있으면 **덮어쓰지 않는다** — LLM이 표 제목 등에서 읽은
    더 구체적인 연도('2024-12')를 잃지 않기 위한 것이다. hint의 꼬리만 떼어낸다.
    """
    m = _HINT_YEAR_TAIL_RE.search(hint or "")
    if not m:
        return hint, period
    cleaned = _HINT_YEAR_TAIL_RE.sub("", hint).strip()
    if m.group(2):
        cleaned += " " + m.group(2)
    # '(합계' 처럼 여는 괄호만 남으면 정리한다.
    if cleaned.count("(") > cleaned.count(")"):
        cleaned = cleaned.rstrip("(").strip()
        if cleaned.count("(") > cleaned.count(")"):
            cleaned += ")"
    return (cleaned or hint), (period or m.group(1))


_QUOTE_MAX_CHARS = 200
_QUOTE_HINT_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]{2,}")


def _value_surface_forms(value: float) -> list[str]:
    """이 값이 원문에 적힐 수 있는 표기들. 자릿수 구분자 유무와 소수점 표기를 모두 본다."""
    forms: list[str] = []
    if value == int(value):
        integer = int(value)
        forms += [f"{integer:,}", str(integer), f"{integer:,}.0", f"{integer}.0"]
    else:
        forms += [f"{value:,}", str(value)]
    return list(dict.fromkeys(forms))


def _contains_value(line: str, form: str) -> bool:
    """숫자 경계를 지켜 포함 여부를 본다. '1,264'가 '11,264'에 걸리면 안 된다."""
    pattern = rf"(?<![\d.,]){re.escape(form)}(?![\d,]|\.\d)"
    return re.search(pattern, line) is not None


# ── 값을 그 값의 근거 문구와 맞춰 보는 후처리 (2026-09-29) ─────────────────────
# 두 보고서 라이브 실측(현대모비스 167쪽 4,063건 · 삼성전기 157쪽 3,175건)에서 기존 게이트가
# 구조적으로 못 보던 결함 두 종류를 찾았다. 둘 다 원인이 같다 — **모델이 적은 값을 그 값의
# 근거 문구와 대조하지 않았다.** D1·G2·G4는 모두 '생성된 문장'을 기준으로 돌기 때문에 문장이
# 인용하지 않은 지표는 검사 범위 밖이었다. 그래서 추출 직후 여기서 대조한다.
_KR_SCALES = {"조": 1e12, "억": 1e8, "천만": 1e7, "백만": 1e6, "만": 1e4, "천": 1e3}
# 값이 **실린 채** 원문과 대조된 사유. 값이 실리지 않은 사유(`unvalued_records`)와 섞으면
# "수치를 보고하지 못한 행" 수가 부풀려지므로 라우터 메타에서 버킷을 나누는 기준이다.
_VALUE_RECONCILED_REASONS = frozenset({
    "scale_chain_recomposed", "scale_chain_unresolved", "value_not_written_in_evidence",
    # 2026-10-05: 원문 칸의 라벨로 정정한 행, 원문 부정 서술로 0을 복원한 행 — 둘 다 값이 실린다.
    "label_from_table_header", "zero_recovered_from_negation"})
# 배율이 붙은 한 토막: '46조', '1,182억', 그리고 배율 없는 꼬리 '5,000'.
_KR_SCALE_TERM_RE = re.compile(
    r"(?<![0-9A-Za-z가-힣])(\d[\d,]*(?:\.\d+)?)\s*(조|억|천만|백만|만|천)?")


@dataclass(frozen=True)
class _ScaleChain:
    """원문의 연속 배율 표기 하나. 금액만이 아니라 **무엇을 센 수인지**를 함께 들고 다닌다.

    PR 69 검토(2026-09-30)에서 금액만 비교한 탓에 `1만 8,400 kg`이 `18.4 ton`을,
    `교육비 1만 2천 원`이 `교육 시간 2.5 시간`을 덮어썼다. 단위와 앞 라벨을 남겨
    같은 물리량·같은 지표인지 확인한 뒤에만 후보로 쓴다.
    """
    amount: float          # 배율을 곱한 값(뒤 단위 기준). `57조 2,370억 원` → 5.7237e13 원
    surface: str           # 원문 표기 그대로 — 보정·확인 기록의 근거로 남긴다
    unit: str | None       # 뒤에 붙은 단위의 정규 표기(`units.normalize_unit`). 모르면 None
    label: str             # 바로 앞 라벨(같은 절 안). 지표 대조에 쓴다
    unit_text: str = ""    # 원문 단위 표현 전체(`MJ/톤`). 정규화하지 못해도 잘라 버리지 않는다
    compound: bool = False  # 분모 표지가 있는 단위(원단위). 단순 단위와 같은 수량이 아니다
    unit_status: str = "ABSENT"   # `_read_unit`의 판독 상태. 보정은 `SIMPLE_COMPLETE`에서만


def _kr_scale_chain_amounts(text: str) -> set[float]:
    """연속 배율 표기를 **하나의 금액으로 합성**한다. `46조 1,182억` → 4.61182e13.

    배율이 내림차순으로 이어지는 토막만 더한다. 마지막 토막은 배율이 없을 수 있다
    (`1만 5,000톤`의 `5,000`). **배율 없는 꼬리는 바로 앞 배율보다 작을 때만** 더한다 —
    그 조건이 없으면 `1만 5,000톤을 확보하였으며 620억 원`에서 옆 문장의 숫자까지 끌어와
    없는 금액을 만든다(실측으로 확인해 넣은 조건이다).

    토막이 둘 이상일 때만(`parts > 1`) 결과에 넣는다. `3억`처럼 배율이 하나뿐인 표기는
    모델이 틀릴 여지가 없으므로 이 대조의 대상이 아니다.
    """
    return {chain.amount for chain in _kr_scale_chains(text)}


def _kr_scale_chains(text: str) -> list[_ScaleChain]:
    """`_kr_scale_chain_amounts`와 같은 합성 규칙으로, 단위·라벨을 붙여 돌려준다."""
    from ..rag_gates.units import parse_number

    terms = [(m.start(), m.end(), parse_number(m.group(1)), m.group(2))
             for m in _KR_SCALE_TERM_RE.finditer(text)]
    terms = [t for t in terms if t[2] is not None]
    chains: list[_ScaleChain] = []
    for index, (start, end, base, scale) in enumerate(terms):
        if scale is None:
            continue
        total, last_factor, parts = base * _KR_SCALES[scale], _KR_SCALES[scale], 1
        cursor = index + 1
        while cursor < len(terms):
            start_next, end_next, base_next, scale_next = terms[cursor]
            if text[end:start_next].strip():
                break   # 토막 사이에 공백 말고 다른 글자가 있으면 같은 금액이 아니다.
            factor = _KR_SCALES[scale_next] if scale_next else 1.0
            if factor >= last_factor:
                break   # 배율이 내림차순이 아니면 다른 금액의 시작이다.
            if scale_next is None and base_next >= last_factor:
                break
            total += base_next * factor
            last_factor, end, parts = factor, end_next, parts + 1
            cursor += 1
            if scale_next is None:
                break
        if parts > 1:
            unit_read = _read_unit(text, end)
            chains.append(_ScaleChain(amount=total, surface=text[start:end].strip(),
                                      unit=unit_read.unit, label=_leading_label(text, start),
                                      unit_text=unit_read.text, compound=unit_read.compound,
                                      unit_status=unit_read.status))
    return chains


@dataclass(frozen=True)
class _UnitRead:
    """수치 뒤 단위 표현을 읽은 결과. 정규화에 실패해도 원문 표현은 남긴다.

    PR 69 3차 검토(2026-09-30): 분모를 읽지 못한 `MJ/(톤)`·`MJ/` 줄바꿈 `톤`·`MJ/100개`가
    분자 `MJ`로 되돌아가 원단위 값이 총량 후보가 됐다. **`/`가 보이면 단순 단위로 끝나지
    않는다** — 분모를 끝까지 읽지 못하면 `INCOMPLETE`다.
    """
    text: str              # raw_text — 원문 단위 표현 전체(조사 제외). 없으면 ""
    unit: str | None       # canonical_unit — `SIMPLE_COMPLETE`일 때만 채운다
    status: str = "ABSENT"  # `_UNIT_STATUSES`
    span: tuple[int, int] = (0, 0)   # 원문에서 읽은 범위
    has_denominator_marker: bool = False
    reason: str = ""

    @property
    def compound(self) -> bool:
        """분모 표지(`/`)가 있는 표현. 분모를 다 읽었는지와 무관하게 단순 단위가 아니다."""
        return self.has_denominator_marker


# `SIMPLE_COMPLETE` 알려진 단순 단위 / `COMPOUND_COMPLETE` 분모까지 알려진 단위로 읽음 /
# `INCOMPLETE` `/` 뒤를 못 읽었거나 잘림 / `UNKNOWN` 표현은 있으나 사전 밖 / `ABSENT` 단위 없음.
_UNIT_STATUSES = ("SIMPLE_COMPLETE", "COMPOUND_COMPLETE", "INCOMPLETE", "UNKNOWN", "ABSENT")
# 수치 뒤 단위 한 낱말. 영문 뒤의 숫자·지수(`m3`·`m^3`·`tCO2eq`)는 단위의 일부로 읽는다.
# 한글 뒤 숫자(`원4,073억`)는 다음 수치의 시작이므로 끊는다.
_UNIT_WORD = r"(?:[A-Za-z]+(?:\^?\d+)?|[^\sA-Za-z\d.,;:!?'\"|()\[\]{}<>·/／+=~\-])+"
_UNIT_WORD_RE = re.compile(rf"[ \t]*({_UNIT_WORD})")
# 분모 표지. 분모 정규식의 성공 여부와 **따로** 찾는다 — 분모를 못 읽어도 표지는 남는다.
# 표지 앞의 줄바꿈 하나까지는 같은 표현으로 본다(`MJ` 줄바꿈 `/톤`).
_UNIT_SLASH_RE = re.compile(r"[ \t]*(?:\n[ \t]*)?[/／]")
# 분모 낱말은 같은 줄에서만 읽는다. 줄을 넘으면 다른 행·지표의 단위를 빌려 올 수 있다.
_UNIT_DENOMINATOR_RE = re.compile(rf"[ \t]*({_UNIT_WORD})")
# 분모 바로 뒤에 오면 분모를 끝까지 읽지 못한 것이다(`톤(`·`톤/년`·`톤2`).
_UNIT_DENOMINATOR_CONTINUES_RE = re.compile(r"[(（/／\d]")
# 단위 뒤에 붙어도 되는 조사·어미. **이 목록에 없는 접미사는 조사로 보지 않는다** —
# 알 수 없는 접미사를 떼어 내고 앞머리를 단위로 인정하면 다른 수량을 같은 단위로 읽는다.
_UNIT_PARTICLES = ("으로는", "으로", "이며", "이고", "이다", "입니다", "이었다", "였다", "에서",
                   "까지", "부터", "에는", "은", "는", "이", "가", "을", "를", "의", "에", "로",
                   "과", "와", "도", "씩")


def _normalize_unit_word(word: str) -> str | None:
    """단위 낱말 하나를 정규화한다. 사전 표기 그대로이거나 허용된 조사만 붙은 경우만 읽는다."""
    from ..rag_gates.units import normalize_unit

    unit = normalize_unit(word)
    if unit is not None:
        return unit
    for particle in _UNIT_PARTICLES:
        if word.endswith(particle) and len(word) > len(particle):
            unit = normalize_unit(word[:-len(particle)])
            if unit is not None:
                return unit
    return None


def _strip_unit_particle(word: str) -> str:
    for particle in _UNIT_PARTICLES:
        if word.endswith(particle) and len(word) > len(particle):
            return word[:-len(particle)]
    return word


def _read_unit(text: str, end: int) -> _UnitRead:
    """`end` 바로 뒤 단위 표현을 **단계별로** 읽는다. 사전에 없으면 unit=None — 추정해 채우지 않는다.

    1. 분자 낱말을 읽는다. 없으면 `ABSENT`.
    2. 분자 뒤에서 `/`를 **독립적으로** 찾는다. 찾으면 이 뒤로는 단순 단위를 반환하지 않는다.
       같은 줄의 분모 낱말을 경계까지 읽고 두 낱말이 모두 사전에 있으면 `COMPOUND_COMPLETE`,
       빠졌거나(`MJ/`·`MJ/` 줄바꿈 `톤`)·괄호·숫자로 시작하거나(`MJ/(톤)`·`MJ/100개`)·
       사전 밖이면 `INCOMPLETE`. 복합 단위는 이 모듈이 환산하지 않으므로 unit은 늘 None이다.
    3. 표지가 없을 때만 단순 단위로 정규화한다. 사전 밖이면 `UNKNOWN`.
    """
    numerator = _UNIT_WORD_RE.match(text, end)
    slash = _UNIT_SLASH_RE.match(text, numerator.end() if numerator else end)
    if not numerator:
        if slash:
            return _UnitRead(text[end:slash.end()].strip(), None, "INCOMPLETE", (end, slash.end()),
                             True, "numerator_missing")
        return _UnitRead("", None, "ABSENT", (end, end))
    word = numerator.group(1)
    if slash:
        denominator = _UNIT_DENOMINATOR_RE.match(text, slash.end())
        start = numerator.start(1)
        if not denominator:
            return _UnitRead(re.sub(r"\s+", "", text[start:slash.end()]), None, "INCOMPLETE",
                             (start, slash.end()), True, "denominator_missing")
        stripped = _strip_unit_particle(denominator.group(1))
        raw = f"{word}/{stripped}"
        span = (start, denominator.end())
        if _UNIT_DENOMINATOR_CONTINUES_RE.match(text, denominator.end()):
            return _UnitRead(raw, None, "INCOMPLETE", span, True, "denominator_partial")
        if _normalize_unit_word(word) is None or _normalize_unit_word(denominator.group(1)) is None:
            return _UnitRead(raw, None, "INCOMPLETE", span, True, "denominator_unknown")
        return _UnitRead(raw, None, "COMPOUND_COMPLETE", span, True, "compound_not_converted")
    unit = _normalize_unit_word(word)
    span = (numerator.start(1), numerator.end())
    if unit is None:
        return _UnitRead(_strip_unit_particle(word), None, "UNKNOWN", span, reason="unit_not_in_table")
    return _UnitRead(_strip_unit_particle(word), unit, "SIMPLE_COMPLETE", span)


def _trailing_unit(text: str, end: int) -> str | None:
    """`end` 바로 뒤 단위의 정규 표기. `SIMPLE_COMPLETE`가 아니면(복합·잘림·사전 밖) None."""
    read = _read_unit(text, end)
    return read.unit if read.status == "SIMPLE_COMPLETE" else None


# 라벨을 끊는 경계: 절 구분(쉼표·세미콜론·줄바꿈·문장 끝 마침표)과 표 칸 구분(`|`).
_LABEL_BREAK_RE = re.compile(r"[,;\n|]|(?<!\d)\.(?!\d)")


def _leading_label(text: str, start: int) -> str:
    """수치 바로 앞 라벨. 앞 수치를 넘지 않고, 비어 있으면 같은 절의 머리(표 행 제목)를 쓴다.

    `매출 46조 1,182억 원 | 영업이익 3,000억 원`의 둘째 금액 라벨은 `영업이익`이고,
    `매출액 | 57조 2,370억 원`처럼 칸이 나뉜 행은 행 제목 `매출액`이 라벨이다.
    """
    head = text[:start]
    breaks = [m.end() for m in _LABEL_BREAK_RE.finditer(head)]
    digits = [m.end() for m in re.finditer(r"\d", head)]
    near = head[max([0, *breaks, *digits]):]
    if _QUOTE_HINT_TOKEN_RE.search(re.sub(r"\d", " ", near)):
        return near.strip()
    clause_breaks = [m.end() for m in re.finditer(r"[,;\n]|(?<!\d)\.(?!\d)", head)]
    clause = head[max([0, *clause_breaks]):]
    return re.sub(r"[\d.,%]+", " ", clause).strip()


# 조사를 떼고 비교한다(`매출은` ↔ `매출액`). 두 글자 낱말에는 적용하지 않는다.
_LABEL_PARTICLES = tuple("은는이가을를의에도와과로")


def _label_tokens(text: str) -> list[str]:
    tokens = _QUOTE_HINT_TOKEN_RE.findall(re.sub(r"\d", " ", text))
    return [t[:-1] if len(t) >= 3 and t.endswith(_LABEL_PARTICLES) else t for t in tokens]


def _label_names_metric(label: str, hint: str) -> bool:
    """원문 라벨이 이 지표를 가리키는가. 한쪽 낱말이 **모두** 다른 쪽에 들어 있어야 한다.

    낱말 하나만 겹치면 같은 지표로 보지 않는다 — `폐기물 배출량`과 `폐기물 재활용량`은
    `폐기물`이 겹치지만 다른 지표다(한쪽 값으로 다른 쪽을 덮어쓰면 옳은 값을 잃는다).
    """
    label_tokens, hint_tokens = _label_tokens(label), _label_tokens(hint)
    if not label_tokens or not hint_tokens:
        return False
    label_compact, hint_compact = "".join(label_tokens), "".join(hint_tokens)
    if _metric_kinds(label_tokens) != _metric_kinds(hint_tokens):
        # 낱말이 포함돼도 수량의 종류가 다르면 다른 지표다(`에너지 사용량 원단위` ≠ `에너지
        # 사용량`, `산업재해율` ≠ `산업재해`). PR 69 2차 검토에서 부분 포함만으로 같다고 봐
        # 원단위 값으로 총량을 덮어썼다.
        return False
    return (all(t in label_compact for t in hint_tokens)
            or all(t in hint_compact for t in label_tokens))


# 같은 대상이라도 **수량의 종류**를 바꾸는 낱말. 한쪽에만 있으면 같은 지표로 보지 않는다.
_METRIC_KIND_WORDS = {
    "원단위": "intensity", "집약도": "intensity", "강도": "intensity",
    "평균": "average", "누계": "cumulative", "누적": "cumulative",
    "비율": "ratio", "비중": "ratio", "증감": "change", "증가": "change", "감소": "change",
    "대비": "change", "목표": "target", "계획": "target", "예상": "target", "전망": "target",
}
# 낱말 끝의 `율`·`률`(비율)과 `당`(단위당 — `1인당`·`톤당`)도 종류를 바꾼다.
_METRIC_KIND_SUFFIXES = {"율": "ratio", "률": "ratio", "당": "intensity"}


def _metric_kinds(tokens: list[str]) -> frozenset[str]:
    kinds = {kind for token in tokens for word, kind in _METRIC_KIND_WORDS.items() if word in token}
    kinds |= {kind for token in tokens for suffix, kind in _METRIC_KIND_SUFFIXES.items()
              if token.endswith(suffix)}
    return frozenset(kinds)


# 글자에 붙은 숫자까지 읽는 느슨한 스캔. `numeric_tokens`의 경계 규칙을 쓰지 않는 이유는
# **묻는 것이 다르기 때문**이다. 그 규칙은 생성된 문장에서 '주장으로 삼을 수치'를 고르려고
# 한글·마침표에 붙은 숫자를 버린다. 여기서 묻는 것은 "사람이 이 문구에서 그 값을 볼 수 있나"
# 이고, `Lv.5`·`제7회`·`3,671억 원4,073억 원`(원문이 칸을 붙여 뽑은 것)의 숫자는 사람 눈에
# 분명히 보인다. 엄격한 규칙을 쓰면 값이 맞는 지표 8건에 검토 표시가 붙었다(실측).
# 부호를 함께 읽는다 — 재무제표 행은 `-146,701,456`처럼 음수가 흔하고, 부호를 버리면 값이
# 맞는 지표 11건에 검토 표시가 붙었다(실측, 삼성전기 p.149~150 기타자본·지분법손익 등).
# 앞 경계에서 `.`은 막지 않는다 — `Lv.5`의 5는 사람 눈에 보이는 값이다.
_LOOSE_AMOUNT_RE = re.compile(r"(?<![\d,])([-−+]?)(\d[\d,]*(?:\.\d+)?)\s*(조|억|천만|백만|만|천)?")


def _evidence_amounts(text: str) -> set[float]:
    """근거 문구에서 사람이 읽을 수 있는 값 후보. 맨 수·배율 표기·연속 배율 합성을 모두 넣는다."""
    from ..rag_gates.units import parse_number

    found: set[float] = set()
    for match in _LOOSE_AMOUNT_RE.finditer(text):
        base = parse_number(match.group(2))
        if base is None:
            continue
        if match.group(1) in ("-", "−"):
            base = -base
        found.add(base)
        if match.group(3):
            found.add(base * _KR_SCALES[match.group(3)])
    return found | _kr_scale_chain_amounts(text)


def _value_written_in_evidence(value: float, unit: str, evidence: str) -> bool:
    """보고된 값이 근거 문구에 **숫자로** 적혀 있는가.

    값 자체와, 단위 배율을 적용한 절대 금액을 모두 대조한다(`57조 2,370억 원`을 근거로
    `572,370 억 원`을 보고한 경우처럼 표기 단위가 다를 수 있다). 1% 허용은 원문이 반올림해
    적는 경우(`19조 5,184억` → `19.5조 원`)를 결함으로 세지 않기 위한 것이다.
    """
    from ..rag_gates.units import numeric_equal

    amounts = _evidence_amounts(evidence)
    if not amounts:
        return False
    absolute = value * _unit_scale(unit)
    if any(numeric_equal(value, amount) or numeric_equal(absolute, amount)
           for amount in amounts):
        return True
    # 단위를 바꿔 적은 값(`18,400 kg` → `18.4 ton`)도 원문에 적힌 값이다. 공통 환산표로만
    # 대조한다 — 원문 단위나 추출 단위를 모르면 환산하지 않는다.
    return any(numeric_equal(value, converted)
               for converted in _evidence_amounts_in_unit(evidence, unit))


def _evidence_amounts_in_unit(text: str, unit: str) -> list[float]:
    """근거 문구의 (수, 뒤 단위) 쌍을 `unit`으로 환산한 값. 환산할 수 없는 쌍은 뺀다.

    뒤 단위는 자동 보정과 같은 `_read_unit` 판독을 쓴다. 분모 표지가 있거나 잘린 단위는
    분자 단위로 되돌려 환산하지 않는다(`1만 2천 MJ/(톤)`은 12 GJ가 아니다).
    """
    from ..rag_gates.units import convert_to_common, normalize_unit, parse_number

    target = normalize_unit(unit or "")
    if target is None:
        return []
    pairs: list[tuple[float, str | None]] = []
    for match in _LOOSE_AMOUNT_RE.finditer(text):
        base = parse_number(match.group(2))
        if base is None:
            continue
        if match.group(1) in ("-", "−"):
            base = -base
        if match.group(3):
            base *= _KR_SCALES[match.group(3)]
        pairs.append((base, _trailing_unit(text, match.end())))
    pairs += [(chain.amount, chain.unit) for chain in _kr_scale_chains(text)
              if chain.unit_status == "SIMPLE_COMPLETE"]
    converted = [convert_to_common(amount, source_unit, target)
                 for amount, source_unit in pairs if source_unit is not None]
    return [value for value in converted if value is not None]


def _zero_is_grounded(quote: str, hint: str = "", period: str = "",
                      boundary: dict[str, Any] | None = None) -> bool:
    """보고된 0을 **원문 사실로 보존해도 되는가.** 판정 상태와 사유는 `_zero_verdict`가 돌려준다.

    실측 결함(삼성전기 4건): 원문 칸이 `-`(미공시)인데 값 0으로 실렸다. 자기 근거 문구에
    0이 한 번도 없으면 그 0은 원문에서 온 값이 아니다. 두 문서 지표 7,238건에 이 판정을
    적용했을 때 **실제 0을 잘못 걸러낸 경우는 0건**이었다(0값 354건 중 4건만 걸렸다).

    숫자 0 말고도 **같은 지표의 명시적 미발생·미보유 서술**은 0의 근거다(PR 69 검토,
    2026-09-30: 한울정밀 11번 `ISMS 인증 미보유`의 0건이 지워지고 '수치를 읽지 못함'
    안내가 떴다). True는 요청 범위까지 확인됐다는 뜻이 **아니다** — `_ZeroVerdict.accepted` 참고.
    """
    return _zero_verdict(quote, hint, period, boundary).accepted


# 0 근거의 최종 상태(PR 69 3차 검토). 파싱 성공·원문 사실·요청 범위 일치를 bool 하나로 섞지 않는다.
#   CONFIRMED   같은 지표의 실제 사실이고 요청 범위(기간·사업장)까지 같다.
#   SOURCE_ONLY 원문 사실은 확인했지만 요청 범위의 실적으로 확정할 정보가 없다(보고 연도만 받은
#               요청 ↔ 원문 4월, 원문에 범위 표기 없음). 값은 **원문 범위로** 보존한다.
#   REJECTED    다른 지표·목표·조건·가정·명시적 범위 충돌 — 이 실적의 근거가 아니다.
#   UNRESOLVED  표현 해석이나 근거 연결 자체를 판단하지 못했다.
_ZERO_STATUSES = ("CONFIRMED", "SOURCE_ONLY", "REJECTED", "UNRESOLVED")


@dataclass(frozen=True)
class _ZeroVerdict:
    """0 근거의 판정. 채택하지 않은 0은 사유와 원문 범위를 확인 목록에 넘긴다."""
    status: str                   # `_ZERO_STATUSES`
    cause: str                    # 채택하지 않은 0은 `_ZERO_CAUSES`, 보존한 0은 `_ZERO_KEPT_CAUSES`의 키
    evidence_period: str = ""     # 대조한 원문의 기간 표기
    evidence_site: str = ""       # 대조한 원문의 사업장 표기
    evidence_text: str = ""       # 판정 근거가 된 서술·표 행(원문 그대로)
    evidence_offset: int = -1     # 인용 안에서 후보의 위치. 후보가 없으면 -1
    candidate: str = ""           # numeric(숫자 0) | negation(미발생·미보유 서술)
    requested_scope: str = ""     # 요청 기간의 의미(`_DateSpan.kind`). 읽지 못하면 ""
    source_scope: Any = None      # 원문 범위(`_DateSpan`). 없으면 None
    evidence_column: str = ""     # 후보 셀의 열 머리(표). 목표·실적 칸이 모두 0이면 실적 칸이다
    column_state: str = ""        # `_ZeroCandidate.column_state`
    source_unit: str = ""         # 원문에 적힌 단위 표기(값 뒤·행 라벨·열 머리). 없으면 ""
    unit_location: str = ""       # after_value | label(행 라벨·열 머리 괄호) | ""(원문에 단위 없음)
    scope_from: str = ""          # 기간·사업장을 읽은 곳: statement | column | heading | ""(읽지 않음)
    scope_heading: str = ""       # scope_from=heading이면 그 머리말 원문
    # 판정에 쓴 범위(PR 69 5차 검토). 감사용 `evidence_text`(표는 행 전체)와 따로 둔다.
    evidence_start: int = -1      # 판정한 셀·서술의 인용 안 시작·끝
    evidence_end: int = -1
    row_label: str = ""           # 표 후보의 행 라벨(값이 없는 앞 칸들). 표가 아니면 ""
    scope_boundary: str = ""      # 범위 상속을 끝낸 새 절 머리말 원문. 없으면 ""
    # 범위 판정에 읽은 구간의 역할(§5 C1). (어디서 읽었나, 역할, 원문, 그 문구 안 시작·끝).
    # 역할: period · site · standard_ref(규격 번호·개정 연도 — 기간 아님). 연도처럼 보이는 숫자를
    # 일괄로 가리지 않고 무엇을 무엇으로 읽었는지 남긴다.
    scope_spans: tuple = ()

    @property
    def accepted(self) -> bool:
        """원문 사실로 0을 **보존해도 되는가**(CONFIRMED·SOURCE_ONLY). 요청 범위 확인과는 다르다."""
        return self.status in ("CONFIRMED", "SOURCE_ONLY")

    @property
    def scope_confirmed(self) -> bool:
        """요청 범위의 실적으로 확인됐는가. SOURCE_ONLY는 여기서 False다."""
        return self.status == "CONFIRMED"


# 채택하지 않은 0의 하위 사유. 확인 목록이 사유별로 다르게 설명한다(`source_review`).
_ZERO_CAUSES = frozenset({
    "no_zero_statement",        # 0도, 미발생·미보유 서술도 없다(미공시 `-`·빈 칸 포함)
    "other_subject",            # 후보의 대상이 이 지표가 아니거나 연결을 특정하지 못했다
    "future_or_intent",         # 미래 예상·목표·계획·가능성(`목표 0건` 포함)
    "conditional",              # 조건(`발생하지 않으면`·`0건일 경우`)
    "assumption",               # 가정·전제(`발생하지 않는다고 가정한다`)
    "not_confirmed",            # 미확인·미집계·미공시·해당 없음·기록 없음
    "evidence_insufficient",    # '발생하지 않았다는 증거가 없다'처럼 미발생의 근거가 없다는 말
    "negation_negated",         # '미보유 상태가 아니다'처럼 부정을 다시 부정했다
    "interpretation_unknown",   # 사실 서술인지 판정하지 못했다(인식하지 못한 표현의 기본값)
    "period_mismatch",          # 원문 기간이 지표 기간과 명시적으로 다르다
    "period_unproven",          # 기간이 겹치지만 같은 범위임을 확인하지 못했다
    "period_ambiguous",         # 원문에 기간이 여럿이라 이 0이 어느 기간인지 특정하지 못했다
    "site_mismatch",            # 원문 사업장이 지표 사업장과 다르다
    "unit_incomplete",          # 0 뒤 단위의 분모를 끝까지 읽지 못했다(`0 MJ/`) — 사실성과 별개
    "unit_unknown",             # 0 뒤 단위가 사전 밖이고 모델 단위와도 다르다(`0 tCO2eqx`)
    "column_unresolved",        # 표에 머리글 행은 있으나 이 값의 열을 특정하지 못했다
})
# 판단 보류(UNRESOLVED)로 남기는 사유. 나머지 사유는 REJECTED다.
_ZERO_UNRESOLVED_CAUSES = frozenset({
    "interpretation_unknown", "unit_incomplete", "unit_unknown", "column_unresolved"})
# 보존한 0의 사유. SOURCE_ONLY는 무엇이 확인되지 않았는지를 남긴다.
_ZERO_KEPT_CAUSES = {
    "stated_zero": "",
    "report_year_only": "요청 기간이 보고 연도뿐이라 원문 범위의 사실로만 보존했습니다(연간 실적으로 확정하지 않음).",
    "period_not_stated": "원문에 기간 표기가 없어 요청 기간의 실적인지 확인하지 못했습니다.",
    "requested_period_unknown": "요청 기간을 읽지 못해 원문 범위의 사실로만 보존했습니다.",
    "source_year_unknown": "원문 기간의 연도가 적혀 있지 않아 요청 기간의 실적인지 확인하지 못했습니다.",
    "site_not_stated": "원문에 사업장 표기가 없어 요청 사업장의 실적인지 확인하지 못했습니다.",
    "source_site_only": "원문 사실이 특정 사업장의 것이라 전체 범위의 실적으로 확정하지 않았습니다.",
    # §5 C2: 기간 표기는 있으나 실제 구간을 확정할 수 없다 — '원문에 기간 표기 없음'과 다르다.
    "fiscal_period_undefined": "원문의 회계연도(FY) 표기는 있으나 시작·종료일이 원문에 정의되지 않아 요청 기간의 실적인지 확인하지 못했습니다.",
    "fiscal_year_abbreviated": "원문의 회계연도 약칭(FY26 등)이 가리키는 연도·구간이 원문에 연결되어 있지 않아 요청 기간의 실적인지 확인하지 못했습니다.",
    "quarter_undefined": "원문의 분기 표기(Q1 등)에 연도·회계연도 시작월이 없어 실제 기간을 확정하지 못했습니다.",
    "quarter_months_not_stated": "원문 분기의 해당 월이 적혀 있지 않아(회계연도 시작월 미상) 실제 기간을 확정하지 못했습니다.",
    # PR71 검토 R4: 같은 FY 표기에 서로 다른 원문 정의가 있고 어느 정의가 적용되는지 원문에 구분이 없다.
    "fiscal_definition_conflict": "원문에 같은 회계연도(FY)의 정의가 서로 다른 구간으로 여럿 있고 어느 정의가 적용되는지 원문에 구분되지 않아 요청 기간의 실적인지 확인하지 못했습니다.",
    # PR71 재검토 C: 원문의 FY 정의가 다른 사업장에만 적용된다 — 그 정의를 이 사업장에 빌려 쓰지 않는다.
    "fiscal_definition_not_applicable": "원문의 회계연도(FY) 정의는 다른 사업장에만 적용되어 {site}에 적용되는 FY 구간을 원문에서 확인할 수 없어 요청 기간의 실적인지 확인하지 못했습니다.",
}
# 판정 상태의 우선순위. 후보마다 **자기 문맥으로** 판정한 뒤 가장 강한 것을 고른다.
_ZERO_STATUS_RANK = {"CONFIRMED": 0, "SOURCE_ONLY": 1, "REJECTED": 2, "UNRESOLVED": 3}


@dataclass(frozen=True)
class _ZeroCandidate:
    """0의 근거 후보 하나. 숫자 0과 부정 서술은 **찾는 방법만** 다르고 판정은 같다."""
    kind: str             # numeric | negation
    statement: str        # 후보가 든 서술(절을 연결 어미로 나눈 한 토막)
    start: int            # statement 안 후보의 시작·끝
    end: int
    offset: int           # 인용 안 후보의 위치
    # 후보 **앞**의 절·서술(인용 안 위치, 문구). 범위를 상속할 수 있는 것은 이 가운데 범위만 적힌
    # 공통 머리말뿐이다(`_zero_heading`). 다른 지표의 서술·뒤 절은 넣지 않는다(PR 69 4차 검토).
    preceding: tuple = ()
    column: str = ""      # 후보 셀의 열 머리(`_column_header`). 없으면 ""
    column_state: str = ""  # cell(값에 붙은 구조화 표 머리) | header(같은 표의 머리글 행) |
                            # none(머리글 행 없는 표·표 아님) | unresolved(머리글은 있으나 열을 특정 못 함)
    header_offset: int = -1  # 열 머리의 인용 안 위치. 없으면 -1
    match: Any = None     # 부정 서술의 정규식 결과
    # PR 69 5차 검토: 표 후보의 `statement`는 **행 라벨 + 자기 셀**이다(다른 값 셀의 숫자·연도·
    # 목표 문구를 이 0의 서술·범위로 읽지 않는다). 셀 머리 괄호는 공백으로 가린다 — 그 좌표는
    # `column`으로만 읽는다. 원문 행 전체는 감사용으로 `context`에 둔다.
    context: str = ""     # 감사용 원문(표 행 전체 또는 서술). 비면 statement
    label: str = ""       # 표 행 라벨. 표가 아니면 ""
    span: tuple = ()      # 판정 범위(셀·서술)의 인용 안 시작·끝


def _zero_verdict(quote: str, hint: str = "", period: str = "",
                  boundary: dict[str, Any] | None = None, unit: str = "",
                  definitions: str = "") -> _ZeroVerdict:
    """숫자 0과 부정 서술 후보를 모두 찾고, **같은 검사**(대상 → 사실성 → 사업장·기간)로 판정한다.

    `if 인용에 0이 있음: 채택` 구조를 없앴다(PR 69 3차 검토: `목표 0건`·`4월 1일 기준 0건`이
    4월 실적 0으로 채택됐다). 한 후보의 성공으로 다른 후보를 덮지 않도록 후보마다 자기 서술·
    표 행·열 머리에서 범위를 읽고, 그 서술에 없을 때만 **범위만 적힌 앞 머리말**을 본다(4차 검토:
    `산업재해 0건, 2026년 4월 김해 제1공장 교육 50명`의 교육 날짜·사업장을 빌려 CONFIRMED가 됐다).
    `unit`은 모델이 보고한 단위다 — 사전 밖 원문 단위를 대조할 때만 쓴다(`_zero_unit`).
    `definitions`는 인용 밖 원문(쪽 텍스트)이다 — 회계연도 정의(`FY2026 = …`)를 찾을 때만 쓴다.
    """
    boundary = boundary or {}
    # 사업장이 적힌 회계연도 정의는 그 사업장에만 적용한다(PR71 재검토 C). 대조할 사업장은 요청 사업장이고, 요청에
    # 사업장이 없으면 그 후보 원문(서술·열 머리·머리말)의 사업장이다 — 후보마다 `_zero_scope_verdict`가 고른다.
    # CONFIRMED에는 원문 사업장이 요청 사업장과 같아야 한다(`_site_matches`)는 대조는 이와 따로 한다.
    request_sites = _site_keys(str(boundary.get("site") or ""))
    fiscal_cache: dict[frozenset, dict[str, Any]] = {}

    def fiscal(evidence_sites: set[str]) -> dict[str, Any]:
        key = frozenset(request_sites or evidence_sites or ())
        if key not in fiscal_cache:
            fiscal_cache[key] = _fiscal_definitions(f"{quote}\n{definitions}", set(key))
        return fiscal_cache[key]
    hint_tokens = _label_tokens(hint)
    specific = [t for t in hint_tokens if t not in _ZERO_GENERIC_TOKENS]
    candidates = _zero_candidates(quote)
    if not candidates:
        return _ZeroVerdict("UNRESOLVED", "no_zero_statement")
    allowed = set(hint_tokens) | _ZERO_GENERIC_TOKENS | _ZERO_FILLER_TOKENS
    requested = _requested_period(period, hint)
    verdicts: list[_ZeroVerdict] = []
    for candidate in candidates:
        span = candidate.span or (-1, -1)
        base = {"evidence_text": (candidate.context or candidate.statement).strip(),
                "evidence_offset": candidate.offset,
                "candidate": candidate.kind, "requested_scope": requested.kind if requested else "",
                "evidence_column": candidate.column, "column_state": candidate.column_state,
                "evidence_start": span[0], "evidence_end": span[1], "row_label": candidate.label}
        if candidate.kind == "numeric":
            read = _zero_unit(candidate.statement, candidate.end, unit)
            label_unit = _zero_label_unit(candidate.statement, candidate.start, candidate.column)
            if read.text:
                base.update(source_unit=read.text, unit_location="after_value")
            elif label_unit:
                base.update(source_unit=label_unit, unit_location="label")
        subject = _zero_subject(candidate, specific, hint_tokens, allowed)
        if subject != "same":
            status = "UNRESOLVED" if subject == "unclear" else "REJECTED"
            cause = "future_or_intent" if subject == "target" else "other_subject"
            verdicts.append(_ZeroVerdict(status, cause, **base))
            continue
        if candidate.kind == "negation":
            cause = _negation_mood(candidate.statement, candidate.match)
        else:
            cause = _numeric_zero_mood(candidate.statement, candidate.end, unit)
        if cause is None and _ZERO_INTENT_RE.search(candidate.column):
            cause = "future_or_intent"   # 표의 목표·계획 열에 있는 0
        if cause is None and candidate.column_state == "unresolved":
            cause = "column_unresolved"  # 머리글은 있는데 어느 열인지 모른다 — 목표·실적을 임의로 고르지 않는다
        if cause is not None:
            verdicts.append(_ZeroVerdict(
                "UNRESOLVED" if cause in _ZERO_UNRESOLVED_CAUSES else "REJECTED", cause, **base))
            continue
        verdicts.append(_zero_scope_verdict(candidate, requested, boundary, base, hint_tokens, fiscal))
    # 지표 대상이 맞은 후보의 판정이 대상이 다른 후보의 판정보다 앞선다. 그 안에서는 상태 순위, 같으면 앞 후보.
    # PR 69 6차 검토: 상태 순위를 먼저 봐서, 열을 정하지 못한 이 지표의 0(UNRESOLVED/column_unresolved)
    # 대신 다른 행의 0(REJECTED/other_subject)이 판정 사유로 남았다.
    return min(verdicts, key=lambda v: (v.cause in ("other_subject", "no_zero_statement"),
                                        _ZERO_STATUS_RANK[v.status]))


# 숫자 0 후보. 숫자 경계를 지킨다 — 날짜·항목 번호·`1,000`·`0.5` 속 0은 후보가 아니다.
_ZERO_NUMERIC_RE = re.compile(r"(?<![\d.,\-/])0(?:\.0+)?(?![\d,]|\.\d|[-./]\d)")
# 실제로 없었다·갖고 있지 않다는 서술의 **어근**. 사실인지는 뒤 어미로 따로 판정한다.
_ZERO_NEGATION_RE = re.compile(
    r"미발생|미보유|미취득|(?:발생|보유|취득)(?:하지|되지)\s*않|없(?=[었음다으습고을는도게기])")
# 목표·계획 표현. 부정 서술 **앞이나 그 어절 안에** 있으면 완료된 사실이 아니다.
_ZERO_INTENT_RE = re.compile(r"않도록|없도록|목표|계획|예정|예상|전망")
# 0이 아니라 **모른다**·**해당 없다**는 서술. 부정 서술을 포함한 서술 안 어디에 있어도 막는다.
_ZERO_UNCONFIRMED_RE = re.compile(
    r"해당\s*(?:사항\s*)?없|자료\s*없|기록\s*없|정보\s*없"
    r"|확인(?:하지|되지|할\s*수)\s*(?:않|없)|미확인|미집계|집계(?:하지|되지)\s*않|미공시"
    r"|공시(?:하지|되지)\s*않|파악(?:하지|되지)\s*않|알\s*수\s*없")
# 미발생의 **근거가 없다**는 서술. `없다`의 대상이 산업재해가 아니라 증거다.
_ZERO_NO_PROOF_RE = re.compile(
    r"(?:증거|근거|입증|증빙)\S*\s*(?:없|부족)|(?:단정|판단|보기)\S*\s*(?:어렵|힘들|할\s*수\s*없)")
# 조건 어미(`않으면`·`없다면`·`않더라도`)와 조건 명사(`않을 경우`·`0건일 때`).
_ZERO_CONDITION_TAIL_RE = re.compile(r"(?:았|었)?(?:으면|면|다면|는다면|라면|더라도|어도|아도)")
_ZERO_CONDITION_NOUN_RE = re.compile(r"\s*(?:경우|때|시)(?![가-힣])")
# 가정·전제. 부정을 안은 뒤 서술어가 이것이면 사실이 아니다(`않는다고 가정한다`).
_ZERO_ASSUMPTION_RE = re.compile(r"가정|전제|간주|상정|추정|가상")
# 부정 어근 뒤 어미. 미래·목표(`않을`·`않도록`·`않기를`·`않겠`)는 완료된 미발생이 아니다.
_ZERO_FUTURE_TAIL_RE = re.compile(r"(?:았|었)?(?:도록|게|기를|기로|고자|으려|려고|려는|겠|을)")
# 인용·명사절로 안긴 부정(`않았다는`·`않았음을`·`않았는지`). 참·거짓은 뒤 서술어가 정한다.
_ZERO_EMBED_TAIL_RE = re.compile(r"(?:았|었)?(?:다는|다고|는지|은지|음을|음이|음은|음도|음에)")
# 관형형(`않은`·`없는`). 뒤 명사에 걸리므로 그 자체로는 사실 서술이 아니다.
_ZERO_ADNOMINAL_TAIL_RE = re.compile(r"(?:았|었)?(?:은|는|던)")
_ZERO_CONFIRMED_RE = re.compile(r"확인(?:했|하였|됐|되었|됨|함|한다|된다)|검증(?:했|하였|됐|되었|됨)")
_ZERO_DOUBT_RE = re.compile(r"없|않|어렵|불확실|모르|못|불명")
# **사실로 인정하는 어미**(명시적 목록). 이 밖의 어미는 사실로 보지 않는다(`interpretation_unknown`).
#   동사 부정(`않`·`없`): 과거 `았다`·`었습니다`·`았음`·`았으며`·`았고`·`았으나`, 현재 `는다`·`다`·`음`.
#   `않고`(현재)는 `않고 있다`·`않고 관리한다`가 갈리므로 넣지 않는다 — `없고`만 사실이다.
_ZERO_VERB_FACT_TAIL_RE = re.compile(
    r"(?:았|었)(?:다|습니다|음|으며|고|으나|지만|는데|으므로)?"
    r"|(?:는다|습니다|음|으며|으나|지만|는데|다)")
_ZERO_EUPSEO_FACT_TAIL_RE = re.compile(r"고")
#   명사 부정(`미보유`·`미발생`·`미취득`): 그대로 끝나거나 `이다`·`이며`·`임`·`상태이다`.
_ZERO_NOUN_FACT_TAIL_RE = re.compile(
    r"(?:\s*상태)?(?:이다|이며|이고|입니다|이었다|였다|이었으며|이었고|임|이었음|이었습니다)?")
# 사실 서술 뒤에 와도 되는 것: 문장부호, 날짜·기준일 괄호, 숫자·날짜만 든 표 칸.
_ZERO_TRAILING_NOISE_RE = re.compile(
    r"(?:\s*(?:\|[\s\d.,\-~/년월일%]*|[(（][\s\d.,\-~/년월일]*(?:기준|현재)?\s*[)）]))*[\s.。!]*")
# 숫자 0의 단위(`_zero_unit`) 뒤: 조사·서술어(`0건이다`·`0건으로 집계됐다`)와 숫자만 든 표 칸.
# 단위는 여기서 다시 정의하지 않는다(PR 69 4차 검토).
_ZERO_NUMERIC_FACT_TAIL_RE = re.compile(
    r"\s*(?:이다|입니다|이었다|였다|이며|이고|임|으로|로|을|를|이|가|은|는|의|이었으며)?"
    r"(?:\s*(?:집계|확인|기록|발생|나타|보고)(?:됐다|되었다|됨|했다|하였다|했음|됐음|났다|되었음|하였음)?)?"
    r"(?:\s*\|[\s\d.,\-~/%]*)*[\s.。!]*")
# 한 절 안의 서술 경계. 연결 어미(`미보유이며`·`않았고`·`있으나`) 뒤는 다른 서술이다 —
# `미보유이며 향후 취득을 계획`의 `계획`은 현재 미보유에 걸리지 않는다.
# 인용 어미(`않았다고`·`않았다며`)는 경계가 아니다 — 뒤 서술어가 그 부정의 참·거짓을 정한다.
# `고`는 용언 어미 뒤에서만 경계다 — 명사 끝의 `고`(`환경 사고 0건`·`재고`)에서 자르면 주어를 잃는다.
_ZERO_STATEMENT_SPLIT_RE = re.compile(
    r"(?:(?<![다라]며)(?<=며)|(?<=[았었했됐않없였하되이]고)|(?<=으나)|(?<=지만)|(?<=는데)|(?<=면서))\s+")
# 절 경계. 자릿수 쉼표(`1,000`)와 소수점은 경계가 아니다.
_ZERO_CLAUSE_SPLIT_RE = re.compile(r"(?<!\d),|,(?!\d)|[;\n]|(?<!\d)\.(?!\d)")
# 지표명에서 무엇을 셌는지 알려 주지 않는 낱말. 이것만 겹쳐서는 같은 지표라 할 수 없다
# (`환경 사고는 발생하지 않았다`가 `산업재해 발생 건수`의 0이 되면 안 된다).
_ZERO_GENERIC_TOKENS = frozenset({
    "발생", "건수", "여부", "상태", "현황", "보유", "횟수", "실적", "누적", "전체", "연간",
    "기준", "인원", "수준", "비율", "총계", "합계"})
# 지표 낱말과 부정 서술 사이에 와도 되는 말(`산업재해가 한 건도 발생하지 않았다`).
_ZERO_FILLER_TOKENS = frozenset({"전혀", "건도", "일체", "모두", "기간", "동안", "현재", "당해"})


def _pieces(text: str, pattern: re.Pattern[str], base: int = 0) -> list[tuple[int, str]]:
    """구분자로 나눈 토막과 그 시작 위치(`base` 기준)."""
    pieces, cursor = [], 0
    for m in pattern.finditer(text):
        pieces.append((base + cursor, text[cursor:m.start()]))
        cursor = m.end()
    pieces.append((base + cursor, text[cursor:]))
    return pieces


def _zero_candidates(quote: str) -> list[_ZeroCandidate]:
    """인용의 숫자 0과 부정 서술을 **위치를 가진 후보**로 찾는다(판정은 하지 않는다).

    표 행(`_table_cells`로 칸이 둘 이상인 줄)의 후보는 **자기 셀**과 행 라벨만 판정 범위로 삼는다
    (PR 69 5차 검토: `산업재해율(‰) | 1(합계|2025) | 0(합계|2026)`의 0이 행 전체의 2025·2026 때문에
    `period_ambiguous`, 뒤 셀 `1(합계|2025)` 때문에 `interpretation_unknown`이 됐다). 표가 아닌 줄은
    절·서술로 나눈다. 앞 문맥(`preceding`)에는 표 행을 한 토막으로 넣는다.
    """
    numeric = [m.start() for m in _ZERO_NUMERIC_RE.finditer(quote)]
    candidates: list[_ZeroCandidate] = []
    earlier: list[tuple[int, str]] = []      # 지금 후보보다 앞의 절·서술·표 행

    def add(statement: str, statement_start: int, span: tuple[int, int], context: str, label: str,
            search: tuple[int, int]) -> None:
        # `search`: statement 안에서 후보를 찾을 구간(표는 자기 셀). 위치는 statement 기준이다.
        lo, hi = search
        found = [("numeric", p - statement_start, None) for p in numeric
                 if statement_start + lo <= p < statement_start + hi]
        found += [("negation", m.start(), m) for m in _ZERO_NEGATION_RE.finditer(statement, lo, hi)]
        for kind, start, match in sorted(found, key=lambda f: f[1]):
            end = match.end() if match else start + 1
            if kind == "numeric":
                zero = _ZERO_NUMERIC_RE.match(statement, start)
                end = zero.end() if zero else end
            offset = statement_start + start
            column, state, header_offset = _column_header(quote, offset, end - start)
            view = statement
            annotation = _cell_annotation(statement, end) if state == "cell" else None
            if annotation:
                # 셀 머리(`0(합계|2026)`)는 값의 좌표다 — 범위는 `column`으로만 읽고, 단위·서술은
                # 그 뒤부터 읽는다. 서술의 기간·사업장으로 다시 읽지 않도록 공백으로 가린다.
                close = annotation[2]
                view = statement[:end] + " " * (close - end) + statement[close:]
                end = close
            candidates.append(_ZeroCandidate(kind, view, start, end, offset, tuple(earlier), column,
                                             state, header_offset, match, context, label, span))

    for line_start, line in _pieces(quote, re.compile(r"\n")):
        cells = _table_cells(line)
        if _is_table_line(line):
            # 행 라벨: 첫 칸과, 그 뒤로 값(숫자·부정 서술·`-`·빈 칸)이 나오기 전까지의 칸(단위 칸 등).
            lead = 1
            while lead < len(cells) and not _is_value_cell(line[cells[lead][0]:cells[lead][1]]):
                lead += 1
            label = " | ".join(line[s:e].strip() for s, e in cells[:lead])
            for k, (s, e) in enumerate(cells):
                if k == 0:
                    statement, prefix = line[s:e], ""
                else:
                    prefix = " | ".join(line[a:b] for a, b in cells[:min(k, lead)]) + " | "
                    statement = prefix + line[s:e]
                # statement 위치 + cell_base = 인용 위치(자기 셀 부분만 원문과 같은 위치에 있다).
                cell_base = line_start + s - len(prefix)
                add(statement, cell_base, (line_start + s, line_start + e), line, label,
                    (len(prefix), len(statement)))
            earlier.append((line_start, line))
            continue
        for clause_start, clause in _pieces(line, _ZERO_CLAUSE_SPLIT_RE, line_start):
            for statement_start, statement in _pieces(clause, _ZERO_STATEMENT_SPLIT_RE, clause_start):
                add(statement, statement_start, (statement_start, statement_start + len(statement)),
                    statement, "", (0, len(statement)))
                earlier.append((statement_start, statement))
    return candidates


def _split_cells(line: str) -> list[tuple[int, int]]:
    """`|`로 나눈 칸(줄 안 시작·끝). 짝이 맞는 괄호 안의 `|`는 칸 구분자가 아니다.

    `_attach_column_headers`는 값 뒤에 `(합계|2026)`·`(국내(별도)|2022)`처럼 `|`가 든 셀 머리를
    붙인다. 단순 `split('|')`은 이 좌표를 칸으로 쪼갠다(PR 69 5차 검토). 짝 없는 괄호(`4)` 각주)는
    무시한다.
    """
    opens, inside = [], [0] * (len(line) + 1)
    for i, ch in enumerate(line):
        if ch in "(（":
            opens.append(i)
        elif ch in ")）" and opens:
            inside[opens.pop() + 1] += 1
            inside[i] -= 1
    cells, depth, start = [], 0, 0
    for i, ch in enumerate(line):
        depth += inside[i]
        if ch == "|" and depth == 0:
            cells.append((start, i))
            start = i + 1
    cells.append((start, len(line)))
    return cells


def _is_table_line(line: str) -> bool:
    """표 행인가: 괄호 밖 `|`가 있다(바깥 테두리만 있는 한 칸 행 `| 비고 |`도 표 행이다)."""
    return len(_split_cells(line)) > 1


def _table_cells(line: str) -> list[tuple[int, int]]:
    """표 행의 **내용 칸**(줄 안 시작·끝). 바깥 테두리(`| 항목 | 2026 |`의 양끝 `|`)는 칸이 아니다.

    PR 69 6차 검토: 테두리 앞의 빈 칸을 첫 칸으로 읽어 행 라벨을 잃었다(`| Scope 3 배출량(tCO2eq) |
    0 |`의 0이 `other_subject`로 삭제). 줄이 `|`로 시작하고 `|`로 끝날 때만 양끝 한 칸씩을 테두리로
    본다. 그 안의 빈 칸(`| | 2025 | 2026 |`의 모서리, 값이 빈 칸)은 실제 칸이라 그대로 둔다 — 빈 칸을
    모두 지우면 뒤 칸의 열 번호가 밀린다. 열 연결(`_column_header`)과 후보 생성(`_zero_candidates`)이
    이 같은 칸 좌표를 쓴다.
    """
    cells = _split_cells(line)
    stripped = line.strip()
    if len(cells) > 2 and stripped.startswith("|") and stripped.endswith("|"):
        cells = cells[1:-1]
    return cells


def _cell_annotation(text: str, pos: int) -> tuple[int, int, int] | None:
    """`pos` 바로 뒤 셀 머리 괄호의 (안쪽 시작, 안쪽 끝, 닫는 괄호 다음). 안쪽 괄호를 허용한다."""
    m = re.compile(r"\s*[(（]").match(text, pos)
    if not m:
        return None
    depth = 1
    for j in range(m.end(), len(text)):
        if text[j] in "(（":
            depth += 1
        elif text[j] in ")）":
            depth -= 1
            if depth == 0:
                return m.end(), j, j + 1
    return None


# 데이터 칸: 수량으로 시작하거나(`50`·`46명`·`12.4`) 미공시 표기(`-`). 연도 머리(`2026`)는 아니다.
_TABLE_VALUE_CELL_RE = re.compile(r"\s*(?:[-+]?\d[\d.,]*|[-–—]\s*$)")
# 열의 역할·연도·축을 가리키는 머리글 낱말. 표 중간의 행이 머리글로 인정받으려면 값 칸 모두가
# 이것(또는 연도)이어야 한다 — `완료 | 완료` 같은 문자형 값은 머리글이 아니다(PR 69 5차 검토).
_TABLE_AXIS_RE = re.compile(
    r"목표|실적|계획|전망|예상|추정|구분|항목|단위|비고|소계|누계|총계|전년|당해|전기|당기|상반기|하반기"
    r"|분기|연도|년도|FY\s*\d{2,4}|" + "|".join(_COL_HEADER_KEYWORDS))


# 칸 안의 수: 낱말에 붙은 숫자(`CO2`·`PM2.5`·`제1공장`)는 수량이 아니라 이름의 일부다.
_CELL_NUMBER_RE = re.compile(r"(?<![A-Za-z가-힣\d.,])[-+]?\d[\d.,]*")


def _is_identifier_cell(text: str) -> bool:
    """사업장 식별자 칸인가(`1공장`·`제2공장`·`김해 제1공장`·`서아산공장`·`본사`). 앞의 숫자는 번호다."""
    return bool(re.fullmatch(rf"\s*(?:{_SITE_MENTION_RE.pattern}|{_SITE_KINDS})\s*", text))


def _is_quantity_cell(text: str) -> bool:
    """수량으로 시작하는 칸인가(`50`·`46명`·`0(합계|2026)`·`-`). 사업장 번호(`1공장`)는 아니다."""
    return bool(_TABLE_VALUE_CELL_RE.match(text)) and not _is_identifier_cell(text)


def _is_value_cell(text: str) -> bool:
    """값이 든 칸인가: 빈 칸·수량·`-`·미발생/미보유 서술, 또는 칸 안에 단독 0이나 단위가 붙은 수
    (`누계 0`·`약 3건`). 행 라벨은 이런 칸 앞까지다.

    PR 69 6차 검토: 숫자가 있기만 하면 값 칸으로 봐서 `Scope 3 배출량(tCO2eq)`이 값 칸이 되고 행
    라벨이 사라졌다. 지표명·식별자 안의 숫자(`Scope 3`·`CO2`·`1공장`)는 값이 아니다.
    """
    from ..rag_gates.units import normalize_unit
    if not text.strip() or _is_quantity_cell(text) or _ZERO_NEGATION_RE.search(text):
        return True
    for m in _CELL_NUMBER_RE.finditer(text):
        if _ZERO_NUMERIC_RE.fullmatch(m.group().lstrip("+-")):
            return True
        unit = re.match(r"\s*([^\s()（）|,]+)", text[m.end():])
        if unit and normalize_unit(unit.group(1)):
            return True
    return False


def _is_table_header_row(cells: list[str]) -> bool:
    """머리글 **후보** 행인가: 첫 칸(행 제목 자리) 뒤 칸이 모두 비었거나 이름·연도이고, 하나 이상 채워졌다.

    숫자 데이터 행(`교육 참여 인원 | 0 | 50`)은 칸 수가 같아도 머리글이 아니다. 미발생·미보유
    서술이 든 행도 사실을 적은 데이터 행이다. 숫자가 없다는 것만으로 머리글이 되지는 않는다 —
    `_column_header`가 열 역할 낱말이나 표 구조(첫 행·구분선)로 확인한다.
    """
    rest = [c.strip() for c in cells[1:]]
    if not any(rest) or _ZERO_NEGATION_RE.search("|".join(cells)):
        return False
    return all(not c or _YEAR_HEADER_RE.fullmatch(c) or not _is_quantity_cell(c) for c in rest)


# 사업장 축 머리글의 첫 칸(행 제목 자리): 열이 사업장별임을 밝히는 축 낱말.
_ROW_AXIS_RE = re.compile(r"구분|항목|지표|분류|내용|사업장")
# 축 이름 칸 = 행 제목 열의 축 낱말(뒤에 `명`·`별`)만 이어진 칸. 낱말 사이 공백·`/`·`·`과 단위 괄호는
# 뗀다. 열 역할(`목표`·`실적`)과 합계(`소계`·`합계`)는 첫 칸에 오면 데이터 행의 라벨이라 넣지 않는다.
_AXIS_NAME_RE = re.compile(
    rf"(?:(?:{_ROW_AXIS_RE.pattern}|공장|부문|연도|년도|기간|단위|비고|FY\s*\d{{2,4}})(?:명|별)?)+")
# 짝이 맞는 가장 안쪽 괄호 하나(반각·전각).
_INNER_PAREN_RE = re.compile(r"[(（]([^()（）]*)[)）]")


def _is_axis_annotation(inner: str) -> bool:
    """괄호 안 **전체**가 축 칸의 표기 주석인가: 기존 판독기가 읽는 단위(`단위: 건`·`%`·`단위：명, %`),
    기간(`2026년`·`2026년 4월`), 열 머리 낱말(`국내`·`연결`·`합계`)로만 됐다. 설명이 한 낱말이라도
    섞이면(`교육 대상`·`단위: 건, 교육 대상`) 무엇을 기록했는지 밝히는 데이터 라벨이다."""
    from ..rag_gates.units import normalize_unit

    def known(tok: str) -> bool:
        return (normalize_unit(tok) is not None or tok in _COL_HEADER_KEYWORDS
                or any(span.text == tok for span in _date_spans(tok)))

    body = re.sub(r"^\s*단위\s*[:：]?\s*", "", inner).strip()
    if not body:
        return False
    return known(body) or all(tok and known(tok) for tok in re.split(r"\s*[,/·|]\s*|\s+", body))


def _is_axis_name(text: str) -> bool:
    """칸 **전체**가 축 이름인가(`구분`·`사업장`·`사업장명`·`구분/연도`·`항목(단위: 건)`).

    PR 69 7차 검토: 축 낱말을 **포함**하기만 하면(`.search`) 축 이름으로 봐서 `교육 대상 사업장 |
    김해 제1공장 | 부산 제1공장`(목표·실적 열마다 사업장을 적은 데이터 행)이 새 머리글이 됐고,
    아래 `산업재해 | 0 | 1`의 목표 0이 실적이 됐다. 무엇을 기록했는지 밝히는 낱말(`교육 대상`·
    `집계`·`소속`)이 붙은 칸은 축 이름이 아니라 데이터 행의 라벨이다.
    8차 검토: 괄호를 모두 지워 `사업장(교육 대상)`이 축 이름 `사업장`이 됐다. 괄호는 안쪽부터,
    내용 전체가 단위·기간·열 머리 표기일 때만 뗀다(`_is_axis_annotation`). 설명 괄호·짝이 맞지 않는
    괄호는 그대로 남아 축 이름이 아니다.
    """
    body = text
    while True:
        groups = list(_INNER_PAREN_RE.finditer(body))
        if not groups:
            break
        if not all(_is_axis_annotation(m.group(1)) for m in groups):
            return False                   # 무엇을 기록했는지 밝히는 설명 — 데이터 행의 라벨
        body = _INNER_PAREN_RE.sub("", body)
    return bool(_AXIS_NAME_RE.fullmatch(re.sub(r"[\s/·,\\]+", "", body)))


def _names_columns(cells: list[str]) -> bool:
    """표 **중간**의 행이 새 머리글인가: 첫 칸(행 제목 자리)이 비었거나 축 이름이고(`_is_axis_name`),
    값 칸이

    - 모두 열 역할·연도·축 낱말(`목표 | 실적`·`2025 | 2026`·`합계 | 국내`), 또는
    - 모두 **서로 다른** 사업장 식별자(`구분 | 1공장 | 2공장`·`구분 | 김해 제1공장 | 부산 제1공장`)다.
      PR 69 6차 검토: `1공장`을 수량으로 읽어 표 중간의 새 머리글을 놓쳤고, 이전 `목표 | 실적` 역할이
      남아 정상 0이 지워졌다.

    첫 칸이 데이터 라벨인 행(`교육 대상 사업장 | 김해 제1공장 | 부산 제1공장`·`점검 상태 | 완료 |
    완료`·`소속 | 김해공장 | 김해공장`)은 값이 사업장·축 낱말이어도 열마다 무엇을 기록했는지 적은
    데이터 행이다 — 확인된 머리글의 열 역할은 그 행을 지나도 유지된다(PR 69 7차 검토). 표의 첫 행과
    구분선 바로 위 행은 `_column_header`가 구조로 따로 확인한다.
    """
    rest = [c.strip() for c in cells[1:] if c.strip()]
    first = cells[0].strip()
    if not rest or (first and not _is_axis_name(first)):
        return False
    if all(_YEAR_HEADER_RE.fullmatch(c) or _TABLE_AXIS_RE.search(c) for c in rest):
        return True
    return len(set(rest)) == len(rest) and all(_is_identifier_cell(c) for c in rest)


def _column_header(quote: str, offset: int, length: int = 1) -> tuple[str, str, int]:
    """후보 셀의 열 머리, 연결 상태, 머리의 인용 안 위치.

    PR 69 4차 검토(2026-10-02): 칸 수가 같은 가장 가까운 앞 행을 머리글로 써서 `교육 참여 인원 |
    0 | 50`의 `0`을 산업재해 행의 열 머리로 읽었다(목표 0이 실적이 됨). 연도 표에서는 사이에 낀
    데이터 행 때문에 연도 열을 잃었다. 5차 검토: 숫자가 없는 문자형 데이터 행(`점검 상태 | 완료 |
    완료`)이 머리글로 인정돼 실제 `목표 | 실적` 머리글을 덮었다. 이제
      1. 값에 셀 머리가 붙어 있으면(구조화 표의 셀 좌표) 그것을 쓴다 — `cell`.
      2. 평문 표는 **같은 표**(칸이 둘 이상인 줄이 이어진 구간) 안에서 머리글로 확인된 행만 본다.
         머리글 확인: 값이 없는 행(`_is_table_header_row`)이면서 (a) 값 칸이 모두 열 역할·연도·축
         낱말이거나 (b) 표의 첫 행이거나 (c) 바로 아래가 구분선(`---`)이다. 그 밖의 행은 데이터 행이라
         건너뛴다. 칸 수가 같은 가장 가까운 머리글 행과 그 바로 위의 머리글 행들(여러 줄 머리글)을
         열마다 이어 붙인다 — `header`.
      3. 같은 표에 머리글 행이 없으면 열 정보 없음 — `none`(머리글이 없다는 이유로 값을 지우지 않는다).
      4. 머리글 행은 있으나 칸 수가 맞는 것이 없으면 `unresolved` — 열을 임의로 고르지 않는다.
    칸은 `_table_cells`로 나눈다(셀 머리 괄호 안 `|`는 칸 구분자가 아니다).
    """
    cell = _cell_annotation(quote, offset + length)
    if cell:
        inner = quote[cell[0]:cell[1]]
        if all(_YEAR_HEADER_RE.fullmatch(part.strip()) or any(k in part for k in _COL_HEADER_KEYWORDS)
               or re.search(r"목표|실적|계획|전망", part) for part in inner.split("|")):
            return inner.strip(), "cell", cell[0]
    line_start = quote.rfind("\n", 0, offset) + 1
    line_end = quote.find("\n", offset)
    line = quote[line_start:line_end if line_end >= 0 else len(quote)]
    if not _is_table_line(line):
        return "", "none", -1
    line_cells = _table_cells(line)
    index = next((k for k, (s, e) in enumerate(line_cells) if offset - line_start <= e), None)
    if index is None:
        return "", "none", -1
    width = len(line_cells)
    # 같은 표의 앞 행(가까운 것부터): (시작, 칸 문구, 칸 위치). 칸은 바깥 테두리를 뺀 내용 칸이다.
    rows: list[tuple[int, list[str], list[tuple[int, int]]]] = []
    cursor = line_start
    while cursor > 0:
        start = quote.rfind("\n", 0, cursor - 1) + 1
        row = quote[start:cursor - 1]
        if not _is_table_line(row):
            break                          # 표가 끝났다 — 다른 표·본문의 행은 보지 않는다
        bounds = _table_cells(row)
        rows.append((start, [row[s:e] for s, e in bounds], bounds))
        cursor = start

    def rule(k: int) -> bool:
        return re.fullmatch(r"[\s:\-–—]*", "".join(rows[k][1])) is not None

    def header(k: int) -> bool:
        cells = rows[k][1]
        if rule(k) or not _is_table_header_row(cells):
            return False
        if _names_columns(cells):
            return True
        top = all(rule(j) for j in range(k + 1, len(rows)))     # 표의 첫 행(위로는 구분선뿐)
        return top or (k > 0 and rule(k - 1))                    # 또는 바로 아래가 구분선

    headers = [k for k in range(len(rows)) if header(k)]
    if not headers:
        return "", "none", -1
    # 가장 가까운 확인된 머리글이 이 행의 머리글이다 — 확인된 새 머리글은 이전 머리글의 적용 구간을
    # 끝낸다(PR 69 6차 검토). 칸 수가 맞지 않으면 더 앞의 머리글(이전 표의 목표·실적)을 고르지 않는다.
    nearest = headers[0]
    if len(rows[nearest][1]) != width:
        return "", "unresolved", -1
    # 여러 줄 머리글: 가장 가까운 머리글 행 바로 위로 이어진 같은 칸 수의 머리글 행.
    stack = [nearest]
    for k in range(nearest + 1, len(rows)):
        if rule(k):
            continue
        if len(rows[k][1]) != width or not header(k):
            break
        stack.append(k)
    parts = [rows[k][1][index].strip() for k in reversed(stack)]
    text = " ".join(p for p in parts if p)
    head_start, head_cells, head_bounds = rows[nearest]
    cell = head_cells[index]
    return text, "header", head_start + head_bounds[index][0] + len(cell) - len(cell.lstrip())


def _zero_subject(candidate: _ZeroCandidate, specific: list[str], hint_tokens: list[str],
                  allowed: set[str]) -> str:
    """후보 앞말이 이 지표를 가리키는가: same / target(같은 지표의 목표) / different / unclear."""
    head = candidate.statement[:candidate.start]
    if specific and _names_zero_subject(head, specific, hint_tokens, allowed):
        return "same"
    words = _label_tokens(head)
    # 표 행 라벨이 지표명의 일부인 경우(`지하수 | 0` ↔ `지하수 취수량`): 앞말의 낱말이 모두
    # 지표명 안에 있고 수량 종류가 같으면 같은 지표다. 일반 낱말만 겹치는 앞말은 인정하지 않는다.
    if (candidate.column or "|" in head) and words:
        hint_compact = "".join(hint_tokens)
        if (any(w not in _ZERO_GENERIC_TOKENS for w in words)
                and all(w in hint_compact for w in words)
                and _metric_kinds(words) == _metric_kinds(hint_tokens)):
            return "same"
    if not specific or not words:
        return "unclear"   # 지표명이 모두 일반 낱말이거나 앞말이 없다 — 연결을 특정할 수 없다
    compact = re.sub(r"\s+", "", head)
    if all(token in compact for token in specific):
        last = max(head.rfind(token) for token in specific)
        gap = _label_tokens(re.sub(r"^\S*", "", head[last:])) if last >= 0 else []
        if "target" in _metric_kinds(gap) and "target" not in _metric_kinds(hint_tokens):
            return "target"   # `산업재해 목표 0건` — 같은 지표의 목표값이지 실적이 아니다
    return "different"


def _names_zero_subject(head: str, specific: list[str], hint_tokens: list[str],
                        allowed: set[str]) -> bool:
    """부정 서술 앞말이 이 지표를 주어로 삼는가."""
    compact = re.sub(r"\s+", "", head)
    if not all(token in compact for token in specific):
        return False
    last = max(head.rfind(token) for token in specific)
    if last < 0:
        return False   # 공백을 사이에 두고 나뉜 지표 낱말 — 위치를 특정할 수 없어 인정하지 않는다
    gap = re.sub(r"^\S*", "", head[last:])   # 지표 낱말이 든 어절의 나머지(조사)는 건너뛴다
    gap = _drop_unit_cells(gap)               # 표의 단위 칸(`Scope 3 배출량 | tCO2eq | 0`)은 대상이 아니다
    # 날짜·한 글자 말(`4월`·`한`)은 `_label_tokens`가 이미 뺀다.
    return not any(word not in allowed and not any(token in word for token in hint_tokens)
                   for word in _label_tokens(gap))


def _negation_mood(statement: str, negation: re.Match[str]) -> str | None:
    """부정 서술이 완료된 사실·현재 상태면 None, 아니면 채택하지 않는 사유.

    **None은 명시적 사실 분기에서만 돌려준다.** 인식하지 못한 어미·문장 구조는 마지막 기본값
    `interpretation_unknown`으로 떨어진다(PR 69 3차 검토: 거부 목록에 없던 `발생하지 않으면`·
    `발생하지 않는다고 가정한다`가 기본값으로 사실이 됐다).
    """
    tail_end = negation.end()
    while tail_end < len(statement) and not statement[tail_end].isspace():
        tail_end += 1
    tail = statement[negation.end():tail_end]          # 부정 어근 뒤 같은 어절의 어미
    core = tail.rstrip(".。!)]\"'”’")
    before, rest = statement[:tail_end], statement[tail_end:]
    if _ZERO_UNCONFIRMED_RE.search(statement):
        return "not_confirmed"
    if _ZERO_NO_PROOF_RE.search(rest):
        return "evidence_insufficient"
    if (_ZERO_CONDITION_TAIL_RE.fullmatch(core)
            or (core in ("을", "는", "은", "던") and _ZERO_CONDITION_NOUN_RE.match(rest))):
        return "conditional"
    if _ZERO_ASSUMPTION_RE.search(rest):
        return "assumption"
    if _ZERO_INTENT_RE.search(before) or _ZERO_FUTURE_TAIL_RE.match(tail):
        return "future_or_intent"
    if _ZERO_EMBED_TAIL_RE.match(tail):
        # `않았음을 확인했다`는 사실, `않았다는 것은 아니다`·`않았다고 보기 어렵다`는 아니다.
        if "아니" in rest:
            return "negation_negated"
        if _ZERO_DOUBT_RE.search(rest):
            return "evidence_insufficient"
        return None if _ZERO_CONFIRMED_RE.search(rest) else "interpretation_unknown"
    if "아니" in tail + rest:
        return "negation_negated"
    if _ZERO_ADNOMINAL_TAIL_RE.fullmatch(tail):
        # `발생하지 않은 것으로 확인됐다`는 사실, `발생하지 않은 사업장`은 이 지표의 0이 아니다.
        confirmed = _ZERO_CONFIRMED_RE.search(rest)
        return None if confirmed and not _ZERO_DOUBT_RE.search(rest[confirmed.end():]) \
            else "interpretation_unknown"
    # ── 명시적 사실 분기 ──
    finished = _ZERO_TRAILING_NOISE_RE.fullmatch(tail[len(core):] + rest) is not None
    word = negation.group(0)
    if word.startswith("미"):
        if finished and _ZERO_NOUN_FACT_TAIL_RE.fullmatch(core):
            return None
        if not core and _ZERO_NOUN_FACT_TAIL_RE.match(rest) and _ZERO_TRAILING_NOISE_RE.fullmatch(
                rest[_ZERO_NOUN_FACT_TAIL_RE.match(rest).end():]):
            return None   # `미보유 상태이다`
    elif finished and (_ZERO_VERB_FACT_TAIL_RE.fullmatch(core)
                       or (word == "없" and _ZERO_EUPSEO_FACT_TAIL_RE.fullmatch(core))):
        return None
    return "interpretation_unknown"


@dataclass(frozen=True)
class _ZeroUnit:
    """숫자 0 바로 뒤 단위를 읽은 결과. `tail`부터가 조사·서술어다(단위가 소비한 범위 뒤)."""
    status: str      # read(사전 단위) | reported(사전 밖이지만 원문 표기 = 모델 단위) | compound |
                     # absent(값 뒤 단위 없음) | incomplete(`/` 뒤를 못 읽음) | unknown(사전 밖)
    text: str        # 원문 단위 표기. 없으면 ""
    tail: int        # statement 안 조사·서술어의 시작


# 단위 없이 숫자 0에 바로 붙는 조사·서술어의 첫머리(`0으로`·`0이다`·`0인 경우`).
_ZERO_UNITLESS_TAIL_RE = re.compile(r"(?:으로|로|이|가|은|는|을|를|의|일|인|임|입니|였)")


def _zero_unit(statement: str, end: int, reported_unit: str = "") -> _ZeroUnit:
    """숫자 0 뒤 단위를 `_read_unit`·단위 사전(`normalize_unit`)으로 읽는다. 0 전용 단위표는 두지 않는다.

    PR 69 4차 검토(2026-10-02): `[A-Za-z%]+\\d*` 또는 한글 1~2글자로 단위를 다시 정의해 사전이
    지원하는 `0 tCO2eq`·`0 백만원`이 미분류 문장으로 지워졌다.
    - 분모 표지가 있으면 F1 판독 그대로다 — 끝까지 읽으면 compound, 못 읽으면 incomplete.
    - 단순 단위는 낱말의 **가장 긴 사전 단위 앞머리**를 단위로, 나머지 한글을 조사·어미로 본다
      (`건이었으며` → `건` + `이었으며`). 나머지에 영문·숫자·기호가 붙으면(`tCO2eqx`) 단위 표기의
      일부이므로 사전 밖 단위다 — 알려진 앞머리만 떼어 채택하지 않는다.
    - 사전 밖 한글 단위(`0곳`)는 원문 표기가 모델 단위와 같을 때만 단위로 인정한다(reported).
      모델 단위만으로 원문에 없는 단위를 확인했다고 하지 않는다.
    """
    from ..rag_gates.units import normalize_unit

    read = _read_unit(statement, end)
    if read.status == "ABSENT":
        return _ZeroUnit("absent", "", end)
    if read.has_denominator_marker:
        if read.status != "COMPOUND_COMPLETE":
            return _ZeroUnit("incomplete", read.text, read.span[1])
        word_end = read.span[1]
        slash = max(statement.rfind("/", 0, word_end), statement.rfind("／", 0, word_end))
        denominator = statement[slash + 1:word_end].strip()
        known = max((n for n in range(1, len(denominator) + 1)
                     if normalize_unit(denominator[:n]) is not None), default=len(denominator))
        return _ZeroUnit("compound", read.text, word_end - len(denominator) + known)
    start, word_end = read.span[0], _UNIT_WORD_RE.match(statement, end).end()
    word = statement[start:word_end]
    reported = re.sub(r"\s+", "", reported_unit or "")
    prefixes = [n for n in range(len(word), 0, -1) if normalize_unit(word[:n]) is not None]
    if reported and word.startswith(reported) and (not prefixes or len(reported) > prefixes[0]):
        prefixes.insert(0, len(reported))
    for n in prefixes:
        if re.fullmatch(r"[가-힣]*", word[n:]):
            status = "read" if normalize_unit(word[:n]) is not None else "reported"
            return _ZeroUnit(status, word[:n], start + n)
    if _ZERO_UNITLESS_TAIL_RE.match(word):
        return _ZeroUnit("absent", "", start)   # `0으로 집계`·`0이다` — 단위 없이 조사·서술어가 붙었다
    return _ZeroUnit("unknown", word, word_end)


def _unit_cell(text: str) -> bool:
    """표 칸 전체가 단위 하나인가(`tCO2eq`·`건`·`백만원`)."""
    from ..rag_gates.units import normalize_unit
    return bool(text.strip()) and normalize_unit(text.strip()) is not None


def _drop_unit_cells(text: str) -> str:
    """`|`로 나뉜 칸 중 단위만 든 칸을 지운다(칸 구분은 남긴다)."""
    return "|".join("" if _unit_cell(part) else part for part in text.split("|"))


def _zero_label_unit(statement: str, start: int, column: str) -> str:
    """값 앞(행 라벨 `산업재해율(‰)`·단위 칸 `| tCO2eq |`)이나 열 머리(`배출량(tCO2eq)`)에 적힌 단위.
    없으면 ""."""
    from ..rag_gates.units import normalize_unit

    for text in (statement[:start], column):
        for m in reversed(list(re.finditer(r"[(（]\s*([^()（）]+?)\s*[)）]", text))):
            if normalize_unit(m.group(1)) is not None:
                return m.group(1)
    cells = statement[:start].split("|")[:-1]          # 값 칸 앞의 행 라벨 칸들
    return next((c.strip() for c in reversed(cells) if _unit_cell(c)), "")


def _numeric_zero_mood(statement: str, end: int, reported_unit: str = "") -> str | None:
    """숫자 0이 실제 실적으로 적혔으면 None, 아니면 채택하지 않는 사유. 부정 서술과 같은 규약이다.

    표의 `산업재해 발생 건수 | 0`과 `0건이다`·`0건으로 집계됐다`는 사실이다. 목표·예상·조건·가정·
    미확인 서술 안의 0, 그리고 **인식하지 못한 뒤 서술**의 0은 사실로 보지 않는다.
    단위(`_zero_unit`)가 소비한 범위 뒤만 조사·서술어로 판정한다. 단위를 읽었다는 것만으로
    채택하지 않고, 단위를 읽지 못한 0(`unit_incomplete`·`unit_unknown`)은 사실성 미판정
    (`interpretation_unknown`)과 따로 기록한다.
    """
    head, rest = statement[:end], statement[end:]
    if _ZERO_UNCONFIRMED_RE.search(statement):
        return "not_confirmed"
    if _ZERO_NO_PROOF_RE.search(rest):
        return "evidence_insufficient"
    unit = _zero_unit(statement, end, reported_unit)
    tail = statement[unit.tail:]
    if re.match(r"\s*(?:이|일|인)?(?:면|라면|다면|더라도)", tail) \
            or re.match(r"\s*(?:일|인)\s*(?:경우|때)", tail):
        return "conditional"
    if _ZERO_ASSUMPTION_RE.search(rest):
        return "assumption"
    if _ZERO_INTENT_RE.search(rest) or re.search(r"(?:목표|계획|예정|예상|전망)\s*$", head[:-1]):
        return "future_or_intent"
    if unit.status in ("incomplete", "unknown"):
        return f"unit_{unit.status}"
    if _ZERO_NUMERIC_FACT_TAIL_RE.fullmatch(_strip_standard_note(tail)):
        return None
    return "interpretation_unknown"


_PAREN_NOTE_RE = re.compile(r"\s*[(（]([^()（）]{1,48})[)）]")
_STANDARD_NOTE_WORDS_RE = re.compile(r"기준|준거|참조|참고|따름|[\s,·]")


def _strip_standard_note(tail: str) -> str:
    """0 바로 뒤의 규격 인용 괄호(`(GRI 403-9 기준)`·`(ISO 45001:2018 기준)`)를 뗀 뒤 서술.

    §5 C1: 규격 인용은 이 0의 기간·조건이 아니다. 괄호 안이 규격 식별자와 `기준`류 낱말뿐일 때만 뗀다
    — `(목표)`·`(추정)`·`(미확인)`처럼 다른 말이 섞인 괄호는 그대로 두어 사실성 판정을 받게 한다.
    """
    m = _PAREN_NOTE_RE.match(tail)
    if not m:
        return tail
    inner = m.group(1)
    refs = _standard_refs(inner)
    if not refs:
        return tail
    leftover = list(inner)
    for name, _number, end in refs:
        leftover[name:end] = " " * (end - name)
    if _STANDARD_NOTE_WORDS_RE.sub("", "".join(leftover)):
        return tail
    return tail[m.end():]


# 공통 머리말에 와도 되는 말: 범위·보고 단위를 가리킬 뿐 따로 센 수량이 없는 낱말.
_ZERO_HEADING_WORDS = frozenset({
    "현황", "실적", "기준", "기간", "보고", "결과", "요약", "현재", "시점", "월간", "연간", "전체",
    "구분", "항목", "주요", "데이터", "지표", "성과", "기록", "대상", "사업장", "공장", "본사",
    "안전", "보건", "환경", "사회", "지배구조", "ESG", "esg"})


# 서술을 끝맺는 어미. 숫자 없는 문장(`교육을 실시했다`)은 제목이 아니라 그 절의 서술이다.
_SENTENCE_END_RE = re.compile(r"(?:다|음|함|됨|임|요)[\s.。!)\]]*$")


# 제목·목록 번호(줄 머리): `2.`·`2)`·`(2)`·`[2]`·`2-1.`·`2.1`·`제2절`·`제3장`, 앞의 `#`·`*` 장식 포함.
_HEADING_NUMBER_RE = re.compile(
    r"\s*[#*>]*\s*(?:제\s*\d{1,3}\s*(?:편|부|장|절|관)"
    r"|[(（\[［]\s*(?:\d{1,3}|[가-하]|[ivxIVX]{1,4})\s*[)）\]］]"
    r"|\d{1,3}(?:[.\-]\d{1,3})*\s*[.)）]|\d{1,3}(?:[.\-]\d{1,3})+)\**(?=\s)")
# 마침표 앞에서 갈려 번호만 남은 토막(`Ⅱ`·`II`·`가`·`①`).
_LIST_MARKER_RE = re.compile(r"\s*[#*>]*\s*(?:[Ⅰ-Ⅻ]+|[IVX]{1,4}|[가-하]|[①-⑳])\s*")


def _heading_number(piece: str) -> int:
    """토막 앞 제목·목록 번호의 길이(없으면 0). 번호 뒤가 단위면 수량이다(`2.5 톤`·`1-2 명`).

    PR 69 6차 검토: `2. 2026년 5월 부산 제1공장 교육 현황`의 `2`를 수량으로 읽어 새 절 제목을 사실로
    건너뛰었고, 앞 절의 4월·김해 범위로 0이 CONFIRMED가 됐다. 번호는 제목의 일부일 뿐 따로 센 수가
    아니다. 줄 머리의 번호 형식만 떼며, 그 뒤 내용은 그대로 판정한다(`1. 산업재해 0건`은 사실이다).
    """
    from ..rag_gates.units import normalize_unit
    m = _HEADING_NUMBER_RE.match(piece)
    if not m:
        return 0
    after = re.match(r"\s*([^\s()（）|,]+)", piece[m.end():])
    if after and normalize_unit(after.group(1)) and re.fullmatch(r"\s*\d[\d.\-]*\**", m.group()):
        return 0
    return m.end()


# 각주 참조(1~3자리, 긴 보고서의 `[123]`까지): 괄호 안의 번호만(`[1]`·`(2)`·`[주3]`), 각주 표지 +
# 번호(`주1)`·`※2`·`*3`), 낱말 뒤 닫는 괄호 번호(`현황1)`·`현황 1)`), 위첨자(`¹`). 괄호 안에 단위가
# 붙으면(`(1건)`) 수량이다.
_HEADING_FOOTNOTE_RE = re.compile(
    r"[(（\[［]\s*(?:주\s*)?\d{1,3}\s*[)）\]］]"
    r"|(?:주|註)\s*\d{1,3}\s*[)）\]］]|(?:※|(?<!\*)\*(?!\*))\s*\d{1,3}(?!\d|[.,]\d)\s*[)）]?"
    r"|(?<=[가-힣A-Za-z\s])\d{1,3}[)）](?=\s*$)"
    r"|[¹²³⁰⁴-⁹]+")
# 규격·문서 식별자: 로마자 이름 + 번호(`ISO 45001`·`OHSAS 18001:2007`·`ISO/IEC 27001(2022)`·
# `GRI 403-9`·`Scope 1·2`). 이름에 붙은 번호는 센 수가 아니다. 개정 연도도 번호의 일부다.
_HEADING_CODE_RE = re.compile(
    # 번호 안의 콜론은 연도가 아닌 갈래 번호만 잇는다(`2:1`). 콜론 뒤 연도는 공백 유무와 상관없이
    # 아래 `revision` 하나로만 읽는다 — PR71 검토 R3: 번호가 `403:2026`을 먼저 삼켜 `GRI 403:2026년
    # 4월`의 실제 연도를 가렸다.
    r"(?<![A-Za-z\d])[A-Z][A-Za-z]*(?:[/\-&][A-Z][A-Za-z]*)*[\s\-]?"
    r"(?P<number>\d+(?:(?:[.\-·/]|:(?!(?:19|20)\d{2}(?!\d)))\d+)*)"
    # 콜론 앞뒤 공백·전각 콜론을 둔 개정 연도(`GRI 403:2018`·`GRI 403: 2018`·`GRI 403：2018`, §5 C1).
    # 개정 연도인지 실제 기간인지(`GRI 403:2026년 4월`)는 `_standard_refs`가 뒤 문구로 판정한다.
    r"(?P<revision>\s*[:：]\s*(?P<revision_year>(?:19|20)\d{2})(?!\d))?"
    r"(?:\s*[(（]\s*(?:19|20)\d{2}\s*[)）])?")
# 공백·전각 콜론 뒤 연도를 개정판으로 붙이는 규격 이름. `Scope 1: 2025 배출량`의 2025는 보고 연도다.
_REVISION_STANDARDS = frozenset({"ISO", "IEC", "GRI", "OHSAS", "KS", "IATF", "EN", "BS", "SA", "AA", "SASB"})
# 차례 번호(`제2차`·`제3회`). `제1공장`은 사업장 표기(`_SITE_MENTION_RE`)가 따로 읽는다.
_HEADING_ORDINAL_RE = re.compile(r"제\s*\d{1,3}\s*(?:차|회|기|호)")
# 규격 목차·색인 줄의 끝: 점선 뒤 쪽 번호(`GRI 403-9 산업재해 ··· 45`).
_TOC_TAIL_RE = re.compile(r"(?:…|‥|(?:[.·・ㆍ]\s?){3,})\s*\d{1,4}\s*$")


def _unit_after(piece: str, end: int) -> bool:
    from ..rag_gates.units import normalize_unit
    after = re.match(r"[\s*]*([^\s()（）|,\[\]*]+)", piece[end:])
    return bool(after) and normalize_unit(after.group(1)) is not None


def _revision_is_period(piece: str, year_start: int) -> bool:
    """콜론 뒤 연도가 실제 보고 기간의 시작인가 — `년`이 붙거나(`2026년`) 월·일·구간으로 이어지면
    (`2026.04`·`2026-04-01`·`2025~2026`) 기간이다. 홀로 선 연도(`45001:2018 기준`)는 개정판이다."""
    tail = piece[year_start:]
    if re.match(r"\d{4}\s*년", tail) or _DATE_ISO_RE.match(tail):
        return True
    join = _RANGE_JOIN_RE.match(tail, 4)
    return bool(join) and bool(re.match(r"(?:19|20)\d{2}(?!\d)", tail[join.end():]))


def _standard_refs(piece: str, start: int = 0) -> list[tuple[int, int, int]]:
    """규격·문서 식별자 `(이름 시작, 번호 시작, 끝)`. 번호는 센 수도 기간도 아니다.

    빼는 것: 번호가 날짜·회계연도·분기로 시작하는 표기(`FY2026`·`FY 2026.04`·`Q1`), 뒤에 단위가
    붙은 수량(`ISO 3건`), 원문 사업장 구간과 겹치는 식별자 모양(`A2공장`의 `A2`).
    """
    sites = [m.span() for m in _SITE_MENTION_RE.finditer(piece)]
    refs = []
    for m in _HEADING_CODE_RE.finditer(piece, start):
        code = m.group("number")
        if any(code.startswith(span.text) for span in _date_spans(code)):
            continue
        if any(span.kind in ("FISCAL", "QUARTER") and m.group(0).startswith(span.text)
               for span in _date_spans(m.group(0))):
            continue
        end = m.end()
        name = piece[m.start():m.start("number")].strip(" -")
        if m.group("revision") and (not set(re.split(r"[/\-&]", name)) & _REVISION_STANDARDS
                                    or _revision_is_period(piece, m.start("revision_year"))):
            end = m.start("revision")        # 개정판이 아닌 콜론 뒤 연도는 실제 기간이다 — 가리지 않는다
        if _unit_after(piece, end):
            continue
        if any(m.start() < s_end and s_start < end for s_start, s_end in sites):
            continue
        refs.append((m.start(), m.start("number"), end))
    return refs


def _standard_mask(text: str) -> str:
    """기간 판독용: 규격 식별자의 번호·개정 연도만 같은 길이의 공백으로 가린 문구(§5 C1).

    서술·열 머리에도 쓴다 — `ISO 45001:2018 기준 산업재해 0건`의 2018은 보고 기간이 아니다.
    사업장·수량·실제 날짜는 그대로 둔다.
    """
    chars = list(str(text or ""))
    for _name, number, end in _standard_refs(str(text or "")):
        chars[number:end] = " " * (end - number)
    return "".join(chars)


def _heading_mask(piece: str) -> str:
    """판정용 제목 문구: 줄 머리 번호·각주 참조·규격 식별자·차례 번호를 **같은 길이의 공백**으로
    가린 문구. 날짜·사업장 위치는 원문과 같다(감사 기록은 원문 `piece`를 쓴다).

    PR 69 7차 검토: 줄 머리 번호를 뗀 뒤 `[1]`·`(1)`·`ISO 45001`의 숫자가 남아 새 절 제목
    `2. 2026년 5월 부산 제1공장 교육 현황 [1]`이 사실 서술로 분류됐고, 앞 절의 4월·김해 범위가
    0의 근거가 됐다. 숫자가 있다는 것과 센 수가 있다는 것은 다르다. 뒤에 단위가 붙은 숫자
    (`ISO 3건`·`(1건)`·`2.5 톤`)는 가리지 않는다 — 실제 수량·단위·날짜·목표 문구는 그대로 판정한다.
    """
    # 원문에서 읽히는 사업장 구간(`A2공장`·`B2사업장`·`김해 제1공장`)은 가리지 않는다 — 식별자
    # 모양(`A2`)이 겹쳐도 사업장 번호다. 가린 뒤 사업장이 사라지면 위 머리말의 사업장을 물려받는다.
    sites = [m.span() for m in _SITE_MENTION_RE.finditer(piece)]
    marks: list[tuple[int, int]] = []
    number = _heading_number(piece)
    if number:
        marks.append((0, number))
    for m in _HEADING_FOOTNOTE_RE.finditer(piece, number):
        if not _unit_after(piece, m.end()):
            marks.append(m.span())
    # 번호가 날짜·회계연도·분기로 시작하면(`FY2026`·`FY 2026.04`·`Q1`) 식별자가 아니라 기간이다
    # (`_date_spans`가 읽는다). 뒤에 붙은 개정 연도(`45001:2018`·`403: 2018`)는 번호의 일부라 가린다.
    # 이름(`ISO`)은 남긴다 — 이 지표에 범위를 줄 수 있는 제목인지는 `_heading_kind`가 판정한다.
    marks += [(number_start, end) for _name, number_start, end in _standard_refs(piece, number)]
    marks += [m.span() for m in _HEADING_ORDINAL_RE.finditer(piece, number)]
    chars = list(piece)
    for start, end in marks:
        if any(start < s_end and s_start < end for s_start, s_end in sites):
            continue
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def _heading_kind(piece: str, allowed: set[str]) -> str:
    """앞 문맥 한 토막의 성격: blank | table(표 행) | fact(수량·부정·문장 서술) |
    heading(이 지표에 범위를 줄 수 있는 머리말) | standard_heading(규격 식별자가 붙은 이 지표의 실적
    제목 — 자기 범위만 주는 새 절) | other_heading(다른 대상의 제목·규격 목차 — 새 절).

    숫자는 `_heading_mask`가 가린 뒤에도 남을 때만 수량이다. 구조화 제목 표지(`#`)가 붙은 줄은
    숫자가 남아도 제목이다 — 그 범위를 이 지표에 줄 수 있는지는 낱말로 따로 판정한다.

    §5 C3(2026-10-05): PR 69 7차 규칙은 `ISO`·`GRI`가 붙은 제목을 모두 다른 대상의 제목으로 봐
    `GRI 403-9 A2공장 안전 현황`의 A2공장·`GRI 403-9 2026년 4월 김해 제1공장 안전 현황`의 기간을 버렸다.
    규격 식별자(이름+번호)를 뗀 나머지가 이 지표의 범위 낱말뿐이면 그 제목의 사업장·기간은 아래 값에
    적용된다. 규격 제목은 여전히 절 경계라 위 머리말의 범위는 물려받지 않는다(`_zero_heading`).
    규격 목차·색인 줄(`GRI 403-9 산업재해 ··· 45`)과 다른 대상의 규격 제목(`ISO 45001 교육 현황`)은
    적용 관계가 불명확하므로 범위를 주지 않는 경계다.
    """
    if not piece.strip():
        return "blank"
    if _is_table_line(piece):
        return "table"
    if _LIST_MARKER_RE.fullmatch(piece):
        return "blank"                     # `Ⅱ.`·`가.`에서 마침표로 갈린 번호 토막 — 내용이 없다
    number = _heading_number(piece)
    refs = _standard_refs(piece, number)
    if refs and _TOC_TAIL_RE.search(piece):
        return "other_heading"
    rest = list(_heading_mask(piece))
    for name, _number, end in refs:
        rest[name:end] = " " * (end - name)  # 규격 이름도 뗀다 — 남은 낱말로 적용 관계를 본다
    rest = "".join(rest)
    for span in _date_spans(rest):
        rest = rest.replace(span.text, " ")
    rest = _SITE_MENTION_RE.sub(" ", rest)
    counted = re.search(r"\d", rest) and not re.match(r"\s*#", piece)
    if counted or _ZERO_NEGATION_RE.search(rest) or _SENTENCE_END_RE.search(rest.strip()):
        return "fact"
    if not all(w in allowed for w in _label_tokens(rest)):
        return "other_heading"
    return "standard_heading" if refs else "heading"


def _zero_heading(candidate: _ZeroCandidate, hint_tokens: list[str],
                  fiscal: dict[str, tuple[Any, Any]] | None = None) -> tuple[list, set[str], str, str]:
    """후보에 범위를 물려줄 수 있는 **공통 머리말**의 기간·사업장, 그 원문, 상속을 끝낸 경계.

    PR 69 4차 검토(2026-10-02): 다른 절 전체를 폴백 문맥으로 써서 `산업재해 0건, 2026년 4월 김해
    제1공장 교육 참석 인원 50명`의 산업재해가 교육의 날짜·사업장으로 CONFIRMED가 됐다.
    5차 검토: 적용할 수 없는 머리말(`2026년 5월 부산 제1공장 교육 현황`)을 건너뛰고 더 앞 절의
    `2026년 4월 김해 제1공장 안전 현황`까지 거슬러 올라가 CONFIRMED가 됐다. 머리말이 **이 지표에
    범위를 줄 수 있는가**와 **새 절의 시작인가**를 따로 본다.
      - 후보보다 **앞**의 토막만 본다(뒤 절은 다른 사실의 서술이다).
      - 후보의 절 머리말을 찾을 때까지 같은 절의 표 행·데이터 서술·빈 줄은 건너뛴다.
      - 처음 만난 제목이 후보의 절 머리말이다. 다른 대상의 제목(`교육 현황`)이면 그것이 새 절의
        경계다 — 범위를 주지 않고, 더 앞으로 가지 않는다(경계 원문을 돌려준다).
      - 이 지표에 적용되는 머리말이면 그 기간·사업장을 쓰고, **바로 위로 이어진** 머리말(빈 줄만
        사이에 둔 상위 머리말)에서만 빠진 범위를 채운다. 그 사이에 서술·표 행·다른 대상의 제목이
        있으면 앞 절이다 — 기간과 사업장을 서로 다른 절에서 따로 가져와 조립하지 않는다.
    """
    allowed = _ZERO_HEADING_WORDS | _ZERO_GENERIC_TOKENS | set(hint_tokens)
    spans: list = []
    sites: set[str] = set()
    used: list[str] = []
    found = False                          # 후보의 절 머리말을 찾았는가
    for _start, piece in reversed(candidate.preceding):
        kind = _heading_kind(piece, allowed)
        if kind == "blank":
            continue
        if not found:
            if kind in ("table", "fact"):
                continue                   # 같은 절의 표·데이터 행
            if kind == "other_heading":
                return [], set(), "", piece.strip()
            found = True
        elif kind != "heading":
            break                          # 머리말 묶음이 끝났다 — 그 위는 앞 절이다
        # 기간은 가린 문구에서 읽는다 — 규격 개정 연도(`ISO 45001:2018`)는 기간이 아니다. 사업장은
        # 원문에서 읽는다 — 판정용 가공 때문에 자기 사업장이 '없음'이 되어 상속되지 않게 한다.
        piece_spans, piece_sites = _date_spans(_heading_mask(piece), fiscal), _site_keys(piece)
        if not spans and piece_spans:
            spans = piece_spans
            used.append(piece.strip())
        if not sites and piece_sites:
            sites = piece_sites
            if piece.strip() not in used:
                used.append(piece.strip())
        if kind == "standard_heading":
            # 규격 실적 제목은 새 절이다 — 자기 사업장·기간만 쓰고 위 머리말에서 빠진 범위를 채우지 않는다.
            return spans, sites, " / ".join(used), piece.strip()
        if spans and sites:
            break
    return spans, sites, " / ".join(used), ""


def _scope_roles(where: str, text: str, fiscal: dict[str, tuple[Any, Any]] | None = None) -> list[tuple]:
    """범위 판정에 읽은 문구의 구간 역할 — (where, 역할, 원문, 시작, 끝). 위치는 그 문구 기준이다."""
    text = str(text or "")
    roles = [(where, "standard_ref", text[name:end], name, end) for name, _number, end in _standard_refs(text)]
    masked = _standard_mask(text)
    cursor = 0
    for span in _date_spans(masked, fiscal):
        at = masked.find(span.text, cursor)
        if at < 0:
            at = masked.find(span.text)
        role = f"period:{span.undefined}" if span.undefined else "period"
        roles.append((where, role, span.text, at, at + len(span.text) if at >= 0 else -1))
        # 상충한 회계연도 정의의 원문과 위치(정의를 읽은 원문 기준) — 어느 정의끼리 충돌했는지 추적한다.
        roles += [("fiscal_definition", "fiscal_definition", definition, d_start, d_end)
                  for definition, d_start, d_end in span.definitions]
        cursor = max(cursor, at + 1)
    roles += [(where, "site", m.group(0), m.start(), m.end()) for m in _SITE_MENTION_RE.finditer(text)]
    return sorted(roles, key=lambda role: role[3])


def _zero_scope_verdict(candidate: _ZeroCandidate, requested: "_DateSpan | None",
                        boundary: dict[str, Any], base: dict[str, Any],
                        hint_tokens: list[str] = (),
                        fiscal: dict[str, tuple[Any, Any]] | None = None) -> _ZeroVerdict:
    """사실로 확인된 후보의 **사업장 → 기간** 대조. 숫자 0과 부정 서술이 같은 결정표를 쓴다.

    | 요청 ↔ 원문                                      | 판정                         |
    | 같은 범위                                         | CONFIRMED                    |
    | 다른 사업장 · 겹치지 않는 기간                     | REJECTED(site/period_mismatch)|
    | 겹치지만 다른 범위(월 ↔ 기준일, 연간 ↔ 월)          | REJECTED(period_unproven)    |
    | 보고 연도 메타데이터 ↔ 그해 월·기준일·연도만 표기   | SOURCE_ONLY(report_year_only) |
    | 보고 연도 메타데이터 ↔ 그해 명시적 연간(`연간`)     | CONFIRMED                    |
    | 원문에 범위 없음 · 요청 기간 미상 · 원문 연도 미상  | SOURCE_ONLY                  |
    | 원문 기간 표기의 구간 미정의(`FY2026`·`Q1`)         | SOURCE_ONLY(fiscal·quarter_*) |
    | 원문 범위가 여럿이라 연결을 특정하지 못함           | UNRESOLVED(period_ambiguous) |

    기간은 서술·열 머리에서도 규격 번호·개정 연도를 가린 문구로 읽는다(§5 C1: `ISO 45001:2018 기준`의
    2018은 보고 기간이 아니다). 사업장은 원문에서 읽는다.

    `fiscal`이 함수면 사업장(서술 → 열 머리 → 머리말)을 먼저 읽고 그 사업장에 적용되는 회계연도 정의를 받는다.
    """
    if callable(fiscal):
        fiscal = fiscal(_site_keys(candidate.statement) or _site_keys(candidate.column)
                        or _zero_heading(candidate, list(hint_tokens))[1])
    statement_spans = _date_spans(_standard_mask(candidate.statement), fiscal)
    column_spans = _date_spans(_standard_mask(candidate.column), fiscal)
    statement_sites, column_sites = _site_keys(candidate.statement), _site_keys(candidate.column)
    spans = statement_spans or column_spans
    sites = statement_sites or column_sites
    origin = "statement" if statement_spans or statement_sites else "column" if spans or sites else ""
    heading = boundary_heading = ""
    if not spans or not sites:
        heading_spans, heading_sites, heading, boundary_heading = _zero_heading(
            candidate, list(hint_tokens), fiscal)
        if (not spans and heading_spans) or (not sites and heading_sites):
            origin = f"{origin}+heading" if origin else "heading"
        else:
            heading = ""
        spans = spans or heading_spans
        sites = sites or heading_sites
    roles = _scope_roles("statement", candidate.statement, fiscal)
    if candidate.column:
        roles += _scope_roles("column", candidate.column, fiscal)
    for where, text in (("heading", heading), ("boundary", boundary_heading)):
        if text and not (where == "boundary" and text == heading):
            roles += _scope_roles(where, text, fiscal)
    base = {**base, "scope_from": origin, "scope_heading": heading, "scope_boundary": boundary_heading,
            "scope_spans": tuple(roles)}
    site_text = ", ".join(sorted(sites))
    if not _site_matches(boundary, sites):
        return _ZeroVerdict("REJECTED", "site_mismatch", evidence_site=site_text, **base)
    relation, span = _period_relation(requested, spans)
    period_text = _spans_text([span]) if span is not None else _spans_text(spans)
    scope = {"evidence_period": period_text, "evidence_site": site_text, "source_scope": span, **base}
    if relation == "mismatch":
        return _ZeroVerdict("REJECTED", "period_mismatch", **scope)
    if relation == "unproven":
        return _ZeroVerdict("REJECTED", "period_unproven", **scope)
    if relation == "ambiguous":
        return _ZeroVerdict("UNRESOLVED", "period_ambiguous", **scope)
    if relation == "undefined":
        return _ZeroVerdict("SOURCE_ONLY", span.undefined, **scope)
    if relation != "match":
        cause = {"report_year": "report_year_only", "unscoped": "period_not_stated",
                 "year_unknown": "source_year_unknown"}.get(relation, "requested_period_unknown")
        return _ZeroVerdict("SOURCE_ONLY", cause, **scope)
    site = str(boundary.get("site") or "").strip()
    if site and not sites:
        return _ZeroVerdict("SOURCE_ONLY", "site_not_stated", **scope)
    if sites and not site:
        return _ZeroVerdict("SOURCE_ONLY", "source_site_only", **scope)
    return _ZeroVerdict("CONFIRMED", "stated_zero", **scope)


def _zero_trace(verdict: _ZeroVerdict) -> dict[str, Any]:
    """0 판정의 연결 근거(위치·열 머리·원문 단위·범위 출처). 감사 기록과 경계 출처에 함께 싣는다.

    `source_unit`은 **원문에 적힌** 단위다. 모델 단위는 별도 필드(`unit`)로 남기고 섞지 않는다.
    """
    return {"evidence_offset": verdict.evidence_offset,
            "evidence_column": verdict.evidence_column, "column_state": verdict.column_state,
            "source_unit": verdict.source_unit, "unit_location": verdict.unit_location,
            "scope_from": verdict.scope_from, "scope_heading": verdict.scope_heading[:_QUOTE_MAX_CHARS],
            "evidence_start": verdict.evidence_start, "evidence_end": verdict.evidence_end,
            "row_label": verdict.row_label[:_QUOTE_MAX_CHARS],
            "scope_boundary": verdict.scope_boundary[:_QUOTE_MAX_CHARS],
            "scope_spans": [{"where": where, "role": role, "text": text, "start": start, "end": end}
                            for where, role, text, start, end in verdict.scope_spans]}


def _zero_scope_boundary(boundary: dict[str, Any], verdict: _ZeroVerdict, period: str) -> dict[str, Any]:
    """보존한 0의 경계: 모델 경계에 **원문 범위**와 판정 기록을 더한다(`MeasurementBoundary.from_dict`).

    - 원문 범위가 있으면 그 기간·집계 방식을 쓴다(MONTH → 월간, AS_OF·DAY → 시점, ANNUAL → 연간,
      RANGE → 기간 합계, 연도 표기만 → 연도만). 연도 미상 범위는 날짜를 만들지 않는다.
    - SOURCE_ONLY는 `review_notes`에 무엇이 확인되지 않았는지를 남긴다. 후속 원장은 이 기록
      (`provenance`의 `zero_evidence`)으로 범위 미확정을 표시한다.
    """
    span = verdict.source_scope
    scope: dict[str, Any] = {"basis": "actual"}
    if span is not None and span.undefined:
        scope["period_text"] = span.text.strip()     # 표기만 남긴다 — 회계연도 구간을 날짜로 만들지 않는다
    elif span is not None:
        scope["period_text"] = span.text.strip()
        if span.kind == "MONTH":
            scope.update(aggregation="monthly", coverage_months=1)
        elif span.kind in ("AS_OF", "DAY"):
            scope["aggregation"] = "point"
        elif span.kind == "ANNUAL":
            scope.update(aggregation="annual", coverage_months=12)
        elif span.kind == "RANGE":
            scope["aggregation"] = "period_total"
        if span.year_known:
            scope["period_year"] = span.start.year
            if span.kind != "YEAR":
                scope.update(period_start=span.start.isoformat(), period_end=span.end.isoformat())
    if verdict.evidence_site and not str(boundary.get("site") or "").strip():
        scope.update(site=verdict.evidence_site, site_scope="site")   # 모델 사업장 표기는 그대로 둔다
    record = {"source": "zero_evidence", "status": verdict.status, "cause": verdict.cause,
              "candidate": verdict.candidate, "requested_period": period,
              "requested_scope": verdict.requested_scope,
              "source_period": verdict.evidence_period, "source_site": verdict.evidence_site,
              "evidence": verdict.evidence_text[:_QUOTE_MAX_CHARS], **_zero_trace(verdict),
              "method": "rule"}
    merged = {**boundary, **scope}
    merged["provenance"] = [*(boundary.get("provenance") or []), record]
    if verdict.status == "SOURCE_ONLY":
        note = _ZERO_KEPT_CAUSES.get(verdict.cause) or "원문 사실만 확인했고 요청 범위는 확인하지 못했습니다."
        note = note.replace("{site}", str(boundary.get("site") or "").strip() or verdict.evidence_site or "이 사업장")
        merged["review_notes"] = [*(boundary.get("review_notes") or []), note]
    return merged


# ── 0의 기간·사업장 대조 (PR 69 2차·3차 검토) ─────────────────────────────────
# 연도 집합만 비교하면 `2026년 3월` 미발생이 4월 0건의 근거가 됐다. 기준일·월·연도·구간을
# 실제 범위로 읽어 **같은 범위일 때만** 인정한다. `measurement_context.period_bounds`는
# 단일 날짜를 읽지 못하고 `site_path`는 지역명을 떼므로 이 경로 전용으로 둔다.
# 3차 검토: 기간에 **무슨 의미로 쓰였는지**(kind)를 함께 둔다. 모델의 `period="2026"`은 보고
# 연도일 뿐 연간 합계가 아니다 — `grain='year'`만으로 둘을 가르지 않는다.

@dataclass(frozen=True)
class _DateSpan:
    start: Any       # datetime.date. 연도 미상이면 `_YEARLESS` 해의 날짜
    end: Any
    grain: str       # day | month | year | range
    text: str
    # REPORT_YEAR(요청의 보고 연도) · ANNUAL(명시적 연간) · YEAR(원문의 연도 표기) · MONTH ·
    # AS_OF(`기준`이 붙은 날) · DAY · RANGE · FISCAL(`FY2026`) · QUARTER(`Q1`)
    kind: str = ""
    year_known: bool = True   # False면 월·일만 확인됐다(`3월`·`04-01~04-07`)
    # 기간 표기는 있으나 실제 시작·종료일을 원문으로 확정할 수 없는 사유(`_ZERO_KEPT_CAUSES`의 키).
    # 비어 있지 않으면 start·end는 비교에 쓰지 않는 자리값이다 — 회계연도 시작월·연도를 보충하지 않는다.
    undefined: str = ""
    # `undefined`가 `fiscal_definition_conflict`·`fiscal_definition_not_applicable`일 때 원문 정의(정의 원문, 시작, 끝).
    definitions: tuple = ()


_YEARLESS = 2000    # 연도 미상 범위를 담는 윤년. 비교 때 요청 연도로 옮긴다.
_DATE_ISO_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})[-./](\d{1,2})(?:[-./](\d{1,2}))?(?![\d])")
_DATE_KR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*년(?:\s*(\d{1,2})\s*월(?:\s*(\d{1,2})\s*일)?)?")
# 연도만 적은 표기. 수량(`2000명`)은 연도로 읽지 않는다.
_DATE_YEAR_RE = re.compile(
    r"(?<![\d.,])((?:19|20)\d{2})(?![\d.,]|\s*(?:명|건|원|개|대|톤|kg|회|시간|%))")
# 연도 없는 월·일(`3월`·`4월 1일`). `12개월`·`매월`은 읽지 않는다.
_DATE_MONTH_ONLY_RE = re.compile(r"(?<![\d.,])()(\d{1,2})\s*월(?:\s*(\d{1,2})\s*일)?")
# 연도 없는 날짜 구간(`04-01~04-07`). 구간일 때만 읽는다 — `3-1`처럼 홀로 선 표기는 항목 번호일 수 있다.
_DATE_PARTIAL_RANGE_RE = re.compile(
    r"(?<![\d\-./])(\d{1,2})[-./](\d{1,2})\s*(?:~|∼|〜|부터)\s*(\d{1,2})[-./](\d{1,2})(?![\d\-./])")
_RANGE_JOIN_RE = re.compile(r"\s*(?:~|∼|〜|–|—|-|부터)\s*")
# 구간의 뒤쪽이 연·월을 생략한 표기(`2026-04-01~04-30`·`4월 1일부터 30일까지`).
_RANGE_PARTIAL_RE = re.compile(r"(?:(\d{1,2})[-./](\d{1,2})|(?:(\d{1,2})\s*월\s*)?(\d{1,2})\s*일)(?![\d])")
# 명시적 연간 표기(`2026년 연간`·`2026년 연합계`·`2026년 전체`)와 기준일 표기(`2026-04-01 기준`).
_ANNUAL_WORD_RE = re.compile(r"연간|연\s*합계|연중|년간|한\s*해\s*(?:동안|전체)?|전체|1년\s*간?")
_AS_OF_WORD_RE = re.compile(r"\s*(?:기준|현재|시점)")
# 회계연도·분기 표기(§5 C2). 표기만으로는 실제 구간을 알 수 없다 — 회계연도 시작월과 `FY26`의
# 세기는 원문 정의(`FY2026 = 2026-01-01~2026-12-31`)가 있을 때만 확정한다. 현재 날짜·요청 기간으로
# 보충하지 않는다.
# `FY 2026.04`·`FY2026-04`처럼 월이 붙으면 날짜 표기다(PR 69 8차: 실제 기간으로 읽는다) — 회계연도로 잡지 않는다.
_FISCAL_RE = re.compile(r"(?<![A-Za-z])FY\s?'?(?P<year>(?:19|20)\d{2}|\d{2})(?![\d]|[.\-/]\d)")
_QUARTER_RE = re.compile(
    r"(?:(?<![A-Za-z\d])Q(?P<q1>[1-4])|(?<![\d.])(?P<q2>[1-4])\s*분기)(?![\d])"
    r"(?:\s*[(（]\s*(?P<m1>\d{1,2})\s*월?\s*[~∼〜\-–]\s*(?P<m2>\d{1,2})\s*월\s*[)）])?")
_QUARTER_YEAR_RE = re.compile(r"(?<![\d.,])((?:19|20)\d{2})\s*년?\s*$")
# 회계연도 정의: `FY2026 = 2026-01-01~2026-12-31`·`FY2026(2026.1.1~2026.12.31)`·`FY26: 2026년 1월~12월`.
_FISCAL_DEF_RE = re.compile(
    r"(?<![A-Za-z])(?P<token>FY\s?'?(?:(?:19|20)\d{2}|\d{2}))(?![\d])\s*(?:=|:|：|[(（])\s*(?P<body>[^)）\n]{4,48})")


def _fiscal_key(token: str) -> str:
    return re.sub(r"[\s']", "", token).upper()


@dataclass(frozen=True)
class _FiscalConflict:
    """회계연도 표기의 구간을 원문 정의로 정할 수 없는 경우 — 서로 다른 구간의 정의가 함께 적용되거나
    (`fiscal_definition_conflict`), 적용되는 정의가 없다(`fiscal_definition_not_applicable`, 다른 사업장의 정의만 있음).

    `definitions`는 (정의 원문, 그 텍스트 안의 시작, 끝)이다. 감사 기록에 그대로 싣는다.
    """
    definitions: tuple = ()
    cause: str = "fiscal_definition_conflict"


def _fiscal_definitions(text: str, sites: set[str] | None = None) -> dict[str, Any]:
    """원문이 직접 적은 회계연도 구간 {`FY2026`: (시작일, 종료일)}. 구간을 한 개로 읽을 때만 싣는다.

    `FY26(2026년)`처럼 연도만 잇고 구간을 적지 않은 표기는 정의가 아니다 — 회계연도 시작월을 모른다.
    `FY26 = FY2026`은 정의된 `FY2026`의 구간을 그대로 잇는다.

    PR71 검토 R4: 첫 정의만 보관해 정의 줄 순서에 따라 같은 0이 CONFIRMED·REJECTED로 갈렸다. 정의를
    모두 모은다. 같은 구간의 반복은 정의 하나다. 구간이 서로 다르면 `_FiscalConflict`로 남긴다 — 첫 값·
    마지막 값·요청과 맞는 값을 고르지 않는다. 별칭(`FY26 = FY2026`)은 가리킨 표기의 충돌·적용 사업장도 함께 잇는다.

    PR71 재검토 C: 적용되는 정의가 하나도 없을 때 `[적용 정의] or found`로 전체 정의에 되돌아가, 부산 제2공장의
    정의가 김해 제1공장의 0을 CONFIRMED·REJECTED로 정했다. 정의는 사업장이 적히지 않은 공통 정의이거나 대조할
    사업장을 적은 정의일 때만 적용한다. 적용되는 정의가 없으면 `fiscal_definition_not_applicable`(구간 미확정)이다 —
    다른 사업장의 정의·요청 기간으로 보충하지 않는다. 공통 정의와 그 사업장 정의가 다르면 우선순위가 원문에 없으므로
    충돌이다. `sites`가 비면(사업장 없는 대조) 공통 정의만 적용한다.
    """
    text = str(text or "")
    entries: dict[str, list[tuple[tuple[Any, Any], set[str], tuple[str, int, int]]]] = {}
    links: dict[str, list[tuple[str, tuple[str, int, int]]]] = {}
    for m in _FISCAL_DEF_RE.finditer(text):
        key, body = _fiscal_key(m.group("token")), m.group("body")
        source = (m.group(0).strip(), m.start(), m.end())
        alias = _FISCAL_RE.match(body.strip())
        if alias:
            links.setdefault(key, []).append((_fiscal_key(alias.group(0)), source))
            continue
        spans = [s for s in _date_spans(body) if s.year_known and not s.undefined]
        if len(spans) == 1 and spans[0].kind in ("RANGE", "ANNUAL"):
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", m.end())
            line_sites = _site_keys(text[line_start:line_end if line_end >= 0 else len(text)])
            entries.setdefault(key, []).append(((spans[0].start, spans[0].end), line_sites, source))
    alias_sources: dict[str, list[tuple[str, int, int]]] = {}
    direct = {key: list(found) for key, found in entries.items()}
    for key, targets in links.items():
        for target, source in targets:
            if direct.get(target):
                alias_sources.setdefault(key, []).append(source)
                entries.setdefault(key, []).extend(direct[target])
    defs: dict[str, Any] = {}
    for key, found in entries.items():
        applicable = [entry for entry in found if not entry[1] or entry[1] & set(sites or ())]
        distinct = {bounds for bounds, _sites, _source in applicable}
        sources = tuple(dict.fromkeys([*alias_sources.get(key, []), *(source for _b, _s, source in found)]))
        if len(distinct) == 1:
            defs[key] = next(iter(distinct))
        elif not distinct:
            defs[key] = _FiscalConflict(sources, "fiscal_definition_not_applicable")
        else:
            defs[key] = _FiscalConflict(sources)
    return defs


def _fiscal_quarter_spans(text: str, fiscal: dict[str, tuple[Any, Any]] | None,
                          taken: list[tuple[int, int]]) -> list[tuple[int, _DateSpan]]:
    """`FY2026`·`FY26`·`Q1`·`2026년 1분기(1~3월)`의 범위. 확정할 수 없으면 `undefined` 사유를 단다."""
    import calendar
    from datetime import date

    found: list[tuple[int, _DateSpan]] = []
    for m in _FISCAL_RE.finditer(text):
        key = _fiscal_key(m.group(0))
        bounds = (fiscal or {}).get(key)
        short = len(m.group("year")) == 2
        if isinstance(bounds, _FiscalConflict):
            year = _YEARLESS if short else int(m.group("year"))
            found.append((m.start(), _DateSpan(
                date(year, 1, 1), date(year, 12, 31), "year", m.group(0), "FISCAL", not short,
                bounds.cause, bounds.definitions)))
        elif bounds:
            whole = (bounds[0].month, bounds[0].day, bounds[1].month, bounds[1].day) == (1, 1, 12, 31) \
                and bounds[0].year == bounds[1].year
            found.append((m.start(), _DateSpan(bounds[0], bounds[1], "range", m.group(0),
                                               "ANNUAL" if whole else "RANGE", True)))
        else:
            year = _YEARLESS if short else int(m.group("year"))
            found.append((m.start(), _DateSpan(
                date(year, 1, 1), date(year, 12, 31), "year", m.group(0), "FISCAL", not short,
                "fiscal_year_abbreviated" if short else "fiscal_period_undefined")))
        taken.append((m.start(), m.end()))
    for m in _QUARTER_RE.finditer(text):
        if any(s < m.end() and m.start() < e for s, e in taken):
            continue
        before = _QUARTER_YEAR_RE.search(text[:m.start()])
        start = m.start()
        year = None
        if before and not any(s < m.start() and before.start(1) < e for s, e in taken):
            year, start = int(before.group(1)), before.start(1)
        label = text[start:m.end()]
        m1, m2 = m.group("m1"), m.group("m2")
        if year and m1 and m2 and 1 <= int(m1) <= int(m2) <= 12:
            first, last = int(m1), int(m2)
            found.append((start, _DateSpan(date(year, first, 1),
                                           date(year, last, calendar.monthrange(year, last)[1]),
                                           "range", label, "RANGE", True)))
        else:
            known = year or _YEARLESS
            found.append((start, _DateSpan(date(known, 1, 1), date(known, 12, 31), "range", label,
                                           "QUARTER", bool(year),
                                           "quarter_months_not_stated" if year else "quarter_undefined")))
        taken.append((start, m.end()))
    return found


def _date_spans(text: str, fiscal: dict[str, tuple[Any, Any]] | None = None) -> list[_DateSpan]:
    """문구의 날짜 표기를 실제 범위와 의미로 읽는다. 구간(`A ~ B`)은 하나로 합친다.

    `fiscal`은 원문의 회계연도 정의(`_fiscal_definitions`)다. 없으면 `FY` 표기는 구간 미상으로 남는다.
    """
    import calendar
    from datetime import date

    text = str(text or "")
    tokens: list[tuple[int, int, int | None, int | None, int | None]] = []
    taken: list[tuple[int, int]] = []
    marked = _fiscal_quarter_spans(text, fiscal, taken)
    partials = []
    for m in _DATE_PARTIAL_RANGE_RE.finditer(text):
        if any(s < m.end() and m.start() < e for s, e in taken):
            continue
        partials.append(m)
        taken.append((m.start(), m.end()))
    for pattern in (_DATE_ISO_RE, _DATE_KR_RE, _DATE_YEAR_RE, _DATE_MONTH_ONLY_RE):
        for m in pattern.finditer(text):
            if any(s < m.end() and m.start() < e for s, e in taken):
                continue
            groups = m.groups() + (None, None)
            year = int(groups[0]) if groups[0] else None
            month = int(groups[1]) if groups[1] else None
            if year is None and not (month and 1 <= month <= 12):
                continue
            tokens.append((m.start(), m.end(), year, month, int(groups[2]) if groups[2] else None))
            taken.append((m.start(), m.end()))
    tokens.sort()

    def span(y, mo, d, start, end) -> _DateSpan | None:
        known, y = y is not None, y if y is not None else _YEARLESS
        try:
            if d is not None:
                day = date(y, mo, d)
                kind = "AS_OF" if _AS_OF_WORD_RE.match(text, end) else "DAY"
                return _DateSpan(day, day, "day", text[start:end], kind, known)
            if mo is not None:
                return _DateSpan(date(y, mo, 1), date(y, mo, calendar.monthrange(y, mo)[1]),
                                 "month", text[start:end], "MONTH", known)
            annual = re.match(rf"\s*(?:{_ANNUAL_WORD_RE.pattern})", text[end:])
            label = text[start:end + annual.end()] if annual else text[start:end]
            return _DateSpan(date(y, 1, 1), date(y, 12, 31), "year", label,
                             "ANNUAL" if annual else "YEAR", known)
        except ValueError:
            return None

    spans: list[_DateSpan] = [found for _start, found in sorted(marked, key=lambda pair: pair[0])]
    for m in partials:
        first = span(None, int(m.group(1)), int(m.group(2)), m.start(), m.start())
        second = span(None, int(m.group(3)), int(m.group(4)), m.end(), m.end())
        if first and second and second.end >= first.start:
            spans.append(_DateSpan(first.start, second.end, "range", m.group(0), "RANGE", False))
    index = 0
    while index < len(tokens):
        start, end, y, mo, d = tokens[index]
        first = span(y, mo, d, start, end)
        index += 1
        if first is None:
            continue
        join = _RANGE_JOIN_RE.match(text, end)
        second = None
        if join:
            if index < len(tokens) and tokens[index][0] == join.end():
                s2, e2, y2, mo2, d2 = tokens[index]
                second = span(y2 if y2 is not None else y, mo2, d2, s2, e2)
                index += 1
            elif mo is not None:
                partial = _RANGE_PARTIAL_RE.match(text, join.end())
                if partial:
                    mo2 = int(partial.group(1) or partial.group(3) or mo)
                    d2 = int(partial.group(2) or partial.group(4))
                    second = span(y, mo2, d2, join.end(), partial.end())
        if second is not None and second.end >= first.start:
            whole_year = (first.start.month, first.start.day, second.end.month, second.end.day) == (1, 1, 12, 31)
            spans.append(_DateSpan(first.start, second.end, "range",
                                   text[start:end] + " ~ " + second.text,
                                   "ANNUAL" if whole_year and first.year_known else "RANGE",
                                   first.year_known and second.year_known))
        else:
            spans.append(first)
    return spans


def _spans_text(spans: list[_DateSpan]) -> str:
    return ", ".join(dict.fromkeys(s.text.strip() for s in spans))


def _requested_period(period: str, hint: str = "") -> _DateSpan | None:
    """요청(모델 응답) 기간의 범위와 의미. 읽지 못하거나 여럿이면 None.

    연도만 적힌 `period="2026"`은 **보고 연도**(REPORT_YEAR)다. 지표명·기간에 연간 표기
    (`연간 산업재해 발생 건수`·`2026년 연간`)가 있을 때만 명시적 연간(ANNUAL)으로 본다.
    """
    from dataclasses import replace

    spans = _date_spans(period)
    if len(spans) != 1 or not spans[0].year_known or spans[0].undefined:
        return None
    span = spans[0]
    if span.kind == "YEAR":
        annual = _ANNUAL_WORD_RE.search(hint or "")
        return replace(span, kind="ANNUAL" if annual else "REPORT_YEAR")
    return span


def _period_relation(requested: _DateSpan | None, spans: list[_DateSpan]) -> tuple[str, _DateSpan | None]:
    """요청 기간과 원문 기간의 관계와 대표 원문 범위.

    match(같은 범위) / mismatch(모두 겹치지 않음) / unproven(겹치지만 다른 범위) /
    report_year(요청이 보고 연도뿐) / year_unknown(원문 연도 미상, 월·일은 요청과 같음) /
    unscoped(원문에 범위 없음) / requested_unknown(요청 기간 미상) / ambiguous(원문 범위가 여럿) /
    undefined(원문에 기간 표기는 있으나 실제 구간을 확정할 수 없음 — `FY2026`·`Q1`).
    """
    from dataclasses import replace

    # 회계연도·분기 표기는 요청 기간으로 보충해 맞추지 않는다. 하나라도 있으면 그 사유로 보류한다.
    undefined = next((s for s in spans if s.undefined), None)
    if undefined is not None:
        return "undefined", undefined
    distinct = list({(s.start, s.end, s.year_known): s for s in spans}.values())
    if not distinct:
        return "unscoped", None
    if requested is None:
        return ("requested_unknown", distinct[0]) if len(distinct) == 1 else ("ambiguous", None)

    def relation(s: _DateSpan) -> str:
        if requested.kind == "REPORT_YEAR":
            if not s.year_known:
                return "report_year"
            if s.end < requested.start or s.start > requested.end:
                return "mismatch"
            if s.kind == "ANNUAL" and (s.start, s.end) == (requested.start, requested.end):
                return "match"   # 원문이 그해 연간 범위를 명시했다(`2026년 연간`) — 연도만의 표기와 다르다
            return "report_year" if requested.start <= s.start and s.end <= requested.end else "unproven"
        found = []
        for year in (range(requested.start.year, requested.end.year + 1) if not s.year_known else [None]):
            try:
                shifted = s if year is None else replace(
                    s, start=s.start.replace(year=year),
                    end=s.end.replace(year=year + (s.end.year - s.start.year)))
            except ValueError:
                continue
            if (shifted.start, shifted.end) == (requested.start, requested.end):
                found.append("match" if s.year_known else "year_unknown")
            elif shifted.end < requested.start or shifted.start > requested.end:
                found.append("mismatch")
            else:
                found.append("unproven")
        for r in ("match", "year_unknown", "unproven"):
            if r in found:
                return r
        return "mismatch"

    relations = [relation(s) for s in distinct]
    if len(distinct) == 1:
        return relations[0], distinct[0]
    if all(r == "mismatch" for r in relations):
        return "mismatch", distinct[0]
    return "ambiguous", None


_SITE_KINDS = r"공장|사업장|사업소|본사|지점|센터|캠퍼스"
# 사업장 표기: `김해 제1공장`(지역 + 번호) 또는 `서아산공장`(붙여 쓴 이름).
_SITE_MENTION_RE = re.compile(
    rf"(?:(?P<region>[가-힣A-Za-z]+)\s*)?제\s*(?P<number>\d+)\s*(?P<kind>{_SITE_KINDS})"
    rf"|(?P<name>[가-힣A-Za-z0-9]+?)(?P<kind2>{_SITE_KINDS})")
_SITE_PARTICLE_RE = re.compile(r"(?:은|는|이|가|을|를|도|의|에서|에)$")


def _site_keys(text: str) -> set[str]:
    """문구의 사업장 식별값. 공백·조사·`제`만 정규화하고 지역·번호는 그대로 둔다."""
    keys = set()
    for m in _SITE_MENTION_RE.finditer(str(text or "")):
        if m.group("kind"):
            region = m.group("region") or ""
            if _SITE_PARTICLE_RE.search(region):
                region = ""   # `산업재해는 제1공장` — 조사가 붙은 말은 지역명이 아니다
            keys.add(f"{region}{m.group('number')}{m.group('kind')}")
        else:
            keys.add(m.group("name") + m.group("kind2"))
    return keys


def _site_matches(boundary: dict[str, Any], sites: set[str]) -> bool:
    """원문 사업장이 지표 사업장을 모두 가리키는가. 한쪽이 사업장을 말하지 않으면 판정하지 않는다.

    부분 문자열로 비교하지 않는다(`아산공장` ≠ `서아산공장`, `부산 제1공장` ≠ `김해 제1공장`).
    지역 없는 `제1공장`만으로 지역이 있는 지표 사업장을 고르지 않는다.
    """
    site = str(boundary.get("site") or "").strip()
    if not site or not sites:
        return True
    wanted = _site_keys(site) or {_SITE_PARTICLE_RE.sub("", re.sub(r"\s+", "", site))}
    return wanted <= sites


def _unit_scale(unit: str) -> float:
    """단위에 붙은 배율. `'억 원'` → 1e8, `'조 원'` → 1e12, `'달러'` → 1.0.

    공백을 지운 뒤 앞머리를 본다 — OCR이 뽑는 단위는 `'백만 원'`처럼 배율과 통화 사이에
    공백이 들어온다(`units.normalize_unit`이 같은 이유로 공백을 지운다).
    """
    compact = re.sub(r"\s+", "", unit or "")
    for token in ("천만", "백만", "조", "억", "만", "천"):
        if compact.startswith(token):
            return _KR_SCALES[token]
    return 1.0


def _reconcile_scale_chain(
    value: float, unit: str, evidence: str, *, hint: str | None = None,
) -> tuple[float, dict[str, Any] | None]:
    """모델이 합성한 한국식 자릿수 금액을 **원문 표기로 되잡는다.**

    실측 결함: `매출액 57조 2,370억 원`을 모델이 `57,237억 원`으로 적었다(약 10배 낮다).
    `46조 1,182억` → 46,182, `10조 4,809억` → 10,409도 같은 종류다. 떨어뜨리는 자릿수가
    건마다 달라 **모델 값에서 규칙으로 복원할 수 없다.** 그래서 모델 값을 고치는 대신
    **원문 표기를 다시 읽어 그 값을 쓴다** — 원문이 근거다.

    건드리지 않는 경우(오탐을 막는 조건):
      - 근거 문구에 합성 표기가 없다.
      - 모델 값의 절대 금액이 원문 합성 금액과 일치한다(1% 허용, 반올림 표기 포함).
        원문과 추출 단위가 다르면 공통 환산표로 맞춰 본다(`1만 8,400 kg` = `18.4 ton`).
      - **모델 값이 근거 문구에 그대로 적혀 있다** — 같은 행의 다른 칸 수치이므로 옳다.
      - 원문 표기의 단위가 추출 단위와 **다른 물리량**이다(원 ↔ 시간, 원 ↔ 달러, kg ↔ m³).
        다른 수량이므로 후보도 아니다.
      - 후보가 둘 이상이거나, 하나라도 **같은 단위·같은 지표임을 입증하지 못했다**
        (단위 미상·라벨 불일치) → 값을 그대로 두고 사유만 남긴다(`scale_chain_unresolved`).

    `hint`를 주면 원문 표기 앞 라벨이 이 지표를 가리키는지까지 확인한다. 추출 경로
    (`_map_vlm_json`)는 항상 지표명을 넘긴다. 주지 않으면 라벨 대조를 생략한다.
    """
    from ..rag_gates.units import convert_to_common, normalize_unit, numeric_equal, units_compatible

    if not evidence or value == 0 or not math.isfinite(value):
        return value, None
    chains = _kr_scale_chains(evidence)
    if not chains:
        return value, None
    scale = _unit_scale(unit)
    absolute = value * scale
    value_unit = normalize_unit(unit or "")
    # 단위가 둘 다 알려져 있고 환산군이 다르면 다른 수량이다 — 후보에서 뺀다.
    # 추출 단위가 단순 단위인데 원문이 분모 있는 복합 단위(`MJ/톤`)면 역시 다른 수량이다
    # (총량 ↔ 원단위). 분모를 지워 같은 단위로 만들지 않는다.
    candidates = [chain for chain in chains
                  if not (value_unit and chain.unit and not units_compatible(value_unit, chain.unit))
                  and not (value_unit and chain.compound)]
    if not candidates:
        return value, None

    def in_value_unit(chain: _ScaleChain) -> float | None:
        if value_unit and chain.unit:
            converted = convert_to_common(chain.amount, chain.unit, value_unit)
            # 환산 계수의 이진 표현 잡음만 지운다(18,400 × 0.001 = 18.400000000000002).
            return float(f"{converted:.12g}") if converted is not None else None
        return None

    for chain in candidates:
        converted = in_value_unit(chain)
        if converted is not None and numeric_equal(value, converted):
            return value, None
        if converted is None and numeric_equal(absolute, chain.amount):
            return value, None
    if any(_contains_value(evidence, form) for form in _value_surface_forms(value)):
        return value, None
    # 다른 단위로 그대로 적힌 값(`재활용량 5,390 kg` → 5.39 ton)도 같은 행의 다른 칸이다.
    if any(numeric_equal(value, converted)
           for converted in _evidence_amounts_in_unit(evidence, unit)):
        return value, None

    def describe(chain: _ScaleChain) -> dict[str, Any]:
        return {"surface": chain.surface, "amount": chain.amount, "unit": chain.unit,
                "unit_text": chain.unit_text, "unit_status": chain.unit_status,
                "value_in_unit": in_value_unit(chain),
                "label": chain.label}

    detail = {"reason": "scale_chain_unresolved", "fatal": False,
              "value": value, "unit": unit,
              "source_amounts": sorted(chain.amount for chain in candidates),
              "candidates": [describe(chain) for chain in candidates],
              "quote": evidence[:_QUOTE_MAX_CHARS]}
    if len(candidates) != 1:
        # 실측 예: `9억 4,000만 달러 한도 … 중 8억 400만 달러를 분할 인출` — 모델 값
        # 840,000,000은 둘 다와 다르다. 어느 쪽으로 고칠지 문구만으로 정할 수 없으므로
        # 고치지 않고 사람이 보게 남긴다.
        return value, {**detail, "cause": "multiple_candidates"}
    chain = candidates[0]
    corrected = in_value_unit(chain)
    if corrected is None:
        # 원문 또는 추출 단위를 사전으로 읽지 못했다 — 단위를 추정해 채우지 않는다.
        return value, {**detail, "cause": "unit_unproven"}
    if chain.unit_status != "SIMPLE_COMPLETE" or value_unit is None:
        # 원문 단위를 끝까지 읽은 단순 단위일 때만 고친다(분모 표지·잘림·사전 밖은 제외).
        return value, {**detail, "cause": "unit_unproven"}
    if hint is not None and not _label_names_metric(chain.label, hint):
        # 같은 문장 안의 다른 지표일 수 있다(`영업이익 1조 2,000억 원 … 매출은`).
        return value, {**detail, "cause": "metric_unproven"}
    ratio = corrected / value
    if not 1e-4 <= abs(ratio) <= 1e4:
        # 자릿수 오류로 설명되지 않는 차이 — 임의로 바꾸지 않는다.
        return value, {**detail, "cause": "ratio_out_of_range"}
    return corrected, {"reason": "scale_chain_recomposed", "fatal": False,
                       "value_before": value, "value_after": corrected, "unit": unit,
                       "source_amount": chain.amount, "source_surface": chain.surface,
                       "source_unit": chain.unit, "source_unit_text": chain.unit_text,
                       "source_unit_status": chain.unit_status,
                       "source_label": chain.label,
                       "basis": "원문 연속 배율 표기를 같은 단위군으로 환산한 값",
                       "quote": evidence[:_QUOTE_MAX_CHARS]}


def _recover_quote(source_text: str, value: float, hint: str) -> str:
    """모델이 인용을 주지 않았을 때 **원문에서** 그 수치가 있는 줄을 찾아 되살린다.

    9/20 전량 추출물 실측에서 지표 4,063건 중 1,599건에 인용 문구가 없었다. 원인은
    모델이 quote를 비워 보내거나 원문과 다르게 적어 `source_quote`의 원문 대조에서
    떨어진 것이다. 인용이 없으면 사람이 원문을 되짚을 수 없고 `source_review`가 직접
    근거로 표시할 수도 없다.

    **지어내지 않는다.** 반환값은 항상 원문 한 줄 그대로이며, 그 줄은 보고된 값을
    숫자 경계까지 포함한다. 후보가 여럿이면 지표 라벨의 낱말로 좁히고, 그래도 하나로
    특정되지 않으면 **빈 문자열을 돌려준다** — 여러 행 중 하나를 임의로 고르면 잘못된
    근거가 만들어진다. 값이 여러 번 나오는 표가 흔하므로 이 보수 조건이 핵심이다.

    문맥 없는 조각은 인용으로 쓰지 않는다. 1차 구현에서는 숫자만 있는 줄('8')도
    채워졌는데, 그런 인용은 사람이 원문을 되짚는 데 아무 도움이 안 되면서 근거가
    있는 것처럼 보이게 한다(9/26 실측: 그런 식으로 1,378건이 채워졌다). 글자 낱말이
    한 개도 없는 줄은 후보에서 뺀다 — 표의 행 제목이나 설명이 함께 있어야 인용이다.
    """
    lines = [line.strip() for line in source_text.splitlines() if line.strip()]
    forms = _value_surface_forms(value)
    candidates = [
        line for line in lines
        if any(_contains_value(line, f) for f in forms)
        and _QUOTE_HINT_TOKEN_RE.search(re.sub(r"[\d.,%\s|()-]+", " ", line))
    ]
    if len(candidates) > 1 and hint:
        tokens = _QUOTE_HINT_TOKEN_RE.findall(hint)
        narrowed = [line for line in candidates if any(token in line for token in tokens)]
        if narrowed:
            candidates = narrowed
    if len(candidates) != 1:
        return ""
    return _quote_window(candidates[0], forms)


def _quote_window(line: str, forms: list[str]) -> str:
    """길이 상한을 지키면서 **보고된 값이 잘려 나가지 않도록** 잘라낸다.

    앞에서부터 상한만큼 자르면 값이 줄 뒤쪽에 있는 행에서 값이 사라진다(9/26 실측:
    복원분 1,378건 중 146건이 자기 값을 담지 않은 인용이었다). 값 위치를 기준으로
    창을 잡고, 잘린 쪽에는 생략 기호를 남겨 원문 일부임을 표시한다.
    """
    if len(line) <= _QUOTE_MAX_CHARS:
        return line
    position = next(
        (m.start() for m in (re.search(rf"(?<![\d.,]){re.escape(f)}(?![\d,]|\.\d)", line)
                             for f in forms) if m is not None),
        0,
    )
    start = max(0, min(position - _QUOTE_MAX_CHARS // 2, len(line) - _QUOTE_MAX_CHARS))
    end = start + _QUOTE_MAX_CHARS
    return ("…" if start > 0 else "") + line[start:end] + ("…" if end < len(line) else "")


# 표 칸 라벨 근거화(2026-10-05 §3). 모델 라벨이 칸의 열 머리와 다른 뜻을 말하면(대상 50명 → `교육 출석
# 인원`, 행 라벨만 남은 `합계` 세 칸) 원문 행 라벨·열 머리로 라벨을 다시 쓴다. 모델 라벨은 감사 기록으로 남긴다.
_CELL_NUMBER_RE = re.compile(r"(?<![\d.,])[+\-−]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\d.,])[+\-−]?\d+(?:\.\d+)?")
# 열 머리가 기간·열 역할·집계 축이면 라벨 문제가 아니다 — 기간·역할·범위 판정이 따로 읽는다.
# `값`·`수치`·`내용`처럼 뜻이 없는 일반 열 머리도 라벨에 보탤 것이 없다.
_ROLE_HEADER_RE = re.compile(r"실적|목표|계획|전망|예상|구분|단위|항목|비고|근거|기간|일자|날짜|^값$|^수치$|^내용$|^데이터$")


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


# 낱말 앞에 붙어 뜻을 뒤집는 접두(`미참석`·`비정규직`·`불참`·`무재해`).
_NEGATING_PREFIXES = frozenset("미비불무未非不無")


def _label_names_header(label: str, header: str, siblings: list[str]) -> bool:
    """라벨(공백 없앤 문구)이 열 머리 `header`를 **그 뜻으로** 담는가.

    PR71 검토 R6: `참석`이 `미참석` 안에 들어 있다는 이유로 46명(참석 칸)의 `교육 미참석 인원` 라벨을
    맞다고 보았다. 부분 문자열 일치로 보지 않는다 —
      - 같은 표의 다른 열 머리가 이 머리를 품으면(`참석` ⊂ `미참석`) 라벨의 그 부분은 다른 열의 말이다.
      - 머리 바로 앞이 뜻을 뒤집는 접두(`미`·`비`·`불`·`무`)면 반대말이다(표에 그 열이 없어도).
    """
    target = _compact(header)
    if len(target) < 2:
        return target == label
    masked = label
    for other in sorted({_compact(o) for o in siblings}, key=len, reverse=True):
        if other != target and target in other and other in masked:
            masked = masked.replace(other, " " * len(other))
    return any(m.start() == 0 or masked[m.start() - 1] not in _NEGATING_PREFIXES
               for m in re.finditer(re.escape(target), masked))


def _cell_number(cell: str) -> float | None:
    numbers = _CELL_NUMBER_RE.findall(cell)
    if len(numbers) != 1:
        return None
    try:
        return float(numbers[0].replace(",", "").replace("−", "-"))
    except ValueError:
        return None


def _table_cell_label(hint: str, value: float, quote: str, source_text: str | None) -> dict[str, str] | None:
    """값이 놓인 표 칸의 (행 라벨, 열 머리)로 모델 라벨을 검사한다. 고칠 필요가 없으면 None.

    - 인용의 표 행을 원문 쪽 텍스트에서 찾아(같은 행이 하나일 때만) 그 위 같은 표의 머리글로 열을 정한다
      (`_column_header` — 0 판정과 같은 머리글 확인 규칙).
    - 값이 그 행의 한 칸에만 있어야 한다. 열 머리가 기간·열 역할·집계 축(`2026`·`목표`·`국내`)이면 보지 않는다.
    - 라벨이 열 머리(또는 그 마지막 낱말)를 이미 담으면 그대로 둔다. 라벨이 행 라벨뿐이면 열 머리를 붙이고(`합계 · 발생량`),
      라벨이 행 라벨도 열 머리도 담지 않으면 원문 행 라벨·열 머리로 바꾼다(행 라벨이 없으면 표 제목 줄).
    """
    if not source_text or "|" not in quote:
        return None
    rows = [line for line in quote.splitlines() if _is_table_line(line)]
    target = None
    for line in rows:
        cells = [line[a:b] for a, b in _table_cells(line)]
        if sum(_cell_number(c) == value for c in cells) == 1:
            if target is not None:
                return None                # 인용 안에 같은 값의 칸이 여럿 — 칸을 특정하지 못한다
            target = line
    if target is None:
        return None
    lines = source_text.splitlines()
    matches = [i for i, line in enumerate(lines) if _compact(line) == _compact(target)]
    if len(matches) != 1:
        return None
    row_index = matches[0]
    context = "\n".join(lines[:row_index + 1])
    line = lines[row_index]
    line_start = len(context) - len(line)
    bounds = _table_cells(line)
    cells = [line[a:b] for a, b in bounds]
    hits = [k for k, cell in enumerate(cells) if _cell_number(cell) == value]
    if len(hits) != 1:
        return None
    k = hits[0]
    header, state, _ = _column_header(context, line_start + bounds[k][0], max(1, bounds[k][1] - bounds[k][0]))
    core = re.sub(r"[(（][^)）]*[)）]", " ", header).strip()
    if state not in ("header", "cell") or not core or _date_spans(core) or _ROLE_HEADER_RE.search(core) \
            or any(word == core for word in _COL_HEADER_KEYWORDS) or re.search(r"\d", core):
        return None
    row_label = " ".join(c.strip() for c in cells[:k] if c.strip() and _cell_number(c) is None).strip()
    label = _compact(hint)
    head = core.split()[-1]
    # 같은 머리글 행의 다른 열 머리 — 라벨이 어느 열의 말을 담았는지 가른다(`참석` ↔ `미참석`).
    siblings = [re.sub(r"[(（][^)）]*[)）]", " ", _column_header(context, line_start + a, max(1, b - a))[0]).strip()
                for j, (a, b) in enumerate(bounds) if j != k]
    sibling_heads = [o.split()[-1] for o in siblings if o.split()]
    if _label_names_header(label, core, siblings + sibling_heads) \
            or (len(head) >= 2 and _label_names_header(label, head, siblings + sibling_heads)):
        return None                        # 열 머리(또는 그 중심어 `미참석`·`비율`)를 그 뜻으로 이미 담았다
    # 정정 사유: 라벨이 같은 표의 다른 열을 말함 / 열 머리의 반대말 / 열 머리를 담지 않음.
    named = [o for o in dict.fromkeys(siblings) if o and _label_names_header(label, o, [core, *siblings])]
    if named:
        reason = "label_names_other_column"
    elif _compact(head) in label or _compact(core) in label:
        reason = "label_negates_column"
    else:
        reason = "label_lacks_column"
    if row_label and label == _compact(row_label):
        new = f"{row_label} · {core}"
        rule = "row_label_plus_column_header"
    else:
        if row_label and _compact(row_label) in label:
            new = f"{hint} · {core}"
            rule = "label_plus_column_header"
        else:
            caption = ""
            if not row_label:
                above = next((lines[j] for j in range(row_index - 1, -1, -1)
                              if lines[j].strip() and not _is_table_line(lines[j])), "")
                if above and len(above.strip()) <= 30 and not re.search(r"\d", above):
                    caption = above.strip()
            new = " · ".join(part for part in (row_label or caption, core) if part)
            rule = "source_row_and_column"
    return {"label": new, "model_label": hint, "row_label": row_label, "column_header": header.strip(),
            "rule": rule, "label_reason": reason, "label_named_columns": named,
            "source_row": line.strip()[:_QUOTE_MAX_CHARS], "source_cell": cells[k].strip()}


def _negation_zero_from_null(m: dict[str, Any], source_text: str | None, source_quote) -> tuple[dict, Any] | None:
    """값을 비운 모델 행을 원문의 명시적 부정 서술로 0 복원할 수 있으면 (행, 판정)을 돌려준다.

    조건: 원문과 대조된 인용이 있고, 0 판정이 **부정 서술 후보**(`미보유`·`발생하지 않았다`)로 원문 사실을
    보존(CONFIRMED·SOURCE_ONLY)한다. 숫자 0 칸만 있거나(빈 칸·`-`와 섞일 수 있다) 미확인·해당 없음·목표면 복원하지 않는다.
    복원한 행의 경계에는 원문 범위와 `value_recovery` 출처(모델 값 없음)를 싣는다.
    """
    if source_text is None:
        return None
    quote = source_quote(m.get("quote"))
    if not quote:
        return None
    hint = str(m.get("metric_hint") or "")
    hint, period = _split_hint_year(hint, str(m.get("period") or ""))
    boundary = m.get("boundary") if isinstance(m.get("boundary"), dict) else {}
    verdict = _zero_verdict(quote, hint, period, boundary, str(m.get("unit") or ""), definitions=source_text)
    if not verdict.accepted or verdict.candidate != "negation":
        return None
    b = dict(boundary)
    b["provenance"] = [*(b.get("provenance") or []),
                       {"source": "value_recovery", "method": "rule", "model_value": None,
                        "rule": "explicit_negation_in_quote", "evidence": verdict.evidence_text[:_QUOTE_MAX_CHARS]}]
    return {**m, "value": 0, "boundary": b}, verdict


def _source_quantity_site_boundary(boundary: dict[str, Any], quote: str, source_text: str | None,
                                   page: int | None) -> dict[str, Any]:
    """원문 수량 인용의 사업장을 같은 청크의 앞 머리말에서만 보완한다.

    인용이 원문에서 유일하게 위치해야 한다. 인용 자체의 사업장을 우선하고, 앞 머리말을 쓰려면 중간에
    다른 사업장 표기가 없어야 한다. 뒤 문장·다른 페이지·요청 사업장에서 범위를 가져오지 않는다.
    """
    from ..report_claims import _heading_line, refine_local_sites, site_scope_transition, sites_compatible
    if not quote or not source_text:
        return boundary
    compact_quote = re.sub(r"\s+", "", quote)
    positions = [i for i, ch in enumerate(source_text) if not ch.isspace()]
    compact_source = "".join(source_text[i] for i in positions)
    at = compact_source.find(compact_quote)
    if at < 0 or compact_source.find(compact_quote, at + 1) >= 0:
        return boundary
    site_keys = _site_keys(quote)
    evidence, scope_from = quote, "quote"
    if not site_keys or all(key[:1].isdigit() for key in site_keys):
        encountered = []
        # 같은 실제 청크에서 인용보다 앞에 있는 가장 가까운 사업장 머리말만 사용한다.
        for line in reversed(source_text[:positions[at]].splitlines()):
            transition = site_scope_transition(line)
            if transition:
                if site_keys:
                    break                 # 인용 자체의 짧은 사업장은 유지하되 이전 절 지역을 붙이지 않는다.
                record = {"source": "quantity_scope", "method": "rule", "scope_from": "scope_transition",
                          "source_site": "", "site_scope": transition, "scope_heading": line.strip(),
                          "quote": quote, "page": page, "model_site": str(boundary.get("site") or "")}
                return {**boundary, "site": "", "site_scope": transition,
                        "provenance": [*(boundary.get("provenance") or []), record]}
            keys = _site_keys(line)
            if not keys:
                continue
            if _heading_line(line):
                if any(sites_compatible(frozenset(keys), frozenset(other)) is False for other in encountered):
                    return boundary
                refined = refine_local_sites(frozenset(site_keys), frozenset(keys))
                if refined != frozenset(site_keys):
                    site_keys, evidence, scope_from = set(refined), line.strip(), "heading"
                break
            encountered.append(keys)
    if len(site_keys) != 1:
        return boundary
    mentions = [m.group(0).strip() for m in _SITE_MENTION_RE.finditer(evidence)
                if _site_keys(m.group(0)) == site_keys]
    if not mentions:
        return boundary
    site = mentions[0]
    record = {"source": "quantity_scope", "method": "rule", "scope_from": scope_from,
              "source_site": site, "scope_heading": evidence if scope_from == "heading" else "",
              "quote": quote, "page": page, "model_site": str(boundary.get("site") or "")}
    return {**boundary, "site": site, "site_scope": "site",
            "provenance": [*(boundary.get("provenance") or []), record]}


def _map_vlm_json(
    data: dict[str, Any], *, page_no: int | None = None, source_text: str | None = None,
    issues: list[dict[str, Any]] | None = None,
) -> tuple[list[ExtractedMetric], list[ExtractedClause]]:
    """LLM 응답을 매핑하되 페이지는 실제 호출 입력에서만 부여한다."""
    metrics: list[ExtractedMetric] = []
    clauses: list[ExtractedClause] = []

    def source_quote(value: Any) -> str:
        quote = str(value or "")
        if source_text is None:
            return ""  # 모델이 작성한 인용을 원문 검증 없이 증빙으로 승격하지 않는다.
        compact = re.sub(r"\s+", "", quote)
        return quote if compact and compact in re.sub(r"\s+", "", source_text) else ""

    for index, m in enumerate(data.get("metrics", [])):
        if not isinstance(m, dict):
            if issues is not None:
                issues.append({"record_type": "metric", "record_index": index,
                               "reason": "not_an_object", "fatal": True})
            continue
        if m.get("value") is None and str(m.get("metric_hint") or "").strip():
            # 라벨은 읽혔지만 수치가 그래픽·빈 칸에 있어 모델이 값을 보고하지 못한 행이다.
            # (실측: 현대모비스 2025 p.16 '젠더 다양성(여성 비율)' 등 3건)
            # 모델이 숫자를 만들어내지 않고 없다고 답한 정직한 응답이므로 깨진 응답과 같이
            # 취급하지 않는다. 0으로 채우지도 않고 미확인으로 버리며 사유만 남긴다.
            # 단, 원문 인용이 같은 지표의 **명시적 미보유·미발생 서술**이면(2026-10-05 한울정밀 11:
            # `2026-04-30 기준 ISMS 인증 미보유`) 그 서술이 0의 근거다 — 0 판정이 보존할 때만 0으로 싣고
            # 모델의 빈 값은 감사 기록으로 남긴다. 숫자 0 칸·빈 칸·미확인 서술에서는 0을 만들지 않는다.
            recovered = _negation_zero_from_null(m, source_text, source_quote)
            if recovered is None:
                if issues is not None:
                    issues.append({"record_type": "metric", "record_index": index,
                                   "reason": "value_not_reported", "fatal": False,
                                   "metric_hint": str(m.get("metric_hint") or "")})
                continue
            m, verdict = recovered
            if issues is not None:
                issues.append({"record_type": "metric", "record_index": index,
                               "reason": "zero_recovered_from_negation", "fatal": False,
                               "metric_hint": str(m.get("metric_hint") or ""), "model_value": None,
                               "value": 0, "unit": str(m.get("unit") or ""), "period": str(m.get("period") or ""),
                               "status": verdict.status, "cause": verdict.cause, "page": page_no,
                               "quote": str(m.get("quote") or "")[:_QUOTE_MAX_CHARS],
                               "evidence_text": verdict.evidence_text[:_QUOTE_MAX_CHARS]})
        try:
            hint = str(m.get("metric_hint") or "")
            value = float(m["value"])
            if not hint or isinstance(m["value"], bool) or not math.isfinite(value):
                raise ValueError("metric hint and finite source value are required")
            if _is_footnote_marker_value(hint, value):
                continue  # G6: 각주 마커('재해율 4)')를 값(4.0)으로 오파싱한 노드 배제
            # 2단 헤더 라벨이 hint에 통째로 들어온 경우 연도를 period로 되돌린다.
            hint, period = _split_hint_year(hint, str(m.get("period") or ""))
            unit = str(m.get("unit") or "")
            # 모델 인용이 원문 대조를 통과하지 못하면 원문에서 되살린다(유일할 때만).
            quote = (source_quote(m.get("quote"))
                     or (_recover_quote(source_text, value, hint) if source_text else ""))

            # 근거 문구가 있으면 값을 그 문구와 대조한다. 문구가 없으면 대조할 수 없으므로
            # 아무것도 단정하지 않고 그대로 둔다(추측으로 값을 바꾸지 않는다).
            if quote:
                boundary = m.get("boundary") if isinstance(m.get("boundary"), dict) else {}
                zero = (_zero_verdict(quote, hint, period, boundary, unit, definitions=source_text or "")
                        if value == 0 else None)
                if zero is not None and not zero.accepted:
                    # 실측 결함: 원문 칸이 `-`(미공시)인데 0으로 실렸다(삼성전기 4건 —
                    # 유동성장기차입금·장기차입금·지역전문가·Category 9). 자기 근거 문구에
                    # 0도, 같은 지표의 명시적 미발생·미보유 서술도 없으면 그 0은 원문에서 온
                    # 값이 아니다. **실제 0과 미확인은 다르다** — 차입금 '0원'과 '미공시'는
                    # 전혀 다른 문장을 만든다. 그래서 0으로 싣지 않고 사유만 남긴다.
                    # 판정 상태·근거 서술·쪽·모델 원값을 함께 남긴다(쪽 0과 쪽 미상 None은 다르다).
                    if issues is not None:
                        issues.append({"record_type": "metric", "record_index": index,
                                       "reason": "zero_not_in_evidence", "fatal": False,
                                       "cause": zero.cause, "status": zero.status,
                                       "metric_hint": hint, "value": value, "unit": unit,
                                       "period": period, "page": page_no,
                                       "requested_scope": zero.requested_scope,
                                       "metric_site": str(boundary.get("site") or ""),
                                       "evidence_period": zero.evidence_period,
                                       "evidence_site": zero.evidence_site,
                                       "evidence_text": zero.evidence_text[:_QUOTE_MAX_CHARS],
                                       **_zero_trace(zero),
                                       "quote": quote[:_QUOTE_MAX_CHARS]})
                    continue
                if zero is not None:
                    # 보존한 0은 **원문 범위**를 경계에 싣는다. 모델이 연도만 보냈어도 원문이 4월이면
                    # 이 근거의 기간은 4월이다 — 후속 원장이 연간 실적으로 넓히지 않게 한다.
                    m = {**m, "boundary": _zero_scope_boundary(boundary, zero, period)}
                value, scale_issue = _reconcile_scale_chain(value, unit, quote, hint=hint)
                if scale_issue is not None and issues is not None:
                    issues.append({"record_type": "metric", "record_index": index,
                                   "metric_hint": hint, "period": period, "page": page_no,
                                   **scale_issue})
                elif (issues is not None and value != 0
                      and not _value_written_in_evidence(value, unit, quote)):
                    # 0은 위에서 이미 숫자 0 또는 같은 지표의 명시적 부정 서술로 확인했다.
                    # 근거 문구에 그 숫자가 없다. 값이 틀렸다는 뜻은 아니다 — 모델이 서술을
                    # 수치로 옮긴 경우가 대부분이다(실측: "교육 대상 임직원 전원이 수료" →
                    # 100%, "최대 2년 6개월" → 2.5년). 값은 살리되 **사람이 인용만 보고는
                    # 확인할 수 없다**는 사실을 남긴다. 값을 지우거나 바꾸지 않는다.
                    issues.append({"record_type": "metric", "record_index": index,
                                   "reason": "value_not_written_in_evidence", "fatal": False,
                                   "metric_hint": hint, "value": value, "unit": unit,
                                   "period": period, "page": page_no,
                                   "quote": quote[:_QUOTE_MAX_CHARS]})

            # 원문 값의 적용 사업장을 모델의 생략과 무관하게 전달한다. 캐시 적중에서도 재실행한다.
            if value != 0 and quote:
                m = {**m, "boundary": _source_quantity_site_boundary(
                    dict(m.get("boundary") or {}), quote, source_text, page_no)}
            relabel = _table_cell_label(hint, value, quote, source_text) if quote else None
            if relabel is not None:
                # 원문 칸의 행 라벨·열 머리가 모델 라벨과 다르다 — 원문 라벨로 싣고 모델 라벨은 감사 기록으로 둔다.
                b = dict(m.get("boundary") or {})
                b["provenance"] = [*(b.get("provenance") or []),
                                   {"source": "label_check", "method": "rule", **relabel}]
                m = {**m, "boundary": b}
                if issues is not None:
                    issues.append({"record_type": "metric", "record_index": index,
                                   "reason": "label_from_table_header", "fatal": False,
                                   "metric_hint": relabel["label"], "page": page_no, "value": value,
                                   "unit": unit, "period": period, "quote": quote[:_QUOTE_MAX_CHARS],
                                   **{k: relabel[k] for k in ("model_label", "row_label", "column_header", "rule",
                                                              "label_reason", "source_cell")}})
                hint = relabel["label"]
            metrics.append(ExtractedMetric(
                metric_hint=hint,
                value=value,
                unit=unit,
                period=period,
                kesg_code_guess=str(m.get("kesg_code") or "") or None,
                confidence=0.75,   # VLM 추출 기본 신뢰도
                # `page_no`는 호출부가 넘기는 **0-기준 실제 청크 페이지**다(기본값 None).
                # main 쪽 `page_no - 1`은 기본값 1(1-기준)에 맞춘 보정이었고, 실제 페이지가
                # 들어오는 이 경로에서 그대로 두면 off-by-one이 된다.
                page=page_no,
                page_source="chunk" if page_no is not None else "",
                quote=quote,
                boundary=m.get("boundary") or {},
            ))
        except (KeyError, TypeError, ValueError) as exc:
            if issues is not None:
                issues.append({"record_type": "metric", "record_index": index,
                               "reason": str(exc), "fatal": True})
            continue

    for index, c in enumerate(data.get("clauses", [])):
        if not isinstance(c, dict) or not isinstance(c.get("text"), str) or not c["text"].strip():
            if issues is not None:
                issues.append({"record_type": "clause", "record_index": index,
                               "reason": "missing_text", "fatal": True})
            continue
        try:
            clauses.append(ExtractedClause(
                section=str(c.get("section", "")),
                text=str(c.get("text", "")),
                kesg_code_guess=str(c.get("kesg_code") or "") or None,
                page=page_no,
                page_source="chunk" if page_no is not None else "",
                quote=source_quote(c.get("quote") or c.get("text")),
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
            quote=matched[0],
        ))
    return augmented


def _mock_unstructured(file_path: str, doc_type: str) -> OcrExtraction:
    """API 키 없을 때 데모용 Mock 반환."""
    source_file = Path(file_path).name
    _mock_meta: dict = {"mock": True, "raw_text_source": "mock", "raw_text_len": 0}

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
            router_meta=_mock_meta,
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
            router_meta=_mock_meta,
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
            router_meta=_mock_meta,
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
