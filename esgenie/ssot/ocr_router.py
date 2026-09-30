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

# Upstage Document Parse 모델 pin.
# 구버전 alias "document-parse"(=document-parse-250618 계열)는 2026-07-31 지원 종료 —
# 신버전을 명시 pin하고, 롤백·비교 실험은 UPSTAGE_DP_MODEL 환경변수로 오버라이드한다.
UPSTAGE_DP_MODEL: str = os.getenv("UPSTAGE_DP_MODEL", "document-parse-260630")

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

    from ..knowledge.kesg_items import _normalize_label, resolve_kesg_code

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
    resolved: list[dict[str, Any]] = []
    for m in ext.metrics:
        if m.kesg_code_guess:
            continue
        code, score, method = resolve_kesg_code(m.metric_hint)
        if not code or method != "exact":
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
        label = {"kepco_bill": "사용전력량", "gas_bill": "도시가스 사용열량", "waste_ledger": "총 위탁량"}.get(doc_type, code)
        metrics.append(ExtractedMetric(
            metric_hint=label, value=round(val, 3), unit=unit,
            period="", kesg_code_guess=code, bbox=bbox, page=page, confidence=0.92,
        ))
    return metrics


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
      data : model=UPSTAGE_DP_MODEL(기본 document-parse-260630), ocr=force|auto, output_formats=['html','text'],
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
    "scale_chain_recomposed", "scale_chain_unresolved", "value_not_written_in_evidence"})
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
    compound: bool = False  # 분모가 있는 복합 단위(원단위). 단순 단위와 같은 수량이 아니다


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
                                      unit_text=unit_read.text, compound=unit_read.compound))
    return chains


@dataclass(frozen=True)
class _UnitRead:
    """수치 뒤 단위 표현을 읽은 결과. 정규화에 실패해도 원문 표현은 남긴다."""
    text: str              # 원문 단위 표현 전체(조사 제외). 없으면 ""
    unit: str | None       # 정규 표기. 복합 단위·사전 밖 표기는 None
    compound: bool = False


# 수치 뒤 단위 한 낱말. 영문 뒤의 숫자·지수(`m3`·`m^3`·`tCO2eq`)는 단위의 일부로 읽는다.
# 한글 뒤 숫자(`원4,073억`)는 다음 수치의 시작이므로 끊는다.
_UNIT_WORD = r"(?:[A-Za-z]+(?:\^?\d+)?|[^\sA-Za-z\d.,;:!?'\"|()\[\]{}<>·/+=~\-])+"
# 분모까지 한 표현으로 읽는다(`MJ/톤`·`MJ / 톤`). `/`에서 끊으면 원단위가 총량이 된다
# (PR 69 2차 검토: `1만 2천 MJ/톤`을 `MJ`로 읽어 에너지 총량 2.5 GJ를 12 GJ로 덮어썼다).
_UNIT_EXPR_RE = re.compile(rf"[ \t]*({_UNIT_WORD})(?:[ \t]*/[ \t]*({_UNIT_WORD}))?")
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
    """`end` 바로 뒤 단위 표현을 **전체로** 읽는다. 사전에 없으면 unit=None — 추정해 채우지 않는다.

    분모가 있는 복합 단위(`MJ/톤`·`원/명`·`kg/일`)는 이 모듈이 환산하지 않는다. 분자만 남겨
    `MJ`·`원`·`kg`로 읽으면 원단위가 총량과 같은 수량이 되므로, 정규화하지 않고 `compound`로
    표시해 둔다.
    """
    match = _UNIT_EXPR_RE.match(text, end)
    if not match:
        return _UnitRead("", None)
    numerator, denominator = match.group(1), match.group(2)
    if denominator:
        return _UnitRead(f"{numerator}/{_strip_unit_particle(denominator)}", None, compound=True)
    return _UnitRead(_strip_unit_particle(numerator), _normalize_unit_word(numerator))


