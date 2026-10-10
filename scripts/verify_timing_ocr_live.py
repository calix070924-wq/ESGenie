#!/usr/bin/env python
"""실제 키로 돌리는 OCR **신규 처리 → 캐시 재생** 계측 (작업지시서 A §3.3 ②·③).

`scripts/verify_timing_cache_modes.py`는 합성 프로브로 캐시 두 모드를 보였다. 이
스크립트는 **실제 API 호출**로 같은 것을 보인다. 둘의 결과를 섞지 않는다 — 파일 이름과
`evidence_kind`(`synthetic_probe` / `live_api`)로 갈라 적는다.

하는 일:

1. 새로 만든 **빈 격리 캐시**(OCR·LLM 각각)에 대고 공개 샘플 PDF를 한 번 추출한다
   → 캐시 미스. 실제 호출 수·토큰·단계 시간을 적는다.
2. **같은 캐시**로 한 번 더 추출한다 → 캐시 적중. 라이브 호출 0이어야 한다.
3 두 기록에서 단계 합 ≤ 전체 경과를 확인한다.

지키는 것:

- **BM 라벨 작업과 독립적인 공개 샘플만** 쓴다. 저장소 밖 경로·BM 원본 폴더는 거부한다.
- **기존 캐시·원본·실행 기록을 건드리지 않는다.** 캐시 디렉터리는 비어 있어야 하고
  (`--cache-dir`), 비어 있지 않으면 멈춘다. 기본 캐시 경로는 쓰지 않는다.
- **mock 폴백을 실측으로 적지 않는다.** 추출이 mock이거나 라이브 호출이 0이면 1단계는
  실패로 멈춘다. 키가 없으면 "실제 실행"이라고 적지 않는다.
- 키 값은 읽기만 하고 화면·파일·로그에 적지 않는다. 적는 것은 모델명과 호출 수·토큰뿐이다.

사용법:
    PYTHONPATH=. python scripts/verify_timing_ocr_live.py \
      --env-file /path/to/.env \
      --cache-dir /tmp/ocr_live_cache_20261007 \
      --out-dir docs/validation/eval-timing-20261006/ocr_live
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]

# BM 원본·실행 기록은 이 스크립트의 입력이 될 수 없다.
FORBIDDEN_PARTS = ("labels_draft", "rehearsal", "reviews", "BM원본", "촬영세트", "outputs")


def _resolve_sample(raw: str) -> Path:
    path = Path(raw).resolve()
    if not path.is_file():
        raise SystemExit(f"[!] 파일이 없다: {path}")
    try:
        rel = path.relative_to(REPO)
    except ValueError as exc:
        raise SystemExit(
            f"[!] 저장소 밖 경로는 거부한다(공개 샘플만 쓴다): {path}") from exc
    for part in rel.parts:
        if any(bad in part for bad in FORBIDDEN_PARTS):
            raise SystemExit(f"[!] BM 작업 자료 경로는 거부한다: {rel}")
    return path


def _prepare_cache(root: Path) -> tuple[Path, Path]:
    """빈 격리 캐시 두 개를 만든다. 비어 있지 않으면 멈춘다."""
    if root.exists() and any(root.iterdir()):
        raise SystemExit(f"[!] 캐시 디렉터리가 비어 있지 않다 — 기존 캐시를 섞지 않는다: {root}")
    ocr_dir, llm_dir = root / "ocr", root / "llm"
    ocr_dir.mkdir(parents=True, exist_ok=True)
    llm_dir.mkdir(parents=True, exist_ok=True)
    os.environ["ESGENIE_OCR_CACHE_DIR"] = str(ocr_dir)
    os.environ["ESGENIE_LLM_CACHE_DIR"] = str(llm_dir)
    os.environ["ESGENIE_OCR_CACHE"] = "1"
    os.environ["ESGENIE_LLM_CACHE"] = "1"
    os.environ.pop("ESGENIE_OCR_CACHE_REFRESH", None)
    os.environ.pop("ESGENIE_LLM_CACHE_REFRESH", None)
    return ocr_dir, llm_dir


def _usage_totals(events: list[dict[str, Any]]) -> dict[str, int | None]:
    """실제 응답의 토큰 합계. 제공되지 않으면 `null`이다 — 0으로 적지 않는다."""
    totals: dict[str, int] = {}
    seen = False
    for ev in events:
        usage = ev.get("usage")
        if not isinstance(usage, dict):
            continue
        for key, value in usage.items():
            if isinstance(value, int):
                totals[key] = totals.get(key, 0) + value
                seen = True
    if not seen:
        return {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
    return dict(sorted(totals.items()))


def _check_invariant(records: list[dict[str, Any]], total_stage: str) -> dict[str, Any]:
    totals = [r for r in records if r.get("stage") == total_stage]
    if len(totals) != 1:
        raise AssertionError(f"합계 행 {total_stage}가 정확히 1개여야 한다: {len(totals)}개")
    total = totals[0]
    if total["staged_sum"] > total["seconds"] + 1e-6:
        raise AssertionError(
            f"단계 합({total['staged_sum']})이 전체 경과({total['seconds']})보다 크다")
    return {"total_seconds": total["seconds"], "staged_sum": total["staged_sum"],
            "unstaged_seconds": total["unstaged_seconds"]}


def _one_pass(sample: Path, pass_name: str) -> dict[str, Any]:
    """추출 한 번을 재고 캐시 상태·호출 수·토큰을 함께 돌려준다."""
    from esgenie import llm_cache
    from esgenie.run_timing import RunTiming
    from esgenie.ssot import ocr_cache, ocr_router

    llm_cache.reset_stats()
    timing = RunTiming()
    with timing.stage("ocr_route_document"):
        decision = ocr_router.route_document(str(sample))
    with timing.stage("ocr_extract_document") as rec:
        extraction = ocr_router.extract_document(str(sample), decision)
        rec["engine"] = (extraction.router_meta or {}).get("engine")
    timing.finish("_ocr_total")

    meta = extraction.router_meta or {}
    hits, misses, mode = ocr_cache.summarize([extraction])
    stats = llm_cache.stats()
    return {
        "pass": pass_name,
        "evidence_kind": "live_api",
        "input": {"file": str(sample.relative_to(REPO)), "doc_type": extraction.doc_type,
                  "channel": extraction.channel.value,
                  "note": "저장소의 공개 테스트 문서다. BM 원본·실제 응답이 아니다"},
        "cache_profile": {
            "ocr_cache": {"hits": hits, "misses": misses, "state": meta.get("ocr_cache"),
                          "mode": mode},
            "llm_cache": stats,
        },
        "calls": {"live_calls": stats.get("live_calls"), "successes": stats.get("successes"),
                  "failures": stats.get("failures"),
                  "model": meta.get("engine"), "chunks": meta.get("chunks")},
        "tokens": _usage_totals(llm_cache.response_events()),
        "extraction": {"status": meta.get("extraction_status"), "mock": bool(meta.get("mock")),
                       "raw_text_len": meta.get("raw_text_len"),
                       "metric_count": len(extraction.metrics or []),
                       "clause_count": len(extraction.clauses or [])},
        "invariant": _check_invariant(timing.records, "_ocr_total"),
        "stages": timing.records,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="실제 키 OCR 신규 처리 → 캐시 재생 계측")
    ap.add_argument("--pdf", default="data/test_docs/safety_policy_2025.pdf",
                    help="공개 샘플 PDF(저장소 안 경로만 허용). `ocr_cache`는 **비정형(L0) "
                         "경로**의 LLM 응답만 담으므로 비정형으로 라우팅되는 문서를 쓴다")
    ap.add_argument("--cache-dir", type=Path, required=True,
                    help="새 격리 캐시 루트. 비어 있어야 한다")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--env-file", type=Path, default=None,
                    help="키가 든 .env 경로. 값은 읽기만 하고 출력하지 않는다")
    args = ap.parse_args()

    if os.getenv("ESGENIE_FORCE_MOCK", "0") == "1":
        raise SystemExit("[!] ESGENIE_FORCE_MOCK=1이면 실제 호출이 아니다 — 끄고 다시 돌려라")

    if args.env_file:
        from dotenv import load_dotenv
        if not args.env_file.is_file():
            raise SystemExit(f"[!] env 파일이 없다: {args.env_file}")
        load_dotenv(args.env_file)

    sample = _resolve_sample(args.pdf)
    ocr_dir, llm_dir = _prepare_cache(args.cache_dir.resolve())
    args.out_dir.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO))

    from esgenie.config import SETTINGS
    if SETTINGS.force_mock or SETTINGS.use_mock_llm:
        raise SystemExit("[!] 설정이 mock이다 — 실제 호출 결과로 적을 수 없다")

    new_pass = _one_pass(sample, "new")
    if new_pass["input"]["channel"] != "unstructured":
        raise SystemExit(
            f"[!] 라우팅이 {new_pass['input']['channel']}다 — `ocr_cache`는 비정형(L0) "
            "경로의 LLM 응답만 담는다. 정형 문서로는 캐시 신규/재생이 갈리지 않는다")
    if new_pass["extraction"]["mock"] or new_pass["calls"]["live_calls"] == 0:
        raise SystemExit(
            "[!] 1단계가 실제 호출로 돌지 않았다"
            f" (mock={new_pass['extraction']['mock']},"
            f" live_calls={new_pass['calls']['live_calls']}) — 실측으로 적지 않는다")
    if new_pass["cache_profile"]["ocr_cache"]["hits"]:
        raise SystemExit("[!] 빈 캐시인데 적중이 생겼다 — 격리가 깨졌다")

    replay_pass = _one_pass(sample, "replay")

    for payload in (new_pass, replay_pass):
        path = args.out_dir / f"timings.ocr_live_{payload['pass']}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")
        print(f"wrote {path}")

    summary = {
        "evidence_kind": "live_api",
        "sample": str(sample.relative_to(REPO)),
        "cache_root": str(args.cache_dir.resolve()),
        "cache_files": {"ocr": len(list(ocr_dir.glob('*'))), "llm": len(list(llm_dir.glob('*')))},
        "new": {k: new_pass[k] for k in ("cache_profile", "calls", "tokens", "invariant")},
        "replay": {k: replay_pass[k] for k in ("cache_profile", "calls", "tokens", "invariant")},
        "checks": {
            "new_is_live": True,
            "replay_live_calls_zero": replay_pass["calls"]["live_calls"] == 0,
            "replay_ocr_hits_positive": replay_pass["cache_profile"]["ocr_cache"]["hits"] > 0,
            "replay_ocr_misses_zero": replay_pass["cache_profile"]["ocr_cache"]["misses"] == 0,
        },
        "note": "재생이 신규보다 빠를 것으로 기대하지만 단정하지 않는다. 두 전체 경과를 "
                "그대로 적고, 절감률로 바꾸지 않는다.",
    }
    (args.out_dir / "ocr_live_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    failed = [k for k, v in summary["checks"].items() if not v]
    if failed:
        raise SystemExit(f"[!] 확인 실패: {failed}")
    print("LIVE OCR CACHE OK — 신규 처리는 라이브 호출, 재생은 호출 0·적중만")


if __name__ == "__main__":
    main()
