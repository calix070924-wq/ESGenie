"""작업B 규제광고 양성확장 검증 게이트 (단계 5).

6개 게이트:
  VB1  dev/test 문장 교집합 = 0 (누수 차단)
  VB2  임계값 고정 확인 -BASELINE과 동일
  VB3  신규 양성 완전성 -gold text_class >=30 신규 편입
  VB4  분포 정직성 -도메인 x 라벨 테이블, 광고문구 도메인 recall 별도 표기
  VB5  표본 변화 -test n, 양성 n before->after 비교 (gold_log 대비)
  VB6  재현 명령 블록 포함 여부

FAIL 게이트 있으면 exit code 1.
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
SPLIT_DIR = ROOT / "data" / "benchmark_v2"
OUT_DIR = ROOT / "outputs" / "benchmark"
OUT_DIR.mkdir(parents=True, exist_ok=True)

BEFORE_N = 113
BEFORE_POS = 27
BEFORE_REG_POS = 10

try:
    from esgenie.calibrate import BASELINE
    _cfg_loaded = True
except ImportError:
    BASELINE = None
    _cfg_loaded = False

FROZEN_CFG = {
    "trigger": 0.25,
    "rule_weight": 0.4,
    "threshold": 0.25,
    "axis_flag": 0.80,
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "").strip()


def main() -> None:
    # 스플릿 로드
    dev  = json.loads((SPLIT_DIR / "dev.json").read_text(encoding="utf-8"))
    test = json.loads((SPLIT_DIR / "test.json").read_text(encoding="utf-8"))
    gold = [json.loads(l) for l in (SPLIT_DIR / "gold_regulatory.jsonl")
            .read_text(encoding="utf-8").splitlines() if l.strip()]

    dev_sents  = {_norm(c["sentence"]) for c in dev["cases"]}
    test_cases = test["cases"]
    test_sents = {_norm(c["sentence"]) for c in test_cases}

    results: list[tuple[str, str, str]] = []

    # VB1 dev/test 교집합
    overlap = dev_sents & test_sents
    if overlap:
        results.append(("VB1", "FAIL",
            f"dev/test 교집합 {len(overlap)}건 누수\n" +
            "\n".join(f"  '{s[:60]}'" for s in list(overlap)[:5])))
    else:
        results.append(("VB1", "PASS",
            f"dev/test 교집합 = 0  (dev={len(dev_sents)}, test={len(test_sents)})"))

    # VB2 임계값 고정
    if not _cfg_loaded:
        results.append(("VB2", "WARN", "esgenie.calibrate import 실패 -임계값 검증 불가"))
    else:
        cfg_mismatch = [f"{k}: 기대={FROZEN_CFG[k]} 실제={BASELINE.get(k)}"
                        for k in FROZEN_CFG if BASELINE.get(k) != FROZEN_CFG[k]]
        if cfg_mismatch:
            results.append(("VB2", "FAIL", "BASELINE 불일치\n" + "\n".join(f"  {m}" for m in cfg_mismatch)))
        else:
            results.append(("VB2", "PASS",
                f"BASELINE 일치: trig={BASELINE['trigger']} w={BASELINE['rule_weight']} "
                f"thr={BASELINE['threshold']} axf={BASELINE['axis_flag']}"))

    # VB3 신규 양성 완전성 (text_class GOLD-15+ 30건)
    NEW_CUTOFF = "GOLD-15"
    new_gold_tc = [r for r in gold
                   if r.get("eval_track") == "text_classification" and r.get("id", "") >= NEW_CUTOFF]
    new_gold_sents = {_norm(r["sentence"]) for r in new_gold_tc}
    in_test = {_norm(c["sentence"]) for c in test_cases
               if c.get("domain") == "regulatory_ad" and c.get("label") == "greenwash"}
    new_in_test = new_gold_sents & in_test
    missing = new_gold_sents - in_test
    if len(new_gold_tc) < 30:
        results.append(("VB3", "FAIL",
            f"신규 text_class gold {len(new_gold_tc)}건 < 30 (GOLD-15~)"))
    elif missing:
        results.append(("VB3", "FAIL",
            f"신규 gold {len(missing)}건이 test에 편입 안 됨\n" +
            "\n".join(f"  '{s[:60]}'" for s in list(missing)[:5])))
    else:
        results.append(("VB3", "PASS",
            f"신규 text_class gold {len(new_gold_tc)}건 전부 test 편입 완료"))

    # VB4 분포 정직성 (도메인 x 라벨)
    dom_label = Counter((c.get("domain", "?"), c["label"]) for c in test_cases)
    reg_pos  = dom_label.get(("regulatory_ad", "greenwash"), 0)
    esg_pos  = dom_label.get(("esg_report",   "greenwash"), 0)
    esg_neg  = dom_label.get(("esg_report",   "clean"),     0)
    table = (f"  regulatory_ad/greenwash = {reg_pos}\n"
             f"  esg_report/greenwash    = {esg_pos}\n"
             f"  esg_report/clean        = {esg_neg}")
    if reg_pos == 0:
        results.append(("VB4", "FAIL", "regulatory_ad 양성 0건 -gold 미편입 의심\n" + table))
    else:
        results.append(("VB4", "PASS",
            f"도메인 x 라벨 분포 OK (광고문구={reg_pos}, 보고서={esg_pos+esg_neg})\n" + table))

    # VB5 표본 변화
    cur_n   = test["n_cases"]
    cur_pos = test["n_pos"]
    delta_n   = cur_n   - BEFORE_N
    delta_pos = cur_pos - BEFORE_POS
    msg = (f"test n: {BEFORE_N} -> {cur_n} ({delta_n:+d})\n"
           f"  양성: {BEFORE_POS} -> {cur_pos} ({delta_pos:+d})\n"
           f"  regulatory_ad 양성: {BEFORE_REG_POS} -> {reg_pos} ({reg_pos-BEFORE_REG_POS:+d})")
    if cur_n < BEFORE_N or cur_pos < BEFORE_POS:
        results.append(("VB5", "FAIL", "test 규모가 축소됨\n" + msg))
    else:
        results.append(("VB5", "PASS", msg))

    # VB6 held_out_ci.md 존재 여부
    ci_path = OUT_DIR / "held_out_ci.md"
    if not ci_path.exists():
        results.append(("VB6", "WARN",
            "held_out_ci.md 없음 -단계 4(held_out_eval.py)가 아직 실행 안 됨\n"
            "  실키 실행 명령:\n"
            "  $env:ESGENIE_STRICT='1'; python scripts/held_out_eval.py"))
    else:
        content = ci_path.read_text(encoding="utf-8")
        if "모드: MOCK" in content or "mode: MOCK" in content.upper():
            results.append(("VB6", "WARN",
                "held_out_ci.md 존재하나 MOCK 실행 결과 -실키 재실행 필요\n"
                "  $env:ESGENIE_STRICT='1'; python scripts/held_out_eval.py"))
        else:
            results.append(("VB6", "PASS", f"held_out_ci.md 실키 결과 존재"))

    # 출력
    print("=" * 60)
    print("verify_taskB - 규제광고 양성확장 검증")
    print("=" * 60)
    fail_count = warn_count = 0
    for gate, status, detail in results:
        tag = {"PASS": "[PASS]", "FAIL": "[FAIL]", "WARN": "[WARN]"}[status]
        print(f"{tag} {gate}  {detail.split(chr(10))[0]}")
        for sub in detail.split("\n")[1:]:
            print(f"       {sub}")
        if status == "FAIL": fail_count += 1
        elif status == "WARN": warn_count += 1
    print("=" * 60)
    print(f"FAIL={fail_count}  WARN={warn_count}")

    # 재현 명령 블록
    repro = (
        "\n## 재현 명령\n"
        "```powershell\n"
        "# 1. 골드 검증\n"
        "python scripts/validate_gold.py\n\n"
        "# 2. 스플릿 재빌드\n"
        "python scripts/build_splits.py\n\n"
        "# 3. held-out 실키 평가 (API 키 필요)\n"
        "$env:ESGENIE_STRICT='1'; python scripts/held_out_eval.py\n\n"
        "# 4. 검증 게이트\n"
        "python scripts/verify_taskB.py\n"
        "```\n"
    )

    # 마크다운 저장
    md_lines = ["# verify_taskB 결과\n", f"- FAIL={fail_count}  WARN={warn_count}\n\n"]
    for gate, status, detail in results:
        md_lines.append(f"## {gate} {status}\n{detail}\n\n")
    md_lines.append(repro)
    (OUT_DIR / "taskB_verification.md").write_text(
        "".join(md_lines), encoding="utf-8")
    print(f"\n저장: {OUT_DIR / 'taskB_verification.md'}")

    if fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
