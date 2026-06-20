# -*- coding: utf-8 -*-
"""
demo_d6.py — 시연 증빙 세트(한울정밀공업)를 라이브 파이프라인에 태워
            폐기물 재활용률 행에서 D6(그린워싱) 🚩 검출이 실제로 일어나는지 확인.

라이브검증_체크리스트.md 의 3번(OCR 채널) + 실사응답서(saq5_env) 검증을 한 스크립트로 합쳤다.
반드시 **로컬 PC**에서 실행 (Azure/OpenAI 키 필요, Claude 샌드박스는 외부망 차단).

사용:
    # 기본(judge 라이브 + 그린워싱 데모 모드)
    python -m scripts.demo_d6
    # 진짜 라이브만 인정(키/네트워크 실패 시 예외)
    python -m scripts.demo_d6 --strict
    # 엑셀 응답서까지 출력
    python -m scripts.demo_d6 --export
옵션:
    --strict      ESGENIE_STRICT=1 (mock 폴백 금지)
    --no-judge    LLM judge 끄기(룰 1차만) — 기본은 judge on
    --no-greenwash  demo_greenwash 끄기
    --export      outputs/_supplychain_demo 에 xlsx 저장
"""
from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EVID_DIR = REPO / "시연증빙세트_한울정밀공업"
CORP_NAME = "한울정밀공업㈜"
WASTE_QID = "SAQ-E-NUM-WASTE"   # "(수치) 폐기물 재활용률" (K-ESG E-6-2)
FRAMEWORK = "saq5_env"

# 업로드 증빙 = 고지서 3종 + 사내규정 1종 (OEM SAQ PDF는 양식 소품이라 제외)
EVIDENCE_GLOBS = ["01_*.pdf", "02_*.pdf", "03_*.pdf", "04_*.pdf"]
# 협력사가 기입해 제출한 SAQ (자가주장 추출 대상)
SAQ_GLOBS = ["05_*.pdf", "06_*.pdf", "07_*.pdf"]
# 수동 입력 자가주장 (SAQ 파싱 실패 대비 폴백/보강) — code: value(%)
MANUAL_CLAIMS = {"E-6-2": 92.0}


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--no-judge", dest="judge", action="store_false")
    ap.add_argument("--no-greenwash", dest="greenwash", action="store_false")
    ap.add_argument("--export", action="store_true")
    ap.set_defaults(judge=True, greenwash=True)
    return ap.parse_args()


def hr(c="─"):
    print(c * 78)


def collect_evidence() -> dict[str, str]:
    files: dict[str, str] = {}
    for g in EVIDENCE_GLOBS:
        for p in sorted(EVID_DIR.glob(g)):
            files[p.name] = str(p)
    if not files:
        sys.exit(f"[!] 증빙을 찾지 못함: {EVID_DIR}")
    return files


