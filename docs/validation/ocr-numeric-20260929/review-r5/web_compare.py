"""정상 BM 웹 결과 대조 — r5 통합(a371086)과 r4 통합(25720c9)의 최초·보완 result·Excel 셀·번들 PDF 본문.
식별자·시각·경로·리비전 키는 비교에서 뺀다(review-r4/web_compare.py와 같은 규칙).
사용: web_compare.py <r4_server_runs> <r4_drive> <r5_server_runs> <r5_drive> <out.json>"""
import json
import sys
from pathlib import Path

import openpyxl

IGN = ["document_id", "id", "project_id", "updated_at", "created_at", "job", "started", "finished", "revision",
       "seconds", "_at", "url", "path"]
a_runs, a_drive, b_runs, b_drive, out = map(Path, sys.argv[1:6])


def clean(x):
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items() if not any(s == k or (s.startswith("_") and k.endswith(s)) or
                                                             (len(s) > 2 and s in k) for s in IGN)}
    if isinstance(x, list):
        return [clean(v) for v in x]
    return x


def diff(a, b, p=""):
    if type(a) is not type(b):
        return [p]
    if isinstance(a, dict):
        return [q for k in sorted(set(a) | set(b)) for q in (diff(a[k], b[k], f"{p}/{k}") if k in a and k in b else [f"{p}/{k}"])]
    if isinstance(a, list):
        return [p + "#len"] if len(a) != len(b) else [q for i, (x, y) in enumerate(zip(a, b)) for q in diff(x, y, f"{p}[{i}]")]
    return [] if a == b else [p]


def cells(p):
    wb = openpyxl.load_workbook(p)
    return {ws.title: [[c.value for c in row] for row in ws.iter_rows()] for ws in wb.worksheets}


rec = {}
for stage, rev in (("initial", 13), ("followup", 14)):
    ra = json.loads(next(a_runs.glob(f"*_rev{rev}/result.json")).read_text(encoding="utf-8"))
    rb = json.loads(next(b_runs.glob(f"*_rev{rev}/result.json")).read_text(encoding="utf-8"))
    d = diff(clean(ra), clean(rb))
    rec[stage] = {"result_diff_paths": d[:50], "result_diff_count": len(d),
                  "pdf_text_identical": (a_drive / "downloads" / stage / "report_text.txt").read_text(encoding="utf-8")
                  == (b_drive / "downloads" / stage / "report_text.txt").read_text(encoding="utf-8"),
                  "xlsx_cells_identical": cells(a_drive / "downloads" / stage / f"검토용초안_{stage}.xlsx")
                  == cells(b_drive / "downloads" / stage / f"검토용초안_{stage}.xlsx")}
rec["stale_download"] = {"a371086": json.loads((b_drive / "followup_stale_download_check.json").read_text())}
rec["ignored_key_substrings"] = IGN
out.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({k: v for k, v in rec.items() if k != "ignored_key_substrings"}, ensure_ascii=False))
