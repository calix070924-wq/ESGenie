"""배치 B — 샘플/시나리오 리포트 미공시 평가셋 A/B 하네스 (멀티티커, 주입 없음).

2026-08-03 리뷰 반영(허정만, PR #52): SME001/SME002는 실제 DART 공시가 아니라
data/sample_dart의 합성 파일럿 리포트다(`.source`="사업보고서 2024 (현대차/삼성전자
협력사 시나리오)"). "실데이터"라는 표현은 이 사실을 오도하므로 정정한다 — 다만
"주입/조작 없이 해당 리포트가 실제로 누락한 코드에서 기권이 자연 발동한다"는
사실 자체는 그대로 유지된다(아래 참조).

probe(abstain_probe_eval.py)와의 차이: probe는 005930 그래프 하나에 omit_codes를
'주입'해 미공시를 재현했다. 여기서는 **케이스마다 자기 회사(ticker) 리포트를
로드**하고, 그 리포트가 실제로 누락한 지표에 대한 주장을 평가한다 — 주입 없이
그래프에서 D1 no_evidence가 자연 발동한다. 즉 docs/abstain_realworld_prevalence.md
가 지적한 "현행 벤치는 미공시를 관측 못 함"을 이 샘플 리포트 실측으로 메운다.

지표 해석은 probe와 동일: Overall = 정답assessed/N 은 이 A/B 설계에서 기권으로
오르지 않는다(단조 비증가). 채택 판정은 기권 정밀도 p 와 비용비 B/R > (1-p)/p.

실행:
  ESGENIE_FORCE_MOCK=1 PYTHONPATH=. python scripts/batch_b_omission_eval.py
  # 실키도 결과 동일(detect_risk_vector 룰 전용, LLM 미호출) — 구조적 결정성.

라이브(룰+LLM) 모드:
  ESGENIE_ABSTAIN_LIVE=1 ESGENIE_STRICT=1 PYTHONPATH=. python scripts/batch_b_omission_eval.py
케이스마다 룰(detect_risk_vector)과 하이브리드(detect_risk_vector_hybrid, 실
LLM 호출)를 둘 다 실행해 abstain 결정이 LLM 단계에서 보존되는지 검증한다.
전 축 룰점수가 JUDGE_TRIGGER 미만이면 judge 자체가 트리거 안 되므로 abstain
케이스는 구조적으로 LLM이 안 불린다 — control_mismatch(값 불일치 대조군)만
실 호출된다. rule↔hybrid 판정이 하나라도 갈리면 비정상 종료(exit 1)한다.
비-라이브(기본) 경로는 이 플래그와 무관하게 기존과 완전히 동일하다.
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
from esgenie.layer3_judge import detect_risk_vector_hybrid

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


def classify_judge_call(judge: dict[str, Any]) -> str:
    """RiskVector.aggregate['judge'] 메타를 분류 (리뷰 #3, PR #52).

    `judge.get("used")`만 보면 ESGENIE_ABSTAIN_LIVE=1 + ESGENIE_FORCE_MOCK=1 조합에서도
    "LLM 호출 성공"으로 잘못 집계된다 — LLMClient가 mock으로 조용히 폴백해도
    judge_risk_vector 입장에선 "judge를 돌렸다(used=True)"이기 때문. used_mock까지
    같이 봐야 실제로 실 LLM을 탄 건지 구분된다.

    Returns: "skipped"(JUDGE_TRIGGER 미만이라 애초에 호출 안 됨) |
             "used"(실 LLM 호출) | "mock_contaminated"(호출은 됐으나 mock 폴백)
    """
    if not judge.get("used"):
        return "skipped"
    return "mock_contaminated" if judge.get("used_mock") else "used"


def main() -> None:
    _layer3_detect.ABSTAIN_ENABLED = True
    _detector_5axis.ABSTAIN_ENABLED = True
    cfg = BASELINE
    mock = os.getenv("ESGENIE_FORCE_MOCK") == "1"
    live = os.getenv("ESGENIE_ABSTAIN_LIVE") == "1"

    bench = json.loads(BENCH_PATH.read_text(encoding="utf-8"))
    graph_cache: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    llm_used = 0
    mismatches: list[str] = []
    mock_contaminated: list[str] = []   # judge.used=True인데 judge.used_mock=True(리뷰 #3)
    try:
        for case in bench["cases"]:
            t = case["ticker"]
            if t not in graph_cache:
                graph_cache[t] = build_evidence_graph(load_report(t))
            graph = graph_cache[t]
            if live:
                rv_rule = detect_risk_vector(case["sentence"], evidence_graph=graph)
                rv = detect_risk_vector_hybrid(case["sentence"], evidence_graph=graph)
                verdict = classify_judge_call(rv.aggregate.get("judge", {}))
                if verdict == "used":
                    llm_used += 1
                elif verdict == "mock_contaminated":
                    mock_contaminated.append(case["id"])
                row_rule = _case_row(case, rv_rule, cfg)
                row = _case_row(case, rv, cfg)
                if (row_rule["abstained"], row_rule["pred"], rv_rule.abstained_axes()) \
                        != (row["abstained"], row["pred"], rv.abstained_axes()):
                    mismatches.append(
                        f"{case['id']}: rule(pred={row_rule['pred']}, abstained={row_rule['abstained']}, "
                        f"axes={rv_rule.abstained_axes()}) != hybrid(pred={row['pred']}, "
                        f"abstained={row['abstained']}, axes={rv.abstained_axes()})"
                    )
            else:
                rv = detect_risk_vector(case["sentence"], evidence_graph=graph)
                row = _case_row(case, rv, cfg)
            rows.append(row)
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
        "# 배치 B — 샘플/시나리오 리포트 미공시 평가셋 결과 (멀티티커, 주입 없음)",
        "",
        f"> bench: `data/benchmark_v2/batch_b_omission.json` (n={on['n']}, 회사={tickers}) · "
        f"모드: {'LIVE(룰+LLM 하이브리드)' if live else ('MOCK(룰 기반, 결정적)' if mock else 'AUTO')} · 임계값 BASELINE 고정",
        "> SME001/SME002는 실제 DART 공시가 아니라 합성 파일럿 리포트(시나리오)다 — "
        "각 케이스는 해당 리포트가 **실제로 공시하지 않은** 지표에 대한 주장 → 주입 없이 "
        "그 그래프에서 no_evidence 자연 발동.",
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
        "> **p는 성능 지표가 아니다**(2026-08-03 리뷰 반영, 허정만): no_evidence 기권은 "
        "content-blind다 — 노드 유무로만 발동하고 주장의 참/거짓을 보지 않는다. 따라서 "
        "기권 케이스에 어떤 라벨을 붙여도 abstain은 참·거짓을 동일하게 전부 기권하므로, "
        "p는 구조적으로 이 파일럿의 라벨 구성비(greenwash:clean = 9:6)와 같아진다 — "
        "relabeling으로 해소되지 않는다. 근거가 있을 때의 실제 D1 성능은 진위 holdout"
        "(`data/benchmark_v2/batch_c_truth_holdout.json`, `scripts/d1_truth_eval.py`)으로 "
        "별도 측정한다.",
        "",
        "## 3. probe(주입) 대비 — 이 샘플 리포트가 말해주는 것",
        "",
        _vs_probe(brk),
        "",
        "## 4. 판정 · 다음 단계",
        "",
        _verdict(off, on, brk, ne, len(rows)),
        "",
    ]
    if live:
        L += [
            "## 5. 라이브(룰+LLM) 검증",
            "",
            f"- LLM 호출: {llm_used}/{len(rows)}건 "
            "(전 축 룰점수 < JUDGE_TRIGGER면 judge 자체가 트리거 안 됨 — "
            "abstain 케이스는 구조적으로 미호출, control_mismatch만 실 호출 기대)",
            f"- rule↔hybrid abstain 판정 불일치: {len(mismatches)}건"
            + ("" if not mismatches else "\n" + "\n".join(f"  - {m}" for m in mismatches)),
            f"- MOCK 오염(judge.used=True인데 used_mock=True): {len(mock_contaminated)}건"
            + ("" if not mock_contaminated else " " + str(mock_contaminated)
               + " — ESGENIE_ABSTAIN_LIVE=1인데 실 LLM 대신 mock으로 폴백함(키 누락/오류 가능). "
                 "이 실행은 라이브 검증으로 인정하지 않는다."),
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
    if live:
        print(f"LIVE: LLM 호출 {llm_used}/{len(rows)}건, rule↔hybrid 불일치 {len(mismatches)}건, "
              f"MOCK 오염 {len(mock_contaminated)}건")
        for m in mismatches:
            print(f"  ⚠ {m}")
        for c in mock_contaminated:
            print(f"  [!] MOCK-CONTAMINATED: {c}")
    print("="*72)
    print(f"결과 문서: {OUT_DOC}")

    if live and (mismatches or mock_contaminated):
        raise SystemExit(1)


def _vs_probe(brk: dict[str, Any]) -> str:
    return (
        "probe(005930 단일 그래프에 omit_codes 주입)와 달리 이 배치는 서로 다른 회사(샘플/시나리오 "
        "리포트)의 **실제 공시 공백**에서 no_evidence가 자연 발동했다 — 주입 없이도 기권 메커니즘이 "
        "동일하게 작동함을 확인. 이로써 '현행 벤치는 미공시를 관측 못 한다'는 감사 결론의 "
        f"해법(배치 B)이 실제로 성립함을 실측으로 입증했다(자연 no_evidence {brk['deferred']}건). "
        "(p 수치 자체의 성능 지표 여부는 §2 주의 참조 — content-blind라 성능 아님.)"
    )


def _verdict(off, on, brk, ne, n) -> str:
    d_acc = on["accuracy_on_assessed"] - off["accuracy_on_assessed"]
    return (
        f"샘플/시나리오 리포트에서 no_evidence 기권이 {ne}건/{n} 자연 발동했고(현행 dev/test는 0건이었음), "
        f"그중 미탐 {brk['saves']}건을 사람 검토로 전환하며 자동판정 정확도를 {d_acc:+.3f} 끌어올렸다. "
        f"Overall({on['overall'] - off['overall']:+.3f})은 이 설계에서 구조상 오를 수 없으므로 여전히 "
        "채택 판정 기준이 아니다. **정밀도 p 역시 채택 판정 기준이 아니다** — content-blind 기권은 "
        "구성한 라벨 비율을 그대로 반영할 뿐 모델 성능이 아니다. 이 하네스가 검증하는 것은 "
        "'정확한 발동(무결성 감사 §0)'과 'Accuracy(assessed)/Coverage 트레이드오프'뿐이며, "
        "근거가 있을 때의 실제 D1 성능은 진위 holdout(`scripts/d1_truth_eval.py`)으로 별도 측정한다. "
        "다음 단계: (1) 이 셋을 실키 회귀에 편입해 실측 고정, (2) HITL 라우팅을 붙여 '기권→검토'를 "
        "실제 동작으로 연결, (3) 진위 holdout 확충으로 D1 실성능 신뢰구간 확보."
    )


if __name__ == "__main__":
    main()
