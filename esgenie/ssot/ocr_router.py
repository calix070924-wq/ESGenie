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
    """정형 문서 채널 — CLOVA OCR + 템플릿 매칭 + LLM 후처리.

    파이프라인:
      1) CLOVA OCR API → 토큰(텍스트 + bbox) 리스트 확보
      2) doc_type별 템플릿(_load_template)으로 라벨↔값 쌍 1차 추출
      3) LLM(STRUCTURED_NORMALIZE_PROMPT)으로 단위 정규화 + K-ESG 코드 추정
      API 키 없으면 Mock 폴백(데모 모드).
    """
    source_file = Path(file_path).name

    clova_key, clova_url = _get_clova_keys()
    openai_key = _get_openai_key()

    if not clova_key:
        # CLOVA 없음 → pymupdf + 정규식(API 키 불필요), 스캔본이면 VLM 에스컬레이션
        return _extract_structured_no_llm(file_path, doc_type=doc_type)

    # 1) CLOVA OCR → 토큰 리스트
    tokens = _call_clova_ocr(file_path, api_url=clova_url, secret_key=clova_key)
    raw_text = " ".join(t["text"] for t in tokens)

    # 2) 템플릿 매칭 → 라벨:값 딕셔너리
    try:
        template = _load_template(doc_type)
        kv_pairs = _apply_template(tokens, template)
    except NotImplementedError:
        # 템플릿 미정의 doc_type → 키워드 기반 폴백
        kv_pairs = _keyword_extract(tokens, doc_type)

    # 3) LLM 후처리 → ExtractedMetric[]
    if openai_key and kv_pairs:
        metrics = _llm_normalize(kv_pairs, doc_type=doc_type, api_key=openai_key)
    else:
        metrics = _rule_normalize(kv_pairs, doc_type=doc_type)

    return OcrExtraction(
        source_file=source_file,
        channel=DocChannel.STRUCTURED,
        doc_type=doc_type,
        metrics=metrics,
        raw_text=raw_text,
    )


# ---- 정형 채널 내부 헬퍼 ------------------------------------------------------

def _get_clova_keys() -> tuple[str | None, str]:
    """CLOVA OCR API 키와 URL 조회."""
    import os
    secret = os.getenv("CLOVA_OCR_SECRET", "")
    url = os.getenv("CLOVA_OCR_URL", "")
    return (secret or None), url


def _call_clova_ocr(file_path: str, *, api_url: str, secret_key: str) -> list[dict[str, Any]]:
    """CLOVA OCR API 호출 → 토큰 리스트 [{text, bbox:[x,y,w,h]}].

    응답 형태: https://api.ncloud-docs.com/docs/ai-application-service-ocr-general
    """
    import requests, base64, json as _json

    p = Path(file_path)
    ext = p.suffix.lower().lstrip(".")
    img_b64 = base64.b64encode(p.read_bytes()).decode()

    payload = {
        "version": "V2",
        "requestId": p.stem,
        "timestamp": 0,
        "images": [{"format": ext, "name": p.stem, "data": img_b64}],
    }
    headers = {"X-OCR-SECRET": secret_key, "Content-Type": "application/json"}
    resp = requests.post(api_url, headers=headers, data=_json.dumps(payload), timeout=30)
    resp.raise_for_status()
    data = resp.json()

    tokens: list[dict[str, Any]] = []
    for img in data.get("images", []):
        for field in img.get("fields", []):
            vertices = field.get("boundingPoly", {}).get("vertices", [])
            bbox = (
                [vertices[0]["x"], vertices[0]["y"],
                 vertices[2]["x"], vertices[2]["y"]]
                if len(vertices) >= 3 else None
            )
            tokens.append({"text": field.get("inferText", ""), "bbox": bbox})
    return tokens


