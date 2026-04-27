"""Layer 5 — Audit Trace 생성.

최종 보고서의 문장 단위로 L0~L4 결과를 묶어 audit_trace.json을 생성한다.

출력 위치: outputs/audit_trace_{ticker}_{ts}.json
스키마 문서: docs/audit_trace_schema.md
"""
from __future__ import annotations

import datetime
import json
import re
from pathlib import Path
from typing import Any

from .config import ROOT_DIR, SETTINGS
from .dart_client import CompanyReport
from .layer1_extract import ExtractionResult
from .layer2_rag import GenerationResult
from .layer3_detect import detect_risk_vector
from .layer4_verify import VerificationResult
from .schemas import AuditSentence, AuditTrace, RefinementAttempt, RiskVector

OUTPUT_DIR = ROOT_DIR / "outputs"


def build_audit_trace(
    report: CompanyReport,
    area: str,
    verification: VerificationResult,
    extraction: ExtractionResult,
    evidence_graph: Any | None = None,   # EvidenceGraph | None
    industry_stats: dict[str, Any] | None = None,
) -> AuditTrace:
    """파이프라인 산출물 → AuditTrace 조립.

    Args:
        report:        CompanyReport (corp_code, corp_name 등)
        area:          "E" | "S" | "G"
        verification:  L4 VerificationResult (최종 텍스트 + steps)
        extraction:    L1 ExtractionResult (kesg_item_id 매핑)
        evidence_graph: L0 EvidenceGraph (없으면 evidence_node_ids=[])
        industry_stats: 업종 벤치마크 dict
    """
    final_text = verification.final.generation.text
    sentences   = _split_sentences(final_text)
    gen         = verification.final.generation

    # RAG 청크 → retrieved_chunks 형식
    chunks = _gen_to_chunks(gen)

    # K-ESG 코드 → sentence 텍스트 역매핑 (간단 키워드 기반)
    kesg_map = _build_kesg_sentence_map(sentences, extraction)

    audit_sentences: list[AuditSentence] = []
    now_iso = _now_iso()

    for idx, sent_text in enumerate(sentences):
        sent_id = f"{report.corp_code}_{area}_{idx:03d}"

        # evidence_node_ids
        ev_node_ids: list[str] = []
        if evidence_graph is not None:
            codes = _guess_kesg_codes(sent_text, extraction)
            for code in codes:
                nodes = evidence_graph.search_nodes(keywords=[code], period=report.report_year)
                ev_node_ids.extend(n.id for n in nodes)

        # retrieved_chunk_ids (최대 3개)
        chunk_ids = _match_chunk_ids(sent_text, chunks)

        # kesg_item_id
        kesg_item_id = kesg_map.get(sent_text)

        # risk_vector (문장 단위)
        rv: RiskVector | None = None
        if evidence_graph is not None or chunks:
            rv = detect_risk_vector(
                sent_text,
                evidence_graph=evidence_graph,
                retrieved_chunks=chunks or None,
                industry_stats=industry_stats,
            )

        # refinement_attempts (해당 문장에 영향을 준 시도 목록)
        ref_attempts = _filter_attempts(verification.refinement_attempts, sent_text)

        # hitl_status
        hitl_status = (
            "HITL_REQUIRED"
            if (verification.hitl_required and idx == 0)   # 첫 문장에 마킹
            else "ok"
        )

        audit_sentences.append(AuditSentence(
            sentence_id=sent_id,
            sentence_text=sent_text,
            kesg_item_id=kesg_item_id,
            evidence_node_ids=list(dict.fromkeys(ev_node_ids)),   # 중복 제거
            retrieved_chunk_ids=chunk_ids,
            risk_vector=rv,
            refinement_attempts=ref_attempts,
            hitl_status=hitl_status,
            timestamps={"created": now_iso, "finalized": now_iso},
            model_versions={
                "llm":   SETTINGS.openai_model,
                "embed": SETTINGS.embed_model,
            },
        ))

    # 요약 통계
    hitl_count    = sum(1 for s in audit_sentences if s.hitl_status == "HITL_REQUIRED")
    risk_scores   = [s.risk_vector.risk_score for s in audit_sentences if s.risk_vector]
    avg_risk      = round(sum(risk_scores) / len(risk_scores), 4) if risk_scores else 0.0
    high_risk_axes: list[str] = []
    if audit_sentences:
        from collections import Counter
        axis_counter: Counter[str] = Counter()
        for s in audit_sentences:
            if s.risk_vector:
                axis_counter[s.risk_vector.top_axis] += 1
        high_risk_axes = [ax for ax, _ in axis_counter.most_common(3) if ax]

    summary = {
        "total_sentences":   len(audit_sentences),
        "hitl_count":        hitl_count,
        "avg_risk_score":    avg_risk,
        "high_risk_axes":    high_risk_axes,
        "refinement_total":  len(verification.refinement_attempts),
        "converged":         verification.converged,
    }

    return AuditTrace(
        ticker=report.corp_code,
        corp_name=report.corp_name,
        area=area,
        generated_at=now_iso,
        sentences=audit_sentences,
        summary=summary,
    )


