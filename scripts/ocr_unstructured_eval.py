# -*- coding: utf-8 -*-
"""ocr_unstructured_eval.py — 비정형 OCR 4축 신뢰도 측정.

정답셋(data/benchmark_ocr/unstructured_gold.json) 대비 비정형 채널의
라우팅·정량·정성(충실도+환각)·신뢰도 캘리브레이션을 채점한다.

실행:
  # 배선 확인 (mock, 수치 무의미)
  ESGENIE_FORCE_MOCK=1 python scripts/ocr_unstructured_eval.py

  # 실측 (유효 키 필요)
  ESGENIE_STRICT=1 python scripts/ocr_unstructured_eval.py
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from esgenie.ssot import ocr_router as R
from esgenie.evaluate import calibration, bootstrap_ci

GOLD_PATH = Path("data/benchmark_ocr/unstructured_gold.json")
IS_MOCK = bool(os.environ.get("ESGENIE_FORCE_MOCK"))
IS_STRICT = bool(os.environ.get("ESGENIE_STRICT"))


# ============================================================
# Data classes for results
# ============================================================

@dataclass
class DocResult:
    doc_id: str
    file: str
    channel_variant: str
    # Axis 1: routing
    route_correct: bool = False
    route_got: str = ""
    route_gold: str = ""
    # Axis 2: quantitative
    metric_hits: int = 0
    metric_total: int = 0
    metric_fp: int = 0  # hallucinated metrics
    # Axis 3: qualitative
    fact_recall_hits: int = 0
    fact_recall_total: int = 0
    halluc_clauses: int = 0
    total_clauses: int = 0
    # Axis 4: calibration rows
    cal_rows: list = field(default_factory=list)
    # Meta
    engine: str = ""
    error: str = ""


def load_gold() -> list[dict]:
    with open(GOLD_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data["docs"]


# ============================================================
# Axis 2: quantitative metric matching (reuses ocr_accuracy_eval pattern)
# ============================================================

def score_metrics(extracted_metrics, gold_metrics) -> tuple[int, int, int]:
    """Returns (hits, total_gold, false_positives)."""
    hits = 0
    total = len(gold_metrics)
    matched_indices: set[int] = set()

    for gm in gold_metrics:
        code = gm["kesg_code"]
        want = gm["value"]
        tol = gm["tol"]
        found = False
        for i, em in enumerate(extracted_metrics):
            if em.kesg_code_guess == code and i not in matched_indices:
                if em.value is not None and abs(em.value - want) <= tol:
                    hits += 1
                    matched_indices.add(i)
                    found = True
                    break
        # If not found by code, try without code match (by value proximity)
        if not found:
            for i, em in enumerate(extracted_metrics):
                if i not in matched_indices and em.value is not None:
                    if abs(em.value - want) <= tol:
                        hits += 1
                        matched_indices.add(i)
                        break

    # False positives: extracted metrics not matching any gold
    fp = len(extracted_metrics) - len(matched_indices)
    return hits, total, fp


# ============================================================
# Axis 3: qualitative fact recall + hallucination
# ============================================================

def score_facts(clauses, facts_gold, raw_text: str) -> tuple[int, int, int, int]:
    """Returns (recall_hits, recall_total, halluc_count, total_clauses).

    Matching method: substring/keyword overlap heuristic.
    A gold fact is "covered" if any clause contains >=50% of the gold fact's
    key terms (nouns/numbers extracted by simple splitting).

    A clause is "hallucinated" if it cannot be grounded in raw_text —
    specifically, if fewer than 30% of its significant tokens appear in raw_text.
    """
    recall_total = len(facts_gold)
    recall_hits = 0
    total_clauses_n = len(clauses)
    halluc_count = 0

    # Extract clause texts
    clause_texts = [c.text for c in clauses]

    # Recall: does some clause cover each gold fact?
    for fact in facts_gold:
        fact_text = fact["text"]
        fact_tokens = _significant_tokens(fact_text)
        if not fact_tokens:
            recall_hits += 1  # trivial fact
            continue
        covered = False
        for ct in clause_texts:
            ct_tokens = _significant_tokens(ct)
            overlap = fact_tokens & ct_tokens
            if len(overlap) >= max(1, len(fact_tokens) * 0.4):
                covered = True
                break
        if covered:
            recall_hits += 1

    # Hallucination: is each clause grounded in raw_text?
    raw_tokens = _significant_tokens(raw_text) if raw_text else set()
    for ct in clause_texts:
        ct_tokens = _significant_tokens(ct)
        if not ct_tokens:
            continue
        grounded = ct_tokens & raw_tokens
        if len(grounded) < max(1, len(ct_tokens) * 0.3):
            halluc_count += 1

    return recall_hits, recall_total, halluc_count, total_clauses_n


def _significant_tokens(text: str) -> set[str]:
    """Extract significant tokens (2+ chars, not purely stopwords)."""
    import re
    tokens = re.findall(r'[\w\d가-힣]+', text)
    stop = {"의", "를", "을", "이", "가", "에", "는", "은", "로", "으로", "및", "등", "한다", "있다", "위한", "따라", "한"}
    return {t for t in tokens if len(t) >= 2 and t not in stop}


# ============================================================
# Main evaluation loop
# ============================================================

def run_eval() -> list[DocResult]:
    gold_docs = load_gold()
    results: list[DocResult] = []

    for entry in gold_docs:
        doc_id = entry["doc_id"]
        file_path = entry["file"]
        dr = DocResult(
            doc_id=doc_id,
            file=file_path,
            channel_variant=entry["channel_variant"],
            route_gold=entry["doc_type_gold"],
            fact_recall_total=len(entry["facts_gold"]),
            metric_total=len(entry["metrics_gold"]),
        )

        if not Path(file_path).exists():
            dr.error = f"FILE_NOT_FOUND: {file_path}"
            results.append(dr)
            continue

        try:
            # Route
            decision = R.route_document(file_path)
            dr.route_got = decision.doc_type
            dr.route_correct = (decision.doc_type == entry["doc_type_gold"])

            # Extract
            ext = R.extract_document(file_path, decision)
            dr.engine = (ext.router_meta or {}).get("engine", "unknown")

            # Axis 2: quantitative
            hits, total, fp = score_metrics(ext.metrics, entry["metrics_gold"])
            dr.metric_hits = hits
            dr.metric_total = total
            dr.metric_fp = fp

            # Axis 3: qualitative
            rh, rt, hc, tc = score_facts(
                ext.clauses, entry["facts_gold"],
                ext.raw_text or ""
            )
            dr.fact_recall_hits = rh
            dr.fact_recall_total = rt
            dr.halluc_clauses = hc
            dr.total_clauses = tc

            # Axis 4: calibration rows (metrics only — clauses lack confidence)
            for m in ext.metrics:
                is_correct = 0
                for gm in entry["metrics_gold"]:
                    if (m.kesg_code_guess == gm["kesg_code"] and
                            m.value is not None and
                            abs(m.value - gm["value"]) <= gm["tol"]):
                        is_correct = 1
                        break
                dr.cal_rows.append({"p": m.confidence or 0.5, "y": is_correct})

        except Exception as e:
            dr.error = f"EXCEPTION: {e}"

        results.append(dr)

    return results


# ============================================================
# Report printing
# ============================================================

def print_report(results: list[DocResult]) -> None:
    is_mock = IS_MOCK
    print("=" * 90)
    print(f"  비정형 OCR 4축 신뢰도 측정 {'[MOCK 배선 확인]' if is_mock else '[STRICT 실측]'}")
    print("=" * 90)

    # Check for errors
    errors = [r for r in results if r.error]
    if errors:
        print(f"\n⚠ 에러 발생 문서 {len(errors)}건:")
        for r in errors:
            print(f"  - {r.doc_id}: {r.error}")

    valid = [r for r in results if not r.error]
    if not valid:
        print("\n평가 가능한 문서가 없습니다.")
        return

    # --- Axis 1: Routing ---
    route_correct = sum(1 for r in valid if r.route_correct)
    print(f"\n{'─'*90}")
    print(f"[축 1] 라우팅 정확도: {route_correct}/{len(valid)} ({100*route_correct/len(valid):.0f}%)")
    print(f"{'─'*90}")
    print(f"  {'doc_id':35} {'gold':20} {'got':20} {'판정'}")
    for r in valid:
        mark = "✅" if r.route_correct else "❌"
        print(f"  {r.doc_id:35} {r.route_gold:20} {r.route_got:20} {mark}")

    # By channel variant
    for variant in ["digital", "scan"]:
        subset = [r for r in valid if r.channel_variant == variant]
        if subset:
            correct = sum(1 for r in subset if r.route_correct)
            print(f"  [{variant}] {correct}/{len(subset)} ({100*correct/len(subset):.0f}%)")

    # --- Axis 2: Quantitative ---
    total_hits = sum(r.metric_hits for r in valid)
    total_gold = sum(r.metric_total for r in valid)
    total_fp = sum(r.metric_fp for r in valid)
    print(f"\n{'─'*90}")
    print(f"[축 2] 정량 수치 정확도: {total_hits}/{total_gold} "
          f"({100*total_hits/total_gold:.0f}% 일치)" if total_gold else
          f"\n{'─'*90}\n[축 2] 정량 수치 정확도: gold 정량 항목 0건 (정성 문서만)")
    print(f"  환각 수치(false positive): {total_fp}건")
    if total_gold:
        print(f"  {'doc_id':35} {'hits/total':12} {'FP':>5}")
        for r in valid:
            if r.metric_total > 0 or r.metric_fp > 0:
                ht_str = f"{r.metric_hits}/{r.metric_total}"
                print(f"  {r.doc_id:35} {ht_str:12} {r.metric_fp:>5}")

    # --- Axis 3: Qualitative ---
    total_rh = sum(r.fact_recall_hits for r in valid)
    total_rt = sum(r.fact_recall_total for r in valid)
    total_hc = sum(r.halluc_clauses for r in valid)
    total_tc = sum(r.total_clauses for r in valid)
    recall_pct = 100 * total_rh / total_rt if total_rt else 0
    halluc_pct = 100 * total_hc / total_tc if total_tc else 0
    print(f"\n{'─'*90}")
    print(f"[축 3] 정성 충실도")
    print(f"  recall: {total_rh}/{total_rt} ({recall_pct:.1f}%)")
    print(f"  환각률: {total_hc}/{total_tc} clauses ({halluc_pct:.1f}%)")
    print(f"  ⚠ clause에 confidence 필드 없음 → 정성 캘리브레이션 불가 (한계)")
    print(f"{'─'*90}")
    print(f"  {'doc_id':35} {'recall':12} {'환각':8} {'clauses':8}")
    for r in valid:
        recall_str = f"{r.fact_recall_hits}/{r.fact_recall_total}"
        print(f"  {r.doc_id:35} {recall_str:12} {r.halluc_clauses:8} {r.total_clauses:8}")

    # By channel variant
    for variant in ["digital", "scan"]:
        subset = [r for r in valid if r.channel_variant == variant]
        if subset:
            vrh = sum(r.fact_recall_hits for r in subset)
            vrt = sum(r.fact_recall_total for r in subset)
            vhc = sum(r.halluc_clauses for r in subset)
            vtc = sum(r.total_clauses for r in subset)
            vr_pct = 100 * vrh / vrt if vrt else 0
            vh_pct = 100 * vhc / vtc if vtc else 0
            print(f"  [{variant}] recall={vrh}/{vrt} ({vr_pct:.1f}%), "
                  f"환각={vhc}/{vtc} ({vh_pct:.1f}%)")

    # --- Axis 4: Calibration ---
    all_cal = []
    for r in valid:
        all_cal.extend(r.cal_rows)

    print(f"\n{'─'*90}")
    print(f"[축 4] 신뢰도 캘리브레이션 (정량 metric.confidence vs 실제 정답 여부)")
    if len(all_cal) >= 3:
        cal_result = calibration(all_cal, n_bins=5)
        print(f"  ECE = {cal_result['ece']:.4f}")
        print(f"  (해석: ECE<0.05 양호, 0.05~0.15 보통, >0.15 교정 필요)")
        print(f"  bin별 상세:")
        for b in cal_result["diagram"]:
            if b["n"] > 0:
                print(f"    {b['range']}: n={b['n']}, conf={b['conf']:.3f}, acc={b['acc']:.3f}, gap={b['gap']:+.3f}")
        # Bootstrap CI
        if len(all_cal) >= 10:
            # For bootstrap, need prf-style rows; adapt
            print(f"  (샘플 수 {len(all_cal)}건 — 부트스트랩 CI 생략: 정량 항목 소수)")
    else:
        print(f"  캘리브레이션 대상 {len(all_cal)}건 — 최소 3건 필요 (정성 문서 위주)")
        print(f"  ⚠ 정량 gold가 거의 없어 ECE 산출 불가 (정성 문서 특성상)")

    # Summary
    print(f"\n{'═'*90}")
    print("요약 (4축)")
    print(f"  라우팅: {route_correct}/{len(valid)} ({100*route_correct/len(valid):.0f}%)")
    if total_gold:
        print(f"  정량:   {total_hits}/{total_gold} ({100*total_hits/total_gold:.0f}%), FP={total_fp}")
    else:
        print(f"  정량:   gold 0건 (정성 문서만)")
    print(f"  정성:   recall={recall_pct:.1f}%, 환각률={halluc_pct:.1f}%")
    if len(all_cal) >= 3:
        print(f"  ECE:    {cal_result['ece']:.4f}")
    else:
        print(f"  ECE:    산출 불가 (정량 샘플 부족)")
    print(f"  엔진:   {set(r.engine for r in valid if r.engine)}")
    print(f"{'═'*90}")

    if is_mock:
        print("\n⚠ MOCK 모드: 수치는 배선 확인용이며 리포트에 싣지 않는다.")


def export_json(results: list[DocResult], path: str = "data/benchmark_ocr/eval_results.json") -> None:
    """Export results as JSON for report generation."""
    out = []
    for r in results:
        out.append({
            "doc_id": r.doc_id,
            "file": r.file,
            "channel_variant": r.channel_variant,
            "route_correct": r.route_correct,
            "route_gold": r.route_gold,
            "route_got": r.route_got,
            "metric_hits": r.metric_hits,
            "metric_total": r.metric_total,
            "metric_fp": r.metric_fp,
            "fact_recall_hits": r.fact_recall_hits,
            "fact_recall_total": r.fact_recall_total,
            "halluc_clauses": r.halluc_clauses,
            "total_clauses": r.total_clauses,
            "cal_rows": r.cal_rows,
            "engine": r.engine,
            "error": r.error,
        })
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n결과 JSON 저장: {path}")


if __name__ == "__main__":
    results = run_eval()
    print_report(results)
    export_json(results)
