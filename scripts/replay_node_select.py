"""저장된 원장 덤프(lp_*.json)에 현재 선택 규칙을 다시 적용해 전후를 비교한다.

`inspect_ledger_provenance.py`의 라이브 재실행 없이 규칙 변경 효과만 본다 — 덤프에
코드별 노드 전량(hint·value·unit·period)이 남아 있으므로 노드를 되살려 규칙만 다시
돌리면 된다. LLM·OCR 호출 0회.

한계(의도적): 덤프에 없는 것은 재현하지 않는다.
  · 미공시로 남은 코드는 덤프에 행이 없다 → '신규 공시' 방향 변화는 안 보인다.
    이번 변경은 배제·표기만 추가하므로(승격 조건 완화 없음) 그 방향은 발생하지 않는다.
  · TextNode 승격 경로는 덤프에 없다 → 정성 항목이 '문서 조항 확인'으로 남는지는
    회귀 테스트가 고정한다.

사용:
    python3 scripts/replay_node_select.py
    python3 scripts/replay_node_select.py --json outputs/replay_2026-07-28.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DUMPS = [
    ("삼성전기", "outputs/lp_009150_E.json"),
    ("LG화학", "outputs/lp_051910_E.json"),
    ("신한지주", "outputs/lp_055550_E.json"),
    ("NAVER", "outputs/lp_035420_E.json"),
    ("현대모비스", "outputs/ledger_provenance_012330_v3.json"),
]


def _revive(row: dict[str, Any], code: str) -> list[Any]:
    """덤프의 노드 dict를 EvidenceNode로 되살린다 — raw_text가 hint 원본이다."""
    from esgenie.ssot.evidence_graph import EvidenceNode

    out = []
    for n in row["nodes"]:
        if n.get("origin") not in ("ocr_structured", "ocr_unstructured"):
            continue          # 원장 풀은 OCR-only(_merge_ssot_evidence와 동일)
        out.append(EvidenceNode(
            id=n["id"], metric=code, value=n["value"], unit=n["unit"],
            period=n["period"], source="ocr/replay", raw_text=n.get("raw_text", ""),
            origin=n["origin"], source_file="", confidence=1.0,
        ))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="저장 덤프에 현재 선택 규칙 재적용")
    ap.add_argument("--json", dest="json_out", default="")
    args = ap.parse_args()

    from esgenie.knowledge.kesg_items import by_code
    from esgenie.ssot.node_select import (
        is_partial_aggregate,
        normalize_to_item_unit,
        select_representative_node,
    )

    report: list[dict[str, Any]] = []
    for label, path in DUMPS:
        d = json.loads((ROOT / path).read_text(encoding="utf-8"))
        year = d.get("report_year")
        print("\n" + "=" * 96)
        print(f"{label} ({d['corp_name']}) · report_year={year} · 항목 {len(d['rows'])}개")
        print("=" * 96)
        print(f"{'코드':8s} {'전(값/단위)':>26s}  {'후(값/단위)':>26s}  변화")
        for row in d["rows"]:
            code = row["code"]
            item = by_code(code)
            pool = _revive(row, code)
            picked = select_representative_node(code, pool, report_year=year)

            before = f"{row['ledger_value']} {row['ledger_unit'] or ''}".strip()
            changes: list[str] = []

            # 정성 항목 정량 차단 — 승격 경로와 같은 판정.
            qualitative = bool(item and item.data_type == "정성")
            if qualitative and picked is not None:
                picked = None
                changes.append("정성항목 정량차단")

            if picked is None:
                after = "— (미공시)"
                if not changes:
                    changes.append("미공시" if pool else "노드없음")
            else:
                value, unit, unit_flag = normalize_to_item_unit(
                    code, picked.value, picked.unit)
                after = f"{value} {unit or ''}".strip()
                if unit_flag:
                    changes.append(unit_flag)
                if is_partial_aggregate(picked):
                    changes.append("partial_value")
                if str(row["ledger_value"]) != str(value):
                    changes.append(f"값변경 {row['ledger_value']}→{value}")
                if (row["ledger_unit"] or "") != (unit or ""):
                    changes.append(f"단위 {row['ledger_unit']}→{unit}")

            # 전 상태의 단위 불일치 — 덤프가 계산해 둔 값.
            if row.get("unit_mismatch"):
                changes.append("(전)단위불일치")

            mark = " · ".join(changes) if changes else "불변"
            print(f"{code:8s} {before:>26s}  {after:>26s}  {mark}")
            report.append({
                "company": label, "code": code,
                "before_value": row["ledger_value"], "before_unit": row["ledger_unit"],
                "before_unit_mismatch": row.get("unit_mismatch"),
                "after_value": None if picked is None else after.split(" ")[0],
                "after_unit": None if picked is None else after.split(" ")[-1],
                "after_undisclosed": picked is None,
                "changes": changes,
            })

    # ── 요약 ────────────────────────────────────────────────────────────────
    print("\n" + "-" * 96)
    print("요약 — 회사별 공시 항목 수(E영역) 전후")
    for label, _ in DUMPS:
        rows = [r for r in report if r["company"] == label]
        before_n = len(rows)
        after_n = sum(1 for r in rows if not r["after_undisclosed"])
        partial = [r["code"] for r in rows if "partial_value" in r["changes"]]
        um_before = [r["code"] for r in rows if r["before_unit_mismatch"]]
        um_after = [r["code"] for r in rows if "unit_suspect" in r["changes"]]
        dropped = [r["code"] for r in rows if r["after_undisclosed"]]
        print(f"  {label:8s} 항목 {before_n} → {after_n}"
              f" · 미공시전환 {dropped}"
              f" · partial_value {partial}"
              f" · 단위불일치 {len(um_before)}→{len(um_after)}")
    print("-" * 96)

    if args.json_out:
        out = ROOT / args.json_out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"저장: {out}")


if __name__ == "__main__":
    main()