def main():
    args = parse_args()
    if args.strict:
        os.environ["ESGENIE_STRICT"] = "1"

    # esgenie import는 STRICT 환경변수 설정 후에
    sys.path.insert(0, str(REPO))
    from esgenie.pipeline import run as run_pipeline
    from esgenie.supplychain import (respond_from_pipeline, parse_saq_claims,
                                     manual_claims, merge_claims)

    evidence = collect_evidence()

    # 협력사 자가주장 로딩: SAQ 파싱 + 수동입력 (둘 다)
    saq_paths = [str(p) for g in SAQ_GLOBS for p in sorted(EVID_DIR.glob(g))]
    # 수동입력을 먼저 깔고 SAQ 파싱이 덮어씀 → SAQ 추출 우선, 수동은 폴백
    claims = merge_claims(manual_claims(MANUAL_CLAIMS), parse_saq_claims(saq_paths))

    print("\n" + "=" * 78)
    print(f"  ESGenie D6 시연 검증 — {CORP_NAME}")
    print(f"  strict={args.strict}  judge={args.judge}  greenwash={args.greenwash}")
    print("=" * 78)
    print(f"증빙 {len(evidence)}건:")
    for n in evidence:
        print("   •", n)
    print(f"협력사 자가주장 {len(claims)}건:")
    for code, c in claims.items():
        print(f"   • {code} = {c.value}{c.unit}  ←  {c.raw}  [{c.source}]")

    # ── 파이프라인 실행 (L0~L5) ──────────────────────────────────────────────
    print("\n[1/3] 파이프라인 실행 (L0 OCR → … → L3 검출 → L4 검증)…")
    result = run_pipeline(
        corp_code="HANUL-SME",
        areas=["E"],
        corp_name=CORP_NAME,
        industry="자동차부품 제조",
        use_dart=False,                 # 비상장 SME
        evidence_files=evidence,
        demo_greenwash=args.greenwash,
        llm_judge=args.judge,
        export_outputs=False,
        profile="sme",
    )

    # ── OCR 채널/엔진 점검 (체크리스트 3번) ─────────────────────────────────
    hr("═")
    print(" [2/3] OCR 채널·엔진 (mock/None 이면 라이브 실패)")
    hr()
    print(f"   {'파일':34} {'채널':13} {'doc_type':16} engine")
    for ext in getattr(result, "ocr_extractions", []) or []:
        m = getattr(ext, "router_meta", {}) or {}
        eng = m.get("engine") or ("mock" if m.get("mock") else m.get("fallback") or "?")
        ch = getattr(getattr(ext, "channel", None), "value", "?")
        dt = m.get("doc_type") or getattr(ext, "doc_type", "?")
        print(f"   {str(getattr(ext,'source_file',''))[:32]:34} {ch:13} {str(dt):16} {eng}")

    # ── 실사 응답서 생성 (+자가주장 대조) + D6 플래그 확인 ───────────────────
    sheet = respond_from_pipeline(result, FRAMEWORK, supplier_claims=claims)
    if not sheet.corp_name:
        sheet.corp_name = CORP_NAME

    hr("═")
    print(f" [3/3] 실사 응답서 ({FRAMEWORK}) — 커버리지 {sheet.coverage_pct}% · 🚩 {sheet.flagged_count}건")
    hr()
    waste = None
    for a in sheet.answers:
        mark = "🚩" if a.status == "flagged" else "  "
        print(f" {mark} [{a.qid:14}] {a.badge:10} value={a.value}")
        for f in a.flags:
            print(f"            └ {f}")
        if a.qid == WASTE_QID:
            waste = a

    # ── 핵심 판정: 폐기물 재활용률 행 ───────────────────────────────────────
    hr("═")
    print(" 핵심 — 폐기물 재활용률 (증빙 실측 ≈ 29.3%, SAQ 자가주장 92%)")
    hr()
    if waste is None:
        print("   [!] SAQ-E-NUM-WASTE 문항을 응답서에서 찾지 못함 (매핑 확인 필요)")
    else:
        print(f"   상태   : {waste.badge}")
        print(f"   값     : {waste.value}")
        print(f"   플래그 : {waste.flags or '(없음)'}")
        print(f"   근거   : {waste.rationale.strip() or '(없음)'}")
        ok = waste.status == "flagged"
        print()
        print("   ▶ 결과:", "✅ D6 🚩 검출됨 — 시연 시나리오 성립"
              if ok else "❌ 미검출 — demo_greenwash/disclosure 경로 점검 필요")

    if args.export:
        from esgenie.supplychain.exporters import export_response_sheet
        out = REPO / "outputs" / "_supplychain_demo"
        path = export_response_sheet(sheet, out)
        print(f"\n   엑셀 응답서 저장 → {path}")

    print("\n합격 기준 요약")
    print("  [ ] OCR engine = azure_docintel(01~03) / gpt-4.1-mini-text(04)  — mock/None 아님")
    print("  [ ] 폐기물 재활용률 행 status = flagged (🚩)")
    print("  [ ] 응답서 flagged_count ≥ 1")
    print()


if __name__ == "__main__":
    main()
