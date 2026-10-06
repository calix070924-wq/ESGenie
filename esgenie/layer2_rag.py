"""Layer 2 — Hybrid RAG 보고서 생성 엔진.

3개의 지식 소스를 각각 독립 FAISS 인덱스로 빌드하고, 쿼리에 대해 병렬 검색 후
가중치 합성한 컨텍스트를 LLM에 전달한다.

소스:
1. K-ESG 가이드라인 (기준·best practice)
2. 업종 벤치마크 (산업 평균·핵심 이슈)
3. 자사 공시·증빙 원문 스니펫

가중치: (0.40, 0.30, 0.30) — K-ESG 기준을 최우선으로 반영.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from collections.abc import Iterable
from typing import Any

from .config import BEST_REPORTS_DIR, INDUSTRY_DIR, KESG_DIR, RAG_GATE_FALLBACK_BYPASS
from .dart_client import CompanyReport
from .embeddings import BM25Index, IndexedDoc, VectorIndex, embedding_backend
from .llm import CLIENT
from .rag_gates import evaluate_retrieval, hybrid_search, run_retrieval_cascade
from .schemas import RetrievalDecision
from .knowledge.kesg_items import KESGItem

WEIGHTS = {"kesg": 0.40, "industry": 0.30, "corp": 0.30}

# 영역별 쿼리 확장에 쓸 SearchTerm 상한 (쿼리 과팽창 방지)
_QUERY_EXPANSION_TERMS_PER_ITEM = 2

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

    항목을 라운드로빈으로 돌며 항목당 `_QUERY_EXPANSION_TERMS_PER_ITEM`개씩 채운다.
    이전에는 항목 순서대로 총 12개에서 끊었는데, 그러면 앞쪽 두세 항목의 용어만
    들어가고 영역 뒷부분은 한 번도 등장하지 못했다. 실측에서 S영역 확장어가
    S-1·S-2(사회책임 목표·신규 채용…)로만 채워져 산업재해(S-4-2)까지 도달하지
    못했고, 재해율 청크가 R3_query_keyword_missing으로 오차단됐다.

    항목당 2개인 이유: S-4-2의 첫 용어 '산업재해율'은 "산업재해 현황 — 재해율"과
    연속 일치하지 않고, 두 번째 용어 '재해율'에서 비로소 걸린다. 1개면 되살림 1/3,
    2개면 3/3이고 무관 청크 오통과는 두 경우 모두 0이었다.

    kesg_items 임포트는 순환 회피 위해 함수 내부에서 수행.
    """
    from .knowledge.kesg_items import by_area

    items = list(by_area(area))  # type: ignore[arg-type]
    extra: list[str] = []
    seen: set[str] = set()
    for depth in range(_QUERY_EXPANSION_TERMS_PER_ITEM):
        for item in items:
            if depth >= len(item.search_terms):
                continue
            term = item.search_terms[depth]
            if term in seen or term in query:
                continue
            seen.add(term)
            extra.append(term)
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

    def as_context_text(self, top_k: int | None = None) -> str:
        """선정 근거 전체를 전달한다. 검증기가 보는 all_hits()와 기본 범위를 맞춘다."""
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
            blocks.append("[자사 공시·증빙 원문]")
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
    # 생성 경로가 결정적으로 넣은 표(원장 표·원문 확인 수치 표) 원문. 보고서 조립의 본문 대조가 LLM 표와 출처로
    # 구분한다(PR71 재검토 B — 표 제목·열 이름으로 가리지 않는다).
    system_tables: tuple[str, ...] = ()


@dataclass
class CorpIndex:
    """회사별 corp 인덱스 쌍. HybridRAG 싱글톤에 얹지 않고 호출마다 만들어 전달한다."""

    vector: VectorIndex
    bm25: BM25Index


