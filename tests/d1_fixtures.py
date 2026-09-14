"""D1 missed detections: actual graph → finalized ledger → claim verification."""
from esgenie.dart_client import CompanyReport
from esgenie.ssot.evidence_graph import build_from_dart
from esgenie.ssot.ssot_pipeline import extract_with_ssot
from esgenie.layer3_detect import score_d1_numeric, extract_numeric_claims

CASES = [
    ('normal', '재생에너지 사용 비율은 31%이며 폐기물 재활용 비율은 92%였다.', 0, 2, 0),
    ('swapped', '재생에너지 사용 비율은 92%이며 폐기물 재활용 비율은 31%였다.', 1, 2, 0),
    ('single_error', '재생에너지 사용 비율은 92%였다.', 1, 1, 0),
    ('comma_error', '온실가스 배출량은 9,999 tCO2eq이다.', 1, 1, 0),
    ('plain_error', '온실가스 배출량은 9999 tCO2eq이다.', 1, 1, 0),
    ('missing', '용수 사용량은 100톤이다.', 0, 0, 1),
    ('normal_missing', '재생에너지 사용 비율은 31%이며 용수 사용량은 100톤이다.', 0, 1, 1),
    ('error_missing', '재생에너지 사용 비율은 92%이며 용수 사용량은 100톤이다.', 1, 1, 1),
]

def make_evidence():
    report = CompanyReport('D1', '수치 검증 합성', '', 2025, {}, {
        'E-4-2': {'value': 31, 'unit': '%'},
        'E-6-2': {'value': 92, 'unit': '%'},
        'E-3-1': {'value': 100, 'unit': 'tCO2eq'},
    }, [], 'synthetic')
    graph = build_from_dart(report)
    graph.report_year = 2025
    ledger = extract_with_ssot(report, graph)
    return report, graph, ledger


def verify_case(sentence, *, llm_judge=False, missing_graph=False):
    """Fixed generated text; real ledger, L3, L4 and L5. No external LLM/OCR."""
    from types import SimpleNamespace
    from esgenie.embeddings import IndexedDoc
    from esgenie.layer2_rag import RAGContext, GenerationResult
    from esgenie.layer4_verify import verify_and_refine
    from esgenie.layer5_audit_trace import build_audit_trace
    from esgenie.schemas import GroundingResult
    report, graph, ledger = make_evidence()
    source = IndexedDoc(text=CASES[0][1] + ' 온실가스 배출량은 100 tCO2eq이다.', meta={'id':'evidence'})
    ctx = RAGContext([], [], [(source, 1.0)])
    rag = SimpleNamespace(retrieve_for_area=lambda *a, **kw: ctx,
        generate_section=lambda *a, **kw: GenerationResult('E', sentence, ctx, True))
    # Isolate D1/L4 behavior from the separately-tested citation gate. Retrieval text
    # contains only supplied evidence; the absent water evidence is never invented.
    accepted_gate = lambda *a: GroundingResult('ACCEPT',[],[],[],False,[],[],1.0)
    graph_arg = None if missing_graph else graph
    verify = verify_and_refine(report,'E',rag,corp=None,max_iter=0,
        evidence_graph=graph_arg,llm_judge=llm_judge,grounding_gate=accepted_gate)
    trace = build_audit_trace(report,'E',verify,ledger,graph_arg,llm_judge=llm_judge)
    return verify, trace, graph, ledger

def real_excerpt_evidence():
    import json
    from pathlib import Path
    from esgenie.ssot.ocr_router import OcrExtraction, ExtractedMetric, DocChannel
    from esgenie.ssot.evidence_graph import build_unified_graph
    data = json.loads((Path(__file__).resolve().parents[1] / 'data/d1_validation/real_excerpt.json').read_text())
    imported = OcrExtraction(source_file=data['source_file'], channel=DocChannel.UNSTRUCTURED,
        doc_type='report', metrics=[ExtractedMetric(m['metric_hint'],m['value'],m['unit'],m['period'],
            kesg_code_guess=m['code'],page=m['source_page_index'],confidence=.9) for m in data['metrics']])
    graph = build_unified_graph(None,[imported],corp_code='MOBIS',corp_name='현대모비스',report_year=2024)
    report = CompanyReport('MOBIS','현대모비스','',2024,{}, {}, [],'saved_real_extraction')
    ledger = extract_with_ssot(report,graph)
    return report, graph, ledger, data
