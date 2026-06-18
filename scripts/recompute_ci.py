"""기존 judge 캐시(예측 불변)에 현재 bench 라벨을 다시 적용해 CI 재계산.

예측(룰+LLM 판정)은 라벨과 무관하므로, 라벨만 바뀐 경우 API 재호출 없이
캐시의 axes를 그대로 쓰고 ground-truth 라벨만 bench에서 id로 조인한다.
"""
from __future__ import annotations
import json
from collections import Counter
from pathlib import Path
from esgenie.calibrate import BASELINE
from esgenie.evaluate import _case_rows, _prf, bootstrap_ci

ROOT = Path(__file__).resolve().parents[1]
SPLIT_DIR = ROOT / "data" / "benchmark_v2"
OUT_DIR = ROOT / "outputs" / "benchmark"
cfg = BASELINE

L = ["# dev vs test held-out CI (라벨 재적용, 캐시 재사용 — API 무호출)",
     f"- 임계값 고정: trig={cfg['trigger']} w={cfg['rule_weight']} thr={cfg['threshold']} axf={cfg['axis_flag']}", ""]
for sp in ["dev", "test"]:
    bench = json.loads((SPLIT_DIR / f"{sp}.json").read_text(encoding="utf-8"))
    label_by_id = {c["id"]: c["label"] for c in bench["cases"]}
    dom_by_id = {c["id"]: c.get("domain", "?") for c in bench["cases"]}
    cache = json.loads((OUT_DIR / f"{sp}_judge_cache.json").read_text(encoding="utf-8"))
    recs = []
    for r in cache["records"]:
        if r["id"] not in label_by_id:   # 라벨 변경으로 제외된 케이스 스킵
            continue
        r = {**r, "label": label_by_id[r["id"]]}   # 캐시 라벨 → 현재 bench 라벨로 덮어쓰기
        recs.append(r)
    rows = _case_rows(recs, cfg)
    prf = _prf(rows)
    f1 = bootstrap_ci(rows, metric="f1")
    pr = bootstrap_ci(rows, metric="precision")
    rc = bootstrap_ci(rows, metric="recall")
    npos = sum(r["y"] for r in rows)
    dom = Counter((dom_by_id.get(r["id"], "?"), "pos" if r["y"] else "neg") for r in rows)
    tag = "튜닝셋" if sp == "dev" else "held-out"
    L += [f"## {sp.upper()} — n={len(rows)} (양성 {npos}) · {tag}",
          f"- 도메인: " + "; ".join(f"{d}/{l}={n}" for (d, l), n in sorted(dom.items())),
          f"- F1        = {f1[0]:.3f}  (95% CI {f1[1]:.3f} ~ {f1[2]:.3f})",
          f"- Precision = {pr[0]:.3f}  (95% CI {pr[1]:.3f} ~ {pr[2]:.3f})",
          f"- Recall    = {rc[0]:.3f}  (95% CI {rc[1]:.3f} ~ {rc[2]:.3f})", ""]
report = "\n".join(L)
(OUT_DIR / "held_out_ci_relabeled.md").write_text(report, encoding="utf-8")
print(report)
