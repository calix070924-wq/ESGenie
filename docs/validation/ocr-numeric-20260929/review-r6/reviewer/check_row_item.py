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
for item in ('납부예정금액', '예상 사용량 및 요금', '계획 사용량 및 요금'):
    rows = [['항목', '사용량(m3)', '사용열량(MJ)', '요금(원)'], [item, '8,420', '360,772', '247,500']]
    extra = ['검증용 가상 변형본 — 원본 아님', '사용 기간: 2026-04-01 ~ 2026-04-30']
    for route in ('table_replay', 'local_pdf'):
        key = f'{item}/{route}'
        if route == 'table_replay':
            ext = h._run([rows], 'gas_bill', extra=extra)
        else:
            pdf = out / f'{item}.pdf'
            doc = fitz.open()
            page = doc.new_page(width=595, height=842)
            for r, row in enumerate([[s] for s in extra] + rows):
                for c, text in enumerate(row):
                    page.insert_text((25+c*140, 60+r*22), text, fontname='korea', fontsize=9)
            doc.save(str(pdf))
            doc.close()
            ext = R._extract_structured_no_llm(str(pdf), doc_type='gas_bill')
        g, dps, sheet, answers = h._pipeline([ext])
        results[key] = {'extraction':asdict(ext), 'points':{k:asdict(v) for k,v in dps.items()}, 'answers':{k:asdict(v) for k,v in answers.items()}, 'nodes':[asdict(n) for n in g.nodes.values()]}
        print(json.dumps({'case':key,'metrics':[(m.value,m.unit,m.metric_hint,m.source_detail.get('row_item')) for m in ext.metrics], 'nodes':[(n.metric,n.value,n.boundary.basis) for n in g.nodes.values()], 'answers':{k:[a.value,a.unit,a.status] for k,a in answers.items()}},ensure_ascii=False))

rows = [['사업장','기간','계량기','항목','사용량(kWh)','요금(원)'], ['김해 제1공장','2026년 4월','전력계 A','납부예정금액','1,000','150,000']]
ext = h._run([rows], 'kepco_bill')
g, dps, sheet, answers = h._pipeline([ext])
results['scoped_mixed']={'extraction':asdict(ext), 'points':{k:asdict(v) for k,v in dps.items()}, 'answers':{k:asdict(v) for k,v in answers.items()}}
print(json.dumps({'case':'scoped_mixed', 'metrics':[(m.value,m.unit,m.source_detail.get('scope')) for m in ext.metrics], 'points':{k:[v.value,v.boundary] for k,v in dps.items()}},ensure_ascii=False))
(out/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,default=str))
