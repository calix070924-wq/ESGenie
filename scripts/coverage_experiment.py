"""커버리지 100% 실험 — 커버리지를 다 채우면 보고서가 좋아지는가?

같은 회사를 두 번 돌려 비교한다:
  1. baseline — 실제 데이터 그대로 (현재 커버리지)
  2. full100  — 프로파일 내 누락 항목을 합성 값으로 채워 커버리지 100% 강제

합성 값은 kesg_data에 note="[합성값] ..."으로 명시 주입되므로 실제 공시와 혼동되지
않는다. 주입은 L1 추출 이전(load_report 직후)에 이뤄져 D6·ISSB·L2 초안 생성 프롬프트
(report.to_context_dict())까지 전부 반영된다 — 즉 "데이터가 다 있으면 초안이 정말
좋아지는가"를 그대로 검증한다.

비교 항목: 커버리지, D6 의심도, ISSB 누락, 영역별 위험도/본문 길이,
통합 보고서(.md) 블록 구성·분량.

사용:
    # 기본 (삼성전자 corp_code, mock LLM — 구조 비교용)
    python scripts/coverage_experiment.py

    # 라이브 LLM으로 본문 품질까지 비교 (키 필요, 비용 발생)
    python scripts/coverage_experiment.py --ticker 00126380 --report-year 2024

    # full100만 다시 실행 (baseline 결과가 이미 있을 때)
    python scripts/coverage_experiment.py --skip-baseline
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # repo 루트에서 `esgenie` 패키지 import (scripts/ 컨벤션)

OUT_ROOT = ROOT / "outputs" / "coverage_experiment"

SYNTH_NOTE = "[합성값] 커버리지 100% 실험용 주입 — 실제 공시 아님"


# ---------------------------------------------------------------------------
# 합성 값 주입
# ---------------------------------------------------------------------------

def _synthetic_entry(item: Any) -> dict[str, Any]:
    """항목 유형에 맞는 그럴듯한 합성 entry 생성."""
    if item.data_type == "정량":
        return {"value": 100.0, "unit": item.unit or "", "note": SYNTH_NOTE}
    # 정성/혼합 — 짧은 정책 서술
    return {
        "value": f"{item.name} 관련 정책 수립·운영 중",
        "unit": "",
        "note": SYNTH_NOTE,
    }


def fill_to_full_coverage(report: Any, profile: str) -> tuple[Any, list[str]]:
    """프로파일 내 누락 항목을 kesg_data에 합성 주입. 주입된 코드 목록 반환."""
    from esgenie.knowledge.kesg_items import detect_profile, items_for_profile

    prof = profile or detect_profile(report.corp_code)
    injected: list[str] = []
    for item in items_for_profile(prof):
        if item.code not in report.kesg_data:
            report.kesg_data[item.code] = _synthetic_entry(item)
            injected.append(item.code)
    return report, injected


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------

def _run(variant: str, args: argparse.Namespace) -> dict[str, Any]:
    """파이프라인 1회 실행 후 요약 dict 반환. variant: 'baseline' | 'full100'."""
    from esgenie import pipeline

    injected: list[str] = []

    if variant == "full100":
        # load_report를 감싸 L1 이전에 합성 주입 — 하위 레이어 전부에 반영된다.
        real_load = pipeline.load_report

        def _patched(corp_code: str, report_year: int | None = None):
            report = real_load(corp_code, report_year=report_year)
            _, codes = fill_to_full_coverage(report, args.profile)
            injected.extend(codes)
            return report

        pipeline.load_report = _patched  # type: ignore[assignment]

    try:
        output = pipeline.run(
            corp_code=args.ticker,
            report_year=args.report_year,
            llm_judge=args.llm_judge,
            profile=args.profile,
            export_report=True,
            export_root=OUT_ROOT / variant,
            save_traces=False,
        )
    finally:
        if variant == "full100":
            pipeline.load_report = real_load  # type: ignore[assignment]

    ext = output.extraction
    row: dict[str, Any] = {
        "variant": variant,
        "injected_codes": injected,
        "coverage_pct": round(ext.coverage_pct, 1) if ext else None,
        "profile": ext.profile_label if ext else None,
        "missing_after": len(ext.missing) if ext else None,
    }
    if output.disclosure is not None:
        d6 = output.disclosure
        row["d6"] = {
            "score": round(d6.score, 2),
            "level": d6.level,
            "orphan_ratios": len(d6.orphan_ratios),
            "omitted_sensitive": len(d6.omitted_sensitive),
        }
    if output.issb_gap is not None:
        row["issb_missing"] = output.issb_gap.in_profile_missing
    row["sections"] = {
        area: {
            "score": round(v.final_score, 1),
            "band": v.final_band,
            "hitl": v.hitl_required,
            "text_chars": len(v.final_text),
        }
        for area, v in output.sections.items()
    }
    row["policy_drafts"] = len(output.policy_drafts or {})

    md_path = output.export_paths.get("report_md")
    if md_path and Path(md_path).exists():
        md = Path(md_path).read_text(encoding="utf-8")
        row["report_md"] = md_path
        row["report_pdf"] = output.export_paths.get("report_pdf")
        row["report_chars"] = len(md)
        row["report_headings"] = re.findall(r"^## (.+)$", md, flags=re.M)
    return row


# ---------------------------------------------------------------------------
# 비교 출력
# ---------------------------------------------------------------------------

def _print_comparison(base: dict[str, Any] | None, full: dict[str, Any]) -> None:
    def line(label: str, get) -> None:
        b = get(base) if base else "—"
        f = get(full)
        mark = "  ←" if b != f and base else ""
        print(f"  {label:<24} {str(b):>18} → {str(f):<18}{mark}")

    print("\n" + "=" * 70)
    print("커버리지 100% 실험 결과  (baseline → full100)")
    print("=" * 70)
    line("커버리지 %", lambda r: r.get("coverage_pct"))
    line("누락 항목 수", lambda r: r.get("missing_after"))
    line("D6 의심도", lambda r: f'{r["d6"]["score"]} ({r["d6"]["level"]})' if r.get("d6") else "—")
    line("D6 고아비율/민감누락", lambda r: f'{r["d6"]["orphan_ratios"]}/{r["d6"]["omitted_sensitive"]}' if r.get("d6") else "—")
    line("ISSB 누락", lambda r: r.get("issb_missing"))
    line("정책 보완 초안 수", lambda r: r.get("policy_drafts"))
    for area in ("E", "S", "G"):
        line(
            f"{area} 위험도/본문자수",
            lambda r, a=area: (
                f'{r["sections"][a]["score"]} ({r["sections"][a]["band"]}) / {r["sections"][a]["text_chars"]}자'
                if a in r.get("sections", {}) else "—"
            ),
        )
    line("보고서 총 분량(자)", lambda r: r.get("report_chars"))

    print("\n  [보고서 블록 구성]")
    b_heads = set(base.get("report_headings", [])) if base else set()
    f_heads = set(full.get("report_headings", []))
    for h in sorted(b_heads | f_heads):
        tag = "양쪽" if h in b_heads and h in f_heads else ("baseline만" if h in b_heads else "full100만")
        print(f"    - {h}  [{tag}]")

    if full.get("injected_codes"):
        print(f"\n  주입된 합성 항목 {len(full['injected_codes'])}개: {', '.join(full['injected_codes'])}")
    for r in (base, full):
        if r and r.get("report_md"):
            print(f"\n  [{r['variant']}] 보고서: {r['report_md']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="커버리지 100% 실험")
    parser.add_argument("--ticker", default="00126380",
                        help="DART corp_code 8자리 (기본: 삼성전자 00126380)")
    parser.add_argument("--report-year", type=int, default=None)
    parser.add_argument("--profile", choices=["sme", "full"], default=None,
                        help="K-ESG 프로파일 (기본: 자동)")
    parser.add_argument("--llm-judge", action="store_true", help="룰+LLM 하이브리드 검출 (비용 발생)")
    parser.add_argument("--skip-baseline", action="store_true",
                        help="baseline 생략, 기존 summary.json의 baseline과 비교")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    summary_path = OUT_ROOT / "summary.json"

    base: dict[str, Any] | None = None
    if args.skip_baseline and summary_path.exists():
        prev = json.loads(summary_path.read_text(encoding="utf-8"))
        base = prev.get("baseline")
        print("[baseline] 기존 summary.json 결과 재사용")
    elif not args.skip_baseline:
        print("\n[1/2] baseline 실행 (실제 데이터 그대로)...")
        base = _run("baseline", args)

    print("\n[2/2] full100 실행 (누락 항목 합성 주입)...")
    full = _run("full100", args)

    if full.get("coverage_pct") is not None and full["coverage_pct"] < 100.0:
        print(f"\n⚠ full100인데 커버리지가 {full['coverage_pct']}%에 그침 — "
              "주입 로직이 프로파일 분모와 어긋났을 가능성. injected_codes 확인 필요.")

    summary_path.write_text(
        json.dumps({"baseline": base, "full100": full}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _print_comparison(base, full)
    print(f"\n요약 저장: {summary_path}")


if __name__ == "__main__":
    main()
