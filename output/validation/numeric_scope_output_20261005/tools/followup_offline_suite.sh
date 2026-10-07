#!/usr/bin/env bash
# PR71 후속(R1~R6) 오프라인 검증 묶음 — 새 OCR·LLM 호출 없음. 사용: followup_offline_suite.sh <worktree> <out_dir>
# 원본(BM 세트 PDF, numeric_scope_output_20261005의 캐시·원시 응답·로그, 검토 폴더)은 읽기만 하고 실행 전후 해시를 남긴다.
# 저장 응답 재생은 사본 캐시만 쓴다. 본문 생성 프롬프트가 바뀐 호출은 `inject_saved_section_responses.py`로 검토 당시의
# 저장 응답(오답 포함)을 그대로 넣는다 — 실호출이 아니며 실제 실행 성공으로 집계하지 않는다.
set -u
W=$1; OUT=$2; SHA=$(git -C "$W" rev-parse --short HEAD)
ROOT=/Users/heojeongmin/Documents/Claude/Projects/ESGenie
V=$ROOT/output/validation/numeric_scope_output_20261005
REVIEW=$ROOT/output/reviews/pr71_20261005
PACK="$ROOT/output/pdf/한울정밀_촬영세트_BM개편_20260928"
BASE=/private/tmp/ESGenie-numeric-scope-baseline-bba2206
TOOLS=$W/output/validation/numeric_scope_output_20261005/tools
RUN="$W/scripts/live_numeric_rehearsal.py"
mkdir -p "$OUT"/{hashes,tests,probe,real_zero,hmc,runs,caches,checks}
cd /tmp || exit 1

snapshot() {  # 원본·이전 산출물 해시(이번 출력 폴더는 뺀다)
  { find "$PACK" -type f -name '*.pdf' -print0 | xargs -0 shasum -a 256
    find "$V/caches" "$V/runs" "$V/02_real_zero_sample" "$V/04_hmc" -type f -print0 | xargs -0 shasum -a 256
    find "$REVIEW" -path "$REVIEW/followup_fix_validation" -prune -o -type f -print0 | xargs -0 shasum -a 256
  } | sort -k2 > "$1"
}
snapshot "$OUT/hashes/before.txt"
echo "== code $SHA $(git -C "$W" rev-parse HEAD)"

# 1) 검토 독립 검사 20건(수정 코드)·기간 4건(기준 main) — 검토 파일은 읽기만 한다.
REVIEW_CODE=$W PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q -o junit_family=xunit1 \
  "$REVIEW/test_pr71_remaining_guards.py" "$REVIEW/test_pr71_period_regression.py" \
  --junitxml="$OUT/tests/independent_20_$SHA.xml" > "$OUT/tests/independent_20_$SHA.log" 2>&1
echo "independent $(tail -1 "$OUT/tests/independent_20_$SHA.log")"
REVIEW_CODE=$BASE PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q -o junit_family=xunit1 \
  "$REVIEW/test_pr71_period_regression.py" --junitxml="$OUT/tests/baseline_period_bba2206.xml" \
  > "$OUT/tests/baseline_period_bba2206.log" 2>&1
echo "baseline period $(tail -1 "$OUT/tests/baseline_period_bba2206.log")"

# 2) 라우터 고정 입력 프로브(§5 35건)·실보고서 0 표본 재생(새 측정, 이전 65/90·0/71과 따로 기록)
python3 "$TOOLS/probe_scope_cases.py" "$W" "$OUT/probe/probe_scope_cases_$SHA.json" | tail -1
cp -R "$V/caches/RZ_real_zero_sample" "$OUT/caches/RZ_real_zero_sample_copy"
python3 "$TOOLS/run_real_zero_sample.py" "$W" "$OUT/caches/RZ_real_zero_sample_copy" "$V/02_real_zero_sample/selection.json" \
  "$OUT/real_zero/fix_${SHA}_offline.json" --offline > "$OUT/real_zero/fix_${SHA}_offline.log" 2>&1
