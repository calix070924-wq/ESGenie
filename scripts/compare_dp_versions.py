# -*- coding: utf-8 -*-
"""compare_dp_versions.py — Upstage Document Parse 구버전 vs document-parse-260630 라이브 비교.

2026-07-31 기본 alias 전환(260128→260630) 전에 신버전을 미리 켜보고
① 속도(throughput +60% 공지 검증) ② 정확도(핵심 ESG 수치) ③ words 옵션(단어단위 bbox)
응답 형태를 확인한다.

⚠ 샌드박스는 api.upstage.ai egress 차단 확인됨 → 반드시 Mac(.env에 유효 UPSTAGE_API_KEY)에서 실행.

사용:
    python -m scripts.compare_dp_versions
"""
from __future__ import annotations

import glob
import sys
import time

sys.path.insert(0, ".")
from esgenie.config import SETTINGS  # noqa: F401  (.env 로드)
from esgenie.ssot import ocr_router as R

OLD_MODEL = "document-parse"           # 현재 default alias → 7/31까지 260128
NEW_MODEL = "document-parse-260630"    # 신버전 명시 pin

D = "시연증빙세트_한울정밀공업/"
GT = [
    (D + "01_*.pdf", "kepco_bill", [("E-4-1", 142560.0, "kWh", 0.5)]),
    (D + "02_*.pdf", "gas_bill", [("E-4-1", 360772.0, "MJ", 0.5)]),
    (D + "03_*.pdf", "waste_ledger", [("E-6-1", 18.4, "ton", 0.05),
                                       ("E-6-2", 29.3, "%", 0.05)]),
]


def _run_one(file_path: str, doc_type: str, model: str) -> dict:
    t0 = time.perf_counter()
    payload = R._call_upstage_dp_payload(file_path, ocr_mode="force", model=model)
    elapsed = time.perf_counter() - t0
    ext = R._tokens_to_extraction(
        payload["tokens"],
        doc_type=doc_type,
        file_path=file_path,
        engine="upstage_dp",
        tables=payload.get("tables") or [],
        engine_meta={"upstage_model": model},
    )
    R._backfill_kesg_codes(ext)
    n_cells = sum(len(t.cells) for t in ext.tables)
    return {"elapsed": elapsed, "ext": ext, "n_tables": len(ext.tables), "n_cells": n_cells}


def _score(ext, items) -> tuple[int, int]:
    hit = total = 0
    for code, want, unit, tol in items:
        total += 1
        cands = [m for m in ext.metrics if m.kesg_code_guess == code]
        got = cands[0].value if cands else None
        if got is not None and abs(got - want) <= tol:
            hit += 1
    return hit, total


def main() -> None:
    key = R._get_upstage_key()
    print("Upstage API 키:", "있음 ✅" if key else "없음 ⚠️ → .env에 UPSTAGE_API_KEY 설정 후 Mac에서 실행")
    if not key:
        return

    rows = []
    for pat, doc_type, items in GT:
        matches = sorted(glob.glob(pat))
        if not matches:
            print(f"  (없음) {pat}")
            continue
        f = matches[0]
        name = f.split("/")[-1]
        for model in (OLD_MODEL, NEW_MODEL):
            try:
                r = _run_one(f, doc_type, model)
                hit, total = _score(r["ext"], items)
                rows.append({
                    "file": name, "model": model, "elapsed": r["elapsed"],
                    "hit": hit, "total": total,
                    "n_tables": r["n_tables"], "n_cells": r["n_cells"],
                    "error": None,
                })
            except Exception as exc:
                rows.append({"file": name, "model": model, "error": str(exc)})

    print(f"\n{'파일':26} {'모델':24} {'시간(s)':8} {'정확도':8} {'표/셀':10}")
    print("-" * 82)
    for r in rows:
        if r.get("error"):
            print(f"{r['file']:26} {r['model']:24} 실패: {r['error'][:40]}")
            continue
        acc = f"{r['hit']}/{r['total']}"
        tc = f"{r['n_tables']}/{r['n_cells']}"
        print(f"{r['file']:26} {r['model']:24} {r['elapsed']:>7.2f} {acc:>8} {tc:>10}")

    # 같은 파일의 old/new 쌍으로 속도 개선폭 요약
    print("\n[속도 개선 요약]")
    by_file: dict[str, dict[str, float]] = {}
    for r in rows:
        if r.get("error"):
            continue
        by_file.setdefault(r["file"], {})[r["model"]] = r["elapsed"]
    for f, m in by_file.items():
        if OLD_MODEL in m and NEW_MODEL in m and m[OLD_MODEL] > 0:
            speedup = (m[OLD_MODEL] - m[NEW_MODEL]) / m[OLD_MODEL] * 100
            print(f"  {f}: {m[OLD_MODEL]:.2f}s → {m[NEW_MODEL]:.2f}s  ({speedup:+.0f}%)")

    print("\n[참고] 표 셀 bbox는 현재 두 버전 모두 표 전체 외접bbox 공유(셀별 좌표 없음).")
    print("       words 옵션(단어단위 bbox) 실측은 scripts/probe_words_option.py 참고.")


if __name__ == "__main__":
    main()
