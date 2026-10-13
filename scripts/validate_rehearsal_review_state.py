"""Offline replay of the real normal5 rehearsal; no AI/OCR requests or source writes."""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess

from esgenie.supplychain.claims import parse_saq_claims
from esgenie.web.review import reconcile

BASE_SHA = "6f5dd9488b54570b2115345e4a74d35d2aa2b28c"
WASTE = "RBA-C-4-E-6-2"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_copy(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rehearsal", type=Path, required=True)
    parser.add_argument("--set-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sources = [args.rehearsal / "before_followup_project.json",
               args.rehearsal / "runs/20261007T070758Z_rev19/result.json",
               args.rehearsal / "reconcile_repro.json"]
    pdfs = sorted(args.set_root.glob("01_처음업로드_17건/*.pdf"))
    pdfs += sorted(args.set_root.glob("02_교육보완때추가_1건/*.pdf"))
    assert len(pdfs) == 18, "Expected initial 17 + followup 1 PDFs"
    hashes_before = {str(p): digest(p) for p in sources + pdfs}
    project = json.loads(sources[0].read_text())
    previous = project["result"]
    result = json.loads(sources[1].read_text())
    assert len(result["sheet"]["answers"]) == 48
    assert project["reviews"][WASTE]["status"] == "complete"
    company_pdf = next(p for p in pdfs if p.name.startswith("05_"))
    parsed = asdict(parse_saq_claims([str(company_pdf)])["E-6-2"])
    assert isinstance(parsed["boundary"]["site_path"], tuple)
    # Restore parser containers in saved results without changing any content.
    fresh = deepcopy(result)
    restored = 0
    for answer in fresh["sheet"]["answers"]:
        for claim in answer.get("self_reports", []):
            if claim.get("code") == parsed["code"]:
                assert claim["boundary"] == json_copy(parsed["boundary"])
                assert claim["candidates"] == json_copy(parsed["candidates"])
                claim["boundary"] = deepcopy(parsed["boundary"])
                claim["candidates"] = deepcopy(parsed["candidates"])
                restored += 1
    assert restored > 0
    assert json_copy(fresh) == result
    fresh_before = deepcopy(fresh)
    project_before = deepcopy(project)
    baseline_source = subprocess.check_output(
        ["git", "show", f"{BASE_SHA}:esgenie/web/review.py"], cwd=root, text=True)
    baseline_module = {}
    exec(compile(baseline_source, "baseline_review.py", "exec"), baseline_module)
    before, after = deepcopy(project), deepcopy(project)
    baseline_module["reconcile"](before, previous, fresh)
    reconcile(after, previous, fresh)
    assert before["reviews"][WASTE]["status"] == "again"
    assert after["reviews"][WASTE]["status"] == "complete"
    assert WASTE not in after["affected_questions"]
    assert "RBA-E-6" in after["affected_questions"]
    assert after["reviews"]["RBA-E-6"]["status"] == "again"
    assert after["reviews"][WASTE] == project["reviews"][WASTE]
    assert all(v["values"] == after["reviews"][q]["values"] and
               v["history"] == after["reviews"][q]["history"] for q, v in project["reviews"].items())
    assert fresh == fresh_before and project == project_before
    # Actual original replacement invalidation must never be healed by equal results.
    invalidated = deepcopy(project)
    invalidated["reviews"][WASTE]["status"] = "again"
    invalidated["affected_questions"] = [WASTE]
    reconcile(invalidated, previous, fresh)
    assert invalidated["reviews"][WASTE]["status"] == "again"
    assert WASTE in invalidated["affected_questions"]
    old = {a["qid"]: a for a in previous["sheet"]["answers"]}
    field_changes = {
        a["qid"]: [k for k in sorted(set(a) | set(old[a["qid"]])) if a.get(k) != old[a["qid"]].get(k)]
        for a in result["sheet"]["answers"] if a != old[a["qid"]]
    }
    report = {"baseline_sha": BASE_SHA,
              "code_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
              "set_id": "hanwool_bm_normal5_20261007_v1", "initial_files": 17, "followup_files": 1,
              "mode": "saved_real_results_with_real_company_pdf_parser", "external_ai_ocr_requests": 0,
              "answer_count": 48, "restored_claim_count": restored,
              "stored_site_path_type": type(old[WASTE]["self_reports"][0]["boundary"]["site_path"]).__name__,
              "fresh_site_path_type": type(parsed["boundary"]["site_path"]).__name__,
              "normalized_fresh_equals_saved_result": True,
              "before": {"affected_questions": before["affected_questions"],
                         "review_statuses": {q: v["status"] for q, v in before["reviews"].items()}},
              "after": {"affected_questions": after["affected_questions"],
                        "review_statuses": {q: v["status"] for q, v in after["reviews"].items()}},
              "actual_result_field_changes": field_changes,
              "human_values_and_history_preserved": True, "previous_invalidation_preserved": True,
              "inputs_unchanged": hashes_before == {str(p): digest(p) for p in sources + pdfs},
              "source_hashes": hashes_before,
              "code_hashes": {p: digest(root / p) for p in (
                  "esgenie/web/review.py", "tests/test_rehearsal_review_state.py",
                  "scripts/validate_rehearsal_review_state.py")}}
    assert report["inputs_unchanged"]
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "real_replay.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({k: report[k] for k in ("answer_count", "before", "after", "inputs_unchanged")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
