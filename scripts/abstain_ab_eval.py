"""ABSTAIN on/off A/B 비교 — 단일 capture 기반 결정적(deterministic) 하네스.

배치 4에서 발견된 결함: 이전 버전은 OFF/ON을 **각각 별도 capture()
호출**(=별개의 LLM/judge 실행)로 비교했다. abstain(특히 주 타깃
no_evidence)은 룰 레이어의 rule_score를 바꾸지 않으므로(미검증 시
OFF/ON 모두 score 0.0, ON은 여기에 abstain=True 플래그만 더함) rule_score
·judgeable·다른 축·judge 대상은 OFF/ON에서 원리상 동일해야 하는데,
별도 실행이라 judge(LLM) 결과가 실행마다 재현되지 않아 abstain과 무관한
축까지 A/B 결과를 오염시켰다(docs/abstention_metrics_result.md 2절).

이번 버전은 **스플릿당 capture를 1회만** 실행(ABSTAIN_ENABLED=True로
abstain 플래그가 캐시에 기록되게 함)하고, 그 동일한 캐시로부터
evaluate.with_abstain_ignored()를 이용해 OFF/ON **두 해석을 모두
유도**한다 — judge 재호출 없음. 이로써 실행 간 변동이 0이 되어 abstain의
순수 효과만 남는다.

핵심 원칙(held_out_eval.py와 동일):
  - 임계값(cfg)은 BASELINE 고정. test에서 재튜닝 금지 → 진짜 held-out 수치.
  - dev = 튜닝셋(낙관 편향 가능) / test = 검수 held-out(일반화).
  - 부트스트랩 95% CI는 evaluate.bootstrap_ci 재사용.

실행:
  # 배선 검증용 목(수치 무의미):
  ESGENIE_FORCE_MOCK=1 python scripts/abstain_ab_eval.py
  # 실키:
  OPENAI_API_KEY=... ESGENIE_STRICT=1 python scripts/abstain_ab_eval.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

try:  # .env 자동 로드 (인라인 환경변수 없이도 키 사용)
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import esgenie.layer3_detect as _layer3_detect
import esgenie.ssot.detector_5axis as _detector_5axis
from esgenie.calibrate import BASELINE, capture
from esgenie.evaluate import _case_rows, _prf, abstain_coverage, bootstrap_ci, with_abstain_ignored

ROOT = Path(__file__).resolve().parents[1]
SPLIT_DIR = ROOT / "data" / "benchmark_v2"
OUT_DIR = ROOT / "outputs" / "benchmark"
SPLITS = ["dev", "test"]


def _run_mode() -> str:
    if os.getenv("ESGENIE_FORCE_MOCK") == "1":
        return "MOCK (배선 검증용 — 수치 무의미)"
    if os.getenv("ESGENIE_STRICT") == "1":
        return "REAL-KEY (strict)"
    return "AUTO (키 있으면 실키, 없으면 mock 폴백)"


def _capture_once(split: str, *, cfg: dict[str, float]) -> dict[str, Any]:
    """스플릿당 단 1회 capture. ABSTAIN_ENABLED=True로 캐시에 abstain 플래그를
    기록한다(ABSTAIN_UNIT_MISMATCH는 config.py 기본값을 그대로 따름 — 이 스크립트가
    임의로 바꾸지 않는다). judge(LLM)는 이 호출에서만 일어난다."""
    _layer3_detect.ABSTAIN_ENABLED = True
    _detector_5axis.ABSTAIN_ENABLED = True
    bench_path = SPLIT_DIR / f"{split}.json"
    cache_path = OUT_DIR / f"{split}_abstain_judge_cache.json"
    cache = capture(bench_path=bench_path, cache_path=cache_path)

    on_rows = _case_rows(cache["records"], cfg)
    off_rows = with_abstain_ignored(on_rows)

    # 정확성 불변식: OFF/ON 두 해석은 같은 rows에서 유도되므로 pred/y/correct가
    # 케이스별로 완전히 동일해야 한다(다르면 with_abstain_ignored 구현 버그).
    assert [r["pred"] for r in off_rows] == [r["pred"] for r in on_rows]
    assert [r["y"] for r in off_rows] == [r["y"] for r in on_rows]
    assert [r["correct"] for r in off_rows] == [r["correct"] for r in on_rows]

    return {
        "n": len(on_rows),
        "llm_calls": cache.get("llm_calls", 0),
        "off": {
            "prf": _prf(off_rows), "coverage": abstain_coverage(off_rows),
            "f1_ci": bootstrap_ci(off_rows, metric="f1"),
        },
        "on": {
            "prf": _prf(on_rows), "coverage": abstain_coverage(on_rows),
            "f1_ci": bootstrap_ci(on_rows, metric="f1"),
        },
    }


def main() -> None:
    cfg = BASELINE
    mode = _run_mode()
    results: dict[str, dict[str, Any]] = {}
    try:
        for split in SPLITS:
            results[split] = _capture_once(split, cfg=cfg)
    finally:
        _layer3_detect.ABSTAIN_ENABLED = False  # 스크립트 종료 후 플래그 원복(방어적)
        _detector_5axis.ABSTAIN_ENABLED = False

    total_llm_calls = sum(r["llm_calls"] for r in results.values())

    L = [
        "# ABSTAIN on/off A/B 비교 — deterministic(single-capture)",
        f"(모드: {mode})",
        f"- 임계값 고정(BASELINE, test 재튜닝 안 함): trig={cfg['trigger']} "
        f"w={cfg['rule_weight']} thr={cfg['threshold']} axf={cfg['axis_flag']}",
        f"- 스플릿당 capture 1회(=LLM 호출 1세트)만 실행 — 총 LLM 호출 {total_llm_calls}건 "
        "(이전 이중-capture 방식 대비 절반)",
        "",
    ]
    for split in SPLITS:
        r = results[split]
        r_off, r_on = r["off"], r["on"]
        tag_label = "튜닝셋(낙관 편향 가능)" if split == "dev" else "held-out(일반화 수치)"
        L += [
            f"## {split.upper()} — n={r['n']} · {tag_label} · LLM 호출 {r['llm_calls']}건(1회)",
            "",
            "| 조건 | Coverage | Accuracy(assessed) | Overall | 기권수(no_ev/unit/lowconf) "
            "| precision | recall | f1 |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for tag, rr in (("OFF", r_off), ("ON", r_on)):
            cov = rr["coverage"]
            by_reason = cov["abstains"]["by_reason"]
            L.append(
                f"| {tag} | {cov['coverage']*100:.1f}% | {cov['accuracy_on_assessed']:.3f} | "
                f"{cov['overall']:.3f} | {cov['abstains']['total']}"
                f"({by_reason.get('no_evidence', 0)}/{by_reason.get('unit_mismatch', 0)}/"
                f"{by_reason.get('low_confidence', 0)}) | "
                f"{rr['prf']['precision']:.3f} | {rr['prf']['recall']:.3f} | {rr['prf']['f1']:.3f} |"
            )
        L += [
            "",
            f"- F1 bootstrap 95% CI — OFF {r_off['f1_ci'][0]:.3f} "
            f"({r_off['f1_ci'][1]:.3f}~{r_off['f1_ci'][2]:.3f}) · "
            f"ON {r_on['f1_ci'][0]:.3f} ({r_on['f1_ci'][1]:.3f}~{r_on['f1_ci'][2]:.3f})",
            "",
        ]

    L.append(
        "> 해석 가이드: ON에서 Coverage는 다소 낮아지되 Accuracy(assessed)가 오르고 "
        "Overall이 하락하지 않으면 이상적(근거 없는 억지 판정을 기권으로 돌려 오탐이 "
        "줄었다는 신호). 이 하네스는 단일 capture에서 OFF/ON을 유도하므로, precision/"
        "recall/f1이 OFF/ON에서 차이가 난다면 그것은 실행 변동이 아니라 abstain이 "
        "실제로 판정을 바꾼 케이스 때문이다(구조적으로 보장됨)."
    )
    if "MOCK" in mode:
        L.append(
            "> ⚠ MOCK 실행 — 위 수치는 파이프라인 배선 확인용일 뿐 성능 근거 아님. "
            "실제 A/B 판단은 실키로 재실행 후 사용할 것."
        )
    report = "\n".join(L)
    (OUT_DIR / "abstain_ab.md").write_text(report, encoding="utf-8")
    print(report)
    print(f"\n저장: {OUT_DIR / 'abstain_ab.md'}")


if __name__ == "__main__":
    main()
