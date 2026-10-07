"""실제 실행 결과(pipeline.json·result.json)에서 수치 인식 대상 문서의 값만 요약한다.

사용: python check_live_outputs.py <run_stage_dir> [--json out.json]
정답 수치는 판정용 기준으로만 쓰며 제품 코드에는 들어가지 않는다.
"""
import json
import sys
from pathlib import Path

TARGET = ("01_", "02_", "03_", "04_", "06_", "07_", "08_")
EXPECT = [  # (문서, 원문 정답 값, 단위)
    ("02_", 142560.0, "kWh"), ("03_", 8420.0, "m³"), ("07_", 680.0, "m³"),
    ("04_", 18400.0, "kg"), ("04_", 5400.0, "kg"), ("04_", 29.3, "%"),
]
FORBIDDEN = [  # (문서, 값, 단위) — 나오면 실패
    ("03_", None, "MJ"), ("03_", None, "TJ"), ("03_", None, "GJ"), ("06_", 3.0, None), ("04_", 18.4, "ton"),
]


def main(stage: Path) -> dict:
    p = json.loads((stage / "pipeline.json").read_text(encoding="utf-8"))
    out = {"stage": str(stage), "ocr": {}, "graph": [], "answers": [], "checks": []}
    for x in p["ocr_extractions"]:
        if not x["source_file"].startswith(TARGET):
            continue
        rm = x.get("router_meta", {})
        out["ocr"][x["source_file"]] = {
            "doc_type": x.get("doc_type"), "engine": rm.get("engine"),
            "table_checks": [(c["name"], c["status"]) for c in (rm.get("table_metrics") or {}).get("checks", [])],
            "hitl_required": rm.get("hitl_required", False),
            "metrics": [{"label": m.get("metric_hint"), "value": m.get("value"), "unit": m.get("unit"),
                         "code": m.get("kesg_code_guess"), "page": m.get("page"), "bbox": m.get("bbox"),
                         "precision": (m.get("source_detail") or {}).get("precision"),
                         "index_check": ((m.get("source_detail") or {}).get("index_check") or {}).get("status")}
                        for m in x.get("metrics", [])]}
    for n in p["evidence_graph"]["nodes"]:
        if not str(n.get("source_file") or "").startswith(TARGET):
            continue
        b = n.get("boundary") or {}
        prov = [q for q in b.get("provenance", []) if q.get("source") == "table_cell"] + \
               [q for q in n.get("provenance", []) or [] if q.get("source") == "table_cell"]
        out["graph"].append({"file": n["source_file"][:3], "metric": n.get("metric"), "value": n.get("value"),
                             "unit": n.get("unit"), "code": n.get("kesg_code") or n.get("code"),
                             "period_text": b.get("period_text"), "site": b.get("site"),
                             "site_scope": b.get("site_scope"), "aggregation": b.get("aggregation"),
                             "page": n.get("page"), "bbox": bool(n.get("bbox")),
                             "table_cell_precision": [q.get("precision") for q in prov]})
    result_path = stage / "result.json"
    if result_path.exists():
        r = json.loads(result_path.read_text(encoding="utf-8"))
        for it in r.get("sheet", {}).get("answers", []):
            blob = json.dumps(it.get("evidence_links"), ensure_ascii=False) + str(it.get("unit"))
            if any(k in blob for k in ("02_", "03_", "04_", "07_", "kWh", "m³", "TJ", "MJ")):
                out["answers"].append({"qid": it["qid"], "value": it.get("value"), "unit": it.get("unit"),
                                       "display_value": it.get("display_value"), "status": it.get("status"),
                                       "badge": it.get("badge"), "boundary_label": it.get("boundary_label"),
                                       "links": [{k: l.get(k) for k in ("source_file", "metric", "value", "unit",
                                                                        "page", "kesg_code") if k in l}
                                                 for l in it.get("evidence_links", []) if isinstance(l, dict)]})
    ocr_all = [(f, m) for f, d in out["ocr"].items() for m in d["metrics"]]
    for prefix, value, unit in EXPECT:
        hit = [m for f, m in ocr_all if f.startswith(prefix) and m["value"] == value and m["unit"] == unit]
        out["checks"].append({"doc": prefix, "expect": f"{value} {unit}", "found": bool(hit),
                              "precision": [m["precision"] for m in hit], "bbox": [bool(m["bbox"]) for m in hit]})
    for prefix, value, unit in FORBIDDEN:
        hit = [m for f, m in ocr_all if f.startswith(prefix) and (value is None or m["value"] == value)
               and (unit is None or m["unit"] == unit)]
        out["checks"].append({"doc": prefix, "forbid": f"{value} {unit}", "found": bool(hit)})
    out["checks"].append({"doc": "01_", "forbid": "waste_ledger", "found": any(
        f.startswith("01_") and d["doc_type"] == "waste_ledger" for f, d in out["ocr"].items())})
    return out


if __name__ == "__main__":
    res = main(Path(sys.argv[1]))
    if "--json" in sys.argv:
        Path(sys.argv[sys.argv.index("--json") + 1]).write_text(
            json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"checks": res["checks"], "graph": res["graph"], "answers": res["answers"]},
                     ensure_ascii=False, indent=1))
