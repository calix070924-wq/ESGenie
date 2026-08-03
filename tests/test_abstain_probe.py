"""no_evidence 기권 통제 probe 하네스 — 회귀 가드.

scripts/abstain_probe_eval.py의 핵심 불변식을 고정한다:
  1) probe 셋의 no_evidence 케이스가 실제로 기권을 발동(기권수 > 0)한다.
  2) OFF/ON 예측(pred·y·correct)이 케이스별로 완전히 동일하다(with_abstain_ignored
     는 플래그만 무시 — determinism 보장).
  3) 기권 분해가 save+waste == 총기권이고 정밀도 산식이 맞다.
  4) 두 번 실행해도 수치가 동일(결정적).
"""
from __future__ import annotations

import importlib

import esgenie.layer3_detect as _layer3_detect
import esgenie.ssot.detector_5axis as _detector_5axis
from esgenie.evaluate import abstain_coverage, with_abstain_ignored

probe_mod = importlib.import_module("scripts.abstain_probe_eval")


def _run_rows():
    _layer3_detect.ABSTAIN_ENABLED = True
    _detector_5axis.ABSTAIN_ENABLED = True
    import json

    from esgenie.calibrate import BASELINE
    from esgenie.dart_client import load_report
    from esgenie.layer0_evidence_graph import build_evidence_graph
    try:
        probe = json.loads(probe_mod.PROBE_PATH.read_text(encoding="utf-8"))
        graph = build_evidence_graph(load_report(probe.get("ticker", "005930")))
        rows = []
        for case in probe["cases"]:
            rv = probe_mod._rv_with_omission(graph, case["sentence"], case.get("omit_codes") or [])
            rows.append(probe_mod._case_row(case, rv, BASELINE))
        return rows
    finally:
        _layer3_detect.ABSTAIN_ENABLED = False
        _detector_5axis.ABSTAIN_ENABLED = False


def test_probe_triggers_no_evidence_abstain():
    rows = _run_rows()
    on = abstain_coverage(rows)
    assert on["abstains"]["total"] > 0
    assert on["abstains"]["by_reason"]["no_evidence"] > 0


def test_off_on_predictions_identical():
    rows = _run_rows()
    off = with_abstain_ignored(rows)
    assert [r["pred"] for r in off] == [r["pred"] for r in rows]
    assert [r["y"] for r in off] == [r["y"] for r in rows]
    assert [r["correct"] for r in off] == [r["correct"] for r in rows]


def test_deferral_breakdown_consistency():
    rows = _run_rows()
    brk = probe_mod._deferral_breakdown(rows)
    assert brk["saves"] + brk["wastes"] == brk["deferred"]
    if brk["deferred"]:
        assert abs(brk["deferral_precision"] - brk["saves"] / brk["deferred"]) < 1e-9


def test_overall_never_increases_with_abstain():
    # 이 A/B 설계의 핵심 성질: Overall = 정답assessed/N 은 기권으로 오를 수 없다.
    rows = _run_rows()
    on = abstain_coverage(rows)
    off = abstain_coverage(with_abstain_ignored(rows))
    assert on["overall"] <= off["overall"] + 1e-9


def test_deterministic_across_runs():
    a = abstain_coverage(_run_rows())
    b = abstain_coverage(_run_rows())
    assert a == b


def test_classify_judge_call_flags_mock_contamination():
    """리뷰 #3(PR #52): ESGENIE_ABSTAIN_LIVE=1인데 LLM이 mock으로 폴백하면
    judge.used만 보는 구 로직은 이를 실호출로 잘못 집계했다. used_mock까지
    확인해야 한다."""
    assert probe_mod.classify_judge_call({"used": False}) == "skipped"
    assert probe_mod.classify_judge_call({"used": True, "used_mock": False}) == "used"
    assert probe_mod.classify_judge_call({"used": True, "used_mock": True}) == "mock_contaminated"
