"""lp7 5개사 덤프에 value_role·대표값 선정을 오프라인 재적용한다.

배정 단계는 바꾸지 않으므로 lp7의 코드별 노드 풀을 그대로 되살려도 이번 변경을
정확히 측정할 수 있다. 네트워크·LLM·OCR 호출은 없다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DUMPS = (
    ("현대모비스", "outputs/lp7_012330.json"),
    ("삼성전기", "outputs/lp7_009150.json"),
    ("LG화학", "outputs/lp7_051910.json"),
    ("신한지주", "outputs/lp7_055550.json"),
    ("NAVER", "outputs/lp7_035420.json"),
)

COMMON_CASES = (
    ("E-2-1", "재생 원자재 사용량 합계", "component"),
    ("E-3-1", "Scope 2 배출량(지역 기반) 합계", "component"),
    ("E-4-1", "비재생에너지 사용량 합계", "component"),
    ("E-4-2", "2040년 RE100 달성률", "target"),
    ("E-5-1", "지표수 취수량 합계 2024", "component"),
    ("E-7-2", "부유물질(SS) 합계 2024", "component"),
)


def _revive(row: dict[str, Any]):
    from esgenie.ssot.evidence_graph import EvidenceNode

    nodes = []
    for n in row.get("nodes", []):
        if n.get("origin") not in ("ocr_structured", "ocr_unstructured"):
            continue
        nodes.append(EvidenceNode(
            id=n["id"], metric=row["code"], value=n["value"], unit=n.get("unit", ""),
            period=n.get("period", 0), source="ocr/lp7-replay",
            raw_text=n.get("raw_text", ""), origin=n["origin"], source_file="",
            period_inferred=bool(n.get("period_inferred", False)),
            value_role=n.get("value_role", "unknown"),
        ))
    return nodes


def main() -> None:
    from types import SimpleNamespace

    from esgenie.layer3_disclosure import OMISSION_SENSITIVITY, detect_selective_disclosure
    from esgenie.ssot.node_select import (
        classify_common_value_role,
        classify_value_role,
        normalize_to_item_unit,
        select_representative_node,
    )

    common_ok = sum(
        classify_common_value_role(hint, report_year=2025) == expected
        for _code, hint, expected in COMMON_CASES
    )
    full_ok = sum(
        classify_value_role(code, hint, report_year=2025) == expected
        for code, hint, expected in COMMON_CASES
    )
    print(f"공통 어휘 커버율: {common_ok}/{len(COMMON_CASES)} "
          f"({100 * common_ok / len(COMMON_CASES):.1f}%)")
    print(f"E-3-1 예외 포함: {full_ok}/{len(COMMON_CASES)} "
          f"({100 * full_ok / len(COMMON_CASES):.1f}%) · "
          "코드별 예외 3개 계열(E-3-1 Scope, E-4-1 범위소실, E-7 물질 alias)")

    results: dict[str, dict[str, Any]] = {}
    sensitive_e = {code for code in OMISSION_SENSITIVITY if code.startswith("E-")}
    for company, relpath in DUMPS:
        data = json.loads((ROOT / relpath).read_text(encoding="utf-8"))
        year = data.get("report_year")
        picks: dict[str, Any] = {}
        unit_bad: list[str] = []
        partial: list[str] = []
        print(f"\n[{company}] report_year={year}")
        for row in data["rows"]:
            code = row["code"]
            if not code.startswith("E-"):
                continue
            picked = select_representative_node(code, _revive(row), report_year=year)
            if picked is None:
                print(f"  {code}: 대표값 없음")
                continue
            role = classify_value_role(code, picked, report_year=year)
            value, unit, flag = normalize_to_item_unit(code, picked.value, picked.unit)
            hint = picked.raw_text.split("=", 1)[0]
            picks[code] = {"value": value, "unit": unit, "role": role, "hint": hint}
            if flag == "unit_suspect":
                unit_bad.append(code)
            if role != "total":
                partial.append(code)
            changed = ""
            if value != row.get("ledger_value") or unit != row.get("ledger_unit"):
                changed = f"  ← lp7 {row.get('ledger_value')} {row.get('ledger_unit')}"
            print(f"  {code}: {value} {unit} [{role}] {hint}{changed}")
        results[company] = {"picks": picks, "unit_bad": unit_bad, "partial": partial}
        print(f"  단위불일치 {len(unit_bad)}건 {unit_bad} · 부분/판정보류 {partial}")
        d6_ext = SimpleNamespace(
            mapped={code: {"code": code, "value_role": row["role"]}
                    for code, row in picks.items()},
            missing=sorted(sensitive_e - set(picks)), confidence_flags={},
        )
        sensitivity = {}
        for factor in (0.3, 0.5, 0.7):
            report = detect_selective_disclosure(
                d6_ext, partial_weight_factor=factor)
            sensitivity[factor] = (report.asymmetry["signal_a"], report.score)
        results[company]["d6_sensitivity"] = sensitivity
        print("  D6 E민감항목 리플레이(부분계수 → 신호A/점수): "
              + " · ".join(f"{f:.1f}→{a:.4f}/{s:.4f}"
                           for f, (a, s) in sensitivity.items()))

    golden = json.loads((ROOT / "outputs/lp6_012330_E.json").read_text(encoding="utf-8"))
    expected = {r["code"]: r["ledger_value"] for r in golden["rows"]}
    actual = results["현대모비스"]["picks"]
    regressions = {
        code: (value, (actual.get(code) or {}).get("value"))
        for code, value in expected.items()
        if (actual.get(code) or {}).get("value") != value
    }
    print(f"\n모비스 lp6 기존 11개 불변: {len(expected) - len(regressions)}/{len(expected)}")
    print(f"불일치: {regressions or '없음'}")


if __name__ == "__main__":
    main()
