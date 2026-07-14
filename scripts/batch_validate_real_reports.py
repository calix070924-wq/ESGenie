"""실보고서 배치 검증 — n=1(한울정밀) 일반화 확인용.

data/real_reports/manifest.json에 등록된 회사별로
지속가능경영보고서 PDF를 증빙으로 주입해 L0~L6 풀 파이프라인을 완주시키고,
회사별 결과(커버리지·D6·섹션 밴드)와 예외를 outputs/에 요약 저장한다.

목적은 점수가 아니라 **깨짐 찾기**다 — 예외가 나도 다음 회사로 계속 진행하고,
traceback을 그대로 기록한다.

사용:
    # 전체 (PDF가 준비된 회사만 실행됨)
    python scripts/batch_validate_real_reports.py

    # 스모크 테스트 — 1개사만
    python scripts/batch_validate_real_reports.py --only 009150

    # LLM judge 포함 (비용 발생)
    python scripts/batch_validate_real_reports.py --llm-judge
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import traceback
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # repo 루트에서 `esgenie` 패키지 import (scripts/ 컨벤션)
MANIFEST = ROOT / "data" / "real_reports" / "manifest.json"


def _run_one(entry: dict, *, llm_judge: bool, export_report: bool) -> dict:
    from esgenie import pipeline

    pdf = ROOT / entry["pdf"]
    row: dict = {
        "ticker": entry["ticker"],
        "name": entry["name"],
        "sector": entry["sector"],
        "pdf": str(pdf),
        "status": "ok",
    }
    t0 = time.monotonic()
    try:
        output = pipeline.run(
            corp_code=entry["ticker"],
            evidence_files={pdf.name: str(pdf)},
            llm_judge=llm_judge,
            export_report=export_report,
        )
        ext = output.extraction
        row["coverage_pct"] = round(ext.coverage_pct, 1) if ext else None
        row["profile"] = ext.profile_label if ext else None
        row["graph_nodes"] = len(output.evidence_graph.nodes)
        row["graph_edges"] = len(output.evidence_graph.edges)
        row["ocr_extractions"] = len(output.ocr_extractions)
        if output.disclosure is not None:
            row["d6_score"] = round(output.disclosure.score, 2)
            row["d6_level"] = output.disclosure.level
        row["sections"] = {
            area: {"score": round(v.final_score, 1), "band": v.final_band,
                   "hitl": v.hitl_required}
            for area, v in output.sections.items()
        }
        row["industry_module"] = output.industry_module_key
    except Exception:
        row["status"] = "error"
        row["traceback"] = traceback.format_exc()
    row["elapsed_sec"] = round(time.monotonic() - t0, 1)
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="실보고서 배치 검증")
    parser.add_argument("--only", default=None, help="특정 ticker만 실행 (스모크 테스트)")
    parser.add_argument("--llm-judge", action="store_true", help="룰+LLM 하이브리드 검출 (비용 발생)")
    parser.add_argument("--export-report", action="store_true", help="회사별 통합 보고서(.md/.pdf) 생성")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if args.only:
        manifest = [e for e in manifest if e["ticker"] == args.only]
        if not manifest:
            raise SystemExit(f"manifest에 없는 ticker: {args.only}")

    results: list[dict] = []
    for entry in manifest:
        pdf = ROOT / entry["pdf"]
        if not pdf.exists():
            print(f"[skip] {entry['name']} — PDF 없음: {entry['pdf']}")
            results.append({**entry, "status": "skipped", "reason": "pdf_missing"})
            continue
        print(f"[run ] {entry['name']} ({entry['ticker']}, {entry['sector']}) …")
        row = _run_one(entry, llm_judge=args.llm_judge, export_report=args.export_report)
        results.append(row)
        mark = "OK" if row["status"] == "ok" else "ERROR"
        print(f"[{mark:5s}] {entry['name']} — {row.get('elapsed_sec', '?')}s"
              f" | 커버리지 {row.get('coverage_pct', '-')}%")

    out_dir = ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"batch_validation_{date.today().isoformat()}.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    # 요약 테이블
    print("\n" + "=" * 72)
    print(f"{'회사':<10} {'업종':<8} {'상태':<8} {'커버리지':>8} {'D6':>6} {'소요(s)':>8}")
    print("-" * 72)
    for r in results:
        cov = f"{r['coverage_pct']}%" if r.get("coverage_pct") is not None else "-"
        d6 = str(r.get("d6_score", "-"))
        print(f"{r['name']:<10} {r.get('sector', '-'):<8} {r['status']:<8}"
              f" {cov:>8} {d6:>6} {str(r.get('elapsed_sec', '-')):>8}")
    n_err = sum(1 for r in results if r["status"] == "error")
    print("-" * 72)
    print(f"결과 저장: {out_path}  (에러 {n_err}건 — traceback은 JSON 참조)")


if __name__ == "__main__":
    main()