def _trailing_unit(text: str, end: int) -> str | None:
    """`end` 바로 뒤 단위의 정규 표기. 복합 단위·사전 밖 표기는 None."""
    return _read_unit(text, end).unit


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
    """근거 문구의 (수, 뒤 단위) 쌍을 `unit`으로 환산한 값. 환산할 수 없는 쌍은 뺀다."""
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
    pairs += [(chain.amount, chain.unit) for chain in _kr_scale_chains(text)]
    converted = [convert_to_common(amount, source_unit, target)
                 for amount, source_unit in pairs if source_unit is not None]
    return [value for value in converted if value is not None]


def _zero_is_grounded(quote: str, hint: str = "", period: str = "",
                      boundary: dict[str, Any] | None = None) -> bool:
    """보고된 0이 **그 근거 문구에 실제로 적혀 있는가.** 사유는 `_zero_verdict`가 돌려준다.

    실측 결함(삼성전기 4건): 원문 칸이 `-`(미공시)인데 값 0으로 실렸다. 자기 근거 문구에
    0이 한 번도 없으면 그 0은 원문에서 온 값이 아니다. 두 문서 지표 7,238건에 이 판정을
    적용했을 때 **실제 0을 잘못 걸러낸 경우는 0건**이었다(0값 354건 중 4건만 걸렸다).

    숫자 0 말고도 **같은 지표의 명시적 미발생·미보유 서술**은 0의 근거다(PR 69 검토,
    2026-09-30: 한울정밀 11번 `ISMS 인증 미보유`의 0건이 지워지고 '수치를 읽지 못함'
    안내가 떴다). 판정은 `_negation_states_zero`가 한다.
    """
    return _zero_verdict(quote, hint, period, boundary).accepted


@dataclass(frozen=True)
class _ZeroVerdict:
    """0을 채택할지와 그 사유. 채택하지 않은 0은 사유와 원문 범위를 확인 목록에 넘긴다."""
    accepted: bool
    cause: str                    # `_ZERO_CAUSES`의 키
    evidence_period: str = ""     # 대조한 원문의 기간 표기
    evidence_site: str = ""       # 대조한 원문의 사업장 표기


# 채택하지 않은 0의 하위 사유. 확인 목록이 사유별로 다르게 설명한다(`source_review`).
_ZERO_CAUSES = frozenset({
    "no_zero_statement",        # 0도, 미발생·미보유 서술도 없다(미공시 `-`·빈 칸 포함)
    "other_subject",            # 부정 서술의 대상이 이 지표가 아니다
    "future_or_intent",         # 미래 예상·목표·계획·가능성
    "not_confirmed",            # 미확인·미집계·미공시·해당 없음·기록 없음
    "evidence_insufficient",    # '발생하지 않았다는 증거가 없다'처럼 미발생의 근거가 없다는 말
    "negation_negated",         # '미보유 상태가 아니다'처럼 부정을 다시 부정했다
    "interpretation_unknown",   # 사실 서술인지 판정하지 못했다
    "period_mismatch",          # 원문 기간이 지표 기간과 명시적으로 다르다
    "period_unproven",          # 기간이 겹치지만 같은 범위임을 확인하지 못했다
    "site_mismatch",            # 원문 사업장이 지표 사업장과 다르다
})


def _zero_verdict(quote: str, hint: str = "", period: str = "",
                  boundary: dict[str, Any] | None = None) -> _ZeroVerdict:
    boundary = boundary or {}
    if any(_contains_value(quote, form) for form in _value_surface_forms(0.0)):
        # 숫자 0도 **명시적으로 다른** 기간·사업장이면 이 지표의 0이 아니다. 날짜·표 번호 속
        # 0(`2026-04-01`)은 `_contains_value`의 숫자 경계가 이미 뺀다 — 그 0으로 아래 범위
        # 대조를 건너뛰지 않는다.
        spans = _date_spans(quote)
        if _period_relation(period, spans) == "mismatch":
            return _ZeroVerdict(False, "period_mismatch", _spans_text(spans))
        sites = _site_keys(quote)
        if not _site_matches(boundary, sites):
            return _ZeroVerdict(False, "site_mismatch", evidence_site=", ".join(sorted(sites)))
        return _ZeroVerdict(True, "stated_zero")
    if not hint:
        return _ZeroVerdict(False, "no_zero_statement")
    return _negation_states_zero(quote, hint, period, boundary)


