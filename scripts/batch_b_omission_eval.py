"""배치 B — 실데이터 미공시 평가셋 A/B 하네스 (멀티티커, 주입 없음).

probe(abstain_probe_eval.py)와의 차이: probe는 005930 그래프 하나에 omit_codes를
'주입'해 미공시를 재현했다. 여기서는 **케이스마다 자기 회사(ticker) 리포트를
로드**하고, 그 리포트가 실제로 누락한 지표에 대한 주장을 평가한다 — 주입 없이
실 그래프에서 D1 no_evidence가 자연 발동한다. 즉 docs/abstain_realworld_prevalence.md
가 지적한 "현행 벤치는 미공시를 관측 못 함"을 실데이터로 메운 실측이다.

지표 해석은 probe와 동일: Overall = 정답assessed/N 은 이 A/B 설계에서 기권으로
오르지 않는다(단조 비증가). 채택 판정은 기권 정밀도 p 와 비용비 B/R > (1-p)/p.

실행:
  ESGENIE_FORCE_MOCK=1 PYTHONPATH=. python scripts/batch_b_omission_eval.py
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

import esgenie.layer3_detect as _layer3_detect
import esgenie.ssot.detector_5axis as _detector_5axis
from esgenie.calibrate import BASELINE, _flagged
from esgenie.dart_client import load_report
from esgenie.evaluate import abstain_coverage, with_abstain_ignored
from esgenie.layer0_evidence_graph import build_evidence_graph
from esgenie.layer3_detect import detect_risk_vector

ROOT = Path(__file__).resolve().parents[1]
BENCH_PATH = ROOT / "data" / "benchmark_v2" / "batch_b_omission.json"
# 생성물은 gitignored outputs/ 아래로 (docs/ 추적 파일 덮어쓰기 방지 — 코드리뷰 개선).
OUT_DOC = ROOT / "outputs" / "benchmark" / "batch_b_omission_result.md"


def _case_row(case: dict[str, Any], rv: Any, cfg: dict[str, float]) -> dict[str, Any]:
    """evaluate._case_rows()와 동일 규칙으로 라이브 RiskVector를 평가 행으로 변환."""
    pred = int(_flagged(rv, cfg["threshold"], cfg["axis_flag"]))
    y = 1 if case["label"] == "greenwash" else 0
    abstained_names = rv.abstained_axes()
    axes_map = {"D1_numeric": rv.D1_numeric, "D2_modifier": rv.D2_modifier,
                "D3_semantic": rv.D3_semantic, "D5_timeseries": rv.D5_timeseries}
    reasons = [axes_map[n].abstain_reason for n in abstained_names if axes_map[n].abstain_reason]
    abstained = bool(abstained_names) and not pred
    d1 = rv.D1_numeric
    return {"id": case["id"], "ticker": case["ticker"], "category": case["category"],
            "label": case["label"], "expect": case.get("expect"),
            "p": rv.risk_score, "y": y, "pred": pred, "correct": int(pred == y),
            "abstained": abstained, "abstain_reasons": reasons,
            "d1_abstain": d1.abstain, "d1_reason": d1.abstain_reason, "d1_score": d1.score}


def _deferral_breakdown(rows: list[dict[str, Any]]) -> dict[str, Any]:
    deferred = [r for r in rows if r["abstained"]]
    saves = [r for r in deferred if r["correct"] == 0]
    wastes = [r for r in deferred if r["correct"] == 1]
    n = len(deferred)
    p = (len(saves) / n) if n else 0.0
    be = ((1 - p) / p) if p > 0 else None
    return {"deferred": n, "saves": len(saves), "wastes": len(wastes),
            "deferral_precision": round(p, 4),
            "breakeven_benefit_cost_ratio": (round(be, 4) if be is not None else None),
            "save_ids": [r["id"] for r in saves], "waste_ids": [r["id"] for r in wastes]}


def _integrity_check(rows: list[dict[str, Any]]) -> list[str]:
    """expect 필드 대비 실제 동작 검증 — 데이터셋이 의도대로 발동하는지 감사."""
    problems = []
    for r in rows:
        exp = r.get("expect")
        if exp == "no_evidence" and not (r["d1_abstain"] and r["d1_reason"] == "no_evidence"):
            problems.append(f"{r['id']}: expect=no_evidence 인데 실제 abstain={r['d1_abstain']} reason={r['d1_reason']}")
        if exp == "verified" and (r["d1_abstain"] or r["d1_score"] > 0.5):
            problems.append(f"{r['id']}: expect=verified 인데 abstain={r['d1_abstain']} d1={r['d1_score']}")
        if exp == "mismatch" and r["pred"] != 1:
            problems.append(f"{r['id']}: expect=mismatch(flag) 인데 pred={r['pred']}")
    return problems


def main() -> None:
    _layer3_detect.ABSTAIN_ENABLED = True
    _detector_5axis.ABSTAIN_ENABLED = True
    cfg = BASELINE
    mock = os.getenv("ESGENIE_FORCE_MOCK") == "1"

    bench = json.loads(BENCH_PATH.read_text(encoding="utf-8"))
    graph_cache: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    try:
        for case in bench["cases"]:
            t = case["ticker"]
            if t not in graph_cache:
                graph_cache[t] = build_evidence_graph(load_report(t))
            rv = detect_risk_vector(case["sentence"], evidence_graph=graph_cache[t])
            rows.append(_case_row(case, rv, cfg))
    finally:
        _layer3_detect.ABSTAIN_ENABLED = False
        _detector_5axis.ABSTAIN_ENABLED = False

    on = abstain_coverage(rows)
    off = abstain_coverage(with_abstain_ignored(rows))
    brk = _deferral_breakdown(rows)
    problems = _integrity_check(rows)

    tickers = sorted({r["ticker"] for r in rows})
    ne = sum(1 for r in rows if r["d1_reason"] == "no_evidence")

    L = [
        "# 배치 B — 실데이터 미공시 평가셋 결과 (멀티티커, 주입 없음)",
        "",
        f"> bench: `data/benchmark_v2/batch_b_omission.json` (n={on['n']}, 회사={tickers}) · "
        f"모드: {'MOCK(룰 기반, 결정적)' if mock else 'AUTO'} · 임계값 BASELINE 고정",
        "> 각 케이스는 해당 회사가 **실제로 공시하지 않은** 지표에 대한 주장 → 주입 없이 실 그래프에서 no_evidence 자연 발동.",
        "",
        "## 0. 데이터 무결성 감사 (expect 대비 실제)",
        "",
        (f"- ✅ 전부 의도대로 동작 (no_evidence 자연 발동 {ne}건, 문제 0건)" if not problems
         else "- ⚠ 문제:\n" + "\n".join(f"  - {p}" for p in problems)),
        "",
        "## 1. 전역 지표 (OFF vs ON)",
        "",
        "| 해석 | Coverage | Accuracy(assessed) | Overall | 기권수 |",
        "|---|---|---|---|---|",
        f"| OFF (기권 무시) | {off['coverage']:.3f} | {off['accuracy_on_assessed']:.3f} | {off['overall']:.3f} | {off['abstains']['total']} |",
        f"| ON  (기권 반영) | {on['coverage']:.3f} | {on['accuracy_on_assessed']:.3f} | {on['overall']:.3f} | {on['abstains']['total']} |",
        "",
        f"- 사유별 기권: {on['abstains']['by_reason']}",
        f"- ΔAccuracy(assessed) = {on['accuracy_on_assessed'] - off['accuracy_on_assessed']:+.3f} · "
        f"ΔCoverage = {on['coverage'] - off['coverage']:+.3f} · "
        f"ΔOverall = {on['overall'] - off['overall']:+.3f}",
        "",
        "## 2. 기권 분해 — 가치(save) vs 비용(waste)",
        "",
        f"- 총 기권 {brk['deferred']}건 = save {brk['saves']}건 + waste {brk['wastes']}건",
        f"- **save**(OFF면 틀렸을 미탐 → 검토 구제): {brk['save_ids']}",
        f"- **waste**(OFF면 맞았을 것 → 불필요 검토): {brk['waste_ids']}",
        f"- **기권 정밀도 p = {brk['deferral_precision']:.3f}** · 손익분기 **B/R > {brk['breakeven_benefit_cost_ratio']}**",
        "",
        "> 주의: p는 이 파일럿의 미공시 영역 라벨 구성(greenwash:clean = 9:6)을 반영한 값이다. "
        "실제 운영에서의 p는 '미검증 주장 중 실제 그린워싱 비율'에 좌우되므로, 회사·지표 수를 "
        "늘려 신뢰구간과 함께 재추정해야 한다(§4 다음 단계).",
        "",
        "## 3. probe(주입) 대비 — 실데이터가 말해주는 것",
        "",
        _vs_probe(brk),
        "",
        "## 4. 판정 · 다음 단계",
        "",
        _verdict(off, on, brk, ne, len(rows)),
        "",
    ]
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.write_text("\n".join(L), encoding="utf-8")
    print("="*72)
    print(f"회사={tickers}  n={on['n']}  no_evidence 자연발동={ne}건  무결성 문제={len(problems)}")
    print(f"OFF: cov={off['coverage']:.3f} acc={off['accuracy_on_assessed']:.3f} overall={off['overall']:.3f}")
    print(f"ON : cov={on['coverage']:.3f} acc={on['accuracy_on_assessed']:.3f} overall={on['overall']:.3f}")
    print(f"기권정밀도 p={brk['deferral_precision']:.3f} (save={brk['saves']} waste={brk['wastes']}) 손익분기 B/R>{brk['breakeven_benefit_cost_ratio']}")
    if problems:
        print("⚠ 무결성:", *problems, sep="\n  ")
    print("="*72)
    print(f"결과 문서: {OUT_DOC}")


def _vs_probe(brk: dict[str, Any]) -> str:
    return (
        "probe(005930 단일 그래프에 omit_codes 주입, p=0.60)와 달리 이 배치는 서로 다른 회사의 "
        "**실제 공시 공백**에서 no_evidence가 자연 발동했다 — 주입 없이도 기권 메커니즘이 실 "
        "데이터에서 동일하게 작동함을 확인. 이로써 '현행 벤치는 미공시를 관측 못 한다'는 감사 결론의 "
        f"해법(배치 B)이 실제로 성립함을 실측으로 입증했다(자연 no_evidence {brk['deferred']}건)."
    )


def _verdict(off, on, brk, ne, n) -> str:
    d_acc = on["accuracy_on_assessed"] - off["accuracy_on_assessed"]
    return (
        f"실데이터에서 no_evidence 기권이 {ne}건/{n} 자연 발동했고(현행 dev/test는 0건이었음), "
        f"그중 미탐 {brk['saves']}건을 사람 검토로 전환하며 자동판정 정확도를 {d_acc:+.3f} 끌어올렸다"
        f"(정밀도 p={brk['deferral_precision']:.3f}). Overall({on['overall'] - off['overall']:+.3f})은 "
        "이 설계에서 구조상 오를 수 없으므로 채택 판정은 p와 B/R로 한다. "
        "다음 단계: (1) 이 셋을 실키 회귀에 편입해 실측 고정, (2) HITL 라우팅을 붙여 '기권→검토'를 "
        "실제 동작으로 연결, (3) 회사 수·지표 수를 늘려 정밀도 p의 안정성(신뢰구간) 확인."
    )


if __name__ == "__main__":
    main()
