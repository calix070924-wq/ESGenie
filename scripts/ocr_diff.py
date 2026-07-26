"""두 실행의 OCR 추출 결과를 비교한다 — 비결정성의 실제 폭을 측정하는 도구.

캐시(esgenie/ssot/ocr_cache.py)는 변동을 **고정**할 뿐 **없애지 않는다.** 캐시를 끄거나
갱신할 때마다 입력이 얼마나 흔들리는지 계속 재보고 있어야 한다. 그 관찰용이다.

입력은 `scripts/inspect_ledger_provenance.py --json`이 저장한 파일 두 개다
(`rows[].nodes[]`에 period·value·unit·hint가 들어 있다).

비교 항목:
  1. 코드별 노드 수 변화
  2. hint 집합의 자카드 유사도 + 공통/소실/신규 hint 목록
  3. 같은 hint의 값·period 변화
  4. 전체 hint 공통 비율

사용:
    python3 scripts/ocr_diff.py outputs/lp_v4a.json outputs/lp_v4b.json
    python3 scripts/ocr_diff.py a.json b.json --show 8       # 코드별 목록 표시 개수
    python3 scripts/ocr_diff.py a.json b.json --codes E-4-1 E-6-1
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load(path: str) -> dict[str, list[dict[str, Any]]]:
    """ledger_provenance JSON → {코드: [노드 dict]}."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {r["code"]: r.get("nodes", []) for r in data.get("rows", [])}


def _hints(nodes: list[dict[str, Any]]) -> set[str]:
    return {str(n.get("hint", "")).strip() for n in nodes if str(n.get("hint", "")).strip()}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def _values_by_hint(nodes: list[dict[str, Any]]) -> dict[str, list[tuple[Any, Any, Any]]]:
    """hint → [(value, unit, period)] — 같은 hint가 여러 값을 갖는 게 정상이다(연도 열)."""
    out: dict[str, list[tuple[Any, Any, Any]]] = {}
    for n in nodes:
        hint = str(n.get("hint", "")).strip()
        if hint:
            out.setdefault(hint, []).append((n.get("value"), n.get("unit"), n.get("period")))
    for vs in out.values():
        vs.sort(key=lambda t: (str(t[0]), str(t[1]), str(t[2])))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="두 실행의 OCR 추출 결과 비교")
    ap.add_argument("left", help="기준 JSON (예: 1회차)")
    ap.add_argument("right", help="비교 JSON (예: 2회차)")
    ap.add_argument("--show", type=int, default=5, help="코드별로 표시할 hint 개수 (기본 5)")
    ap.add_argument("--codes", nargs="*", default=None, help="특정 코드만 비교")
    args = ap.parse_args()

    left, right = _load(args.left), _load(args.right)
    codes = sorted(set(left) | set(right))
    if args.codes:
        codes = [c for c in codes if c in set(args.codes)]

    print("=" * 78)
    print(f"OCR 추출 비교   L={Path(args.left).name}   R={Path(args.right).name}")
    print("=" * 78)

    print(f"\n{'코드':<8} {'L노드':>6} {'R노드':>6} {'L힌트':>6} {'R힌트':>6} "
          f"{'공통':>5} {'소실':>5} {'신규':>5} {'자카드':>7}")
    print("-" * 78)

    tot_common = tot_union = 0
    tot_l = tot_r = 0
    detail: list[tuple[str, set[str], set[str], set[str]]] = []
    value_changes: list[tuple[str, str, list, list]] = []

    for code in codes:
        ln, rn = left.get(code, []), right.get(code, [])
        lh, rh = _hints(ln), _hints(rn)
        common, lost, new = lh & rh, lh - rh, rh - lh
        tot_common += len(common)
        tot_union += len(lh | rh)
        tot_l += len(ln)
        tot_r += len(rn)
        print(f"{code:<8} {len(ln):>6} {len(rn):>6} {len(lh):>6} {len(rh):>6} "
              f"{len(common):>5} {len(lost):>5} {len(new):>5} {_jaccard(lh, rh):>7.2f}")
        if lost or new:
            detail.append((code, common, lost, new))
        # 같은 hint인데 값 집합이 달라진 경우 — 추출 자체가 흔들린 것.
        lv, rv = _values_by_hint(ln), _values_by_hint(rn)
        for hint in sorted(common):
            if lv.get(hint) != rv.get(hint):
                value_changes.append((code, hint, lv.get(hint, []), rv.get(hint, [])))

    if detail:
        print("\n" + "-" * 78)
        print("hint 변화 상세 (소실 = L에만, 신규 = R에만)")
        for code, _common, lost, new in detail:
            print(f"\n[{code}]")
            for h in sorted(lost)[:args.show]:
                print(f"  - {h[:70]}")
            if len(lost) > args.show:
                print(f"  … 소실 {len(lost) - args.show}건 더")
            for h in sorted(new)[:args.show]:
                print(f"  + {h[:70]}")
            if len(new) > args.show:
                print(f"  … 신규 {len(new) - args.show}건 더")

    if value_changes:
        print("\n" + "-" * 78)
        print("같은 hint · 값 변화 (같은 라벨인데 추출 값이 달라진 것)")
        for code, hint, lv, rv in value_changes[:30]:
            print(f"\n[{code}] {hint[:60]}")
            print(f"  L: {[(v, u, p) for v, u, p in lv][:6]}")
            print(f"  R: {[(v, u, p) for v, u, p in rv][:6]}")
        if len(value_changes) > 30:
            print(f"\n… 값 변화 {len(value_changes) - 30}건 더")

    print("\n" + "-" * 78)
    print("요약")
    print(f"  전체 노드      : L {tot_l} → R {tot_r}  (Δ{tot_r - tot_l:+d})")
    print(f"  hint 공통 비율 : {tot_common}/{tot_union} = "
          f"{(tot_common / tot_union * 100) if tot_union else 100.0:.1f}%")
    print(f"  hint 집합이 바뀐 코드 : {len(detail)}개 / {len(codes)}개")
    print(f"  같은 hint 값 변화     : {len(value_changes)}건")
    identical = tot_l == tot_r and not detail and not value_changes
    print(f"  판정 : {'완전 동일 — 재현됨' if identical else '차이 있음 — 추출이 흔들렸다'}")
    print("-" * 78)


if __name__ == "__main__":
    main()
