import os
import sys
import json
import importlib.util
from pathlib import Path
from dataclasses import asdict

os.environ['ESGENIE_FORCE_MOCK'] = '1'
base, label = sys.argv[1:3]
sys.path.insert(0, base)
from esgenie.ssot import ocr_router as R
for name in ('_get_openai_key', '_get_anthropic_key', '_get_upstage_key'):
    setattr(R, name, lambda: None)
spec = importlib.util.spec_from_file_location('review_helpers', '/private/tmp/ESGenie-ocr-numeric-20260929/tests/test_pr68_review_r1_r5.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
import fitz

out = Path(__file__).parent / label
out.mkdir(exist_ok=True)
results = {}
for fee_label in ('납부금액', '납부예정금액', '청구예정금액', '추후청구요금'):
    for heat in ('360,772', '-'):
        for route in ('table_replay', 'local_pdf'):
            key = f'{fee_label}/{heat}/{route}'
            rows = [['사용량(m3)', '사용열량(MJ)'], ['8,420', heat], [fee_label, '247,500']]
            extra = ['검증용 가상 변형본 — 원본 아님', '도시가스 요금 청구서', '사용 기간: 2026-04-01 ~ 2026-04-30']
            if route == 'table_replay':
                ext = h._run([rows], 'gas_bill', extra=extra)
            else:
                pdf = out / f'{fee_label}_{"valid" if heat != "-" else "empty"}.pdf'
                doc = fitz.open()
                page = doc.new_page(width=595, height=842)
                for r, row in enumerate([[s] for s in extra] + rows):
                    for c, text in enumerate(row):
                        page.insert_text((50+c*180, 60+r*22), text, fontname='korea', fontsize=10)
                doc.save(str(pdf))
                doc.close()
                ext = R._extract_structured_no_llm(str(pdf), doc_type='gas_bill')
            g, dps, sheet, answers = h._pipeline([ext])
            results[key] = {'extraction':asdict(ext), 'points':{k:asdict(v) for k,v in dps.items()}, 'answers':{k:asdict(v) for k,v in answers.items()}}
            print(json.dumps({'case':key,'metrics':[(m.value,m.unit) for m in ext.metrics], 'answers':{k:[a.value,a.unit] for k,a in answers.items()}, 'review':[r['reason'] for r in ext.router_meta.get('table_metrics',{}).get('review',[])]},ensure_ascii=False))
(out/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,default=str))
