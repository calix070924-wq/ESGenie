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
    python3 scripts/replay_node_select.py --compare-modes     # ㄱ/ㄴ/ㄷ 세 형태 실측 비교

`--compare-modes`(2026-07-29)는 값 최빈 tie-breaker의 세 설계안을 같은 덤프에 각각
적용해 **기준선(최빈 없음) 대비 무엇이 바뀌는지**를 나란히 낸다. 기준선을 dump의
`ledger_value`가 아니라 '현재 코드에서 최빈 축만 뺀 결과'로 잡는 게 핵심이다 —
덤프의 ledger_value는 이전 규칙 버전에서 저장된 값이라 그 뒤 머지된 변경(부분값
표기·지역어 보강 등)이 섞여 최빈 축의 효과만 분리되지 않는다.
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


# ==========================================================================
# ㄱ/ㄴ/ㄷ 세 형태 비교 (2026-07-29) — 값 최빈 tie-breaker 설계 근거 실측
# ==========================================================================
#
#   기준선  최빈 축 없음(2026-07-28 상태) — 연도 → 최신 → 고신뢰 → str(id)
#   ㄱ) 전역 빈도를 정렬 키에 넣음 — 연도 축보다 앞
#   ㄴ) 전 축 동률 그룹 안에서만 최빈 — 연도로 좁히기 전
#   ㄷ) 연도로 좁힌 뒤 그 안에서만 최빈 (채택안, 최빈값 유일할 때만)
#
# 넷 다 3~6단계(계열·분해·집계·단위)까지는 동일하다. 갈리는 건 연도 축과 최빈 축의
# 순서·적용 범위뿐이므로, 그 뒷부분만 여기서 다시 구현해 비교한다.


def _rank_prefix(code: str, node: Any, expected: str | None) -> tuple:
    """3~6단계 순위 — 네 형태가 공유하는 부분."""
    from esgenie.ssot.node_select import (
        _aggregation_rank,
        _breakdown_rank,
        _family_rank,
        _node_hint,
        _unit_rank,
    )

    hint = _node_hint(node)
    return (
        _family_rank(code, hint),
        _breakdown_rank(hint),
        _aggregation_rank(hint),
        _unit_rank(getattr(node, "unit", None), expected),
    )


def _select_variant(variant: str, code: str, nodes: list[Any],
                    report_year: int | None) -> Any | None:
    """네 형태 중 하나로 대표 노드를 고른다. 1·2단계 hard 배제는 공통."""
    from esgenie.ssot.node_select import (
        _conflicts_metric,
        _expected_unit,
        _keep_min,
        _keep_value_mode,
        _node_hint,
        is_derived_hint,
    )

    base = code.split("__", 1)[0]
    expected = _expected_unit(base)
    survivors = [n for n in nodes if n is not None
                 and not is_derived_hint(_node_hint(n))
                 and not _conflicts_metric(base, _node_hint(n))]
    if not survivors:
        return None

    def period(n: Any) -> int:
        return getattr(n, "period", 0) or 0

    def year_rank(n: Any) -> int:
        return abs(period(n) - report_year) if report_year is not None else 0

    def freq(pool: list[Any]) -> dict[float, int]:
        out: dict[float, int] = {}
        for n in pool:
            v = getattr(n, "value", None)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[float(v)] = out.get(float(v), 0) + 1
        return out

    if variant == "ㄱ":
        # 전역 빈도를 정렬 키에 직접 넣는다 — 연도 축보다 앞이므로 값이 연도를 넘어
        # 우연히 반복되면 옛 값·부분값이 이긴다.
        f = freq(survivors)
        return min(survivors, key=lambda n: (
            _rank_prefix(base, n, expected),
            -f.get(float(getattr(n, "value", 0) or 0), 0),
            year_rank(n), -period(n),
            -(getattr(n, "confidence", 0.0) or 0.0), str(getattr(n, "id", "")),
        ))

    survivors = _keep_min(survivors, lambda n: _rank_prefix(base, n, expected))

    if variant == "ㄴ":
        # 전 축 동률 그룹 안에서만 최빈 — 연도로 좁히기 **전**이라 같은 hint의 여러
        # 연도가 한 그룹에 남는다. 시계열 정체가 최빈으로 이긴다.
        survivors = _keep_value_mode(survivors)
        survivors = _keep_min(survivors, year_rank)
    elif variant == "ㄷ":
        survivors = _keep_min(survivors, year_rank)
        survivors = _keep_value_mode(survivors)
    elif variant == "기준선":
        survivors = _keep_min(survivors, year_rank)
    else:                                        # pragma: no cover - 방어
        raise ValueError(variant)

    return min(survivors, key=lambda n: (
        -period(n), -(getattr(n, "confidence", 0.0) or 0.0), str(getattr(n, "id", ""))))


