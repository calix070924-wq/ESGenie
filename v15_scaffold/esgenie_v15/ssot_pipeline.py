"""L1/L2 SSOT 연결 브리지 (v15).

v10의 Layer1(K-ESG 추출)과 Layer2(Hybrid RAG)를 v15 EvidenceGraph(SSOT)와 연결한다.

연결 포인트
-----------
  L1 — extract_with_ssot()
    · v10 extract()에 v15 EvidenceGraph를 직접 전달
      → v10이 search_nodes()를 호출해 증거 노드 ID를 자동 부착
    · OCR 출처 노드가 있는 항목은 evidence_node_ids에 ocr_* 노드도 병합
    · 'no_evidence' 플래그가 있는 항목을 OCR 노드로 해소(resolve) 처리

  L2 — build_rag_with_ssot()
    · v10 HybridRAG.build_corp_index()로 DART 원문 인덱스 먼저 빌드
    · SSOT TextNode(규정집·회의록)를 corp_index에 추가 편입
      → D3 의미일관성 검증과 규정 검증(P축)이 같은 인덱스를 공유
    · SSOT EvidenceNode(OCR 수치)를 corp_index에 추가 편입
      → "E-4-1 128400 kWh (kepco_bill.pdf)" 같은 증빙 문자열로 검색 가능

사용 예시 (app.py)
------------------
    from esgenie_v15.ssot_pipeline import extract_with_ssot, build_rag_with_ssot

    graph  = build_unified_graph(dart_report, ocr_extractions, ...)
    l1     = extract_with_ssot(dart_report, graph)
    rag    = HybridRAG()
    build_rag_with_ssot(rag, dart_report, graph)
    ctx    = rag.retrieve("온실가스 감축 목표", k=3)
"""
from __future__ import annotations

from typing import Any

from .evidence_graph import EvidenceGraph, EvidenceNode


# ====================================================================
# L1 — extract_with_ssot
# ====================================================================

def extract_with_ssot(
    report: Any,           # esgenie.dart_client.CompanyReport
    graph: EvidenceGraph,
):
    """K-ESG 61개 항목 추출 + v15 SSOT 증거 부착.

    Parameters
    ----------
    report : CompanyReport   (v10 DART 보고서 객체)
    graph  : EvidenceGraph   (v15 SSOT — DART + OCR 통합)

    Returns
    -------
    ExtractionResult  (v10 호환, evidence_node_ids에 OCR 노드 포함)
    """
    from esgenie.layer1_extract import extract as _v10_extract

    # v10 extract()는 evidence_graph.search_nodes(keywords, period) 인터페이스만 사용.
    # v15 EvidenceGraph에 search_nodes()가 추가됐으므로 직접 전달 가능.
    result = _v10_extract(report, evidence_graph=graph)

    # ── OCR 노드 증거 병합 ────────────────────────────────────────────
    # v10 extract()는 DART 노드만 탐색하므로, OCR 출처(ocr_structured / ocr_unstructured)
    # 노드도 evidence_node_ids에 추가하고 'no_evidence' 플래그를 해소한다.
    ocr_by_metric: dict[str, list[str]] = {}
    for node in graph.nodes.values():
        if node.origin in ("ocr_structured", "ocr_unstructured"):
            ocr_by_metric.setdefault(node.metric, []).append(node.id)

    for code, entry in result.mapped.items():
        ocr_ids = ocr_by_metric.get(code, [])
        if ocr_ids:
            existing = entry.get("evidence_node_ids") or []
            entry["evidence_node_ids"] = list(dict.fromkeys(existing + ocr_ids))
            # OCR 증거가 생겼으면 'no_evidence' 플래그 제거
            flags = result.confidence_flags.get(code, [])
            if "no_evidence" in flags:
                result.confidence_flags[code] = [f for f in flags if f != "no_evidence"]
                result.notes.append(f"[OCR resolved] {code}: OCR 증빙 노드로 근거 확보")

    # ── 커버리지 메모 추가 ─────────────────────────────────────────────
    ocr_resolved = sum(
        1 for code, entry in result.mapped.items()
        if any(
            nid for nid in (entry.get("evidence_node_ids") or [])
            if "__ocr_" in nid
        )
    )
    if ocr_resolved:
        result.notes.append(f"OCR 증빙 병합: {ocr_resolved}개 항목에 내부 증빙 노드 부착")

    return result


