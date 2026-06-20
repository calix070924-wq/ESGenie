"""증빙-그라운딩 재현율(recall) 실측 — held_out_eval '증빙 트랙'.

held_out_eval.py(텍스트분류 CI)가 측정하지 못하는 축을 채운다(방법론 §5 참조):
'K-ESG 코드 → 올바른 증빙 노드'를 실제로 검색해내는가(검색 재현율).

  · 데이터  : 한울정밀공업 시연 증빙 PDF (data/benchmark_v2/evidence_recall_gold.json)
  · 파이프  : ocr_router(Azure Doc Intelligence / gpt-4.1-mini) → SSOT EvidenceGraph
  · 측정    : layer1._match_evidence_nodes 와 동일한 매칭경로로
                before = [code] 만
                after  = [code] + KESGItem.search_terms (ESGReveal <SearchTerm>)
              두 모드의 코드단위 recall 을 부트스트랩 95% CI 와 함께 산출.

핵심: Azure 키가 연결된 환경에서 돌려야 '프록시'가 아니라 '실측' 재현율이 나온다.
키가 없으면 OCR 이 mock 으로 떨어져 수치가 무의미해지므로, 그 경우 리포트에
경고를 박고 심사·데모 근거로 쓰지 않도록 한다(held_out_eval 과 동일 규약).

실행:
  # 실키(정민 로컬, Azure 도달 가능) — 실측 수치
  ESGENIE_STRICT=1 PYTHONPATH=. python3 scripts/evidence_recall_eval.py
  # 배선 검증용 목(수치 무의미)
  ESGENIE_FORCE_MOCK=1 PYTHONPATH=. python3 scripts/evidence_recall_eval.py
"""
from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

try:  # .env 자동 로드 (인라인 환경변수 없이도 키 사용)
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GOLD_PATH = ROOT / "data" / "benchmark_v2" / "evidence_recall_gold.json"
EVID_DIR = ROOT / "시연증빙세트_한울정밀공업"
OUT_DIR = ROOT / "outputs" / "benchmark"


# ─────────────────────────────────────────────────────────────────────────────
# 실행 모드 (held_out_eval.py 와 동일 규약)
# ─────────────────────────────────────────────────────────────────────────────
def _run_mode() -> str:
    if os.getenv("ESGENIE_FORCE_MOCK") == "1":
        return "MOCK (배선 검증용 — 수치 무의미)"
    if os.getenv("ESGENIE_STRICT") == "1":
        return "REAL-KEY (strict)"
    return "AUTO (키 있으면 실키, 없으면 mock 폴백)"


def _bootstrap_proportion(hits: list[int], n_boot: int = 2000, seed: int = 42):
    """이진 hit 리스트(코드단위)의 비율 점추정 + 95% CI."""
    n = len(hits)
    if n == 0:
        return 0.0, 0.0, 0.0
    point = sum(hits) / n
    rng = random.Random(seed)
    samples = []
    for _ in range(n_boot):
        boot = [hits[rng.randrange(n)] for _ in range(n)]
        samples.append(sum(boot) / n)
    samples.sort()
    lo = samples[int(0.025 * n_boot)]
    hi = samples[int(0.975 * n_boot)]
    return round(point, 3), round(lo, 3), round(hi, 3)


