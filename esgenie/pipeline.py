"""6-Layer 통합 오케스트레이터.

CLI와 Streamlit이 동일한 SSOT 기반 실행 경로를 공유한다.

실행:
    python -m esgenie.pipeline --ticker 005930
    python -m esgenie.pipeline --ticker 005930 --areas E S G
"""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import INDUSTRY_DIR, MAX_REFINEMENT_ITER, SETTINGS
from .dart_client import CompanyReport, load_report
from .industry import resolve_module  # 업종 모듈 self-register 포함
from .issb_gap import ISSBGapReport, build_issb_gap_report
from .layer1_extract import ExtractionResult
from .layer3_disclosure import DisclosureReport, detect_selective_disclosure
from .layer2_rag import HybridRAG
from .layer4_verify import VerificationResult, verify_and_refine
from .layer5_audit_trace import AuditTrace, build_audit_trace, save_audit_trace
from .llm import CLIENT as LLM_CLIENT
from .knowledge.kesg_items import BASIC_28_CODES
from .ssot import (
    audit_trace as ssot_audit_trace,
    detector_5axis,
    evidence_graph as ssot_evidence_graph,
    excel_exporter,
    ocr_router,
)
from .ssot.ssot_pipeline import build_rag_with_ssot, extract_local_with_ssot, extract_with_ssot

logger = logging.getLogger(__name__)

POLICY_CODES = [
    "P-1-1",
    "E-1-1", "E-1-2", "E-3-3",
    "S-1-1", "S-2-6", "S-4-1", "S-5-1", "S-6-1", "S-7-1", "S-8-1",
    "G-1-1", "G-3-1", "G-4-1", "G-5-1",
]


@dataclass
class PipelineOutput:
    report: CompanyReport | None
    evidence_graph: ssot_evidence_graph.EvidenceGraph
    extraction: ExtractionResult | None
    sections: dict[str, VerificationResult] = field(default_factory=dict)
    audit_traces: dict[str, AuditTrace] = field(default_factory=dict)
    trace_paths: dict[str, str] = field(default_factory=dict)
    disclosure: DisclosureReport | None = None
    issb_gap: ISSBGapReport | None = None
    risk_rows: list[dict[str, Any]] = field(default_factory=list)
    policy_results: list[Any] = field(default_factory=list)
    policy_drafts: dict[str, str] = field(default_factory=dict)
    v15_trace: Any | None = None
    export_paths: dict[str, str] = field(default_factory=dict)
    ocr_extractions: list[ocr_router.OcrExtraction] = field(default_factory=list)
    requested_areas: list[str] = field(default_factory=list)
    industry_module_key: str | None = None   # 적용된 업종 모듈 키(없으면 전역). 점수 변동 설명용.
    supplier_claims: dict[str, Any] = field(default_factory=dict)
    supplier_claim_files: list[str] = field(default_factory=list)


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


def _collect_ocr_extractions(
    evidence_files: dict[str, str] | None,
    *,
    survey_answers: dict[str, dict[str, str]] | None = None,
) -> list[ocr_router.OcrExtraction]:
    extractions: list[ocr_router.OcrExtraction] = []

    for fname, path in (evidence_files or {}).items():
        try:
            decision = ocr_router.route_document(path)
            extraction = ocr_router.extract_document(path, decision)
            extraction.source_file = fname
            extractions.append(extraction)
        except Exception as exc:
            logger.warning("OCR 처리 실패 [%s]: %s", fname, exc)

    clauses: list[ocr_router.ExtractedClause] = []
    for code, answer in (survey_answers or {}).items():
        yn = answer.get("yn", "미입력")
        if yn == "미입력":
            continue
        text = f"[설문] {yn}"
        if answer.get("text"):
            text += f": {answer['text']}"
        clauses.append(ocr_router.ExtractedClause(
            section=code,
            text=text,
            kesg_code_guess=code,
            page=1,
        ))

    if clauses:
        extractions.append(ocr_router.OcrExtraction(
            source_file="survey_form",
            channel=ocr_router.DocChannel.UNSTRUCTURED,
            doc_type="survey",
            clauses=clauses,
            router_meta={"source": "survey"},
        ))

    return extractions


