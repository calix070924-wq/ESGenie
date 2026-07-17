"""D1 정밀도 수정(목표 문맥 가드 + 다지표 교차 매칭 차단) 빠른 실검증.

풀 배치(회사당 30~60분) 대신 한 회사의 필요한 영역만 돌린다.
기본: LG화학 E·S — 2026-07-17 배치에서 E=목표 오탐(HIGH), S=교차 매칭 오탐(MEDIUM)
이었던 바로 그 사례. 기대: E가 HIGH 아래로, D1 detail에서 교차 비교 소멸.

사용:
    python3 scripts/verify_d1_fix.py                 # LG화학 E S (~15-20분)
    python3 scripts/verify_d1_fix.py --areas E       # 더 빠르게 E만
    python3 scripts/verify_d1_fix.py --ticker 009150 --areas E   # 삼성전기 D2 확인
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MANIFEST = ROOT / "data" / "real_reports" / "manifest.json"

# 2026-07-17 19:40 배치 (수정 전) 기준값 — 비교 출력용
BEFORE = {
    "051910": {"E": (50.2, "HIGH"), "S": (35.5, "MEDIUM"), "G": (25.6, "MEDIUM")},
    "009150": {"E": (30.0, "MEDIUM"), "S": (26.2, "MEDIUM"), "G": (23.1, "LOW")},
}


def main() -> None:
    ap = argparse.ArgumentParser(description="D1 수정 빠른 실검증 (부분 영역 실행)")
    ap.add_argument("--ticker", default="051910", help="manifest ticker (기본: LG화학)")
    ap.add_argument("--areas", nargs="+", default=["E", "S"], choices=["E", "S", "G"])
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    entry = next(e for e in json.loads(MANIFEST.read_text(encoding="utf-8"))
                 if e["ticker"] == args.ticker)
    pdf = ROOT / entry["pdf"]

    from esgenie import pipeline
    output = pipeline.run(
        corp_code=entry.get("corp_code", entry["ticker"]),
        areas=args.areas,
        evidence_files={pdf.name: str(pdf)},
        save_traces=True,
    )

    print("\n" + "=" * 72)
    print(f"D1 수정 검증 — {entry['name']} ({', '.join(args.areas)})  [before=2026-07-17 19:40 배치]")
    print("=" * 72)
    before = BEFORE.get(args.ticker, {})
    for area, v in output.sections.items():
        b = before.get(area)
        b_str = f"{b[0]} ({b[1]})" if b else "—"
        rv = v.final.detection.risk_vector
        d1 = f"D1={rv.D1_numeric.score*100:.0f}" if rv else "D1=?"
        print(f"  {area}: {b_str} → {v.final_score:.1f} ({v.final_band})  [{d1}]")
        if rv and rv.D1_numeric.detail:
            print(f"     detail: {rv.D1_numeric.detail[:220]}")


if __name__ == "__main__":
    main()