# ─────────────────────────────────────────────────────────────────────────────
# 1) 증빙 OCR → SSOT EvidenceGraph (실측 경로)
# ─────────────────────────────────────────────────────────────────────────────
def build_graph(gold: dict):
    from esgenie.ssot import ocr_router
    from esgenie.ssot import evidence_graph as eg

    wanted_tokens = {
        token
        for row in gold.get("gold", [])
        for token in row.get("evidence_files", [])
    }
    paths: list[tuple[str, str]] = []
    for g in gold["evidence_globs"]:
        for p in sorted(EVID_DIR.glob(g)):
            if wanted_tokens and not any(token in p.name for token in wanted_tokens):
                continue
            paths.append((p.name, str(p)))
    if not paths:
        sys.exit(f"[!] 증빙을 찾지 못함: {EVID_DIR}")

    extractions = []
    engines: list[tuple[str, str]] = []
    for fname, path in paths:
        decision = ocr_router.route_document(path)
        ext = ocr_router.extract_document(path, decision)
        ext.source_file = fname
        extractions.append(ext)
        meta = ext.router_meta or {}
        eng = meta.get("engine") or ("mock" if meta.get("mock") else meta.get("fallback") or "?")
        engines.append((fname, str(eng)))

    graph = eg.build_unified_graph(
        None,                       # 비상장 SME → DART 없음
        extractions,
        corp_code=gold["corp_code"],
        corp_name=gold["corp_name"],
        report_year=int(gold["report_year"]),
    )
    return graph, engines


def _is_real(engines: list[tuple[str, str]]) -> bool:
    """mock 이 아닌 실제 추출 엔진이면 실측으로 간주."""
    if not engines:
        return False
    return all(e and "mock" not in e.lower() and e.lower() != "none" for _, e in engines)


# ─────────────────────────────────────────────────────────────────────────────
# 2) 코드단위 before/after 재현율 측정
#    layer1._match_evidence_nodes 와 동일한 키워드 구성/검색 호출을 재현한다.
# ─────────────────────────────────────────────────────────────────────────────
def _relevant_node_ids(graph, row: dict, period: int) -> set[str]:
    """gold 가 지정한 '올바른 파일 + 목표 지표'에 해당하는 정답 노드 집합."""
    code = row["code"]
    expect_files = row["evidence_files"]
    relevant_terms = [str(t).lower() for t in row.get("relevant_terms", [])]
    out: set[str] = set()
    for n in graph.nodes.values():
        if n.period != period:
            continue
        if n.metric != code:
            continue
        src = (n.source_file or "") + " " + (n.source or "")
        if any(token in src for token in expect_files):
            hay = f"{n.metric} {n.raw_text}".lower()
            if relevant_terms and not any(term in hay for term in relevant_terms):
                continue
            out.add(n.id)
    return out


def _search(graph, keywords: list[str], period: int) -> set[str]:
    return {n.id for n in graph.search_nodes(keywords=keywords, period=period)}


def measure(graph, gold: dict):
    from esgenie.knowledge.kesg_items import by_code

    period = int(gold["report_year"])
    rows = []
    hits_before, hits_after = [], []
    for g in gold["gold"]:
        code = g["code"]
        item = by_code(code)
        search_terms = list(item.search_terms) if item is not None else []

        relevant = _relevant_node_ids(graph, g, period)
        extracted = len(relevant) > 0   # OCR 가 애초에 그 증빙을 노드로 뽑았는가

        before = _search(graph, [code], period)
        after = _search(graph, [code] + search_terms, period)

        hit_before = int(bool(relevant & before))
        hit_after = int(bool(relevant & after))
        hits_before.append(hit_before)
        hits_after.append(hit_after)

        rows.append({
            "code": code, "name": g["name"],
            "extracted": extracted, "n_relevant": len(relevant),
            "hit_before": hit_before, "hit_after": hit_after,
            "gain": hit_after - hit_before,
        })

    rb = _bootstrap_proportion(hits_before)
    ra = _bootstrap_proportion(hits_after)
    # 추출 자체가 된 코드만 본 '조건부' 재현율(상한) — OCR 추출 실패와 검색 실패를 분리
    ext_idx = [i for i, r in enumerate(rows) if r["extracted"]]
    cb = _bootstrap_proportion([hits_before[i] for i in ext_idx]) if ext_idx else (0.0, 0.0, 0.0)
    ca = _bootstrap_proportion([hits_after[i] for i in ext_idx]) if ext_idx else (0.0, 0.0, 0.0)
    return rows, {"before": rb, "after": ra, "cond_before": cb, "cond_after": ca,
                  "n": len(rows), "n_extracted": len(ext_idx)}


