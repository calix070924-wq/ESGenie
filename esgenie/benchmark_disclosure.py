"""D6 선택적 공시(문서단위) 벤치 하네스.

문장단위 greenwash 벤치와 별개 트랙. 룰 기반 결정적 탐지기를
'사람이 부여한 기대 레벨'과 대조해 레벨 정확도·혼동행렬을 본다.
불일치는 임계값 캘리브레이션 신호.

실행: python -m esgenie.benchmark_disclosure
"""
from __future__ import annotations
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .layer3_disclosure import detect_selective_disclosure

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "data" / "benchmark_v2" / "disclosure_bench.json"
LEVELS = ["low", "medium", "high"]


def _ext(disclosed: list[str], missing: list[str]) -> SimpleNamespace:
    return SimpleNamespace(mapped={c: {"code": c} for c in disclosed}, missing=list(missing))


def run(bench_path: Path = BENCH) -> dict[str, Any]:
    bench = json.loads(Path(bench_path).read_text(encoding="utf-8"))
    cases = bench["cases"]
    rows, level_ok, flag_ok = [], 0, 0
    conf = {a: {b: 0 for b in LEVELS} for a in LEVELS}   # 기대 → 예측
    for c in cases:
        d = detect_selective_disclosure(_ext(c["disclosed"], c["missing"]))
        exp, pred = c["expected_level"], d.level
        conf[exp][pred] += 1
        level_ok += int(exp == pred)
        # 이진(의심 여부): medium+ = flagged
        flag_ok += int((exp in ("medium", "high")) == (pred in ("medium", "high")))
        rows.append({"id": c["id"], "scenario": c["scenario"], "score": d.score,
                     "expected": exp, "pred": pred, "match": exp == pred})

    n = len(cases)
    out = {"n": n, "level_acc": level_ok / n, "flag_acc": flag_ok / n,
           "confusion": conf, "rows": rows,
           "disagreements": [r for r in rows if not r["match"]]}

    # ── 리포트 ──
    print(f"# D6 선택적 공시 벤치 — {n}케이스")
    print(f"- 레벨 정확도(3분류): {out['level_acc']*100:.1f}%")
    print(f"- 의심 탐지 정확도(이진 medium+): {out['flag_acc']*100:.1f}%\n")
    print(f"{'ID':7s} {'score':>6s}  {'기대':7s} {'예측':7s}  시나리오")
    for r in rows:
        mark = "" if r["match"] else "   ❌"
        print(f"{r['id']:7s} {r['score']:6.3f}  {r['expected']:7s} {r['pred']:7s}{mark}  {r['scenario']}")
    print("\n혼동행렬 (행=기대, 열=예측):")
    print(f"{'':9s}" + "".join(f"{l:>8s}" for l in LEVELS))
    for a in LEVELS:
        print(f"{a:9s}" + "".join(f"{conf[a][b]:8d}" for b in LEVELS))
    if out["disagreements"]:
        print("\n불일치 (캘리브레이션 점검):")
        for r in out["disagreements"]:
            print(f"  · {r['id']} {r['scenario']}: 기대={r['expected']} 예측={r['pred']} (score {r['score']:.3f})")

    md = [f"# D6 선택적 공시 벤치 결과", "",
          f"- 케이스 {n} · 레벨 정확도 {out['level_acc']*100:.1f}% · 이진 {out['flag_acc']*100:.1f}%", ""]
    md += ["| ID | score | 기대 | 예측 | 일치 | 시나리오 |", "|---|---|---|---|---|---|"]
    md += [f"| {r['id']} | {r['score']:.3f} | {r['expected']} | {r['pred']} | "
           f"{'✅' if r['match'] else '❌'} | {r['scenario']} |" for r in rows]
    (ROOT / "outputs" / "benchmark").mkdir(parents=True, exist_ok=True)
    (ROOT / "outputs" / "benchmark" / "disclosure_bench_report.md").write_text(
        "\n".join(md), encoding="utf-8")
    return out


if __name__ == "__main__":
    run()
