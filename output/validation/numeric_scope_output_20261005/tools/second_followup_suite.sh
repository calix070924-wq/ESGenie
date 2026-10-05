#!/usr/bin/env bash
# PR71 재검토(A~D) 보완 최종 검증 묶음 — 새 OCR·LLM 호출 없음. 사용: second_followup_suite.sh <worktree> <out_dir>
# - RP_*  : 직전 실제 LLM 실행(LIVE_61b6167·LIVEV_61b6167)의 응답 캐시 사본을 네트워크 차단·캐시 적중만으로 재생한다.
#           생성 프롬프트가 바뀌지 않았음을 미스 0건으로 확인한다(미스가 있으면 실패로 기록 — 모의 성공으로 바꾸지 않는다).
# - INJ_* : 검토 당시 저장된 오답 응답(15명·21명)을 생성 순서대로 넣은 재생(`inject_saved_section_responses.py`).
# - RV_*  : 실제 응답에 A~D 관계 오답·정상 대조를 넣은 검증 실행(`inject_relation_variants.py`). RVS_*는 다른 사업장 관찰.
# 원본(BM 세트 PDF, numeric_scope_output_20261005의 캐시·원시 응답·로그, 검토 폴더)은 읽기만 하고 실행 전후 해시를 남긴다.
set -u
W=$1; OUT=$2; SHA=$(git -C "$W" rev-parse --short HEAD)
ROOT=/Users/heojeongmin/Documents/Claude/Projects/ESGenie
V=$ROOT/output/validation/numeric_scope_output_20261005
REVIEW=$ROOT/output/reviews/pr71_20261005
PREV=$REVIEW/followup_fix_validation/02_final_0b770df
PACK="$ROOT/output/pdf/한울정밀_촬영세트_BM개편_20260928"
BASE=/private/tmp/ESGenie-numeric-scope-baseline-bba2206
TOOLS=$W/output/validation/numeric_scope_output_20261005/tools
RUN="$W/scripts/live_numeric_rehearsal.py"
mkdir -p "$OUT"/{hashes,tests,probe,real_zero,hmc,runs,caches,checks,render}
cd /tmp || exit 1

snapshot() {  # 원본·이전 산출물 해시(이번 출력 폴더는 뺀다)
  { find "$PACK" -type f -name '*.pdf' -print0 | xargs -0 shasum -a 256
    find "$V" -type f -print0 | xargs -0 shasum -a 256
    find "$REVIEW" -path "$REVIEW/second_followup_fix_validation" -prune -o -type f -print0 | xargs -0 shasum -a 256
    find "$ROOT/시연증빙세트_한울정밀공업" -type f -print0 | xargs -0 shasum -a 256
  } | sort -k2 > "$1"
}
snapshot "$OUT/hashes/before.txt"
echo "== code $SHA $(git -C "$W" rev-parse HEAD) dirty=$(git -C "$W" status --porcelain | grep -v '^??' | wc -l | tr -d ' ')"

# 1) 독립 검사: 이전 20건·재검토 17건(수정 코드), 기간 4건(기준 main). 검토 파일은 읽기만 한다.
REVIEW_CODE=$W PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q -o junit_family=xunit1 \
  "$REVIEW/test_pr71_remaining_guards.py" "$REVIEW/test_pr71_period_regression.py" \
  --junitxml="$OUT/tests/independent_20_$SHA.xml" > "$OUT/tests/independent_20_$SHA.log" 2>&1
echo "independent20 $(tail -1 "$OUT/tests/independent_20_$SHA.log")"
REVIEW_CODE=$W PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q -o junit_family=xunit1 \
  "$REVIEW/second_review_29defa0/test_followup_remaining.py" \
  --junitxml="$OUT/tests/second_review_17_$SHA.xml" > "$OUT/tests/second_review_17_$SHA.log" 2>&1
echo "second_review17 $(tail -1 "$OUT/tests/second_review_17_$SHA.log")"
REVIEW_CODE=$BASE PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q -o junit_family=xunit1 \
  "$REVIEW/test_pr71_period_regression.py" --junitxml="$OUT/tests/baseline_period_bba2206.xml" \
  > "$OUT/tests/baseline_period_bba2206.log" 2>&1
echo "baseline period $(tail -1 "$OUT/tests/baseline_period_bba2206.log")"
# 재검토 검사기 탐침(복사본 — 원본은 검토 폴더에 결과를 쓰므로 실행하지 않는다)
cp "$REVIEW/second_review_29defa0/probe_output_checker.py" "$OUT/probe/probe_output_checker_copy.py"
REVIEW_CODE=$W PYTHONDONTWRITEBYTECODE=1 python3 "$OUT/probe/probe_output_checker_copy.py" > "$OUT/probe/probe_output_checker_$SHA.log" 2>&1
echo "second-review checker probe exit=$? $(grep -E '^(swapped|correct|wrong)' "$OUT/probe/probe_output_checker_$SHA.log" | tr '\n' ' ')"

