"""esgenie.ssot — 증빙 OCR 통합(SSOT) + 대기업 실사 데이터 출력 서브패키지.

(구 v15_scaffold/esgenie_v15 — 메인 패키지로 흡수 통합됨)

L0(DART + OCR 하이브리드) → L5(엑셀 + 증빙 서류철) 워크플로우.
코어 모듈(layer0~5, llm, embeddings)과 같은 패키지 안에서 동작한다.

  - evidence_graph: 단일 진실 원천(SSOT) — DART + OCR 노드 + cross_check 엣지
  - ocr_router:     증빙 문서 듀얼 채널 OCR (정형 Upstage DP / 비정형 VLM, mock 폴백)
  - ssot_pipeline:  L1/L2 브리지 (OCR 증빙으로 no_evidence 해소, RAG 편입)
  - detector_5axis: D1 증빙 강화 + P축 사내규정 검증
  - audit_trace:    실사 대응 audit_trace_v15 (data_points + policy_audit)
  - excel_exporter: 대기업 제출용 정량 엑셀 + 증빙 서류철
"""

__all__ = [
    "ocr_router",
    "evidence_graph",
    "detector_5axis",
    "audit_trace",
    "excel_exporter",
    "prompts",
    "ssot_pipeline",
]