def _apply_survey_answers(
    extraction: ExtractionResult | None,
    survey_answers: dict[str, dict[str, str]] | None,
) -> None:
    if extraction is None or not survey_answers:
        return

    from esgenie.knowledge.kesg_items import by_code

    for code, answer in survey_answers.items():
        if answer.get("yn", "미입력") == "미입력" or code in extraction.mapped:
            continue
        item = by_code(code)
        if item is None:
            continue
        extraction.mapped[code] = {
            "code": code,
            "name": item.name,
            "area": item.area,
            "category": item.category,
            "data_type": item.data_type,
            "value": answer.get("yn"),
            "unit": "",
            "note": answer.get("text") or None,
            "evidence_node_ids": [f"survey_{code}"],
        }
        if code in extraction.missing:
            extraction.missing.remove(code)


def _build_risk_rows(
    graph: ssot_evidence_graph.EvidenceGraph,
    *,
    target_codes: list[str],
    industry_module=None,
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    d1_scores: dict[str, float] = {}
    risk_rows: list[dict[str, Any]] = []

    for code in target_codes:
        nodes = graph.nodes_by_metric(code)
        if not nodes:
            continue
        node = nodes[-1]
        axes = detector_5axis.detect_risk_axes(
            f"{code} 값은 {node.value}{node.unit}이다.",
            code,
            graph,
            industry_module=industry_module,
        )
        d1_scores[code] = axes["D1"].score
        risk_rows.append({
            "K-ESG 코드": code,
            "값": f"{node.value} {node.unit}",
            "D1 수치": round(axes["D1"].score, 3),
            "D2 수식어": round(axes["D2"].score, 3),
            "D3 의미": round(axes["D3"].score, 3),
            "D5 시계열": round(axes["D5"].score, 3),
            "종합 위험도": round(axes["aggregate"].score, 3),
        })
    return d1_scores, risk_rows


def _audit_policy_documents(
    graph: ssot_evidence_graph.EvidenceGraph,
    *,
    corp_name: str,
    industry: str,
) -> tuple[list[Any], dict[str, str]]:
    active_codes = list({
        *[code for code in POLICY_CODES if graph.text_nodes_by_code(code) or graph.nodes_by_metric(code)],
        "S-3-1", "E-1-1",
    })

    results: list[Any] = []
    drafts: dict[str, str] = {}
    for code in active_codes:
        result = detector_5axis.audit_policy_documents(code, graph, LLM_CLIENT)
        results.append(result)
        if not result.passed:
            drafts[code] = detector_5axis.draft_missing_policy(
                code,
                result,
                corp_name,
                industry,
                LLM_CLIENT,
            )
    return results, drafts


def _export_v15_artifacts(
    graph: ssot_evidence_graph.EvidenceGraph,
    *,
    corp_code: str,
    corp_name: str,
    industry: str,
    report_year: int,
    evidence_files: dict[str, str] | None,
    export_outputs: bool,
    export_root: str | Path = "outputs",
    industry_module=None,
) -> tuple[Any | None, dict[str, str], list[dict[str, Any]], list[Any], dict[str, str]]:
    d1_scores, risk_rows = _build_risk_rows(
        graph, target_codes=BASIC_28_CODES, industry_module=industry_module)
    policy_results, policy_drafts = _audit_policy_documents(
        graph,
        corp_name=corp_name,
        industry=industry,
    )
    v15_trace = ssot_audit_trace.build_audit_trace_v15(
        corp_code,
        corp_name,
        ssot_audit_trace.build_data_points(graph, d1_scores, target_codes=BASIC_28_CODES),
        policy_results,
    )

    export_paths: dict[str, str] = {}
    if export_outputs:
        out_dir = Path(export_root) / f"{corp_code or corp_name}_{report_year}"
        export_paths = excel_exporter.export_datasheet(
            v15_trace,
            out_dir,
            uploaded_files=evidence_files,
        )
    return v15_trace, export_paths, risk_rows, policy_results, policy_drafts


def run(
    corp_code: str,
    areas: list[str] | None = None,
    *,
    corp_name: str | None = None,
    industry: str | None = None,
    report_year: int | None = None,
    use_dart: bool = True,
    evidence_files: dict[str, str] | None = None,
    survey_answers: dict[str, dict[str, str]] | None = None,
    ocr_extractions: list[ocr_router.OcrExtraction] | None = None,
    demo_greenwash: bool = False,
    save_traces: bool = True,
    llm_judge: bool = False,
    threshold: float = 30.0,
    max_iter: int = MAX_REFINEMENT_ITER,
    export_outputs: bool = False,
    export_root: str | Path = "outputs",
    export_report: bool = False,  # True면 통합 보고서(.md/.pdf)를 export_paths에 저장
    profile: str | None = None,   # "sme" | "full" | None(자동: 상장코드→full, 그 외→sme)
    active_industry: str | None = None,  # 업종 모듈 키 명시(추론보다 우선). None이면 SETTINGS→DART추론→전역
) -> PipelineOutput:
    """L0 → L1 → L2 → L3 → L4(루프) → L5 전체 실행.

    Args:
        corp_code:      종목코드 (예: "005930")
        areas:          분석 영역 목록 (기본: ["E", "S", "G"])
        corp_name:      비DART 실행 시 표시용 회사명
        industry:       비DART 실행 시 정책 초안용 업종명
        report_year:    대상 연도 (DART/OCR 통합 기준)
        use_dart:       True면 DART 로드 시도
        evidence_files: {표시 파일명: 로컬 경로} 형태의 업로드 증빙
        survey_answers: 정성 설문 응답
        ocr_extractions: 이미 추출된 OCR 결과가 있으면 직접 주입
        demo_greenwash: True면 초안을 의도적 과장 생성
        save_traces:    True면 outputs/ 에 audit_trace JSON 저장
        llm_judge:      True면 룰 1차 + LLM 2차 판정 하이브리드 검출
                        (키 없으면 mock 판정 — 데모 가능)
    """
    areas = areas or ["E", "S", "G"]

    logger.info("[L0] SSOT Evidence Graph 구축 중...")

    report: CompanyReport | None = None
    if use_dart and corp_code:
        report = load_report(corp_code, report_year=report_year)

    extractions = list(ocr_extractions or [])
    if not extractions:
        extractions = _collect_ocr_extractions(
            evidence_files,
            survey_answers=survey_answers,
        )

    corp_code_final = corp_code or (report.corp_code if report is not None else "LOCAL")
    corp_name_final = corp_name or (report.corp_name if report is not None else corp_code_final)
    report_year_final = report_year or (report.report_year if report is not None else 2025)

    # 시연 익명화: 회사명만 별칭으로 치환(데이터는 corp_code로 실제 사용). report.corp_name까지
    # 덮어 audit_trace·실사응답서 엑셀 등 산출물에서도 실명이 새지 않게 한다.
    from .demo_aliases import display_name as _demo_display_name
    corp_name_final = _demo_display_name(corp_name_final)
    if report is not None:
        report.corp_name = _demo_display_name(report.corp_name)

    # 업종 모듈을 진입점에서 한 번만 결정해 하위로 동일 객체 전달(레이어별 재해석 방지).
    # 우선순위: 인자 active_industry > 환경설정 SETTINGS.active_industry > DART 업종명 추론 > None(전역).
    report_industry = (report.industry if report is not None else None) or industry
    industry_module = resolve_module(
        active_industry or SETTINGS.active_industry, report_industry)
    industry_module_key = industry_module.key if industry_module is not None else None
    if industry_module_key:
        logger.info("[업종] 모듈 적용: %s", industry_module_key)

    evidence_graph = ssot_evidence_graph.build_unified_graph(
        report,
        extractions,
        corp_code=corp_code_final,
        corp_name=corp_name_final,
        report_year=report_year_final,
        industry_module=industry_module,
    )
    logger.info(
        "[L0] 완료: %d 노드, %d 텍스트노드, %d 엣지",
        len(evidence_graph.nodes),
        len(evidence_graph.text_nodes),
        len(evidence_graph.edges),
    )

    extraction: ExtractionResult | None = None
    disclosure: DisclosureReport | None = None
    issb_gap: ISSBGapReport | None = None

    sections: dict[str, VerificationResult] = {}
    audit_traces: dict[str, AuditTrace] = {}
    trace_paths: dict[str, str] = {}

    if report is not None:
        logger.info("[L1] K-ESG 항목 추출 중...")
        extraction = extract_with_ssot(report, evidence_graph, profile=profile)
        _apply_survey_answers(extraction, survey_answers)
        logger.info(
            "[L1] 완료: %.1f%% 커버리지 (%s)",
            extraction.coverage_pct,
            extraction.profile_label,
        )

        disclosure = detect_selective_disclosure(extraction, industry_module)
        logger.info("[D6] 선택적 공시 의심도=%.2f (%s)", disclosure.score, disclosure.level)
        issb_gap = build_issb_gap_report(extraction)
        logger.info(
            "[ISSB] 프로파일 내 %d/%d 공시, 누락 %d",
            issb_gap.in_profile_disclosed,
            issb_gap.in_profile_total,
            issb_gap.in_profile_missing,
        )

        from .embeddings import embedding_backend
        backend = embedding_backend()
        if backend != "sbert":
            logger.warning(
                "[L2] ⚠ 임베딩 폴백 모드(%s) — D3 의미검증 품질 저하. sentence-transformers 설치 권장",
                backend,
            )
        logger.info("[L2] Hybrid RAG 인덱스 빌드 중... (backend=%s)", backend)
        rag = HybridRAG()
        build_rag_with_ssot(rag, report, evidence_graph)

        industry_stats = _load_industry_stats(report.industry)
        for area in areas:
            logger.info("[L3-L4] 영역 %s 탐지·검증 중...", area)
            verify = verify_and_refine(
                report,
                area,
                rag,
                threshold=threshold,
                max_iter=max_iter,
                demo_greenwash=demo_greenwash,
                evidence_graph=evidence_graph,
                industry_stats=industry_stats,
                industry_module=industry_module,
                llm_judge=llm_judge,
            )
            sections[area] = verify
            logger.info(
                "[L4] 영역 %s 완료: 위험도=%.1f, 수렴=%s, HITL=%s",
                area,
                verify.final_score,
                verify.converged,
                verify.hitl_required,
            )

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
    elif evidence_graph.nodes or evidence_graph.text_nodes:
        logger.info("[L1] K-ESG 항목 추출 중... (비상장/SSOT 로컬)")
        extraction = extract_local_with_ssot(
            evidence_graph,
            corp_code=corp_code_final,
            corp_name=corp_name_final,
            report_year=report_year_final,
            industry=((industry or "").strip()),
            profile=profile,
        )
        _apply_survey_answers(extraction, survey_answers)
        logger.info(
            "[L1] 완료: %.1f%% 커버리지 (%s)",
            extraction.coverage_pct,
            extraction.profile_label,
        )

        disclosure = detect_selective_disclosure(extraction, industry_module)
        logger.info("[D6] 선택적 공시 의심도=%.2f (%s)", disclosure.score, disclosure.level)
        issb_gap = build_issb_gap_report(extraction)
        logger.info(
            "[ISSB] 프로파일 내 %d/%d 공시, 누락 %d",
            issb_gap.in_profile_disclosed,
            issb_gap.in_profile_total,
            issb_gap.in_profile_missing,
        )

    effective_industry = ((report.industry if report is not None else "") or industry or "")
    v15_trace, export_paths, risk_rows, policy_results, policy_drafts = _export_v15_artifacts(
        evidence_graph,
        corp_code=corp_code_final,
        corp_name=corp_name_final,
        industry=effective_industry,
        report_year=report_year_final,
        evidence_files=evidence_files,
        export_outputs=export_outputs,
        export_root=export_root,
        industry_module=industry_module,
    )

    result = PipelineOutput(
        report=report,
        evidence_graph=evidence_graph,
        extraction=extraction,
        sections=sections,
        audit_traces=audit_traces,
        trace_paths=trace_paths,
        disclosure=disclosure,
        issb_gap=issb_gap,
        risk_rows=risk_rows,
        policy_results=policy_results,
        policy_drafts=policy_drafts,
        v15_trace=v15_trace,
        export_paths=export_paths,
        ocr_extractions=extractions,
        requested_areas=list(areas),
        industry_module_key=industry_module_key,
    )

    if export_report and sections:
        try:
            from .layer6_report import assemble_report
            from .exporters.report_pdf import export_report_pdf
            doc = assemble_report(result)
            out_dir = Path(export_root) / f"{corp_code_final or corp_name_final}_{report_year_final}"
            out_dir.mkdir(parents=True, exist_ok=True)
            md_path = out_dir / f"ESG보고서_{(corp_name_final or 'corp').replace('/', '_')}_{report_year_final}.md"
            md_path.write_text(doc.to_markdown(), encoding="utf-8")
            result.export_paths["report_md"] = str(md_path)
            result.export_paths["report_pdf"] = export_report_pdf(doc, out_dir)
            logger.info("[L6] 통합 보고서 저장: %s", result.export_paths["report_pdf"])
        except Exception as exc:
            logger.warning("[L6] 통합 보고서 생성 실패: %s", exc)

    return result


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
    parser.add_argument("--industry", default=None,
                        help="업종 모듈 키 명시 (예: automotive_parts). 기본: DART 업종명 자동 추론")
    parser.add_argument("--no-save", action="store_true", help="audit_trace 파일 미저장")
    parser.add_argument("--export-report", action="store_true",
                        help="통합 보고서(.md/.pdf)를 outputs/에 생성")
    parser.add_argument("--report-year", type=int, default=None, help="분석 기준 연도")
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
        report_year=args.report_year,
        profile=args.profile,
        active_industry=args.industry,
        export_report=args.export_report,
    )

    print(f"\n{'='*60}")
    corp_label = output.report.corp_name if output.report is not None else args.ticker
    print(f"기업: {corp_label} ({args.ticker})")
    print(f"분석 영역: {args.areas}")
    if output.industry_module_key:
        print(f"업종 모듈: {output.industry_module_key}")
    if output.extraction is not None:
        print(f"K-ESG 커버리지: {output.extraction.coverage_pct:.1f}% — {output.extraction.profile_label}")
    print(f"Evidence Graph: {len(output.evidence_graph.nodes)}노드 / {len(output.evidence_graph.edges)}엣지")
    if output.disclosure is not None:
        d6 = output.disclosure
        print(f"D6 선택적 공시: 의심도 {d6.score:.2f} ({d6.level}) — {d6.rationale}")
        for o in d6.orphan_ratios:
            print(f"   · 고아비율: {o.detail}")
    if output.issb_gap is not None:
        gap = output.issb_gap
        print(
            f"ISSB 갭 리포트: 프로파일 내 {gap.in_profile_disclosed}/{gap.in_profile_total} 공시"
            f" · 누락 {gap.in_profile_missing}"
        )
    for area, verify in output.sections.items():
        hitl = " [HITL_REQUIRED]" if verify.hitl_required else ""
        print(f"  [{area}] 위험도={verify.final_score:.1f} | {verify.final_band}{hitl}")
    if output.trace_paths:
        print("\nAudit Trace 저장:")
        for area, path in output.trace_paths.items():
            print(f"  [{area}] {path}")
    if output.export_paths.get("report_pdf"):
        print("\n통합 보고서:")
        print(f"  PDF: {output.export_paths['report_pdf']}")
        if output.export_paths.get("report_md"):
            print(f"  MD : {output.export_paths['report_md']}")
    print("="*60)


if __name__ == "__main__":
    _cli()
