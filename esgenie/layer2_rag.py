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

from .config import BEST_REPORTS_DIR, INDUSTRY_DIR, KESG_DIR
from .dart_client import CompanyReport
from .embeddings import IndexedDoc, VectorIndex
from .llm import CLIENT

WEIGHTS = {"kesg": 0.40, "industry": 0.30, "corp": 0.30}

# 영역별 쿼리 확장에 쓸 SearchTerm 상한 (쿼리 과팽창 방지)
_QUERY_EXPANSION_MAX_TERMS = 12


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


@dataclass
class RAGContext:
    kesg_hits: list[tuple[IndexedDoc, float]]
    industry_hits: list[tuple[IndexedDoc, float]]
    corp_hits: list[tuple[IndexedDoc, float]]

    def as_context_text(self, top_k: int = 2) -> str:
        blocks: list[str] = []
        if self.kesg_hits:
            blocks.append("[K-ESG 기준]")
            for doc, _ in self.kesg_hits[:top_k]:
                blocks.append(f"- {doc.text}")
        if self.industry_hits:
            blocks.append("[업종 벤치마크]")
            for doc, _ in self.industry_hits[:top_k]:
                blocks.append(f"- {doc.text}")
        if self.corp_hits:
            blocks.append("[자사 DART 원문]")
            for doc, _ in self.corp_hits[:top_k]:
                blocks.append(f"- {doc.text}")
        return "\n".join(blocks)


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
        self.industry_index = VectorIndex()
        self.corp_index = VectorIndex()
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
                docs.append(IndexedDoc(text=text, meta={"code": g["code"], "source": "kesg"}))
        # 우수 보고서 발췌도 이 인덱스에 합침 (서술 스타일 레퍼런스)
        for path in sorted(BEST_REPORTS_DIR.glob("*.json")):
            with open(path, encoding="utf-8") as fp:
                obj = json.load(fp)
            for e in obj.get("excerpts", []):
                docs.append(IndexedDoc(
                    text=f"[우수사례 {e['area']}/{e['topic']}] {e['text']}",
                    meta={"source": "best_report", "area": e["area"]},
                ))
        self.kesg_index.build(docs)

    def _load_industry(self) -> None:
        docs: list[IndexedDoc] = []
        for path in sorted(INDUSTRY_DIR.glob("*.json")):
            with open(path, encoding="utf-8") as fp:
                obj = json.load(fp)
            for b in obj.get("benchmarks", []):
                metrics = ", ".join(f"{k}={v}" for k, v in b.get("metrics", {}).items())
                issues = "; ".join(b.get("key_issues", []))
                text = (
                    f"[{b['industry']}] 산업 평균 지표: {metrics}. "
                    f"핵심 이슈: {issues}. 비고: {b.get('notes', '')}"
                )
                docs.append(IndexedDoc(text=text, meta={"industry": b["industry"]}))
        self.industry_index.build(docs)

    def build_corp_index(self, report: CompanyReport) -> None:
        docs: list[IndexedDoc] = [
            IndexedDoc(text=s, meta={"source": "dart_raw"}) for s in report.raw_text_snippets
        ]
        for code, entry in report.kesg_data.items():
            docs.append(IndexedDoc(
                text=f"[DART/{code}] {entry.get('note', '')} 수치: {entry.get('value')} {entry.get('unit', '')}",
                meta={"source": "dart_struct", "code": code},
            ))
        self.corp_index.build(docs)

    # ---- retrieval ----------------------------------------------------
    def retrieve(self, query: str, k: int = 3) -> RAGContext:
        return RAGContext(
            kesg_hits=self.kesg_index.search(query, k=k),
            industry_hits=self.industry_index.search(query, k=k),
            corp_hits=self.corp_index.search(query, k=k),
        )

    # ---- generation ---------------------------------------------------
    def generate_section(
        self,
        report: CompanyReport,
        area: str,
        extra_instruction: str | None = None,
        *,
        demo_greenwash: bool = False,
    ) -> GenerationResult:
        assert area in ("E", "S", "G"), "area must be one of E/S/G"
        query = {
            "E": "온실가스, 재생에너지, 폐기물, 용수, 환경 규제 성과",
            "S": "정규직, 이직률, 여성 비율, 산업재해율, 정보보호",
            "G": "사외이사 비율, 이사회 다양성, 출석률, 윤리경영, 감사기구",
        }[area]
        # 해당 영역 지표의 SearchTerm으로 쿼리 확장 → 검색 재현율 보강
        # (ESGReveal <SearchTerm>). 기존 큐레이션 쿼리는 유지하고 덧붙인다.
        query = _expand_query_with_search_terms(query, area)
        ctx = self.retrieve(query, k=3)
        corp_ctx = report.to_context_dict()
        system = (
            "당신은 한국 K-ESG 가이드라인을 준수하는 ESG 공시 보고서 전문 작성자다. "
            "반드시 제공된 DART 수치만 사용하고, 정량 근거 없는 과장 표현을 피하라."
        )
        area_name = {"E": "환경", "S": "사회", "G": "지배구조"}[area]
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
            "주의: DART 수치만 사용하고, 근거 없는 과장 표현(혁신적, 압도적, 최고 수준 등)은 사용하지 마시오."
        )
        if extra_instruction:
            user += f"\n\n추가 지시: {extra_instruction}"
        variant = "greenwash" if demo_greenwash else "clean"
        resp = CLIENT.complete(system, user, mock_hint="generate", mock_variant=variant)
        return GenerationResult(area=area, text=resp.content.strip(), context=ctx, used_mock_llm=resp.used_mock)
