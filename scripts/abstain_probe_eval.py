"""no_evidence 기권(abstain) 순수 효과 — 통제 probe A/B 하네스 (deterministic).

왜 별도 probe인가
-----------------
현행 benchmark_v2(dev/test, n=320)는 검증 가능한 수치만 큐레이션돼 있어
no_evidence 기권이 **0건** 관측된다(docs/abstention_metrics_result.md). 즉 기존
A/B로는 기권 기능이 아예 발동하지 않아 효과를 측정할 수 없다. 이 스크립트는
"회사가 해당 지표를 공시하지 않음"을 그래프에서 omit_codes 코드를 제거해 재현,
no_evidence 기권을 **결정적으로** 발동시켜 그 순수 효과만 격리 측정한다.

핵심 지표 해석 (중요)
--------------------
Overall = Accuracy(assessed) x Coverage = (assessed 중 정답 수) / N.
예측(rule_score)이 OFF/ON에서 동일한 이 A/B 설계에서 기권은 assessed에서
케이스를 빼기만 하므로:
  - 맞던 케이스를 기권하면 → Overall 하락(비용).
  - 틀리던 케이스를 기권하면 → Overall 불변(원래 크레딧 0).
따라서 **Overall은 기권으로 절대 오르지 않는다**(단조 비증가). 기권의 진짜
가치("조용히 틀리던 미탐을 사람 검토로 전환")는 Overall이 구조적으로 못 본다.
그 가치는 두 곳에서 드러난다:
  1) Accuracy(assessed) 상승 — 자동판정 집합에서 오답을 덜어낸 만큼 깨끗해짐.
  2) 기권 정밀도(deferral precision) = 기권한 것 중 'OFF였다면 틀렸을' 비율.
그리고 비용-가중 판정: 미탐 1건을 잡는 가치 B, 불필요 검토 1건 비용 R일 때
기권이 순이득이려면  B/R > (1 - p) / p   (p = 기권 정밀도).

실행
----
  ESGENIE_FORCE_MOCK=1 python scripts/abstain_probe_eval.py   # 결정적(수치 유의미: 룰 기반)
  # 실키 불필요 — probe 케이스는 조용한 수치주장이라 D1 룰/기권만으로 판정된다.

라이브(룰+LLM) 모드
-------------------
  ESGENIE_ABSTAIN_LIVE=1 ESGENIE_STRICT=1 python scripts/abstain_probe_eval.py
케이스마다 룰(detect_risk_vector)과 하이브리드(detect_risk_vector_hybrid, 실
LLM 호출)를 **둘 다** 실행해 abstain 결정이 LLM 단계에서 보존되는지 검증한다.
전 축 룰점수가 JUDGE_TRIGGER 미만이면 LLM 자체가 트리거 안 되므로(judge
used=False), abstain 케이스는 구조적으로 LLM이 안 불린다 — control_mismatch
(D1=1.0, 값 불일치 대조군)만 실 호출된다. rule↔hybrid 판정이 하나라도 갈리면
그 케이스를 출력하고 비정상 종료(exit 1)한다. 비-라이브(기본) 경로는 이
플래그와 무관하게 기존과 완전히 동일하다.
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
PROBE_PATH = ROOT / "data" / "benchmark_v2" / "abstain_probe.json"
# 생성물은 gitignored outputs/ 아래로 (docs/의 추적 파일을 덮어써 워킹트리를
# 더럽히지 않도록 — 코드리뷰 개선. docs/의 결과 스냅샷은 수동 큐레이션 유지).
OUT_DOC = ROOT / "outputs" / "benchmark" / "abstain_probe_result.md"


def _rv_with_omission(graph: Any, sentence: str, omit_codes: list[str]) -> Any:
    """omit_codes 코드의 노드를 그래프에서 일시 제거(=회사가 그 지표를 공시하지
    않음)한 상태로 detect_risk_vector 실행. 호출 후 원복."""
    orig = graph.search_nodes
    if omit_codes:
        def patched(keywords=None, **kw):
            if keywords and any(c in keywords for c in omit_codes):
                return []
            return orig(keywords=keywords, **kw)
        graph.search_nodes = patched
    try:
        return detect_risk_vector(sentence, evidence_graph=graph)
    finally:
        graph.search_nodes = orig


def _rv_pair_with_omission(graph: Any, sentence: str, omit_codes: list[str]) -> tuple[Any, Any]:
    """라이브 모드 전용: 동일 omission 패치 아래 rule/hybrid 두 경로를 모두
    실행해 (rv_rule, rv_hybrid)를 반환한다. 검색 함수를 1회만 패치/원복한다."""
    orig = graph.search_nodes
    if omit_codes:
        def patched(keywords=None, **kw):
            if keywords and any(c in keywords for c in omit_codes):
                return []
            return orig(keywords=keywords, **kw)
        graph.search_nodes = patched
    try:
        rv_rule = detect_risk_vector(sentence, evidence_graph=graph)
        rv_hyb = detect_risk_vector_hybrid(sentence, evidence_graph=graph)
        return rv_rule, rv_hyb
    finally:
        graph.search_nodes = orig


def _case_row(rec: dict[str, Any], rv: Any, cfg: dict[str, float]) -> dict[str, Any]:
    """evaluate._case_rows()와 동일 규칙으로 라이브 RiskVector를 평가 행으로 변환."""
    pred = int(_flagged(rv, cfg["threshold"], cfg["axis_flag"]))
    y = 1 if rec["label"] == "greenwash" else 0
    abstained_names = rv.abstained_axes()
    axes_map = {
        "D1_numeric": rv.D1_numeric, "D2_modifier": rv.D2_modifier,
        "D3_semantic": rv.D3_semantic, "D5_timeseries": rv.D5_timeseries,
    }
    reasons = [axes_map[n].abstain_reason for n in abstained_names if axes_map[n].abstain_reason]
    abstained = bool(abstained_names) and not pred
    return {"id": rec["id"], "category": rec["category"], "label": rec["label"],
            "p": rv.risk_score, "y": y, "pred": pred, "correct": int(pred == y),
            "abstained": abstained, "abstain_reasons": reasons}


def _deferral_breakdown(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """기권(ON에서 assessed 제외)된 행을 '가치(OFF였다면 오답=save)' vs
    '비용(OFF였다면 정답=waste)'으로 분해."""
    deferred = [r for r in rows if r["abstained"]]
    saves = [r for r in deferred if r["correct"] == 0]   # OFF면 틀렸을 것 → 검토로 구제
    wastes = [r for r in deferred if r["correct"] == 1]   # OFF면 맞았을 것 → 불필요 검토
    n_def = len(deferred)
    precision = (len(saves) / n_def) if n_def else 0.0
    breakeven = ((1 - precision) / precision) if precision > 0 else float("inf")
    return {
        "deferred": n_def, "saves": len(saves), "wastes": len(wastes),
        "deferral_precision": round(precision, 4),
        "breakeven_benefit_cost_ratio": (round(breakeven, 4) if breakeven != float("inf") else None),
        "save_ids": [r["id"] for r in saves], "waste_ids": [r["id"] for r in wastes],
    }


def main() -> None:
    _layer3_detect.ABSTAIN_ENABLED = True
    _detector_5axis.ABSTAIN_ENABLED = True
    cfg = BASELINE
    mock = os.getenv("ESGENIE_FORCE_MOCK") == "1"
    live = os.getenv("ESGENIE_ABSTAIN_LIVE") == "1"

    probe = json.loads(PROBE_PATH.read_text(encoding="utf-8"))
    report = load_report(probe.get("ticker", "005930"))
    graph = build_evidence_graph(report)

    rows: list[dict[str, Any]] = []
    llm_used = 0
    mismatches: list[str] = []
    try:
        for case in probe["cases"]:
            omit_codes = case.get("omit_codes") or []
            if live:
                rv_rule, rv = _rv_pair_with_omission(graph, case["sentence"], omit_codes)
                if bool(rv.aggregate.get("judge", {}).get("used")):
                    llm_used += 1
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
                rv = _rv_with_omission(graph, case["sentence"], omit_codes)
                row = _case_row(case, rv, cfg)
            rows.append(row)
    finally:
        _layer3_detect.ABSTAIN_ENABLED = False
        _detector_5axis.ABSTAIN_ENABLED = False

    on = abstain_coverage(rows)                          # 기권 ON
    off = abstain_coverage(with_abstain_ignored(rows))   # 기권 OFF(같은 예측, 플래그만 무시)
    brk = _deferral_breakdown(rows)

    # ---- 콘솔 + 문서 출력 -------------------------------------------------
    def fmt(m):
        return (f"coverage={m['coverage']:.3f}  acc(assessed)={m['accuracy_on_assessed']:.3f}  "
                f"overall={m['overall']:.3f}  abstains={m['abstains']['total']}")

    lines = [
        "# no_evidence 기권 순수 효과 — 통제 probe 결과",
        "",
        f"> probe: `data/benchmark_v2/abstain_probe.json` (n={on['n']}) · "
        f"모드: {'LIVE(룰+LLM 하이브리드)' if live else ('MOCK(룰 기반, 결정적)' if mock else 'AUTO')} · 임계값 BASELINE 고정",
        "> 각 케이스는 omit_codes 코드를 그래프에서 제거해 '해당 지표 미공시'를 재현 → no_evidence 기권 발동.",
        "",
        "## 1. 전역 지표 (OFF vs ON)",
        "",
        "| 해석 | Coverage | Accuracy(assessed) | Overall | 기권수 |",
        "|---|---|---|---|---|",
        f"| OFF (기권 무시) | {off['coverage']:.3f} | {off['accuracy_on_assessed']:.3f} | {off['overall']:.3f} | {off['abstains']['total']} |",
        f"| ON  (기권 반영) | {on['coverage']:.3f} | {on['accuracy_on_assessed']:.3f} | {on['overall']:.3f} | {on['abstains']['total']} |",
        "",
        f"- 사유별 기권: {on['abstains']['by_reason']}",
        f"- ΔAccuracy(assessed) = {on['accuracy_on_assessed'] - off['accuracy_on_assessed']:+.3f}  "
        f"(자동판정 집합에서 오답을 덜어낸 효과)",
        f"- ΔCoverage = {on['coverage'] - off['coverage']:+.3f}  (자동화율 비용)",
        f"- ΔOverall = {on['overall'] - off['overall']:+.3f}  "
        "(구조상 <= 0 — Overall은 기권의 미탐-구제 가치를 못 봄)",
        "",
        "## 2. 기권 분해 — 가치(save) vs 비용(waste)",
        "",
        f"- 총 기권: {brk['deferred']}건",
        f"- **save**(OFF면 틀렸을 미탐 → 검토로 구제): {brk['saves']}건 {brk['save_ids']}",
        f"- **waste**(OFF면 맞았을 것 → 불필요 검토): {brk['wastes']}건 {brk['waste_ids']}",
        f"- **기권 정밀도 p = {brk['deferral_precision']:.3f}** ({brk['saves']}/{brk['deferred']})",
        f"- 손익분기 비용비: 미탐 1건 가치 B, 불필요 검토 1건 비용 R 일 때 "
        f"**B/R > {brk['breakeven_benefit_cost_ratio']}** 이면 기권 순이득.",
        "",
        "## 3. 현실 prevalence 투영 (probe는 전부 no_evidence라 Overall 하락이 과장됨)",
        "",
        _projection(brk, base_n=320, base_acc=0.90),
        "",
        "## 4. 판정",
        "",
        _verdict(off, on, brk),
        "",
        "## 5. 다음단계 게이트",
        "",
        _next_gate(brk),
        "",
    ]
    if live:
        lines += [
            "## 6. 라이브(룰+LLM) 검증",
            "",
            f"- LLM 호출: {llm_used}/{len(rows)}건 "
            "(전 축 룰점수 < JUDGE_TRIGGER면 judge 자체가 트리거 안 됨 — "
            "abstain 케이스는 구조적으로 미호출, control_mismatch만 실 호출 기대)",
            f"- rule↔hybrid abstain 판정 불일치: {len(mismatches)}건"
            + ("" if not mismatches else "\n" + "\n".join(f"  - {m}" for m in mismatches)),
            "",
        ]
    out = "\n".join(lines)
    print("\n" + "="*72)
    print(f"OFF : {fmt(off)}")
    print(f"ON  : {fmt(on)}")
    print(f"기권 정밀도 p={brk['deferral_precision']:.3f}  "
          f"(save={brk['saves']}, waste={brk['wastes']}, 손익분기 B/R>{brk['breakeven_benefit_cost_ratio']})")
    if live:
        print(f"LIVE: LLM 호출 {llm_used}/{len(rows)}건, rule↔hybrid 불일치 {len(mismatches)}건")
        for m in mismatches:
            print(f"  ⚠ {m}")
    print("="*72 + "\n")
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.write_text(out, encoding="utf-8")
    print(f"결과 문서 저장: {OUT_DOC}")

    if live and mismatches:
        raise SystemExit(1)


def _projection(brk: dict[str, Any], *, base_n: int, base_acc: float) -> str:
    """no_evidence 영역을 검증가능 base 코퍼스에 섞었을 때의 전역 지표 투영.
    base: base_n건 전부 assessed·정답률 base_acc. 여기에 save(=OFF면 오답) saves건 +
    waste(=OFF면 정답) wastes건을 주입."""
    s, w = brk["saves"], brk["wastes"]
    cb = round(base_n * base_acc)          # base 정답 수
    n_mix = base_n + s + w
    # OFF: save는 오답, waste는 정답 → C_off = cb + w
    acc_off = (cb + w) / n_mix
    ov_off = (cb + w) / n_mix
    # ON: no_evidence 전부 기권 → assessed=base만
    acc_on = cb / base_n
    cov_on = base_n / n_mix
    ov_on = cb / n_mix
    return (
        f"가정: base n={base_n}, 정답률 {base_acc:.2f}(=정답 {cb}건, 전부 검증가능·기권0). "
        f"여기에 no_evidence {s+w}건(save {s}, waste {w}) 주입.\n\n"
        "| 해석 | Coverage | Accuracy(assessed) | Overall |\n|---|---|---|---|\n"
        f"| OFF | 1.000 | {acc_off:.3f} | {ov_off:.3f} |\n"
        f"| ON  | {cov_on:.3f} | {acc_on:.3f} | {ov_on:.3f} |\n\n"
        f"→ 현실 비중에선 ΔCoverage={cov_on-1:+.3f}, "
        f"ΔAccuracy(assessed)={acc_on-acc_off:+.3f}, ΔOverall={ov_on-ov_off:+.3f}. "
        "coverage 비용은 작고 정확도는 오히려 상승하지만, "
        f"미탐 {s}건이 '조용한 통과' 대신 검토로 올라온다(Overall은 여전히 이 이득을 못 봄)."
    )


def _next_gate(brk: dict[str, Any]) -> str:
    p = brk["deferral_precision"]
    return (
        f"현재 기권 정밀도 p={p:.3f} → 기권 10건 중 {brk['wastes']}건은 맞던 것을 넘긴 낭비다. "
        "두 갈래 중 택1로 성능을 올린다:\n\n"
        "1. **정밀도↑ (기권을 더 똑똑하게)**: no_evidence라도 '위험 신호가 동반될 때만' 기권하도록 "
        "게이트를 조인다(예: D2 모호어·D3 의미이탈이 함께 뜨는 미검증 수치만 기권). "
        "clean 오기권을 줄여 coverage 비용을 낮춘다.\n"
        "2. **prevalence↓ (근거 검색 개선)**: no_evidence 자체를 줄인다 — retrieval_gate/근거 연결을 강화해 "
        "실제로는 리포트에 있는 수치를 못 찾아 기권하는 경우를 없앤다. 기권은 '정말 공시 안 된' 것만 남긴다.\n\n"
        "권고: 먼저 (2)로 no_evidence의 '진짜 미공시 vs 검색실패' 비율을 실측(실키 실행)하고, "
        "검색실패가 크면 (2), 진짜 미공시가 대부분이면 (1)로 간다."
    )


def _verdict(off, on, brk) -> str:
    p = brk["deferral_precision"]
    d_acc = on["accuracy_on_assessed"] - off["accuracy_on_assessed"]
    if brk["deferred"] == 0:
        return "기권 0건 — probe가 no_evidence를 발동시키지 못함(omit_codes 점검 필요)."
    return (
        f"기권이 no_evidence 영역에서 미탐 {brk['saves']}건을 사람 검토로 전환하며 "
        f"자동판정 정확도를 {d_acc:+.3f} 끌어올렸다(정밀도 p={p:.3f}). "
        f"Overall은 {on['overall'] - off['overall']:+.3f}로 이 설계에선 구조상 오를 수 없으므로, "
        f"기권의 채택 근거는 Overall이 아니라 '정밀도 p와 비용비 B/R'로 판단해야 한다. "
        f"컴플라이언스 맥락(미탐 1건 손실 >> 검토 1건 비용)에서 손익분기 B/R>"
        f"{brk['breakeven_benefit_cost_ratio']}는 통상 쉽게 충족된다."
    )


if __name__ == "__main__":
    main()
