# Gold Labeling Log — Combined (Pass A base + Pass B cross-validation)
## Method
- **Pass A** (claude-code-passA): Independent derivation from source PDF text only.
- **Pass B** (claude-code-passB): Independent derivation from source PDF text only (separate context).
- **Reconciliation**: Pass A used as base gold. Agreement computed via token overlap matching.
- **Human gate**: agreement_human field left null. To complete: use labeling_worksheet.csv.
- **Overall AI agreement**: 78.0% (69 matched / 88+89 total facts)

## Circular contamination safeguard
Neither pass consulted eval_results.json, judge_decisions.json, or any LLM extraction output.
Gold was derived exclusively from pymupdf text extraction of source PDFs.
Commit order: gold (commit A) precedes extraction results (commit C) in git history.

## Detailed pass A labeling log
See: data/benchmark_ocr/labeling_log_passA.md

## Per-document agreement

| doc_id | Pass A facts | Pass B facts | Matched | Agreement |
|---|---|---|---|---|
| work_hours_2025 | 8 | 7 | 7 | 93.3% |
| doc_record_mgmt_2025 | 8 | 7 | 6 | 80.0% |
| rohs_reach_2025 | 8 | 7 | 7 | 93.3% |
| capa_2025 | 8 | 7 | 6 | 80.0% |
| sanitation_housing_2025 | 8 | 8 | 6 | 75.0% |
| hazmat_2025 | 8 | 8 | 6 | 75.0% |
| ip_protection_2025 | 7 | 6 | 6 | 92.3% |
| responsible_minerals_2025 | 7 | 8 | 7 | 93.3% |
| safety_minutes_2025 | 8 | 9 | 7 | 82.3% |
| emergency_manual_2025 | 8 | 10 | 5 | 55.6% |
| hr_policy_2025 | 10 | 12 | 6 | 54.5% |
| **Overall** | **88** | **89** | **69** | **78.0%** |
