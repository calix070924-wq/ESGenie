"""Post-generation grounding gate for citation and numeric support checks."""
from __future__ import annotations

from typing import Any

from .signals import (
    CitedSentence,
    extract_numbers,
    is_claim_sentence,
    number_in_text,
    parse_cited_sentences,
    strip_citation_markers,
)
from ..schemas import GroundingResult


def evaluate_grounding(answer_text: str, cited_chunks: list[dict[str, Any]]) -> GroundingResult:
    sentences = parse_cited_sentences(answer_text)
    chunk_map = {
        str(chunk.get("id") or ""): str(chunk.get("text") or "")
        for chunk in cited_chunks
        if chunk.get("id")
    }

    uncited: list[str] = []
    orphan_numbers: list[str] = []
    supported_sentences = 0
    claim_sentences = 0

    for sent in sentences:
        if not is_claim_sentence(sent.clean_text):
            continue
        claim_sentences += 1
        if not sent.cited_chunk_ids:
            uncited.append(sent.clean_text)
            continue

        cited_texts = [chunk_map[cid] for cid in sent.cited_chunk_ids if cid in chunk_map]
        if cited_texts:
            supported_sentences += 1
        for number in extract_numbers(sent.clean_text):
            if not any(number_in_text(number, chunk_text) for chunk_text in cited_texts):
                orphan_numbers.append(number)

    hard_fails: list[str] = []
    if uncited:
        hard_fails.append("G1_uncited_claims")
    if orphan_numbers:
        hard_fails.append("G2_orphan_numbers")

    decision = "ACCEPT" if not hard_fails else "ESCALATE"
    faithfulness = 1.0 if claim_sentences == 0 else round(supported_sentences / claim_sentences, 4)

    return GroundingResult(
        decision=decision,
        g1_uncited_sentences=uncited,
        g2_orphan_numbers=_dedupe(orphan_numbers),
        g4_unit_mismatches=[],
        g5_overclaim=False,
        hard_fails=hard_fails,
        soft_flags=[],
        faithfulness=faithfulness,
    )


def grounding_feedback(result: GroundingResult) -> str:
    if result.decision == "ACCEPT":
        return ""

    parts = [
        "=== 근거 게이트 재작성 제약 ===",
        "모든 주장 문장 끝에 제공된 검색 청크의 [chunk_id] 인용을 붙일 것.",
        "인용한 청크에 없는 숫자는 절대 새로 쓰지 말 것.",
        "근거가 없으면 해당 문장을 삭제하거나 보수적으로 완화할 것.",
    ]
    if result.g1_uncited_sentences:
        parts.append("인용 누락 문장:")
        parts.extend(f"- {text}" for text in result.g1_uncited_sentences[:5])
    if result.g2_orphan_numbers:
        parts.append("청크 원문에서 확인되지 않은 숫자:")
        parts.append("- " + ", ".join(result.g2_orphan_numbers[:10]))
    parts.append("=== 위 제약을 모두 지켜 재작성하라. ===")
    return "\n".join(parts)


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


__all__ = [
    "CitedSentence",
    "evaluate_grounding",
    "grounding_feedback",
    "parse_cited_sentences",
    "strip_citation_markers",
]
