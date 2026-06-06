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
    bbox: list[float] | None = None      # [x0,y0,x1,y1] 원문 위치 (감사 추적용)
    confidence: float = 0.0


@dataclass
class ExtractedClause:
    """비정형 문서에서 추출한 정성 텍스트 단위 (→ 텍스트 노드 후보)."""
    section: str           # "비상대응 절차", "근로자 대표 참여" …
    text: str
    kesg_code_guess: str | None = None
    page: int | None = None


@dataclass
class OcrExtraction:
    """OCR 채널의 통합 산출물."""
    source_file: str                       # 원본 파일명 (감사 증빙 하드링크 키)
    channel: DocChannel
    doc_type: str                          # "kepco_bill" | "waste_ledger" | "safety_minutes" | ...
    metrics: list[ExtractedMetric] = field(default_factory=list)
    clauses: list[ExtractedClause] = field(default_factory=list)
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
    ext.router_meta = {
        "confidence": decision.confidence,
        "matched_keywords": decision.matched_keywords,
        "rationale": decision.rationale,
    }
    return ext


# ====================================================================
# 채널 A — 정형: 전통 OCR + LLM 후처리   (STUB)
# ====================================================================

def extract_structured(file_path: str, *, doc_type: str) -> OcrExtraction:
    """정형 문서 채널.

    구현 단계(파이프라인):
      1) 전통 OCR — Naver CLOVA OCR(권장, 한글 표 인식 우수) 또는 PaddleOCR/Tesseract.
         → 셀 좌표(bbox) 포함 토큰 리스트 확보.
      2) 양식 템플릿 매칭 — doc_type별 키-값 좌표 템플릿(_load_template)으로 1차 추출.
         (한전 고지서의 '사용전력량' 셀 위치는 거의 고정)
      3) LLM 후처리 — 추출 토큰을 prompts.STRUCTURED_NORMALIZE_PROMPT에 넣어
         · 단위 정규화(kWh→MJ 환산 등)
         · K-ESG 코드 추정(E-4-1 에너지 사용량 …)
         · 비정상치 플래그
         결과를 ExtractedMetric[]로 환원.

    Returns: OcrExtraction(channel=STRUCTURED, metrics=[...])
    """
    raise NotImplementedError(
        "정형 채널 구현 TODO:\n"
        "  - clova_ocr_client.recognize(file_path) -> tokens\n"
        "  - template = _load_template(doc_type)\n"
        "  - kv = template.apply(tokens)\n"
        "  - metrics = llm_postprocess(kv, prompt=STRUCTURED_NORMALIZE_PROMPT)\n"
        "  - return OcrExtraction(source_file, STRUCTURED, doc_type, metrics=metrics, raw_text=...)"
    )


# ====================================================================
# 채널 B — 비정형: VLM 우선   (STUB)
# ====================================================================

def extract_unstructured(file_path: str, *, doc_type: str) -> OcrExtraction:
    """비정형 문서 채널.

    구현 단계:
      1) 페이지 이미지화 — pdf2image / PyMuPDF로 페이지를 PNG로 렌더.
      2) VLM 호출 — 각 페이지를 base64로 GPT-4o Vision 등에 전달,
         prompts.VLM_EXTRACT_PROMPT로 (a)정량 수치 (b)정성 조항을 JSON으로 동시 추출.
      3) 스키마 매핑 — 반환 JSON → ExtractedMetric[] + ExtractedClause[].
         회의록·매뉴얼은 대부분 clauses 중심, 표가 섞여 있으면 metrics도 채움.

    Returns: OcrExtraction(channel=UNSTRUCTURED, metrics=[...], clauses=[...])
    """
    raise NotImplementedError(
        "비정형 채널 구현 TODO:\n"
        "  - images = render_pages(file_path)  # PNG per page\n"
        "  - resp = llm.complete(system=VLM_SYSTEM, user=VLM_EXTRACT_PROMPT, images=images, json_mode=True)\n"
        "  - data = json.loads(resp.content)\n"
        "  - metrics, clauses = _map_vlm_json(data)\n"
        "  - return OcrExtraction(source_file, UNSTRUCTURED, doc_type, metrics, clauses, raw_text=...)"
    )


# ====================================================================
# 내부 헬퍼 (STUB)
# ====================================================================

def _quick_preview(file_path: str, max_chars: int = 1500) -> str:
    """1페이지만 싸게 텍스트화 (라우팅 판단용).

    구현: PDF면 PyMuPDF page[0].get_text(); 이미지면 경량 OCR(Tesseract --psm 6).
    임베디드 텍스트가 있으면 OCR 없이 그대로 반환(스캔본만 OCR).
    """
    p = Path(file_path)
    if not p.exists():
        return ""
    # TODO: 실제 프리뷰. 현재는 파일명만 신호로 사용.
    return p.stem


def _score_signatures(text: str, fname: str, table: dict[str, list[str]]) -> dict[str, dict[str, Any]]:
    """시그니처 사전 대비 키워드 매칭 점수(0~1 근사) 계산."""
    out: dict[str, dict[str, Any]] = {}
    haystack = f"{text} {fname}"
    for doc_type, kws in table.items():
        matched = [kw for kw in kws if kw.lower() in haystack]
        score = len(matched) / max(len(kws), 1)
        out[doc_type] = {"score": score, "kw": matched}
    return out


def _load_template(doc_type: str) -> Any:
    """doc_type별 키-값 좌표 템플릿 로더 (정형 채널용). TODO."""
    raise NotImplementedError(f"template for {doc_type}")
