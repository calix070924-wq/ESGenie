"""dev / test 스플릿별 부트스트랩 95% CI를 따로 계산.

핵심 원칙:
  - 임계값(cfg)은 BASELINE 고정. test에서 재튜닝 금지 → 진짜 held-out 수치.
  - dev = 튜닝셋(낙관 편향 가능) / test = 검수 held-out(일반화).
  - 도메인(esg_report vs regulatory_ad)별 양성 분포를 같이 출력해
    recall 수치를 어디서 끌어왔는지 투명하게.

실행:
  # 실키(정민 로컬, Azure 도달 가능):
  OPENAI_API_KEY=... AZURE_OPENAI_ENDPOINT=... OPENAI_MODEL=gpt-4.1-mini \
  ESGENIE_STRICT=1 python scripts/held_out_eval.py
  # 배선 검증용 목(수치 무의미):
  ESGENIE_FORCE_MOCK=1 python scripts/held_out_eval.py
"""
from __future__ import annotations
import json, os
from collections import Counter
from pathlib import Path

try:  # .env 자동 로드 (인라인 환경변수 없이도 키 사용)
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from esgenie.calibrate import capture, BASELINE
from esgenie.evaluate import _case_rows, _prf, bootstrap_ci

ROOT = Path(__file__).resolve().parents[1]
SPLIT_DIR = ROOT / "data" / "benchmark_v2"
OUT_DIR = ROOT / "outputs" / "benchmark"
SPLITS = ["dev", "test"]


def _run_mode() -> str:
    if os.getenv("ESGENIE_FORCE_MOCK") == "1":
        return "MOCK (배선 검증용 — 수치 무의미)"
    if os.getenv("ESGENIE_STRICT") == "1":
        return "REAL-KEY (strict)"
    return "AUTO (키 있으면 실키, 없으면 mock 폴백)"


def _domain_breakdown(bench: dict) -> str:
    dom = Counter((c.get("domain", "?"), c["label"]) for c in bench["cases"])
    return "; ".join(f"{d}/{l}={n}" for (d, l), n in sorted(dom.items()))


def main() -> None:
    cfg = BASELINE
    mode = _run_mode()
    results = {}
    for sp in SPLITS:
        bench_path = SPLIT_DIR / f"{sp}.json"
        cache_path = OUT_DIR / f"{sp}_judge_cache.json"
        bench = json.loads(bench_path.read_text(encoding="utf-8"))
        # LLM 1회 실측 → 캐시 (mock 모드면 mock 판정)
        capture(bench_path=bench_path, cache_path=cache_path)
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        rows = _case_rows(cache["records"], cfg)
        prf = _prf(rows)
        f1 = bootstrap_ci(rows, metric="f1")
        pr = bootstrap_ci(rows, metric="precision")
        rc = bootstrap_ci(rows, metric="recall")
        results[sp] = {"n": len(rows),
                       "n_pos": sum(r["y"] for r in rows),
                       "prf": prf, "f1": f1, "precision": pr, "recall": rc,
                       "domain": _domain_breakdown(bench)}

    # --- 리포트 ---
    L = [f"# dev vs test held-out CI  (모드: {mode})",
         f"- 임계값 고정: trig={cfg['trigger']} w={cfg['rule_weight']} "
         f"thr={cfg['threshold']} axf={cfg['axis_flag']}  (test 재튜닝 안 함)",
         ""]
    for sp in SPLITS:
        r = results[sp]
        tag = "튜닝셋(낙관 편향 가능)" if sp == "dev" else "held-out(일반화 수치)"
        L += [f"## {sp.upper()} — n={r['n']} (양성 {r['n_pos']}) · {tag}",
              f"- 도메인×라벨: {r['domain']}",
              f"- F1        = {r['f1'][0]:.3f}  (95% CI {r['f1'][1]:.3f} ~ {r['f1'][2]:.3f})",
              f"- Precision = {r['precision'][0]:.3f}  (95% CI {r['precision'][1]:.3f} ~ {r['precision'][2]:.3f})",
              f"- Recall    = {r['recall'][0]:.3f}  (95% CI {r['recall'][1]:.3f} ~ {r['recall'][2]:.3f})",
              ""]
    if "MOCK" in mode:
        L.append("> ⚠ MOCK 실행 — 위 수치는 파이프라인 배선 확인용일 뿐 성능 근거 아님.")
    report = "\n".join(L)
    (OUT_DIR / "held_out_ci.md").write_text(report, encoding="utf-8")
    print(report)
    print(f"\n저장: {OUT_DIR / 'held_out_ci.md'}")


if __name__ == "__main__":
    main()
