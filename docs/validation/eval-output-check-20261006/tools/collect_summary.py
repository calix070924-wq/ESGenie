#!/usr/bin/env python3
"""검사·주입 결과에서 커밋할 요약 JSON만 모은다.

`outputs/eval/…`의 결과 원본은 커밋하지 않는다(gitignore 대상). 이 스크립트는
사람이 읽을 요약만 뽑아 `docs/validation/eval-output-check-20261006/` 아래에 쓴다.
개인 절대 경로는 저장소 상대 경로 또는 자리표시자로 바꾼다 — 경로에 사용자 이름이
들어가면 공개 문서에 그대로 남는다.

사용:
  python docs/validation/eval-output-check-20261006/tools/collect_summary.py \
      --live <outputs/eval/<날짜>_b1_live> --inject <outputs/eval/<날짜>_b1_inject> \
      --out docs/validation/eval-output-check-20261006
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[4]
# 결과 루트는 저장소 밖(다른 worktree 공통)일 수 있어 자리표시자로 바꾼다.
PLACEHOLDERS = (
    (re.compile(r"[A-Za-z]:[\\/].*?[\\/]outputs[\\/]eval[\\/]", re.IGNORECASE), "<OUTPUTS_EVAL>/"),
    (re.compile(r"[A-Za-z]:[\\/].*?[\\/]ESGenie_data[\\/].*?[\\/]", re.IGNORECASE), "<DATA_PACK>/"),
    (re.compile(r"[A-Za-z]:[\\/].*?[\\/](ESGenie-[A-Za-z0-9]+|ESGenie)[\\/]"), "<REPO>/"),
)


def scrub(value):
    """경로 문자열에서 개인 절대 경로를 지운다(값·키 모두 재귀)."""
    if isinstance(value, str):
        out = value
        for pattern, repl in PLACEHOLDERS:
            out = pattern.sub(repl, out)
        return out.replace("\\", "/")
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def live_summary(path: Path) -> dict:
    payload = json.loads((path / "consistency.json").read_text(encoding="utf-8"))
    return {
        "run_dir": scrub(payload["run_dir"]),
        "checked_at": payload["checked_at"],
        "fields": payload["fields"],
        "checks": payload.get("checks"),
        "inputs": {k: {"path": scrub(v["path"]), "sha256": v["sha256"]}
                   for k, v in payload["inputs"].items()},
        "summary": payload["summary"],
        # 불일치가 있으면 전부 싣는다 — 요약에서 빼지 않는다.
        "mismatches": scrub(payload["mismatches"]),
        "unchecked_kinds": sorted({(u["field"], u["reason"]) for u in payload["unchecked"]}),
    }


def inject_summary(path: Path) -> dict:
    log = json.loads((path / "inject_log.json").read_text(encoding="utf-8"))
    return {
        "run_dir": scrub(log["run_dir"]),
        "checker": log["checker"],
        "checked_at": log["checked_at"],
        "picked_rows": log["picked_rows"],
        "clean": log["clean"],
        "clean_copy_recheck": log["clean_copy_recheck"],
        "summary": log["summary"],
        "injections": [
            {k: scrub(v) for k, v in row.items()
             if k not in ("copy_dir",)}
            for row in log["injections"]
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", type=Path, required=True)
    ap.add_argument("--inject", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--reference", type=Path, default=None,
                    help="참고용 옛 실행 검사 결과 폴더(선택)")
    args = ap.parse_args()

    out = {"consistency": {}, "injection": {}}
    for stage in ("initial", "followup"):
        if (args.live / stage / "consistency.json").exists():
            out["consistency"][stage] = live_summary(args.live / stage)
        if (args.inject / stage / "inject_log.json").exists():
            out["injection"][stage] = inject_summary(args.inject / stage)
    if args.reference and args.reference.is_dir():
        out["reference_old_runs"] = {
            d.name: live_summary(d) for d in sorted(args.reference.iterdir())
            if (d / "consistency.json").exists()
        }

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "consistency_summary.json").write_text(
        json.dumps(out["consistency"], ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out / "injection_summary.json").write_text(
        json.dumps(out["injection"], ensure_ascii=False, indent=2), encoding="utf-8")
    if "reference_old_runs" in out:
        (args.out / "reference_old_runs_summary.json").write_text(
            json.dumps(out["reference_old_runs"], ensure_ascii=False, indent=2),
            encoding="utf-8")
    print(json.dumps({"consistency": list(out["consistency"]),
                      "injection": list(out["injection"]),
                      "reference": list(out.get("reference_old_runs", {})),
                      "out": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
