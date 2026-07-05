"""eval_crosscheck_bench.py — D6 독립 held-out + D1 mock 단위검증 하네스.

설계 원칙:
  - 기대라벨은 검출기 실행 전 도메인 로직으로 확정됨 (designer≠evaluator).
  - 결과를 보고 라벨·검출기·임계값을 수정하지 않는다.
  - 이진 FN 은 재라벨 대상이 아니라 '탐지 공백' 발견으로 보고.

실행:
  PYTHONPATH=. python scripts/eval_crosscheck_bench.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Windows CP949 콘솔에서 한국어 + 특수문자 출력
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

D6_BENCH_PATH = ROOT / "data" / "crosscheck_bench" / "d6_bench.json"
D1_BENCH_PATH = ROOT / "data" / "crosscheck_bench" / "d1_bench.json"
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


# ── D6 mock ──────────────────────────────────────────────────────────────────
def _ext(disclosed: list, missing: list) -> SimpleNamespace:
    return SimpleNamespace(
        mapped={c: {"code": c} for c in disclosed},
        missing=list(missing),
    )


# ── D1 mock ──────────────────────────────────────────────────────────────────
def _mock_ans() -> SimpleNamespace:
    return SimpleNamespace(status="verified", flags=[], rationale="", value=None)


def _mock_claim(value: float, unit: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        value=value,
        unit=unit,
        raw=f"{value}{unit}",
        source="eval_crosscheck",
    )


# ── 출력 헬퍼 ────────────────────────────────────────────────────────────────
def _yn(b: bool) -> str:
    return "✅" if b else "❌"


def _d6_binary(level: str) -> bool:
    return level in ("medium", "high")


# ═══════════════════════════════════════════════════════════════════════════════
def main() -> None:
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
        expected = case["expected_level"]
        actual = report.level
        d6_results.append({
            "id": case["id"],
            "scenario": case["scenario"],
            "expected": expected,
            "actual": actual,
            "score": round(report.score, 4),
            "level_match": expected == actual,
            "binary_expected": _d6_binary(expected),
            "binary_actual": _d6_binary(actual),
            "binary_match": _d6_binary(expected) == _d6_binary(actual),
            "boundary": case.get("boundary", False),
            "detection_gap": case.get("detection_gap_expected", False),
            "orphans": len(report.orphan_ratios),
            "omitted": len(report.omitted_sensitive),
        })

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
        false_pp = any("Δ" in f for f in result.flags)
        pattern = probe.get("expected_flag_pattern", "")
        flag_pattern_ok = any(pattern in f for f in result.flags)
        probe_results.append({
            "id": probe["id"],
            "code": probe["code"],
            "claim": f"{probe['claim_value']}{probe['claim_unit']}",
            "evid": f"{probe['evid_value']}{probe['evid_unit']}",
            "flagged": flagged,
            "false_pp_diff": false_pp,
            "expected_false_pp": probe["expected_false_pp_diff"],
            "pp_guard_ok": false_pp == probe["expected_false_pp_diff"],
            "flag_pattern_ok": flag_pattern_ok,
            "flags": result.flags,
        })

    # ═══════════════════════════════════════════════════════════════════════════
    # 출력
    # ═══════════════════════════════════════════════════════════════════════════
    SEP = "=" * 84

    print(SEP)
    print("  D6 독립 held-out 벤치마크 (IHD6, n=8)  — 도메인 라벨 vs 검출기 실측")
    print(SEP)

    # [표 A] 3단계 level 정확도
    print("\n[표 A] Level 3단계 정확도 (low / medium / high)\n")
    hdr = f"{'ID':<14}{'시나리오':<26}{'expected':<10}{'actual':<10}{'score':<8}{'고아':<5}{'match':<6}비고"
    print(hdr)
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
              f"{r['score']:<8}{r['orphans']:<5}{_yn(r['level_match']):<6}{note}")
    lvl_pct = level_correct / len(d6_results) * 100
    print(f"\n  3단계 정확도: {level_correct}/{len(d6_results)} = {lvl_pct:.1f}%")

    # [표 B] 이진 (medium+) 정확도
    print("\n[표 B] 이진 적중 (medium+ = flagged)\n")
    hdr2 = f"{'ID':<14}{'exp_flag':<10}{'act_flag':<10}{'match':<6}비고"
    print(hdr2)
    print("-" * 60)
    binary_correct = 0
    for r in d6_results:
        note = ""
        if not r["binary_match"]:
            if r["detection_gap"]:
                note = "FN — omission-only 탐지 공백"
            else:
                note = "불일치"
        elif r["boundary"] and not r["level_match"]:
            note = "★이진 TP / 3단계 경계 민감도 발견"
        binary_correct += r["binary_match"]
        print(f"{r['id']:<14}{str(r['binary_expected']):<10}{str(r['binary_actual']):<10}"
              f"{_yn(r['binary_match']):<6}{note}")
    bin_pct = binary_correct / len(d6_results) * 100
    print(f"\n  이진 정확도: {binary_correct}/{len(d6_results)} = {bin_pct:.1f}%")

    # 핵심 발견
    gap_fn = [r["id"] for r in d6_results if r["detection_gap"] and not r["binary_match"]]
    boundary_mismatch = [r["id"] for r in d6_results if r["boundary"] and not r["level_match"]]
    print("\n[D6 핵심 발견]")
    if gap_fn:
        print(f"  탐지 공백 FN: {', '.join(gap_fn)}")
        print("  → D6 검출기는 고아(orphan) 신호 없는 omission-only 은폐를 구조적으로 미탐지.")
        print("    공식: score = 0.45×signal_a + 0.55×signal_b")
        print("    고아 0개 → signal_b=0 → max_score=0.45 < medium 임계(0.50) → 항상 low.")
    if boundary_mismatch:
        print(f"  경계 민감도 발견: {', '.join(boundary_mismatch)}")
        print("  → 도메인 high, 검출기 medium. 재라벨 아님.")
    if not gap_fn and not boundary_mismatch:
        print("  발견 없음 — 전 케이스 일치.")

    # ── D1 ──────────────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  D1 mock 단위검증 (n=7 괴리 케이스 + 2 probe)")
    print(SEP)

    print("\n[표 C] D1 괴리 케이스 — flagged 여부\n")
    hdr3 = f"{'ID':<8}{'code':<8}{'claim':<10}{'evid':<12}{'exp':<7}{'act':<7}{'match':<6}★"
    print(hdr3)
    print("-" * 60)
    d1_correct = 0
    for r in d1_results:
        bnd = "★경계" if r["boundary"] else ""
        d1_correct += r["match"]
        print(f"{r['id']:<8}{r['code']:<8}{r['claim']:<10}{r['evid']:<12}"
              f"{str(r['expected']):<7}{str(r['actual']):<7}{_yn(r['match']):<6}{bnd}")
    d1_pct = d1_correct / len(d1_results) * 100
    print(f"\n  D1 정확도: {d1_correct}/{len(d1_results)} = {d1_pct:.1f}%")

    print("\n[표 D] D1 단위처리 Probe (거짓 %p 괴리 guard 검증)\n")
    hdr4 = f"{'ID':<8}{'code':<8}{'claim':<8}{'evid':<12}{'flagged':<9}{'거짓%p':<8}{'guard OK':<10}flag reason"
    print(hdr4)
    print("-" * 84)
    probe_ok_count = 0
    for r in probe_results:
        reason = r["flags"][0][:45] if r["flags"] else "(없음)"
        probe_ok_count += r["pp_guard_ok"]
        print(f"{r['id']:<8}{r['code']:<8}{r['claim']:<8}{r['evid']:<12}"
              f"{str(r['flagged']):<9}{str(r['false_pp_diff']):<8}"
              f"{_yn(r['pp_guard_ok']):<10}{reason}")
    print(f"\n  Probe guard 통과: {probe_ok_count}/{len(probe_results)}")

    # ── 별도 이슈 고지 ──────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  [별도 이슈] 기존 disclosure_bench.json 골드 라벨 ↔ 현행 공식 불일치")
    print(SEP)
    print("  D6-10 (법규 3종, expected=medium): 현행 공식 → score≈0.122 → low")
    print("  D6-08 (6건 은폐,   expected=high):  현행 공식 → score≈0.231 → low")
    print("  → 공식이 설계 이후 변경됐거나 골드 라벨이 구버전 기준으로 설정된 것으로 의심.")
    print("  → 기존 12케이스 '자체설계 100%'는 현행 공식 기준 미성립. 회귀 조사 필요.")
    print("  → 이 PR에서 수정 금지. 별도 이슈로 등록 필요.\n")


if __name__ == "__main__":
    main()