# 2) 라우터 고정 프로브(§5)·고용형태 정답표(원본 PDF 직접 판독)·실보고서 0 표본 재생
python3 "$TOOLS/probe_scope_cases.py" "$W" "$OUT/probe/probe_scope_cases_$SHA.json" | tail -1
python3 "$TOOLS/derive_training_groups.py" "$PACK" "$OUT/probe/training_groups_from_pdf.json" > /dev/null && \
  diff -q "$OUT/probe/training_groups_from_pdf.json" "$V/06_second_followup/training_groups_from_pdf.json" 2>/dev/null; \
  echo "training groups from PDF: $(python3 -c "import json;print([(g['role'],g['group'],g['count']) for g in json.load(open('$OUT/probe/training_groups_from_pdf.json'))['groups']])")"
cp -R "$V/caches/RZ_real_zero_sample" "$OUT/caches/RZ_real_zero_sample_copy"
python3 "$TOOLS/run_real_zero_sample.py" "$W" "$OUT/caches/RZ_real_zero_sample_copy" "$V/02_real_zero_sample/selection.json" \
  "$OUT/real_zero/fix_${SHA}_offline.json" --offline > "$OUT/real_zero/fix_${SHA}_offline.log" 2>&1
tail -1 "$OUT/real_zero/fix_${SHA}_offline.log"

