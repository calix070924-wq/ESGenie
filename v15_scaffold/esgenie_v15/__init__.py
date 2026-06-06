"""ESGenie v15 — 증빙 OCR 통합 + 대기업 실사 데이터 출력 확장 패키지.

L0(DART + OCR 하이브리드) → L5(엑셀 + 증빙 서류철) 워크플로우.
기존 v10 esgenie 패키지(L1 RAG, L2, L4 재생성 루프)와 결합해 사용한다.
"""

__all__ = [
    "ocr_router",
    "evidence_graph",
    "detector_5axis",
    "audit_trace",
    "excel_exporter",
    "prompts",
]
__version__ = "15.0.0-mvp"