# 실제로 없었다·갖고 있지 않다는 서술의 **어근**. 사실인지는 뒤 어미로 따로 판정한다.
_ZERO_NEGATION_RE = re.compile(
    r"미발생|미보유|미취득|(?:발생|보유|취득)(?:하지|되지)\s*않|없(?=[었음다으습고을는도게기])")
# 목표·계획 표현. 부정 서술 **앞이나 그 어절 안에** 있으면 완료된 사실이 아니다.
_ZERO_INTENT_RE = re.compile(r"않도록|없도록|목표|계획|예정")
# 0이 아니라 **모른다**·**해당 없다**는 서술. 부정 서술을 포함한 서술 안 어디에 있어도 막는다.
_ZERO_UNCONFIRMED_RE = re.compile(
    r"해당\s*(?:사항\s*)?없|자료\s*없|기록\s*없|정보\s*없"
    r"|확인(?:하지|되지|할\s*수)\s*(?:않|없)|미확인|미집계|집계(?:하지|되지)\s*않|미공시"
    r"|공시(?:하지|되지)\s*않|파악(?:하지|되지)\s*않|알\s*수\s*없")
# 미발생의 **근거가 없다**는 서술. `없다`의 대상이 산업재해가 아니라 증거다.
_ZERO_NO_PROOF_RE = re.compile(
    r"(?:증거|근거|입증|증빙)\S*\s*(?:없|부족)|(?:단정|판단|보기)\S*\s*(?:어렵|힘들|할\s*수\s*없)")
# 부정 어근 뒤 어미. 미래·목표(`않을`·`않도록`·`않기를`·`않겠`)는 완료된 미발생이 아니다.
_ZERO_FUTURE_TAIL_RE = re.compile(r"(?:았|었)?(?:도록|게|기를|기로|고자|으려|려고|려는|겠|을)")
# 인용·명사절로 안긴 부정(`않았다는`·`않았음을`·`않았는지`). 참·거짓은 뒤 서술어가 정한다.
_ZERO_EMBED_TAIL_RE = re.compile(r"(?:았|었)?(?:다는|다고|는지|은지|음을|음이|음은|음도|음에)")
# 관형형(`않은`·`없는`). 뒤 명사에 걸리므로 그 자체로는 사실 서술이 아니다.
_ZERO_ADNOMINAL_TAIL_RE = re.compile(r"(?:았|었)?(?:은|는|던)")
_ZERO_CONFIRMED_RE = re.compile(r"확인(?:했|하였|됐|되었|됨|함|한다|된다)|검증(?:했|하였|됐|되었|됨)")
_ZERO_DOUBT_RE = re.compile(r"없|않|어렵|불확실|모르|못|불명")
# 한 절 안의 서술 경계. 연결 어미(`미보유이며`·`않았고`·`있으나`) 뒤는 다른 서술이다 —
# `미보유이며 향후 취득을 계획`의 `계획`은 현재 미보유에 걸리지 않는다.
# 인용 어미(`않았다고`·`않았다며`)는 경계가 아니다 — 뒤 서술어가 그 부정의 참·거짓을 정한다.
_ZERO_STATEMENT_SPLIT_RE = re.compile(
    r"(?:(?<![다라]며)(?<=며)|(?<![다라]고)(?<=고)|(?<=으나)|(?<=지만)|(?<=는데)|(?<=면서))\s+")
