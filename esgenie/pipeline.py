"""6-Layer 통합 오케스트레이터.

실행:
    python -m esgenie.pipeline --ticker 005930
    python -m esgenie.pipeline --ticker 005930 --areas E S G
"""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from typing import Any

from .config import INDUSTRY_DIR
from .dart_client import CompanyReport, load_report
from .layer0_evidence_graph import EvidenceGraph, build_evidence_graph
from .layer1_extract import ExtractionResult, extract
from .layer2_rag import HybridRAG
from .layer4_verify import VerificationResult, verify_and_refine
from .layer5_audit_trace import AuditTrace, build_audit_trace, save_audit_trace
from .schemas import RiskVector

logger = logging.getLogger(__name__)


@dataclass
class PipelineOutput:
    report: CompanyReport
    evidence_graph: EvidenceGraph
    extraction: ExtractionResult
    sections: dict[str, VerificationResult]   # area → result
    audit_traces: dict[str, AuditTrace]        # area → trace
    trace_paths: dict[str, str]                # area → 파일 경로


def _load_industry_stats(industry: str) -> dict[str, Any] | None:
    """benchmarks.json에서 업종 벤치마크 로드."""
    try:
        for path in INDUSTRY_DIR.glob("*.json"):
            with open(path, encoding="utf-8") as fp:
                obj = json.load(fp)
            for b in obj.get("benchmarks", []):
                if b["industry"] == industry:
                    return b
    except Exception as exc:
        logger.warning("업종 벤치마크 로드 실패: %s", exc)
    return None


def run(
    corp_code: str,
    areas: list[str] | None = None,
    *,
    demo_greenwash: bool = False,
    save_traces: bool = True,
    llm_judge: bool = False,
    profile: str | None = None,   # "sme" | "full" | None(자동: 상장코드→full, 그 외→sme)
) -> PipelineOutput:
    """L0 → L1 → L2 → L3 → L4(루프) → L5 전체 실행.

    Args:
        corp_code:      종목코드 (예: "005930")
        areas:          분석 영역 목록 (기본: ["E", "S", "G"])
        demo_greenwash: True면 초안을 의도적 과장 생성
        save_traces:    True면 outputs/ 에 audit_trace JSON 저장
        llm_judge:      True면 룰 1차 + LLM 2차 판정 하이브리드 검출
                        (키 없으면 mock 판정 — 데모 가능)
    """
    areas = areas or ["E", "S", "G"]

    # L0 — Evidence Graph
    logger.info("[L0] Evidence Graph 구축 중...")
    report = load_report(corp_code)
    evidence_graph = build_evidence_graph(report)
    logger.info(
        "[L0] 완료: %d 노드, %d 엣지",
        len(evidence_graph.nodes), len(evidence_graph.edges),
    )

    # L1 — K-ESG 항목 추출 (프로파일 기반, evidence_node_ids 부착)
    logger.info("[L1] K-ESG 항목 추출 중...")
    extraction = extract(report, evidence_graph=evidence_graph, profile=profile)
    logger.info("[L1] 완료: %.1f%% 커버리지 (%s)",
                extraction.coverage_pct, extraction.profile_label)

    # L2 — Hybrid RAG 인덱스 빌드
    from .embeddings import embedding_backend
    _backend = embedding_backend()
    if _backend != "sbert":
        logger.warning("[L2] ⚠ 임베딩 폴백 모드(%s) — D3 의미검증 품질 저하. "
                       "sentence-transformers 설치 권장", _backend)
    logger.info("[L2] Hybrid RAG 인덱스 빌드 중... (backend=%s)", _backend)
    rag = HybridRAG()
    rag.build_corp_index(report)

    # 업종 벤치마크 로드
    industry_stats = _load_industry_stats(report.industry)

    sections: dict[str, VerificationResult] = {}
    audit_traces: dict[str, AuditTrace] = {}
    trace_paths: dict[str, str] = {}

    for area in areas:
        logger.info("[L3-L4] 영역 %s 탐지·검증 중...", area)

        # L3+L4 — detect_risk_vector + 5축 제약 재생성
        verify = verify_and_refine(
            report, area, rag,
            demo_greenwash=demo_greenwash,
            evidence_graph=evidence_graph,
            industry_stats=industry_stats,
            llm_judge=llm_judge,
        )
        sections[area] = verify
        logger.info(
            "[L4] 영역 %s 완료: 위험도=%.1f, 수렴=%s, HITL=%s",
            area, verify.final_score, verify.converged, verify.hitl_required,
        )

        # L5 — Audit Trace 생성
        logger.info("[L5] Audit Trace 생성 중 (영역 %s)...", area)
        trace = build_audit_trace(
            report=report,
            area=area,
            verification=verify,
            extraction=extraction,
            evidence_graph=evidence_graph,
            industry_stats=industry_stats,
            llm_judge=llm_judge,
        )
        audit_traces[area] = trace

        if save_traces:
            path = save_audit_trace(trace)
            trace_paths[area] = str(path)
            logger.info("[L5] 저장 완료: %s", path)

    return PipelineOutput(
        report=report,
        evidence_graph=evidence_graph,
        extraction=extraction,
        sections=sections,
        audit_traces=audit_traces,
        trace_paths=trace_paths,
    )


# ---- CLI 진입점 -------------------------------------------------------------

def _cli() -> None:
    parser = argparse.ArgumentParser(description="ESGenie 6-Layer 파이프라인")
    parser.add_argument("--ticker", required=True, help="종목코드 (예: 005930)")
    parser.add_argument(
        "--areas", nargs="+", default=["E", "S", "G"],
        choices=["E", "S", "G"], help="분석 영역 (기본: E S G)",
    )
    parser.add_argument("--demo-greenwash", action="store_true", help="그린워싱 시연 모드")
    parser.add_argument("--llm-judge", action="store_true",
                        help="룰+LLM 하이브리드 검출 (2차 LLM 판정, 키 없으면 mock)")
    parser.add_argument("--profile", choices=["sme", "full"], default=None,
                        help="K-ESG 프로파일 (기본: 자동 — 상장코드→full, 그 외→sme)")
    parser.add_argument("--no-save", action="store_true", help="audit_trace 파일 미저장")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    output = run(
        corp_code=args.ticker,
        areas=args.areas,
        demo_greenwash=args.demo_greenwash,
        save_traces=not args.no_save,
        llm_judge=args.llm_judge,
        profile=args.profile,
    )

    print(f"\n{'='*60}")
    print(f"기업: {output.report.corp_name} ({args.ticker})")
    print(f"분석 영역: {args.areas}")
    print(f"K-ESG 커버리지: {output.extraction.coverage_pct:.1f}% — {output.extraction.profile_label}")
    print(f"Evidence Graph: {len(output.evidence_graph.nodes)}노드 / {len(output.evidence_graph.edges)}엣지")
    for area, verify in output.sections.items():
        hitl = " [HITL_REQUIRED]" if verify.hitl_required else ""
        print(f"  [{area}] 위험도={verify.final_score:.1f} | {verify.final_band}{hitl}")
    if output.trace_paths:
        print("\nAudit Trace 저장:")
        for area, path in output.trace_paths.items():
            print(f"  [{area}] {path}")
    print("="*60)


if __name__ == "__main__":
    _cli()