# R1은 "검색이 뭔가 찾았는가"를 보는 **묶음 1위 전용** 점수 검사다. 점수는 tier마다 다른
# 방식으로 정규화되므로(tier0 min-max, tier1 가중합, tier2 RRF/최댓값) 2위 이하 후보는
# 내용이 정확해도 구조적으로 RAG_R1_MIN을 넘지 못한다. 실측: 2024년 환경 규제 위반 1건
# 기록이 tier1 3위 점수 0.4707로 R1_low_top1_score 탈락. 묶음 단위 점수 요건은
# `decision`(cascade 판정)이 이미 강제하므로, 2위 이하 후보는 내용 기준으로만 판단한다.
# RAG_R1_MIN·RAG_R2_MIN 등 임계값과 영역 검색 판정은 바꾸지 않는다.
_SCALE_ONLY_HARD_FAILS = frozenset({"R1_low_top1_score"})


@dataclass
class ItemRetrievalResult:
    """별도 확인 목록의 항목별 검색. 기존 보고서 원장·점수를 갱신하지 않는다."""

    item_code: str
    area: str
    query: str
    hits: list[tuple[IndexedDoc, float]]
    decision: RetrievalDecision
    hit_decisions: dict[str, RetrievalDecision] = field(default_factory=dict)
    #: 개별 게이트를 통과한 근거를 몇 개까지 붙일지. 기존 검색 k와 같은 값을 쓴다.
    accept_limit: int | None = None

    @property
    def accepted_hits(self) -> list[tuple[IndexedDoc, float]]:
        if self.decision.decision != "ACCEPT":
            return []
        accepted: list[tuple[IndexedDoc, float]] = []
        for rank, (doc, score) in enumerate(self.hits):
            decision = self.hit_decisions.get(doc.chunk_id)
            if decision is None:
                continue
            if rank == 0:
                if decision.decision != "ACCEPT":
                    continue
            elif set(decision.hard_fails) - _SCALE_ONLY_HARD_FAILS:
                continue
            accepted.append((doc, score))
        return accepted if self.accept_limit is None else accepted[: self.accept_limit]

    def as_chunk_dicts(self) -> list[dict[str, Any]]:
        return [
            {"id": doc.chunk_id, "text": doc.text, "meta": dict(doc.meta), "score": score}
            for doc, score in self.accepted_hits
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_code": self.item_code, "area": self.area, "query": self.query,
            "decision": self.decision.to_dict(),
            "hits": [
                {"id": doc.chunk_id, "text": doc.text, "meta": dict(doc.meta), "score": score,
                 "decision": self.hit_decisions[doc.chunk_id].to_dict()
                 if doc.chunk_id in self.hit_decisions else None}
                for doc, score in self.hits
            ],
            "accepted_chunk_ids": [doc.chunk_id for doc, _ in self.accepted_hits],
        }


