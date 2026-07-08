"""AI 초안 생성 엔진 — hitl_required 정성항목을 사내규정 TextNode 근거로 초안 작성.

Fail-closed: 근거게이트(G1·G2·G4 hard + G5 soft) 통과분만 draft_ready,
실패 시 초안 폐기하고 원상태 유지.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from ..embeddings import BM25Index, IndexedDoc
from ..knowledge.kesg_evidence_requirements import requirement_for
from ..llm import LLMClient, LLMResponse
from ..rag_gates.grounding_gate import evaluate_grounding, grounding_feedback
from .schema import Answer, ResponseSheet

logger = logging.getLogger(__name__)

# ── 검색 경로 상수 ────────────────────────────────────────────────────────────
RETRIEVAL_CODE_MATCH = "code_match"
RETRIEVAL_BM25_FALLBACK = "bm25_fallback"

_DRAFTER_SYSTEM = (
    "당신은 ESG 실사 응답 초안 작성기입니다. "
    "아래 제공된 발췌(청크)만을 근거로 답변을 작성하세요. "
    "각 문장 끝에 반드시 [chunk_id] 형식으로 인용을 붙이세요. "
    "발췌에 없는 내용은 절대 포함하지 마세요. "
    "발췌가 질문에 답하기에 불충분하면 다른 말을 덧붙이지 말고 "
    "정확히 INSUFFICIENT_EVIDENCE 라고만 출력하라."
)

_DRAFTER_USER_TEMPLATE = """\
질문: {question}

아래 발췌만 근거로 답변을 작성하세요. 각 문장 끝에 [chunk_id] 인용 필수.
발췌에 없는 내용을 작성하면 안 됩니다.
발췌가 질문에 답하기에 불충분하면 정확히 INSUFFICIENT_EVIDENCE 라고만 출력하세요.

[검색 청크]
{chunks_text}

답변:"""

_BM25_TOP_K = 5
# 후보 축소용 1차 필터 — 관련도 보장은 관련성 사전 게이트(_check_relevance)가 담당
_BM25_MIN_SCORE = 2.0
_BM25_RELATIVE_THRESHOLD = 0.4

# ── 자백 패턴 (sentence-level context-aware) ─────────────────────────────────
# Category A: 단독으로 불충분 자백인 패턴 (문맥 불문)
_CONFESSION_STANDALONE: tuple[str, ...] = (
    "답변을 제공할 수 없",
    "답변을 드리기 어렵",
    "판단할 수 없",
)

# Category B: 발췌 지시어(referent)와 같은 문장에 동시 출현해야 자백으로 판정
_CONFESSION_CONTEXTUAL: tuple[str, ...] = (
    "포함되어 있지 않",
    "확인할 수 없",
    "언급된 내용이 없",
    "포함되지 않",
    "찾을 수 없",
    "근거가 없",
    "내용이 없",
)

_EXCERPT_REFERENTS: tuple[str, ...] = (
    "발췌", "청크", "제공된", "위 문서", "해당 문서",
)

_RELEVANCE_SYSTEM = "당신은 ESG 증빙 문서와 질문 간 관련성을 판정하는 전문가입니다."

_RELEVANCE_USER_TEMPLATE = """\
다음 발췌가 아래 질문에 실질적으로 답할 근거를 담고 있는가?

질문: {question}

발췌:
{chunks_text}

