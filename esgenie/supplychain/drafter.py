"""AI 초안 생성 엔진 — hitl_required 정성항목을 사내규정 TextNode 근거로 초안 작성.

Fail-closed: 근거게이트(G1·G2·G4 hard + G5 soft) 통과분만 draft_ready,
실패 시 초안 폐기하고 원상태 유지.
"""
from __future__ import annotations

import logging
from typing import Any

from ..embeddings import BM25Index, IndexedDoc
from ..knowledge.kesg_evidence_requirements import requirement_for
from ..llm import LLMClient, LLMResponse
from ..rag_gates.grounding_gate import evaluate_grounding, grounding_feedback
from .schema import Answer, ResponseSheet

logger = logging.getLogger(__name__)

_DRAFTER_SYSTEM = (
    "당신은 ESG 실사 응답 초안 작성기입니다. "
    "아래 제공된 발췌(청크)만을 근거로 답변을 작성하세요. "
    "각 문장 끝에 반드시 [chunk_id] 형식으로 인용을 붙이세요. "
    "발췌에 없는 내용은 절대 포함하지 마세요."
)

_DRAFTER_USER_TEMPLATE = """\
질문: {question}

아래 발췌만 근거로 답변을 작성하세요. 각 문장 끝에 [chunk_id] 인용 필수.
발췌에 없는 내용을 작성하면 안 됩니다.

[검색 청크]
{chunks_text}

답변:"""

_BM25_TOP_K = 5
_BM25_MIN_SCORE = 2.0
_BM25_RELATIVE_THRESHOLD = 0.4


def _code_pillar(code: str) -> str:
    """K-ESG 코드의 pillar(E/S/G/P 등)를 반환. RBA 코드(A-3 등)는 빈 문자열."""
    if not code:
        return ""
    first = code[0].upper()
    if first in ("E", "S", "G", "P"):
        return first
    return ""


def generate_drafts(
    sheet: ResponseSheet,
    evidence_graph: Any,
    *,
    max_retries: int = 2,
) -> ResponseSheet:
    """hitl_required/insufficient(policy) 항목에 AI 초안을 생성해 draft_ready로 전환.

    순수 함수형: sheet를 받아 초안 붙인 sheet를 반환.
    실패 시 원상태 유지(fail-closed).
    """
    if evidence_graph is None:
        return sheet

    text_nodes = getattr(evidence_graph, "text_nodes", {})
    if not text_nodes:
        return sheet

    # qid→(primary_code, question_text) 맵을 루프 밖에서 한 번만 빌드
    code_map, qtext_map = _build_code_map(sheet)

    bm25 = _build_bm25_index(text_nodes)
    llm = LLMClient()

    for answer in sheet.answers:
        primary_code = code_map.get(answer.qid, "")
        if not _is_draft_candidate(answer, primary_code):
            continue

        if not primary_code:
            continue

        question_text = qtext_map.get(answer.qid, answer.question_text)
        chunks = _collect_chunks(primary_code, evidence_graph, bm25, question_text)
        if not chunks:
            continue

        _attempt_draft(answer, chunks, llm, max_retries=max_retries)

    return sheet


def _build_code_map(sheet: ResponseSheet) -> tuple[dict[str, str], dict[str, str]]:
    """framework를 한 번 역조회해 qid→primary_code, qid→question_text 맵을 구축."""
    from .frameworks import get_framework
    try:
        fw = get_framework(sheet.framework_key)
    except (KeyError, ValueError):
        return {}, {}
    code_map = {q.qid: q.primary_code for q in fw.questions}
    qtext_map = {q.qid: q.text for q in fw.questions}
    return code_map, qtext_map


def _is_draft_candidate(answer: Answer, primary_code: str) -> bool:
    """초안 대상 여부: hitl_required, 또는 insufficient 중 policy kind."""
    if answer.status == "hitl_required":
        return True
    if answer.status == "insufficient":
        if primary_code:
            req = requirement_for(primary_code)
            return req.kind == "policy"
    return False


def _build_bm25_index(text_nodes: dict[str, Any]) -> BM25Index:
    """전체 TextNode로 BM25 인덱스를 구축."""
    docs = [
        IndexedDoc(
            text=node.text,
            meta={
                "id": node.id,
                "source_file": getattr(node, "source_file", ""),
                "page": getattr(node, "page", None),
                "kesg_code": getattr(node, "kesg_code", None),
            },
            chunk_id=node.id,
        )
        for node in text_nodes.values()
    ]
    index = BM25Index()
    if docs:
        index.build(docs)
    return index


