"""D2 분모/문맥면제 변경이 벤치 F1에 주는 영향을 오프라인으로 측정.

원리
----
D2는 **문장만의 순수 함수**다(evidence_graph·RAG 불필요). 따라서 judge_cache에
저장된 D1/D3/D5 룰 점수는 그대로 두고 **D2 룰 점수만 재계산**하면, LLM을 다시
부르지 않고도 임의의 D2 정의에서 F1을 계산할 수 있다. calibrate.py의 오프라인
스윕과 같은 가정(판정 verdict는 co-triggered 축과 무관하게 안정)을 쓴다.

⚠ 가정의 한계: verdict는 **당시 rule_score가 프롬프트에 박힌 상태**로 실측됐다.
D2 룰 점수를 바꾸면 실키 재측정 시 verdict가 달라질 수 있다. 여기 수치는
"판정 캐시 고정 하에서의 D2 정의 변경 효과"이며 실키 재측정을 대체하지 않는다.

실행:
  python3 scripts/measure_d2_impact.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # repo 루트에서 `esgenie` 패키지 import (scripts/ 컨벤션)

from esgenie.calibrate import BASELINE, _flagged, _simulate_vector  # noqa: E402
from esgenie.config import D2_THRESHOLD  # noqa: E402
from esgenie.knowledge.greenwash_lexicon import vague_matches  # noqa: E402

SPLIT_DIR = ROOT / "data" / "benchmark_v2"
CACHE_DIR = ROOT / "outputs" / "benchmark"


# ---- D2 정의 변종 ----------------------------------------------------------

def d2_current(sentence: str) -> float:
    """현행: density = hits / max(D2_THRESHOLD*4, 1) → 분모 1.0 (히트 1개면 만점)."""
    hits = vague_matches(sentence)
    return round(min(1.0, len(hits) / max(D2_THRESHOLD * 4, 1)), 4)


def d2_denom4(sentence: str) -> float:
    """주석 의도대로: 4개 = 만점 (히트 1개 → 0.25)."""
    hits = vague_matches(sentence)
    return round(min(1.0, len(hits) / 4.0), 4)


def d2_denom2(sentence: str) -> float:
    """중간안: 2개 = 만점 (히트 1개 → 0.5)."""
    hits = vague_matches(sentence)
    return round(min(1.0, len(hits) / 2.0), 4)


def d2_exempt(sentence: str) -> float:
    """문맥 면제((나)(다)) 적용 + 현행 분모 유지 — 작업 1 단독 효과."""
    from esgenie.knowledge.greenwash_lexicon import vague_matches_filtered
    hits = vague_matches_filtered(sentence)
    return round(min(1.0, len(hits) / max(D2_THRESHOLD * 4, 1)), 4)


VARIANTS: dict[str, Callable[[str], float]] = {
    "현행 (분모 1.0)": d2_current,
    "분모 2 (2개 만점)": d2_denom2,
    "분모 4 (주석 의도)": d2_denom4,
}


# ---- 평가 ------------------------------------------------------------------

def _prf(rows: list[tuple[int, int]]) -> dict[str, Any]:
    tp = sum(1 for y, p in rows if y == 1 and p == 1)
    fp = sum(1 for y, p in rows if y == 0 and p == 1)
    fn = sum(1 for y, p in rows if y == 1 and p == 0)
    tn = sum(1 for y, p in rows if y == 0 and p == 0)
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return {"precision": round(prec, 3), "recall": round(rec, 3), "f1": round(f1, 3),
            "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def eval_variant(records: list[dict[str, Any]], sent_by_id: dict[str, str],
                 fn: Callable[[str], float], cfg: dict[str, float]) -> dict[str, Any]:
    """D2만 fn으로 갈아끼운 뒤 BASELINE 임계값으로 F1 계산."""
    rows: list[tuple[int, int]] = []
    for rec in records:
        patched = json.loads(json.dumps(rec))   # 원본 캐시 불변
        patched["axes"]["D2_modifier"]["rule_score"] = fn(sent_by_id[rec["id"]])
        rv = _simulate_vector(patched, trigger=cfg["trigger"], rule_weight=cfg["rule_weight"])
        pred = _flagged(rv, cfg["threshold"], cfg["axis_flag"])
        rows.append((1 if rec["label"] == "greenwash" else 0, int(pred)))
    return _prf(rows)


def d2_axis_only(cases: list[dict[str, Any]], fn: Callable[[str], float],
                 flag_at: float) -> dict[str, Any]:
    """LLM 없이 D2 축 단독 판정 — 전체 케이스(캐시 없는 것 포함)에서 계산 가능."""
    rows = [(1 if c["label"] == "greenwash" else 0, int(fn(c["sentence"]) >= flag_at))
            for c in cases]
    return _prf(rows)


# ---- 룰 단독 (결정적, LLM 불필요, 전건) --------------------------------------
# detect_risk_vector는 LLM을 부르지 않는다 → 벤치 전건(n=270)을 캐시 없이 재현할 수
# 있다. README의 "룰 단독 (1차)" 검출기와 같은 경로이므로 이 수치가 D2 정의 변경의
# 가장 신뢰할 수 있는 벤치 지표다(하이브리드는 판정 캐시 가정에 묶여 있다).

def rule_only_axes(cases: list[dict[str, Any]], ticker: str) -> dict[str, dict[str, float]]:
    """케이스별 D1/D3/D5 룰 점수를 1회 계산해 캐시(D2는 변종마다 따로 계산)."""
    from esgenie.dart_client import load_report
    from esgenie.layer0_evidence_graph import build_evidence_graph
    from esgenie.layer3_detect import detect_risk_vector

    graph = build_evidence_graph(load_report(ticker))
    out: dict[str, dict[str, float]] = {}
    for c in cases:
        rv = detect_risk_vector(c["sentence"], evidence_graph=graph)
        out[c["id"]] = {"D1": rv.D1_numeric.score, "D3": rv.D3_semantic.score,
                        "D5": rv.D5_timeseries.score}
    return out


def eval_rule_only(cases: list[dict[str, Any]], axes: dict[str, dict[str, float]],
                   fn: Callable[[str], float], cfg: dict[str, float]) -> dict[str, Any]:
    from esgenie.config import D_WEIGHTS
    rows: list[tuple[int, int]] = []
    for c in cases:
        a = axes[c["id"]]
        d2 = fn(c["sentence"])
        risk = (D_WEIGHTS["D1_numeric"] * a["D1"] + D_WEIGHTS["D2_modifier"] * d2
                + D_WEIGHTS["D3_semantic"] * a["D3"] + D_WEIGHTS["D5_timeseries"] * a["D5"])
        pred = risk >= cfg["threshold"] or max(a["D1"], d2, a["D5"]) >= cfg["axis_flag"]
        rows.append((1 if c["label"] == "greenwash" else 0, int(pred)))
    return _prf(rows)


def _table(out: list[str], rows: list[tuple[str, dict[str, Any]]]) -> None:
    out.append("| D2 정의 | Precision | Recall | F1 | TP | FP | FN | TN |")
    out.append("|---|---|---|---|---|---|---|---|")
    for label, m in rows:
        out.append(f"| {label} | {m['precision']:.3f} | {m['recall']:.3f} | "
                   f"**{m['f1']:.3f}** | {m['tp']} | {m['fp']} | {m['fn']} | {m['tn']} |")
    out.append("")


def main() -> None:
    cfg = BASELINE
    out: list[str] = ["# D2 정의 변경의 벤치 영향", ""]
    out.append(f"- 임계값 고정: trig={cfg['trigger']} w={cfg['rule_weight']} "
               f"thr={cfg['threshold']} axf={cfg['axis_flag']} (재튜닝 안 함)")
    out.append(f"- `D2_THRESHOLD={D2_THRESHOLD}` → 현행 분모 = "
               f"`max({D2_THRESHOLD}*4, 1)` = **{max(D2_THRESHOLD * 4, 1)}**")
    out.append("")

    for sp in ("dev", "test"):
        bench = json.loads((SPLIT_DIR / f"{sp}.json").read_text(encoding="utf-8"))
        cases = bench["cases"]
        sent_by_id = {c["id"]: c["sentence"] for c in cases}
        n_pos = sum(1 for c in cases if c["label"] == "greenwash")

        # (1) 룰 단독 — 결정적·전건. D2 변경의 1차 지표.
        out.append(f"## {sp.upper()} — 룰 단독 (결정적, 전건 n={len(cases)}, 양성 {n_pos})")
        out.append("`detect_risk_vector`는 LLM을 부르지 않는다 → 캐시 없이 전건 재현. "
                   "README '룰 단독 (1차)' 검출기와 같은 경로.")
        out.append("")
        axes = rule_only_axes(cases, bench.get("ticker", "005930"))
        _table(out, [(lb, eval_rule_only(cases, axes, fn, cfg)) for lb, fn in VARIANTS.items()])

        # (2) 하이브리드 — 판정 캐시가 있는 부분집합만.
        cache_path = CACHE_DIR / f"{sp}_judge_cache.json"
        if not cache_path.exists():
            out.append(f"## {sp.upper()} — 하이브리드: 판정 캐시 없음, 스킵")
            out.append("")
            continue
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        records = [r for r in cache["records"] if r["id"] in sent_by_id]
        c_pos = sum(1 for r in records if r["label"] == "greenwash")

        out.append(f"## {sp.upper()} — 하이브리드 (판정 캐시 n={len(records)}, 양성 {c_pos})")
        if len(records) < len(cases):
            out.append(f"> ⚠ 벤치 {len(cases)}건 중 캐시 보유 **{len(records)}건**만 측정. "
                       f"미보유 {len(cases) - len(records)}건은 실키 capture 필요(사용자 몫).")
        out.append("> ⚠ verdict는 **당시 D2 rule_score가 프롬프트에 박힌 상태**로 실측됐다. "
                   "D2를 바꾸면 실키에서 verdict가 달라질 수 있어, 이 표는 근사다.")
        out.append("")
        _table(out, [(lb, eval_variant(records, sent_by_id, fn, cfg)) for lb, fn in VARIANTS.items()])

    text = "\n".join(out)
    print(text)
    dest = CACHE_DIR / "d2_impact.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text + "\n", encoding="utf-8")
    print(f"\n✅ 저장: {dest}")


if __name__ == "__main__":
    main()
