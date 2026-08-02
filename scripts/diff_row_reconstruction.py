"""작업3 — 행 재구성 텍스트의 before/after diff. **라이브 전 무료 관문.**

`_attach_column_headers`의 2단 헤더 부착(2026-07-29)이 잘 되던 표를 망가뜨리지 않는지
5개사 PDF 전량으로 확인한다. 행 재구성은 **결정적(LLM 없음)**이므로 비용이 0이다.
캐시 무효화·라이브 과금 이전에 여기서 오부착을 잡는 것이 목적이다.

before는 `_attach_column_headers`의 연도 부착 부분을 끈 재구현이 아니라, 완성된 텍스트
에서 `(라벨|연도)` → `(라벨)` 로 되돌린 것이다. 재구현하면 '재구현이 틀렸을 가능성'이
diff에 섞여 판정이 흐려진다 — 되돌리기는 부착 형식만 가정하므로 그 위험이 없다.

출력 4종(프롬프트 §작업3):
  1. 회사별 부착/미부착 행 수 — 연도 부착 행이 몇 개 늘었는가
  2. 모비스 p.70 검증 — 원문 행 원문 그대로 인용
  3. 연도 없는 표 오부착 검출 — 연도 헤더가 없는 페이지에 연도가 붙었는가(0이어야 함)
  4. 텍스트 길이·청크 수 변화 — 라이브 비용 예측

사용:
    python3 scripts/diff_row_reconstruction.py                 # 5개사 요약
    python3 scripts/diff_row_reconstruction.py --ticker 012330 --page 70
    python3 scripts/diff_row_reconstruction.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MANIFEST = ROOT / "data" / "real_reports" / "manifest.json"

# 부착된 연도 꼬리 — `(합계|2024)`. before 복원과 검출에 같은 식을 쓴다.
YEAR_TAIL_RE = re.compile(r"\|(20\d{2})\)")
# 순수 연도 셀 — 그 페이지에 연도 헤더가 실재하는지 확인용(오부착 판정).
# 경계는 lookahead/lookbehind로 본다 — 구분자를 소비하면 '2022 | 2023 | 2024'에서
# 짝수번째 연도가 매칭되지 않아(비겹침) 실재하는 연도를 '창작'으로 오판한다.
PURE_YEAR_CELL_RE = re.compile(r"(?<![^|\s])(20\d{2})(?=\s*\||\s*$)")


def strip_years(text: str) -> str:
    """부착 후 텍스트 → 부착 전(연도 없음) 텍스트. `(합계|2024)` → `(합계)`."""
    return YEAR_TAIL_RE.sub(")", text)


def page_texts(pdf: Path) -> list[str]:
    import fitz

    from esgenie.ssot.ocr_router import _reconstruct_rows_from_dict

    doc = fitz.open(str(pdf))
    return [_reconstruct_rows_from_dict(p) for p in doc]


def chunk_count(text: str) -> int:
    """라이브 LLM 호출 수의 대리 지표 — `_split_text_chunks`와 같은 함수를 쓴다."""
    from esgenie.ssot.ocr_router import _split_text_chunks, _UNSTRUCTURED_CHUNK_CHARS

    return len(_split_text_chunks(text, _UNSTRUCTURED_CHUNK_CHARS))


def row_year_shape_ok(line: str) -> bool:
    """한 행의 연도 부착이 표 기하와 맞는가 — 오부착 판정의 실질 검사.

    표는 연도가 좌→우로 **오름차순 연속 블록**을 이룬다(2022×4 | 2023×4 | 2024×4).
    부착된 연도 열이 `2022 2023 2022`처럼 뒤섞이거나 블록 크기가 다르면 x매핑이
    어긋난 것이다. `_attach_column_headers`의 균등 가드가 헤더 컬럼 기준으로만
    검사하므로, 실제 값 행에서 결과를 다시 확인한다(가드가 통과시킨 오부착 검출).

    빈 셀('~')이 섞인 행은 블록 크기가 달라질 수 있어(모비스 '집약도' 행: 연도별 2개)
    크기 균등은 요구하지 않고 **순서와 연속성만** 본다.
    """
    seq = YEAR_TAIL_RE.findall(line)
    if not seq:
        return True
    blocks: list[str] = []
    for y in seq:
        if not blocks or blocks[-1] != y:
            blocks.append(y)
    return len(blocks) == len(set(blocks)) and blocks == sorted(blocks)


def analyze(pdf: Path) -> dict[str, Any]:
    after_pages = page_texts(pdf)
    rows: list[dict[str, Any]] = []
    misattached: list[dict[str, Any]] = []

    for pno, after in enumerate(after_pages, start=1):
        if "|" not in after:
            continue
        before = strip_years(after)
        if before == after:
            continue
        # ① 이 페이지에 연도 헤더 행이 실제로 있는가. 없는데 연도가 붙었다면 오부착이다.
        has_year_header = any(
            len(PURE_YEAR_CELL_RE.findall(line)) >= 2
            for line in before.splitlines()
        )
        page_years = {y for line in before.splitlines()
                      for y in PURE_YEAR_CELL_RE.findall(line)}
        attached_lines = [ln for ln in after.splitlines() if YEAR_TAIL_RE.search(ln)]
        years = sorted(set(YEAR_TAIL_RE.findall(after)))
        # ② 부착된 연도가 이 페이지 헤더에 실재하는 연도인가(없는 연도 창작 검출).
        alien = sorted(set(years) - page_years)
        # ③ 행 안에서 연도가 오름차순 연속 블록인가(x매핑 어긋남 검출).
        bad_shape = [ln for ln in attached_lines if not row_year_shape_ok(ln)]
        rows.append({"page": pno, "attached_rows": len(attached_lines), "years": years,
                     "has_year_header": has_year_header,
                     "alien_years": alien, "bad_shape_rows": len(bad_shape)})
        reasons = []
        if not has_year_header:
            reasons.append("연도 헤더 행 없음")
        if alien:
            reasons.append(f"페이지에 없는 연도 {alien}")
        if bad_shape:
            reasons.append(f"연도 순서/연속성 위반 {len(bad_shape)}행")
        if reasons:
            misattached.append({
                "page": pno, "years": years, "reasons": reasons,
                "sample": (bad_shape or attached_lines)[0][:220],
            })

    before_all = "\n".join(strip_years(t) for t in after_pages)
    after_all = "\n".join(after_pages)
    # 표가 있는 행(= '|' 포함) 중 연도가 붙지 않은 행 — 부착률 분모.
    table_rows = sum(1 for t in after_pages for ln in t.splitlines() if "|" in ln)
    attached = sum(1 for t in after_pages for ln in t.splitlines() if YEAR_TAIL_RE.search(ln))

    return {
        "pdf": pdf.name,
        "pages": len(after_pages),
        "table_rows": table_rows,
        "attached_rows": attached,
        "unattached_rows": table_rows - attached,
        "changed_pages": len(rows),
        "misattached_pages": misattached,
        "chars_before": len(before_all),
        "chars_after": len(after_all),
        "chunks_before": chunk_count(before_all),
        "chunks_after": chunk_count(after_all),
        "page_detail": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="행 재구성 before/after diff (무료·오프라인)")
    ap.add_argument("--ticker", default="", help="특정 회사만 (기본: manifest 전량)")
    ap.add_argument("--page", type=int, default=0, help="이 페이지의 before/after 행 전문 출력")
    ap.add_argument("--json", dest="json_out", default="", help="결과 JSON 저장 경로")
    args = ap.parse_args()

    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if args.ticker:
        entries = [e for e in entries if e["ticker"] == args.ticker]

    # ── 특정 페이지 전문 출력(모비스 p.70 검증용) ────────────────────────────
    if args.page:
        pdf = ROOT / entries[0]["pdf"]
        after = page_texts(pdf)[args.page - 1]
        before = strip_years(after)
        print("=" * 78)
        print(f"{pdf.name} p.{args.page} — before(연도 부착 없음)")
        print("=" * 78)
        for ln in before.splitlines():
            if "|" in ln:
                print(f"  {ln}")
        print("\n" + "=" * 78)
        print(f"{pdf.name} p.{args.page} — after(2단 헤더 연도 부착)")
        print("=" * 78)
        for ln in after.splitlines():
            if "|" in ln:
                print(f"  {ln}")
        return

    results = []
    for e in entries:
        pdf = ROOT / e["pdf"]
        if not pdf.exists():
            print(f"⚠ 없음: {pdf}")
            continue
        results.append(analyze(pdf))

    print("\n" + "=" * 78)
    print("행 재구성 diff — 2단 헤더 연도 부착 (오프라인·과금 0)")
    print("=" * 78)
    for r in results:
        print(f"\n[{r['pdf']}] {r['pages']}p")
        print(f"  표 행          : {r['table_rows']}개")
        print(f"  연도 부착 행    : {r['attached_rows']}개 "
              f"(미부착 {r['unattached_rows']}개)")
        print(f"  변경된 페이지    : {r['changed_pages']}개")
        print(f"  텍스트 길이     : {r['chars_before']:,} → {r['chars_after']:,} "
              f"(+{r['chars_after'] - r['chars_before']:,})")
        print(f"  청크 수        : {r['chunks_before']} → {r['chunks_after']} "
              f"(라이브 호출 수 대리 지표)")
        if r["misattached_pages"]:
            print(f"  ❌ 오부착 의심 {len(r['misattached_pages'])}페이지 "
                  f"— 연도 헤더가 없는데 연도가 붙었다:")
            for m in r["misattached_pages"][:5]:
                print(f"     p.{m['page']} {m['years']} — {', '.join(m['reasons'])}")
                print(f"       {m['sample']}")
        elif r["attached_rows"]:
            print("  ✅ 오부착 0 — 연도 헤더 실재 · 창작 연도 0 · 행 내 연도 오름차순 연속")
        else:
            print("  ✅ 변경 없음 — 이 회사는 2단 헤더 표가 없다(기존 동작 그대로)")
        for d in r["page_detail"][:8]:
            print(f"     p.{d['page']}: {d['attached_rows']}행 {d['years']}")
        if len(r["page_detail"]) > 8:
            print(f"     … 외 {len(r['page_detail']) - 8}페이지")

    total_mis = sum(len(r["misattached_pages"]) for r in results)
    print("\n" + "-" * 78)
    print("관문 판정")
    print(f"  총 오부착 의심 페이지: {total_mis}개")
    if total_mis:
        print("  ❌ 라이브를 돌리지 마라 — 오부착을 먼저 잡아야 한다")
    else:
        print("  ✅ 오프라인 관문 통과 — 작업4 라이브로 넘어갈 수 있다")
    print("-" * 78)

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"저장: {out}")


if __name__ == "__main__":
    main()
