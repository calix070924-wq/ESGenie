from __future__ import annotations

from esgenie.eval.bootstrap_rag_eval import bootstrap
from esgenie.eval.rag_eval import (
    GROUNDING_LABELS_PATH,
    RETRIEVAL_QRELS_PATH,
    evaluate_grounding_dataset,
    load_jsonl,
    summarize_grounding,
    summarize_retrieval,
    sweep_r1_threshold,
)
from esgenie.eval.rag_eval import GroundingEvalRow, RetrievalEvalRow


def test_rag_eval_expanded_files_exist_and_load() -> None:
    retrieval_rows = load_jsonl(RETRIEVAL_QRELS_PATH)
    grounding_rows = load_jsonl(GROUNDING_LABELS_PATH)

    assert len(retrieval_rows) >= 30
    assert len(grounding_rows) >= 20
    assert all("query_id" in row for row in retrieval_rows)
    assert all("case_id" in row for row in grounding_rows)
    assert all("source_file" in row for row in retrieval_rows[:5])
    assert all("label_method" in row for row in grounding_rows[:5])


def test_bootstrap_rag_eval_generates_expected_scale() -> None:
    counts = bootstrap()

    assert counts["retrieval_qrels"] >= 40
    assert counts["grounding_labels"] >= 30


def test_summarize_retrieval_metrics() -> None:
    rows = [
        RetrievalEvalRow(
            query_id="q1",
            corp_code="005930",
            area="E",
            query="온실가스",
            relevant_chunk_ids=["corp_1"],
            predicted_chunk_ids=["corp_1"],
            top1_score=0.7,
            hit_at_k=True,
            reciprocal_rank=1.0,
            gate_decision="ACCEPT",
            hard_fails=[],
        ),
        RetrievalEvalRow(
            query_id="q2",
            corp_code="005930",
            area="E",
            query="없는 질의",
            relevant_chunk_ids=[],
            predicted_chunk_ids=["corp_9"],
            top1_score=0.02,
            hit_at_k=False,
            reciprocal_rank=0.0,
            gate_decision="HUMAN",
            hard_fails=["R1_low_top1_score"],
        ),
    ]

    summary = summarize_retrieval(rows)

    assert summary["n"] == 2
    assert summary["recall_at_k"] == 1.0
    assert summary["mrr"] == 1.0
    assert summary["gate_precision"] == 1.0
    assert summary["gate_recall"] == 1.0


def test_sweep_r1_threshold_prefers_perfect_cut() -> None:
    rows = [
        RetrievalEvalRow("q1", "c1", "E", "a", ["x"], ["x"], 0.8, True, 1.0, "ACCEPT", []),
        RetrievalEvalRow("q2", "c1", "E", "b", [], ["y"], 0.1, False, 0.0, "HUMAN", ["R1"]),
    ]

    sweep = sweep_r1_threshold(rows)

    assert sweep["best_threshold_by_f1"] == 0.8
    assert any(entry["threshold"] == 0.8 for entry in sweep["rows"])


def test_summarize_grounding_metrics() -> None:
    rows = [
        GroundingEvalRow("c1", True, True, [], [], False, False, "ACCEPT", 1.0),
        GroundingEvalRow("c2", False, False, ["31"], ["31"], True, True, "ESCALATE", 0.0),
    ]

    summary = summarize_grounding(rows)

    assert summary["n"] == 2
    assert summary["faithfulness_accuracy"] == 1.0
    assert summary["hallucination_exact_match"] == 1.0
    assert summary["uncited_accuracy"] == 1.0
    assert summary["abstention_rate"] == 0.5


def test_evaluate_grounding_dataset_runs_on_expanded_set() -> None:
    result = evaluate_grounding_dataset()

    assert result["summary"]["n"] >= 30
    assert 0.0 <= result["summary"]["faithfulness_accuracy"] <= 1.0
    assert result["rows"][0]["case_id"].startswith("grd-")