def _collect_chunks(
    code: str,
    evidence_graph: Any,
    bm25: BM25Index,
    question_text: str,
) -> list[dict[str, Any]]:
    """코드에 매칭되는 TextNode를 수집. 1차 code 매칭, 부족 시 BM25 보조."""
    nodes = evidence_graph.text_nodes_by_code(code)

    if nodes:
        return [
            {
                "id": _node_id(n),
                "text": _node_text(n),
                "source_file": getattr(n, "source_file", ""),
                "page": getattr(n, "page", None),
                "retrieval": "code_match",
            }
            for n in nodes
        ]

    # BM25 폴백 — pillar 가드 + 임계 보수화
    req = requirement_for(code)
    query = " ".join(req.evidence_types) + " " + question_text
    results = bm25.search(query, k=_BM25_TOP_K)

    if not results:
        return []

    target_pillar = _code_pillar(code)
    top1_score = results[0][1] if results else 0.0

    accepted: list[dict[str, Any]] = []
    for doc, score in results:
        # 절대점수 기준
        if score < _BM25_MIN_SCORE:
            continue
        # top1 대비 상대비율 기준
        if top1_score > 0 and score < top1_score * _BM25_RELATIVE_THRESHOLD:
            continue
        # pillar 가드: 노드에 kesg_code가 있고 pillar가 다르면 배제
        node_code = doc.meta.get("kesg_code")
        if node_code:
            node_pillar = _code_pillar(node_code)
            if node_pillar and target_pillar and node_pillar != target_pillar:
                continue

        accepted.append({
            "id": doc.chunk_id or doc.meta.get("id", ""),
            "text": doc.text,
            "source_file": doc.meta.get("source_file", ""),
            "page": doc.meta.get("page"),
            "retrieval": "bm25_fallback",
        })

    return accepted


class _PseudoNode:
    __slots__ = ("id", "text", "source_file", "page")

    def __init__(self, id: str, text: str, source_file: str, page: Any):
        self.id = id
        self.text = text
        self.source_file = source_file
        self.page = page


def _node_id(node: Any) -> str:
    return getattr(node, "id", "")


def _node_text(node: Any) -> str:
    return getattr(node, "text", "")


def _attempt_draft(
    answer: Answer,
    chunks: list[dict[str, Any]],
    llm: LLMClient,
    *,
    max_retries: int,
) -> None:
    """초안 생성 → 근거게이트 검증 → 통과 시 draft_ready, 실패 시 재시도."""
    chunks_text = "\n".join(
        f"- [{c['id']}] {c['text']}" for c in chunks
    )

    user_prompt = _DRAFTER_USER_TEMPLATE.format(
        question=answer.question_text,
        chunks_text=chunks_text,
    )

    feedback_constraint = ""
    for attempt in range(1 + max_retries):
        full_prompt = user_prompt
        if feedback_constraint:
            full_prompt = user_prompt + "\n\n" + feedback_constraint

        resp: LLMResponse = llm.complete(
            system=_DRAFTER_SYSTEM,
            user=full_prompt,
            mock_hint="generate",
            temperature=0.2,
        )
        draft_text = resp.content.strip()
        if not draft_text:
            continue

        result = evaluate_grounding(draft_text, chunks)

        if result.decision == "ACCEPT" and not result.soft_flags:
            answer.status = "draft_ready"
            answer.draft_text = draft_text
            answer.draft_citations = _build_citations(chunks)
            answer.draft_grounding = result.to_dict()
            return

        if attempt < max_retries:
            feedback_constraint = grounding_feedback(result)
        else:
            logger.info(
                "drafter: 초안 폐기 (qid=%s, 최종 decision=%s, hard_fails=%s, soft_flags=%s)",
                answer.qid, result.decision, result.hard_fails, result.soft_flags,
            )


def _build_citations(chunks: list[dict[str, Any]]) -> list[dict]:
    """인용 메타데이터 리스트 생성."""
    return [
        {
            "node_id": c["id"],
            "source_file": c.get("source_file", ""),
            "page": c.get("page"),
            "text_preview": c["text"][:100],
            "retrieval": c.get("retrieval", "code_match"),
        }
        for c in chunks
    ]
