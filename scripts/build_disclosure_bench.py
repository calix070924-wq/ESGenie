"""D6 선택적 공시 문서단위 벤치 생성.

각 케이스 = (공시 항목집합, 누락 항목집합) → 기대 레벨(사람 판단).
기대 레벨은 detector를 돌려서가 아니라 **시나리오 심각도에 대한 사람 직관**으로 부여한다.
detector(룰)와 불일치하면 그게 임계값 캘리브레이션 신호다.
"""
from __future__ import annotations
import json
from pathlib import Path
from esgenie.layer3_disclosure import OMISSION_SENSITIVITY, RATIO_CONTEXT_PAIRS

ROOT = Path(__file__).resolve().parents[1]
SENS = list(OMISSION_SENSITIVITY.keys())
RATIOS = list(RATIO_CONTEXT_PAIRS.keys())


def case(cid, scenario, *, omit=None, orphan=None, expected="low", note=""):
    """omit: 누락할 민감코드 / orphan: 공시할 고아비율코드(분모는 자동 누락)."""
    omit = set(omit or [])
    orphan = list(orphan or [])
    missing = set(omit)
    disclosed = set(SENS) - omit        # 기본: 민감항목 전부 공시
    for r in orphan:                    # 고아비율: 비율 공시 + 분모/맥락 누락
        disclosed.add(r)
        for ctx in RATIO_CONTEXT_PAIRS[r]:
            missing.add(ctx)
            disclosed.discard(ctx)
    return {"id": cid, "scenario": scenario, "expected_level": expected,
            "disclosed": sorted(disclosed), "missing": sorted(missing), "note": note}


CASES = [
    case("D6-01", "전 항목 정상 공시", expected="low",
         note="민감항목 전부 공시, 고아비율 없음 → 의심 없음"),
    case("D6-02", "유리비율+분모 함께 공시", orphan=[], expected="low",
         note="재활용률과 폐기물 총량을 함께 공시(맥락 충족) → 정상"),
    case("D6-03", "용수총량 1건 누락(경미)", omit=["E-5-1"], expected="low",
         note="단일 저민감 항목 누락 — 선택적 공시 패턴 아님"),
    case("D6-04", "온실가스 배출량 1건 누락", omit=["E-3-1"], expected="low",
         note="단일 누락(고민감이나 1건) — 경계 케이스"),
    case("D6-05", "재활용률만 공시·폐기물총량 누락(고아)", orphan=["E-6-2"], expected="medium",
         note="유리 비율만 자랑, 분모 침묵 — 전형적 cherry-picking"),
    case("D6-06", "재생에너지비율만 공시·총배출/총에너지 누락", orphan=["E-4-2"], expected="medium",
         note="고아 비율 1건(맥락 2개 누락)"),
    case("D6-07", "고아비율 2건", orphan=["E-6-2", "E-5-2"], expected="high",
         note="재활용률·재사용용수 둘 다 분모 없이 공시 — 강한 신호"),
    case("D6-08", "위반·배출·산재 6건 은폐", expected="high",
         omit=["E-3-1", "E-6-1", "E-8-1", "S-4-2", "S-9-1", "G-6-1"],
         note="불리 항목 전반 누락 — 체계적 선택공시(★대량 누락 캘리브레이션 점검)"),
    case("D6-09", "고아비율 1건 + 위반 다수 누락", orphan=["E-6-2"], expected="high",
         omit=["E-8-1", "S-9-1", "G-6-1"],
         note="cherry-pick + 법규위반 은폐 동반"),
    case("D6-10", "법규위반 3종 누락", omit=["E-8-1", "S-9-1", "G-6-1"], expected="medium",
         note="환경·사회·지배 법규위반 모두 누락"),
    case("D6-11", "고아비율 3건", orphan=["E-6-2", "E-5-2", "E-2-2"], expected="high",
         note="유리 비율 다수를 분모 없이 공시"),
    case("D6-12", "저민감 2건 누락", omit=["E-5-1", "E-2-1"], expected="low",
         note="총량성 저민감 항목 2건 — 경미"),
]


def main():
    out = {"track": "selective_disclosure(D6) document-level",
           "level_order": ["low", "medium", "high"], "n_cases": len(CASES), "cases": CASES}
    p = ROOT / "data" / "benchmark_v2" / "disclosure_bench.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(CASES)}개 케이스 저장 → {p}")


if __name__ == "__main__":
    main()
