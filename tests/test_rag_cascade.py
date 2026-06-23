from __future__ import annotations

from esgenie.embeddings import BM25Index, IndexedDoc, VectorIndex
from esgenie.rag_gates.cascade import hybrid_search, run_retrieval_cascade


def _indexes(docs: list[IndexedDoc]) -> tuple[VectorIndex, BM25Index]:
    vec = VectorIndex()
    bm25 = BM25Index()
    vec.build(docs)
    bm25.build(docs)
    return vec, bm25


def test_bm25_exact_match_prefers_metric_doc() -> None:
    docs = [
        IndexedDoc(text="온실가스 배출량 120 tCO2eq 2024년", meta={"source": "dart_raw"}, chunk_id="corp_1"),
        IndexedDoc(text="환경 전략 및 계획", meta={"source": "dart_raw"}, chunk_id="corp_2"),
    ]
    _, bm25 = _indexes(docs)

    hits = bm25.search("온실가스 배출량 120", k=2)

    assert hits[0][0].chunk_id == "corp_1"
    assert hits[0][1] > 0


def test_hybrid_search_merges_bm25_and_vector_hits() -> None:
    docs = [
        IndexedDoc(text="재생에너지 사용 비율 31% 2024년", meta={"source": "dart_raw"}, chunk_id="corp_1"),
        IndexedDoc(text="온실가스 배출량 120 tCO2eq 2024년", meta={"source": "dart_raw"}, chunk_id="corp_2"),
        IndexedDoc(text="환경 전략 및 계획", meta={"source": "dart_raw"}, chunk_id="corp_3"),
    ]
    vec, bm25 = _indexes(docs)

    hits = hybrid_search(query="재생에너지 비율 31", vector_index=vec, bm25_index=bm25, k=2)

    assert hits[0][0].chunk_id == "corp_1"
    assert 0.0 <= hits[0][1] <= 1.0


def test_run_retrieval_cascade_accepts_supported_doc() -> None:
    docs = [
        IndexedDoc(text="온실가스 배출량 120 tCO2eq 2024년", meta={"source": "dart_raw"}, chunk_id="corp_1"),
        IndexedDoc(text="재생에너지 사용 비율 31% 2024년", meta={"source": "dart_raw"}, chunk_id="corp_2"),
    ]
    vec, bm25 = _indexes(docs)

    result = run_retrieval_cascade(
        area="E",
        query="온실가스 배출량 120",
        vector_index=vec,
        bm25_index=bm25,
        k=2,
        max_tier=2,
    )

    assert result.decision.decision == "ACCEPT"
    assert result.hits[0][0].chunk_id == "corp_1"
    assert result.decision.queries_tried


def test_run_retrieval_cascade_falls_to_human_at_last_tier() -> None:
    docs = [
        IndexedDoc(text="환경 전략 및 계획", meta={"source": "dart_raw"}, chunk_id="corp_1"),
        IndexedDoc(text="지속가능경영 추진", meta={"source": "dart_raw"}, chunk_id="corp_2"),
    ]
    vec, bm25 = _indexes(docs)

    result = run_retrieval_cascade(
        area="E",
        query="온실가스 배출량 120",
        vector_index=vec,
        bm25_index=bm25,
        k=2,
        max_tier=2,
    )

    assert result.decision.decision == "HUMAN"
    assert result.decision.tier == 2
    assert "R0_no_corp_hits" in result.decision.hard_fails or result.decision.hard_fails
