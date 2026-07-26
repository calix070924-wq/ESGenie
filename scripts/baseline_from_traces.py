"""Phase 0 — audit trace 기반 보고서 본문 기준선(before) 측정.

배치가 --export-report 없이 돌아 보고서 .md가 없어도, L5 audit trace에는
문장 단위 본문(sentence_text)·항목 매핑·evidence 연결이 남는다. 이를 집계해
L2 본문 형식 개편(Phase 1)의 before 수치를 확정한다.

측정 지표 (영역 섹션별):
- 문장 수 / 정량 문장 수(숫자 포함) / 표 파이프('|') 포함 문장 수
- evidence 연결률: evidence_node_ids가 붙은 문장 비율
- 본문에 등장한 고유 K-ESG 항목 수 (지표 반영률의 분자 프록시)
- 오매핑 의심: 항목 코드 영역 접두가 섹션 영역과 다른 문장 수 (P-*는 공통이라 제외)
- 평균 리스크 / HITL 문장 수

사용:
    python scripts/baseline_from_traces.py                # outputs/의 전체 trace, (ticker,area)별 최신본
    python scripts/baseline_from_traces.py --since 20260716   # 해당 날짜 이후 trace만
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "outputs"

_FNAME = re.compile(r"audit_trace_(?P<ticker>[^_]+)_(?P<area>[ESG])_(?P<ts>\d{8}_\d{6})\.json$")
_HAS_NUMBER = re.compile(r"\d")


def _latest_traces(since: str | None) -> dict[tuple[str, str], Path]:
    """(ticker, area) → 최신 trace 경로."""
    best: dict[tuple[str, str], tuple[str, Path]] = {}
    for p in OUT_DIR.glob("audit_trace_*.json"):
        m = _FNAME.search(p.name)
        if not m:
            continue
        ts = m.group("ts")
        if since and ts < since:
            continue
        key = (m.group("ticker"), m.group("area"))
        if key not in best or ts > best[key][0]:
            best[key] = (ts, p)
    return {k: v[1] for k, v in sorted(best.items())}


def _analyze(path: Path, area: str) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    sents = d.get("sentences", [])
    items = {s.get("kesg_item_id") for s in sents if s.get("kesg_item_id")}
    mismapped = [
        s.get("kesg_item_id") for s in sents
        if s.get("kesg_item_id") and s["kesg_item_id"][0] not in (area, "P")
    ]
    return {
        "corp": d.get("corp_name"),
        "sentences": len(sents),
        "numeric_sentences": sum(1 for s in sents if _HAS_NUMBER.search(s.get("sentence_text", ""))),
        "table_pipe_sentences": sum(1 for s in sents if "|" in s.get("sentence_text", "")),
        "evidence_linked": sum(1 for s in sents if s.get("evidence_node_ids")),
        "unique_items": len(items),
        "mismapped": mismapped,
        "hitl": sum(1 for s in sents if s.get("hitl_status") not in (None, "", "none", "ok", False)),
        "avg_risk": d.get("summary", {}).get("avg_risk_score"),
        "high_risk_axes": d.get("summary", {}).get("high_risk_axes", []),
        "trace": path.name,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="audit trace 기반 본문 기준선 측정")
    ap.add_argument("--since", default=None, help="YYYYMMDD — 이 날짜 이후 trace만 (파일명 기준)")
    args = ap.parse_args()

    traces = _latest_traces(args.since)
    if not traces:
        print("대상 trace 없음")
        return

    rows: dict[str, dict[str, dict]] = defaultdict(dict)  # ticker → area → row
    for (ticker, area), path in traces.items():
        rows[ticker][area] = _analyze(path, area)

    print(f"\n{'='*100}")
    print(f"{'기업':<14}{'영역':<4}{'문장':>4}{'정량':>4}{'표':>4}{'evid연결':>8}{'항목수':>6}{'오매핑':>6}{'HITL':>5}{'avg risk':>9}  비고")
    print("-" * 100)
    summary: dict[str, dict] = {}
    for ticker, areas in rows.items():
        for area, r in areas.items():
            corp = (r["corp"] or ticker)[:12]
            note = ",".join(r["mismapped"][:3])
            print(f"{corp:<14}{area:<4}{r['sentences']:>4}{r['numeric_sentences']:>4}"
                  f"{r['table_pipe_sentences']:>4}{r['evidence_linked']:>8}{r['unique_items']:>6}"
                  f"{len(r['mismapped']):>6}{r['hitl']:>5}{str(r['avg_risk']):>9}  {note}")
        summary[ticker] = areas

    # 전체 집계 — 개편안 §2 완료 기준과 대응
    all_rows = [r for a in rows.values() for r in a.values()]
    n_sent = sum(r["sentences"] for r in all_rows)
    print("-" * 100)
    print(f"섹션 {len(all_rows)}개 · 총 문장 {n_sent} · "
          f"표 포함 섹션 {sum(1 for r in all_rows if r['table_pipe_sentences'])}/{len(all_rows)} · "
          f"evidence 연결 문장 {sum(r['evidence_linked'] for r in all_rows)}/{n_sent} · "
          f"오매핑 문장 {sum(len(r['mismapped']) for r in all_rows)}")

    out = OUT_DIR / "baseline_from_traces.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {out}")


if __name__ == "__main__":
    main()