# ─────────────────────────────────────────────────────────────────────────────
# 3) 리포트
# ─────────────────────────────────────────────────────────────────────────────
def report(rows, agg, mode, engines, n_nodes) -> str:
    real = _is_real(engines)
    L = [f"# 증빙-그라운딩 재현율 (증빙 트랙)  (모드: {mode})",
         "",
         "held_out_eval(텍스트분류 CI)와 분리된 **증빙-대조 트랙**. "
         "K-ESG 코드 → 올바른 증빙 노드 검색 재현율을 "
         "**SearchTerm 적용 전/후(before/after)** 로 실측한다.",
         "",
         "## OCR 엔진 (mock/None 이면 실측 아님)"]
    for fname, eng in engines:
        L.append(f"- {fname}  →  `{eng}`")
    L += [f"- EvidenceGraph 노드 수: {n_nodes}",
          "",
          f"## 재현율 — before(코드만) vs after(코드+SearchTerm)  ·  코드 {agg['n']}건",
          "",
          "| 모드 | Recall | 95% CI |",
          "|---|---|---|",
          f"| **before** (코드만) | {agg['before'][0]:.3f} | {agg['before'][1]:.3f} ~ {agg['before'][2]:.3f} |",
          f"| **after** (+SearchTerm) | {agg['after'][0]:.3f} | {agg['after'][1]:.3f} ~ {agg['after'][2]:.3f} |",
          "",
          f"> 조건부(추출 성공 {agg['n_extracted']}건 한정) — before {agg['cond_before'][0]:.3f} / "
          f"after {agg['cond_after'][0]:.3f}. "
          "OCR 추출 실패와 코드-검색 실패를 분리해, SearchTerm 의 순수 검색 기여를 본다.",
          "",
          "## 코드별 상세",
          "",
          "| 코드 | 항목 | 추출 | 정답노드 | before | after | Δ |",
          "|---|---|:--:|:--:|:--:|:--:|:--:|"]
    for r in rows:
        ext = "✅" if r["extracted"] else "—"
        b = "✅" if r["hit_before"] else "❌"
        a = "✅" if r["hit_after"] else "❌"
        d = f"+{r['gain']}" if r["gain"] > 0 else ("0" if r["gain"] == 0 else str(r["gain"]))
        L.append(f"| {r['code']} | {r['name']} | {ext} | {r['n_relevant']} | {b} | {a} | {d} |")

    gained = [r["code"] for r in rows if r["gain"] > 0]
    L += ["",
          "## 해석",
          f"- SearchTerm 으로 **새로 회수된 코드**: {', '.join(gained) if gained else '없음'}."]
    if not gained and real:
        L.append("  (LLM/화이트리스트가 코드를 모두 해소 → before 단계에서 이미 검색됨. "
                 "SearchTerm 기여 0 도 정직한 실측 결과다.)")
    if agg["n"] < 10:
        L.append(f"- 표본 수가 작다(n={agg['n']}) → CI 해석력보다 케이스별 정답 여부가 더 중요하다.")
    if agg["before"][0] == 1.0 and agg["after"][0] == 1.0:
        L.append("- 천장효과: 현재 셋만으로는 SearchTerm 보강의 검색 lift를 입증하기 어렵다.")
    if not real or "MOCK" in mode:
        L += ["",
              "> ⚠ **실측 아님** — OCR 엔진이 mock 으로 떨어졌다. 위 수치는 파이프라인 배선 "
              "확인용일 뿐 성능 근거가 아니다. Azure 키 연결 환경에서 "
              "`ESGENIE_STRICT=1` 로 재실행해야 심사·데모 근거가 된다."]
    return "\n".join(L)


def main() -> None:
    mode = _run_mode()
    gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))

    graph, engines = build_graph(gold)
    rows, agg = measure(graph, gold)
    out = report(rows, agg, mode, engines, len(graph.nodes))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "evidence_recall.md").write_text(out, encoding="utf-8")
    print(out)
    print(f"\n저장: {OUT_DIR / 'evidence_recall.md'}")


if __name__ == "__main__":
    main()
