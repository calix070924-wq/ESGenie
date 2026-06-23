"""RAG retrieval/grounding evaluation runners."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from ..config import DATA_DIR, ROOT_DIR
from ..dart_client import CompanyReport, load_report
from ..layer2_rag import HybridRAG
from ..rag_gates import evaluate_grounding

RAG_EVAL_DIR = DATA_DIR / "rag_eval"
RETRIEVAL_QRELS_PATH = RAG_EVAL_DIR / "retrieval_qrels.jsonl"
GROUNDING_LABELS_PATH = RAG_EVAL_DIR / "grounding_labels.jsonl"
OUTPUT_DIR = ROOT_DIR / "outputs" / "rag_eval"


@dataclass
class RetrievalEvalRow:
    query_id: str
    corp_code: str
    area: str
    query: str
    relevant_chunk_ids: list[str]
    predicted_chunk_ids: list[str]
    top1_score: float
    hit_at_k: bool
    reciprocal_rank: float
    gate_decision: str
    hard_fails: list[str]


@dataclass
class GroundingEvalRow:
    case_id: str
    faithful: bool
    predicted_faithful: bool
    hallucinated_numbers_expected: list[str]
    hallucinated_numbers_predicted: list[str]
    uncited_expected: bool
    uncited_predicted: bool
    decision: str
    faithfulness_score: float


_RAG_CACHE: dict[str, tuple[CompanyReport, HybridRAG]] = {}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def evaluate_retrieval_dataset(path: Path = RETRIEVAL_QRELS_PATH, *, k: int = 5) -> dict[str, Any]:
    cases = load_jsonl(path)
    rows = [_run_retrieval_case(case, k=k) for case in cases]
    summary = summarize_retrieval(rows)
    summary["path"] = str(path)
    return {"rows": [asdict(row) for row in rows], "summary": summary}


def evaluate_grounding_dataset(path: Path = GROUNDING_LABELS_PATH) -> dict[str, Any]:
    cases = load_jsonl(path)
    rows: list[GroundingEvalRow] = []
    for case in cases:
        result = evaluate_grounding(case["answer"], case["cited_chunks"])
        rows.append(GroundingEvalRow(
            case_id=case["case_id"],
            faithful=bool(case["faithful"]),
            predicted_faithful=result.decision == "ACCEPT",
            hallucinated_numbers_expected=list(case.get("hallucinated_numbers", [])),
            hallucinated_numbers_predicted=result.g2_orphan_numbers,
            uncited_expected=bool(case.get("has_uncited_claim", False)),
            uncited_predicted=bool(result.g1_uncited_sentences),
            decision=result.decision,
            faithfulness_score=result.faithfulness,
        ))
    summary = summarize_grounding(rows)
    summary["path"] = str(path)
    return {"rows": [asdict(row) for row in rows], "summary": summary}


def summarize_retrieval(rows: list[RetrievalEvalRow]) -> dict[str, Any]:
    positives = [row for row in rows if row.relevant_chunk_ids]
    negatives = [row for row in rows if not row.relevant_chunk_ids]
    recall_at_k = _mean([1.0 if row.hit_at_k else 0.0 for row in positives])
    mrr = _mean([row.reciprocal_rank for row in positives])

    gate_tp = sum(1 for row in rows if row.relevant_chunk_ids and row.gate_decision == "ACCEPT")
    gate_fp = sum(1 for row in rows if (not row.relevant_chunk_ids) and row.gate_decision == "ACCEPT")
    gate_fn = sum(1 for row in rows if row.relevant_chunk_ids and row.gate_decision != "ACCEPT")
    gate_tn = sum(1 for row in rows if (not row.relevant_chunk_ids) and row.gate_decision != "ACCEPT")
    gate_precision, gate_recall, gate_f1 = _prf(gate_tp, gate_fp, gate_fn)
    gate_accuracy = (gate_tp + gate_tn) / len(rows) if rows else 0.0

    sweep = sweep_r1_threshold(rows)
    return {
        "n": len(rows),
        "n_positive": len(positives),
        "n_negative": len(negatives),
        "recall_at_k": round(recall_at_k, 4),
        "mrr": round(mrr, 4),
        "gate_precision": round(gate_precision, 4),
        "gate_recall": round(gate_recall, 4),
        "gate_f1": round(gate_f1, 4),
        "gate_accuracy": round(gate_accuracy, 4),
        "best_r1_threshold_by_f1": sweep["best_threshold_by_f1"],
        "r1_sweep": sweep["rows"],
    }


def summarize_grounding(rows: list[GroundingEvalRow]) -> dict[str, Any]:
    faith_tp = sum(1 for row in rows if row.faithful and row.predicted_faithful)
    faith_fp = sum(1 for row in rows if (not row.faithful) and row.predicted_faithful)
    faith_fn = sum(1 for row in rows if row.faithful and (not row.predicted_faithful))
    precision, recall, f1 = _prf(faith_tp, faith_fp, faith_fn)
    accuracy = _mean([1.0 if row.faithful == row.predicted_faithful else 0.0 for row in rows])
    hallucination_exact = _mean([
        1.0 if sorted(set(row.hallucinated_numbers_expected)) == sorted(set(row.hallucinated_numbers_predicted)) else 0.0
        for row in rows
    ])
    uncited_accuracy = _mean([
        1.0 if row.uncited_expected == row.uncited_predicted else 0.0
        for row in rows
    ])
    abstention_rate = _mean([1.0 if row.decision != "ACCEPT" else 0.0 for row in rows])
    return {
        "n": len(rows),
        "faithfulness_accuracy": round(accuracy, 4),
        "faithfulness_precision": round(precision, 4),
        "faithfulness_recall": round(recall, 4),
        "faithfulness_f1": round(f1, 4),
        "hallucination_exact_match": round(hallucination_exact, 4),
        "uncited_accuracy": round(uncited_accuracy, 4),
        "abstention_rate": round(abstention_rate, 4),
    }


def sweep_r1_threshold(rows: list[RetrievalEvalRow]) -> dict[str, Any]:
    candidates = sorted({0.0, *(round(row.top1_score, 4) for row in rows)}, reverse=True)
    out: list[dict[str, Any]] = []
    best_threshold = 0.0
    best_f1 = -1.0
    for threshold in candidates:
        tp = sum(1 for row in rows if row.relevant_chunk_ids and row.top1_score >= threshold)
        fp = sum(1 for row in rows if (not row.relevant_chunk_ids) and row.top1_score >= threshold)
        fn = sum(1 for row in rows if row.relevant_chunk_ids and row.top1_score < threshold)
        precision, recall, f1 = _prf(tp, fp, fn)
        entry = {
            "threshold": threshold,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }
        out.append(entry)
        if f1 > best_f1 or (f1 == best_f1 and threshold < best_threshold):
            best_f1 = f1
            best_threshold = threshold
    return {"best_threshold_by_f1": best_threshold, "rows": out}


def write_report(result: dict[str, Any], *, name: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{name}.json"
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False, indent=2)
    return out_path


def _run_retrieval_case(case: dict[str, Any], *, k: int) -> RetrievalEvalRow:
    _, rag = _get_rag(case["corp_code"])
    ctx = rag.retrieve(case["query"], k=k, area=case["area"])
    predicted_chunk_ids = [doc.chunk_id for doc, _ in ctx.corp_hits[:k]]
    relevant = list(case.get("relevant_chunk_ids", []))
    hit_at_k = any(chunk_id in predicted_chunk_ids for chunk_id in relevant)
    reciprocal_rank = 0.0
    for idx, chunk_id in enumerate(predicted_chunk_ids, start=1):
        if chunk_id in relevant:
            reciprocal_rank = 1.0 / idx
            break
    top1_score = float(ctx.corp_hits[0][1]) if ctx.corp_hits else 0.0
    decision = ctx.retrieval_decision
    gate_decision = decision.decision if decision is not None else "HUMAN"
    hard_fails = decision.hard_fails if decision is not None else ["R0_no_decision"]
    return RetrievalEvalRow(
        query_id=case["query_id"],
        corp_code=case["corp_code"],
        area=case["area"],
        query=case["query"],
        relevant_chunk_ids=relevant,
        predicted_chunk_ids=predicted_chunk_ids,
        top1_score=top1_score,
        hit_at_k=hit_at_k,
        reciprocal_rank=round(reciprocal_rank, 4),
        gate_decision=gate_decision,
        hard_fails=hard_fails,
    )


def _get_rag(corp_code: str) -> tuple[CompanyReport, HybridRAG]:
    cached = _RAG_CACHE.get(corp_code)
    if cached is not None:
        return cached
    report = load_report(corp_code)
    rag = HybridRAG()
    rag.build_corp_index(report)
    _RAG_CACHE[corp_code] = (report, rag)
    return report, rag


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def _cli() -> None:
    parser = argparse.ArgumentParser(description="RAG retrieval/grounding eval runner")
    parser.add_argument("task", choices=["retrieval", "grounding", "all"], nargs="?", default="all")
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()

    if args.task in {"retrieval", "all"}:
        retrieval = evaluate_retrieval_dataset(k=args.k)
        path = write_report(retrieval, name="retrieval_eval")
        print(json.dumps(retrieval["summary"], ensure_ascii=False, indent=2))
        print(f"saved: {path}")
    if args.task in {"grounding", "all"}:
        grounding = evaluate_grounding_dataset()
        path = write_report(grounding, name="grounding_eval")
        print(json.dumps(grounding["summary"], ensure_ascii=False, indent=2))
        print(f"saved: {path}")


if __name__ == "__main__":
    _cli()
