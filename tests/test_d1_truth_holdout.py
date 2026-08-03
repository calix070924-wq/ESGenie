"""D1 진위(truth) holdout 회귀 가드 (PR #52 리뷰 Fix C-2).

no_evidence 기권의 정밀도 p는 content-blind라 성능 지표가 될 수 없다
(docs/abstain_wrapup_conclusion.md §1-c). 이 홀드아웃은 근거가 있는(no_evidence가
아닌) 케이스만으로 구성해, D1 수치검증 자체의 정확도를 라벨-독립적으로(실제 공시값
대비 진위로) 측정한다. 기권 트랙과는 무관 — 노드를 숨기지 않는다.
"""
from __future__ import annotations

import importlib
import json

from esgenie.dart_client import load_report
from esgenie.layer0_evidence_graph import build_evidence_graph
from esgenie.layer3_detect import detect_risk_vector

mod = importlib.import_module("scripts.d1_truth_eval")


def _run_rows():
    holdout = json.loads(mod.HOLDOUT_PATH.read_text(encoding="utf-8"))
    graph_cache = {}
    rows = []
    for case in holdout["cases"]:
        t = case["ticker"]
        if t not in graph_cache:
            graph_cache[t] = build_evidence_graph(load_report(t))
        graph = graph_cache[t]
        assert graph.search_nodes(keywords=[case["code"]]), (
            f"{case['id']}: 근거 present 전제 위반 — {t}의 {case['code']} 노드 없음"
        )
        rv = detect_risk_vector(case["sentence"], evidence_graph=graph)
        assert rv.D1_numeric.detail != "수치 매칭 없음", (
            f"{case['id']}: 문장이 {case['code']} 토픽으로 매칭되지 않음"
        )
        y = 1 if case["label"] == "greenwash" else 0
        pred = int(rv.D1_numeric.score >= mod.FLAG_THRESHOLD)
        rows.append({"id": case["id"], "y": y, "pred": pred})
    return rows


def test_all_cases_have_evidence_and_match_topic():
    """이 홀드아웃의 설계 전제(근거 present + 토픽 매칭 성공) 자체가 깨지지 않는지 감사."""
    rows = _run_rows()
    assert len(rows) == 24


def test_blatant_false_claim_is_flagged():
    """리뷰 예시 그대로: 재활용률 98%(claim) vs 실제 86.3% → D1이 flag해야 한다."""
    rows = {r["id"]: r for r in _run_rows()}
    assert rows["C-02"]["y"] == 1
    assert rows["C-02"]["pred"] == 1


def test_clean_claims_are_not_flagged():
    """실제 공시값과 거의 일치하는(오차 <1%) clean 케이스는 flag되면 안 된다."""
    rows = {r["id"]: r for r in _run_rows()}
    for cid in ("C-01", "C-05", "C-09", "C-15"):
        assert rows[cid]["pred"] == 0, f"{cid}: clean인데 flag됨"


def test_f1_above_floor():
    """D1 자체의 실성능(라벨-독립) — 최소 F1 바닥선 회귀 가드."""
    rows = _run_rows()
    tp = sum(1 for r in rows if r["y"] == 1 and r["pred"] == 1)
    fp = sum(1 for r in rows if r["y"] == 0 and r["pred"] == 1)
    fn = sum(1 for r in rows if r["y"] == 1 and r["pred"] == 0)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    assert f1 >= 0.8
