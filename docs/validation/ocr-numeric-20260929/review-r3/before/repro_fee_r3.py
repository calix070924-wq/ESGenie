"""R8 재현 — 검토자 repro_fee.py(/private/tmp/ESGenie-pr68-review3-20260929, 읽기만)와 같은 가상 PDF를
로컬 텍스트 추출 → 근거 그래프 → 원장 선택 → DataPoint → 응답서까지 처리한다. 외부 API 없음(키 None, 모의 LLM).
사용: repro_fee_r3.py <code_path> <out_dir>
비교 기준은 answers의 value·unit·evidence_links와 points(콘솔 answer_text 아님)."""
import hashlib
import importlib.util
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

os.environ["ESGENIE_FORCE_MOCK"] = "1"
base, dest = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(base))
from esgenie.ssot import ocr_router as R  # noqa: E402

for name in ("_get_openai_key", "_get_anthropic_key", "_get_upstage_key"):
    setattr(R, name, lambda: None)
spec = importlib.util.spec_from_file_location("review_helpers", str(Path(__file__).with_name("pipeline_helper.py")))
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
import fitz  # noqa: E402

dest.mkdir(parents=True, exist_ok=True)
out = {"code_path": str(base), "imported": R.__file__, "cases": {}}
for case, heat, fee in [("valid_heat", "360,772", "247,500"), ("empty_heat", "-", "247,500"), ("fee_with_won", "-", "247,500원")]:
    pdf = dest / f"{case}.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    lines = [["검증용 가상 변형본 — 원본 아님"], ["도시가스 요금 청구서"], ["사용 기간: 2026-04-01 ~ 2026-04-30"]]
    lines += [["사용량(m3)", "사용열량(MJ)"], ["8,420", heat], ["기본요금", fee]]
    for r, row in enumerate(lines):
        for c, txt in enumerate(row):
            page.insert_text((50 + c * 180, 60 + r * 22), txt, fontname="korea", fontsize=10)
    doc.save(str(pdf))
    doc.close()
    ext = R._extract_structured_no_llm(str(pdf), doc_type="gas_bill")
    graph, dps, sheet, answers = helpers._pipeline([ext])
    out["cases"][case] = {
        "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
        "metrics": [{"value": m.value, "unit": m.unit, "code": m.kesg_code_guess, "hint": m.metric_hint,
                     "raw_text": (getattr(m, "source_detail", None) or {}).get("raw_text"), "header": (getattr(m, "source_detail", None) or {}).get("header"),
                     "row_label": (getattr(m, "source_detail", None) or {}).get("row_label")} for m in ext.metrics],
        "review": [r.get("reason") for r in ((ext.router_meta.get("table_metrics") or {}).get("review") or [])],
        "nodes": sorted([n.metric, n.value, n.unit] for n in graph.nodes.values()),
        "points": {k: {"value": p.value, "unit": p.unit, "comparison": p.comparison, "scope_notes": p.scope_notes,
                       "evidence": [e.quote for e in p.evidence_files], "reference": [e.quote for e in p.reference_files]}
                   for k, p in dps.items()},
        "answers": {k: {"value": a.value, "unit": a.unit, "status": a.status,
                        "evidence": [e.quote for e in a.evidence_links], "reference": [e.quote for e in a.reference_links]}
                    for k, a in answers.items()},
    }
(dest / "results.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
for c, v in out["cases"].items():
    print(c, [(m["value"], m["unit"], m["code"]) for m in v["metrics"]],
          {k: (a["value"], a["unit"]) for k, a in v["answers"].items()})
