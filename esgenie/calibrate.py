"""그린워싱 검출 임계값 캘리브레이션 (오프라인 그리드 서치).

핵심 아이디어
-------------
임계값 조합마다 LLM을 다시 부르면 비용·시간 낭비다. LLM 판정은 **1회만**
실측해서 캐시(`capture`)하고, 그 위에서 4개 손잡이를 **오프라인 스윕**한다:
  - JUDGE_TRIGGER     : 룰 점수 ≥ trigger 인 축만 LLM 판정 반영
  - JUDGE_RULE_WEIGHT : final = w*rule + (1-w)*llm  (verdict=false_positive면 llm만)
  - threshold         : aggregate risk_score 판정 경계
  - axis_flag         : 단일 축 강신호 경계

LLM 판정은 co-triggered 축과 무관하게 안정적이라고 가정하고, capture 시
모든 판정가능 축을 한 번에 판정해 verdict+llm_score를 저장한다. 이후 trigger
스윕은 "rule_score ≥ trigger 인 축만 그 판정을 반영"으로 충실히 재현된다.

실행
----
  # 1) 실측 1회 — 실제 LLM 필요 (OPENAI_API_KEY + ESGENIE 환경)
  ESGENIE_STRICT=1 python -m esgenie.calibrate capture

  # 2) 오프라인 그리드 서치 — LLM 불필요, 즉시
  python -m esgenie.calibrate search
"""
from __future__ import annotations

import argparse
import copy
import itertools
import json
from pathlib import Path
from typing import Any

from .config import DATA_DIR, ROOT_DIR
from .schemas import AxisScore, RiskVector
from .layer3_judge import (
    JUDGE_SYSTEM, JUDGE_PROMPT_TEMPLATE, _parse_judge_response, _llm_score,
    _rebuild_vector,
)

BENCH_PATH = DATA_DIR / "benchmark" / "greenwash_bench.json"
CACHE_PATH = ROOT_DIR / "outputs" / "benchmark" / "judge_cache.json"

_AXES = ["D1_numeric", "D2_modifier", "D3_semantic", "D5_timeseries"]
# D3는 RAG 컨텍스트 의존이라 벤치마크에서 중립 고정 → 판정 대상에서 제외
_JUDGEABLE = ["D1_numeric", "D2_modifier", "D5_timeseries"]


def _is_judgeable(ax: AxisScore) -> bool:
    return "중립값" not in ax.detail and "스킵" not in ax.detail


# ====================================================================
# 1) CAPTURE — 실제 LLM 판정을 1회 실측해 캐시
# ====================================================================

def capture(bench_path: Path = BENCH_PATH, cache_path: Path = CACHE_PATH) -> dict[str, Any]:
    from .dart_client import load_report
    from .layer0_evidence_graph import build_evidence_graph
    from .layer3_detect import detect_risk_vector
    from .llm import LLMClient

    with open(bench_path, encoding="utf-8") as fp:
        bench = json.load(fp)
    report = load_report(bench.get("ticker", "005930"))
    graph = build_evidence_graph(report)
    llm = LLMClient()

    records: list[dict[str, Any]] = []
    n_calls = 0
    total = len(bench["cases"])
    print(f"capture 시작 — {total}개 케이스, 케이스마다 LLM 1회 호출", flush=True)
    for idx, case in enumerate(bench["cases"], 1):
        print(f"  [{idx}/{total}] {case['id']} ...", flush=True)
        sent, label = case["sentence"], case["label"]
        rv = detect_risk_vector(sent, evidence_graph=graph)
        axes_obj = {
            "D1_numeric": rv.D1_numeric, "D2_modifier": rv.D2_modifier,
            "D3_semantic": rv.D3_semantic, "D5_timeseries": rv.D5_timeseries,
        }
        # 판정가능(중립·스킵 아님) 축을 한 번에 LLM 판정
        triggered = {n: axes_obj[n] for n in _JUDGEABLE if _is_judgeable(axes_obj[n])}
        verdicts: dict[str, Any] = {}
        if triggered:
            axes_block = "\n".join(
                f"- {n} | rule_score={a.score} | detail={a.detail}"
                for n, a in triggered.items()
            )
            resp = llm.complete(
                system=JUDGE_SYSTEM,
                user=JUDGE_PROMPT_TEMPLATE.format(
                    sentence=sent, axes_block=axes_block,
                    axis_names=", ".join(triggered),
                ),
                mock_hint="judge", json_mode=True, temperature=0.0,
            )
            n_calls += 1
            verdicts = _parse_judge_response(resp.content)

        rec_axes: dict[str, Any] = {}
        for n in _AXES:
            a = axes_obj[n]
            entry: dict[str, Any] = {"rule_score": a.score, "detail": a.detail,
                                     "judgeable": n in triggered}
            if n in triggered and n in verdicts:
                v = verdicts[n]
                entry["verdict"] = v.get("verdict")
                entry["llm_score"] = _llm_score(v, a.score)
            rec_axes[n] = entry
        records.append({"id": case["id"], "label": label,
                        "category": case["category"], "axes": rec_axes})

    cache = {"ticker": bench.get("ticker", "005930"),
             "n_cases": len(records), "llm_calls": n_calls, "records": records}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 캐시 저장: {cache_path}  (cases={len(records)}, LLM호출={n_calls})")
    return cache


# ====================================================================
# 2) SEARCH — 캐시 위에서 오프라인 그리드 서치
# ====================================================================