_ZERO_CLAUSE_SPLIT_RE = re.compile(r"[,;\n]|(?<!\d)\.(?!\d)")
# 지표명에서 무엇을 셌는지 알려 주지 않는 낱말. 이것만 겹쳐서는 같은 지표라 할 수 없다
# (`환경 사고는 발생하지 않았다`가 `산업재해 발생 건수`의 0이 되면 안 된다).
_ZERO_GENERIC_TOKENS = frozenset({
    "발생", "건수", "여부", "상태", "현황", "보유", "횟수", "실적", "누적", "전체", "연간",
    "기준", "인원", "수준", "비율", "총계", "합계"})
# 지표 낱말과 부정 서술 사이에 와도 되는 말(`산업재해가 한 건도 발생하지 않았다`).
_ZERO_FILLER_TOKENS = frozenset({"전혀", "건도", "일체", "모두", "기간", "동안", "현재", "당해"})


def _negation_states_zero(quote: str, hint: str, period: str, boundary: dict[str, Any]) -> _ZeroVerdict:
    """근거 문구의 한 서술이 **이 지표·기간·사업장**에 대해 없었다·보유하지 않았다고 말하는가.

    서술마다 순서대로 본다(모두 통과해야 채택):
      1. 대상 — 부정 어근 앞에 지표명의 구체 낱말(`_ZERO_GENERIC_TOKENS` 제외)이 모두 있고,
         마지막 지표 낱말과 부정 사이에 다른 대상 낱말이 없다(`산업재해 예방교육은 없었다`).
      2. 사실성 — 부정 어근을 찾은 것만으로 인정하지 않는다. 앞말과 뒤 어미를 본다:
         목표·계획(`않도록`·`않을 것이다`), 미확인·미집계, 근거 부재(`않았다는 증거가 없다`),
         이중 부정(`미보유 상태가 아니다`)은 채택하지 않는다. 판정할 수 없으면 채택하지 않는다.
      3. 기간 — 원문 기간이 지표 기간과 같은 범위다(`_period_relation`). 다른 범위의 0으로
         이 범위의 0을 추론하지 않는다.
      4. 사업장 — 원문 사업장이 지역·번호까지 지표 사업장과 같다(`_site_matches`).
    여러 서술이 모두 떨어지면 **대상이 맞은 서술의 첫 사유**를 돌려준다.
    """
    hint_tokens = _label_tokens(hint)
    specific = [t for t in hint_tokens if t not in _ZERO_GENERIC_TOKENS]
    if not specific:
        return _ZeroVerdict(False, "no_zero_statement")
    allowed = set(hint_tokens) | _ZERO_GENERIC_TOKENS | _ZERO_FILLER_TOKENS
    found_negation, rejected = False, None
    for clause in _ZERO_CLAUSE_SPLIT_RE.split(quote):
        statements = _ZERO_STATEMENT_SPLIT_RE.split(clause)
        for position, statement in enumerate(statements):
            # 서술에 기간·사업장이 없으면 문구 전체에서 찾되, 같은 절의 **다른 서술**(연결 어미
            # 뒤 `2027년 취득을 목표로`)의 날짜·사업장은 빼고 본다.
            context = quote.replace(clause, " ".join(statements[:position]))
            for negation in _ZERO_NEGATION_RE.finditer(statement):
                found_negation = True
                head = statement[:negation.start()]
                if not _names_zero_subject(head, specific, hint_tokens, allowed):
                    continue
                cause = _negation_mood(statement, negation)
                if cause is None:
                    spans = _date_spans(statement) or _date_spans(context)
                    relation = _period_relation(period, spans)
                    if relation in ("mismatch", "unproven"):
                        cause = "period_mismatch" if relation == "mismatch" else "period_unproven"
                        rejected = rejected or _ZeroVerdict(False, cause, _spans_text(spans))
                        continue
                    sites = _site_keys(statement) or _site_keys(context)
                    if not _site_matches(boundary, sites):
                        rejected = rejected or _ZeroVerdict(
                            False, "site_mismatch", evidence_site=", ".join(sorted(sites)))
                        continue
                    return _ZeroVerdict(True, "stated_zero", _spans_text(spans),
                                        ", ".join(sorted(sites)))
                rejected = rejected or _ZeroVerdict(False, cause)
    if rejected is not None:
        return rejected
    return _ZeroVerdict(False, "other_subject" if found_negation else "no_zero_statement")


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
    # 날짜·한 글자 말(`4월`·`한`)은 `_label_tokens`가 이미 뺀다.
    return not any(word not in allowed and not any(token in word for token in hint_tokens)
                   for word in _label_tokens(gap))


