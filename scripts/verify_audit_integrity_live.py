"""Isolated real API audit for the seven fictional Hanwool documents.

Load existing credentials only into this process. No mocked network responses are used.
First invocation requires an absent run directory; --reuse shares only its dedicated caches.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def serialize(value):
    if hasattr(value, 'to_dict'):
        return value.to_dict()
    if is_dataclass(value):
        return asdict(value)
    return str(value)


def write_json(path, value):
    path.write_text(json.dumps(value,default=serialize,ensure_ascii=False,indent=2),encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file',type=Path,required=True)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--reuse',action='store_true')
    parser.add_argument('--label',default='',help='Separate output label for a follow-up verification')
    args = parser.parse_args()
    from dotenv import load_dotenv
    if not args.env_file.is_file():
        raise SystemExit('Existing environment file is missing')
    load_dotenv(args.env_file,override=True)
    for name in ('UPSTAGE_API_KEY','OPENAI_API_KEY','AZURE_OPENAI_ENDPOINT','OPENAI_MODEL'):
        if not os.environ.get(name):
            raise SystemExit(f'Required existing setting is absent: {name}')
    root = args.run_dir.resolve()
    if not args.reuse:
        root.mkdir(parents=True,exist_ok=False)
    elif not (root/'run1'/'live.json').is_file():
        raise SystemExit('A completed first run is required for cache reuse')
    if args.label and (not args.label.replace('_','').isalnum()):
        raise SystemExit('Run label must contain only letters, digits or underscores')
    run_dir = root/(args.label or ('run2' if args.reuse else 'run1'))
    run_dir.mkdir(exist_ok=False)
    for key,value in {
        'ESGENIE_FORCE_MOCK':'0','ESGENIE_STRICT':'1',
        'ESGENIE_OCR_CACHE':'1','ESGENIE_LLM_CACHE':'1',
        'ESGENIE_OCR_CACHE_REFRESH':'0','ESGENIE_LLM_CACHE_REFRESH':'0',
        'ESGENIE_OCR_CACHE_DIR':str(root/'cache'/'ocr'),
        'ESGENIE_LLM_CACHE_DIR':str(root/'cache'/'llm'),
        # Use already installed/local embedding resources, not an unrelated download.
        'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1',
    }.items():
        os.environ[key] = value
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s',
                        handlers=[logging.FileHandler(run_dir/'pipeline.log',encoding='utf-8'),
                                  logging.StreamHandler()])
    logging.getLogger('httpx').setLevel(logging.WARNING)
    from esgenie import pipeline,llm_cache,llm,layer5_audit_trace
    from esgenie.config import SETTINGS
    from esgenie.ssot import ocr_router,ocr_cache
    from esgenie.supplychain import parse_saq_claims,respond_from_pipeline
    from esgenie.supplychain.exporters import export_response_sheet,export_response_sheet_pdf
    from esgenie.layer1_extract import evidence_coverage_pct
    from run_demo_hanwool import collect_inputs,CORP_CODE,CORP_NAME,INDUSTRY,REPORT_YEAR,_axis_payload

    evidence,claims_paths,input_files = collect_inputs(True)
    if len(evidence) != 4 or len(claims_paths) != 3:
        raise SystemExit('Expected objective documents 01-04 and SAQ 05-07')
    input_manifest = [{'file':Path(p).name,'role':('evidence' if p in evidence.values() else 'self_claim'),
                       'sha256':hashlib.sha256(Path(p).read_bytes()).hexdigest()}
                      for p in [*evidence.values(),*claims_paths]]
    settings = {'force_mock':SETTINGS.force_mock,'strict':SETTINGS.strict_llm,
                'model':SETTINGS.openai_model,'connection':llm.CLIENT.cache_connection(),
                'ocr_cache':str(ocr_cache.cache_dir()),'llm_cache':os.environ['ESGENIE_LLM_CACHE_DIR']}
    if args.reuse:
        previous = json.loads((root/'run1'/'live.json').read_text())
        if previous['inputs'] != input_manifest or previous['settings'] != settings:
            raise SystemExit('Input or settings differ from first run')
    write_json(run_dir/'manifest.json',{'inputs':input_manifest,'settings':settings})
    dp_events=[]
    completion_events=[]
    real_dp=ocr_router._call_upstage_dp_payload
    real_complete=llm.LLMClient.complete

    def observe_dp(file_path,**kwargs):
        event={'file':Path(file_path).name,'pages':kwargs.get('pages'),'success':False}
        dp_events.append(event)
        try:
            result=real_dp(file_path,**kwargs)
            event.update(result.get('response_meta',{}))
            event.update(success=True,token_count=len(result['tokens']),table_count=len(result['tables']))
            event['sample']=[{'text':t['text'][:160],'page':t.get('page'),'bbox':t.get('bbox')}
                             for t in result['tokens'][:2]]
            logging.info('DP success: %s model=%s tokens=%s tables=%s',event['file'],
                         event.get('returned_model'),event['token_count'],event['table_count'])
            return result
        except Exception as exc:
            event['error_type']=type(exc).__name__
            raise
        finally:
            write_json(run_dir/'dp-events.json',dp_events)

    def observe_completion(self,*a,**kw):
        result=real_complete(self,*a,**kw)
        completion_events.append({'hint':kw.get('mock_hint'),'used_mock':result.used_mock,
                                  'meta':result.meta,'content_chars':len(result.content),
                                  'content_sha256':hashlib.sha256(result.content.encode()).hexdigest()})
        write_json(run_dir/'completion-events.json',completion_events)
        return result

    ocr_router._call_upstage_dp_payload=observe_dp
    llm.LLMClient.complete=observe_completion
    layer5_audit_trace.OUTPUT_DIR=run_dir/'traces'
    llm_cache.reset_stats()
    start=time.monotonic()
    try:
        claims=parse_saq_claims(claims_paths)
        out=pipeline.run(CORP_CODE,areas=['E','S','G'],corp_name=CORP_NAME,industry=INDUSTRY,
            report_year=REPORT_YEAR,use_dart=False,evidence_files=evidence,save_traces=True,
            export_outputs=True,export_root=run_dir/'artifacts',export_report=True)
        out.supplier_claims=claims
        out.supplier_claim_files=[Path(p).name for p in claims_paths]
        sheets={key:respond_from_pipeline(out,key,supplier_claims=claims) for key in ('rba42','saq5_env')}
        response_paths={}
        for key,sheet in sheets.items():
            response_paths[key]={'xlsx':export_response_sheet(sheet,run_dir/'responses'),
                'pdf':export_response_sheet_pdf(sheet,run_dir/'responses',embed_evidence=False)}
        hits,misses,mode=ocr_cache.summarize(out.ocr_extractions)
        data={'inputs':input_manifest,'settings':settings,'elapsed_sec':round(time.monotonic()-start,2),
              'dp_events':dp_events,'llm_stats':llm_cache.stats(),'llm_success_events':llm_cache.response_events(),
              'completion_events':completion_events,
              'ocr_cache':{'hits':hits,'misses':misses,'mode':mode},
              'ocr':[{'file':x.source_file,'channel':x.channel.value,'doc_type':x.doc_type,
                      'router_meta':x.router_meta,'metrics':x.metrics,'clauses_count':len(x.clauses),
                      'table_count':len(x.tables)} for x in out.ocr_extractions],
              'ledger':out.extraction,'graph':out.evidence_graph,'risk_rows':out.risk_rows,
              'v15':out.v15_trace,'d6':out.disclosure,'evidence_coverage_pct':evidence_coverage_pct(out.extraction),
              'claims':claims,'claim_diagnostics':claims.diagnostics,'responses':sheets,
              'sections':{k:{'score':v.final_score,'band':v.final_band,'converged':v.converged,
                             'hitl_required':v.hitl_required,'evaluation':v.final.detection.risk_vector.aggregate,
                             'axes':_axis_payload(v.final.detection.risk_vector),
                             'final_text':v.final_text} for k,v in out.sections.items()},
              'exports':out.export_paths,'response_paths':response_paths}
        failures=[]
        if not any(x['success'] and x.get('status_code') == 200 for x in dp_events): failures.append('No successful real DP response')
        if any(not x['success'] for x in dp_events): failures.append('DP request failure occurred')
        if len(out.ocr_extractions)!=4: failures.append('Missing document extraction')
        if any(x.router_meta.get('upstage_error') or x.router_meta.get('mock') for x in out.ocr_extractions):
            failures.append('Local/mock fallback occurred')
        if not completion_events or any(x['used_mock'] for x in completion_events):
            failures.append('No real GPT responses or mock fallback occurred')
        if not args.reuse and llm_cache.stats()['successes'] < 1:
            failures.append('No successful real GPT call in first run')
        if not all('gpt-4.1-mini' in str(x['meta'].get('returned_model','')) for x in completion_events):
            failures.append('Returned model is missing or differs from GPT-4.1-mini')
        if not out.export_paths.get('report_pdf'): failures.append('Missing report PDF')
        data['live_api_success']=not failures
        data['failures']=failures
        write_json(run_dir/'live.json',data)
        import pickle
        with (run_dir/'pipeline-output.pkl').open('wb') as fh:
            pickle.dump((out,sheets),fh)
        logging.info('Live API verification %s; DP=%s GPT=%s OCR cache=%s',
                     not failures,len(dp_events),llm_cache.stats(),data['ocr_cache'])
        if failures: raise SystemExit('; '.join(failures))
    finally:
        write_json(run_dir/'llm-stats.json',llm_cache.stats())
        write_json(run_dir/'llm-success-events.json',llm_cache.response_events())


if __name__=='__main__':
    main()
