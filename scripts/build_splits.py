"""dev/test 스플릿 벤치 빌드 — 튜닝셋과 held-out 검수셋을 분리.

규칙:
  - dev  = 기존 임계값 튜닝에 쓴 greenwash_bench 50셋 (절대 test와 안 섞음)
  - test = 신규 검수 데이터 중 '확정 라벨'만 (held-out, 절대 재튜닝 금지)
      · 양성: gold_regulatory 텍스트분류 가능분(eval_track=text_classification)
              + report_labeled/refined-고확신 중 greenwash
      · 음성: report_labeled clean + refined(확신=high & 추천라벨=clean)
  - 검토/uncertain/제외/범위밖(out_of_scope)·확신=low 는 전부 제외
  - dev 문장과 중복되는 test 문장은 누수 방지로 제거

출력: data/benchmark_v2/dev.json, data/benchmark_v2/test.json (greenwash_bench 스키마)
"""
from __future__ import annotations
import json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / "data"


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "").strip()


def main() -> None:
    # --- dev: 기존 튜닝 50셋 ---
    bench = json.loads((D / "benchmark" / "greenwash_bench.json").read_text(encoding="utf-8"))
    ticker = bench.get("ticker", "005930")
    dev_cases = []
    dev_sents = set()
    for c in bench["cases"]:
        dev_cases.append({"id": c["id"], "sentence": c["sentence"],
                          "label": c["label"], "category": c.get("category", "dev"),
                          "split": "dev", "domain": "tuning_synthetic"})
        dev_sents.add(_norm(c["sentence"]))

    # --- test 소스 로드 ---
    gold = [json.loads(l) for l in (D / "benchmark_v2" / "gold_regulatory.jsonl")
            .read_text(encoding="utf-8").splitlines() if l.strip()]
    report = json.loads((D / "benchmark_v2" / "report_labeled.json").read_text(encoding="utf-8"))
    refined = json.loads((D / "benchmark_v2" / "refined.json").read_text(encoding="utf-8"))

    test_cases = []
    seen = set()
    dropped = {"dev_dup": 0, "out_of_scope": 0, "low_conf": 0, "unconfirmed": 0, "test_dup": 0}

    def add(cid, sent, label, category, domain):
        key = _norm(sent)
        if key in dev_sents:
            dropped["dev_dup"] += 1; return
        if key in seen:
            dropped["test_dup"] += 1; return
        seen.add(key)
        test_cases.append({"id": cid, "sentence": sent, "label": label,
                           "category": category, "split": "test", "domain": domain})

    # 1) 규제 골드 — 텍스트만으로 판정 가능한 양성 (out_of_scope 제외)
    for r in gold:
        if r.get("eval_track") != "text_classification":
            dropped["out_of_scope"] += 1; continue
        add(r["id"], r["sentence"], "greenwash", r.get("category", "regulatory"),
            "regulatory_ad")  # 광고문구 — 도메인 상이

    # 2) report_labeled — 실제 ESG 보고서 문장 (확정 라벨)
    for r in report:
        lab = r.get("label")
        if lab not in ("clean", "greenwash"):
            dropped["unconfirmed"] += 1; continue
        add(r["id"], r["sentence"], lab, "report_sentence", "esg_report")

    # 3) refined — 확신 high & 추천라벨 확정분만
    for i, r in enumerate(refined):
        if r.get("확신") != "high":
            dropped["low_conf"] += 1; continue
        rec = r.get("추천라벨")
        if rec not in ("clean", "greenwash"):
            dropped["unconfirmed"] += 1; continue
        cid = r.get("id") or f"REF-{i:03d}"
        add(cid, r["sentence"], rec, r.get("category", "report_sentence"), "esg_report")

    def pack(cases):
        pos = sum(1 for c in cases if c["label"] == "greenwash")
        return {"ticker": ticker, "n_cases": len(cases),
                "n_pos": pos, "n_neg": len(cases) - pos, "cases": cases}

    dev_out = pack(dev_cases)
    test_out = pack(test_cases)
    (D / "benchmark_v2" / "dev.json").write_text(
        json.dumps(dev_out, ensure_ascii=False, indent=2), encoding="utf-8")
    (D / "benchmark_v2" / "test.json").write_text(
        json.dumps(test_out, ensure_ascii=False, indent=2), encoding="utf-8")

    # 도메인별 양성/음성 분포 (정직성 점검)
    from collections import Counter
    dom = Counter((c["domain"], c["label"]) for c in test_cases)
    print("=== DEV ===")
    print(f"  n={dev_out['n_cases']}  pos={dev_out['n_pos']}  neg={dev_out['n_neg']}")
    print("=== TEST (held-out) ===")
    print(f"  n={test_out['n_cases']}  pos={test_out['n_pos']}  neg={test_out['n_neg']}")
    print("  도메인×라벨:")
    for (d, l), n in sorted(dom.items()):
        print(f"    {d:16s} {l:10s} {n}")
    print("  제외:", dropped)


if __name__ == "__main__":
    main()
