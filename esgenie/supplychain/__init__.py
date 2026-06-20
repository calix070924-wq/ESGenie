"""공급망 실사 응답 출력 모듈.

ESGenie의 검출 산출물(L1 추출 / D6 선택적 공시 / v15 증빙 data_points)을
대기업(OEM) ESG 자가진단 양식의 자동 응답서로 변환한다.

핵심 진입점
  · build_response_sheet(...)   — 산출물 3종 + 양식 → ResponseSheet
  · respond_from_pipeline(...)  — PipelineOutput에서 바로 응답서 생성
  · export_response_sheet(...)  — ResponseSheet → .xlsx
"""
from __future__ import annotations

from .claims import (
    SupplierClaim,
    is_saq_upload,
    manual_claims,
    merge_claims,
    parse_saq_claims,
)
from .frameworks import all_framework_keys, get_framework
from .responder import build_response_sheet, respond_from_pipeline
from .schema import Answer, Framework, Question, ResponseSheet

__all__ = [
    "build_response_sheet",
    "respond_from_pipeline",
    "get_framework",
    "all_framework_keys",
    "Framework",
    "Question",
    "Answer",
    "ResponseSheet",
    "SupplierClaim",
    "is_saq_upload",
    "parse_saq_claims",
    "manual_claims",
    "merge_claims",
]
