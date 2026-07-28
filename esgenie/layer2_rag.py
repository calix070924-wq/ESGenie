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
import re
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


@dataclass
class CorpIndex:
    """회사별 corp 인덱스 쌍. HybridRAG 싱글톤에 얹지 않고 호출마다 만들어 전달한다."""

    vector: VectorIndex
    bm25: BM25Index


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
            f"- {r['code']} {r['name']}: {_row_value(r)}" for r in covered
        )
        missing_names = ", ".join(f"{r['code']} {r['name']}" for r in missing) or "없음"

        user = (
            f"회사: {report.corp_name} ({report.industry}, {report.report_year}년)\n"
            f"영역: {area} ({area_name})\n\n"
            f"[K-ESG 공시값 원장] — 지표 수치는 아래 값만 언급 가능하며, "
            f"수치가 든 문장 끝에는 [{pseudo.chunk_id}]를 인용하시오:\n{ledger_lines}\n\n"
            f"[미공시 항목] — 값을 지어내지 말 것. '향후 계획 및 공시 보완 과제'에서 "
            f"보완 대상으로만 언급 가능: {missing_names}\n\n"
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
            "주의: 원장과 인용 청크에 없는 숫자는 절대 쓰지 마시오. "
            "근거 없는 과장 표현(혁신적, 압도적, 최고 수준 등)을 사용하지 마시오. "
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
        body = _assemble_section_v2(resp.content.strip(), table_md, area_name)
        return GenerationResult(area=area, text=body, context=ctx, used_mock_llm=resp.used_mock)


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
        covered.append({
            "code": code,
            "name": entry.get("name") or code,
            "value": entry.get("value"),
            "unit": entry.get("unit") or "",
            "status": status,
        })
    missing: list[dict[str, Any]] = []
    for code in getattr(extraction, "missing", []) or []:
        item = by_code(code)
        if item is None or item.area != area:
            continue
        missing.append({
            "code": code, "name": item.name,
            "value": None, "unit": item.unit or "", "status": "미공시",
        })
    covered.sort(key=lambda r: r["code"])
    missing.sort(key=lambda r: r["code"])
    return covered, missing


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


def _kesg_pseudo_chunk(covered: list[dict[str, Any]], area: str) -> IndexedDoc:
    """공시값 원장을 인용 가능한 청크로 승격 — 지표 해설 문장이 [kesg_items_{area}]를
    인용하면 grounding 게이트의 숫자 대조(G2)가 원장 텍스트에서 값을 찾는다."""
    text = " ; ".join(f"{r['code']} {r['name']} {_row_value(r)}" for r in covered)
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