tail -1 "$OUT/real_zero/fix_${SHA}_offline.log"

# 3) 저장 응답 주입 재생(최초 12건·보완 13건·변형본) — 네트워크 차단, Upstage 테이프·캐시 사본
inject() {  # id stage tape_src cache_src [extra...] ; windows는 INJECT_WINDOWS
  local id=$1 st=$2 tape=$3 csrc=$4; shift 4
  [ -d "$OUT/caches/$id" ] || cp -R "$V/caches/$csrc" "$OUT/caches/$id"
  python3 "$TOOLS/inject_saved_section_responses.py" --saved-cache "$V/caches/$csrc/llm" $INJECT_WINDOWS \
    core --stage "$st" --export --replay-upstage "$V/runs/$tape/$st/raw_upstage" --run-id "$id" \
    --code-path "$W" --env-file "$ROOT/.env" --cache-dir "$OUT/caches/$id" --run-dir "$OUT/runs/$id" --pack-dir "$PACK" "$@" \
    > "$OUT/runs/${id}_${st}_console.txt" 2>&1
  echo "$id $st exit=$? $(python3 -c "import json;s=json.load(open('$OUT/runs/$id/$st/run_stats.json'));e=json.load(open('$OUT/runs/$id/$st/injection_events.json'));print('llm',s['llm'],'misses',len(s['replay_llm_misses']),'injected',[(x['area'],x['attempt'],x['saved_key'][:10]) for x in e['events']])")"
}
R2I="--saved-window 2026-10-05T04:35:52,2026-10-05T04:38:45"
R2F="--saved-window 2026-10-05T04:38:45,2026-10-05T04:40:00"
R3I="--saved-window 2026-10-05T04:56:24,2026-10-05T04:59:00"
INJECT_WINDOWS="$R2I" inject "INJ_$SHA" initial R2_fix_4faabb0_live R2_fix_4faabb0_live
INJECT_WINDOWS="$R2I $R2F" inject "INJ_$SHA" followup R2_fix_4faabb0_live R2_fix_4faabb0_live
VAR=$(ls "$V"/03_variant/*.pdf)
INJECT_WINDOWS="$R2I $R3I" inject "INJV_$SHA" initial R3_variant_live R3_variant_live --extra-evidence "$VAR" --also-framework kesg61

# 4) 출력 검사기(정상 출력) + 오답 주입 사본 — 실패 시 종료 코드 1
for spec in "INJ_$SHA/initial:" "INJ_$SHA/followup:" "INJV_$SHA/initial:--variant"; do
  stage=${spec%%:*}; flag=${spec#*:}; tag=$(echo "$stage" | tr / _)
  python3 "$TOOLS/check_outputs.py" "$OUT/runs/$stage" $flag --json "$OUT/checks/${tag}_checks.json" > "$OUT/checks/${tag}_checks.txt" 2>&1
  echo "check $stage exit=$? $(tail -1 "$OUT/checks/${tag}_checks.txt")"
  python3 "$TOOLS/check_outputs.py" "$OUT/runs/$stage" $flag --inject --json "$OUT/checks/${tag}_inject.json" > "$OUT/checks/${tag}_inject.txt" 2>&1
  echo "inject $stage exit=$? $(tail -1 "$OUT/checks/${tag}_inject.txt")"
done
# 검토 당시 산출물(R2r)에도 새 검사기를 돌린다 — 오답 15명·21명이 실패로 잡혀야 한다(종료 코드 1이 기대값).
python3 "$TOOLS/check_outputs.py" "$V/runs/R2r_replay_329daa7/followup" --json "$OUT/checks/R2r_329daa7_followup_newchecker.json" \
  > "$OUT/checks/R2r_329daa7_followup_newchecker.txt" 2>&1
echo "R2r(329daa7) with new checker exit=$? $(tail -1 "$OUT/checks/R2r_329daa7_followup_newchecker.txt")"

# 5) HMC 감사 검증(기존 renewable:source_identity_page 2건과 비교)
mkdir -p "$OUT/hmc/$SHA"
(cd "$W" && ESGENIE_FORCE_MOCK=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python3 scripts/hmc_integrity_validation.py \
  --evidence-dir "$ROOT/시연증빙세트_한울정밀공업" --cache-dir "$ROOT/data/_cache/ocr" --out-dir "$OUT/hmc/$SHA" \
  --log-dir "$OUT/hmc/$SHA/logs" > "$OUT/hmc/$SHA/console.txt" 2>&1)
python3 -c "import json;d=json.load(open('$OUT/hmc/$SHA/logs/validation_report.json'));print('hmc failed',d['failed'],'actual',d['actual']['executed'],'controlled',d['controlled']['executed'])"

# 6) 저장소 검사(관련 회귀 → 전체). 키·모델 호출 없음.
export ESGENIE_FORCE_MOCK=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 ESGENIE_OCR_CACHE_DIR=/tmp/esg-fu-ocr-$SHA ESGENIE_LLM_CACHE_DIR=/tmp/esg-fu-llm-$SHA
unset OPENAI_API_KEY AZURE_OPENAI_ENDPOINT UPSTAGE_API_KEY
cd "$W" || exit 1
T=$OUT/tests
python3 -m pytest tests/test_pr71_followup_review_regressions.py tests/test_numeric_scope_output_20261005.py -q -p no:cacheprovider > "$T/pr71_$SHA.txt" 2>&1; echo "pr71 $(tail -1 "$T/pr71_$SHA.txt")"
python3 -m pytest tests/test_pr69_*.py -q -p no:cacheprovider > "$T/pr69_$SHA.txt" 2>&1; echo "pr69 $(tail -1 "$T/pr69_$SHA.txt")"
python3 -m pytest tests/test_pr68_*.py tests/test_ocr_numeric_extraction.py tests/test_ocr_bbox_norm.py tests/test_unit_dictionary_coverage.py tests/test_d1_units.py -q -p no:cacheprovider > "$T/pr68_pr70_units_$SHA.txt" 2>&1; echo "pr68/70/units $(tail -1 "$T/pr68_pr70_units_$SHA.txt")"
python3 -m pytest tests/test_representative_scope_period.py tests/test_measurement_boundary.py tests/test_node_selection.py tests/test_ledger_hold_reason.py tests/test_ledger_provenance.py tests/test_source_review.py tests/test_answer_comparison_state.py tests/test_d1_consumers.py tests/test_supplychain_*.py tests/test_layer2_body_format.py tests/test_layer6_report.py tests/test_pdf_glyph_coverage.py tests/test_pdf_render.py tests/test_review_pdf_text.py tests/test_hmc_followup.py tests/test_hmc_validation_contract.py tests/test_grounding_gate.py -q -p no:cacheprovider > "$T/scope_ledger_answer_report_export_hmc_$SHA.txt" 2>&1; echo "scope/ledger/answer/report/export/hmc $(grep -E 'passed|failed' "$T/scope_ledger_answer_report_export_hmc_$SHA.txt" | tail -1)"
python3 -m pytest tests -q -p no:cacheprovider -rs -o junit_family=xunit1 --junitxml="$T/full_$SHA.xml" > "$T/full_$SHA.txt" 2>&1; echo "full $(tail -1 "$T/full_$SHA.txt")"

cd /tmp || exit 1
snapshot "$OUT/hashes/after.txt"
if diff -q "$OUT/hashes/before.txt" "$OUT/hashes/after.txt" > /dev/null; then echo "hashes unchanged"; else
  diff "$OUT/hashes/before.txt" "$OUT/hashes/after.txt" > "$OUT/hashes/diff.txt"; echo "hashes CHANGED (see hashes/diff.txt)"; fi
