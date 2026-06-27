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
    from esgenie.ssot.ssot_pipeline import extract_with_ssot, build_rag_with_ssot

    graph  = build_unified_graph(dart_report, ocr_extractions, ...)
    l1     = extract_with_ssot(dart_report, graph)
    rag    = HybridRAG()
    build_rag_with_ssot(rag, dart_report, graph)
    ctx    = rag.retrieve("온실가스 감축 목표", k=3)
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from .evidence_graph import EvidenceGraph, EvidenceNode


# ====================================================================
# L1 — extract_with_ssot
# ====================================================================

def extract_with_ssot(
    report: Any,           # esgenie.dart_client.CompanyReport
    graph: EvidenceGraph,
    profile: str | None = None,   # "sme" | "full" | None(자동 판별)
):
    """K-ESG 항목 추출(프로파일 기반) + v15 SSOT 증거 부착.

    Parameters
    ----------
    report : CompanyReport   (v10 DART 보고서 객체)
    graph  : EvidenceGraph   (v15 SSOT — DART + OCR 통합)
    profile: K-ESG 프로파일 — None이면 corp_code로 자동 판별
             (중소기업 → 기본형 28항목, 상장사 → 61항목 전체)

    Returns
    -------
    ExtractionResult  (v10 호환, evidence_node_ids에 OCR 노드 포함)
    """
    from esgenie.layer1_extract import extract as _v10_extract

    # v10 extract()는 evidence_graph.search_nodes(keywords, period) 인터페이스만 사용.
    # v15 EvidenceGraph에 search_nodes()가 추가됐으므로 직접 전달 가능.
    result = _v10_extract(report, evidence_graph=graph, profile=profile)

    _merge_ssot_evidence(result, graph)
    return result


def extract_local_with_ssot(
    graph: EvidenceGraph,
    *,
    corp_code: str,
    corp_name: str,
    report_year: int,
    industry: str = "",
    profile: str | None = None,
):
    """비상장/비DART 경로용 L1 추출.

    SSOT(graph)에 이미 편입된 OCR 정량/정성 노드를 CompanyReport 형태로 얇게 합성한 뒤
    기존 extract_with_ssot()를 재사용한다. 이렇게 하면 공급망 실사·커버리지·D6/ISSB 계산이
    상장사 경로와 같은 ExtractionResult 스키마를 공유한다.
    """
    from esgenie.dart_client import CompanyReport
    from esgenie.knowledge.kesg_items import by_code

    kesg_data: dict[str, dict[str, Any]] = {}
    snippets: list[str] = []

    # metric별 최신/고신뢰 노드를 대표값으로 사용한다.
    best_nodes: dict[str, EvidenceNode] = {}
    for node in graph.nodes.values():
        current = best_nodes.get(node.metric)
        if current is None or (node.period, node.confidence) >= (current.period, current.confidence):
            best_nodes[node.metric] = node

    for code, node in best_nodes.items():
        item = by_code(code)
        kesg_data[code] = {
            "value": node.value,
            "unit": node.unit or (item.unit if item else ""),
            "note": node.source_file or node.source,
        }
        if node.raw_text:
            snippets.append(node.raw_text)

    text_by_code: dict[str, list[Any]] = defaultdict(list)
    for tnode in graph.text_nodes.values():
        if not tnode.kesg_code:
            continue
        text_by_code[tnode.kesg_code].append(tnode)
        snippets.append(tnode.text)

    for code, nodes in text_by_code.items():
        if code in kesg_data:
            continue
        item = by_code(code)
        kesg_data[code] = {
            "value": "문서 조항 확인",
            "unit": "",
            "note": nodes[0].section if nodes else (item.name if item else ""),
        }

    synthetic = CompanyReport(
        corp_code=corp_code,
        corp_name=corp_name,
        industry=industry,
        report_year=report_year,
        financials={},
        kesg_data=kesg_data,
        raw_text_snippets=snippets[:20],
        source="ssot_local",
    )
    result = extract_with_ssot(synthetic, graph, profile=profile)
    result.notes.append("SSOT 로컬 추출: OCR 정량/정성 노드로 비상장 경로 L1 매핑")
    return result


