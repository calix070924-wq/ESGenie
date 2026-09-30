"""PR #68 4차 검토(R8 재보완) 짝 검사 실행기 — 테스트와 review-r4 재현 스크립트가 함께 쓴다.

한 사례를 표 객체·마크다운·텍스트 줄·로컬 PDF 경로로 넣고, 추출값 → 근거 그래프 → 최종 선택 → 답변까지 실행해
관찰값(observe)과 기대 위반 목록(violations)을 만든다. 외부 API를 호출하지 않는다.
"""
from __future__ import annotations

import json
from pathlib import Path

from esgenie.ssot import ocr_router as R
from tests.test_pr68_review_r1_r5 import _pipeline, _run

FIXTURE = Path(__file__).parent / "fixtures" / "ocr_numeric_review_r4" / "pair_cases.json"
VARIANT_MARK = "검증용 가상 변형본 — 원본 아님"


def load_cases(path=FIXTURE):
    return json.loads(Path(path).read_text(encoding="utf-8"))["cases"]


def _markdown(rows):
    width = max(map(len, rows))
    lines = ["| " + " | ".join(rows[0] + [""] * (width - len(rows[0]))) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r + [""] * (width - len(r))) + " |" for r in rows[1:]]
    return "\n".join(lines)


def run_path(case, path, tmp_dir=None):
    tables, doc_type, extra = case["tables"], case["doc_type"], case["extra"]
    if path == "table_cells":
        return _run(tables, doc_type, extra=extra, pages=list(range(len(tables))))
    if path == "markdown":
        tokens = [{"text": s, "bbox": None, "page": 0} for s in extra]
        tokens += [{"text": _markdown(rows), "bbox": [0.05, 0.2 + t * 0.3, 0.6, 0.4 + t * 0.3], "page": t}
                   for t, rows in enumerate(tables)]
        return R._tokens_to_extraction(tokens, doc_type=doc_type, file_path="md.pdf", engine="upstage_dp", tables=[])
    lines = [[s] for s in extra]
    for rows in tables:
        lines += [list(r) for r in rows] + [[]]
    if path == "text_lines":
        tokens = [{"text": t, "bbox": [0.08 + c * 0.22, 0.1 + r * 0.03, 0.2 + c * 0.22, 0.11 + r * 0.03], "page": 0}
                  for r, row in enumerate(lines) for c, t in enumerate(row) if t]
        return R._tokens_to_extraction(tokens, doc_type=doc_type, file_path="lines.pdf", engine="pymupdf")
    if path == "local_pdf":
        import fitz
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        for r, row in enumerate([[VARIANT_MARK]] + lines):
            for c, text in enumerate(row):
                if text:
                    page.insert_text((40 + c * 130, 60 + r * 22), text, fontname="korea", fontsize=10)
        out = Path(tmp_dir) / "case.pdf"
        doc.save(str(out))
        return R._extract_structured_no_llm(str(out), doc_type=doc_type)
    raise ValueError(path)


def _hits(value, forbidden):
    return any(abs(value - f) < 1e-6 or abs(value - f / 1e6) < 1e-9 or abs(value - round(f * 0.0000561, 3)) < 1e-9
               for f in forbidden)


def observe(ext, forbidden=()):
    g, dps, sheet, answers = _pipeline([ext])
    tm = ext.router_meta.get("table_metrics") or {}
    ans = {}
    for code in ("E-4-1", "E-3-1"):
        a = answers.get(code)
        dp = dps.get(code)
        ans[code] = None if a is None else {
            "value": a.value, "unit": getattr(dp, "unit", None), "status": a.status,
            "evidence": [link.quote for link in a.evidence_links],
            "reference": [link.quote for link in a.reference_links]}
    status = {code: sorted({a.status for a in sheet.answers if a.qid.endswith(f"-{code}")}) for code in ("E-4-1", "E-3-1")}
    link_text = " ".join(q for a in ans.values() if a for q in (*a["evidence"], *a["reference"]))
    fee_texts = [t for f in forbidden for t in (f"{int(f):,}", str(int(f)))]
    return {
        "metrics": sorted([m.value, m.unit] for m in ext.metrics),
        "metric_sources": [{"value": m.value, "unit": m.unit, "row_label": (m.source_detail or {}).get("row_label"),
                            "scope": (m.source_detail or {}).get("scope"), "hint": m.metric_hint,
                            "cell": (m.source_detail or {}).get("cell_text"), "bbox": m.bbox}
                           for m in ext.metrics],
        "answers": ans, "answer_status": status,
        "review_reasons": sorted({r["reason"] for r in tm.get("review", [])}),
        "forbidden_in_metrics": [m.value for m in ext.metrics if _hits(m.value, forbidden)],
        "forbidden_in_graph": sorted({n.value for n in g.nodes.values()
                                      if isinstance(n.value, (int, float)) and _hits(n.value, forbidden)}),
        "forbidden_in_links": [t for t in fee_texts if t in link_text],
        "fake_3_m3": any(m.value == 3 and m.unit in ("m³", "m3") for m in ext.metrics),
    }


def violations(case, obs):
    exp, out = case["expected"], []
    if obs["metrics"] != sorted(exp["metrics"]):
        out.append(f"metrics {obs['metrics']} != {sorted(exp['metrics'])}")
    for code in ("E-4-1", "E-3-1"):
        if code not in exp:
            continue
        a = obs["answers"][code]
        if exp[code] is None:
            if a is not None and a["value"] is not None:
                out.append(f"{code} should be absent, got {a['value']}")
        elif a is None or a["value"] is None or abs(a["value"] - exp[code][0]) > 1e-6 or a["unit"] != exp[code][1]:
            out.append(f"{code} {None if a is None else (a['value'], a['unit'])} != {exp[code]}")
    for k in ("forbidden_in_metrics", "forbidden_in_graph", "forbidden_in_links"):
        if obs[k]:
            out.append(f"{k} {obs[k]}")
    if obs["fake_3_m3"]:
        out.append("fake 3 m3")
    ev = case.get("evidence_contains")
    if ev and not (obs["answers"]["E-4-1"] and any(ev in q for q in obs["answers"]["E-4-1"]["evidence"])):
        out.append(f"E-4-1 evidence lacks {ev}")
    for axis, values in (exp.get("scope") or {}).items():
        got = sorted((s["scope"] or {}).get(axis) for s in obs["metric_sources"])
        if got != sorted(values):
            out.append(f"scope {axis} {got} != {values}")
    return out