class HybridRAG:
    """3개의 독립 인덱스를 병렬로 검색하는 Multi-Retriever 구조."""

    def __init__(self) -> None:
        # kesg/industry 인덱스는 고정 데이터 — get_hybrid_rag() 싱글톤이 공유한다.
        # corp 인덱스는 회사별로 달라지므로 여기서 들고 있지 않고,
        # build_corp_index()가 호출마다 CorpIndex를 새로 만들어 반환한다.
        self.kesg_index = VectorIndex()
        self.kesg_bm25_index = BM25Index()
        self.industry_index = VectorIndex()
        self.industry_bm25_index = BM25Index()
        # 영역 질의는 동의어를 붙여 128토큰을 넘고 뒤쪽 용어가 임베딩 질의에서 통째로
        # 빠진다(2026-09-26 실측: E 37개 중 10개, S 45개 중 16개, G 36개 중 12개).
        # 그래서 `VectorIndex.split_query`를 만들어 켜 봤지만 **실측이 이득을 지지하지
        # 않아 켜지 않았다** — 잘리던 용어를 담은 문서의 최고 순위가 상승 2건·하락 8건이고,
        # 잘린 용어 절반 이상은 이 코퍼스에 해당 문서가 아예 없었다. 질의를 조각내 문서별
        # 최고값을 쓰면 짧은 조각이 순위 분포를 흔든다. 근거:
        # outputs/diagnostics/20260925_area_search_split/query_split_term_ranks.json
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

    def build_corp_index(self, report: CompanyReport) -> CorpIndex:
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
        vector_index = VectorIndex()
        vector_index.build(docs)
        bm25_index = BM25Index()
        bm25_index.build(docs)
        return CorpIndex(vector=vector_index, bm25=bm25_index)

    # ---- retrieval ----------------------------------------------------
    def retrieve(self, query: str, k: int = 3, *, area: str | None = None, corp: CorpIndex) -> RAGContext:
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
            vector_index=corp.vector,
            bm25_index=corp.bm25,
            k=k,
        )
        if area is not None:
            cascade = run_retrieval_cascade(
                area=area,
                query=query,
                vector_index=corp.vector,
                bm25_index=corp.bm25,
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

    def retrieve_for_area(self, area: str, k: int = 5, *, corp: CorpIndex) -> RAGContext:
        query = _expand_query_with_search_terms(_area_query(area), area)
        return self.retrieve(query, k=k, area=area, corp=corp)

    def retrieve_for_items(
        self, items: Iterable[KESGItem], *, corp: CorpIndex, k: int = 3,
    ) -> list[ItemRetrievalResult]:
        """항목 정의에서 질의를 자동 만들고 별도 인덱스의 근거를 추가 검색한다.

        정답 문구·특정 회사명 없이 항목명·설명과 기존 동의어 사전을 사용한다. 기존
        영역 검색과 원장을 덮어쓰지 않으며, 모든 후보에 원래 검색 게이트를 적용한다.
        """
        if k < 1:
            raise ValueError("k must be positive")
        vector = VectorIndex(model_name=corp.vector.model_name)
        docs = vector.split_documents(corp.vector._docs)
        # SSOT 메타데이터의 항목 코드 별칭을 게이트가 읽는 이름으로 연결한다.
        # 코드가 없는 근거에 질의의 코드를 붙여 정성 예외를 만드는 일은 하지 않는다.
        for doc in docs:
            if not doc.meta.get("code") and doc.meta.get("kesg_code"):
                doc.meta["code"] = doc.meta["kesg_code"]
        vector.build(docs)
        bm25 = BM25Index()
        bm25.build(docs)
        results: list[ItemRetrievalResult] = []
        seen: set[str] = set()
        for item in items:
            if item.code in seen:
                continue
            seen.add(item.code)
            query = ", ".join(dict.fromkeys(
                part for part in (item.name, item.description, *item.search_terms[:2]) if part.strip()
            ))
            if item.area not in ("E", "S", "G"):
                decision = RetrievalDecision(
                    decision="HUMAN", tier=0, top1_score=0.0,
                    field_coverage={}, hard_fails=["R0_unsupported_area"], soft_flags=[],
                    chunk_ids=[], scores=[], queries_tried=[query],
                )
                results.append(ItemRetrievalResult(item.code, item.area, query, [], decision))
                continue
            cascade = run_retrieval_cascade(
                area=item.area, query=query, vector_index=vector, bm25_index=bm25,
                k=k, gate_enabled=True,
            )
            # cascade가 실제로 통과한 각 tier의 답안을 모두 후보로 본다. tier 판정은 top 1
            # 문서만 보므로, 무관한 top 1(예: '안건: 산업안전보건법 …') 때문에 escalate된
            # tier에서 정작 유효한 근거(예: 2024년 환경 규제 위반 1건 기록)가 함께 버려지고,
            # 다음 tier의 다중 질의 RRF는 여러 변형에 두루 걸리는 일반 정책 문구를 앞세운다.
            # k를 키우거나 게이트를 완화하지 않는다. 후보마다 기존 개별 게이트를 다시 적용해
            # ACCEPT인 근거만 남기며, 채택 수는 기존 k로 묶어 둔다.
            candidates = cascade.tier_hits or cascade.hits
            hit_decisions = {
                doc.chunk_id: evaluate_retrieval(
                    item.area, [(doc, score)], query=query,
                    tier=cascade.tier, queries_tried=cascade.queries_tried,
                )
                for doc, score in candidates
            }
            results.append(ItemRetrievalResult(
                item.code, item.area, query, candidates, cascade.decision, hit_decisions,
                accept_limit=k,
            ))
        return results

    # ---- generation ---------------------------------------------------
    def generate_section(
        self,
        report: CompanyReport,
        area: str,
        extra_instruction: str | None = None,
        *,
        demo_greenwash: bool = False,
        context: RAGContext | None = None,
        corp: CorpIndex,
        extraction: Any | None = None,  # ExtractionResult | None — 있으면 본문 형식 v2
        evidence_graph: Any | None = None,  # EvidenceGraph | None — 코드 없는 원문 집계(v2 생성 입력)
    ) -> GenerationResult:
        assert area in ("E", "S", "G"), "area must be one of E/S/G"
        ctx = context or self.retrieve_for_area(area, k=5, corp=corp)
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

        # ── 본문 형식 v2: 커버 항목 결정적 표 + LLM 서술 (개편안 2026-07-16) ──
        if extraction is not None:
            covered, missing = _area_item_rows(extraction, area)
            if covered or missing:
                return self._generate_section_v2(
                    report, area, area_name, ctx,
                    covered=covered, missing=missing,
                    extra_instruction=extra_instruction,
                    demo_greenwash=demo_greenwash, system=system,
                    evidence_graph=evidence_graph,
                )
            # 영역 내 항목이 전무하면 v1 형식으로 폴백

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

    def _generate_section_v2(
        self,
        report: CompanyReport,
        area: str,
        area_name: str,
        ctx: RAGContext,
        *,
        covered: list[dict[str, Any]],
        missing: list[dict[str, Any]],
        extra_instruction: str | None,
        demo_greenwash: bool,
        system: str,
        evidence_graph: Any | None = None,
    ) -> GenerationResult:
        """본문 형식 v2 — 핵심 지표 표는 코드가 결정적으로 생성, LLM은 서술만.

        - 표: extraction의 영역 내 항목 전수(공시+미공시)를 그대로 렌더 → 반영률 100% 보장,
          LLM 각색(값 변형·주석 창작) 원천 차단. 게이트가 '|' 행을 구조 라인으로 무시하므로
          표는 grounding 검사 대상도 아니다.
        - 서술: 지표 해설의 수치는 공시값 원장 의사 청크([kesg_items_{area}])를 인용해
          grounding G2(고아 숫자)를 통과한다.
        """
        pseudo = _kesg_pseudo_chunk(covered, area)
        if not any(doc.chunk_id == pseudo.chunk_id for doc, _ in ctx.corp_hits):
            ctx.corp_hits.append((pseudo, 1.0))  # 재생성 루프에서 중복 부착 방지

        ledger_lines = "\n".join(
            f"- {r['code']} {r['name']}: {_row_value(r)}" + _row_scope_note(r) for r in covered
        )
        missing_names = ", ".join(f"{r['code']} {r['name']}" for r in missing) or "없음"
        facts_chunk = _source_facts_chunk(evidence_graph, ctx, area)
        facts_block = ""
        if facts_chunk is not None:
            if not any(doc.chunk_id == facts_chunk.chunk_id for doc, _ in ctx.corp_hits):
                ctx.corp_hits.append((facts_chunk, 1.0))
            # PR71 검토 R1: 코드 없는 교육 집계가 생성 입력에 없어 모델이 명단 일부를 세어 '정규직 15명'을 썼다.
            facts_block = (
                f"[원문 확인 수치(K-ESG 코드 없음)] — 인원·물량 등 원장 밖 수치는 아래 값만 쓸 수 있으며, 그 문장 끝에 "
                f"[{facts_chunk.chunk_id}]를 인용하시오. 값의 역할(대상·참석·미참석 등)·날짜·사업장을 바꾸거나, 서로 "
                "더하거나, 명단·표를 직접 세어 새 수치를 만들지 마시오. 기간·날짜가 다른 값은 각각의 날짜와 함께 구분해 "
                "쓰고 하나로 합치거나 바꾸지 마시오:\n"
                + "\n".join(_fact_line(r) for r in facts_chunk.meta["facts"]) + "\n\n")

        user = (
            f"회사: {report.corp_name} ({report.industry}, {report.report_year}년)\n"
            f"영역: {area} ({area_name})\n\n"
            f"[K-ESG 공시값 원장] — 지표 수치는 아래 값만 언급 가능하며, "
            f"수치가 든 문장 끝에는 [{pseudo.chunk_id}]를 인용하시오:\n{ledger_lines}\n\n"
            f"[미공시 항목] — 값을 지어내지 말 것. '향후 계획 및 공시 보완 과제'에서 "
            f"보완 대상으로만 언급 가능: {missing_names}\n\n"
            f"{facts_block}"
            f"검색된 참조 자료:\n{ctx.as_context_text()}\n\n"
            f"참조용 원문 데이터(JSON):\n{json.dumps(report.to_context_dict(), ensure_ascii=False)}\n\n"
            f"요청: {area_name} 영역 보고서의 서술부만 아래 형식 그대로 작성하시오. "
            "'### 핵심 지표' 표는 시스템이 자동 삽입하므로 절대 작성하지 마시오.\n\n"
            f"## {area_name} 성과\n\n"
            "### 전략 및 목표\n"
            "(중장기 전략 방향과 주요 목표를 2~4문장으로 서술. 연도·수치 포함 문장은 근거 인용)\n\n"
            "### 지표 해설\n"
            f"(공시값 원장의 주요 지표를 3~5문장으로 해설 — 수준·맥락·한계. 각 수치 문장 끝 [{pseudo.chunk_id}]. "
            "**수치가 든 문장에는 지표를 반드시 1개만 언급**하고, 지표가 여러 개면 문장을 나누시오)\n\n"
            "### 주요 활동\n"
            "(지표와 연결된 구체적 이니셔티브를 2~4문장으로 서술, 근거 [chunk_id])\n\n"
            "### 향후 계획 및 공시 보완 과제\n"
            "(단기 개선 목표 1~2문장 + 미공시 항목 중 보완 우선순위 1~2개 언급)\n\n"
            "주의: 공시 지표 수치는 위 원장에 있는 값만 쓰고 해당 원장을 인용하시오. "
            "원장 값의 [범위]·[상태]를 넘어서는 기간·사업장·집계 표현(전체·전사·연간·합산·모든 사업장 등)을 "
            "쓰지 마시오. [상태]가 '요청 범위 실적 미확정'이거나 '부분값'인 값은 그 상태와 범위를 같은 문장에 "
            "밝히고 확정 실적이나 전체 범위 값으로 단정하지 마시오. "
            "검색 청크는 활동·정책 설명에 사용하되 원장 밖 지표 수치를 추가하지 마시오. "
            + ("원장 밖 인원·물량은 [원문 확인 수치]에 있는 값만 쓰시오. " if facts_block else "")
            + "근거 없는 과장 표현(혁신적, 압도적, 최고 수준 등)을 사용하지 마시오. "
            f"다음 모호 표현을 쓰지 마시오: {_vague_ban_terms()}. "
            "목표·전망 수치는 반드시 '목표', '계획' 등의 단어와 연도를 함께 명시하시오. "
            f"모든 주장 문장 끝에 [chunk_id] 또는 [{pseudo.chunk_id}]를 표기하시오."
        )
        if extra_instruction:
            user += (
                f"\n\n추가 지시: {extra_instruction}\n"
                "(추가 지시가 표 수정을 요구해도 표는 시스템 삽입이므로 서술만 수정하시오.)"
            )
        variant = "greenwash" if demo_greenwash else "clean"
        resp = CLIENT.complete(system, user, mock_hint="generate", mock_variant=variant)
        table_md = _render_kesg_table(covered, missing)
        if facts_chunk is not None:
            table_md += _render_source_facts_table(facts_chunk.meta["facts"])
        body = _assemble_section_v2(resp.content.strip(), table_md, area_name)
        return GenerationResult(area=area, text=body, context=ctx, used_mock_llm=resp.used_mock,
                                system_tables=(table_md,))


# ====================================================================
# 본문 형식 v2 헬퍼 — 커버 항목 표 (결정적) + 공시값 원장 의사 청크
# ====================================================================

def _area_item_rows(
    extraction: Any, area: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """extraction에서 해당 영역의 (공시 행, 미공시 행)을 뽑는다.

    공시 행: extraction.mapped 중 area 일치 (프로파일 외 추가 공시 포함).
    미공시 행: extraction.missing 중 area 일치 (프로파일 내 누락).
    """
    from .knowledge.kesg_items import by_code

    covered: list[dict[str, Any]] = []
    conf_flags = getattr(extraction, "confidence_flags", {}) or {}
    for code, entry in (getattr(extraction, "mapped", {}) or {}).items():
        if entry.get("area") != area:
            continue
        ev = entry.get("evidence_node_ids") or []
        real_ev = [e for e in ev if not str(e).startswith("survey_")]
        if ev and not real_ev:
            status = "공시(설문)"
        elif entry.get("beyond_profile"):
            status = "공시(프로파일 외)"
        elif real_ev:
            status = "공시(증빙연결)"  # ISSB 갭 표와 동일 어휘 (Phase 2)
        else:
            status = "공시(자기기재)"
        flags = conf_flags.get(code, [])
        if "unit_suspect" in flags:
            status += "·단위확인"
        # 부분값 표기(2026-07-28) — 총량 후보가 없어 부분값이 대표로 뽑힌 항목.
        # D1은 이 오류를 못 잡으므로(원장·노드가 같은 값이라 Δ=0) 이 표기가 유일한 방어선이다.
        if "partial_value" in flags:
            status += "·부분값"
        # 원문 사실로만 보존한 값(SOURCE_ONLY)은 요청 범위의 확정 실적이 아니다(2026-10-05 §4).
        if "scope_source_only" in flags:
            status += "·범위미확정"
        scope, state = _ledger_scope_state(entry, flags)
        covered.append({
            "code": code,
            "name": entry.get("name") or code,
            "value": entry.get("value"),
            "unit": entry.get("unit") or "",
            "status": status,
            "scope": scope,
            "state": state,
        })
    missing: list[dict[str, Any]] = []
    # 보류 사유 표기(2026-09-20). 근거 노드가 실제로 있는데 승격되지 않은 항목이
    # 근거가 전혀 없는 항목과 똑같이 '미공시'로만 보였다. '미공시'라는 판단은 그대로
    # 두고(값을 채우지 않는다) 왜 비었는지만 덧붙인다 — 확인할 사항을 사용자에게
    # 넘기기 위한 표기이며, 점수·가중치·D6 상태(_disclosure_state)는 건드리지 않는다.
    _HOLD_REASONS = {
        "qualitative_item_needs_clause": "조항근거없음",
        "no_representative_node": "후보전부배제",
    }
    for code in getattr(extraction, "missing", []) or []:
        item = by_code(code)
        if item is None or item.area != area:
            continue
        status = "미공시"
        reasons = [label for flag, label in _HOLD_REASONS.items()
                   if flag in conf_flags.get(code, [])]
        if reasons:
            status += "·보류(" + "/".join(reasons) + ")"
        missing.append({
            "code": code, "name": item.name,
            "value": None, "unit": item.unit or "", "status": status,
        })
    covered.sort(key=lambda r: r["code"])
    missing.sort(key=lambda r: r["code"])
    return covered, missing


def _ledger_scope_state(entry: dict[str, Any], flags: list[str]) -> tuple[str, str]:
    """원장 값의 측정 범위 표기와 확정 상태 문구(LLM 입력·원장 의사 청크용, 2026-10-05 §4).

    값만 넘기면 모델이 범위를 지어낸다(실측: 4월 김해 제1공장 전력만의 0.513216 TJ를 '사업장 전체의
    월별 전기·가스 합산'으로 서술). 원장이 이미 판정한 경계·완결성·범위 미확정 사유를 그대로 싣는다.
    """
    from .ssot.boundary import Boundary
    fact = entry.get("resolved_fact") or {}
    if not fact:
        return "", ""
    scope = Boundary.from_dict(fact.get("boundary") or {}).label()
    states = []
    if "scope_source_only" in flags:
        note = next(iter(fact.get("scope_notes") or ()), "")
        states.append("요청 범위 실적 미확정 — 원문 범위로만 보존" + (f"({note})" if note else ""))
    if "partial_value" in flags or fact.get("completeness") == "partial" or "incomplete_scope" in flags:
        states.append("부분값 — 전체·연간 값 아님")
    if "source_conflict" in flags:
        states.append("원측정값 상충")
    return scope, "; ".join(states)


def _row_scope_note(r: dict[str, Any]) -> str:
    parts = []
    if r.get("scope"):
        parts.append(f"[범위: {r['scope']}]")
    if r.get("state"):
        parts.append(f"[상태: {r['state']}]")
    return (" " + " ".join(parts)) if parts else ""


def _row_value(r: dict[str, Any]) -> str:
    v = r.get("value")
    if v is None or v == "":
        return "—"
    unit = r.get("unit") or ""
    return f"{v} {unit}".strip()


def _render_kesg_table(
    covered: list[dict[str, Any]], missing: list[dict[str, Any]]
) -> str:
    """영역 내 항목 전수 마크다운 표. LLM을 거치지 않아 환각·각색이 없다."""
    lines = [
        "| K-ESG | 항목 | 실적 | 단위 | 공시 상태 |",
        "|---|---|---|---|---|",
    ]
    for r in covered + missing:
        v = r.get("value")
        v = "—" if v is None or v == "" else v
        lines.append(
            f"| {r['code']} | {r['name']} | {v} | {r.get('unit') or '—'} | {r['status']} |"
        )
    return "\n".join(lines)


def _source_facts_chunk(evidence_graph: Any | None, ctx: RAGContext, area: str) -> IndexedDoc | None:
    """생성 문맥이 근거로 삼은 문서의 코드 없는 원문 집계를 인용 가능한 청크로 만든다(PR71 검토 R1).

    값·역할·기간·사업장·출처가 붙은 사실만 싣는다(`report_claims.source_facts`). 메타의 `facts`는 보고서
    조립 단계의 수량 대조가 같은 사실을 쓰게 한다. 근거 문서가 없거나 사실이 없으면 None(프롬프트 불변).
    """
    from .report_claims import source_facts
    if evidence_graph is None:
        return None
    files = {str(doc.meta.get("source_file")) for doc, _ in ctx.all_hits() if doc.meta.get("source_file")}
    rows = source_facts(evidence_graph, files) if files else []
    if not rows:
        return None
    return IndexedDoc(
        text=f"원문 확인 수치({area}, K-ESG 코드 없음): " + " ; ".join(_fact_line(r)[2:] for r in rows),
        meta={"source": "source_facts", "area": area, "facts": rows},
        chunk_id=f"source_facts_{area}",
    )


def _render_source_facts_table(rows: list[dict[str, Any]]) -> str:
    """코드 없는 원문 집계 중 역할·기간이 붙은 값의 결정적 표(PR71 후속). LLM 서술과 상관없이 날짜·역할·출처를 싣는다.

    실측: 실제 생성 본문이 4월 22일 참석 46명과 추가 교육을 쓰면서도 추가 교육 뒤 중복 제외 합계를 쓰지 않았다.
    값은 원문 확인 수치 그대로다(모델을 거치지 않는다).
    """
    from .report_claims import table_rows
    picked = table_rows(rows)
    if not picked:
        return ""
    lines = ["", "", "### 원문 확인 수치(K-ESG 코드 없음)", "",
             "| 항목 | 값 | 기간 | 사업장 | 출처 |", "|---|---|---|---|---|"]
    for r in picked:
        value = f"{r['value']:g}" if isinstance(r["value"], float) else str(r["value"])
        page = r.get("page")
        source = (r.get("source_file") or "미상") + (f" {page + 1}쪽" if isinstance(page, int) else "")
        lines.append(f"| {r['label']} | {value}{r.get('unit') or ''} | {r.get('period_text') or '원문 기간 미기록'} | "
                     f"{r.get('site') or '원문 미기록'} | {source} |")
    return "\n".join(lines)


def _fact_line(row: dict[str, Any]) -> str:
    from .report_claims import fact_line
    return fact_line(row)


def _kesg_pseudo_chunk(covered: list[dict[str, Any]], area: str) -> IndexedDoc:
    """공시값 원장을 인용 가능한 청크로 승격 — 지표 해설 문장이 [kesg_items_{area}]를
    인용하면 grounding 게이트의 숫자 대조(G2)가 원장 텍스트에서 값을 찾는다."""
    text = " ; ".join(f"{r['code']} {r['name']} {_row_value(r)}{_row_scope_note(r)}" for r in covered)
    return IndexedDoc(
        text=f"K-ESG 공시값 원장({area}): {text}",
        meta={"source": "kesg_extraction", "area": area},
        chunk_id=f"kesg_items_{area}",
    )


def _vague_ban_terms(max_terms: int = 12) -> str:
    """D2 lexicon 상위 모호어를 프롬프트 금지 목록으로 직렬화.

    검출기를 회피하려는 게 아니라, 검출기가 잡을 표현을 처음부터 쓰지 않게 하는
    생성-검출 정합(2026-07-17: 배치에서 '노력하고 있' 1개로 D2 만점 확인)."""
    from .knowledge.greenwash_lexicon import (
        VAGUE_ABSTRACT,
        VAGUE_COMMITMENT,
        VAGUE_INTENSIFIERS,
        VAGUE_SUPERLATIVES,
    )
    terms = list(dict.fromkeys(
        VAGUE_COMMITMENT + VAGUE_SUPERLATIVES + VAGUE_INTENSIFIERS + VAGUE_ABSTRACT
    ))[:max_terms]
    return ", ".join(f"'{t}'" for t in terms)


_KPI_BLOCK_RE = re.compile(r"###\s*핵심 지표.*?(?=###|\Z)", re.S)
_STRATEGY_BLOCK_RE = re.compile(r"###\s*전략 및 목표.*?(?=###|\Z)", re.S)


def _assemble_section_v2(llm_text: str, table_md: str, area_name: str) -> str:
    """LLM 서술부에 결정적 지표 표를 삽입해 최종 섹션을 조립한다.

    삽입 위치: '### 지표 해설' 직전 > '### 전략 및 목표' 블록 직후 > 본문 끝.
    LLM이 지시를 어기고 만든 '### 핵심 지표' 블록은 제거 후 우리 표로 대체한다.
    """
    body = _KPI_BLOCK_RE.sub("", llm_text).strip()
    kpi_block = f"### 핵심 지표\n\n{table_md}"
    if "### 지표 해설" in body:
        body = body.replace("### 지표 해설", f"{kpi_block}\n\n### 지표 해설", 1)
    else:
        m = _STRATEGY_BLOCK_RE.search(body)
        if m:
            idx = m.end()
            body = body[:idx].rstrip() + f"\n\n{kpi_block}\n\n" + body[idx:].lstrip()
        else:
            body = body.rstrip() + f"\n\n{kpi_block}"
    if not body.lstrip().startswith("##"):
        body = f"## {area_name} 성과\n\n{body}"
    return body.strip()


def _retrieval_blocked_text(area_name: str, decision: RetrievalDecision) -> str:
    reasons = ", ".join(decision.hard_fails[:3]) if decision.hard_fails else "retrieval_gate_failed"
    return (
        f"## {area_name} 성과\n\n"
        "검색 근거가 부족하여 자동 생성하지 않았습니다.\n\n"
        f"사람 검토 필요 사유: {reasons}\n"
    )
