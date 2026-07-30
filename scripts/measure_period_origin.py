"""작업0 계측 — period 폴백의 실제 건수와 원인을 오프라인으로 확정한다.

라이브 호출 없음. `data/_cache/ocr/*.json`(LLM 원본 응답 캐시)과 기존 원장 덤프
(`outputs/lp_*_E.json`)를 조인해, 노드의 `period == report_year`가
  (a) 진짜 2025 실적인지  (b) `_normalize_period`의 폴백인지
를 `period_raw` 원문으로 가른다. `_normalize_period`에 계측을 심는 것과 동등하되
파이프라인 재실행이 필요 없다(캐시가 LLM 응답을 그대로 담기 때문).
"""
from __future__ import annotations

import collections
import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
YEAR_RE = re.compile(r"(20\d{2})")

# 회사별 (원장 덤프, OCR source_file, 표시명)
TARGETS = {
    "012330": ("outputs/ledger_provenance_012330_v3.json", "012330_mobis_2025.pdf", "현대모비스"),
    "009150": ("outputs/lp_009150_E.json", "009150_samsungsem_2025.pdf", "삼성전기"),
    "051910": ("outputs/lp_051910_E.json", "051910_lgchem_2025.pdf", "LG화학"),
    "035420": ("outputs/lp_035420_E.json", "035420_naver_2025.pdf", "NAVER"),
    "055550": ("outputs/lp_055550_E.json", "055550_shinhan_2025.pdf", "신한지주"),
}


def _fval(m: dict) -> float | None:
    try:
        return float(m.get("value"))
    except (TypeError, ValueError):
        return None


def build_cache_index(cache_dir: Path):
    """(source_file, hint, value, unit) → [period_raw] 와 (source_file, hint) → [period_raw]."""
    exact: dict[tuple, list[str]] = collections.defaultdict(list)
    by_hint: dict[tuple, list[str]] = collections.defaultdict(list)
    for path in glob.glob(str(cache_dir / "*.json")):
        entry = json.loads(Path(path).read_text(encoding="utf-8"))
        src = entry.get("meta", {}).get("source_file", "?")
        for m in (entry.get("response") or {}).get("metrics", []):
            val = _fval(m)
            if val is None:
                continue
            hint = str(m.get("metric_hint", ""))
            raw = str(m.get("period") or "")
            unit = str(m.get("unit") or "")
            exact[(src, hint, val, unit)].append(raw)
            by_hint[(src, hint)].append(raw)
    return exact, by_hint


def classify(raws: list[str], report_year: int) -> str:
    """period_raw 목록 → 'fallback' | 'real' | 'ambiguous'."""
    years = {int(m.group(1)) for r in raws if (m := YEAR_RE.search(r))}
    if not years:
        return "fallback"          # 원문에 연도 없음 → report_year 폴백이 확실
    if years == {report_year}:
        return "real"              # 원문이 report_year를 명시
    return "ambiguous"             # 여러 연도 or 다른 연도 (조인 다대일)


def main() -> int:
    exact, by_hint = build_cache_index(ROOT / "data" / "_cache" / "ocr")
    if not exact:
        print("캐시 비어 있음 — data/_cache/ocr 확인", file=sys.stderr)
        return 1

    print(f"{'회사':10s} {'OCR노드':>7s} {'p=ry':>5s} {'ry비율':>7s} | "
          f"{'폴백확정':>8s} {'진짜ry':>7s} {'모호':>5s} | {'폴백/전체':>9s}")
    reasons: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for ticker, (dump, src, name) in TARGETS.items():
        path = ROOT / dump
        if not path.exists():
            print(f"{name:10s} (덤프 없음: {dump})")
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        report_year = data["report_year"]
        total = at_ry = fallback = real = ambiguous = 0
        seen: set[str] = set()
        for row in data["rows"]:
            for n in row.get("nodes", []):
                nid = n.get("id")
                if nid in seen or not str(n.get("origin", "")).startswith("ocr"):
                    continue
                seen.add(nid)
                total += 1
                if n.get("period") != report_year:
                    continue
                at_ry += 1
                key = (src, n.get("hint", ""), float(n["value"]), n.get("unit", ""))
                raws = exact.get(key) or by_hint.get((src, n.get("hint", "")))
                if not raws:
                    ambiguous += 1
                    reasons[name]["캐시 미매칭"] += 1
                    continue
                verdict = classify(raws, report_year)
                if verdict == "fallback":
                    fallback += 1
                    reasons[name][f"폴백 period_raw={sorted(set(raws))[:2]}"] += 1
                elif verdict == "real":
                    real += 1
                else:
                    ambiguous += 1
                    reasons[name]["연도 있으나 ry와 불일치(다대일 조인)"] += 1
        print(f"{name:10s} {total:7d} {at_ry:5d} {100*at_ry/max(total,1):6.1f}% | "
              f"{fallback:8d} {real:7d} {ambiguous:5d} | {100*fallback/max(total,1):8.1f}%")

    print("\n[period=report_year 내역]")
    for name, counter in reasons.items():
        print(f"  --- {name} ---")
        for reason, cnt in counter.most_common(8):
            print(f"      {cnt:4d}  {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
