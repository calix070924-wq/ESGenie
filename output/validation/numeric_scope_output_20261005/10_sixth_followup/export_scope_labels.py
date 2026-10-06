"""6차 독립 원문의 모델 라벨 충돌→최종 출력 대조. 외부 호출을 차단한다."""
from pathlib import Path
import importlib.util,json,socket,sys
w=Path('/private/tmp/ESGenie-numeric-scope-output-20261005');sys.path.insert(0,str(w))
out=Path(__file__).resolve().parents[1]/'edge_output'
def blocked(*a,**k):raise AssertionError('offline validation only')
socket.socket.connect=blocked;socket.create_connection=blocked
spec=importlib.util.spec_from_file_location('sixth_review',w/'tests/test_pr71_sixth_review_regressions.py');t=importlib.util.module_from_spec(spec);spec.loader.exec_module(t)
from esgenie.layer6_report import ReportDoc,ReportBlock
from esgenie.exporters.report_pdf import export_report_pdf
import fitz
results=[];blocks=[];labels=['김해 제1공장 교육 참석 인원','제1공장 교육 참석 인원']
for scope in t.HEADINGS:
    scope_results=[]
    for label in labels:
        for extent in ['count','whole_source']:
            text=t.source(scope);quote='참석 27명' if extent=='count' else text;r=t.run_case(label,text,quote)
            assert any(m.get('action')=='replaced' for m in r['marks'])
            assert r['facts'][0]['value']==27 and r['facts'][0]['site_scope']==scope
            assert not r['facts'][0]['site_identity']
            assert '제1공장' not in r['table'] and '제1공장' not in r['prompt_facts']
            assert any(c['original_label']==label for m in r['marks'] for c in m.get('scope_corrections',[]))
            scope_results.append({'scope':scope,'label':label,'extent':extent,**r})
    neutral=t.run_case('교육 참석 인원',t.source(scope),'참석 27명')
    assert any(m.get('action')=='replaced' for m in neutral['marks']);results.extend(scope_results);results.append({'scope':scope,'label':'교육 참석 인원','extent':'neutral',**neutral})
    held=scope_results[0]
    blocks.append(ReportBlock(scope,'전사 범위 대조' if scope=='entity' else '사업장 미기록 대조',held['body']+'\n\n'+held['table'],'deterministic',held['marks']))
normal_text='교육 기록: 김해 제1공장 / 2026-06-03\n참석 27명'
for label in labels:
    r=t.run_case(label,normal_text,'참석 27명');assert not any(m.get('action')=='replaced' for m in r['marks']);results.append({'scope':'site','label':label,'extent':'normal',**r})
blocks.append(ReportBlock('normal','정상 사업장 대조',r['body']+'\n\n'+r['table'],'deterministic',r['marks']))
doc=ReportDoc('검증용','',2026,'2026-10-06',blocks)
(out/'scope_labels.md').write_text(doc.to_markdown())
pdf_path=export_report_pdf(doc,out);flat=lambda s:''.join(s.split());wrong='2026년 6월 3일 김해 제1공장 교육에는 27명이 참석했다'
with fitz.open(pdf_path) as pdf:
    text=''.join(p.get_text() for p in pdf)
    assert flat(text).count(flat(wrong))==1
    assert flat('전체 사업장') in flat(text) and flat('원문 미기록') in flat(text)
    for i,p in enumerate(pdf):p.get_pixmap(matrix=fitz.Matrix(1,1)).save(out/f'page_{i+1}.png')
    pages=len(pdf)
(out/'results.json').write_text(json.dumps({'checks':12,'passed':12,'results':results,'pdf':str(pdf_path),'pages':pages},indent=2,ensure_ascii=False))
print('label scope exports:12/12; pages:',pages,pdf_path)