# 3) 실제 LLM 응답 재생(RP) — 직전 실호출 캐시 사본, 네트워크 차단, 캐시 미스 = 실패
replay() {  # id stage tape cache_src [extra...]
  local id=$1 st=$2 tape=$3 csrc=$4; shift 4
  [ -d "$OUT/caches/$id" ] || cp -R "$PREV/caches/$csrc" "$OUT/caches/$id"
  python3 "$RUN" core --stage "$st" --export --replay-upstage "$V/runs/$tape/$st/raw_upstage" --run-id "$id" \
    --code-path "$W" --env-file "$ROOT/.env" --cache-dir "$OUT/caches/$id" --run-dir "$OUT/runs/$id" --pack-dir "$PACK" "$@" \
    > "$OUT/runs/${id}_${st}_console.txt" 2>&1
  echo "$id $st exit=$? $(python3 -c "import json;s=json.load(open('$OUT/runs/$id/$st/run_stats.json'));print('llm',s['llm'],'misses',len(s['replay_llm_misses'] or []),'upstage',s['upstage']['requests'],'tape',sorted({e['status'] for e in s['upstage_tape'] or []}))")"
}
VAR=$(ls "$V"/03_variant/*.pdf)
replay "RP_$SHA" initial R2_fix_4faabb0_live LIVE_61b6167
replay "RP_$SHA" followup R2_fix_4faabb0_live LIVE_61b6167
replay "RPV_$SHA" initial R3_variant_live LIVEV_61b6167 --extra-evidence "$VAR" --also-framework kesg61

# 4) 저장 오답 응답 주입 재생(INJ) — 검토 당시 R2·R3 저장 응답(15명·21명 포함)
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
INJECT_WINDOWS="$R2I $R3I" inject "INJV_$SHA" initial R3_variant_live R3_variant_live --extra-evidence "$VAR" --also-framework kesg61

# 5) A~D 관계 변형 주입(RV)·다른 사업장 관찰(RVS) — 실제 응답 캐시 사본에 문장을 더해 제품 경로로 처리
variant() {  # id set stage
  local id=$1 set=$2 st=$3
  [ -d "$OUT/caches/$id" ] || cp -R "$PREV/caches/LIVE_61b6167" "$OUT/caches/$id"
  # 실제 보완 단계 요약 응답(직전 실호출 캐시 항목, RP 재생에서 같은 프롬프트로 적중) — 요약 프롬프트가 바뀌면 이것에 주입한다.
  python3 "$TOOLS/inject_relation_variants.py" --variant-set "$set" \
    --saved-summary "$PREV/caches/LIVE_61b6167/llm/8e1d99f1805ef421d1ad9ab8f1f93519623d0f2a4237497a4c250dd96381dddc.json" \
    core --stage "$st" --export \
    --replay-upstage "$V/runs/R2_fix_4faabb0_live/$st/raw_upstage" --run-id "$id" --code-path "$W" --env-file "$ROOT/.env" \
    --cache-dir "$OUT/caches/$id" --run-dir "$OUT/runs/$id" --pack-dir "$PACK" > "$OUT/runs/${id}_${st}_console.txt" 2>&1
  echo "$id $st exit=$? $(python3 -c "import json;s=json.load(open('$OUT/runs/$id/$st/run_stats.json'));e=json.load(open('$OUT/runs/$id/$st/injection_events.json'));print('llm',s['llm'],'misses',len(s['replay_llm_misses']),'events',[x['kind'] for x in e['events']])")"
  python3 "$TOOLS/check_relation_variants.py" "$OUT/runs/$id/$st" --json "$OUT/checks/${id}_${st}_variants.json" \
    > "$OUT/checks/${id}_${st}_variants.txt" 2>&1
  echo "variants $id $st exit=$? $(tail -1 "$OUT/checks/${id}_${st}_variants.txt")"
}
variant "RV_$SHA" ad followup
variant "RVS_$SHA" site followup

# 6) 출력 검사기(정상·오답 주입) — 실패 시 종료 코드 1
for spec in "RP_$SHA/initial:" "RP_$SHA/followup:" "RPV_$SHA/initial:--variant" "INJ_$SHA/initial:" "INJ_$SHA/followup:" \
            "INJV_$SHA/initial:--variant" "RV_$SHA/followup:" "RVS_$SHA/followup:"; do
  stage=${spec%%:*}; flag=${spec#*:}; tag=$(echo "$stage" | tr / _)
  python3 "$TOOLS/check_outputs.py" "$OUT/runs/$stage" $flag --json "$OUT/checks/${tag}_checks.json" > "$OUT/checks/${tag}_checks.txt" 2>&1
  echo "check $stage exit=$? $(tail -1 "$OUT/checks/${tag}_checks.txt")"
  case "$stage" in RV*) continue;; esac
  python3 "$TOOLS/check_outputs.py" "$OUT/runs/$stage" $flag --inject --json "$OUT/checks/${tag}_inject.json" > "$OUT/checks/${tag}_inject.txt" 2>&1
  echo "inject $stage exit=$? $(tail -1 "$OUT/checks/${tag}_inject.txt")"
done
# 검토 당시 산출물(R2r)·직전 최종 산출물에도 새 검사기 — 저장 파일을 다시 읽기만 한 결과(재생성과 구분)
python3 "$TOOLS/check_outputs.py" "$V/runs/R2r_replay_329daa7/followup" --json "$OUT/checks/R2r_329daa7_followup_newchecker.json" \
  > "$OUT/checks/R2r_329daa7_followup_newchecker.txt" 2>&1
echo "R2r(329daa7) with new checker exit=$? $(tail -1 "$OUT/checks/R2r_329daa7_followup_newchecker.txt")"
for spec in "LIVE_61b6167/initial:" "LIVE_61b6167/followup:" "LIVEV_61b6167/initial:--variant"; do
  stage=${spec%%:*}; flag=${spec#*:}; tag=prev_$(echo "$stage" | tr / _)
  python3 "$TOOLS/check_outputs.py" "$PREV/runs/$stage" $flag --json "$OUT/checks/${tag}_reread.json" > "$OUT/checks/${tag}_reread.txt" 2>&1
  echo "re-read $stage exit=$? $(tail -1 "$OUT/checks/${tag}_reread.txt")"
done

# 6b) 재생성한 보고서 Markdown을 직전 최종 산출물과 비교(줄 집합 — 부록 정책 초안 블록 순서는 재생마다 바뀐다)·실보고서 표본 기록 비교
mkdir -p "$OUT/compare"
for spec in "LIVE_61b6167/initial:RP_$SHA/initial" "LIVE_61b6167/followup:RP_$SHA/followup" "LIVEV_61b6167/initial:RPV_$SHA/initial" \
            "INJ_61b6167/initial:INJ_$SHA/initial" "INJ_61b6167/followup:INJ_$SHA/followup" "INJV_61b6167/initial:INJV_$SHA/initial"; do
  a=${spec%%:*}; b=${spec#*:}; tag=$(echo "$b" | tr / _)
  diff <(sed 's/_생성일: .*_//' "$PREV"/runs/$a/exports/*/ESG보고서_*.md | sort) \
       <(sed 's/_생성일: .*_//' "$OUT"/runs/$b/exports/*/ESG보고서_*.md | sort) > "$OUT/compare/${tag}_vs_prev.diff"
  echo "compare $b vs $a: $(grep -c '^[<>]' "$OUT/compare/${tag}_vs_prev.diff") differing lines (order-insensitive)"
done
python3 -c "
import json
a=json.load(open('$PREV/real_zero/fix_61b6167_offline.json'))['records']; b=json.load(open('$OUT/real_zero/fix_${SHA}_offline.json'))['records']
print('real zero records', len(a), len(b), 'differing fields', sum(1 for x,y in zip(a,b) for k in set(x)|set(y) if x.get(k)!=y.get(k)))"
{ git -C "$ROOT" status --porcelain=v1 | shasum -a 256; (cd "$ROOT" && shasum -a 256 app.py esgenie/ui/tabs.py "docs/본선_시연영상_컷시나리오_v2.md")
  git -C "$ROOT/outputs/ui_redesign_workspace" rev-parse HEAD; git -C "$ROOT/outputs/ui_redesign_workspace" status --porcelain | shasum -a 256; } \
  > "$OUT/hashes/root_user_state_after.txt"
echo "root user files + PR65: $(diff -q <(sed -n 2,6p "$REVIEW/second_followup_fix_validation/00_before_29defa0/hashes/root_user_state_before.txt") <(sed -n 2,6p "$OUT/hashes/root_user_state_after.txt") > /dev/null && echo unchanged || echo CHANGED)"

# 7) HMC 감사 검증(기존 renewable:source_identity_page 2건과 비교)
mkdir -p "$OUT/hmc/$SHA"
(cd "$W" && ESGENIE_FORCE_MOCK=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python3 scripts/hmc_integrity_validation.py \
  --evidence-dir "$ROOT/시연증빙세트_한울정밀공업" --cache-dir "$ROOT/data/_cache/ocr" --out-dir "$OUT/hmc/$SHA" \
  --log-dir "$OUT/hmc/$SHA/logs" > "$OUT/hmc/$SHA/console.txt" 2>&1)
python3 -c "import json;d=json.load(open('$OUT/hmc/$SHA/logs/validation_report.json'));print('hmc failed',d['failed'],'actual',d['actual']['executed'],'controlled',d['controlled']['executed'])"

# 8) 저장소 검사(관련 회귀 → 전체). 키·모델 호출 없음.
export ESGENIE_FORCE_MOCK=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 ESGENIE_OCR_CACHE_DIR=/tmp/esg-sfu-ocr-$SHA ESGENIE_LLM_CACHE_DIR=/tmp/esg-sfu-llm-$SHA
unset OPENAI_API_KEY AZURE_OPENAI_ENDPOINT UPSTAGE_API_KEY
cd "$W" || exit 1
T=$OUT/tests
python3 -m pytest tests/test_pr71_second_review_regressions.py -q -p no:cacheprovider -o junit_family=xunit1 --junitxml="$T/pr71_second_$SHA.xml" > "$T/pr71_second_$SHA.txt" 2>&1; echo "pr71 second review $(tail -1 "$T/pr71_second_$SHA.txt")"
python3 -m pytest tests/test_pr71_followup_review_regressions.py tests/test_numeric_scope_output_20261005.py -q -p no:cacheprovider > "$T/pr71_$SHA.txt" 2>&1; echo "pr71 $(tail -1 "$T/pr71_$SHA.txt")"
python3 -m pytest tests/test_pr69_*.py -q -p no:cacheprovider > "$T/pr69_$SHA.txt" 2>&1; echo "pr69 $(tail -1 "$T/pr69_$SHA.txt")"
python3 -m pytest tests/test_pr68_*.py tests/test_ocr_numeric_extraction.py tests/test_ocr_bbox_norm.py tests/test_unit_dictionary_coverage.py tests/test_d1_units.py -q -p no:cacheprovider > "$T/pr68_pr70_units_$SHA.txt" 2>&1; echo "pr68/70/units $(tail -1 "$T/pr68_pr70_units_$SHA.txt")"
python3 -m pytest tests/test_representative_scope_period.py tests/test_measurement_boundary.py tests/test_node_selection.py tests/test_ledger_hold_reason.py tests/test_ledger_provenance.py tests/test_source_review.py tests/test_answer_comparison_state.py tests/test_d1_consumers.py tests/test_supplychain_*.py tests/test_layer2_body_format.py tests/test_layer6_report.py tests/test_pdf_glyph_coverage.py tests/test_pdf_render.py tests/test_review_pdf_text.py tests/test_hmc_followup.py tests/test_hmc_validation_contract.py tests/test_grounding_gate.py -q -p no:cacheprovider > "$T/scope_ledger_answer_report_export_hmc_$SHA.txt" 2>&1; echo "scope/ledger/answer/report/export/hmc $(grep -E 'passed|failed' "$T/scope_ledger_answer_report_export_hmc_$SHA.txt" | tail -1)"
python3 -m pytest tests -q -p no:cacheprovider -rs -o junit_family=xunit1 --junitxml="$T/full_$SHA.xml" > "$T/full_$SHA.txt" 2>&1; echo "full $(tail -1 "$T/full_$SHA.txt")"

cd /tmp || exit 1
snapshot "$OUT/hashes/after.txt"
if diff -q "$OUT/hashes/before.txt" "$OUT/hashes/after.txt" > /dev/null; then echo "hashes unchanged"; else
  diff "$OUT/hashes/before.txt" "$OUT/hashes/after.txt" > "$OUT/hashes/diff.txt"; echo "hashes CHANGED (see hashes/diff.txt)"; fi