def _negation_mood(statement: str, negation: re.Match[str]) -> str | None:
    """부정 서술이 완료된 사실·현재 상태면 None, 아니면 채택하지 않는 사유."""
    tail_end = negation.end()
    while tail_end < len(statement) and not statement[tail_end].isspace():
        tail_end += 1
    tail = statement[negation.end():tail_end]          # 부정 어근 뒤 같은 어절의 어미
    before, rest = statement[:tail_end], statement[tail_end:]
    if _ZERO_UNCONFIRMED_RE.search(statement):
        return "not_confirmed"
    if _ZERO_NO_PROOF_RE.search(rest):
        return "evidence_insufficient"
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
    return None


# ── 0의 기간·사업장 대조 (PR 69 2차 검토) ─────────────────────────────────────
# 연도 집합만 비교하면 `2026년 3월` 미발생이 4월 0건의 근거가 됐다. 기준일·월·연도·구간을
# 실제 범위로 읽어 **같은 범위일 때만** 인정한다. `measurement_context.period_bounds`는
# 단일 날짜를 읽지 못하고 `site_path`는 지역명을 떼므로 이 경로 전용으로 둔다.

@dataclass(frozen=True)
class _DateSpan:
    start: Any       # datetime.date
    end: Any
    grain: str       # day | month | year | range
    text: str


_DATE_ISO_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})[-./](\d{1,2})(?:[-./](\d{1,2}))?(?![\d])")
_DATE_KR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*년(?:\s*(\d{1,2})\s*월(?:\s*(\d{1,2})\s*일)?)?")
# 연도만 적은 표기. 수량(`2000명`)은 연도로 읽지 않는다.
_DATE_YEAR_RE = re.compile(
    r"(?<![\d.,])((?:19|20)\d{2})(?![\d.,]|\s*(?:명|건|원|개|대|톤|kg|회|시간|%))")
_RANGE_JOIN_RE = re.compile(r"\s*(?:~|∼|〜|–|—|-|부터)\s*")
# 구간의 뒤쪽이 연·월을 생략한 표기(`2026-04-01~04-30`·`4월 1일부터 30일까지`).
_RANGE_PARTIAL_RE = re.compile(r"(?:(\d{1,2})[-./](\d{1,2})|(?:(\d{1,2})\s*월\s*)?(\d{1,2})\s*일)(?![\d])")


def _date_spans(text: str) -> list[_DateSpan]:
    """문구의 날짜 표기를 실제 범위로 읽는다. 구간(`A ~ B`)은 하나로 합친다."""
    import calendar
    from datetime import date

    text = str(text or "")
    tokens: list[tuple[int, int, int, int | None, int | None]] = []
    taken: list[tuple[int, int]] = []
    for pattern in (_DATE_ISO_RE, _DATE_KR_RE, _DATE_YEAR_RE):
        for m in pattern.finditer(text):
            if any(s < m.end() and m.start() < e for s, e in taken):
                continue
            groups = m.groups() + (None, None)
            tokens.append((m.start(), m.end(), int(groups[0]),
                           int(groups[1]) if groups[1] else None, int(groups[2]) if groups[2] else None))
            taken.append((m.start(), m.end()))
    tokens.sort()

    def span(y, mo, d, start, end) -> _DateSpan | None:
        try:
            if d is not None:
                day = date(y, mo, d)
                return _DateSpan(day, day, "day", text[start:end])
            if mo is not None:
                return _DateSpan(date(y, mo, 1), date(y, mo, calendar.monthrange(y, mo)[1]),
                                 "month", text[start:end])
            return _DateSpan(date(y, 1, 1), date(y, 12, 31), "year", text[start:end])
        except ValueError:
            return None

    spans: list[_DateSpan] = []
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
                second = span(y2, mo2, d2, s2, e2)
                index += 1
            elif mo is not None:
                partial = _RANGE_PARTIAL_RE.match(text, join.end())
                if partial:
                    mo2 = int(partial.group(1) or partial.group(3) or mo)
                    d2 = int(partial.group(2) or partial.group(4))
                    second = span(y, mo2, d2, join.end(), partial.end())
        if second is not None and second.end >= first.start:
            spans.append(_DateSpan(first.start, second.end, "range",
                                   text[start:end] + " ~ " + second.text))
        else:
            spans.append(first)
    return spans


