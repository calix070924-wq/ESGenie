"""평가 인프라 — 신뢰도 캘리브레이션 + 불확실성 정량화 + abstention.

벤치마크 "정확도 한 숫자"를 넘어, **그 숫자를 얼마나 믿을 수 있는가**를 측정한다:

  1) 신뢰구간(bootstrap)   — F1/P/R이 작은 표본에서 얼마나 흔들리는가 (95% CI)
  2) 신뢰도 캘리브레이션    — risk_score를 P(그린워싱)으로 봤을 때 실제와 맞는가
                              (reliability diagram + ECE: Expected Calibration Error)
  3) abstention(선택적 판정) — 애매한 케이스를 사람에게 넘기면(coverage↓)
                              남은 자동판정 정확도가 얼마나 오르는가 (risk-coverage)

입력: outputs/benchmark/judge_cache.json  (calibrate capture가 생성, LLM 1회 실측)
실행: python -m esgenie.evaluate report

⚠ 캐시가 mock으로 생성됐으면 수치는 무의미(도구 검증용). 실키는
  ESGENIE_STRICT=1 python -m esgenie.calibrate capture 후 실행.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
from pathlib import Path
from typing import Any

from .calibrate import CACHE_PATH, BASELINE, _simulate_vector, _flagged


# ====================================================================
# 케이스 → (예측확률 p, 정답 y, 자동판정 pred) 변환
# ====================================================================

def _case_rows(records: list[dict[str, Any]], cfg: dict[str, float]) -> list[dict[str, Any]]:
    """각 케이스를 평가용 행으로: risk_score(=P예측), 정답, flagged 여부."""
    rows = []
    for rec in records:
        rv = _simulate_vector(rec, trigger=cfg["trigger"], rule_weight=cfg["rule_weight"])
        p = rv.risk_score
        pred = _flagged(rv, cfg["threshold"], cfg["axis_flag"])
        y = 1 if rec["label"] == "greenwash" else 0
        rows.append({"id": rec["id"], "category": rec["category"],
                     "p": p, "y": y, "pred": int(pred), "correct": int(int(pred) == y)})
    return rows


def _prf(rows: list[dict[str, Any]]) -> dict[str, float]:
    tp = sum(1 for r in rows if r["y"] == 1 and r["pred"] == 1)
    fp = sum(1 for r in rows if r["y"] == 0 and r["pred"] == 1)
    fn = sum(1 for r in rows if r["y"] == 1 and r["pred"] == 0)
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"precision": p, "recall": r, "f1": f1}


# ====================================================================
# 1) 부트스트랩 신뢰구간
# ====================================================================

def bootstrap_ci(rows: list[dict[str, Any]], *, metric: str = "f1",
                 n_boot: int = 2000, seed: int = 42) -> tuple[float, float, float]:
    """리샘플링으로 metric의 점추정 + 95% CI."""
    rng = random.Random(seed)
    n = len(rows)
    point = _prf(rows)[metric]
    samples = []
    for _ in range(n_boot):
        boot = [rows[rng.randrange(n)] for _ in range(n)]
        samples.append(_prf(boot)[metric])
    samples.sort()
    lo = samples[int(0.025 * n_boot)]
    hi = samples[int(0.975 * n_boot)]
    return round(point, 3), round(lo, 3), round(hi, 3)


# ====================================================================
# 2) 신뢰도 캘리브레이션 (ECE + reliability diagram)
# ====================================================================

def calibration(rows: list[dict[str, Any]], *, n_bins: int = 10) -> dict[str, Any]:
    """risk_score를 P(greenwash) 예측으로 보고 실제 양성률과 비교.

    ECE = Σ_bin (n_bin/N) · |예측확률평균 − 실제양성률|
    """
    bins: list[list[dict[str, Any]]] = [[] for _ in range(n_bins)]
    for r in rows:
        idx = min(int(r["p"] * n_bins), n_bins - 1)
        bins[idx].append(r)

    N = len(rows)
    ece = 0.0
    diagram = []
    for i, b in enumerate(bins):
        lo, hi = i / n_bins, (i + 1) / n_bins
        if not b:
            diagram.append({"range": f"{lo:.1f}-{hi:.1f}", "n": 0, "conf": None, "acc": None})
            continue
        conf = statistics.mean(r["p"] for r in b)          # 예측 P(greenwash) 평균
        acc = statistics.mean(r["y"] for r in b)           # 실제 양성률
        ece += (len(b) / N) * abs(conf - acc)
        diagram.append({"range": f"{lo:.1f}-{hi:.1f}", "n": len(b),
                        "conf": round(conf, 3), "acc": round(acc, 3),
                        "gap": round(conf - acc, 3)})
    return {"ece": round(ece, 4), "diagram": diagram}


# ====================================================================
# 3) abstention — risk-coverage 곡선
# ====================================================================

def risk_coverage(rows: list[dict[str, Any]], cfg: dict[str, float],
                  *, steps: tuple[float, ...] = (1.0, 0.9, 0.8, 0.7, 0.6, 0.5)) -> list[dict[str, Any]]:
    """결정 경계에서 먼(자신있는) 순으로 자동판정, 나머지는 사람에게 위임.

    confidence = |risk_score − threshold| (경계에서 멀수록 확신).
    coverage c = 자동판정 비율 → 그 부분집합의 정확도.
    """
    thr = cfg["threshold"]
    ordered = sorted(rows, key=lambda r: abs(r["p"] - thr), reverse=True)
    N = len(rows)
    out = []
    for c in steps:
        k = max(1, int(round(N * c)))
        subset = ordered[:k]
        acc = statistics.mean(r["correct"] for r in subset)
        out.append({"coverage": c, "auto_n": k, "defer_n": N - k,
                    "accuracy": round(acc, 3)})
    return out


# ====================================================================
# 리포트
# ====================================================================

def evaluate(cache_path: Path = CACHE_PATH, cfg: dict[str, float] | None = None) -> dict[str, Any]:
    cfg = cfg or BASELINE
    cache = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    records = cache["records"]
    rows = _case_rows(records, cfg)

    prf = _prf(rows)
    f1 = bootstrap_ci(rows, metric="f1")
    pr = bootstrap_ci(rows, metric="precision")
    rc = bootstrap_ci(rows, metric="recall")
    cal = calibration(rows)
    cov = risk_coverage(rows, cfg)

    mock = cache.get("llm_calls", 0) and "  ⚠(캐시 출처 확인: mock이면 무효)"
    lines = [
        "# ESGenie 평가 리포트 (신뢰도·불확실성)",
        "",
        f"- 케이스: {len(rows)} | 설정: trig={cfg['trigger']} w={cfg['rule_weight']} "
        f"thr={cfg['threshold']} axf={cfg['axis_flag']}",
        "",
        "## 1. 성능 + 95% 신뢰구간 (bootstrap, 작은 표본 변동성)",
        "",
        f"- F1        = {f1[0]:.3f}  (95% CI {f1[1]:.3f} ~ {f1[2]:.3f})",
        f"- Precision = {pr[0]:.3f}  (95% CI {pr[1]:.3f} ~ {pr[2]:.3f})",
        f"- Recall    = {rc[0]:.3f}  (95% CI {rc[1]:.3f} ~ {rc[2]:.3f})",
        "",
        "## 2. 신뢰도 캘리브레이션 (ECE — 낮을수록 좋음)",
        "",
        f"- **ECE = {cal['ece']:.4f}**  (risk_score가 실제 그린워싱 확률과 얼마나 일치하나)",
        "",
        "| 점수구간 | n | 예측P | 실제양성률 | 격차 |",
        "|---|---|---|---|---|",
    ]
    for d in cal["diagram"]:
        if d["n"] == 0:
            lines.append(f"| {d['range']} | 0 | - | - | - |")
        else:
            lines.append(f"| {d['range']} | {d['n']} | {d['conf']:.3f} | {d['acc']:.3f} | {d['gap']:+.3f} |")
    lines += [
        "",
        "## 3. abstention — risk-coverage (애매한 건 사람에게)",
        "",
        "| coverage(자동판정) | 자동 | 위임 | 자동판정 정확도 |",
        "|---|---|---|---|",
    ]
    for c in cov:
        lines.append(f"| {c['coverage']*100:.0f}% | {c['auto_n']} | {c['defer_n']} | {c['accuracy']:.3f} |")
    lines += [
        "",
        "> 해석: coverage를 낮출수록(=사람 위임↑) 자동판정 정확도가 오르면,",
        "> '자신 없을 때 넘기는' 설계가 작동하는 것. 목표 정확도에 맞는 위임 비율을 고른다.",
    ]
    report = "\n".join(lines)

    out_dir = Path(cache_path).parent
    md_path = out_dir / "eval_report.md"
    md_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\n저장: {md_path}")
    return {"prf": prf, "ci": {"f1": f1, "precision": pr, "recall": rc},
            "calibration": cal, "risk_coverage": cov}


def _cli() -> None:
    p = argparse.ArgumentParser(description="ESGenie 평가(신뢰도·불확실성) 리포트")
    p.add_argument("cmd", nargs="?", default="report", choices=["report"])
    p.parse_args()
    evaluate()


if __name__ == "__main__":
    _cli()
