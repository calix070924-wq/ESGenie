"""R8 재생 경로별 관찰 — 같은 가상 입력(A: 360,772 / B: '-', 아래 기본요금 247,500)을 표 객체 칸,
좌표 텍스트 줄, 마크다운 표 세 경로로 _tokens_to_extraction에 넣는다. 외부 API 없음.
사용: replay_paths.py <code_path> <out.json>"""
import json
import os
import sys

os.environ["ESGENIE_FORCE_MOCK"] = "1"
sys.path.insert(0, sys.argv[1])
from esgenie.ssot import ocr_router as R  # noqa: E402
from esgenie.ssot.ocr_router import ExtractedTable, TableCell  # noqa: E402

for name in ("_get_openai_key", "_get_anthropic_key", "_get_upstage_key"):
    setattr(R, name, lambda: None)
PERIOD = "사용 기간: 2026-04-01 ~ 2026-04-30"
out = {"code_path": sys.argv[1], "imported": R.__file__, "paths": {}}
for case, heat in (("A_valid_heat", "360,772"), ("B_empty_heat", "-")):
    rows = [["사용량(m3)", "사용열량(MJ)"], ["8,420", heat], ["기본요금", "247,500"]]
    boxes = {(r, c): [0.08 + c * 0.3, 0.2 + r * 0.03, 0.2 + c * 0.3, 0.21 + r * 0.03]
             for r, row in enumerate(rows) for c, _ in enumerate(row)}
    lines = [{"text": t, "bbox": boxes[r, c], "page": 0} for r, row in enumerate(rows) for c, t in enumerate(row)]
    cells = [TableCell(row_index=r, column_index=c, content=t, bbox=boxes[r, c], page=0)
             for r, row in enumerate(rows) for c, t in enumerate(row)]
    md = "\n".join(["| 사용량(m3) | 사용열량(MJ) |", "|---|---|", f"| 8,420 | {heat} |", "| 기본요금 | 247,500 |"])
    runs = {
        "table_cells": dict(tokens=[{"text": PERIOD, "bbox": None, "page": 0}] + lines, engine="upstage_dp",
                            tables=[ExtractedTable(table_id="t0", row_count=3, column_count=2, cells=cells,
                                                   source="upstage_dp", page=0)]),
        "text_lines": dict(tokens=[{"text": PERIOD, "bbox": [0.08, 0.1, 0.5, 0.11], "page": 0}] + lines,
                           engine="pymupdf", tables=None),
        "markdown": dict(tokens=[{"text": PERIOD, "bbox": None, "page": 0},
                                 {"text": md, "bbox": [0.05, 0.2, 0.5, 0.3], "page": 0}], engine="upstage_dp", tables=[]),
    }
    for path, kw in runs.items():
        ext = R._tokens_to_extraction(kw["tokens"], doc_type="gas_bill", file_path=f"{case}.pdf",
                                      engine=kw["engine"], tables=kw["tables"])
        tm = ext.router_meta.get("table_metrics") or {}
        out["paths"][f"{case}/{path}"] = {
            "metrics": [[m.value, m.unit, m.kesg_code_guess] for m in ext.metrics],
            "review": sorted({r.get("reason") for r in tm.get("review", [])}),
        }
json.dump(out, open(sys.argv[2], "w"), ensure_ascii=False, indent=1)
for k, v in out["paths"].items():
    print(k, v["metrics"])
