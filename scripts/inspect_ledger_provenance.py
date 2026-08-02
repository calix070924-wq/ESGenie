"""작업0 — L1 원장 값의 출처(provenance)를 코드별로 찍는다.

`docs/역추적_claim오염_경로분리_2026-07-25.md`의 경로 판정([A] DART 무게이트 정규식 vs
[B] 게이트 통과한 OCR 노드 승격)을 라이브 1회로 확정하기 위한 관찰 전용 스크립트.

**L0 + L1만 돌린다.** L2 초안 생성·L3 검출·L4 검증 루프를 타지 않으므로 LLM 호출이 없고
비용은 OCR 1회뿐이다(풀 파이프라인 30~60분 → 대폭 단축).

출력 3종:
  1. 코드별 원장 값/단위/note → note 문자열로 [A]/[B] 판정
  2. 같은 코드의 그래프 노드 전량(period·value·unit·hint·origin)
  3. **풀 구성 차이로 갈리는 코드**: 원장(ocr_* 노드만)과 D1(search_nodes — DART 포함)이
     같은 규칙을 각자 돌렸을 때 다른 노드를 고르는 코드. 두 호출부가 공용 함수를
     공유해도 **넘기는 풀이 다르면** 갈린다 — 실측 재현 구조다.
     2026-07-26(2차)부터 원장이 자기 결정을 `graph.representative_node_ids`에 남기고
     D1이 그것을 따르므로, 이 항목이 0이 아니어도 실제 비교는 어긋나지 않는다.
     여기서 0이 아닌 코드는 '기록 공유가 실제로 일하고 있는 지점'이다.
     (`스크립트 재현 ≠ 실행 기록`이 0이 아니면 이 스크립트의 풀 재현이 틀린 것이다.)

사용:
    python3 scripts/inspect_ledger_provenance.py                      # 현대모비스
    python3 scripts/inspect_ledger_provenance.py --ticker 051910      # LG화학
    python3 scripts/inspect_ledger_provenance.py --areas E S          # 영역 필터
    python3 scripts/inspect_ledger_provenance.py --json out.json      # 기계 판독용 저장
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MANIFEST = ROOT / "data" / "real_reports" / "manifest.json"

# note 문자열 → 공급 경로 라벨. dart_client / ssot_pipeline이 남기는 note를 그대로 읽는다.
_PATH_BY_NOTE: list[tuple[str, str]] = [
    ("DART 원문 정규식 추출", "[A] DART 정규식 (무게이트)"),
    ("구조화 API", "[A'] DART 구조화 API (신뢰)"),
    ("DART 배당 구조화 API", "[A'] DART 구조화 API (신뢰)"),
    ("DART 사외이사", "[A'] DART 구조화 API (신뢰)"),
    ("OCR 정량 증빙으로 자동 인식", "[B] OCR 노드 승격 (게이트 통과)"),
    ("OCR 정성 증빙으로 자동 인식", "[B] OCR 정성 노드"),
    ("설문", "[C] 설문 주입"),
]


def _hint_of(node: Any) -> str:
    """노드의 원 metric_hint 복원.

    EvidenceNode는 hint를 별도 필드로 갖지 않는다. merge_ocr_extraction이
    raw_text="{hint}={value}{unit} ({file})" 로 합쳐 저장하므로 첫 '=' 앞을 떼어낸다.
    """
    raw = str(getattr(node, "raw_text", "") or "")
    return raw.split("=", 1)[0].strip() if "=" in raw else raw.strip()


def classify_path(note: Any) -> str:
    text = str(note or "")
    for needle, label in _PATH_BY_NOTE:
        if needle in text:
            return label
    return "[?] 판정 불가" if text else "[?] note 없음"


def main() -> None:
    ap = argparse.ArgumentParser(description="L1 원장 값 출처 점검 (L0+L1만 실행)")
    ap.add_argument("--ticker", default="012330", help="manifest ticker (기본: 현대모비스)")
    ap.add_argument("--areas", nargs="+", default=["E", "S", "G"], choices=["E", "S", "G", "P"])
    ap.add_argument("--json", dest="json_out", default="", help="결과 JSON 저장 경로")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    entry = next(e for e in json.loads(MANIFEST.read_text(encoding="utf-8"))
                 if e["ticker"] == args.ticker)
    pdf = ROOT / entry["pdf"]
    corp_code = entry.get("corp_code", entry["ticker"])

    # ── L0 + L1만 (pipeline.run은 L2~L5까지 가므로 직접 조립) ────────────────
    from esgenie.dart_client import load_report
    from esgenie.pipeline import _collect_ocr_extractions
    from esgenie.ssot import evidence_graph as ssot_evidence_graph
    from esgenie.ssot.node_select import select_representative_node
    from esgenie.ssot.ssot_pipeline import extract_with_ssot
    from esgenie.knowledge.kesg_items import by_code

    report = load_report(corp_code)
    extractions = _collect_ocr_extractions({pdf.name: str(pdf)})

    from esgenie.ssot import ocr_cache as _ocr_cache
    cache_hits, cache_misses, cache_mode = _ocr_cache.summarize(extractions)

    # 원장이 DART 정규식만으로 어떤 값을 갖고 있었는지 = 그래프 병합 이전 스냅샷.
    dart_only = {c: dict(e) for c, e in (report.kesg_data or {}).items()}

    graph = ssot_evidence_graph.build_unified_graph(
        report, extractions,
        corp_code=corp_code, corp_name=report.corp_name,
        report_year=report.report_year,
    )
    extraction = extract_with_ssot(report, graph)

    ref_year = getattr(graph, "report_year", None) or report.report_year

    rows: list[dict[str, Any]] = []
    for code in sorted(extraction.mapped):
        item = by_code(code)
        if item is None or item.area not in args.areas:
            continue
        led = extraction.mapped[code]
        nodes = [n for n in graph.nodes.values() if n.metric == code]
        nodes.sort(key=lambda n: n.period)

        # 두 경로의 **후보 풀 구성을 각각 흉내내서** 규칙을 돌린다(2026-07-26 2차).
        # 같은 인자를 같은 함수에 두 번 넘기면 "불일치 0개"가 항상 참이라 아무것도
        # 검증하지 않는다. 실제 갈림은 규칙이 아니라 풀에서 온다:
        #   원장  _merge_ssot_evidence : origin이 ocr_* 인 노드 + metric 정확 일치
        #   D1    _score_d1_numeric    : graph.search_nodes(keywords=[code]) — DART 포함,
        #                                부분포함 매칭. (claim이 없어 단위 필터는 생략)
        ledger_pool = [n for n in nodes
                       if getattr(n, "origin", "") in ("ocr_structured", "ocr_unstructured")]
        d1_pool = graph.search_nodes(keywords=[code])
        ledger_pick = select_representative_node(code, ledger_pool, report_year=ref_year)
        d1_pick = select_representative_node(code, d1_pool, report_year=ref_year)
        # 원장이 실행 중에 실제로 남긴 결정 — D1이 따라 쓰는 값. 위 재현과 일치해야 한다.
        recorded_id = getattr(graph, "representative_node_ids", {}).get(code)

        # 대표 노드와 같은 연도의 형제 노드 — 값이 흩어져 있으면 연도 규칙만으로는 못 고른다.
        siblings = [n for n in nodes if ledger_pick and n.period == ledger_pick.period]
        sibling_count = len(siblings)
        sibling_min = min((n.value for n in siblings), default=None)
        sibling_max = max((n.value for n in siblings), default=None)
        rows.append({
            "code": code,
            "name": item.name,
            "expected_unit": item.unit,
            "ledger_value": led.get("value"),
            "ledger_unit": led.get("unit"),
            "note": led.get("note"),
            "path": classify_path(led.get("note")),
            "dart_only_value": (dart_only.get(code) or {}).get("value"),
            # EvidenceNode에는 metric_hint 필드가 없다 — 원 hint는 raw_text에
            # "{hint}={value}{unit} (file)" 형태로 들어 있다(evidence_graph:352).
            "nodes": [
                {"period": n.period, "value": n.value, "unit": n.unit,
                 "raw_text": getattr(n, "raw_text", "") or "",
                 "hint": _hint_of(n),
                 # 원문에 연도가 없어 report_year로 채운 값인가(2026-07-29). 이 표시가
                 # 없으면 '2025 실적'과 '연도 미상'이 출력에서 구분되지 않는다.
                 "period_inferred": bool(getattr(n, "period_inferred", False)),
                 "origin": getattr(n, "origin", ""), "id": n.id}
                for n in nodes
            ],
            # 대표 노드의 연도가 폴백값인가 — 원장 값 해석을 바꾸는 정보다.
            "ledger_period_inferred": bool(
                ledger_pick is not None and getattr(ledger_pick, "period_inferred", False)
            ),
            # 풀 구성 차이로 두 경로가 다른 노드를 가리키는가. 이게 진짜 신호다 —
            # 원장의 결정 기록(representative_node_ids)이 이 차이를 흡수해야 한다.
            "selector_split": bool(
                ledger_pick is not None and d1_pick is not None
                and ledger_pick.id != d1_pick.id
            ),
            "ledger_pick": ledger_pick.id if ledger_pick else None,
            "d1_pick": d1_pick.id if d1_pick else None,
            "recorded_pick": recorded_id,
            # 재현한 원장 선택과 실행 중 실제 기록이 다르면 스크립트 재현이 틀린 것이다.
            "record_mismatch": bool(
                (ledger_pick.id if ledger_pick else None) != recorded_id
            ),
            "ledger_pool_size": len(ledger_pool),
            "d1_pool_size": len(d1_pool),
            "unit_mismatch": bool(
                led.get("unit") and item.unit and str(led["unit"]).strip() != str(item.unit).strip()
            ),
            # 대표값이 '연도'만으로 결정되지 않는 정도 — 같은 연도에 형제 노드가 몇 개인가.
            # 사업장별·범주별 값이 한 코드에 뭉쳐 있으면 연도 규칙은 무력하다.
            "sibling_count": sibling_count,
            "sibling_min": sibling_min,
            "sibling_max": sibling_max,
        })

    # ── 출력 ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print(f"L1 원장 출처 점검 — {report.corp_name} ({corp_code}) · report_year={ref_year}")
    print(f"영역 {'·'.join(args.areas)} · 항목 {len(rows)}개 · 그래프 노드 {len(graph.nodes)}개")
    # 캐시 히트를 감추지 않는다 — 이 실행이 라이브 추출인지 캐시 리플레이인지가
    # 결과 해석을 완전히 바꾼다(miss면 노드/hint가 지난 실행과 다를 수 있다).
    if cache_misses:
        cache_note = "   ← 라이브 추출 포함 (다음 실행부터 재현)"
    elif cache_hits:
        cache_note = "   ← 전량 캐시 리플레이 (추출 고정)"
    else:
        cache_note = "   ← 캐시 경로 미사용 (mock/구조화 채널)"
    print(f"OCR 캐시 : hit {cache_hits} / miss {cache_misses}  (mode={cache_mode})" + cache_note)
    print("=" * 78)

    for r in rows:
        print(f"\n[{r['code']}] {r['name']}")
        print(f"  원장   : {r['ledger_value']} {r['ledger_unit'] or ''}"
              f"   (항목 단위 {r['expected_unit']})"
              + ("   ⚠단위불일치" if r["unit_mismatch"] else ""))
        print(f"  경로   : {r['path']}")
        print(f"  note   : {r['note']}")
        if r["dart_only_value"] is not None:
            print(f"  DART값 : {r['dart_only_value']}  (그래프 병합 이전)")
        if not r["nodes"]:
            print("  노드   : 없음 — D1 비교 대상 없음")
        for n in r["nodes"]:
            mark = ""
            if n["id"] == r["ledger_pick"]:
                mark += " ←원장풀선택"
            if n["id"] == r["d1_pick"]:
                mark += " ←D1풀선택"
            if n["id"] == r["recorded_pick"]:
                mark += " ★원장기록"
            # 연도가 폴백값이면 연도 뒤에 '?'를 붙인다 — 2025와 미상을 눈으로 가른다.
            period_txt = f"{n['period']}?" if n.get("period_inferred") else str(n["period"])
            print(f"  노드   : {period_txt}  {n['value']} {n['unit'] or ''}"
                  f"  [{n['origin']}] {n['hint'][:48]}{mark}")
        print(f"  풀     : 원장 {r['ledger_pool_size']}개(ocr_*만) · "
              f"D1 {r['d1_pool_size']}개(search_nodes — DART 포함, 단위 필터 생략)")
        if r["selector_split"]:
            print("  ⚠ 풀 구성 차이로 갈림 — 원장풀 선택 ≠ D1풀 선택 "
                  "(D1은 ★원장기록을 따라가므로 실제 비교는 어긋나지 않는다)")
        if r["record_mismatch"]:
            print("  ⚠ 스크립트 재현 ≠ 실행 중 기록 — 풀 재현 로직이 원장과 어긋났다")
        if r["ledger_period_inferred"]:
            print("  ⚠ 대표 노드의 연도가 추론값 — 원문에 연도가 없어 보고연도로 채웠다 "
                  "(period_inferred). 원장 값의 연도를 신뢰하지 마라")
        if r["sibling_count"] > 1:
            print(f"  ⚠ 동일 코드·동일 연도 노드 {r['sibling_count']}개 "
                  f"(값 범위 {r['sibling_min']} ~ {r['sibling_max']}) "
                  f"— 연도만으로는 대표값이 결정되지 않는다")

    # 요약
    by_path: dict[str, int] = {}
    for r in rows:
        by_path[r["path"]] = by_path.get(r["path"], 0) + 1
    splits = [r["code"] for r in rows if r["selector_split"]]
    units = [r["code"] for r in rows if r["unit_mismatch"]]
    orphan = [r["code"] for r in rows if not r["nodes"]]
    unrecorded = [r["code"] for r in rows if r["nodes"] and not r["recorded_pick"]]
    rec_bad = [r["code"] for r in rows if r["record_mismatch"]]
    inferred_codes = [r["code"] for r in rows if r["ledger_period_inferred"]]
    inferred_nodes = sum(
        1 for r in rows for n in r["nodes"] if n.get("period_inferred"))
    ocr_nodes = sum(
        1 for r in rows for n in r["nodes"] if str(n.get("origin", "")).startswith("ocr"))

    print("\n" + "-" * 78)
    print("요약")
    for p, c in sorted(by_path.items()):
        print(f"  {p}: {c}개")
    print(f"  풀 구성 차이로 갈리는 코드: {len(splits)}개 {splits}")
    print(f"  원장 대표노드 기록 없음(D1 폴백 경로): {len(unrecorded)}개 {unrecorded}")
    print(f"  스크립트 재현 ≠ 실행 기록: {len(rec_bad)}개 {rec_bad}")
    print(f"  원장 단위 ≠ 항목 단위: {len(units)}개 {units}")
    print(f"  비교 노드 없음(claim만 존재): {len(orphan)}개 {orphan}")
    # 연도 폴백 실태 — 작업4 라이브 판정의 핵심 지표다(모비스 감소 / 나머지 불변).
    pct = (100.0 * inferred_nodes / ocr_nodes) if ocr_nodes else 0.0
    print(f"  연도 추론 노드(period_inferred): {inferred_nodes}/{ocr_nodes} OCR노드 ({pct:.1f}%)")
    print(f"  대표 노드가 추론 연도인 코드: {len(inferred_codes)}개 {inferred_codes}")
    print("-" * 78)

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(
            {"corp_code": corp_code, "corp_name": report.corp_name,
             "report_year": ref_year,
             # 이 스냅샷이 라이브 추출인지 캐시 리플레이인지 — ocr_diff.py로 두 파일을
             # 비교할 때 '왜 달라졌는가'의 1차 판정 근거다.
             "ocr_cache": {"mode": cache_mode, "hits": cache_hits, "misses": cache_misses},
             "rows": rows},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"저장: {out}")


if __name__ == "__main__":
    main()
