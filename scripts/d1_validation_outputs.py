"""Generate review evidence using the application's actual JSON/PDF/Excel exporters."""
import argparse
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
# Installs the offline guard before application imports.
from scripts.d1_validation_snapshot import blocked
from tests.d1_fixtures import CASES, verify_case, real_excerpt_evidence, make_evidence
from esgenie.layer6_report import _block_esg, ReportDoc
from esgenie.exporters.report_pdf import export_report_pdf
from esgenie.pipeline import _build_risk_rows
from esgenie.ssot.audit_trace import build_data_points, build_audit_trace_v15
from esgenie.ssot.excel_exporter import export_datasheet
from esgenie.layer3_detect import score_d1_numeric


def datasheet(graph, codes, output):
    scores, rows = _build_risk_rows(graph,target_codes=codes)
    points = build_data_points(graph,scores,target_codes=codes,
        d1_evaluations={r['K-ESG 코드']:r['D1 평가'] for r in rows})
    trace = build_audit_trace_v15(graph.corp_code,graph.corp_name,points,[])
    return rows, export_datasheet(trace,output)


def main(output):
    output.mkdir(parents=True,exist_ok=True)
    results = {'mode':'offline; fixed generated text; real graph, ledger, L3-L6 and exporters; citation gate controlled',
               'cases':[]}
    for case in (CASES[0],CASES[1],CASES[4],CASES[5],CASES[6],CASES[7]):
        name,sentence,*_ = case
        verify,trace,*_ = verify_case(sentence)
        folder = output/name;folder.mkdir(exist_ok=True)
        (folder/'audit.json').write_text(json.dumps(trace.to_dict(),ensure_ascii=False,indent=2,allow_nan=False))
        block = _block_esg(SimpleNamespace(sections={'E':verify}),'E')
        doc = ReportDoc('D1검증','',2025,'2026-09-14',[block])
        (folder/'report.md').write_text(doc.to_markdown())
        export_report_pdf(doc,folder)
        results['cases'].append({'name':name,'text':sentence,'score':verify.final_score,
            'converged':verify.converged,'hitl_required':verify.hitl_required,
            'numeric_evaluation':verify.final.detection.risk_vector.numeric_evaluation})
    _,graph,ledger,source = real_excerpt_evidence()
    rows,paths=datasheet(graph,[m['code'] for m in source['metrics']],output/'actual-document')
    results['actual_document']={'source':source,'rows':rows,
        'unselected_recycling_claim':score_d1_numeric('폐기물 재활용 비율은 92.9%였다.',graph).to_dict()}
    _,graph,_=make_evidence()
    graph.resolved_facts['E-3-1']=replace(graph.resolved_facts['E-3-1'],value=-100)
    results['synthetic_ssot_rows'],_=datasheet(graph,['E-4-2','E-3-1'],output/'synthetic-ssot')
    (output/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,allow_nan=False))
    print('Saved exporter review evidence:',output)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('output',type=Path)
    main(parser.parse_args().output)
