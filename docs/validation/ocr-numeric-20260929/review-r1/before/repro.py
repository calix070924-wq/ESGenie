import os
import sys
import json

os.environ['ESGENIE_FORCE_MOCK'] = '1'
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
sys.path.insert(0, sys.argv[1])
from esgenie.ssot import ocr_router as R
from esgenie.ssot.evidence_graph import EvidenceGraph, merge_ocr_extraction

R._get_openai_key = lambda: None
R._get_anthropic_key = lambda: None
R._get_upstage_key = lambda: None

def run_case(name, rows_list, doc_type, extra=()):
    tokens = [{'text': s, 'bbox': None, 'page': 0} for s in extra]
    tables = []
    for t, rows in enumerate(rows_list):
        cells = []
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                box = [0.05 + c * 0.16, 0.2 + t * 0.2 + r * 0.03,
                       0.19 + c * 0.16, 0.21 + t * 0.2 + r * 0.03]
                tokens.append({'text': text, 'bbox': box, 'page': 0})
                cells.append(R.TableCell(row_index=r, column_index=c, content=text, bbox=box, page=0))
        tables.append(R.ExtractedTable(table_id=f't{t}', row_count=len(rows),
                                      column_count=max(map(len, rows)), cells=cells, source='upstage_dp', page=0))
    e = R._tokens_to_extraction(tokens, doc_type=doc_type, file_path='review-variant.pdf',
                                 engine='upstage_dp', tables=tables)
    g = EvidenceGraph('review', '가상회사')
    merge_ocr_extraction(g, e, report_year=2026)
    return {
        'case': name,
        'metrics': [{'label': m.metric_hint, 'value': m.value, 'unit': m.unit,
                     'code': m.kesg_code_guess, 'period': m.period, 'confidence': m.confidence,
                     'detail': getattr(m, 'source_detail', {})} for m in e.metrics],
        'meta': e.router_meta,
        'nodes': [{'label': n.metric, 'value': n.value, 'unit': n.unit,
                   'period': n.boundary.period_text, 'site': n.boundary.site,
                   'review_notes': n.boundary.review_notes} for n in g.nodes.values()],
    }

cases = [
    run_case('gas_MJ_usage_header', [[['가스사용량(MJ)'], ['360,772']]], 'gas_bill'),
    run_case('two_sites_equal_values', [
        [['사업장', '사용량(kWh)'], ['김해 제1공장', '1,000']],
        [['사업장', '사용량(kWh)'], ['양산 제2공장', '1,000']]], 'kepco_bill'),
    run_case('two_months_equal_values', [
        [['기간', '사용량(kWh)'], ['2026년 4월', '1,000']],
        [['기간', '사용량(kWh)'], ['2026년 5월', '1,000']]], 'kepco_bill'),
    run_case('computed_mixed_energy_units', [[
        ['전월지침(kWh)', '당월지침(MWh)', '배율'], ['1,000', '2', '1']]], 'kepco_bill'),
    run_case('computed_usage_unit_differs_from_index', [[
        ['전월지침(MWh)', '당월지침(MWh)', '배율', '사용량(kWh)'],
        ['1', '2', '1', '-']]], 'kepco_bill'),
    run_case('explicit_false_agreement_different_units', [[
        ['전월지침(MWh)', '당월지침(MWh)', '배율', '사용량(kWh)'],
        ['1', '2', '1', '1']]], 'kepco_bill'),
    run_case('rounded_integer_rate', [
        [['재활용', '소각', '매립', '합계'], ['29.3 kg', '30 kg', '40.7 kg', '100 kg']]],
        'waste_ledger', ['폐기물 재활용률 29%']),
    run_case('explicit_meter_mismatch', [[
        ['이전 지침', '당월 지침', '계기 배율', '당월 전력 사용량'],
        ['48,210', '50,586', '60', '150,000 kWh']]], 'kepco_bill',
        ['사용 기간: 2026-04-01 ~ 2026-04-30', '사업장: 김해 제1공장']),
]
print(json.dumps(cases, ensure_ascii=False, indent=2))