def save_audit_trace(trace: AuditTrace) -> Path:
    """AuditTrace → outputs/audit_trace_{ticker}_{ts}.json 저장."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"audit_trace_{trace.ticker}_{trace.area}_{ts}.json"
    out_path = OUTPUT_DIR / filename
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(trace.to_dict(), fp, ensure_ascii=False, indent=2)
    return out_path


# ---- 내부 헬퍼 --------------------------------------------------------------

def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?。\n])\s+", text.strip())
    return [p.strip() for p in parts if p.strip() and len(p.strip()) > 5]


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _gen_to_chunks(gen: GenerationResult) -> list[dict[str, Any]]:
    """GenerationResult의 RAG 히트를 retrieved_chunks 형식으로 변환."""
    chunks: list[dict[str, Any]] = []
    all_hits = gen.context.kesg_hits + gen.context.industry_hits + gen.context.corp_hits
    for i, (doc, score) in enumerate(all_hits):
        chunks.append({
            "id":    doc.meta.get("code") or doc.meta.get("source") or f"chunk_{i}",
            "text":  doc.text,
            "score": score,
        })
    return chunks


def _match_chunk_ids(sentence: str, chunks: list[dict[str, Any]], top_k: int = 3) -> list[str]:
    """간단 키워드 겹침으로 연관 청크 ID 추출."""
    sentence_words = set(sentence.split())
    scored: list[tuple[int, str]] = []
    for c in chunks:
        overlap = len(sentence_words & set(c["text"].split()))
        if overlap > 0:
            scored.append((overlap, c["id"]))
    scored.sort(reverse=True)
    return [cid for _, cid in scored[:top_k]]


def _guess_kesg_codes(sentence: str, extraction: ExtractionResult) -> list[str]:
    """문장 키워드 기반으로 관련 K-ESG 코드 추론."""
    codes: list[str] = []
    for code, entry in extraction.mapped.items():
        note = entry.get("note", "")
        name = entry.get("name", "")
        if any(kw in sentence for kw in (note, name) if kw):
            codes.append(code)
    return codes[:5]   # 최대 5개


def _build_kesg_sentence_map(
    sentences: list[str],
    extraction: ExtractionResult,
) -> dict[str, str]:
    """sentence_text → kesg_item_id 역매핑.

    각 문장에서 가장 많이 키워드가 겹치는 K-ESG 코드를 선택한다.
    """
    result: dict[str, str] = {}
    for sent in sentences:
        best_code: str | None = None
        best_overlap = 0
        for code, entry in extraction.mapped.items():
            note = entry.get("note", "") or ""
            name = entry.get("name", "") or ""
            overlap = sum(1 for kw in name.split() if kw in sent)
            overlap += sum(1 for kw in note.split() if kw in sent)
            if overlap > best_overlap:
                best_overlap = overlap
                best_code = code
        if best_code:
            result[sent] = best_code
    return result


def _filter_attempts(
    attempts: list[RefinementAttempt],
    sentence: str,
) -> list[RefinementAttempt]:
    """해당 문장이 before_text/after_text에 포함된 시도만 필터링."""
    return [
        a for a in attempts
        if sentence[:30] in a.before_text or sentence[:30] in a.after_text
    ]
