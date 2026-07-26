# -*- coding: utf-8 -*-
"""Ingest human labels from labeling_worksheet.csv into unstructured_gold.json.

Usage:
  # Dry-run (default): show what would change
  python scripts/ingest_human_labels.py

  # Apply: update unstructured_gold.json with human labels
  python scripts/ingest_human_labels.py --apply

Reads data/benchmark_ocr/labeling_worksheet.csv (filled by human labeler),
computes agreement_human vs gold(passA), and updates gold JSON.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

WORKSHEET_PATH = Path("data/benchmark_ocr/labeling_worksheet.csv")
GOLD_PATH = Path("data/benchmark_ocr/unstructured_gold.json")


def _tokenize(text: str) -> set[str]:
    tokens = re.findall(r'[\w\d가-힣]+', text)
    return {t.lower() for t in tokens if len(t) >= 2}


def _fact_matches(text_a: str, text_b: str, threshold: float = 0.5) -> bool:
    ta = _tokenize(text_a)
    tb = _tokenize(text_b)
    if not ta or not tb:
        return False
    overlap = ta & tb
    return (len(overlap) >= len(ta) * threshold and
            len(overlap) >= len(tb) * threshold)


def load_worksheet() -> dict[str, list[dict]]:
    """Load human labels grouped by doc_id. Returns {doc_id: [{fact_text, kesg_code_hint, labeler_name}]}."""
    if not WORKSHEET_PATH.exists():
        print(f"ERROR: {WORKSHEET_PATH} not found.")
        sys.exit(1)

    docs: dict[str, list[dict]] = {}
    with open(WORKSHEET_PATH, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            fact_text = (row.get("fact_text") or "").strip()
            if not fact_text:
                continue
            doc_id = row["doc_id"]
            docs.setdefault(doc_id, []).append({
                "fact_text": fact_text,
                "kesg_code_hint": row.get("kesg_code_hint", ""),
                "labeler_name": row.get("labeler_name", ""),
            })
    return docs


def compute_human_agreement(human_facts: list[dict], gold_facts: list[dict]) -> float:
    """Compute agreement between human-labeled facts and gold(passA)."""
    if not human_facts and not gold_facts:
        return 1.0
    if not human_facts or not gold_facts:
        return 0.0

    matched_gold = set()
    matched_human = set()
    for i, hf in enumerate(human_facts):
        for j, gf in enumerate(gold_facts):
            if j in matched_gold:
                continue
            if _fact_matches(hf["fact_text"], gf["text"]):
                matched_human.add(i)
                matched_gold.add(j)
                break

    n_matched = len(matched_human)
    total = len(human_facts) + len(gold_facts)
    return 2 * n_matched / total if total > 0 else 0.0


def main():
    apply_mode = "--apply" in sys.argv

    human_docs = load_worksheet()
    if not human_docs:
        print("Worksheet is empty — no human labels to ingest.")
        print("Fill data/benchmark_ocr/labeling_worksheet.csv first.")
        print("(Open the source PDF, read it, write facts in the 'fact_text' column.)")
        sys.exit(0)

    with open(GOLD_PATH, encoding="utf-8") as f:
        gold = json.load(f)
    gold_by_id = {d["doc_id"]: d for d in gold["docs"]}

    print("=" * 60)
    print("Human Label Ingestion")
    print("=" * 60)

    agreements = {}
    labeler_names = set()
    for doc_id, human_facts in human_docs.items():
        gold_doc = gold_by_id.get(doc_id)
        if not gold_doc:
            print(f"  WARNING: {doc_id} not in gold — skipping")
            continue

        gold_facts = gold_doc.get("facts_gold", [])
        agreement = compute_human_agreement(human_facts, gold_facts)
        agreements[doc_id] = agreement
        labeler_names.update(hf["labeler_name"] for hf in human_facts if hf["labeler_name"])

        print(f"  {doc_id:35} human={len(human_facts)} gold={len(gold_facts)} "
              f"agreement={agreement:.1%}")

    if not agreements:
        print("\nNo documents with human labels found.")
        sys.exit(0)

    overall = sum(agreements.values()) / len(agreements)
    labeler_str = ", ".join(sorted(labeler_names)) or "(unnamed)"
    print(f"\n  Overall agreement_human: {overall:.1%} ({len(agreements)} docs)")
    print(f"  Labeler(s): {labeler_str}")

    if apply_mode:
        for doc_id, agreement in agreements.items():
            gold_doc = gold_by_id[doc_id]
            gold_doc["agreement_human"] = round(agreement, 4)
            gold_doc["labeler2_human"] = labeler_str

        with open(GOLD_PATH, "w", encoding="utf-8") as f:
            json.dump(gold, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"\n  Updated: {GOLD_PATH}")
        print("  Fields written: agreement_human, labeler2_human")
    else:
        print(f"\n  (Dry-run. Use --apply to write to {GOLD_PATH})")


if __name__ == "__main__":
    main()
