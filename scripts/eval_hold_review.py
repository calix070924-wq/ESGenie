#!/usr/bin/env python3
"""보류 판단 적절성 평가(규칙 v2) CLI — v1 채점 결과 + 행별 판정 기록 → v2 보고서.

v1 결과 파일은 읽기만 한다. 출력은 별도 경로에 쓴다(기존 결과 보존).

    python -I scripts/eval_hold_review.py \
      --score <v1 score_result.json> --reviews data/eval/hold_review/hold_review_v2.csv \
      --out <출력>/score_v2.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esgenie.eval import hold_review  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--score", required=True, type=Path, help="v1 채점 결과 JSON")
    ap.add_argument("--reviews", required=True, type=Path, help="보류 판정 기록 CSV")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--overwrite", action="store_true")
    a = ap.parse_args(argv)
    if a.out.exists() and not a.overwrite:
        ap.error(f"출력이 이미 있다(덮어쓰기 금지): {a.out}")
    if a.out.resolve() == a.score.resolve():
        ap.error("v1 결과 파일에 덮어쓸 수 없다")
    rep = hold_review.run(a.score, a.reviews)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    for sc in rep["scopes"]:
        j = sc["판단_적절성"]
        print(f"{sc['run_id']} {sc['stage']}: 값·근거 {sc['A1_값근거_정확률']['numerator']}/{sc['A1_값근거_정확률']['denominator']}"
              f" · 확정 정답 {sc['M1_확정정답률']['numerator']}/{sc['M1_확정정답률']['denominator']}"
              f" · 정당 보류 {j['정당한_검토_보류']} · 불필요 보류 {j['불필요한_보류']} · 판단 대기 {j['판단_대기']}"
              f" · 잘못된 확정 {j['잘못된_확정']}")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