def _spans_text(spans: list[_DateSpan]) -> str:
    return ", ".join(dict.fromkeys(s.text.strip() for s in spans))


def _period_relation(period: str, spans: list[_DateSpan]) -> str:
    """지표 기간과 원문 기간의 관계: match / mismatch / unproven / unknown.

    - 같은 범위면 match(`2026-04` = `2026년 4월` = `2026-04-01 ~ 2026-04-30`).
    - 지표 기간이 연도만이고 원문 범위가 그 해 안이면 match — 모델이 보고 연도만 적은
      경우다. 원문 범위는 인용으로 남고, 연간 합계로 확대하는 추론은 하지 않는다.
    - 모두 겹치지 않으면 mismatch(3월 ↔ 4월, 4월 1일 ↔ 4월 30일).
    - 겹치지만 범위가 다르면 unproven(연간 미발생 → 4월, 4월 1일 기준 → 4월 한 달).
    - 한쪽이라도 기간을 읽지 못하면 unknown(판정하지 않는다).
    """
    metric = _date_spans(period)
    if len(metric) != 1 or not spans:
        return "unknown"
    target = metric[0]
    relations = []
    for s in spans:
        if (s.start, s.end) == (target.start, target.end):
            relations.append("match")
        elif s.end < target.start or s.start > target.end:
            relations.append("mismatch")
        elif target.grain == "year" and target.start <= s.start and s.end <= target.end:
            relations.append("match")
        else:
            relations.append("unproven")
    if "match" in relations:
        return "match"
    return "mismatch" if all(r == "mismatch" for r in relations) else "unproven"


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
                "unit_text": chain.unit_text, "value_in_unit": in_value_unit(chain),
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
            if issues is not None:
                issues.append({"record_type": "metric", "record_index": index,
                               "reason": "value_not_reported", "fatal": False,
                               "metric_hint": str(m.get("metric_hint") or "")})
            continue
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
                zero = _zero_verdict(quote, hint, period, boundary) if value == 0 else None
                if zero is not None and not zero.accepted:
                    # 실측 결함: 원문 칸이 `-`(미공시)인데 0으로 실렸다(삼성전기 4건 —
                    # 유동성장기차입금·장기차입금·지역전문가·Category 9). 자기 근거 문구에
                    # 0도, 같은 지표의 명시적 미발생·미보유 서술도 없으면 그 0은 원문에서 온
                    # 값이 아니다. **실제 0과 미확인은 다르다** — 차입금 '0원'과 '미공시'는
                    # 전혀 다른 문장을 만든다. 그래서 0으로 싣지 않고 사유만 남긴다.
                    if issues is not None:
                        issues.append({"record_type": "metric", "record_index": index,
                                       "reason": "zero_not_in_evidence", "fatal": False,
                                       "cause": zero.cause,
                                       "metric_hint": hint, "unit": unit, "period": period,
                                       "metric_site": str(boundary.get("site") or ""),
                                       "evidence_period": zero.evidence_period,
                                       "evidence_site": zero.evidence_site,
                                       "quote": quote[:_QUOTE_MAX_CHARS]})
                    continue
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
