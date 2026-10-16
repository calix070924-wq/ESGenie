#!/usr/bin/env python3
"""A·B 원값 대조(협의 전) 행별 CSV를 집합·노출 기준별로 센다.

입력은 `data/eval/independence/ab_raw_compare_*.csv` 하나뿐이다. 라벨 파일도 응답도 읽지
않는다. 내는 것은 세 가지 부분집합의 건수와 분모다.

- raw: 노출 여부와 무관한 전체 행 (**독립 일치율이 아니다**)
- b_independent: B 노출 기록이 `independent`인 행
- b_independent_a_not_reported: 위에서 A가 노출을 신고한 행(A_status=excluded)까지 뺀 행

분모가 0이면 비율을 쓰지 않고 `None`을 돌려준다(0%로 쓰지 않는다). 합산하지 않는다 —
공식 20행과 수치 전수 12행은 집합별로만 센다(겹치는 2행을 고유 30행으로 합쳐 무작위 표본이라
부르지 않는다).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

SUBSETS = {
    "raw": lambda r: True,
    "b_independent": lambda r: r["B_status"] == "independent",
    "b_independent_a_not_reported":
        lambda r: r["B_status"] == "independent" and r["A_status"] != "excluded",
}


def _frac(num: int, den: int) -> dict:
    return {"n": num, "d": den, "text": f"{num}/{den}" if den else "산출 불가(분모 0)"}


def summarize_rows(rows: list[dict]) -> dict:
    out: dict = {}
    for name, keep in SUBSETS.items():
        sel = [r for r in rows if keep(r)]
        both_hold = [r for r in sel if r["A_decision"] == "hold" and r["B_decision"] == "hold"]
        both_ans = [r for r in sel if r["A_decision"] == "answer" and r["B_decision"] == "answer"]
        out[name] = {
            "rows": len(sel),
            "decision_match": _frac(sum(r["A_decision"] == r["B_decision"] for r in sel), len(sel)),
            "hold_reason_match": _frac(
                sum(r["A_hold_reason"] == r["B_hold_reason"] for r in both_hold), len(both_hold)),
            "value_match_b_20261009": _frac(
                sum(r["A_value"].strip() == r["B_value_20261009"].strip() for r in both_ans),
                len(both_ans)),
            "value_match_b_20261010": _frac(
                sum(r["A_value"].strip() == r["B_value_20261010"].strip() for r in both_ans),
                len(both_ans)),
        }
    out["b_status_counts"] = {
        s: sum(r["B_status"] == s for r in rows) for s in ("independent", "excluded", "pending")}
    return out


def summarize(path: Path) -> dict:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    by_set: dict[str, list[dict]] = {}
    for r in rows:
        by_set.setdefault(r["set"], []).append(r)
    return {s: summarize_rows(rs) for s, rs in by_set.items()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("csv")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    result = summarize(Path(args.csv))
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