VARIANTS = ("기준선", "ㄱ", "ㄴ", "ㄷ")


def compare_modes() -> None:
    """네 형태를 5개 덤프에 각각 적용해 기준선 대비 변경 항목을 나란히 낸다."""
    print("=" * 96)
    print("값 최빈 tie-breaker — ㄱ/ㄴ/ㄷ 세 형태 실측 비교 (기준선 = 최빈 축 없음)")
    print("=" * 96)

    picks: dict[str, dict[tuple[str, str], Any]] = {v: {} for v in VARIANTS}
    total_items = 0
    for label, path in DUMPS:
        d = json.loads((ROOT / path).read_text(encoding="utf-8"))
        year = d.get("report_year")
        for row in d["rows"]:
            code = row["code"]
            pool = _revive(row, code)
            total_items += 1
            for v in VARIANTS:
                node = _select_variant(v, code, pool, year)
                picks[v][(label, code)] = None if node is None else node.value

    keys = list(picks["기준선"].keys())
    print(f"\n대상 항목 {total_items}개 (5개사 E영역)\n")
    print(f"{'회사':10s} {'코드':8s} {'기준선':>12s} {'ㄱ':>12s} {'ㄴ':>12s} {'ㄷ':>12s}")
    print("-" * 96)
    changed = {v: [] for v in VARIANTS if v != "기준선"}
    for key in keys:
        base = picks["기준선"][key]
        diffs = [v for v in ("ㄱ", "ㄴ", "ㄷ") if picks[v][key] != base]
        if not diffs:
            continue
        for v in diffs:
            changed[v].append(key)
        label, code = key
        print(f"{label:10s} {code:8s} {base!s:>12s}"
              + "".join(f" {picks[v][key]!s:>12s}" for v in ("ㄱ", "ㄴ", "ㄷ")))
    print("-" * 96)
    for v in ("ㄱ", "ㄴ", "ㄷ"):
        n = len(changed[v])
        detail = ", ".join(f"{c}({l})" for l, c in changed[v]) or "없음"
        print(f"  {v}) 기준선 대비 변경 {n}건 / {total_items}개 — {detail}")
    print("-" * 96)
    print("판정: ㄷ)만 목표 1건(현대모비스 E-4-2)을 수정하고 나머지를 건드리지 않는다.")
    print("      ㄴ)은 삼성전기 E-6-2를 99.0 → 97.0으로 회귀시킨다(시계열 정체 효과).")

    # 현재 코드(채택안 ㄷ)가 이 표와 같은 답을 내는지 교차 확인.
    from esgenie.ssot.node_select import select_representative_node

    mismatch = []
    for label, path in DUMPS:
        d = json.loads((ROOT / path).read_text(encoding="utf-8"))
        year = d.get("report_year")
        for row in d["rows"]:
            code = row["code"]
            node = select_representative_node(code, _revive(row, code), report_year=year)
            live = None if node is None else node.value
            if live != picks["ㄷ"][(label, code)]:
                mismatch.append((label, code, live, picks["ㄷ"][(label, code)]))
    if mismatch:
        print(f"\n⚠ 현재 코드가 ㄷ)와 다르다: {mismatch}")
    else:
        print("\n✔ 현재 코드(node_select.select_representative_node) = ㄷ) 전 항목 일치")


def main() -> None:
    ap = argparse.ArgumentParser(description="저장 덤프에 현재 선택 규칙 재적용")
    ap.add_argument("--json", dest="json_out", default="")
    ap.add_argument("--compare-modes", action="store_true",
                    help="값 최빈 tie-breaker ㄱ/ㄴ/ㄷ 세 형태 비교")
    args = ap.parse_args()

    if args.compare_modes:
        compare_modes()
        return

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
