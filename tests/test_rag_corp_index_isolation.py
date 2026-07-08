"""이슈1 — RAG corp_index 싱글톤 오염 회귀 방지 테스트.

get_hybrid_rag()가 반환하는 공유 HybridRAG 인스턴스는 kesg/industry 인덱스만
들고 있어야 하고, 회사별 corp 인덱스는 build_rag_with_ssot()가 호출마다 만들어
반환하는 CorpIndex로만 흘러야 한다(싱글톤 속성으로 얹히면 안 됨).
"""
from __future__ import annotations

from esgenie.dart_client import load_report
from esgenie.layer2_rag import CorpIndex, HybridRAG, get_hybrid_rag
from esgenie.ssot.evidence_graph import build_unified_graph
from esgenie.ssot.ocr_router import DocChannel, ExtractedMetric, OcrExtraction
from esgenie.ssot.ssot_pipeline import build_rag_with_ssot


def _kepco_extraction_for(corp_code: str, value: float) -> OcrExtraction:
    return OcrExtraction(
        source_file=f"{corp_code}_kepco_bill.pdf",
        channel=DocChannel.STRUCTURED,
        doc_type="kepco_bill",
        metrics=[ExtractedMetric(
            metric_hint="사용전력량", value=value, unit="kWh",
            period="2025-12", kesg_code_guess="E-4-1", confidence=0.93,
        )],
    )


def _graph_for(report, value: float):
    return build_unified_graph(
        report, [_kepco_extraction_for(report.corp_code, value)],
        corp_code=report.corp_code, corp_name=report.corp_name,
        report_year=report.report_year,
    )


def test_corp_index_isolated_across_consecutive_builds():
    report_a = load_report("005930")
    report_b = load_report("005380")
    graph_a = _graph_for(report_a, 111_111.0)
    graph_b = _graph_for(report_b, 222_222.0)

    rag = HybridRAG()
    corp_a = build_rag_with_ssot(rag, report_a, graph_a)
    corp_b = build_rag_with_ssot(rag, report_b, graph_b)

    assert corp_a is not corp_b
    assert isinstance(corp_a, CorpIndex) and isinstance(corp_b, CorpIndex)

    # B를 빌드한 뒤에도 corp_a는 A 데이터만 유지해야 한다(오염되지 않음).
    docs_a = getattr(corp_a.vector, "_docs", [])
    docs_b = getattr(corp_b.vector, "_docs", [])

    chunk_ids_a = {d.chunk_id for d in docs_a}
    chunk_ids_b = {d.chunk_id for d in docs_b}

    assert any(cid.startswith(f"corp_{report_a.corp_code}_") for cid in chunk_ids_a)
    assert not any(cid.startswith(f"corp_{report_a.corp_code}_") for cid in chunk_ids_b)
    assert any(cid.startswith(f"corp_{report_b.corp_code}_") for cid in chunk_ids_b)
    assert not any(cid.startswith(f"corp_{report_b.corp_code}_") for cid in chunk_ids_a)

    # SSOT로 추가 편입된 OCR 문서도 회사별로 분리되어야 한다.
    assert any(d.meta.get("source_file") == f"{report_a.corp_code}_kepco_bill.pdf" for d in docs_a)
    assert not any(d.meta.get("source_file") == f"{report_a.corp_code}_kepco_bill.pdf" for d in docs_b)
    assert any(d.meta.get("source_file") == f"{report_b.corp_code}_kepco_bill.pdf" for d in docs_b)
    assert not any(d.meta.get("source_file") == f"{report_b.corp_code}_kepco_bill.pdf" for d in docs_a)


def test_shared_singleton_does_not_hold_corp_index():
    rag = get_hybrid_rag()
    assert not hasattr(rag, "corp_index")
    assert not hasattr(rag, "corp_bm25_index")


def test_get_hybrid_rag_returns_same_instance_with_stable_kesg_industry():
    r1 = get_hybrid_rag()
    r2 = get_hybrid_rag()
    assert r1 is r2
    assert r1.kesg_index is r2.kesg_index
    assert r1.industry_index is r2.industry_index


def test_get_hybrid_rag_builds_kesg_industry_only_once(monkeypatch):
    """싱글톤을 반복 호출해도 kesg/industry는 최초 1회만 빌드되고,
    corp만 매 호출마다 build_rag_with_ssot()로 새로 빌드됨을 확인."""
    import esgenie.layer2_rag as layer2_rag

    monkeypatch.setattr(layer2_rag, "_RAG_SINGLETON", None)

    calls = {"kesg": 0, "industry": 0}
    original_load_kesg = layer2_rag.HybridRAG._load_kesg
    original_load_industry = layer2_rag.HybridRAG._load_industry

    def counting_load_kesg(self):
        calls["kesg"] += 1
        return original_load_kesg(self)

    def counting_load_industry(self):
        calls["industry"] += 1
        return original_load_industry(self)

    monkeypatch.setattr(layer2_rag.HybridRAG, "_load_kesg", counting_load_kesg)
    monkeypatch.setattr(layer2_rag.HybridRAG, "_load_industry", counting_load_industry)

    rag1 = layer2_rag.get_hybrid_rag()
    rag2 = layer2_rag.get_hybrid_rag()
    rag3 = layer2_rag.get_hybrid_rag()

    assert rag1 is rag2 is rag3
    assert calls["kesg"] == 1
    assert calls["industry"] == 1

    report = load_report("005930")
    corp1 = rag1.build_corp_index(report)
    corp2 = rag1.build_corp_index(report)
    assert corp1 is not corp2  # corp는 호출마다 새로 빌드됨
    assert calls["kesg"] == 1 and calls["industry"] == 1  # kesg/industry는 여전히 재빌드 안 됨


def test_extend_corp_index_vector_bm25_parity():
    """extra_docs 확장 후 corp.vector와 corp.bm25가 동일한 문서 집합(개수·ID)을 보유하는지 검증.

    rebuild-only 경로에서 vector·bm25가 동일한 (기존 + extra) docs로 함께 재빌드된다.
    add() 증분 분기가 남아 있으면 corp.bm25는 갱신되지 않아 이 테스트가 깨진다.
    """
    from esgenie.embeddings import BM25Index, IndexedDoc, VectorIndex
    from esgenie.layer2_rag import CorpIndex
    from esgenie.ssot.ssot_pipeline import _extend_corp_index

    initial_docs = [
        IndexedDoc(text="삼성전자 온실가스 배출량 2023", meta={"source": "dart"}),
        IndexedDoc(text="삼성전자 산재율 보고서", meta={"source": "dart"}),
    ]
    v = VectorIndex()
    v.build(initial_docs)
    b = BM25Index()
    b.build(initial_docs)
    corp = CorpIndex(vector=v, bm25=b)

    extra = [IndexedDoc(text="삼성전자 폐기물 재활용률 현황", meta={"source": "ssot"})]
    corp2 = _extend_corp_index(corp, extra)

    vec_ids = {d.chunk_id for d in corp2.vector._docs}
    bm25_ids = {d.chunk_id for d in corp2.bm25._docs}

    assert len(corp2.vector._docs) == len(initial_docs) + len(extra), (
        f"vector 문서 수 불일치: {len(corp2.vector._docs)}"
    )
    assert len(corp2.bm25._docs) == len(initial_docs) + len(extra), (
        f"bm25 문서 수 불일치: {len(corp2.bm25._docs)}"
    )
    assert vec_ids == bm25_ids, (
        f"vector-only IDs: {vec_ids - bm25_ids}, bm25-only IDs: {bm25_ids - vec_ids}"
    )