def _simulate_vector(rec: dict[str, Any], *, trigger: float, rule_weight: float) -> RiskVector:
    """한 케이스를 (trigger, rule_weight) 조합으로 재합성한 RiskVector."""
    axes: dict[str, AxisScore] = {}
    for n in _AXES:
        a = rec["axes"][n]
        rule = float(a["rule_score"])
        judged = (a.get("judgeable") and a.get("verdict") is not None and rule >= trigger)
        if judged:
            llm_s = float(a["llm_score"])
            if a["verdict"] == "false_positive":
                blended = round(llm_s, 4)
            else:
                blended = round(rule_weight * rule + (1.0 - rule_weight) * llm_s, 4)
        else:
            blended = rule
        axes[n] = AxisScore(score=blended, evidence=[], detail="")
    return _rebuild_vector(axes)


def _flagged(rv: RiskVector, threshold: float, axis_flag: float) -> bool:
    max_axis = max(rv.D1_numeric.score, rv.D2_modifier.score, rv.D5_timeseries.score)
    return rv.risk_score >= threshold or max_axis >= axis_flag


def _f1(records: list[dict[str, Any]], *, trigger: float, rule_weight: float,
        threshold: float, axis_flag: float) -> dict[str, Any]:
    tp = fp = fn = tn = 0
    for rec in records:
        rv = _simulate_vector(rec, trigger=trigger, rule_weight=rule_weight)
        pred = _flagged(rv, threshold, axis_flag)
        gw = rec["label"] == "greenwash"
        if gw and pred: tp += 1
        elif (not gw) and pred: fp += 1
        elif gw and (not pred): fn += 1
        else: tn += 1
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec_ = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec_ / (prec + rec_) if (prec + rec_) else 0.0
    return {"trigger": trigger, "rule_weight": rule_weight, "threshold": threshold,
            "axis_flag": axis_flag, "precision": round(prec, 3), "recall": round(rec_, 3),
            "f1": round(f1, 3), "tp": tp, "fp": fp, "fn": fn, "tn": tn}


GRID = {
    "trigger":     [0.15, 0.20, 0.25, 0.30, 0.35, 0.40],
    "rule_weight": [0.2, 0.3, 0.4, 0.5, 0.6],
    "threshold":   [0.15, 0.20, 0.25, 0.30, 0.35],
    "axis_flag":   [0.70, 0.80, 0.90],
}
BASELINE = {"trigger": 0.25, "rule_weight": 0.4, "threshold": 0.25, "axis_flag": 0.80}


def search(cache_path: Path = CACHE_PATH, top_n: int = 15) -> dict[str, Any]:
    cache = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    records = cache["records"]

    base = _f1(records, **BASELINE)
    results = [
        _f1(records, trigger=t, rule_weight=w, threshold=th, axis_flag=af)
        for t, w, th, af in itertools.product(
            GRID["trigger"], GRID["rule_weight"], GRID["threshold"], GRID["axis_flag"])
    ]
    # F1 우선, 동률이면 precision 우선(오탐 억제 목표)
    results.sort(key=lambda r: (r["f1"], r["precision"]), reverse=True)
    best = results[0]

    print(f"# 캘리브레이션 결과 (cases={cache['n_cases']}, LLM호출={cache.get('llm_calls')})\n")
    print("## 현재 기본값 (baseline)")
    print(f"  trig={base['trigger']} w={base['rule_weight']} thr={base['threshold']} "
          f"axf={base['axis_flag']}  →  F1={base['f1']} P={base['precision']} R={base['recall']} "
          f"(fp={base['fp']} fn={base['fn']})\n")
    print(f"## 상위 {top_n} 조합")
    print("  trig | w   | thr  | axf | F1    | P     | R     | fp fn")
    for r in results[:top_n]:
        print(f"  {r['trigger']:.2f} | {r['rule_weight']:.1f} | {r['threshold']:.2f} | "
              f"{r['axis_flag']:.2f}| {r['f1']:.3f} | {r['precision']:.3f} | {r['recall']:.3f} | "
              f"{r['fp']} {r['fn']}")
    gain = round(best["f1"] - base["f1"], 3)
    print(f"\n## 최적 → F1 {best['f1']} (baseline 대비 {'+' if gain>=0 else ''}{gain})")
    print(f"   JUDGE_TRIGGER={best['trigger']} JUDGE_RULE_WEIGHT={best['rule_weight']} "
          f"threshold={best['threshold']} axis_flag={best['axis_flag']}")

    # 최적 조합에서 틀린 케이스 — few-shot 설계용 오류 분석
    print("\n## 최적 조합 오답 (few-shot 설계용)")
    for rec in records:
        rv = _simulate_vector(rec, trigger=best["trigger"], rule_weight=best["rule_weight"])
        pred = _flagged(rv, best["threshold"], best["axis_flag"])
        gw = rec["label"] == "greenwash"
        if pred == gw:
            continue
        kind = "오탐FP" if (pred and not gw) else "미탐FN"
        verdicts = {n: rec["axes"][n].get("verdict") for n in _JUDGEABLE
                    if rec["axes"][n].get("verdict")}
        print(f"  [{kind}] {rec['id']} ({rec['category']}) risk={rv.risk_score:.3f} "
              f"verdicts={verdicts}")
    return {"baseline": base, "best": best, "top": results[:top_n]}


def _cli() -> None:
    p = argparse.ArgumentParser(description="ESGenie 검출 임계값 캘리브레이션")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("capture", help="실제 LLM 판정 1회 실측 → 캐시")
    s = sub.add_parser("search", help="캐시 위 오프라인 그리드 서치")
    s.add_argument("--top", type=int, default=15)
    args = p.parse_args()
    if args.cmd == "capture":
        capture()
    else:
        search(top_n=args.top)


if __name__ == "__main__":
    _cli()