def _apply_template(tokens: list[dict[str, Any]], template: dict[str, Any]) -> dict[str, Any]:
    """템플릿의 라벨 키워드와 토큰 텍스트를 매칭해 {라벨: {value, unit, bbox}} 추출.

    전략: 라벨 키워드와 인접한(오른쪽 또는 아래) 숫자 토큰을 값으로 채택.
    """
    import re
    number_re = re.compile(r"[\d,]+\.?\d*")
    result: dict[str, Any] = {}

    for label_key, label_info in template.items():
        keywords: list[str] = label_info.get("keywords", [])
        unit: str = label_info.get("unit", "")
        kesg: str | None = label_info.get("kesg_code")

        for i, tok in enumerate(tokens):
            if any(kw in tok["text"] for kw in keywords):
                # 현재 토큰에 숫자가 있으면 우선 사용 (예: "사용전력량(kWh): 128,400")
                m = number_re.search(tok["text"].replace(",", ""))
                if m:
                    result[label_key] = {
                        "value": float(m.group()),
                        "unit": unit,
                        "kesg_code": kesg,
                        "bbox": tok.get("bbox"),
                        "raw_label": tok["text"],
                    }
                    break
                # 현재 토큰에 숫자 없으면 인접 토큰(최대 5개) 탐색
                for j in range(i + 1, min(i + 6, len(tokens))):
                    m = number_re.search(tokens[j]["text"].replace(",", ""))
                    if m:
                        result[label_key] = {
                            "value": float(m.group()),
                            "unit": unit,
                            "kesg_code": kesg,
                            "bbox": tokens[j].get("bbox"),
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
                    m = number_re.search(tokens[j]["text"].replace(",", ""))
                    if m:
                        result[kw] = {
                            "value": float(m.group()),
                            **info,
                            "bbox": tokens[j].get("bbox"),
                            "raw_label": tok["text"],
                        }
                        break
    return result


def _llm_normalize(
    kv_pairs: dict[str, Any],
    *,
    doc_type: str,
    api_key: str,
) -> list[ExtractedMetric]:
    """Claude Haiku로 추출 KV 쌍을 ExtractedMetric[]으로 정규화."""
    import anthropic, json as _json
    from .prompts import STRUCTURED_NORMALIZE_SYSTEM, STRUCTURED_NORMALIZE_PROMPT

    tokens_str = _json.dumps(kv_pairs, ensure_ascii=False, indent=2)
    prompt = STRUCTURED_NORMALIZE_PROMPT.format(doc_type=doc_type, ocr_tokens=tokens_str)

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
        system=STRUCTURED_NORMALIZE_SYSTEM + "\n반드시 JSON 형식으로만 응답하라.",
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text if resp.content else "{}"
    # JSON 블록 추출
    import re
    m = re.search(r'\{.*\}', text, re.DOTALL)
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
                confidence=float(m.get("confidence", 0.85)),
            ))
        except (TypeError, ValueError):
            continue
    return metrics


def _extract_structured_gpt_fallback(file_path: str, *, doc_type: str, api_key: str) -> OcrExtraction:
    """CLOVA 없을 때 pymupdf 텍스트 추출 + GPT-4o 정규화 폴백."""
    return _extract_structured_no_llm(file_path, doc_type=doc_type)


