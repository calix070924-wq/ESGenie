"""Layer 2 — Hybrid RAG 보고서 생성 엔진.

3개의 지식 소스를 각각 독립 FAISS 인덱스로 빌드하고, 쿼리에 대해 병렬 검색 후
가중치 합성한 컨텍스트를 LLM에 전달한다.

소스:
1. K-ESG 가이드라인 (기준·best practice)
2. 업종 벤치마크 (산업 평균·핵심 이슈)
3. 자사 DART 원문 스니펫

가중치: (0.40, 0.30, 0.30) — K-ESG 기준을 최우선으로 반영.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .config import BEST_REPORTS_DIR, INDUSTRY_DIR, KESG_DIR, RAG_GATE_FALLBACK_BYPASS
from .dart_client import CompanyReport
from .embeddings import BM25Index, IndexedDoc, VectorIndex, embedding_backend
from .llm import CLIENT
from .rag_gates import hybrid_search, run_retrieval_cascade
from .schemas import RetrievalDecision

WEIGHTS = {"kesg": 0.40, "industry": 0.30, "corp": 0.30}

# 영역별 쿼리 확장에 쓸 SearchTerm 상한 (쿼리 과팽창 방지)
_QUERY_EXPANSION_MAX_TERMS = 12

# KESG/Industry 인덱스는 고정 데이터 — 프로세스 생존 동안 한 번만 빌드
_RAG_SINGLETON: "HybridRAG | None" = None


def _gate_blocking_enabled() -> bool:
    """검색 게이트가 생성을 '차단'할 수 있는지 여부.

    hash-fallback 임베딩에선 점수 스케일이 달라 게이트가 상시 오차단하므로,
    RAG_GATE_FALLBACK_BYPASS가 켜져 있으면 폴백 백엔드에서 차단을 끈다(자문용으로만 동작).
    """
    if RAG_GATE_FALLBACK_BYPASS and embedding_backend() == "hash-fallback":
        return False
    return True


def get_hybrid_rag() -> "HybridRAG":
    """KESG·Industry 인덱스가 로드된 HybridRAG 싱글톤을 반환.

    최초 호출 시에만 인덱스를 빌드하고, 이후 호출은 캐시된 인스턴스를 반환한다.
    corp_index는 run마다 build_rag_with_ssot()가 별도로 빌드하므로 여기서 초기화하지 않는다.
    """
    global _RAG_SINGLETON
    if _RAG_SINGLETON is None:
        _RAG_SINGLETON = HybridRAG()
    return _RAG_SINGLETON


def _expand_query_with_search_terms(query: str, area: str) -> str:
    """기존 큐레이션 쿼리에 해당 영역 지표들의 SearchTerm을 덧붙인다.

    중복·과팽창을 막기 위해 새 키워드만 골라 상한까지만 추가한다.
    kesg_items 임포트는 순환 회피 위해 함수 내부에서 수행.
    """
    from .knowledge.kesg_items import by_area

    have = query
    extra: list[str] = []
    seen: set[str] = set()
    for item in by_area(area):  # type: ignore[arg-type]
        for term in item.search_terms:
            if term in seen or term in have:
                continue
            seen.add(term)
            extra.append(term)
            if len(extra) >= _QUERY_EXPANSION_MAX_TERMS:
                break
        if len(extra) >= _QUERY_EXPANSION_MAX_TERMS:
            break
    if not extra:
        return query
    return f"{query}, " + ", ".join(extra)


def _area_query(area: str) -> str:
    assert area in ("E", "S", "G"), "area must be one of E/S/G"
    return {
        "E": "온실가스, 재생에너지, 폐기물, 용수, 환경 규제 성과",
        "S": "정규직, 이직률, 여성 비율, 산업재해율, 정보보호",
        "G": "사외이사 비율, 이사회 다양성, 출석률, 윤리경영, 감사기구",
    }[area]


@dataclass
class RAGContext:
    kesg_hits: list[tuple[IndexedDoc, float]]
    industry_hits: list[tuple[IndexedDoc, float]]
    corp_hits: list[tuple[IndexedDoc, float]]
    retrieval_tier: int | None = None
    retrieval_decision: RetrievalDecision | None = None

    def as_context_text(self, top_k: int = 2) -> str:
        blocks: list[str] = []
        if self.kesg_hits:
            blocks.append("[K-ESG 기준]")
            for doc, _ in self.kesg_hits[:top_k]:
                blocks.append(f"- [{doc.chunk_id}] {doc.text}")
        if self.industry_hits:
            blocks.append("[업종 벤치마크]")
            for doc, _ in self.industry_hits[:top_k]:
                blocks.append(f"- [{doc.chunk_id}] {doc.text}")
        if self.corp_hits:
            blocks.append("[자사 DART 원문]")
            for doc, _ in self.corp_hits[:top_k]:
                blocks.append(f"- [{doc.chunk_id}] {doc.text}")
        return "\n".join(blocks)

    def all_hits(self) -> list[tuple[IndexedDoc, float]]:
        return self.kesg_hits + self.industry_hits + self.corp_hits

    def as_chunk_dicts(self) -> list[dict[str, Any]]:
        return [
            {"id": doc.chunk_id, "text": doc.text, "score": score}
            for doc, score in self.all_hits()
        ]


@dataclass
class GenerationResult:
    area: str
    text: str
    context: RAGContext
    used_mock_llm: bool


class HybridRAG:
    """3개의 독립 인덱스를 병렬로 검색하는 Multi-Retriever 구조."""

    def __init__(self) -> None:
        self.kesg_index = VectorIndex()
        self.kesg_bm25_index = BM25Index()
        self.industry_index = VectorIndex()
        self.industry_bm25_index = BM25Index()
        self.corp_index = VectorIndex()
        self.corp_bm25_index = BM25Index()
        self._load_kesg()
        self._load_industry()

    # ---- loaders ------------------------------------------------------
    def _load_kesg(self) -> None:
        docs: list[IndexedDoc] = []
        for path in sorted(KESG_DIR.glob("*.json")):
            with open(path, encoding="utf-8") as fp:
                obj = json.load(fp)
            for g in obj.get("guidelines", []):
                text = (
                    f"[{g['code']}] {g['title']}: {g['criteria']} "
                    f"(best practice: {g['best_practice']}; tip: {g['reporting_tips']})"
                )
                docs.append(IndexedDoc(
                    text=text,
                    meta={"code": g["code"], "source": "kesg"},
                    chunk_id=f"kesg_{g['code']}",
                ))
        # 우수 보고서 발췌도 이 인덱스에 합침 (서술 스타일 레퍼런스)
        for path in sorted(BEST_REPORTS_DIR.glob("*.json")):
            with open(path, encoding="utf-8") as fp:
                obj = json.load(fp)
            for idx, e in enumerate(obj.get("excerpts", [])):
                docs.append(IndexedDoc(
                    text=f"[우수사례 {e['area']}/{e['topic']}] {e['text']}",
                    meta={"source": "best_report", "area": e["area"]},
                    chunk_id=f"best_report_{e['area']}_{idx}",
                ))
        self.kesg_index.build(docs)
        self.kesg_bm25_index.build(docs)

    def _load_industry(self) -> None:
        docs: list[IndexedDoc] = []
        for path in sorted(INDUSTRY_DIR.glob("*.json")):
            with open(path, encoding="utf-8") as fp:
                obj = json.load(fp)
            for idx, b in enumerate(obj.get("benchmarks", [])):
                metrics = ", ".join(f"{k}={v}" for k, v in b.get("metrics", {}).items())
                issues = "; ".join(b.get("key_issues", []))
                text = (
                    f"[{b['industry']}] 산업 평균 지표: {metrics}. "
                    f"핵심 이슈: {issues}. 비고: {b.get('notes', '')}"
                )
                docs.append(IndexedDoc(
                    text=text,
                    meta={"industry": b["industry"], "source": "industry"},
                    chunk_id=f"industry_{b['industry']}_{idx}",
                ))
        self.industry_index.build(docs)
        self.industry_bm25_index.build(docs)

    def build_corp_index(self, report: CompanyReport) -> None:
        docs: list[IndexedDoc] = [
            IndexedDoc(
                text=s,
                meta={
                    "source": "dart_raw",
                    "corp_code": report.corp_code,
                    "report_year": report.report_year,
                    "snippet_index": idx,
                },
                chunk_id=f"corp_{report.corp_code}_raw_{idx}",
            )
            for idx, s in enumerate(report.raw_text_snippets)
        ]
        for code, entry in report.kesg_data.items():
            docs.append(IndexedDoc(
                text=f"[DART/{code}] {entry.get('note', '')} 수치: {entry.get('value')} {entry.get('unit', '')}",
                meta={
                    "source": "dart_struct",
                    "code": code,
                    "corp_code": report.corp_code,
                    "report_year": report.report_year,
                },
                chunk_id=f"corp_{report.corp_code}_{code}",
            ))
        self.corp_index.build(docs)
        self.corp_bm25_index.build(docs)

    # ---- retrieval ----------------------------------------------------
    def retrieve(self, query: str, k: int = 3, *, area: str | None = None) -> RAGContext:
        kesg_hits = hybrid_search(
            query=query,
            vector_index=self.kesg_index,
            bm25_index=self.kesg_bm25_index,
            k=k,
        )
        industry_hits = hybrid_search(
            query=query,
            vector_index=self.industry_index,
            bm25_index=self.industry_bm25_index,
            k=k,
        )
        retrieval_tier = 0
        retrieval_decision: RetrievalDecision | None = None
        corp_hits = hybrid_search(
            query=query,
            vector_index=self.corp_index,
            bm25_index=self.corp_bm25_index,
            k=k,
        )
        if area is not None:
            cascade = run_retrieval_cascade(
                area=area,
                query=query,
                vector_index=self.corp_index,
                bm25_index=self.corp_bm25_index,
                k=k,
                gate_enabled=_gate_blocking_enabled(),
            )
            corp_hits = cascade.hits
            retrieval_tier = cascade.tier
            retrieval_decision = cascade.decision
        ctx = RAGContext(
            kesg_hits=kesg_hits,
            industry_hits=industry_hits,
            corp_hits=corp_hits,
            retrieval_tier=retrieval_tier,
            retrieval_decision=retrieval_decision,
        )
        return ctx

    def retrieve_for_area(self, area: str, k: int = 5) -> RAGContext:
        query = _expand_query_with_search_terms(_area_query(area), area)
        return self.retrieve(query, k=k, area=area)

    # ---- generation ---------------------------------------------------
    def generate_section(
        self,
        report: CompanyReport,
        area: str,
        extra_instruction: str | None = None,
        *,
        demo_greenwash: bool = False,
        context: RAGContext | None = None,
    ) -> GenerationResult:
        assert area in ("E", "S", "G"), "area must be one of E/S/G"
        ctx = context or self.retrieve_for_area(area, k=5)
        corp_ctx = report.to_context_dict()
        system = (
            "당신은 한국 K-ESG 가이드라인을 준수하는 ESG 공시 보고서 전문 작성자다. "
            "반드시 제공된 DART 수치만 사용하고, 정량 근거 없는 과장 표현을 피하라."
        )
        area_name = {"E": "환경", "S": "사회", "G": "지배구조"}[area]
        if ctx.retrieval_decision is not None and ctx.retrieval_decision.decision != "ACCEPT":
            return GenerationResult(
                area=area,
                text=_retrieval_blocked_text(area_name, ctx.retrieval_decision),
                context=ctx,
                used_mock_llm=True,
            )
        user = (
            f"회사: {report.corp_name} ({report.industry}, {report.report_year}년)\n"
            f"영역: {area} ({area_name})\n\n"
            f"DART 원문 + 구조화 데이터(JSON):\n{json.dumps(corp_ctx, ensure_ascii=False)}\n\n"
            f"검색된 참조 자료:\n{ctx.as_context_text()}\n\n"
            f"요청: 위 데이터를 바탕으로 {area_name} 영역 보고서 섹션을 아래 형식에 맞춰 작성하시오.\n\n"
            "## [영역명] 성과\n\n"
            "### 전략 및 목표\n"
            "(중장기 전략 방향과 주요 목표를 2~3문장으로 서술. 구체적인 연도·수치 포함)\n\n"
            "### 핵심 지표\n"
            "| 항목 | 실적 | 단위 |\n"
            "|---|---|---|\n"
            "(DART 데이터에서 해당 영역의 주요 정량 지표를 5개 이상 표로 제시)\n\n"
            "### 주요 활동\n"
            "(핵심 지표와 연결된 구체적인 이니셔티브·프로그램을 2~3문장으로 서술)\n\n"
            "### 향후 계획\n"
            "(단기·중기 개선 목표와 실행 방안을 1~2문장으로 서술)\n\n"
            "주의: DART 수치만 사용하고, 근거 없는 과장 표현(혁신적, 압도적, 최고 수준 등)은 사용하지 마시오.\n"
            "모든 주장 문장 끝에는 반드시 하나 이상의 근거 [chunk_id]를 표기하시오. "
            "인용한 chunk 텍스트에 없는 숫자는 절대 쓰지 마시오."
        )
        if extra_instruction:
            user += f"\n\n추가 지시: {extra_instruction}"
        variant = "greenwash" if demo_greenwash else "clean"
        resp = CLIENT.complete(system, user, mock_hint="generate", mock_variant=variant)
        return GenerationResult(area=area, text=resp.content.strip(), context=ctx, used_mock_llm=resp.used_mock)


def _retrieval_blocked_text(area_name: str, decision: RetrievalDecision) -> str:
    reasons = ", ".join(decision.hard_fails[:3]) if decision.hard_fails else "retrieval_gate_failed"
    return (
        f"## {area_name} 성과\n\n"
        "검색 근거가 부족하여 자동 생성하지 않았습니다.\n\n"
        f"사람 검토 필요 사유: {reasons}\n"
    )
