"""본선 시연 케이스(한울정밀공업, 가상 협력사) 풀 파이프라인 리허설 러너.

`pipeline.py`의 CLI는 `--ticker`만 받아 **증빙 파일을 넘길 방법이 없다.** 그래서 이 케이스는
지금까지 Streamlit 업로드로만 돌았고, 리허설이 재현 가능하지 않았다. 이 스크립트가 그 자리를
메운다 — 증빙 폴더를 통째로 넘겨 L0~L6를 돌리고 시연 대사에 쓰는 숫자를 한 화면에 찍는다.

**비상장 경로도 빈 보고서를 통해 상장 경로와 같은 L1~L6를 탄다.** 대표노드 선정·단위
정규화·역할 판정뿐 아니라 D1/D2와 보고서 생성까지 단일 경로이므로, 그 전에 기록된
수치(K-ESG 53.6% · D6 62.7pp)와는 달라질 수 있다. **달라지면 컷시나리오의 대사도 함께 본다.**

사용:
    python3 scripts/run_demo_hanwool.py                      # 번호 01~20 전체, E S G
    python3 scripts/run_demo_hanwool.py --areas E            # 빠르게 E만
    python3 scripts/run_demo_hanwool.py --core-only          # README 7종만(시연 최소 세트)
    python3 scripts/run_demo_hanwool.py --export-report      # L6 보고서(.md/.pdf)까지
    python3 scripts/run_demo_hanwool.py --json out/demo.json # 기계 판독용 저장
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EVIDENCE_DIR = ROOT / "시연증빙세트_한울정밀공업"
# 폴더에는 후속 보강 규정까지 31개 PDF가 있지만 본선 세트는 번호 01~20이다.
FULL_PREFIXES = tuple(f"{i:02d}_" for i in range(1, 21))
# README가 '시연 시나리오 7종'으로 지정한 최소 세트.
CORE_PREFIXES = FULL_PREFIXES[:7]
# 앱과 같은 역할 분리: OEM SAQ는 증빙이 아니라 검증 대상인 협력사 자가주장이다.
CLAIM_PREFIXES = ("05_", "06_", "07_")

CORP_CODE = "SME001"          # 비상장 → DART 미조회(use_dart=False)
CORP_NAME = "한울정밀공업㈜"
INDUSTRY = "자동차 차체부품"
REPORT_YEAR = 2026


def collect_inputs(core_only: bool) -> tuple[dict[str, str], list[str], list[str]]:
    """본선 번호 세트 → (객관 증빙, SAQ 자가주장 경로, 전체 파일명)."""
    prefixes = CORE_PREFIXES if core_only else FULL_PREFIXES
    selected = [p for p in sorted(EVIDENCE_DIR.glob("*.pdf"))
                if p.name.startswith(prefixes)]
    evidence = {p.name: str(p) for p in selected if not p.name.startswith(CLAIM_PREFIXES)}
    claim_paths = [str(p) for p in selected if p.name.startswith(CLAIM_PREFIXES)]
    return evidence, claim_paths, [p.name for p in selected]


def _axis_payload(rv: Any) -> dict[str, dict[str, Any]]:
    if rv is None:
        return {}
    return {
        key: {
            "score": round(getattr(rv, key).score, 4),
            "evidence": list(getattr(rv, key).evidence),
            "detail": getattr(rv, key).detail,
        }
        for key in ("D1_numeric", "D2_modifier", "D3_semantic", "D5_timeseries")
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="한울정밀 시연 케이스 풀 리허설")
    ap.add_argument("--areas", nargs="+", default=["E", "S", "G"], choices=["E", "S", "G"])
    ap.add_argument("--core-only", action="store_true", help="README 7종만 사용")
    ap.add_argument("--export-report", action="store_true", help="L6 보고서(.md/.pdf) 생성")
    ap.add_argument("--json", dest="json_out", default="", help="결과 JSON 저장 경로")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    evidence, claim_paths, input_files = collect_inputs(args.core_only)
    if not input_files:
        raise SystemExit(f"증빙을 찾지 못했다: {EVIDENCE_DIR}")
    print(f"입력 {len(input_files)}개 (객관 증빙 {len(evidence)} · SAQ 자가주장 {len(claim_paths)})"
          f" · 영역 {'·'.join(args.areas)}")

    from esgenie import pipeline
    from esgenie import llm_cache
    from esgenie.knowledge.kesg_items import by_code, items_for_profile
    from esgenie.layer1_extract import evidence_coverage_pct
    from esgenie.ssot import ocr_cache
    from esgenie.supplychain import parse_saq_claims, respond_from_pipeline

    supplier_claims = parse_saq_claims(claim_paths)
    llm_cache.reset_stats()

    t0 = time.time()
    out = pipeline.run(
        corp_code=CORP_CODE,
        corp_name=CORP_NAME,
        industry=INDUSTRY,
        report_year=REPORT_YEAR,
        areas=args.areas,
        use_dart=False,               # 비상장 — 빈 보고서로 단일 L1~L6 경로 합류
        evidence_files=evidence,
        save_traces=True,
        export_report=args.export_report,
    )
    elapsed = time.time() - t0
    out.supplier_claims = supplier_claims
    out.supplier_claim_files = [Path(p).name for p in claim_paths]

    ext = out.extraction
    if ext is None:
        raise SystemExit("extraction이 비었다 — 증빙에서 노드가 안 나왔다")

    # ── 원장 ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print(f"L1 원장 — {CORP_NAME} · 프로파일 {ext.profile_label} · {elapsed/60:.1f}분")
    print("=" * 78)
    rows: list[dict[str, Any]] = []
    for code in sorted(ext.mapped):
        item = by_code(code)
        if item is None or item.area not in args.areas:
            continue
        e = ext.mapped[code]
        flags = ext.confidence_flags.get(code, [])
        rows.append({"code": code, "name": item.name, "value": e.get("value"),
                     "unit": e.get("unit"), "value_role": e.get("value_role"),
                     "source_tier": e.get("source_tier"), "flags": flags,
                     "note": e.get("note"),
                     "evidence_node_ids": e.get("evidence_node_ids", [])})
        print(f"  {code:7} {str(e.get('value'))[:16]:>18} {str(e.get('unit') or ''):<8}"
              f" [{e.get('value_role') or '-'}]"
              + (f"  ⚠{flags}" if flags else ""))

    # ── 커버리지 · D6 ───────────────────────────────────────────────────────
    prof = [i for i in items_for_profile(ext.profile) if i.area in args.areas]
    prof_codes = {i.code for i in prof}
    covered_count = sum(1 for code in ext.mapped if code in prof_codes)
    selected_coverage_pct = 100 * covered_count / max(len(prof), 1)
    evidence_cov_pct = evidence_coverage_pct(ext)
    cache_hits, cache_misses, cache_mode = ocr_cache.summarize(out.ocr_extractions)
    llm_cache_stats = llm_cache.stats()
    print("\n" + "-" * 78)
    print(f"커버리지: {covered_count}/{len(prof)} ({selected_coverage_pct:.1f}%)"
          f"  · 전체 프로파일 기준 {ext.coverage_pct:.1f}%")
    d6 = out.disclosure
    if d6 is not None:
        print(f"D6 선택적 공시: 점수 {d6.score:.4f} · 레벨 {d6.level}")
        print(f"   disclosure_states={d6.asymmetry.get('disclosure_states', {})}")
        print(f"   signal_a={d6.asymmetry.get('signal_a')}"
              f" · orphan_ratios={d6.asymmetry.get('orphan_ratios')}")
        for orphan in d6.orphan_ratios:
            print(f"      {orphan.ratio_code}: {orphan.detail}")
    print(f"증빙연결 커버리지: {evidence_cov_pct:.1f}%")
    print(f"OCR 캐시: mode={cache_mode} · hit {cache_hits} / miss {cache_misses}")
    print(f"LLM 캐시: mode={llm_cache_stats['mode']} · hit {llm_cache_stats['hits']} / "
          f"miss {llm_cache_stats['misses']} · live calls {llm_cache_stats['live_calls']}")

    # ── RBA42 · 폐기물 자가주장 충돌 ────────────────────────────────────────
    rba = respond_from_pipeline(out, "rba42", supplier_claims=supplier_claims)
    waste = next((a for a in rba.answers if a.qid == "RBA-C-4-E-6-2"), None)
    claim = supplier_claims.get("E-6-2")
    claim_value = float(claim.value) if claim is not None else None
    evidence_value = float(waste.value) if waste is not None and isinstance(waste.value, (int, float)) else None
    gap_pp = (round(abs(claim_value - evidence_value), 1)
              if claim_value is not None and evidence_value is not None else None)
    waste_conflict = {
        "qid": waste.qid if waste else "RBA-C-4-E-6-2",
        "status": waste.status if waste else "missing",
        "badge": waste.badge if waste else "",
        "claim_value": claim_value,
        "evidence_value": evidence_value,
        "gap_pp": gap_pp,
        "flags": list(waste.flags) if waste else [],
        "rationale": waste.rationale if waste else "문항 없음",
    }
    print(f"RBA42 자동응답 커버리지: {rba.coverage_pct:.1f}% "
          f"({sum(a.answered for a in rba.answers)}/{rba.denominator})")
    print(f"D6 폐기물 모순: {waste_conflict['badge'] or waste_conflict['status']}"
          f" · 자가신고 {claim_value}% ↔ 증빙 {evidence_value}%"
          f" · Δ{gap_pp}%p")
    for flag in waste_conflict["flags"]:
        print(f"   {flag}")

    # ── 위험도 ──────────────────────────────────────────────────────────────
    print("\n" + "-" * 78)
    section_rows: dict[str, dict[str, Any]] = {}
    for area, v in out.sections.items():
        rv = v.final.detection.risk_vector
        axes = _axis_payload(rv)
        step_scores = [step.detection.risk_score for step in v.steps]
        initial_score = step_scores[0]
        section_rows[area] = {
            "score": v.final_score,
            "initial_score": initial_score,
            "final_score": v.final_score,
            "score_delta": round(initial_score - v.final_score, 1),
            "step_scores": step_scores,
            "iterations_used": v.iterations_used,
            "band": v.final_band,
            "axes": axes,
            "converged": v.converged,
            "hitl_required": v.hitl_required,
        }
        axis_scores = {key: axis["score"] for key, axis in axes.items()}
        print(f"  {area}: 초안 {initial_score:.1f} → 최종 {v.final_score:.1f} "
              f"(하락폭 {initial_score - v.final_score:+.1f}, {v.iterations_used}회, "
              f"{v.final_band})  {axis_scores}")
    for area, tr in (out.trace_paths or {}).items():
        print(f"     trace[{area}] {tr}")
    if out.export_paths:
        for k, p in out.export_paths.items():
            print(f"     {k}: {p}")

    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "corp": CORP_NAME, "areas": args.areas, "elapsed_sec": round(elapsed, 1),
            "input_document_count": len(input_files), "input_files": input_files,
            "evidence_count": len(evidence), "supplier_claim_file_count": len(claim_paths),
            "supplier_claim_files": out.supplier_claim_files,
            "supplier_claims": {code: vars(value) for code, value in supplier_claims.items()},
            "profile": ext.profile,
            "coverage_pct": ext.coverage_pct,
            "selected_areas_coverage_pct": round(selected_coverage_pct, 1),
            "evidence_coverage_pct": round(evidence_cov_pct, 1),
            "ocr_cache": {"mode": cache_mode, "hits": cache_hits, "misses": cache_misses},
            "llm_cache": llm_cache_stats,
            "rows": rows,
            "d6": d6.to_dict() if d6 else None,
            "rba42": rba.to_dict(),
            "waste_conflict": waste_conflict,
            "sections": section_rows,
            "export_paths": out.export_paths,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n저장: {p}")


if __name__ == "__main__":
    main()
