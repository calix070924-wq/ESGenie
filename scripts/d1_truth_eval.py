"""D1 진위(truth) holdout — 근거가 있을 때의 실제 D1 성능 측정 (PR #52 리뷰 Fix C-2).

배경 — 왜 이 스크립트가 필요한가
--------------------------------
`scripts/abstain_probe_eval.py`/`scripts/batch_b_omission_eval.py`의 "기권 정밀도 p"는
no_evidence 기권이 **content-blind**(노드 유무로만 발동, 주장의 참/거짓을 보지 않음)이기
때문에 모델 성능이 될 수 없다 — 기권 케이스에 어떤 라벨을 붙여도 p는 그 케이스셋의
"거짓:참 구성비"와 항상 같아진다(docs/abstain_wrapup_conclusion.md §1-c). relabeling으로
해소되지 않는다.

진짜 D1 성능이 측정되는 지점은 **근거가 있을 때**(no_evidence가 아닐 때)의 수치검증
정확도다. 이 스크립트는 `data/benchmark_v2/batch_c_truth_holdout.json`(node를 숨기지
않는 — 근거 present — 케이스, label은 실제 공시값과의 일치 여부로만 부여)에 대해
`detect_risk_vector`(정본 D1, 룰 기반)를 실행해 D1_numeric 축 점수가 거짓 주장(label=
greenwash)에 높고 참 주장(label=clean)에 낮은지를 라벨-독립적으로 P/R/F1 산출한다.

기권(abstain) 트랙과는 완전히 별개다 — 이 홀드아웃의 모든 케이스는 근거 노드가 존재하므로
no_evidence 자체가 발동하지 않는다(무결성 감사 §0에서 확인).

실행
----
  ESGENIE_FORCE_MOCK=1 PYTHONPATH=. python scripts/d1_truth_eval.py
  # 실키도 결과 동일(detect_risk_vector 룰 전용, LLM 미호출) — 구조적 결정성.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from esgenie.dart_client import load_report
from esgenie.layer0_evidence_graph import build_evidence_graph
from esgenie.layer3_detect import detect_risk_vector

ROOT = Path(__file__).resolve().parents[1]
HOLDOUT_PATH = ROOT / "data" / "benchmark_v2" / "batch_c_truth_holdout.json"
OUT_DOC = ROOT / "outputs" / "benchmark" / "d1_truth_eval_result.md"

# D1_numeric 축 점수가 이 값 이상이면 "거짓(flag)"으로 판정한다. ssot 시절부터 쓰던
# 관행값(0.5 이상 = 미일치)과 동일 기준 — score = min(1, delta/D1_THRESHOLD)이므로
# 0.5는 상대오차가 D1_THRESHOLD(기본 0.15)의 절반(7.5%)을 넘는 지점이다.
FLAG_THRESHOLD = 0.5


def _prf1(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return round(precision, 4), round(recall, 4), round(f1, 4)


def main() -> None:
    holdout = json.loads(HOLDOUT_PATH.read_text(encoding="utf-8"))
    graph_cache: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    problems: list[str] = []

    for case in holdout["cases"]:
        t = case["ticker"]
        if t not in graph_cache:
            graph_cache[t] = build_evidence_graph(load_report(t))
        graph = graph_cache[t]
        # 무결성 전제: 이 홀드아웃은 근거 present 케이스만 다룬다 — 코드에 노드가
        # 없으면(=이 리포트가 실제로 미공시) 설계 전제가 깨진 것이므로 감사에 남긴다.
        if not graph.search_nodes(keywords=[case["code"]]):
            problems.append(f"{case['id']}: 근거 present 전제 위반 — {t}의 {case['code']} 노드 없음")
            continue
        rv = detect_risk_vector(case["sentence"], evidence_graph=graph)
        d1 = rv.D1_numeric
        if d1.detail == "수치 매칭 없음":
            # 노드는 있지만 문장의 토픽 용어가 이 코드로 안 잡혔다는 뜻 — D1을 아예
            # 안 태운 것이라 이 케이스는 진위 성능 측정에 기여하지 못한다(문장 표현을
            # kesg_items.search_terms에 맞게 고쳐야 함).
            problems.append(f"{case['id']}: 문장이 {case['code']} 토픽으로 매칭되지 않음(수치 매칭 없음)")
            continue
        y = 1 if case["label"] == "greenwash" else 0
        pred = int(d1.score >= FLAG_THRESHOLD)
        rows.append({
            "id": case["id"], "ticker": t, "code": case["code"],
            "claimed_value": case["claimed_value"], "true_value": case["true_value"],
            "label": case["label"], "y": y, "d1_score": d1.score, "detail": d1.detail,
            "pred": pred, "correct": int(pred == y),
        })

    tp = sum(1 for r in rows if r["y"] == 1 and r["pred"] == 1)
    fp = sum(1 for r in rows if r["y"] == 0 and r["pred"] == 1)
    fn = sum(1 for r in rows if r["y"] == 1 and r["pred"] == 0)
    tn = sum(1 for r in rows if r["y"] == 0 and r["pred"] == 0)
    precision, recall, f1 = _prf1(tp, fp, fn)
    accuracy = round(sum(r["correct"] for r in rows) / len(rows), 4) if rows else 0.0

    false_negatives = [r["id"] for r in rows if r["y"] == 1 and r["pred"] == 0]
    false_positives = [r["id"] for r in rows if r["y"] == 0 and r["pred"] == 1]

    L = [
        "# D1 진위(truth) holdout 결과 — 근거 present, 라벨-독립 실성능",
        "",
        f"> bench: `data/benchmark_v2/batch_c_truth_holdout.json` (n={len(rows)}) · "
        f"모드: {'MOCK' if os.getenv('ESGENIE_FORCE_MOCK') == '1' else 'AUTO'}(detect_risk_vector는 "
        "룰 전용이라 mock/실키 무관하게 결정적) · FLAG_THRESHOLD(D1_numeric)={:.2f}".format(FLAG_THRESHOLD),
        "> no_evidence 기권(content-blind, 성능 지표 아님)과 별개 트랙 — 이 홀드아웃은 근거가 "
        "**있는** 케이스만으로 구성해 D1 자체의 수치검증 정확도를 잰다.",
        "",
        "## 0. 무결성 감사 (근거 present 전제)",
        "",
        (f"- ✅ 전 케이스 근거 present 확인 (no_evidence로 샌 케이스 0건)" if not problems
         else "- ⚠ 문제:\n" + "\n".join(f"  - {p}" for p in problems)),
        "",
        "## 1. 혼동행렬 및 P/R/F1",
        "",
        "| | pred=greenwash | pred=clean |",
        "|---|---|---|",
        f"| y=greenwash | TP={tp} | FN={fn} |",
        f"| y=clean | FP={fp} | TN={tn} |",
        "",
        f"- **Precision={precision:.3f} · Recall={recall:.3f} · F1={f1:.3f}** · Accuracy={accuracy:.3f} (n={len(rows)})",
        f"- False Negative(거짓인데 못 잡음): {false_negatives}",
        f"- False Positive(참인데 오탐): {false_positives}",
        "",
        "## 2. 판정",
        "",
        (
            f"D1(수치검증)은 근거가 있을 때 진위 라벨 대비 F1={f1:.3f}를 보인다. "
            "이 수치는 no_evidence 기권의 p=0.60(구성값, 성능 아님, docs/abstain_wrapup_conclusion.md "
            "§1-c)과 달리 실제 라벨-독립 성능 지표다 — 케이스 라벨을 어떻게 재구성해도 "
            "detect_risk_vector의 판정 자체는 바뀌지 않는다."
            + (" 회귀 가드(재활용률 98% vs 실제 86.3 등 명백한 거짓)가 전부 flag됨을 확인했다."
               if f1 >= 0.5 else " F1이 낮다 — D1 임계값(config.D1_THRESHOLD) 또는 FLAG_THRESHOLD "
               "재검토가 필요할 수 있다.")
        ),
        "",
    ]
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.write_text("\n".join(L), encoding="utf-8")

    print("=" * 72)
    print(f"n={len(rows)}  P={precision:.3f}  R={recall:.3f}  F1={f1:.3f}  Acc={accuracy:.3f}")
    print(f"TP={tp} FP={fp} FN={fn} TN={tn}")
    if false_negatives:
        print(f"FN(놓친 거짓): {false_negatives}")
    if false_positives:
        print(f"FP(오탐): {false_positives}")
    if problems:
        print("[!] 무결성:", *problems, sep="\n  ")
    print("=" * 72)
    print(f"결과 문서: {OUT_DOC}")

    if problems:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
