#!/usr/bin/env python
"""단계 시간 합 ≤ 전체 경과 + 캐시 재생/신규 `timings` 예시 (작업지시서 A §3.3 ②).

**세 가지를 실제로 돌려 떨군다.**

1. `timings.pipeline_sample.json` — 저장소의 **공개 DART 샘플**로 파이프라인을 한 번 돌린
   실제 단계 기록. 단계 이름·초는 모두 실측이다.
2. `timings.cache_new.json` — 비어 있는 LLM 캐시에 대고 돌린 **신규 처리** 기록.
3. `timings.cache_replay.json` — 같은 키를 다시 조회한 **캐시 재생** 기록.

2·3은 `esgenie.llm_cache`의 **실제** `lookup()`/`store()`를 임시 캐시 디렉터리에 대고
부른다. 적중·미스 숫자는 그 모듈의 카운터에서 그대로 나오고, 초는 `perf_counter`
실측이다. 단계 이름(`synthetic_*`)은 합성이라고 이름에 적는다 — 파이프라인 단계인
것처럼 쓰지 않는다.

**왜 합성 입력인가:** BM 실제 응답은 라벨 확정 전까지 열지 않는다. 또 OCR 캐시의
재생/신규는 실제 Upstage·LLM 호출이 있어야 갈리므로, 키가 설정된 리허설 실행의
`timings.json`(`cache_profile.ocr_cache`)에서 나온다. 여기서 그 숫자를 만들어 적지
않는다.

검사하는 불변식:
    staged_sum ≤ seconds(전체 경과)   — 단계를 겹치지 않게 잡았다는 뜻
    `_`로 시작하는 합계 행은 staged_sum에 들어가지 않는다

사용법:
    PYTHONPATH=. python scripts/verify_timing_cache_modes.py --out-dir /tmp/timing_examples
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _check(records: list[dict[str, Any]], total_stage: str) -> dict[str, Any]:
    """합계 행을 찾아 불변식을 확인한다. 어기면 예외를 올린다(조용히 넘기지 않는다)."""
    totals = [r for r in records if r.get("stage") == total_stage]
    if len(totals) != 1:
        raise AssertionError(f"합계 행 {total_stage}가 정확히 1개여야 한다: {len(totals)}개")
    total = totals[0]
    staged = total["staged_sum"]
    if staged > total["seconds"] + 1e-6:
        raise AssertionError(f"단계 합({staged})이 전체 경과({total['seconds']})보다 크다")
    return {"total_seconds": total["seconds"], "staged_sum": staged,
            "unstaged_seconds": total["unstaged_seconds"],
            "stage_rows": sum(1 for r in records if not str(r.get("stage", "")).startswith("_"))}


def _dump(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"wrote {path}")


def pipeline_sample(out_dir: Path, corp: str, areas: list[str]) -> dict[str, Any]:
    """공개 DART 샘플로 파이프라인을 돌려 실제 단계 기록을 받는다."""
    from esgenie import llm_cache
    from esgenie.pipeline import run
    from esgenie.ssot import ocr_cache

    llm_cache.reset_stats()
    output = run(corp, areas=list(areas), save_traces=False)
    hits, misses, mode = ocr_cache.summarize(getattr(output, "ocr_extractions", []) or [])
    summary = _check(output.timings, "_pipeline_total")
    _dump(out_dir / "timings.pipeline_sample.json", {
        "kind": "pipeline_sample",
        "input": f"공개 DART 샘플 {corp} (areas={','.join(areas)}) — BM 실제 응답 아님",
        "cache_profile": {"llm_cache": llm_cache.stats(),
                          "ocr_cache": {"hits": hits, "misses": misses, "mode": mode}},
        "invariant": summary,
        "note": "단계 이름·초는 모두 실측이다. llm_cache mode가 disabled면 "
                "키 없이 돌린 실행이라 적중·미스가 0이다 — 0을 캐시 재생 증거로 쓰지 않는다.",
        "stages": output.timings,
    })
    return summary


def cache_modes(out_dir: Path, items: int) -> dict[str, dict[str, Any]]:
    """같은 키를 두 번 돌려 신규 처리 / 캐시 재생 기록을 각각 남긴다."""
    from esgenie import llm_cache
    from esgenie.run_timing import RunTiming

    keys = [llm_cache.make_key(provider="synthetic", model="synthetic-timing-probe",
                               system="timing probe", user=f"probe-{i}",
                               temperature=0.0, json_mode=False) for i in range(items)]
    summaries: dict[str, dict[str, Any]] = {}
    for kind, label in (("cache_new", "신규 처리"), ("cache_replay", "캐시 재생")):
        llm_cache.reset_stats()
        timing = RunTiming()
        for i, key in enumerate(keys):
            with timing.stage(f"synthetic_llm_lookup_{i}", probe=True) as rec:
                found = llm_cache.lookup(key)
                rec["cache_hit"] = found is not None
                if found is None:
                    llm_cache.store(key, f"probe payload {i}",
                                    provider="synthetic", model="synthetic-timing-probe",
                                    response_meta={"source": "scripts/verify_timing_cache_modes.py",
                                                   "synthetic": True})
        timing.finish("_probe_total")
        summaries[kind] = _check(timing.records, "_probe_total")
        _dump(out_dir / f"timings.{kind}.json", {
            "kind": kind,
            "input": f"합성 프로브 {items}건 — 실제 문서·응답이 아니다",
            "cache_profile": {"llm_cache": llm_cache.stats()},
            "invariant": summaries[kind],
            "note": f"{label} 기록. 적중·미스는 esgenie.llm_cache 카운터 실측이고 "
                    "초는 perf_counter 실측이다. 단계 이름은 합성(`synthetic_`)이다.",
            "stages": timing.records,
        })
    return summaries


def main() -> None:
    ap = argparse.ArgumentParser(description="단계 합 불변식 + 캐시 재생/신규 timings 예시")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--corp", default="005930", help="공개 DART 샘플 종목코드")
    ap.add_argument("--areas", nargs="+", default=["E", "S", "G"])
    ap.add_argument("--items", type=int, default=3, help="합성 프로브 건수")
    ap.add_argument("--skip-pipeline", action="store_true",
                    help="파이프라인 샘플을 건너뛴다(캐시 예시만 다시 만들 때)")
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="timing_llm_cache_") as tmp:
        # 실제 캐시 디렉터리를 건드리지 않는다. 프로브 엔트리를 공용 캐시에 섞지 않는다.
        os.environ["ESGENIE_LLM_CACHE_DIR"] = tmp
        os.environ["ESGENIE_LLM_CACHE"] = "1"
        if not args.skip_pipeline:
            report["pipeline_sample"] = pipeline_sample(args.out_dir, args.corp, list(args.areas))
        report.update(cache_modes(args.out_dir, args.items))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("INVARIANT OK — 모든 기록에서 단계 합 ≤ 전체 경과")


if __name__ == "__main__":
    main()
