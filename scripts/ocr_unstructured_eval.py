# -*- coding: utf-8 -*-
"""ocr_unstructured_eval.py — 비정형 OCR 4축 신뢰도 측정.

정답셋(data/benchmark_ocr/unstructured_gold.json) 대비 비정형 채널의
라우팅·정량·정성(충실도+환각)·신뢰도 캘리브레이션을 채점한다.

실행:
  # 배선 확인 (mock, 수치 무의미) — strict 파일 건드리지 않음
  ESGENIE_FORCE_MOCK=1 python scripts/ocr_unstructured_eval.py

  # 실측 (유효 키 필요, LLM judge 포함)
  ESGENIE_STRICT=1 python scripts/ocr_unstructured_eval.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from esgenie.ssot import ocr_router as R
from esgenie.evaluate import calibration, bootstrap_ci

GOLD_PATH = Path("data/benchmark_ocr/unstructured_gold.json")
IS_MOCK = bool(os.environ.get("ESGENIE_FORCE_MOCK"))
IS_STRICT = bool(os.environ.get("ESGENIE_STRICT"))

MODE = "strict" if IS_STRICT else ("mock" if IS_MOCK else "default")

# mock 수치는 리포트/실측 파일에 절대 유입 금지
OUTPUT_PATH = (
    Path("data/benchmark_ocr/eval_results.mock.json") if MODE == "mock"
    else Path("data/benchmark_ocr/eval_results.json")
)
JUDGE_PATH = Path("data/benchmark_ocr/judge_decisions.json")


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
    metric_fp_true_halluc: int = 0
    metric_fp_unlisted_real: int = 0
    # Axis 3: qualitative (heuristic)
    fact_recall_hits_heuristic: int = 0
    fact_recall_total: int = 0
    halluc_clauses_heuristic: int = 0
    total_clauses: int = 0
    # Axis 3: qualitative (judge) — only in strict mode
    fact_recall_hits_judge: int = 0
    halluc_clauses_judge: int = 0
    judge_failed_clauses: int = 0
    # Axis 4: calibration rows
    cal_rows: list = field(default_factory=list)
    # Meta
    engine: str = ""
    raw_text_source: str = ""
    raw_text_len: int = 0
    upstage_error: str = ""
    error: str = ""


def load_gold() -> list[dict]:
    with open(GOLD_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data["docs"]


# ============================================================
# Axis 2: quantitative metric matching with FP split
# ============================================================

def _parse_raw_numbers(raw_text: str) -> set[float]:
    """Extract all numeric tokens from raw text and parse to float values.

    Handles: integers, decimals, comma-separated thousands (e.g. 142,560).
    Each token is parsed independently — '2060' yields 2060.0, not 60.0.
    """
    tokens = re.findall(r'\d[\d,]*\.?\d*', raw_text)
    values: set[float] = set()
    for t in tokens:
        try:
            values.add(float(t.replace(",", "")))
        except ValueError:
            continue
    return values


def _value_in_raw_text(value: float, raw_text: str) -> bool:
    """Check if a numeric value actually appears in the raw text as a standalone token.

    Uses numeric comparison (not substring) to avoid false matches like
    60 matching inside '2060' or '60,000'.
    """
    if not raw_text or value is None:
        return False
    raw_numbers = _parse_raw_numbers(raw_text)
    eps = max(0.01, abs(value) * 1e-6)
    return any(abs(value - n) <= eps for n in raw_numbers)


_UNIT_SYNONYMS: dict[str, str] = {
    "ton": "t", "tons": "t", "톤": "t", "tco2eq": "tco2eq", "tco2e": "tco2eq",
    "kwh": "kwh", "mwh": "mwh", "gwh": "gwh",
    "%": "%", "퍼센트": "%", "percent": "%",
    "원": "krw", "won": "krw", "krw": "krw",
    "명": "persons", "인": "persons", "people": "persons", "persons": "persons",
    "건": "cases", "件": "cases", "cases": "cases",
    "시간": "hours", "hours": "hours", "hr": "hours", "hrs": "hours",
    "일": "days", "days": "days",
    "kg": "kg", "g": "g", "mg": "mg",
    "m3": "m3", "㎥": "m3",
    "l": "l", "리터": "l",
}


def _normalize_unit(unit: str | None) -> str:
    """Normalize a unit string for comparison."""
    if not unit:
        return ""
    u = unit.strip().lower().replace(" ", "")
    return _UNIT_SYNONYMS.get(u, u)


def _units_match(extracted_unit: str | None, gold_unit: str | None) -> bool:
    """Check if extracted unit matches gold unit (with synonym normalization).
    If gold has no unit specified, any unit matches (backwards compat).
    """
    if not gold_unit:
        return True
    return _normalize_unit(extracted_unit) == _normalize_unit(gold_unit)


def score_metrics(extracted_metrics, gold_metrics, raw_text: str) -> tuple[int, int, int, int]:
    """Returns (hits, total_gold, fp_true_halluc, fp_unlisted_real)."""
    hits = 0
    total = len(gold_metrics)
    matched_indices: set[int] = set()

    for gm in gold_metrics:
        code = gm["kesg_code"]
        want = gm["value"]
        tol = gm["tol"]
        gold_unit = gm.get("unit")
        found = False
        for i, em in enumerate(extracted_metrics):
            if em.kesg_code_guess == code and i not in matched_indices:
                if (em.value is not None and abs(em.value - want) <= tol
                        and _units_match(getattr(em, "unit", None), gold_unit)):
                    hits += 1
                    matched_indices.add(i)
                    found = True
                    break
        if not found:
            for i, em in enumerate(extracted_metrics):
                if i not in matched_indices and em.value is not None:
                    if (abs(em.value - want) <= tol
                            and _units_match(getattr(em, "unit", None), gold_unit)):
                        hits += 1
                        matched_indices.add(i)
                        break

    # Split FPs: true hallucination vs unlisted-but-real
    fp_true_halluc = 0
    fp_unlisted_real = 0
    for i, em in enumerate(extracted_metrics):
        if i in matched_indices:
            continue
        if em.value is not None and _value_in_raw_text(em.value, raw_text):
            fp_unlisted_real += 1
        else:
            fp_true_halluc += 1

    return hits, total, fp_true_halluc, fp_unlisted_real


# ============================================================
# Axis 3: qualitative — heuristic scoring (cross-validation)
# ============================================================

def score_facts_heuristic(clauses, facts_gold, raw_text: str) -> tuple[int, int, int, int]:
    """Heuristic scoring: token overlap.
    Returns (recall_hits, recall_total, halluc_count, total_clauses).
    """
    recall_total = len(facts_gold)
    recall_hits = 0
    total_clauses_n = len(clauses)
    halluc_count = 0

    clause_texts = [c.text for c in clauses]

    for fact in facts_gold:
        fact_text = fact["text"]
        fact_tokens = _significant_tokens(fact_text)
        if not fact_tokens:
            recall_hits += 1
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
    tokens = re.findall(r'[\w\d가-힣]+', text)
    stop = {"의", "를", "을", "이", "가", "에", "는", "은", "로", "으로", "및", "등",
            "한다", "있다", "위한", "따라", "한"}
    return {t for t in tokens if len(t) >= 2 and t not in stop}


# ============================================================
# Axis 3: qualitative — LLM-as-judge (STRICT only)
# ============================================================

def _build_judge_client():
    """Build a judge LLM client that prefers Anthropic for independent judgment.

    If ANTHROPIC_API_KEY is available, builds an Anthropic-only client
    so the judge is independent from the extraction model (OpenAI/Azure).
    Falls back to default LLMClient if Anthropic is unavailable.
    """
    from esgenie.config import SETTINGS
    from esgenie.llm import LLMClient
    if SETTINGS.anthropic_api_key:
        client = LLMClient.__new__(LLMClient)
        client._openai_client = None
        client._anthropic_client = None
        try:
            import anthropic
            client._anthropic_client = anthropic.Anthropic(
                api_key=SETTINGS.anthropic_api_key
            )
        except Exception:
            return LLMClient()
        return client
    return LLMClient()


def _judge_combined(facts_gold: list[dict], clause_texts: list[str],
                    raw_text: str, judge_client) -> tuple[list[dict], list[dict], dict]:
    """Single LLM call: judge both recall and hallucination together.
    Returns (recall_decisions, halluc_decisions, resp_meta).
    """
    if not clause_texts:
        return (
            [{"fact_id": f["id"], "covered": False, "by_clause_idx": None} for f in facts_gold],
            [],
            {},
        )

    facts_str = "\n".join(f"  {f['id']}: {f['text']}" for f in facts_gold)
    clauses_str = "\n".join(f"  [{i}]: {ct}" for i, ct in enumerate(clause_texts))
    raw_excerpt = raw_text[:3000] if raw_text else "(원문 없음)"

    system = (
        "너는 문서 추출 품질을 평가하는 엄격한 심사관이다. 두 가지 판정을 수행한다.\n\n"
        "[판정A: recall] 정답 사실 각각이 추출된 조항 중 어느 것에 의해 의미적으로 커버되는지.\n"
        "커버 = 핵심 내용(주체·행위·조건·수치)이 조항에 포함. 근거 불명확하면 covered=false.\n\n"
        "[판정B: grounding] 각 추출 조항이 원문에 실제로 근거가 있는지.\n"
        "- grounded: 원문에 명시적으로 존재\n"
        "- unsupported: 원문에 없거나 추론으로만 도달 가능\n"
        "- contradicts: 원문과 상충\n"
        "근거 불명확하면 unsupported로 판정한다."
    )
    user = (
        f"[원문 텍스트]\n{raw_excerpt}\n\n"
        f"[정답 사실 목록]\n{facts_str}\n\n"
        f"[추출된 조항]\n{clauses_str}\n\n"
        "아래 JSON 형식으로 응답하라 (코드블록 없이 순수 JSON만):\n"
        '{"recall": [{"fact_id": "F1", "covered": true, "by_clause_idx": 0}], '
        '"grounding": [{"clause_idx": 0, "verdict": "grounded", "reason": "..."}]}'
    )

    try:
        resp = judge_client.complete(system=system, user=user,
                                     json_mode=True, temperature=0.0,
                                     mock_hint="ocr_judge")
        m = re.search(r'\{.*\}', resp.content, re.DOTALL)
        if m:
            data = json.loads(m.group())
            recall = data.get("recall", [])
            grounding = data.get("grounding", [])
            return recall, grounding, resp.meta
    except Exception:
        pass

    return (
        [{"fact_id": f["id"], "covered": False, "by_clause_idx": None} for f in facts_gold],
        [{"clause_idx": i, "verdict": "judge_failed", "reason": "judge_call_failed"} for i in range(len(clause_texts))],
        {"provider": "failed", "model": "N/A"},
    )


# ============================================================
# Main evaluation loop
# ============================================================

def run_eval() -> tuple[list[DocResult], list[dict], str]:
    """Returns (results, judge_decisions, judge_model). judge_decisions is empty in mock mode."""
    gold_docs = load_gold()
    results: list[DocResult] = []
    judge_decisions: list[dict] = []

    judge_client = None
    judge_model = "N/A (mock)"
    judge_is_independent = False
    if MODE != "mock":
        judge_client = _build_judge_client()
        # Label will be determined from actual response meta after the run.
        # Set a provisional label that will be overridden.
        judge_model = "(pending — determined from response meta)"

    total_docs = len(gold_docs)
    for idx, entry in enumerate(gold_docs, 1):
        doc_id = entry["doc_id"]
        file_path = entry["file"]
        print(f"  [{idx}/{total_docs}] {doc_id}...", end=" ", flush=True)
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
            print("FILE NOT FOUND", flush=True)
            continue

        try:
            # Route
            decision = R.route_document(file_path)
            dr.route_got = decision.doc_type
            dr.route_correct = (decision.doc_type == entry["doc_type_gold"])

            # Extract
            ext = R.extract_document(file_path, decision)
            meta = ext.router_meta or {}
            dr.engine = meta.get("engine", "unknown")
            dr.raw_text_source = meta.get("raw_text_source", "unknown")
            dr.raw_text_len = meta.get("raw_text_len", 0)
            dr.upstage_error = meta.get("upstage_error", "")
            raw_text = ext.raw_text or ""

            # Axis 2: quantitative with FP split
            hits, total, fp_halluc, fp_real = score_metrics(
                ext.metrics, entry["metrics_gold"], raw_text
            )
            dr.metric_hits = hits
            dr.metric_total = total
            dr.metric_fp_true_halluc = fp_halluc
            dr.metric_fp_unlisted_real = fp_real

            # Axis 3 heuristic
            rh, rt, hc, tc = score_facts_heuristic(
                ext.clauses, entry["facts_gold"], raw_text
            )
            dr.fact_recall_hits_heuristic = rh
            dr.fact_recall_total = rt
            dr.halluc_clauses_heuristic = hc
            dr.total_clauses = tc

            # Axis 3 judge (strict/default only) — single combined call
            clause_texts = [c.text for c in ext.clauses]
            if judge_client is not None and MODE != "mock":
                recall_decisions, halluc_decisions, judge_resp_meta = _judge_combined(
                    entry["facts_gold"], clause_texts, raw_text, judge_client
                )
                gold_ids = {f["id"] for f in entry["facts_gold"]}
                dr.fact_recall_hits_judge = len({
                    d["fact_id"] for d in recall_decisions
                    if d.get("covered") and d.get("fact_id") in gold_ids
                })
                assert dr.fact_recall_hits_judge <= dr.fact_recall_total, (
                    f"{doc_id}: recall_hits({dr.fact_recall_hits_judge}) > total({dr.fact_recall_total})"
                )
                dr.halluc_clauses_judge = sum(
                    1 for d in halluc_decisions
                    if d.get("verdict") in ("unsupported", "contradicts")
                )
                dr.judge_failed_clauses = sum(
                    1 for d in halluc_decisions
                    if d.get("verdict") == "judge_failed"
                )
                judge_decisions.append({
                    "doc_id": doc_id,
                    "judge_model": judge_resp_meta.get("model", "unknown"),
                    "judge_provider": judge_resp_meta.get("provider", "unknown"),
                    "recall": recall_decisions,
                    "hallucination": halluc_decisions,
                })
            else:
                dr.fact_recall_hits_judge = 0
                dr.halluc_clauses_judge = 0

            # Axis 4: calibration rows
            for m_item in ext.metrics:
                is_correct = 0
                for gm in entry["metrics_gold"]:
                    if (m_item.kesg_code_guess == gm["kesg_code"] and
                            m_item.value is not None and
                            abs(m_item.value - gm["value"]) <= gm["tol"]):
                        is_correct = 1
                        break
                dr.cal_rows.append({"p": m_item.confidence or 0.5, "y": is_correct})

        except Exception as e:
            dr.error = f"EXCEPTION: {e}"
            print(f"ERROR: {e}", flush=True)

        results.append(dr)
        if not dr.error:
            print("OK", flush=True)

    # Determine judge model from actual response meta (never from config alone)
    if judge_decisions:
        actual_providers = {d.get("judge_provider", "unknown") for d in judge_decisions}
        actual_providers.discard("failed")
        if actual_providers:
            provider = next(iter(actual_providers))
            model_name = judge_decisions[0].get("judge_model", "unknown")
            # Determine independence by comparing to extractor provider
            extractor_provider = "openai"  # hardcoded: extraction always uses OpenAI/Azure
            is_self = (provider == extractor_provider)
            judge_model = f"{model_name} ({'self-judge' if is_self else 'independent'})"
            judge_is_independent = not is_self
            if not is_self:
                print(f"  ✅ Judge is INDEPENDENT (provider={provider}, extractor={extractor_provider})")
        else:
            judge_model = "N/A (all judge calls failed)"
            print("  ⚠ WARNING: All judge calls failed — no valid judge model determined")
    elif MODE != "mock":
        judge_model = "N/A (no judge calls made)"

    return results, judge_decisions, judge_model


# ============================================================
# Report printing
# ============================================================

def print_report(results: list[DocResult], judge_model: str) -> None:
    print("=" * 90)
    print(f"  비정형 OCR 4축 신뢰도 측정 [{MODE.upper()}]")
    print("=" * 90)

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

    for variant in ["digital", "scan"]:
        subset = [r for r in valid if r.channel_variant == variant]
        if subset:
            correct = sum(1 for r in subset if r.route_correct)
            print(f"  [{variant}] {correct}/{len(subset)} ({100*correct/len(subset):.0f}%)")

    # --- Axis 2: Quantitative with FP split ---
    total_hits = sum(r.metric_hits for r in valid)
    total_gold = sum(r.metric_total for r in valid)
    total_fp_halluc = sum(r.metric_fp_true_halluc for r in valid)
    total_fp_real = sum(r.metric_fp_unlisted_real for r in valid)
    total_fp = total_fp_halluc + total_fp_real
    print(f"\n{'─'*90}")
    if total_gold:
        print(f"[축 2] 정량 수치 정확도: {total_hits}/{total_gold} "
              f"({100*total_hits/total_gold:.0f}% 일치)")
    else:
        print(f"[축 2] 정량 수치 정확도: gold 정량 항목 0건 (정성 문서만)")
    print(f"  FP 합계: {total_fp}건")
    print(f"    ├ true_halluc (원문에 없는 수치 창작): {total_fp_halluc}건")
    print(f"    └ unlisted_real (원문에 있으나 gold 미기재): {total_fp_real}건")
    if total_gold or total_fp:
        print(f"  {'doc_id':35} {'hits/gold':10} {'halluc':>7} {'unlisted':>9}")
        for r in valid:
            if r.metric_total > 0 or r.metric_fp_true_halluc > 0 or r.metric_fp_unlisted_real > 0:
                ht_str = f"{r.metric_hits}/{r.metric_total}"
                print(f"  {r.doc_id:35} {ht_str:10} {r.metric_fp_true_halluc:>7} "
                      f"{r.metric_fp_unlisted_real:>9}")

    # --- Axis 3: Qualitative ---
    # Heuristic
    h_rh = sum(r.fact_recall_hits_heuristic for r in valid)
    h_rt = sum(r.fact_recall_total for r in valid)
    h_hc = sum(r.halluc_clauses_heuristic for r in valid)
    h_tc = sum(r.total_clauses for r in valid)
    h_recall_pct = 100 * h_rh / h_rt if h_rt else 0
    h_halluc_pct = 100 * h_hc / h_tc if h_tc else 0

    # Judge
    j_rh = sum(r.fact_recall_hits_judge for r in valid)
    j_hc = sum(r.halluc_clauses_judge for r in valid)
    j_failed = sum(r.judge_failed_clauses for r in valid)
    j_judged = h_tc - j_failed  # denominator excludes judge failures
    j_recall_pct = 100 * j_rh / h_rt if h_rt else 0
    j_halluc_pct = 100 * j_hc / j_judged if j_judged else 0

    print(f"\n{'─'*90}")
    print(f"[축 3] 정성 충실도")
    print(f"  {'방법':<12} {'recall':<22} {'환각률':<22}")
    print(f"  {'휴리스틱':<12} {h_rh}/{h_rt} ({h_recall_pct:.1f}%){'':5}"
          f"{h_hc}/{h_tc} ({h_halluc_pct:.1f}%)")
    if MODE != "mock":
        print(f"  {'LLM-judge':<12} {j_rh}/{h_rt} ({j_recall_pct:.1f}%){'':5}"
              f"{j_hc}/{j_judged} ({j_halluc_pct:.1f}%)"
              f"{'  ('+str(j_failed)+' failed 제외)' if j_failed else ''}")
        print(f"  judge 모델: {judge_model}")
    print(f"  ⚠ clause에 confidence 필드 없음 → 정성 캘리브레이션 불가 (한계)")
    print(f"{'─'*90}")
    print(f"  {'doc_id':35} {'h_recall':8} {'j_recall':8} {'h_환각':7} {'j_환각':7} {'clauses':8}")
    for r in valid:
        hr_str = f"{r.fact_recall_hits_heuristic}/{r.fact_recall_total}"
        jr_str = f"{r.fact_recall_hits_judge}/{r.fact_recall_total}" if MODE != "mock" else "—"
        print(f"  {r.doc_id:35} {hr_str:8} {jr_str:8} "
              f"{r.halluc_clauses_heuristic:7} {r.halluc_clauses_judge:7} {r.total_clauses:8}")

    for variant in ["digital", "scan"]:
        subset = [r for r in valid if r.channel_variant == variant]
        if subset:
            vrh = sum(r.fact_recall_hits_judge for r in subset) if MODE != "mock" else sum(r.fact_recall_hits_heuristic for r in subset)
            vrt = sum(r.fact_recall_total for r in subset)
            vhc = sum(r.halluc_clauses_judge for r in subset) if MODE != "mock" else sum(r.halluc_clauses_heuristic for r in subset)
            vtc = sum(r.total_clauses for r in subset)
            vfailed = sum(r.judge_failed_clauses for r in subset) if MODE != "mock" else 0
            vjudged = vtc - vfailed
            method = "judge" if MODE != "mock" else "heuristic"
            vr_pct = 100 * vrh / vrt if vrt else 0
            vh_pct = 100 * vhc / vjudged if vjudged else 0
            print(f"  [{variant}] ({method}) recall={vrh}/{vrt} ({vr_pct:.1f}%), "
                  f"환각={vhc}/{vjudged} ({vh_pct:.1f}%)")

    # --- Axis 4: Calibration ---
    all_cal = []
    for r in valid:
        all_cal.extend(r.cal_rows)

    print(f"\n{'─'*90}")
    print(f"[축 4] 신뢰도 캘리브레이션 (정량 metric.confidence vs 실제 정답 여부)")
    if len(all_cal) >= 3:
        cal_result = calibration(all_cal, n_bins=5)
        print(f"  ECE = {cal_result['ece']:.4f}")
        print(f"  ⚠ confidence는 상수 0.75 (_map_vlm_json 하드코딩) → 판별력 없음")
        print(f"  bin별 상세:")
        for b in cal_result["diagram"]:
            if b["n"] > 0:
                print(f"    {b['range']}: n={b['n']}, conf={b['conf']:.3f}, "
                      f"acc={b['acc']:.3f}, gap={b['gap']:+.3f}")
    else:
        print(f"  캘리브레이션 대상 {len(all_cal)}건 — 최소 3건 필요")

    # Summary
    print(f"\n{'═'*90}")
    print("요약 (4축)")
    print(f"  라우팅: {route_correct}/{len(valid)} ({100*route_correct/len(valid):.0f}%)")
    if total_gold:
        print(f"  정량:   {total_hits}/{total_gold} ({100*total_hits/total_gold:.0f}%), "
              f"true_halluc={total_fp_halluc}, unlisted_real={total_fp_real}")
    else:
        print(f"  정량:   gold 0건")
    if MODE != "mock":
        print(f"  정성(judge):   recall={j_recall_pct:.1f}%, 환각률={j_halluc_pct:.1f}%")
    print(f"  정성(heuristic): recall={h_recall_pct:.1f}%, 환각률={h_halluc_pct:.1f}%")
    if len(all_cal) >= 3:
        print(f"  ECE:    {cal_result['ece']:.4f} (상수 conf=0.75 → 캘리브레이션 신호 부재)")
    print(f"  엔진:   {set(r.engine for r in valid if r.engine)}")
    print(f"  judge:  {judge_model}")
    print(f"{'═'*90}")

    if MODE == "mock":
        print("\n⚠ MOCK 모드: 수치는 배선 확인용이며 리포트에 싣지 않는다.")


def export_json(results: list[DocResult], judge_decisions: list[dict],
                judge_model: str) -> None:
    """Export results with provenance metadata."""
    out_results = []
    for r in results:
        out_results.append({
            "doc_id": r.doc_id,
            "file": r.file,
            "channel_variant": r.channel_variant,
            "route_correct": r.route_correct,
            "route_gold": r.route_gold,
            "route_got": r.route_got,
            "metric_hits": r.metric_hits,
            "metric_total": r.metric_total,
            "metric_fp_true_halluc": r.metric_fp_true_halluc,
            "metric_fp_unlisted_real": r.metric_fp_unlisted_real,
            "fact_recall_hits_heuristic": r.fact_recall_hits_heuristic,
            "fact_recall_hits_judge": r.fact_recall_hits_judge,
            "fact_recall_total": r.fact_recall_total,
            "halluc_clauses_heuristic": r.halluc_clauses_heuristic,
            "halluc_clauses_judge": r.halluc_clauses_judge,
            "judge_failed_clauses": r.judge_failed_clauses,
            "total_clauses": r.total_clauses,
            "cal_rows": r.cal_rows,
            "engine": r.engine,
            "raw_text_source": r.raw_text_source,
            "raw_text_len": r.raw_text_len,
            "upstage_error": r.upstage_error or None,
            "error": r.error,
        })

    engines = list(set(r.engine for r in results if r.engine and not r.error))
    is_independent = "independent" in judge_model
    output = {
        "meta": {
            "mode": MODE,
            "extractor_engine": engines[0] if engines else "unknown",
            "judge_model": judge_model,
            "judge_is_independent": is_independent,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "gold_path": str(GOLD_PATH),
        },
        "results": out_results,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n결과 JSON 저장: {OUTPUT_PATH}")

    # Judge decisions (strict only)
    if judge_decisions and MODE != "mock":
        with open(JUDGE_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "meta": {
                    "judge_model": judge_model,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                },
                "decisions": judge_decisions,
            }, f, ensure_ascii=False, indent=2)
        print(f"Judge 판정 저장: {JUDGE_PATH}")


if __name__ == "__main__":
    results, judge_decisions, judge_model = run_eval()
    print_report(results, judge_model)
    export_json(results, judge_decisions, judge_model)
