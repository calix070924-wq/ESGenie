#!/usr/bin/env bash
# PR71 후속 — 생성 입력·프롬프트가 바뀐 본문·요약 경로의 실제 LLM 실행. 사용: followup_live_runs.sh <worktree> <out_dir>
# Upstage는 R2·R3 실제 실행에서 기록한 원시 응답을 재생한다(새 OCR 호출 없음). LLM은 캐시 사본의 적중을 쓰고
# 미스(바뀐 프롬프트)만 실제로 호출한다(`--live-llm`, strict). 원본 캐시는 사본으로만 쓴다.
# CACHE_ROOT·LIVE_SRC·LIVEV_SRC로 시작 캐시를 바꿀 수 있다(이전 실호출 응답을 같은 프롬프트에 그대로 쓰려면).
set -u
W=$1; OUT=$2; SHA=$(git -C "$W" rev-parse --short HEAD)
ROOT=/Users/heojeongmin/Documents/Claude/Projects/ESGenie
V=$ROOT/output/validation/numeric_scope_output_20261005
PACK="$ROOT/output/pdf/한울정밀_촬영세트_BM개편_20260928"
TOOLS=$W/output/validation/numeric_scope_output_20261005/tools
RUN="$W/scripts/live_numeric_rehearsal.py"
mkdir -p "$OUT"/{runs,caches,checks}
cd /tmp || exit 1
CACHE_ROOT=${CACHE_ROOT:-$V/caches}
live() {  # id stage tape_src cache_src [extra...]
  local id=$1 st=$2 tape=$3 csrc=$4; shift 4
  [ -d "$OUT/caches/$id" ] || cp -R "$CACHE_ROOT/$csrc" "$OUT/caches/$id"
  python3 "$RUN" core --stage "$st" --export --replay-upstage "$V/runs/$tape/$st/raw_upstage" --live-llm --run-id "$id" \
    --code-path "$W" --env-file "$ROOT/.env" --cache-dir "$OUT/caches/$id" --run-dir "$OUT/runs/$id" --pack-dir "$PACK" "$@" \
    > "$OUT/runs/${id}_${st}_console.txt" 2>&1
  echo "$id $st exit=$? $(python3 -c "import json;s=json.load(open('$OUT/runs/$id/$st/run_stats.json'));print('llm',s['llm'],'models',sorted({e.get('returned_model') for e in s['llm_response_events']}),'upstage',s['upstage']['requests'],'tape',sorted({e['status'] for e in s['upstage_tape'] or []}))")"
}
live "LIVE_$SHA" initial R2_fix_4faabb0_live "${LIVE_SRC:-R2_fix_4faabb0_live}"
live "LIVE_$SHA" followup R2_fix_4faabb0_live "${LIVE_SRC:-R2_fix_4faabb0_live}"
VAR=$(ls "$V"/03_variant/*.pdf)
live "LIVEV_$SHA" initial R3_variant_live "${LIVEV_SRC:-R3_variant_live}" --extra-evidence "$VAR" --also-framework kesg61
for spec in "LIVE_$SHA/initial:" "LIVE_$SHA/followup:" "LIVEV_$SHA/initial:--variant"; do
  stage=${spec%%:*}; flag=${spec#*:}; tag=$(echo "$stage" | tr / _)
  python3 "$TOOLS/check_outputs.py" "$OUT/runs/$stage" $flag --json "$OUT/checks/${tag}_checks.json" > "$OUT/checks/${tag}_checks.txt" 2>&1
  echo "check $stage exit=$? $(tail -1 "$OUT/checks/${tag}_checks.txt")"
  python3 "$TOOLS/check_outputs.py" "$OUT/runs/$stage" $flag --inject --json "$OUT/checks/${tag}_inject.json" > "$OUT/checks/${tag}_inject.txt" 2>&1
  echo "inject $stage exit=$? $(tail -1 "$OUT/checks/${tag}_inject.txt")"
done