반드시 YES 또는 NO 한 단어로만 답하라."""


def generate_drafts(
    sheet: ResponseSheet,
    evidence_graph: Any,
    *,
    framework: Any | None = None,
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

    code_map, qtext_map = _build_code_map(sheet, framework=framework)

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

        is_fallback = chunks[0].get("retrieval") == RETRIEVAL_BM25_FALLBACK
        if is_fallback and not _check_relevance(question_text, chunks, llm):
            logger.info(
                "drafter: 관련성 게이트 NO (qid=%s)", answer.qid,
            )
            continue

        _attempt_draft(answer, chunks, llm, max_retries=max_retries)

    return sheet


def _build_code_map(
    sheet: ResponseSheet,
    *,
    framework: Any | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """framework를 한 번 역조회해 qid→primary_code, qid→question_text 맵을 구축."""
    fw = framework
    if fw is None:
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
    """미분류(kesg_code=None) TextNode만으로 BM25 인덱스를 구축.

    kesg_code가 있는 노드는 code_match 경로에서만 사용된다.
    폴백 인덱스를 미분류 노드로 한정함으로써 same-pillar 오염을 원천 차단한다.
    (이전 pillar 가드를 포섭·대체)
    """
    docs = [
        IndexedDoc(
            text=node.text,
            meta={
                "id": node.id,
                "source_file": getattr(node, "source_file", ""),
                "page": getattr(node, "page", None),
                "kesg_code": None,
            },
            chunk_id=node.id,
        )
        for node in text_nodes.values()
        if getattr(node, "kesg_code", None) is None
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
    """코드에 매칭되는 TextNode를 수집. 1차 code 매칭, 부족 시 BM25 보조.

    BM25 폴백은 미분류(kesg_code=None) 노드만 대상으로 한다.
    이미 다른 코드로 분류된 노드는 자기 코드의 code_match 경로에서만 사용되며,
    폴백 시장에 나오지 않는다 — same-pillar 오염 원천 차단.
    """
    nodes = evidence_graph.text_nodes_by_code(code)

    if nodes:
        return [
            {
                "id": getattr(n, "id", ""),
                "text": getattr(n, "text", ""),
                "source_file": getattr(n, "source_file", ""),
                "page": getattr(n, "page", None),
                "retrieval": RETRIEVAL_CODE_MATCH,
            }
            for n in nodes
        ]

    # BM25 폴백 — 인덱스 자체가 미분류 노드만 포함(pillar 가드 대체)
    req = requirement_for(code)
    query = " ".join(req.evidence_types) + " " + question_text
    results = bm25.search(query, k=_BM25_TOP_K)

    if not results:
        return []

    top1_score = results[0][1]

    accepted: list[dict[str, Any]] = []
    for doc, score in results:
        if score < _BM25_MIN_SCORE:
            continue
        if top1_score > 0 and score < top1_score * _BM25_RELATIVE_THRESHOLD:
            continue

        accepted.append({
            "id": doc.chunk_id or doc.meta.get("id", ""),
            "text": doc.text,
            "source_file": doc.meta.get("source_file", ""),
            "page": doc.meta.get("page"),
            "retrieval": RETRIEVAL_BM25_FALLBACK,
        })

    return accepted


def _format_chunks(chunks: list[dict[str, Any]]) -> str:
    """청크 리스트를 프롬프트용 텍스트로 포맷."""
    return "\n".join(f"- [{c['id']}] {c['text']}" for c in chunks)


def _check_relevance(
    question_text: str,
    chunks: list[dict[str, Any]],
    llm: LLMClient,
) -> bool:
    """BM25 폴백 청크가 질문에 실질적으로 답할 근거를 담고 있는지 판정.

    Fail-closed: 정확히 "YES" 토큰(뒤에 구두점 하나 허용)만 통과.
    """
    user_prompt = _RELEVANCE_USER_TEMPLATE.format(
        question=question_text,
        chunks_text=_format_chunks(chunks),
    )
    resp: LLMResponse = llm.complete(
        system=_RELEVANCE_SYSTEM,
        user=user_prompt,
        mock_hint="classify",
        temperature=0.0,
    )
    verdict = resp.content.strip().upper()
    return bool(re.fullmatch(r"YES[.!]?", verdict))


def _is_insufficient_draft(draft_text: str) -> bool:
    """센티널("INSUFFICIENT_EVIDENCE") 또는 문장 수준 자백 패턴 감지.

    Two-category sentence-level detection:
    - Standalone: 패턴만으로 자백 확정
    - Contextual: 발췌 지시어(referent)와 같은 문장에 동시 출현해야 자백
    """
    if "INSUFFICIENT_EVIDENCE" in draft_text:
        return True

    sentences = re.split(r"[.。!\n]", draft_text)

    for sentence in sentences:
        for pat in _CONFESSION_STANDALONE:
            if pat in sentence:
                return True

        for pat in _CONFESSION_CONTEXTUAL:
            if pat in sentence:
                if any(ref in sentence for ref in _EXCERPT_REFERENTS):
                    return True

    return False


def _attempt_draft(
    answer: Answer,
    chunks: list[dict[str, Any]],
    llm: LLMClient,
    *,
    max_retries: int,
) -> None:
    """초안 생성 → 근거게이트 검증 → 통과 시 draft_ready, 실패 시 재시도."""
    chunks_text = _format_chunks(chunks)

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

        # 센티널·자백 차단 — 재시도 없이 즉시 포기
        if _is_insufficient_draft(draft_text):
            logger.info(
                "drafter: 불충분 센티널/자백 감지 (qid=%s)", answer.qid,
            )
            return

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
            "retrieval": c.get("retrieval", RETRIEVAL_CODE_MATCH),
        }
        for c in chunks
    ]
