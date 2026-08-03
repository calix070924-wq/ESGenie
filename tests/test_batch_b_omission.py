"""배치 B — 샘플/시나리오 리포트 미공시 평가셋 하네스 회귀 가드.

핵심 불변식:
  1) 각 케이스가 자기 회사 리포트의 실제 공백에서 no_evidence를 '주입 없이' 발동한다.
  2) 데이터 무결성 감사(expect 대비 실제)가 문제 0건.
  3) OFF/ON 예측 동일(with_abstain_ignored는 플래그만 무시).
  4) Overall 은 기권으로 오르지 않는다(단조 비증가).
  5) 결정적(두 번 실행 동일).
"""
from __future__ import annotations

import importlib
import json

import esgenie.layer3_detect as _layer3_detect
import esgenie.ssot.detector_5axis as _detector_5axis
from esgenie.calibrate import BASELINE
from esgenie.dart_client import load_report
from esgenie.evaluate import abstain_coverage, with_abstain_ignored
from esgenie.layer0_evidence_graph import build_evidence_graph
from esgenie.layer3_detect import detect_risk_vector

mod = importlib.import_module("scripts.batch_b_omission_eval")


def _run_rows():
    _layer3_detect.ABSTAIN_ENABLED = True
    _detector_5axis.ABSTAIN_ENABLED = True
    try:
        bench = json.loads(mod.BENCH_PATH.read_text(encoding="utf-8"))
        gc = {}
        rows = []
        for case in bench["cases"]:
            t = case["ticker"]
            if t not in gc:
                gc[t] = build_evidence_graph(load_report(t))
            rv = detect_risk_vector(case["sentence"], evidence_graph=gc[t])
            rows.append(mod._case_row(case, rv, BASELINE))
        return rows
    finally:
        _layer3_detect.ABSTAIN_ENABLED = False
        _detector_5axis.ABSTAIN_ENABLED = False


def test_real_no_evidence_fires_without_injection():
    rows = _run_rows()
    ne = [r for r in rows if r["d1_reason"] == "no_evidence"]
    assert len(ne) >= 10  # SME 실공백에서 다수 자연 발동
    on = abstain_coverage(rows)
    assert on["abstains"]["by_reason"]["no_evidence"] == len(
        [r for r in rows if r["abstained"] and "no_evidence" in r["abstain_reasons"]]
    )


def test_dataset_integrity_no_problems():
    rows = _run_rows()
    assert mod._integrity_check(rows) == []


def test_off_on_predictions_identical():
    rows = _run_rows()
    off = with_abstain_ignored(rows)
    assert [r["pred"] for r in off] == [r["pred"] for r in rows]
    assert [r["correct"] for r in off] == [r["correct"] for r in rows]


def test_overall_never_increases():
    rows = _run_rows()
    on = abstain_coverage(rows)
    off = abstain_coverage(with_abstain_ignored(rows))
    assert on["overall"] <= off["overall"] + 1e-9


def test_deferral_breakdown_consistency():
    brk = mod._deferral_breakdown(_run_rows())
    assert brk["saves"] + brk["wastes"] == brk["deferred"]


def test_deterministic():
    assert abstain_coverage(_run_rows()) == abstain_coverage(_run_rows())


def test_classify_judge_call_flags_mock_contamination():
    """리뷰 #3(PR #52): ESGENIE_ABSTAIN_LIVE=1인데 LLM이 mock으로 폴백하면
    judge.used만 보는 구 로직은 이를 실호출로 잘못 집계했다. used_mock까지
    확인해야 한다."""
    assert mod.classify_judge_call({"used": False}) == "skipped"
    assert mod.classify_judge_call({"used": True, "used_mock": False}) == "used"
    assert mod.classify_judge_call({"used": True, "used_mock": True}) == "mock_contaminated"
