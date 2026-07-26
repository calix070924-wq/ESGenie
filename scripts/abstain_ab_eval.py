"""ABSTAIN_ENABLED on/off A/B 비교 — Coverage / Accuracy(assessed) / Overall.

목적: 기권(abstain) 표식이 실제로 성능에 도움이 되는지 "측정"한다(EmeraldMind
방식). 게이트·HITL 라우팅은 아직 켜지 않는다 — 이 결과를 보고 나서 다음 단계
(Step 4) 진행 여부를 사람이 결정한다.

핵심 원칙(held_out_eval.py와 동일):
  - 임계값(cfg)은 BASELINE 고정. test에서 재튜닝 금지 → 진짜 held-out 수치.
  - dev = 튜닝셋(낙관 편향 가능) / test = 검수 held-out(일반화).
  - 부트스트랩 95% CI는 evaluate.bootstrap_ci 재사용.

ABSTAIN_ENABLED는 모듈 임포트 시 1회 바인딩되는 상수라 프로세스 내에서
os.environ만 바꿔서는 반영되지 않는다. 이 스크립트는 esgenie.layer3_detect /
esgenie.ssot.detector_5axis 모듈 속성을 직접 토글해 같은 프로세스에서
off→on 캡처를 순차 실행한다(테스트의 monkeypatch와 동일 기법).

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
from esgenie.evaluate import _case_rows, _prf, abstain_coverage, bootstrap_ci

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


def _set_abstain(enabled: bool) -> None:
    """두 D1 경로의 ABSTAIN_ENABLED를 같은 프로세스 내에서 런타임 토글."""
    _layer3_detect.ABSTAIN_ENABLED = enabled
    _detector_5axis.ABSTAIN_ENABLED = enabled


def _run_one(split: str, *, abstain_on: bool, cfg: dict[str, float]) -> dict[str, Any]:
    _set_abstain(abstain_on)
    tag = "on" if abstain_on else "off"
    bench_path = SPLIT_DIR / f"{split}.json"
    cache_path = OUT_DIR / f"{split}_abstain_{tag}_judge_cache.json"
    capture(bench_path=bench_path, cache_path=cache_path)
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    rows = _case_rows(cache["records"], cfg)
    return {
        "n": len(rows),
        "prf": _prf(rows),
        "coverage": abstain_coverage(rows),
        "f1_ci": bootstrap_ci(rows, metric="f1"),
    }


def main() -> None:
    cfg = BASELINE
    mode = _run_mode()
    results: dict[str, dict[str, dict[str, Any]]] = {}
    try:
        for split in SPLITS:
            results[split] = {
                "off": _run_one(split, abstain_on=False, cfg=cfg),
                "on": _run_one(split, abstain_on=True, cfg=cfg),
            }
    finally:
        _set_abstain(False)  # 스크립트 종료 후 플래그 원복(방어적 — 기본 False 유지)

    L = [
        f"# ABSTAIN_ENABLED on/off A/B 비교  (모드: {mode})",
        f"- 임계값 고정(BASELINE, test 재튜닝 안 함): trig={cfg['trigger']} "
        f"w={cfg['rule_weight']} thr={cfg['threshold']} axf={cfg['axis_flag']}",
        "",
    ]
    for split in SPLITS:
        r_off, r_on = results[split]["off"], results[split]["on"]
        tag_label = "튜닝셋(낙관 편향 가능)" if split == "dev" else "held-out(일반화 수치)"
        L += [
            f"## {split.upper()} — n={r_off['n']} · {tag_label}",
            "",
            "| 조건 | Coverage | Accuracy(assessed) | Overall | 기권수(no_ev/unit/lowconf) "
            "| precision | recall | f1 |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for tag, r in (("OFF", r_off), ("ON", r_on)):
            cov = r["coverage"]
            by_reason = cov["abstains"]["by_reason"]
            L.append(
                f"| {tag} | {cov['coverage']*100:.1f}% | {cov['accuracy_on_assessed']:.3f} | "
                f"{cov['overall']:.3f} | {cov['abstains']['total']}"
                f"({by_reason.get('no_evidence', 0)}/{by_reason.get('unit_mismatch', 0)}/"
                f"{by_reason.get('low_confidence', 0)}) | "
                f"{r['prf']['precision']:.3f} | {r['prf']['recall']:.3f} | {r['prf']['f1']:.3f} |"
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
        "줄었다는 신호). Overall이 떨어지면 기권 조건이 과하다는 신호 — 다음 단계(게이트 "
        "라우팅) 전에 조건 재검토 필요."
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
