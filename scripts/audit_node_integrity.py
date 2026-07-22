"""노드 정합성 전수 감사 — L0 오염 실태 census (작업0-1).

evidence graph를 실제로 구축해(OCR 라이브 추출 필요) 노드별 정합성 플래그를 전수로
집계한다. audit_trace는 metric_hint를 해시해 value/unit을 복원할 수 없어(오프라인 한계),
이 스크립트는 라이브 실행으로 그래프를 새로 만든다.

플래그:
  - unit_mismatch : kesg_items.unit 대비 노드 unit 불일치(_unit_suspect 재사용)
  - future_period : period > report_year (미래 전망 노드)
  - guard_term    : metric_hint에 '목표·전망·감축량·전환량·누적·집약도·원단위' 등
  - footnote_like : metric_hint 말미 '\\d)' (각주 마커를 값/라벨로 오파싱)

출력:
  - 노드별 CSV: outputs/node_integrity/{ticker}_nodes.csv
  - 집계: origin별 노드 수, 플래그별 건수·비율, 플래그 2개+ 겹치는 노드 수
  - before/after 비교용 JSON: outputs/node_integrity/summary_{tag}.json

사용(라이브 — 과금):
    python3 scripts/audit_node_integrity.py --tag before          # 5개사 전부
    python3 scripts/audit_node_integrity.py --tickers 012330 --tag before
    python3 scripts/audit_node_integrity.py --tag after            # 수정 후 재실행
    python3 scripts/audit_node_integrity.py --diff before after    # 두 census 비교표

주의: --diff는 라이브 불필요(기존 summary JSON 비교). census 생성만 라이브.
DART 구조화 경로(origin=dart)는 대조군이라 집계엔 포함하되 플래그 해석은 ocr_* 중심.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MANIFEST = ROOT / "data" / "real_reports" / "manifest.json"
OUT_DIR = ROOT / "outputs" / "node_integrity"

# 가드 어휘 — 설계안 §G1과 동일(실적 총량이 아닌 값). census 판정용 사본.
_GUARD_TERMS = (
    "목표", "전망", "계획", "예정", "로드맵", "선언",
    "감축량", "전환량", "절감량", "누적",
    "원단위", "집약도", "intensity",
)
_FOOTNOTE_RE = re.compile(r"\d\)\s*$")


def _hint_from_node(node) -> str:
    """노드 raw_text('{hint}={value}{unit} (file)')에서 metric_hint 복원."""
    raw = getattr(node, "raw_text", "") or ""
    # 형식: "에너지 사용량=9075.0TJ (mobis.pdf)" → '=' 앞이 hint
    return raw.split("=", 1)[0].strip() if "=" in raw else raw.strip()


def _flags_for_node(node, report_year: int) -> list[str]:
    """노드 하나의 정합성 위반 플래그 목록."""
    from esgenie.knowledge.kesg_items import by_code
    from esgenie.layer1_extract import _unit_suspect

    flags: list[str] = []
    hint = _hint_from_node(node)
    h = hint.replace(" ", "")

    # unit_mismatch — 확정 코드(K-ESG)의 정의 단위 대비. 미해소 노드(metric=hint)는 제외.
    code = node.metric
    if re.match(r"^[A-Z]-\d+-\d+$", code or ""):
        item = by_code(code)
        if item and item.unit and _unit_suspect(node.unit, item.unit):
            flags.append("unit_mismatch")
    if isinstance(node.period, int) and node.period > report_year:
        flags.append("future_period")
    if any(g in h for g in _GUARD_TERMS):
        flags.append("guard_term")
    if _FOOTNOTE_RE.search(hint):
        flags.append("footnote_like")
    return flags


def _build_graph(ticker: str, entry: dict):
    """한 회사의 evidence graph를 라이브로 구축(OCR 추출 포함)."""
    from esgenie.pipeline import _collect_ocr_extractions, load_report
    from esgenie.ssot import evidence_graph as eg

    pdf = ROOT / entry["pdf"]
    corp_code = entry.get("corp_code", ticker)
    report_year = 2025
    report = load_report(corp_code, report_year=report_year)
    extractions = _collect_ocr_extractions({pdf.name: str(pdf)}, survey_answers=None)
    graph = eg.build_unified_graph(
        report, extractions,
        corp_code=corp_code, corp_name=entry["name"], report_year=report_year,
    )
    return graph, report_year


def _census_company(ticker: str, entry: dict) -> dict:
    """한 회사 노드 전수 census — CSV 저장 + 집계 dict 반환."""
    graph, report_year = _build_graph(ticker, entry)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    rows = []
    for node in graph.nodes.values():
        flags = _flags_for_node(node, report_year)
        rows.append({
            "corp": entry["name"],
            "node_id": node.id,
            "metric": node.metric,
            "metric_hint": _hint_from_node(node),
            "value": node.value,
            "unit": node.unit,
            "period": node.period,
            "origin": node.origin,
            "confidence": node.confidence,
            "flags": "|".join(flags),
        })

    csv_path = OUT_DIR / f"{ticker}_nodes.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else
                           ["corp", "node_id", "metric", "metric_hint", "value",
                            "unit", "period", "origin", "confidence", "flags"])
        w.writeheader()
        w.writerows(rows)

    return _aggregate(entry["name"], rows)


def _aggregate(corp: str, rows: list[dict]) -> dict:
    """노드 행 목록 → 집계(origin별·플래그별·다중플래그)."""
    from collections import Counter
    origin_counts = Counter(r["origin"] for r in rows)
    flag_counts = Counter()
    multi = 0
    for r in rows:
        fs = [x for x in r["flags"].split("|") if x]
        for x in fs:
            flag_counts[x] += 1
        if len(fs) >= 2:
            multi += 1
    total = len(rows)
    return {
        "corp": corp,
        "total_nodes": total,
        "origin": dict(origin_counts),
        "flags": dict(flag_counts),
        "flag_pct": {k: round(100 * v / total, 1) for k, v in flag_counts.items()} if total else {},
        "multi_flag_nodes": multi,
    }


def _print_summary(summaries: list[dict], tag: str) -> None:
    """집계 표 출력 + summary JSON 저장."""
    print(f"\n{'='*72}\n노드 정합성 census [tag={tag}]\n{'='*72}")
    agg_total = 0
    agg_flags: dict[str, int] = {}
    agg_origin: dict[str, int] = {}
    for s in summaries:
        print(f"\n[{s['corp']}] 노드 {s['total_nodes']}개")
        print(f"  origin: {s['origin']}")
        print(f"  flags : {s['flags']}  (%: {s['flag_pct']})")
        print(f"  다중플래그(2+): {s['multi_flag_nodes']}")
        agg_total += s["total_nodes"]
        for k, v in s["flags"].items():
            agg_flags[k] = agg_flags.get(k, 0) + v
        for k, v in s["origin"].items():
            agg_origin[k] = agg_origin.get(k, 0) + v

    print(f"\n{'-'*72}\n종합: 노드 {agg_total}개")
    print(f"  origin 합계: {agg_origin}")
    print(f"  flag 합계  : {agg_flags}")
    if agg_total:
        print(f"  flag 비율  : { {k: round(100*v/agg_total,1) for k,v in agg_flags.items()} }")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"summary_{tag}.json"
    out.write_text(json.dumps({
        "tag": tag, "total_nodes": agg_total, "origin": agg_origin,
        "flags": agg_flags, "companies": summaries,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {out}")


def _diff(tag_before: str, tag_after: str) -> None:
    """두 census summary JSON 비교표(라이브 불필요)."""
    b = json.loads((OUT_DIR / f"summary_{tag_before}.json").read_text(encoding="utf-8"))
    a = json.loads((OUT_DIR / f"summary_{tag_after}.json").read_text(encoding="utf-8"))
    print(f"\n{'='*72}\n플래그 건수 변화: {tag_before} → {tag_after}\n{'='*72}")
    print(f"{'플래그':<16}{tag_before:>10}{tag_after:>10}{'Δ':>8}")
    keys = sorted(set(b["flags"]) | set(a["flags"]))
    for k in keys:
        bv, av = b["flags"].get(k, 0), a["flags"].get(k, 0)
        print(f"{k:<16}{bv:>10}{av:>10}{av-bv:>+8}")
    print(f"{'총 노드':<16}{b['total_nodes']:>10}{a['total_nodes']:>10}{a['total_nodes']-b['total_nodes']:>+8}")


def main() -> None:
    ap = argparse.ArgumentParser(description="노드 정합성 전수 감사")
    ap.add_argument("--tickers", nargs="+", help="대상 ticker(기본: manifest 전체)")
    ap.add_argument("--tag", default="before", help="census 태그(before/after 등)")
    ap.add_argument("--diff", nargs=2, metavar=("BEFORE", "AFTER"),
                    help="두 census summary 비교(라이브 불필요)")
    args = ap.parse_args()

    if args.diff:
        _diff(*args.diff)
        return

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    targets = manifest if not args.tickers else [
        e for e in manifest if e["ticker"] in args.tickers]

    summaries = []
    for entry in targets:
        print(f"[census] {entry['name']} ({entry['ticker']}) 그래프 구축 중...")
        summaries.append(_census_company(entry["ticker"], entry))
    _print_summary(summaries, args.tag)


if __name__ == "__main__":
    main()