# ====================================================================
# L2 — build_rag_with_ssot
# ====================================================================

def build_rag_with_ssot(
    rag: Any,              # esgenie.layer2_rag.HybridRAG
    report: Any,           # CompanyReport
    graph: EvidenceGraph,
) -> None:
    """HybridRAG corp_index에 DART + SSOT(TextNode + OCR 수치) 모두 편입.

    Parameters
    ----------
    rag    : HybridRAG  (v10 인스턴스 — kesg/industry 인덱스는 이미 로드된 상태)
    report : CompanyReport
    graph  : EvidenceGraph  (v15 SSOT)

    Side-effect
    -----------
    rag.corp_index를 DART + OCR 텍스트로 (재)빌드한다.
    기존 build_corp_index()를 먼저 호출한 뒤 TextNode/수치 노드를 추가 편입한다.
    """
    from esgenie.embeddings import IndexedDoc

    # ── DART 원문 인덱스 먼저 빌드 (v10 원본 로직) ────────────────────
    rag.build_corp_index(report)

    # ── SSOT TextNode 추가 편입 (규정집·회의록 조항) ───────────────────
    text_docs: list[IndexedDoc] = []
    for tnode in graph.text_nodes.values():
        code_tag = f"[{tnode.kesg_code}] " if tnode.kesg_code else ""
        text = (
            f"{code_tag}{tnode.section}: {tnode.text}"
            f" (출처: {tnode.source_file}, p.{tnode.page})"
        )
        text_docs.append(IndexedDoc(
            text=text,
            meta={
                "source": "ssot_text",
                "kesg_code": tnode.kesg_code,
                "source_file": tnode.source_file,
                "node_id": tnode.id,
            },
        ))

    # ── SSOT OCR 수치 노드 추가 편입 ──────────────────────────────────
    ocr_docs: list[IndexedDoc] = []
    for node in graph.nodes.values():
        if node.origin in ("ocr_structured", "ocr_unstructured"):
            text = (
                f"[{node.metric}] {node.value}{node.unit} "
                f"({node.period}년, 출처: {node.source_file or node.source}, "
                f"신뢰도: {node.confidence:.2f})"
            )
            ocr_docs.append(IndexedDoc(
                text=text,
                meta={
                    "source": "ssot_ocr",
                    "kesg_code": node.metric,
                    "source_file": node.source_file,
                    "origin": node.origin,
                    "node_id": node.id,
                },
            ))

    # ── corp_index에 추가 문서 편입 ────────────────────────────────────
    # VectorIndex.add()가 없을 경우 기존 문서 위에 rebuild 방식으로 처리
    extra = text_docs + ocr_docs
    if extra:
        _extend_corp_index(rag, extra)


def _extend_corp_index(rag: Any, extra_docs: list[Any]) -> None:
    """기존 corp_index에 문서를 추가 편입.

    VectorIndex가 add() API를 제공하면 그것을 사용하고,
    없으면 기존 문서를 꺼내 합쳐서 rebuild한다.
    """
    index = rag.corp_index

    # ── add() API 있으면 직접 추가 ────────────────────────────────────
    if hasattr(index, "add"):
        index.add(extra_docs)
        return

    # ── rebuild 방식 폴백 ─────────────────────────────────────────────
    # VectorIndex 내부 문서 목록을 꺼내 합쳐 rebuild
    existing: list[Any] = []
    if hasattr(index, "_docs"):
        existing = list(index._docs)

    from esgenie.embeddings import VectorIndex
    new_index = VectorIndex()
    new_index.build(existing + extra_docs)
    rag.corp_index = new_index


# ====================================================================
# 유틸 — SSOT 요약 출력 (디버그/로깅용)
# ====================================================================

def ssot_summary(graph: EvidenceGraph) -> dict[str, Any]:
    """EvidenceGraph의 출처별 통계를 반환 (로그·UI 표시용)."""
    from collections import Counter
    origins = Counter(n.origin for n in graph.nodes.values())
    return {
        "corp": graph.corp_name,
        "total_nodes": len(graph.nodes),
        "by_origin": dict(origins),
        "text_nodes": len(graph.text_nodes),
        "edges": len(graph.edges),
        "cross_check_edges": sum(1 for e in graph.edges if e.edge_type == "cross_check"),
    }
