"""Retrieval cascade: BM25 -> hybrid -> multi-query hybrid."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config import RAG_MAX_TIER
from ..embeddings import BM25Index, IndexedDoc, VectorIndex
from .retrieval_gate import evaluate_retrieval


@dataclass
class CascadeResult:
    hits: list[tuple[IndexedDoc, float]]
    decision: Any
    tier: int
    bm25_hits: list[tuple[IndexedDoc, float]] = field(default_factory=list)
    embed_hits: list[tuple[IndexedDoc, float]] = field(default_factory=list)
    queries_tried: list[str] = field(default_factory=list)
    #: 이 cascade가 실제로 통과한 각 tier의 답안 묶음(최종 tier 우선, 중복 제거).
    #: 판정(`decision`)과 최종 `hits`는 바꾸지 않는다. tier 판정은 top 1 문서만 보므로,
    #: 무관한 top 1 때문에 escalate된 tier의 개별 유효 근거가 그대로 버려진다. 항목별
    #: 추가 검색(`retrieve_for_items`)이 그 근거에 기존 개별 게이트를 다시 적용할 수
    #: 있도록, 이미 계산해 둔 답안 묶음을 기록만 해 둔다. k를 키우는 것이 아니다.
    tier_hits: list[tuple[IndexedDoc, float]] = field(default_factory=list)


def _dedup_tier_hits(
    *groups: list[tuple[IndexedDoc, float]],
) -> list[tuple[IndexedDoc, float]]:
    """각 tier의 답안을 순서대로 합치되 chunk_id 기준 첫 등장만 남긴다."""
    seen: set[str] = set()
    merged: list[tuple[IndexedDoc, float]] = []
    for group in groups:
        for doc, score in group:
            if doc.chunk_id in seen:
                continue
            seen.add(doc.chunk_id)
            merged.append((doc, score))
    return merged


def run_retrieval_cascade(
    *,
    area: str,
    query: str,
    vector_index: VectorIndex,
    bm25_index: BM25Index,
    k: int = 5,
    max_tier: int = RAG_MAX_TIER,
    gate_enabled: bool = True,
) -> CascadeResult:
    if not gate_enabled:
        # 폴백 백엔드 등 게이트 차단 비활성 모드: 하이브리드 검색 결과를 그대로 쓰되
        # 판정은 자문용으로만 계산하고 강제 ACCEPT 하여 생성을 막지 않는다.
        hybrid = hybrid_search(query=query, vector_index=vector_index, bm25_index=bm25_index, k=k)
        bm25_hits = bm25_index.search(query, k=max(k, 8))
        embed_hits = vector_index.search(query, k=max(k, 8))
        decision = evaluate_retrieval(
            area, hybrid, query=query, tier=1, max_tier=max_tier,
            bm25_hits=bm25_hits, embed_hits=embed_hits, queries_tried=[query],
        )
        decision.decision = "ACCEPT"
        if "GATE_FALLBACK_BYPASS" not in decision.soft_flags:
            decision.soft_flags.append("GATE_FALLBACK_BYPASS")
        return CascadeResult(
            hits=hybrid, decision=decision, tier=1,
            bm25_hits=bm25_hits, embed_hits=embed_hits, queries_tried=[query],
            tier_hits=_dedup_tier_hits(hybrid),
        )

    tier0_bm25 = bm25_index.search(query, k=max(k, 8))
    tier0_hits = _normalized_group(tier0_bm25)[:k]
    tier0_decision = evaluate_retrieval(
        area,
        tier0_hits,
        query=query,
        tier=0,
        max_tier=max_tier,
        bm25_hits=tier0_bm25,
        embed_hits=[],
        queries_tried=[query],
    )
    if tier0_decision.decision == "ACCEPT" or max_tier == 0:
        return CascadeResult(
            hits=tier0_hits,
            decision=tier0_decision,
            tier=0,
            bm25_hits=tier0_bm25,
            queries_tried=[query],
            tier_hits=_dedup_tier_hits(tier0_hits),
        )

    tier1_embed = vector_index.search(query, k=max(k, 8))
    tier1_hybrid = hybrid_search(query=query, vector_index=vector_index, bm25_index=bm25_index, k=k)
    tier1_decision = evaluate_retrieval(
        area,
        tier1_hybrid,
        query=query,
        tier=1,
        max_tier=max_tier,
        bm25_hits=tier0_bm25,
        embed_hits=tier1_embed,
        queries_tried=[query],
    )
    if tier1_decision.decision == "ACCEPT" or max_tier == 1:
        return CascadeResult(
            hits=tier1_hybrid,
            decision=tier1_decision,
            tier=1,
            bm25_hits=tier0_bm25,
            embed_hits=tier1_embed,
            queries_tried=[query],
            tier_hits=_dedup_tier_hits(tier1_hybrid, tier0_hits),
        )

    queries = _query_variants(query)
    tier2_hybrid = multi_query_hybrid_search(
        queries=queries,
        vector_index=vector_index,
        bm25_index=bm25_index,
        k=k,
    )
    tier2_embed = vector_index.search(queries[0], k=max(k, 8))
    tier2_bm25 = bm25_index.search(queries[0], k=max(k, 8))
    tier2_decision = evaluate_retrieval(
        area,
        tier2_hybrid,
        query=query,
        tier=2,
        max_tier=max_tier,
        bm25_hits=tier2_bm25,
        embed_hits=tier2_embed,
        queries_tried=queries,
    )
    return CascadeResult(
        hits=tier2_hybrid,
        decision=tier2_decision,
        tier=min(2, max_tier),
        bm25_hits=tier2_bm25,
        embed_hits=tier2_embed,
        queries_tried=queries,
        tier_hits=_dedup_tier_hits(tier2_hybrid, tier1_hybrid, tier0_hits),
    )


def hybrid_search(
    *,
    query: str,
    vector_index: VectorIndex,
    bm25_index: BM25Index,
    k: int = 5,
) -> list[tuple[IndexedDoc, float]]:
    bm25_hits = bm25_index.search(query, k=max(k, 8))
    embed_hits = vector_index.search(query, k=max(k, 8))
    return _merge_hits_weighted([bm25_hits, embed_hits], weights=[0.55, 0.45], k=k, query=query)


def multi_query_hybrid_search(
    *,
    queries: list[str],
    vector_index: VectorIndex,
    bm25_index: BM25Index,
    k: int = 5,
) -> list[tuple[IndexedDoc, float]]:
    ranked_groups: list[list[tuple[IndexedDoc, float]]] = []
    weights: list[float] = []
    for idx, variant in enumerate(queries):
        ranked_groups.append(hybrid_search(query=variant, vector_index=vector_index, bm25_index=bm25_index, k=max(k, 8)))
        weights.append(1.0 if idx == 0 else 0.7)
    return _merge_hits_rrf(ranked_groups, weights=weights, k=k, query=queries[0] if queries else "")


def _query_variants(query: str) -> list[str]:
    seen: set[str] = set()
    variants: list[str] = []

    def add(value: str) -> None:
        cleaned = " ".join(value.replace(",", " ").split()).strip()
        if not cleaned or cleaned in seen:
            return
        seen.add(cleaned)
        variants.append(cleaned)

    add(query)
    for part in query.split(","):
        part = part.strip()
        if len(part) < 2:
            continue
        add(part)
        add(f"{part} 수치")
    return variants[:8]


def _merge_hits_weighted(
    hit_groups: list[list[tuple[IndexedDoc, float]]],
    *,
    weights: list[float],
    k: int,
    query: str = "",
) -> list[tuple[IndexedDoc, float]]:
    score_map: dict[str, float] = {}
    doc_map: dict[str, IndexedDoc] = {}
    for hits, weight in zip(hit_groups, weights):
        norm = _normalize_hits(hits)
        for doc, score in hits:
            doc_map[doc.chunk_id] = doc
            score_map[doc.chunk_id] = score_map.get(doc.chunk_id, 0.0) + weight * norm.get(doc.chunk_id, 0.0)
    for chunk_id, doc in doc_map.items():
        score_map[chunk_id] = score_map.get(chunk_id, 0.0) + _query_bonus(doc, query)
    ranked = sorted(score_map.items(), key=lambda item: item[1], reverse=True)[:k]
    return [(doc_map[chunk_id], round(score, 4)) for chunk_id, score in ranked]


def _merge_hits_rrf(
    hit_groups: list[list[tuple[IndexedDoc, float]]],
    *,
    weights: list[float],
    k: int,
    query: str = "",
    rrf_k: int = 60,
) -> list[tuple[IndexedDoc, float]]:
    score_map: dict[str, float] = {}
    doc_map: dict[str, IndexedDoc] = {}
    for hits, weight in zip(hit_groups, weights):
        for rank, (doc, _) in enumerate(hits, start=1):
            doc_map[doc.chunk_id] = doc
            score_map[doc.chunk_id] = score_map.get(doc.chunk_id, 0.0) + weight / (rrf_k + rank)
    for chunk_id, doc in doc_map.items():
        score_map[chunk_id] = score_map.get(chunk_id, 0.0) + _query_bonus(doc, query) / 10.0
    ranked = sorted(score_map.items(), key=lambda item: item[1], reverse=True)[:k]
    if not ranked:
        return []
    top = ranked[0][1]
    return [(doc_map[chunk_id], round(score / top, 4)) for chunk_id, score in ranked]


def _normalize_hits(hits: list[tuple[IndexedDoc, float]]) -> dict[str, float]:
    if not hits:
        return {}
    scores = [score for _, score in hits]
    hi = max(scores)
    lo = min(scores)
    if hi == lo:
        return {doc.chunk_id: 1.0 for doc, _ in hits}
    return {
        doc.chunk_id: (score - lo) / (hi - lo)
        for doc, score in hits
    }


def _normalized_group(hits: list[tuple[IndexedDoc, float]]) -> list[tuple[IndexedDoc, float]]:
    norm = _normalize_hits(hits)
    return [(doc, round(norm.get(doc.chunk_id, 0.0), 4)) for doc, _ in hits]


def _query_bonus(doc: IndexedDoc, query: str) -> float:
    if not query.strip():
        return 0.0
    bonus = 0.0
    code = str(doc.meta.get("code") or "")
    if code and code in query:
        bonus += 0.35
    if doc.meta.get("source") == "dart_struct":
        bonus += 0.05
    return bonus
