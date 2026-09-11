"""Check which graph-level failures can be reached through the OCR-result importer.

These fixtures begin at structured OCR output, not at a real OCR API call.
The target/projection controls keep the scope of the graph injection tests clear.
"""
import pytest

from esgenie.ssot.ocr_router import OcrExtraction, ExtractedMetric, DocChannel
from esgenie.ssot.evidence_graph import build_unified_graph
from esgenie.ssot.ssot_pipeline import extract_with_ssot
from esgenie.ssot.audit_trace import build_data_points
from esgenie.pipeline import _build_risk_rows
from esgenie.supplychain.frameworks.saq5 import SAQ5_ENV
from esgenie.supplychain.responder import build_response_sheet
from test_fresh_contracts import answer, report


@pytest.mark.parametrize('case,label,year', [
    ('component', '에너지 사용량 국내 사업장', '2025'),
    ('target', '에너지 사용량 목표', '2025'),
    ('projection', '에너지 사용량 전사 합계', '2030'),
])
def test_imported_ocr_output_preserves_selected_actual(case, label, year, observe):
    extraction = OcrExtraction(source_file='synthetic_energy.pdf',
        channel=DocChannel.UNSTRUCTURED, doc_type='report', metrics=[
            ExtractedMetric('에너지 사용량 전사 합계', 248.5, 'TJ', '2025',
                kesg_code_guess='E-4-1', confidence=.9, page=0),
            ExtractedMetric(label, 61.2, 'TJ', year,
                kesg_code_guess='E-4-1', confidence=.99, page=0),
        ])
    graph = build_unified_graph(None, [extraction], corp_code='AUDIT',
        corp_name='Synthetic audit company', report_year=2025)
    ledger = extract_with_ssot(report(), graph, profile='sme')
    assert ledger.mapped['E-4-1']['value'] == 248.5
    scores, rows = _build_risk_rows(graph, target_codes=['E-4-1'])
    points = build_data_points(graph, scores, target_codes=['E-4-1'])
    ans = answer(build_response_sheet(SAQ5_ENV, extraction=ledger,
        data_points=points, evidence_graph=graph), 'SAQ-E-NUM-ENERGY')
    observe(case=case, ledger_value=248.5, output_value=ans.value, status=ans.status,
        graph_nodes=[n.to_dict() for n in graph.nodes.values()], risk_rows=rows,
        points=[p.to_dict() for p in points])
    assert ans.value == 248.5
