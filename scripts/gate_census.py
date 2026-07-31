#!/usr/bin/env python3
"""검색 게이트 전수 조사 — 어떤 기업·영역이 왜 차단되는가.

배경
----
`retrieval_gate.evaluate_retrieval`은 생성 이전 단계에서 검색 결과를 심사하고,
hard_fail이 하나라도 있으면 섹션 생성을 포기한다. 그 결과 audit_trace에는
"검색 근거가 부족하여 자동 생성하지 않았습니다."라는 안내문만 남고 보고서가 빈다.
(실측: 005930 E, 009150 S·G)

이 스크립트는 LLM을 호출하지 않는다. 게이트는 생성 이전이므로 검색까지만 돌리면
판정을 그대로 재현할 수 있다 — 비용 0, 결정적, 수 초.

측정 대상
--------
샘플 DART 5개사 × E/S/G = 15개 조합에 대해
  - decision (ACCEPT / 차단)
  - 최종 tier (캐스케이드가 몇 단계까지 내려갔는가)
  - hard_fails / soft_flags 사유별 빈도
  - top1_score와 field_coverage
  - **top-1 청크의 실제 텍스트** — 왜 영역 단어가 없는지 눈으로 보기 위함

실행
----
    python scripts/gate_census.py
    python scripts/gate_census.py --tickers 005930 SME001 --areas E
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # PYTHONPATH=. 없이도 직접 실행 가능하게

TICKERS = ["005930", "005380", "005490", "SME001", "SME002"]
AREAS = ["E", "S", "G"]


def _is_accept(decision: object) -> bool:
    """판정값은 GateDecision.ACCEPT.value("ACCEPT")로 대문자다."""
    return str(decision).upper() == "ACCEPT"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="*", default=TICKERS)
    ap.add_argument("--areas", nargs="*", default=AREAS)
    ap.add_argument("--out", default="outputs/gate_census")
    args = ap.parse_args()

    from esgenie.dart_client import load_report
    from esgenie.embeddings import embedding_backend
    from esgenie.layer2_rag import get_hybrid_rag

    print(f"임베딩 백엔드: {embedding_backend()}\n")
    rag = get_hybrid_rag()

    rows: list[dict] = []
    for ticker in args.tickers:
        report = load_report(ticker)
        corp = rag.build_corp_index(report)
        for area in args.areas:
            ctx = rag.retrieve_for_area(area, k=5, corp=corp)
            dec = ctx.retrieval_decision
            top_text = ctx.corp_hits[0][0].text if ctx.corp_hits else ""
            row = {
                "ticker": ticker,
                "corp": getattr(report, "corp_name", "") or ticker,
                "area": area,
                "decision": getattr(dec, "decision", "GATE_OFF"),
                "tier": getattr(dec, "tier", None),
                "top1_score": round(float(getattr(dec, "top1_score", 0.0)), 4),
                "hard_fails": list(getattr(dec, "hard_fails", []) or []),
                "soft_flags": list(getattr(dec, "soft_flags", []) or []),
                "field_coverage": dict(getattr(dec, "field_coverage", {}) or {}),
                "n_corp_hits": len(ctx.corp_hits),
                "top1_text": top_text[:160],
            }
            rows.append(row)
            mark = "OK " if _is_accept(row["decision"]) else "차단"
            print(f"[{mark}] {ticker} {area} | tier={row['tier']} top1={row['top1_score']:.3f} "
                  f"hard={row['hard_fails']}")
            print(f"       top1청크: {top_text[:110]!r}\n")

    total = len(rows)
    accepted = sum(1 for r in rows if _is_accept(r["decision"]))
    hard_counter = Counter(f for r in rows for f in r["hard_fails"])
    soft_counter = Counter(f for r in rows for f in r["soft_flags"])
    cov_counter = Counter(
        k for r in rows for k, v in r["field_coverage"].items() if not v
    )

    lines = [
        "# 검색 게이트 전수 조사",
        "",
        f"- 대상: {total}개 조합 ({len(args.tickers)}개사 × {len(args.areas)}영역)",
        f"- **통과 {accepted}/{total} ({accepted / total * 100:.1f}%)** — 나머지는 섹션이 비어서 생성됨",
        "",
        "## hard_fail 사유별 빈도 (생성 차단의 직접 원인)",
        "",
        "| 사유 | 건수 |",
        "|---|---|",
    ]
    lines += [f"| {k} | {v} |" for k, v in hard_counter.most_common()] or ["| (없음) | 0 |"]
    lines += ["", "## soft_flag 빈도", "", "| 사유 | 건수 |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in soft_counter.most_common()] or ["| (없음) | 0 |"]
    lines += ["", "## 미충족 필드 빈도", "", "| 필드 | 미충족 |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in cov_counter.most_common()] or ["| (없음) | 0 |"]
    lines += ["", "## 조합별 상세", "", "| 기업 | 영역 | 판정 | tier | top1 | hard_fails |", "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(
            f"| {r['corp']} | {r['area']} | {r['decision']} | {r['tier']} | "
            f"{r['top1_score']:.3f} | {', '.join(r['hard_fails']) or '-'} |"
        )

    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    (out / f"gate_census_{stamp}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / f"gate_census_{stamp}.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n".join(lines))
    print(f"\n저장: {out}/gate_census_{stamp}.md")


if __name__ == "__main__":
    main()
