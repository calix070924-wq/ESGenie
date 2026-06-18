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
    # 채널 추출기가 기록한 router_meta(engine/azure_error 등)를 보존하고 라우팅 정보만 병합
    ext.router_meta.update({
        "route_confidence": decision.confidence,
        "matched_keywords": decision.matched_keywords,
        "rationale": decision.rationale,
    })
    return ext


# ====================================================================
# 채널 A — 정형: 전통 OCR + LLM 후처리   (STUB)
# ====================================================================

def extract_structured(file_path: str, *, doc_type: str) -> OcrExtraction:
    """정형 문서 채널 — OCR 엔진(우선순위) + 템플릿 매칭 + LLM 후처리.

    엔진 우선순위:
      1) Azure AI Document Intelligence (AZURE_DOC_INTEL_*) — 한국어·표·좌표
      2) CLOVA OCR (CLOVA_OCR_*) — 레거시 경로
      3) pymupdf + 정규식 (키 불필요) — 디지털 PDF
      4) mock (데모)
    공통 후처리:
      · doc_type 템플릿/키워드로 라벨↔값 1차 추출
      · LLM(gpt-4.1-mini via Azure) 단위 정규화 + K-ESG 코드 추정
    """
    az_key, az_ep = _get_azure_docintel_keys()
    clova_key, clova_url = _get_clova_keys()

    if az_key and az_ep:
        try:
            tokens = _call_azure_docintel(file_path)
            return _tokens_to_extraction(
                tokens, doc_type=doc_type, file_path=file_path, engine="azure_docintel"
            )
        except Exception as exc:
            # Azure 실패 → 디지털 PDF 폴백 (데모 안정성)
            ext = _extract_structured_no_llm(file_path, doc_type=doc_type)
            ext.router_meta["azure_error"] = str(exc)
            return ext

    if clova_key:
        tokens = _call_clova_ocr(file_path, api_url=clova_url, secret_key=clova_key)
        return _tokens_to_extraction(
            tokens, doc_type=doc_type, file_path=file_path, engine="clova"
        )

    # OCR 키 없음 → pymupdf + 정규식, 스캔본이면 VLM 에스컬레이션
    return _extract_structured_no_llm(file_path, doc_type=doc_type)


