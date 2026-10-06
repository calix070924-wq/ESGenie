#!/usr/bin/env bash
# 최종 코드의 오프라인 검증 묶음(새 OCR·LLM 호출 없음). 사용: final_offline_suite.sh <worktree> <validation_dir>
# 재생은 R0(main)·R2(수정, 실제 실행)·R3(변형본, 실제 실행)에서 기록한 Upstage 원시 응답과 LLM 캐시 사본만 쓴다.
set -u
W=$1; V=$2; SHA=$(git -C "$W" rev-parse --short HEAD)
ROOT=/Users/heojeongmin/Documents/Claude/Projects/ESGenie
PACK="$ROOT/output/pdf/한울정밀_촬영세트_BM개편_20260928"
E=$ROOT/output/reviews/pr69_20261003/eighth_fix_validation
RUN="$W/scripts/live_numeric_rehearsal.py"
cd /tmp || exit 1
echo "== $SHA"
python3 "$V/tools/probe_scope_cases.py" "$W" "$V/00_baseline/probe_scope_cases_$SHA.json" | tail -1
for c in r0 new old; do
  SRC=$([ $c = r0 ] && echo "$V/caches/R0_main_bba2206_live/ocr" || echo "$E/replay_final_${c}/cache_copy")
  python3 "$V/tools/replay_llm_cache_v3_pr69.py" "$W" "$V/01_replay/router_${c}_$SHA" "$SRC" > "$V/01_replay/router_${c}_$SHA.log" 2>&1
  echo "router $c exit=$? $(tail -1 "$V/01_replay/router_${c}_$SHA.log")"
done
cp -R "$V/caches/RZ_real_zero_sample" "$V/caches/RZ_real_zero_sample_copy_$SHA"
python3 "$V/tools/run_real_zero_sample.py" "$W" "$V/caches/RZ_real_zero_sample_copy_$SHA" "$V/02_real_zero_sample/selection.json" \
  "$V/02_real_zero_sample/fix_${SHA}_offline.json" --offline > "$V/02_real_zero_sample/fix_${SHA}_offline.log" 2>&1
tail -1 "$V/02_real_zero_sample/fix_${SHA}_offline.log"
replay() {  # run_id source_run stage [extra args...]
  local id=$1 src=$2 st=$3; shift 3
  [ -d "$V/caches/$id" ] || cp -R "$V/caches/$(basename "$src")" "$V/caches/$id" 2>/dev/null || cp -R "$V/caches/${src}" "$V/caches/$id"
  python3 "$RUN" core --stage "$st" --export --replay-upstage "$V/runs/$src/$st/raw_upstage" --run-id "$id" \
    --code-path "$W" --env-file "$ROOT/.env" --cache-dir "$V/caches/$id" --run-dir "$V/runs/$id" --pack-dir "$PACK" "$@" \
    > "$V/runs/${id}_${st}_console.txt" 2>&1
  echo "$id $st exit=$? $(python3 -c "import json;s=json.load(open('$V/runs/$id/$st/run_stats.json'));print('llm',s['llm'],'misses',len(s['replay_llm_misses']))")"
  python3 "$V/tools/check_outputs.py" "$V/runs/$id/$st" --json "$V/runs/$id/${st}_checks.json" | tail -1
}
replay "R1_replay_$SHA" R0_main_bba2206_live initial; replay "R1_replay_$SHA" R0_main_bba2206_live followup
replay "R2r_replay_$SHA" R2_fix_4faabb0_live initial; replay "R2r_replay_$SHA" R2_fix_4faabb0_live followup
VAR=$(ls "$V"/03_variant/*.pdf)
replay "R3r_replay_$SHA" R3_variant_live initial --extra-evidence "$VAR" --also-framework kesg61
mkdir -p "$V/04_hmc/$SHA"
(cd "$W" && ESGENIE_FORCE_MOCK=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python3 scripts/hmc_integrity_validation.py \
  --evidence-dir "$ROOT/시연증빙세트_한울정밀공업" --cache-dir "$ROOT/data/_cache/ocr" --out-dir "$V/04_hmc/$SHA" \
  --log-dir "$V/04_hmc/$SHA/logs" > "$V/04_hmc/$SHA/console.txt" 2>&1)
python3 -c "import json;d=json.load(open('$V/04_hmc/$SHA/logs/validation_report.json'));print('hmc failed',d['failed'],'actual',d['actual']['executed'],'controlled',d['controlled']['executed'])"
export ESGENIE_FORCE_MOCK=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 ESGENIE_OCR_CACHE_DIR=/tmp/esg-final-ocr-$SHA ESGENIE_LLM_CACHE_DIR=/tmp/esg-final-llm-$SHA
unset OPENAI_API_KEY AZURE_OPENAI_ENDPOINT UPSTAGE_API_KEY
cd "$W" || exit 1
T=$V/03_tests
python3 -m pytest tests/test_numeric_scope_output_20261005.py -q -p no:cacheprovider > "$T/new_tests_$SHA.txt" 2>&1; echo "new $(grep -E 'passed|failed' "$T/new_tests_$SHA.txt" | tail -1)"
python3 -m pytest tests/test_pr69_*.py -q -p no:cacheprovider > "$T/pr69_existing_$SHA.txt" 2>&1; echo "pr69 $(tail -1 "$T/pr69_existing_$SHA.txt")"
python3 -m pytest tests/test_pr68_*.py tests/test_ocr_numeric_extraction.py tests/test_ocr_bbox_norm.py tests/test_unit_dictionary_coverage.py tests/test_d1_units.py -q -p no:cacheprovider > "$T/pr68_pr70_units_$SHA.txt" 2>&1; echo "pr68/70/units $(tail -1 "$T/pr68_pr70_units_$SHA.txt")"
python3 -m pytest tests/test_representative_scope_period.py tests/test_measurement_boundary.py tests/test_node_selection.py tests/test_ledger_hold_reason.py tests/test_ledger_provenance.py tests/test_source_review.py tests/test_answer_comparison_state.py tests/test_d1_consumers.py tests/test_supplychain_*.py tests/test_layer2_body_format.py tests/test_layer6_report.py tests/test_pdf_glyph_coverage.py tests/test_pdf_render.py tests/test_review_pdf_text.py tests/test_hmc_followup.py tests/test_hmc_validation_contract.py -q -p no:cacheprovider > "$T/scope_ledger_answer_report_export_hmc_$SHA.txt" 2>&1; echo "scope/ledger/answer/report/export/hmc $(grep -E 'passed|failed' "$T/scope_ledger_answer_report_export_hmc_$SHA.txt" | tail -1)"
python3 -m pytest tests/audit_integrity/test_cache_connection.py -q -p no:cacheprovider > "$T/audit_isolation_$SHA.txt" 2>&1; echo "audit isolation $(tail -1 "$T/audit_isolation_$SHA.txt")"
python3 -m pytest tests -q -p no:cacheprovider -o junit_family=xunit1 --junitxml="$T/full_$SHA.xml" > "$T/full_$SHA.txt" 2>&1; echo "full $(tail -1 "$T/full_$SHA.txt")"
