# -*- coding: utf-8 -*-
"""Compute inter-pass agreement between gold_passA.json and gold_passB.json.

Usage:
  # Read-only: print agreement stats (DEFAULT, safe)
  python scripts/compute_gold_agreement.py

  # Write mode: update unstructured_gold.json (preserves human fields)
  python scripts/compute_gold_agreement.py --write

Agreement metric: For each fact in pass A, find best-matching fact in pass B
(by token overlap >= 50%). Count bidirectional matches.
Agreement = 2 * matched / (|A| + |B|) per document.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_HUMAN_PRESERVED_FIELDS = frozenset({
    "agreement_human", "labeler2_human", "facts_gold_human",
})


def _tokenize(text: str) -> set[str]:
    tokens = re.findall(r'[\w\d가-힣]+', text)
    return {t.lower() for t in tokens if len(t) >= 2}


def _fact_matches(fact_a: dict, fact_b: dict, threshold: float = 0.5) -> bool:
    """Check if two facts are semantically equivalent via token overlap."""
    ta = _tokenize(fact_a["text"])
    tb = _tokenize(fact_b["text"])
    if not ta or not tb:
        return False
    overlap = ta & tb
    return (len(overlap) >= len(ta) * threshold and
            len(overlap) >= len(tb) * threshold)


def compute_agreement(pass_a_path: str, pass_b_path: str) -> dict:
    """Compute agreement between two gold passes. Read-only — no file writes."""
    with open(pass_a_path, encoding="utf-8") as f:
        a_data = json.load(f)
    with open(pass_b_path, encoding="utf-8") as f:
        b_data = json.load(f)

    a_docs = {d["doc_id"]: d for d in a_data["docs"]}
    b_docs = {d["doc_id"]: d for d in b_data["docs"]}

    results = {}
    total_a = 0
    total_b = 0
    total_matched = 0

    for doc_id in a_docs:
        if doc_id not in b_docs:
            continue
        if "_scan" in doc_id:
            continue

        facts_a = a_docs[doc_id].get("facts_gold", [])
        facts_b = b_docs[doc_id].get("facts_gold", [])

        matched_b = set()
        matched_a = set()
        for i, fa in enumerate(facts_a):
            for j, fb in enumerate(facts_b):
                if j in matched_b:
                    continue
                if _fact_matches(fa, fb):
                    matched_a.add(i)
                    matched_b.add(j)
                    break

        n_matched = len(matched_a)
        n_a = len(facts_a)
        n_b = len(facts_b)
        agreement = 2 * n_matched / (n_a + n_b) if (n_a + n_b) > 0 else 1.0

        results[doc_id] = {
            "facts_a": n_a,
            "facts_b": n_b,
            "matched": n_matched,
            "agreement": round(agreement, 4),
        }
        total_a += n_a
        total_b += n_b
        total_matched += n_matched

    overall = 2 * total_matched / (total_a + total_b) if (total_a + total_b) > 0 else 0
    return {
        "per_doc": results,
        "overall": {
            "total_facts_a": total_a,
            "total_facts_b": total_b,
            "total_matched": total_matched,
            "agreement": round(overall, 4),
        },
    }


def reconcile_gold(pass_a_path: str, pass_b_path: str, output_path: str,
                   agreement_result: dict) -> None:
    """Reconcile passes into final gold. Preserves human-entered fields
    from existing gold if present."""
    with open(pass_a_path, encoding="utf-8") as f:
        a_data = json.load(f)

    # Load existing gold to preserve human fields
    existing_by_id: dict[str, dict] = {}
    if Path(output_path).exists():
        with open(output_path, encoding="utf-8") as f:
            existing = json.load(f)
        existing_by_id = {d["doc_id"]: d for d in existing.get("docs", [])}

    for doc in a_data["docs"]:
        doc_id = doc["doc_id"]
        if "_scan" in doc_id:
            orig_id = doc_id.replace("_scan", "")
            orig = next((d for d in a_data["docs"] if d["doc_id"] == orig_id), None)
            if orig:
                doc["facts_gold"] = orig["facts_gold"]
                doc["metrics_gold"] = orig["metrics_gold"]
            continue

        doc["labeler1"] = "claude-code-passA"
        doc["labeler2"] = "claude-code-passB (AI cross-validation, not human)"
        per = agreement_result["per_doc"].get(doc_id, {})
        doc["agreement_ai"] = per.get("agreement")

        # Preserve human-entered fields from existing gold
        existing_doc = existing_by_id.get(doc_id, {})
        doc["agreement_human"] = existing_doc.get("agreement_human")
        if existing_doc.get("labeler2_human"):
            doc["labeler2_human"] = existing_doc["labeler2_human"]
        if existing_doc.get("facts_gold_human"):
            doc["facts_gold_human"] = existing_doc["facts_gold_human"]

    a_data["meta"] = {
        "labeling_method": "AI 2-pass independent derivation from source text only",
        "passA_file": str(pass_a_path),
        "passB_file": str(pass_b_path),
        "overall_agreement_ai": agreement_result["overall"]["agreement"],
        "note": "AI 2-pass self-consistency is NOT a substitute for human inter-annotator agreement",
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(a_data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"Reconciled gold written to: {output_path}")


if __name__ == "__main__":
    a_path = "data/benchmark_ocr/gold_passA.json"
    b_path = "data/benchmark_ocr/gold_passB.json"
    gold_path = "data/benchmark_ocr/unstructured_gold.json"
    write_mode = "--write" in sys.argv

    if not Path(a_path).exists() or not Path(b_path).exists():
        print("ERROR: gold_passA.json and gold_passB.json must exist first.")
        sys.exit(1)

    agreement = compute_agreement(a_path, b_path)

    print("=" * 60)
    print("Gold Label Agreement: Pass A ↔ Pass B")
    print("=" * 60)
    for doc_id, info in agreement["per_doc"].items():
        print(f"  {doc_id:35} A={info['facts_a']} B={info['facts_b']} "
              f"matched={info['matched']} agreement={info['agreement']:.1%}")
    print(f"\n  Overall: {agreement['overall']['agreement']:.1%} "
          f"(matched={agreement['overall']['total_matched']} / "
          f"A={agreement['overall']['total_facts_a']} + B={agreement['overall']['total_facts_b']})")
    print(f"\n  Scope: digital 11 docs only (scan 4 excluded — same gold as digital originals)")

    if write_mode:
        reconcile_gold(a_path, b_path, gold_path, agreement)
        print("\nDone. Human labeling worksheet: data/benchmark_ocr/labeling_worksheet.csv")
    else:
        print("\n  (Read-only mode. Use --write to update unstructured_gold.json)")
