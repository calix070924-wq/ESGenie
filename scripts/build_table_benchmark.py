# -*- coding: utf-8 -*-
"""표 게이트 평가셋 후보 생성 + 리뷰 워크북 빌드.

사용:
  PYTHONPATH=. python3 scripts/build_table_benchmark.py
  PYTHONPATH=. python3 scripts/build_table_benchmark.py --split dev --glob '시연증빙세트_한울정밀공업/*.pdf'
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from esgenie.ssot import ocr_router, ocr_table_gate
from esgenie.ssot.table_benchmark import (
    build_review_workbook,
    extract_table_cases,
    write_cases_jsonl,
    write_summary_json,
)


DEFAULT_GLOBS = [
    "시연증빙세트_한울정밀공업/*.pdf",
    "data/test_docs/*.pdf",
]


def _collect_paths(patterns: list[str]) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for pattern in patterns:
        for path in sorted(ROOT.glob(pattern)):
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            out.append(path)
    return out


def build_extractions(paths: list[Path]) -> tuple[list[ocr_router.OcrExtraction], dict[str, str]]:
    extractions: list[ocr_router.OcrExtraction] = []
    source_paths: dict[str, str] = {}
    for path in paths:
        decision = ocr_router.route_document(str(path))
        ext = ocr_router.extract_document(str(path), decision)
        ext.source_file = path.name
        ocr_table_gate.apply_table_gate(ext, tier=0)
        extractions.append(ext)
        source_paths[path.name] = str(path)
    return extractions, source_paths


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", default="dev", help="케이스 split 라벨 (dev/test)")
    parser.add_argument("--glob", action="append", dest="globs", help="입력 PDF glob. 여러 번 사용 가능")
    parser.add_argument(
        "--out-dir",
        default=str(ROOT / "data" / "benchmark_tables"),
        help="평가셋 출력 디렉터리",
    )
    parser.add_argument(
        "--xlsx",
        default=str(ROOT / "outputs" / "benchmark_tables" / "review_candidates.xlsx"),
        help="리뷰 워크북 출력 경로",
    )
    args = parser.parse_args()

    patterns = args.globs or DEFAULT_GLOBS
    paths = _collect_paths(patterns)
    if not paths:
        raise SystemExit(f"[!] 입력 파일을 찾지 못했습니다: {patterns}")

    out_dir = Path(args.out_dir)
    raw_dir = out_dir / "raw"
    extractions, source_paths = build_extractions(paths)
    cases, summary = extract_table_cases(
        extractions,
        split=args.split,
        raw_dir=raw_dir,
        source_paths=source_paths,
    )
    write_cases_jsonl(cases, out_dir / "cases.jsonl")
    write_summary_json(summary, out_dir / "summary.json")
    build_review_workbook(cases, args.xlsx)

    print(f"[ok] scanned files : {len(paths)}")
    print(f"[ok] candidate cases: {len(cases)}")
    print(f"[ok] cases.jsonl    : {out_dir / 'cases.jsonl'}")
    print(f"[ok] summary.json   : {out_dir / 'summary.json'}")
    print(f"[ok] review xlsx    : {Path(args.xlsx)}")


if __name__ == "__main__":
    main()
