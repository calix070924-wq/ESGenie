#!/usr/bin/env python3
"""eval_crosscheck_bench.py — D6 특성화 벤치 + D1 단위검증 하네스.

명칭·완료기준
  D6: 독립 held-out 특성화 벤치 + snapshot 회귀 게이트
      - expected vs actual 비교는 발견 측정용 (expected 불일치는 게이트 아님)
      - snapshot 회귀 게이트: actual이 baseline과 달라지면 non-zero exit
      - snapshot 갱신은 검출기 의도 변경 시에만 수동으로 (--save-snapshot)
  D1: 단위검증 하드 게이트
      - 괴리 7건: expected_flagged != actual → non-zero exit
      - probe 2건: flagged=True AND 거짓%p 미발생 AND flag_reason 패턴 일치
                   셋 중 하나라도 실패 → non-zero exit

주의: CI 통과 목적의 라벨·검출기·임계값 수정 금지.

실행:
  PYTHONPATH=. python scripts/eval_crosscheck_bench.py              # 평가 + 전체 게이트
  PYTHONPATH=. python scripts/eval_crosscheck_bench.py --save-snapshot  # D6 baseline 생성/갱신
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

D6_BENCH_PATH = ROOT / "data" / "crosscheck_bench" / "d6_bench.json"
D1_BENCH_PATH = ROOT / "data" / "crosscheck_bench" / "d1_bench.json"
D6_SNAPSHOT_PATH = ROOT / "data" / "crosscheck_bench" / "d6_baseline.json"
EXISTING_BENCH_PATH = ROOT / "data" / "benchmark_v2" / "disclosure_bench.json"


# ── 독립성 assert ────────────────────────────────────────────────────────────
def assert_independence(d6_cases: list, existing_cases: list) -> None:
    for nc in d6_cases:
        nc_key = (frozenset(nc["disclosed"]), frozenset(nc.get("missing", [])))
        for ec in existing_cases:
            ec_key = (frozenset(ec["disclosed"]), frozenset(ec.get("missing", [])))
            assert nc_key != ec_key, (
                f"독립성 위반: {nc['id']} (disclosed, missing) 조합이 "
                f"기존 {ec['id']}와 중복"
            )
    print("[OK] 독립성 assert -- IHD6 8케이스, 기존 disclosure_bench.json과 중복 없음\n")


# ── mock 헬퍼 ────────────────────────────────────────────────────────────────
def _ext(disclosed: list, missing: list) -> SimpleNamespace:
    return SimpleNamespace(
        mapped={c: {"code": c} for c in disclosed},
        missing=list(missing),
    )


def _mock_ans() -> SimpleNamespace:
    return SimpleNamespace(status="verified", flags=[], rationale="", value=None)


def _mock_claim(value: float, unit: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        value=value, unit=unit,
        raw=f"{value}{unit}", source="eval_crosscheck",
    )


# ── 출력 헬퍼 ────────────────────────────────────────────────────────────────
def _yn(b: bool) -> str:
    return "OK" if b else "NG"


def _d6_binary(level: str) -> bool:
    return level in ("medium", "high")


# ═══════════════════════════════════════════════════════════════════════════════
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--save-snapshot", action="store_true",
        help="D6 실측 결과를 d6_baseline.json으로 저장 (검출기 의도 변경 시에만 사용)"
    )
    args = parser.parse_args()

    from esgenie.layer3_disclosure import detect_selective_disclosure
    from esgenie.supplychain.mapping import _reconcile_claim

    d6_bench = json.loads(D6_BENCH_PATH.read_text(encoding="utf-8"))
    d1_bench = json.loads(D1_BENCH_PATH.read_text(encoding="utf-8"))
    existing_bench = json.loads(EXISTING_BENCH_PATH.read_text(encoding="utf-8"))

    assert_independence(d6_bench["cases"], existing_bench["cases"])

    # ── D6 실측 ──────────────────────────────────────────────────────────────
    d6_results = []
    for case in d6_bench["cases"]:
        ext = _ext(case["disclosed"], case.get("missing", []))
        report = detect_selective_disclosure(ext)
        d6_results.append({
            "id": case["id"],
            "scenario": case["scenario"],
            "expected": case["expected_level"],
            "actual": report.level,
            "score": round(report.score, 4),
            "level_match": case["expected_level"] == report.level,
            "binary_expected": _d6_binary(case["expected_level"]),
            "binary_actual": _d6_binary(report.level),
            "binary_match": _d6_binary(case["expected_level"]) == _d6_binary(report.level),
            "boundary": case.get("boundary", False),
            "detection_gap": case.get("detection_gap_expected", False),
            "orphans": len(report.orphan_ratios),
            "omitted": len(report.omitted_sensitive),
        })

    # ── D6 snapshot 저장 모드 ────────────────────────────────────────────────
    if args.save_snapshot:
        snapshot = {
            "generated_at": str(date.today()),
            "note": (
                "현행 D6 검출기 실측 기준선. "
                "갱신은 검출기 의도 변경 시에만 수동으로 (--save-snapshot). "
                "CI 통과 목적의 라벨·검출기 수정 후 snapshot 갱신 금지."
            ),
            "cases": [
                {"id": r["id"], "actual_level": r["actual"], "score": r["score"]}
                for r in d6_results
            ],
        }
        D6_SNAPSHOT_PATH.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[OK] D6 baseline snapshot 저장 → {D6_SNAPSHOT_PATH}")
        for r in d6_results:
            print(f"     {r['id']}: {r['actual']} (score={r['score']})")
        print()
        # snapshot 저장 후 일반 평가도 계속 진행

    # ── D1 실측 ──────────────────────────────────────────────────────────────
    d1_results = []
    for case in d1_bench["cases"]:
        ans = _mock_ans()
        claim = _mock_claim(case["claim_value"], case["claim_unit"])
        result = _reconcile_claim(
            ans, claim, case["evid_value"], case["evid_unit"], code=case["code"]
        )
        flagged = result.status == "flagged"
        d1_results.append({
            "id": case["id"],
            "code": case["code"],
            "claim": f"{case['claim_value']}{case['claim_unit']}",
            "evid": f"{case['evid_value']}{case['evid_unit']}",
            "expected": case["expected_flagged"],
            "actual": flagged,
            "match": case["expected_flagged"] == flagged,
            "boundary": case.get("boundary", False),
            "flags": result.flags,
        })

    # ── D1 Probe 실측 ────────────────────────────────────────────────────────
    probe_results = []
    for probe in d1_bench["probes"]:
        ans = _mock_ans()
        claim = _mock_claim(probe["claim_value"], probe["claim_unit"])
        result = _reconcile_claim(
            ans, claim, probe["evid_value"], probe["evid_unit"], code=probe["code"]
        )
        flagged = result.status == "flagged"
        false_pp = any("Δ" in f for f in result.flags)   # Δ 존재 = 거짓 %p 계산
        pattern = probe.get("expected_flag_pattern", "")
        flag_pattern_ok = any(pattern in f for f in result.flags)
        # 3조건 모두 만족해야 pass
        probe_pass = flagged and (not false_pp) and flag_pattern_ok
        probe_results.append({
            "id": probe["id"],
            "code": probe["code"],
            "claim": f"{probe['claim_value']}{probe['claim_unit']}",
            "evid": f"{probe['evid_value']}{probe['evid_unit']}",
            "flagged": flagged,
            "false_pp_diff": false_pp,
            "flag_pattern_ok": flag_pattern_ok,
            "probe_pass": probe_pass,
            "flags": result.flags,
        })

    # ── 기존 disclosure_bench.json 동적 불일치 검사 ──────────────────────────
    existing_mismatches = []
    for case in existing_bench["cases"]:
        ext = _ext(case["disclosed"], case.get("missing", []))
        report = detect_selective_disclosure(ext)
        if report.level != case["expected_level"]:
            existing_mismatches.append({
                "id": case["id"],
                "labeled": case["expected_level"],
                "computed": report.level,
                "score": round(report.score, 4),
            })

    # ═══════════════════════════════════════════════════════════════════════════
    # 출력
    # ═══════════════════════════════════════════════════════════════════════════
    SEP = "=" * 84

    print(SEP)
    print("  D6 독립 held-out 특성화 벤치 (IHD6, n=8)  — 발견 측정 (게이트 아님)")
    print(SEP)

    # [표 A] 3단계 level 비교
    print("\n[표 A] Level 3단계 비교 (low / medium / high)  — 도메인 라벨 vs 실측\n")
    print(f"{'ID':<14}{'시나리오':<26}{'expected':<10}{'actual':<10}{'score':<8}{'고아':<5}{'match':<5}  비고")
    print("-" * 84)
    level_correct = 0
    for r in d6_results:
        note = ""
        if not r["level_match"]:
            if r["boundary"]:
                note = "경계 민감도 발견"
            elif r["detection_gap"]:
                note = "탐지 공백 (omission-only)"
            else:
                note = "불일치"
        level_correct += r["level_match"]
        sc = f"{r['scenario'][:24]:<26}"
        print(f"{r['id']:<14}{sc}{r['expected']:<10}{r['actual']:<10}"
              f"{r['score']:<8}{r['orphans']:<5}{_yn(r['level_match']):<5}  {note}")
    print(f"\n  3단계 일치: {level_correct}/{len(d6_results)} = {level_correct/len(d6_results)*100:.1f}%")

    # [표 B] 이진 비교
    print("\n[표 B] 이진 비교 (medium+ = flagged)  — 발견 측정\n")
    print(f"{'ID':<14}{'exp_flag':<10}{'act_flag':<10}{'match':<5}  비고")
    print("-" * 60)
    binary_correct = 0
    for r in d6_results:
        note = ""
        if not r["binary_match"]:
            note = "FN — omission-only 탐지 공백" if r["detection_gap"] else "불일치"
        elif r["boundary"] and not r["level_match"]:
            note = "이진 TP / 3단계 경계 민감도 발견"
        binary_correct += r["binary_match"]
        print(f"{r['id']:<14}{str(r['binary_expected']):<10}{str(r['binary_actual']):<10}"
              f"{_yn(r['binary_match']):<5}  {note}")
    print(f"\n  이진 일치: {binary_correct}/{len(d6_results)} = {binary_correct/len(d6_results)*100:.1f}%")

    # D6 핵심 발견
    gap_fn = [r["id"] for r in d6_results if r["detection_gap"] and not r["binary_match"]]
    boundary_mismatch = [r["id"] for r in d6_results if r["boundary"] and not r["level_match"]]
    print("\n[D6 핵심 발견]")
    if gap_fn:
        print(f"  탐지 공백 FN: {', '.join(gap_fn)}")
        print("  D6 검출기는 고아(orphan) 신호 없는 omission-only 은폐를 구조적으로 미탐지.")
        print("  공식: score = 0.45*signal_a + 0.55*signal_b")
        print("  고아 0개 → signal_b=0 → max_score=0.45 < medium 임계(0.50) → 항상 low.")
    if boundary_mismatch:
        print(f"  경계 민감도 발견: {', '.join(boundary_mismatch)} (도메인 high → 검출기 medium, 재라벨 아님)")
    if not gap_fn and not boundary_mismatch:
        print("  없음 — 전 케이스 일치.")

    # ── D6 snapshot 회귀 게이트 ──────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  D6 Snapshot 회귀 게이트")
    print(SEP)

    exit_code = 0

    if not D6_SNAPSHOT_PATH.exists():
        print(f"  [WARN] {D6_SNAPSHOT_PATH.name} 없음.")
        print("  --save-snapshot 으로 baseline을 먼저 생성하세요.")
        print("  snapshot 없이는 회귀 게이트를 건너뜁니다.\n")
    else:
        snapshot = json.loads(D6_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        snap_by_id = {s["id"]: s for s in snapshot["cases"]}
        regressions = []
        for r in d6_results:
            snap = snap_by_id.get(r["id"])
            if snap is None:
                regressions.append(f"  {r['id']}: snapshot에 없음")
            elif r["actual"] != snap["actual_level"]:
                regressions.append(
                    f"  {r['id']}: {snap['actual_level']} -> {r['actual']} [회귀]"
                )
        if regressions:
            print(f"[FAIL] D6 snapshot 회귀 {len(regressions)}건:")
            for msg in regressions:
                print(msg)
            exit_code = 1
        else:
            print(f"[OK] D6 snapshot 회귀 없음 ({len(d6_results)}건, "
                  f"baseline: {snapshot.get('generated_at', '?')})")

    # ── D1 ──────────────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  D1 mock 단위검증 — 하드 게이트 (괴리 7건 + probe 2건)")
    print(SEP)

    print("\n[표 C] D1 괴리 케이스 — flagged 여부\n")
    print(f"{'ID':<8}{'code':<8}{'claim':<10}{'evid':<12}{'exp':<7}{'act':<7}{'GATE':<6}  ★")
    print("-" * 60)
    d1_failures = []
    for r in d1_results:
        bnd = "★경계" if r["boundary"] else ""
        gate = _yn(r["match"])
        if not r["match"]:
            d1_failures.append(
                f"  {r['id']}: expected_flagged={r['expected']}, actual={r['actual']}"
            )
        print(f"{r['id']:<8}{r['code']:<8}{r['claim']:<10}{r['evid']:<12}"
              f"{str(r['expected']):<7}{str(r['actual']):<7}{gate:<6}  {bnd}")
    d1_pct = (len(d1_results) - len(d1_failures)) / len(d1_results) * 100
    print(f"\n  D1 정확도: {len(d1_results)-len(d1_failures)}/{len(d1_results)} = {d1_pct:.1f}%")

    if d1_failures:
        print(f"\n  [FAIL] D1 괴리 케이스 실패 {len(d1_failures)}건:")
        for msg in d1_failures:
            print(msg)
        exit_code = 1
    else:
        print("  [OK] D1 괴리 케이스 전부 통과")

    print("\n[표 D] D1 단위처리 Probe (거짓 %p 괴리 guard 검증 — 3조건 모두 만족해야 PASS)\n")
    print(f"{'ID':<8}{'code':<8}{'claim':<8}{'evid':<12}{'flagged':<9}{'거짓%p':<8}{'패턴OK':<8}{'GATE':<6}  flag reason")
    print("-" * 84)
    probe_failures = []
    for r in probe_results:
        reason = r["flags"][0][:40] if r["flags"] else "(없음)"
        gate = _yn(r["probe_pass"])
        if not r["probe_pass"]:
            probe_failures.append(
                f"  {r['id']}: flagged={r['flagged']}, false_pp={r['false_pp_diff']}, "
                f"pattern_ok={r['flag_pattern_ok']}"
            )
        print(f"{r['id']:<8}{r['code']:<8}{r['claim']:<8}{r['evid']:<12}"
              f"{str(r['flagged']):<9}{str(r['false_pp_diff']):<8}"
              f"{str(r['flag_pattern_ok']):<8}{gate:<6}  {reason}")

    if probe_failures:
        print(f"\n  [FAIL] Probe 실패 {len(probe_failures)}건:")
        for msg in probe_failures:
            print(msg)
        exit_code = 1
    else:
        probe_ok = sum(r["probe_pass"] for r in probe_results)
        print(f"\n  [OK] Probe {probe_ok}/{len(probe_results)} 통과")

    # ── 기존 disclosure_bench 동적 불일치 (Issue #22) ────────────────────────
    print(f"\n{SEP}")
    print("  [별도 이슈] 기존 disclosure_bench.json 골드 라벨 ↔ 현행 공식 동적 검사")
    print(SEP)
    if existing_mismatches:
        print(f"  불일치 {len(existing_mismatches)}건 (현행 공식으로 재계산):")
        for m in existing_mismatches:
            print(f"    {m['id']}: labeled={m['labeled']}, computed={m['computed']} "
                  f"(score={m['score']})")
        print("  -> 공식 변경 이후 라벨이 구버전 기준으로 남은 것으로 의심.")
        print("  -> 이 PR에서 수정 금지. Issue #22로 추적.")
    else:
        print("  불일치 없음 — 기존 12케이스 라벨이 현행 공식과 일치.")
    print()

    # ── 최종 게이트 결론 ─────────────────────────────────────────────────────
    print(SEP)
    if exit_code == 0:
        print("  [PASS] 전체 게이트 통과 (D6 snapshot 회귀 없음 + D1 단위검증 통과)")
    else:
        print("  [FAIL] 게이트 실패 — 위 [FAIL] 항목 확인")
    print(SEP)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