def _tokens_to_extraction(
    tokens: list[dict[str, Any]], *, doc_type: str, file_path: str, engine: str
) -> OcrExtraction:
    """OCR 토큰[{text,bbox}] → 템플릿/키워드 추출 → LLM/규칙 정규화 → OcrExtraction."""
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
    else:
        metrics = _rule_normalize(kv_pairs, doc_type=doc_type)

    return OcrExtraction(
        source_file=Path(file_path).name,
        channel=DocChannel.STRUCTURED,
        doc_type=doc_type,
        metrics=metrics,
        raw_text=raw_text,
        router_meta={"engine": engine},
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


# ---- 정형 채널 내부 헬퍼 ------------------------------------------------------

def _get_azure_docintel_keys() -> tuple[str | None, str]:
    """Azure Document Intelligence 키와 엔드포인트 조회."""
    import os
    key = os.getenv("AZURE_DOC_INTEL_KEY", "")
    endpoint = os.getenv("AZURE_DOC_INTEL_ENDPOINT", "").rstrip("/")
    return (key or None), endpoint


def _call_azure_docintel(file_path: str, *, model: str = "prebuilt-read") -> list[dict[str, Any]]:
    """Azure AI Document Intelligence REST 호출 → 줄 단위 토큰 [{text, bbox}].

    v4.0(2024-11-30) Analyze API: POST로 분석 시작 → Operation-Location 폴링 →
    analyzeResult.pages[].lines[]에서 content + polygon 추출.
    polygon = [x1,y1,x2,y2,x3,y3,x4,y4] → bbox = [x1,y1,x3,y3].
    """
    import requests, time
    key, endpoint = _get_azure_docintel_keys()
    if not key or not endpoint:
        raise RuntimeError("AZURE_DOC_INTEL_ENDPOINT/KEY 미설정")

    api_version = "2024-11-30"
    url = f"{endpoint}/documentintelligence/documentModels/{model}:analyze?api-version={api_version}"
    headers = {"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/octet-stream"}
    data = Path(file_path).read_bytes()

    resp = requests.post(url, headers=headers, data=data, timeout=60)
    resp.raise_for_status()
    op_loc = resp.headers.get("Operation-Location") or resp.headers.get("operation-location")
    if not op_loc:
        raise RuntimeError("Operation-Location 헤더 없음")

    body: dict[str, Any] = {}
    for _ in range(30):
        time.sleep(2)
        r = requests.get(op_loc, headers={"Ocp-Apim-Subscription-Key": key}, timeout=30)
        r.raise_for_status()
        body = r.json()
        status = body.get("status")
        if status == "succeeded":
            break
        if status == "failed":
            raise RuntimeError(f"Azure DocIntel 분석 실패: {body.get('error')}")
    else:
        raise RuntimeError("Azure DocIntel 폴링 타임아웃")

    tokens: list[dict[str, Any]] = []
    for page in body.get("analyzeResult", {}).get("pages", []):
        pw = float(page.get("width") or 0) or None
        ph = float(page.get("height") or 0) or None
        pno = int(page.get("pageNumber", 1)) - 1   # 0-기준 인덱스
        for line in page.get("lines", []):
            poly = line.get("polygon") or []
            # polygon 좌표를 페이지 크기로 나눠 [0,1] 정규화 (단위 무관: inch/pixel 동일 처리)
            if len(poly) >= 6 and pw and ph:
                bbox = [poly[0] / pw, poly[1] / ph, poly[4] / pw, poly[5] / ph]
            else:
                bbox = None
            tokens.append({"text": line.get("content", ""), "bbox": bbox, "page": pno})
    return tokens


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


_DIGIT_SEP_RE = __import__("re").compile(r"(?<=\d)[,\s]+(?=\d)")
_NUMBER_RE = __import__("re").compile(r"\d+\.?\d*")


def _find_number(text: str):
    """텍스트에서 첫 숫자를 추출. 천 단위 콤마·공백 구분자 정규화.

    Azure OCR이 '128, 400'처럼 콤마 뒤 공백을 넣어도 128400으로 합친다.
    """
    cleaned = _DIGIT_SEP_RE.sub("", text)
    return _NUMBER_RE.search(cleaned)


def _apply_template(tokens: list[dict[str, Any]], template: dict[str, Any]) -> dict[str, Any]:
    """템플릿의 라벨 키워드와 토큰 텍스트를 매칭해 {라벨: {value, unit, bbox}} 추출.

    전략: 라벨 키워드와 인접한(오른쪽 또는 아래) 숫자 토큰을 값으로 채택.
    """
    number_re = None  # _find_number 사용
    result: dict[str, Any] = {}

    for label_key, label_info in template.items():
        keywords: list[str] = label_info.get("keywords", [])
        unit: str = label_info.get("unit", "")
        kesg: str | None = label_info.get("kesg_code")

        for i, tok in enumerate(tokens):
            if any(kw in tok["text"] for kw in keywords):
                # 현재 토큰에 숫자가 있으면 우선 사용 (예: "사용전력량(kWh): 128,400")
                m = _find_number(tok["text"])
                if m:
                    result[label_key] = {
                        "value": float(m.group()),
                        "unit": unit,
                        "kesg_code": kesg,
                        "bbox": tok.get("bbox"),
                        "page": tok.get("page"),
                        "raw_label": tok["text"],
                    }
                    break
                # 현재 토큰에 숫자 없으면 인접 토큰(최대 5개) 탐색
                for j in range(i + 1, min(i + 6, len(tokens))):
                    m = _find_number(tokens[j]["text"])
                    if m:
                        result[label_key] = {
                            "value": float(m.group()),
                            "unit": unit,
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
    prompt = STRUCTURED_NORMALIZE_PROMPT.format(doc_type=doc_type, ocr_tokens=tokens_str)

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

    return OcrExtraction(
        source_file=Path(file_path).name,
        channel=DocChannel.STRUCTURED,
        doc_type=doc_type,
        metrics=metrics,
        raw_text=raw_text,
        router_meta={"fallback": "pymupdf+regex", "clova": False},
    )


def _pymupdf_line_tokens(file_path: str, max_pages: int = 5) -> list[dict[str, Any]]:
    """pymupdf로 줄 단위 토큰 추출 [{text, bbox(0~1 정규화), page}].

    디지털(텍스트 임베딩) PDF면 Azure 없이도 위치 좌표를 제공 → provenance 박스 가능.
    스캔본/실패 시 빈 리스트.
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
    """비정형 문서 채널 — 텍스트 추출 후 LLM(gpt-4.1-mini)로 정량·정성 동시 추출.

    파이프라인:
      1) 디지털 PDF → pymupdf 텍스트 추출 (정확·저렴)
      2) 스캔본(임베딩 텍스트 없음) → Azure Document Intelligence로 OCR 텍스트화
      3) 텍스트 → LLM(VLM_EXTRACT_PROMPT)로 metrics + clauses JSON 추출
      텍스트도 키도 없으면 Mock 폴백.
    """
    openai_key = _get_openai_key()
    if not openai_key:
        return _mock_unstructured(file_path, doc_type)

    # 1) 디지털 PDF 텍스트
    raw_text = _extract_text_pymupdf(file_path, max_pages=10)

    # 2) 스캔본 → Azure Document Intelligence OCR로 텍스트화
    if not raw_text.strip():
        az_key, az_ep = _get_azure_docintel_keys()
        if az_key and az_ep:
            try:
                tokens = _call_azure_docintel(file_path)
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