def _merge_ssot_evidence(result: Any, graph: EvidenceGraph) -> None:
    """SSOT graph의 OCR/TextNode를 L1 결과에 병합."""
    # v10 extract()는 DART 노드만 탐색하므로, OCR 출처(ocr_structured / ocr_unstructured)
    # 노드도 evidence_node_ids에 추가하고 'no_evidence' 플래그를 해소한다.
    ocr_by_metric: dict[str, list[str]] = {}
    # 코드별 대표 OCR 노드(최신 연도·고신뢰 우선) — DART 미공시 코드를 승격할 때 표시값 출처.
    ocr_repr: dict[str, EvidenceNode] = {}
    for node in graph.nodes.values():
        if node.origin in ("ocr_structured", "ocr_unstructured"):
            ocr_by_metric.setdefault(node.metric, []).append(node.id)
            current = ocr_repr.get(node.metric)
            if current is None or (node.period, node.confidence) >= (current.period, current.confidence):
                ocr_repr[node.metric] = node

    # 정성 조항(TextNode)도 존재형 문항의 증빙 근거다. 규정집/회의록에서 매핑된
    # K-ESG 코드가 있으면 해당 항목의 evidence_node_ids에 편입한다.
    text_by_code: dict[str, list[str]] = {}
    for tnode in graph.text_nodes.values():
        if tnode.kesg_code:
            text_by_code.setdefault(tnode.kesg_code, []).append(tnode.id)

    for code, entry in result.mapped.items():
        ocr_ids = ocr_by_metric.get(code, [])
        text_ids = text_by_code.get(code, [])
        if ocr_ids or text_ids:
            existing = entry.get("evidence_node_ids") or []
            entry["evidence_node_ids"] = list(dict.fromkeys(existing + ocr_ids + text_ids))
        if ocr_ids:
            # OCR 증거가 생겼으면 'no_evidence' 플래그 제거
            flags = result.confidence_flags.get(code, [])
            if "no_evidence" in flags:
                result.confidence_flags[code] = [f for f in flags if f != "no_evidence"]
                result.notes.append(f"[OCR resolved] {code}: OCR 증빙 노드로 근거 확보")

    # DART에 없더라도 OCR 정량노드(전기·가스·수도·폐기물 등)나 규정집/회의록 TextNode가
    # 있으면 공시 항목으로 승격한다. 기존엔 TextNode(정성)만 승격하고 OCR 정량노드는
    # 'DART가 먼저 만든 mapped 항목'에만 보조증거로 붙어, DART 미공시 코드의 OCR 정량값이
    # 통째로 버려졌다(예: 재활용률 E-6-2, 용수 E-5-1 — 공시 항목 표에서 누락).
    # by_code()로 유효 K-ESG 코드만 승격해 깨진 VLM hint('CSPD count' 등)는 자동 제외하며,
    # 이 게이트가 그래프 빌드 단계의 중복가드(지정폐기물 차단·재활용량/률 구분)도 그대로 보존한다.
    candidate_codes = sorted(set(ocr_by_metric) | set(text_by_code))
    if candidate_codes:
        from esgenie.knowledge.kesg_items import by_code, items_for_profile

        profile_codes = {item.code for item in items_for_profile(result.profile)}
        quant_added = 0
        text_added = 0
        for code in candidate_codes:
            if code in result.mapped:
                continue
            item = by_code(code)
            if item is None:
                continue
            ocr_ids = ocr_by_metric.get(code, [])
            text_ids = text_by_code.get(code, [])
            repr_node = ocr_repr.get(code)
            if repr_node is not None:
                # 정량 OCR 노드 → 실제 수치로 채움
                value: Any = repr_node.value
                unit = repr_node.unit or (item.unit or "")
                note = f"OCR 정량 증빙으로 자동 인식 ({repr_node.source_file or repr_node.source})"
                quant_added += 1
            else:
                # 정성 TextNode만 있는 존재형 문항 → 문서 조항 확인
                value = "문서 조항 확인"
                unit = ""
                note = "OCR 정성 증빙으로 자동 인식"
                text_added += 1
            in_profile = code in profile_codes
            result.mapped[code] = {
                "code": item.code,
                "name": item.name,
                "area": item.area,
                "category": item.category,
                "data_type": item.data_type,
                "value": value,
                "unit": unit,
                "note": note,
                "evidence_node_ids": list(dict.fromkeys(ocr_ids + text_ids)),
                "beyond_profile": not in_profile,
            }
            if in_profile:
                if code in result.missing:
                    result.missing.remove(code)
                result.by_area[item.area]["present"] += 1
            elif code not in result.beyond_profile:
                result.beyond_profile.append(code)

        if quant_added or text_added:
            profile_items = items_for_profile(result.profile)
            in_profile_mapped = sum(
                1 for entry in result.mapped.values() if not entry.get("beyond_profile")
            )
            result.coverage_pct = 100 * in_profile_mapped / len(profile_items)
            if quant_added:
                result.notes.append(
                    f"OCR 정량 증빙 승격: {quant_added}개 항목을 내부 증빙 수치로 자동 채움"
                )
            if text_added:
                result.notes.append(
                    f"TextNode 증빙 병합: {text_added}개 항목을 규정/회의록 근거로 자동 채움"
                )

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

    from esgenie.embeddings import BM25Index, VectorIndex
    new_index = VectorIndex()
    new_index.build(existing + extra_docs)
    rag.corp_index = new_index
    if hasattr(rag, "corp_bm25_index"):
        new_bm25 = BM25Index()
        new_bm25.build(existing + extra_docs)
        rag.corp_bm25_index = new_bm25


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