def _extract_structured_no_llm(file_path: str, *, doc_type: str) -> OcrExtraction:
    """CLOVA/LLM 없이 pymupdf + 정규식으로 디지털 PDF 처리.

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

    # 텍스트를 줄 단위 토큰으로 변환 → 기존 _keyword_extract 재사용
    tokens = [{"text": line.strip(), "bbox": None} for line in raw_text.splitlines() if line.strip()]

    try:
        template = _load_template(doc_type)
        kv_pairs = _apply_template(tokens, template)
    except NotImplementedError:
        kv_pairs = _keyword_extract(tokens, doc_type)

    metrics = _rule_normalize(kv_pairs, doc_type=doc_type)

    return OcrExtraction(
        source_file=Path(file_path).name,
        channel=DocChannel.STRUCTURED,
        doc_type=doc_type,
        metrics=metrics,
        raw_text=raw_text,
        router_meta={"fallback": "pymupdf+regex", "clova": False},
    )


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
    """CLOVA OCR 키 없을 때 데모용 Mock 반환."""
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
    """비정형 문서 채널 — Claude Sonnet Vision 기반 정량·정성 동시 추출.

    파이프라인:
      1) PDF → 페이지 이미지(PNG) — pymupdf 우선, 없으면 pdf2image 폴백
      2) 각 페이지를 base64 인코딩 후 Claude Sonnet Vision에 전달
      3) VLM_EXTRACT_PROMPT로 metrics(정량) + clauses(정성) JSON 동시 추출
      4) 스키마 매핑 → ExtractedMetric[] + ExtractedClause[]
      API 키 없으면 Mock 폴백.
    """
    source_file = Path(file_path).name

    api_key = _get_anthropic_key()
    if not api_key:
        return _mock_unstructured(file_path, doc_type)

    # 디지털 PDF는 텍스트 추출이 더 정확하고 저렴 → 먼저 시도
    raw_text = _extract_text_pymupdf(file_path, max_pages=10)
    if raw_text.strip():
        return _extract_unstructured_text(file_path, doc_type=doc_type, raw_text=raw_text, api_key=api_key)

    # 스캔본 → Vision
    images_b64 = _render_pages_b64(file_path, max_pages=10)
    if not images_b64:
        return _mock_unstructured(file_path, doc_type)

    from .prompts import VLM_EXTRACT_SYSTEM, VLM_EXTRACT_PROMPT
    import anthropic, json as _json, re

    client = anthropic.Anthropic(api_key=api_key)
    prompt_text = VLM_EXTRACT_PROMPT.format(doc_type=doc_type)
    all_metrics: list[ExtractedMetric] = []
    all_clauses: list[ExtractedClause] = []
    raw_texts: list[str] = []

    for page_no, img_b64 in enumerate(images_b64, start=1):
        try:
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2000,
                system=VLM_EXTRACT_SYSTEM + "\n반드시 JSON 형식으로만 응답하라.",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
                        {"type": "text", "text": prompt_text},
                    ],
                }],
            )
            text = resp.content[0].text if resp.content else "{}"
            m = re.search(r'\{.*\}', text, re.DOTALL)
            data = _json.loads(m.group() if m else "{}")
        except Exception as e:
            data = {}
            raw_texts.append(f"[page {page_no} error: {e}]")

        metrics, clauses = _map_vlm_json(data, page_no=page_no)
        all_metrics.extend(metrics)
        all_clauses.extend(clauses)

    return OcrExtraction(
        source_file=source_file,
        channel=DocChannel.UNSTRUCTURED,
        doc_type=doc_type,
        metrics=all_metrics,
        clauses=all_clauses,
        raw_text="\n".join(raw_texts),
    )


def _extract_unstructured_text(
    file_path: str, *, doc_type: str, raw_text: str, api_key: str
) -> OcrExtraction:
    """디지털 텍스트 비정형 문서 → Claude Haiku로 정성 조항 추출."""
    import anthropic, json as _json, re
    from .prompts import VLM_EXTRACT_SYSTEM, VLM_EXTRACT_PROMPT

    prompt = VLM_EXTRACT_PROMPT.format(doc_type=doc_type) + f"\n\n문서 텍스트:\n{raw_text[:4000]}"
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        system=VLM_EXTRACT_SYSTEM + "\n반드시 JSON 형식으로만 응답하라.",
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text if resp.content else "{}"
    m = re.search(r'\{.*\}', text, re.DOTALL)
    data = _json.loads(m.group() if m else "{}")
    metrics, clauses = _map_vlm_json(data)

    return OcrExtraction(
        source_file=Path(file_path).name,
        channel=DocChannel.UNSTRUCTURED,
        doc_type=doc_type,
        metrics=metrics,
        clauses=clauses,
        raw_text=raw_text,
        router_meta={"fallback": "claude-haiku-text", "vision": False},
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
        except ImportError:
            pass

    # 이미지 or pymupdf 미설치 → 파일명만 신호로 사용
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
            "폐기물처리량": {
                "keywords": ["처리량", "배출량", "폐기물량", "인계량"],
                "unit": "ton",
                "kesg_code": "E-6-1",
            },
            "지정폐기물": {
                "keywords": ["지정폐기물"],
                "unit": "ton",
                "kesg_code": "E-6-1",
            },
            "재활용량": {
                "keywords": ["재활용", "재생이용"],
                "unit": "ton",
                "kesg_code": "E-6-2",
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
