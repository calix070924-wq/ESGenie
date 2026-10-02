"""tests/test_pr68_review_r1_r5.py::_pipeline(dfef4f7) 사본 — 수정 전후·main에서 같은 파이프라인을 쓰기 위해 고정."""
from types import SimpleNamespace


def _pipeline(exts, framework="rba42"):
    from esgenie.ssot.audit_trace import build_data_points
    from esgenie.ssot.detector_5axis import detect_d1_numeric
    from esgenie.ssot.evidence_graph import build_unified_graph
    from esgenie.ssot.ssot_pipeline import extract_with_ssot
    from esgenie.supplychain.responder import build_response_sheet
    g = build_unified_graph(None, exts, corp_code="TEST", corp_name="가상회사", report_year=2026)
    report = SimpleNamespace(source="ssot_local", corp_code="TEST", corp_name="가상회사", report_year=2026,
                             fiscal_year=2026, kesg_data={}, sections={}, raw_text="")
    result = extract_with_ssot(report, g, profile="sme")
    codes = [c for c in ("E-4-1", "E-3-1", "E-5-1", "E-6-1", "E-6-2") if g.resolved_facts.get(c)]
    scores, evaluations = {}, {}
    for code in codes:
        fact = g.resolved_facts[code]
        axis = detect_d1_numeric(f"{result.mapped[code]['name']} {fact.value}{fact.unit}", code, g)
        scores[code], evaluations[code] = axis.score, axis.evaluation
    points = build_data_points(g, scores, target_codes=codes, d1_evaluations=evaluations)
    sheet = build_response_sheet(framework, corp_name="가상회사", extraction=result, data_points=points)
    answers = {code: a for a in sheet.answers for code in codes if a.qid.endswith(f"-{code}")}
    return g, {p.kesg_code: p for p in points}, sheet, answers
