"""Replay saved extraction/ledger without OCR, LLM, or network calls."""
import argparse
import hashlib
import json
import esgenie
from pathlib import Path
from types import SimpleNamespace

from esgenie.ssot.ocr_router import OcrExtraction
from esgenie.ssot.evidence_graph import build_unified_graph
from esgenie.ssot.audit_trace import DataPoint, EvidenceLink
from esgenie.supplychain.claims import SupplierClaim, trace_claim_values
from esgenie.ssot.selection import ResolvedFact
from esgenie.supplychain.responder import build_response_sheet


def record(path, transform=None):
    saved = json.loads(path.read_text())
    inputs = [OcrExtraction.from_dict(x) for x in saved['ocr_extractions']]
    if transform:
        transform(inputs)
    trace = saved['v15_trace']
    graph = build_unified_graph(None, inputs, corp_code=trace['ticker'], corp_name=trace['corp_name'], report_year=2026)
    graph.resolved_facts = {c: ResolvedFact(**entry['resolved_fact'])
                            for c, entry in saved['extraction']['mapped'].items() if entry.get('resolved_fact')}
    points = []
    for row in trace['data_points']:
        row = dict(row)
        for key in ('evidence_files', 'reference_files'):
            row[key] = [EvidenceLink(**x) for x in row.get(key, [])]
        points.append(DataPoint(**row))
    # 웹 엔진은 pipeline 이후 회사 답변을 파싱한다. pipeline.json에는 이 주장이
    # 비어 있으므로 같은 실행의 result.json에 저장된 실제 회사 답변도 복원한다.
    claim_rows = dict(saved.get('supplier_claims') or {})
    if not claim_rows:
        result = json.loads(path.with_name('result.json').read_text())
        claim_rows = {row['code']: row for a in result['sheet']['answers']
                      for row in a.get('self_reports', []) if row.get('code')}
    claims = {k: SupplierClaim(**v) for k, v in claim_rows.items()}
    claims = trace_claim_values(claims, SimpleNamespace(evidence_graph=graph, ocr_extractions=inputs))
    disclosure = SimpleNamespace(**{k: [SimpleNamespace(**x) for x in saved['disclosure'].get(k, [])]
                                  for k in ('orphan_ratios', 'omitted_sensitive')})
    gap = saved.get('issb_gap') or {}
    sheet = build_response_sheet('rba42', extraction=SimpleNamespace(**saved['extraction']),
          evidence_graph=graph, data_points=points, supplier_claims=claims,
          disclosure=disclosure, issb_gap=SimpleNamespace(rows=[SimpleNamespace(**x) for x in gap.get('rows', [])]),
          policy_audit=trace['policy_audit'])
    return sheet, graph, inputs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for stage, dirname in [('initial', '20261007T062629Z_rev18'), ('followup', '20261007T070758Z_rev19')]:
        path = args.source / 'runs' / dirname / 'pipeline.json'
        sheet, graph, inputs = record(path)
        code_root = Path(esgenie.__file__).resolve().parent
        code_files = ['supplychain/responder.py', 'supplychain/requirement_fitness.py', 'ssot/evidence_graph.py']
        payload = {'code_root': str(code_root),
                   'code_file_sha256': {f: hashlib.sha256((code_root / f).read_bytes()).hexdigest()
                                        for f in code_files if (code_root / f).is_file()},
                   'result_sha256': hashlib.sha256(path.with_name('result.json').read_bytes()).hexdigest(),
                   'evidence_extraction_count': len(inputs), 'company_answer_source': 'same_run_result.self_reports',
                   'mode': 'saved_extraction_and_ledger_replay', 'external_calls': 0,
                   'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                   'set_id': 'hanwool_bm_normal5_20261007_v1', 'sheet': sheet.to_dict(),
                   'tagging': [{'source': x.source_file, 'clauses': len(x.clauses),
                                'tags': [c.rba_code_guess for c in x.clauses]} for x in inputs]}
        (args.output / f'{stage}.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        for a in sheet.answers:
            print(stage, a.qid, a.value, a.status, len(a.evidence_links), a.boundary_label)

if __name__ == '__main__':
    main()
