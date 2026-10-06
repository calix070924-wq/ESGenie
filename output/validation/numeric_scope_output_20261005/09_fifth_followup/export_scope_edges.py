"""5차 검토의 원문→매핑→그래프→생성 입력→최종 문서/PDF 대조. 외부 호출 차단."""
from pathlib import Path
import importlib.util, json, socket, sys
w=Path('/private/tmp/ESGenie-numeric-scope-output-20261005')
sys.path.insert(0,str(w))
out=Path(__file__).resolve().parents[1]/'edge_output'
def blocked(*a,**k): raise AssertionError('offline validation only')
socket.socket.connect=blocked; socket.create_connection=blocked
spec=importlib.util.spec_from_file_location('fifth_review',w/'tests/test_pr71_fifth_review_regressions.py')
t=importlib.util.module_from_spec(spec);spec.loader.exec_module(t)
from esgenie.layer6_report import ReportDoc,ReportBlock
from esgenie.layer2_rag import _render_source_facts_table
from esgenie.exporters.report_pdf import export_report_pdf
import fitz
results=[]; held_bodies=[]; scope_rows=[]; blocks=[]
titles=['전사 머리말','사업장 미기록 머리말','전사·수량 같은 줄','미기록·수량 같은 줄','대상 수량이 있는 머리말','적용 범위 표']
wrong='2026년 6월 3일 김해 제1공장 교육에는 27명이 참석했다'
for case_no,(name,tail) in enumerate(t.SCOPES.items()):
    for extent,quote in [('quantity','참석 27명'),('scope_and_quantity',tail),('previous_section',t.PREFIX+tail)]:
        r=t.mapped_review(t.PREFIX+tail,quote)
        assert any(m.get('action')=='replaced' for m in r['marks'])
        assert any(f['value']==27 for f in r['facts'])
        results.append({'case':name,'extent':extent,**r})
    held_bodies.append(titles[case_no]+': '+r['body'])
    scope_rows.extend([{**f, 'label':titles[case_no]+' 참석 인원'} for f in r['facts']])
normal='교육 기록: 김해 제1공장 / 2026-06-03\n교육 내용\n참석 27명'
r=t.mapped_review(normal,'교육 내용\n참석 27명')
assert not any(m.get('action')=='replaced' for m in r['marks'])
results.append({'case':'normal','extent':'subheading',**r})
blocks.append(ReportBlock('scope','범위 전환 6종 대조','\n\n'.join(held_bodies)+'\n\n'+_render_source_facts_table(scope_rows),'deterministic',[]))
blocks.append(ReportBlock('normal','정상 사업장 대조',r['body']+'\n\n'+_render_source_facts_table(r['facts']),'deterministic',r['marks']))
table=_render_source_facts_table([scope_rows[0]])
for row in scope_rows[1:]: table+='\n'+_render_source_facts_table([row]).splitlines()[-1]
blocks[0].body_md='\n\n'.join(held_bodies)+'\n\n'+table
doc=ReportDoc('검증용','',2026,'2026-10-06',blocks)
(out/'scope_edges.md').write_text(doc.to_markdown())
pdf_path=export_report_pdf(doc,out)
flat=lambda s:''.join(s.split())
with fitz.open(pdf_path) as pdf:
    texts=[p.get_text() for p in pdf]
    joined=''.join(texts)
    assert flat(joined).count(flat(wrong)) == 1
    assert flat('27명') in flat(joined)
    for i,p in enumerate(pdf): p.get_pixmap(matrix=fitz.Matrix(1,1)).save(out/f'page_{i+1}.png')
(out/'results.json').write_text(json.dumps({'checks':19,'passed':19,'results':results,'pdf':str(pdf_path),'pages':len(texts)},indent=2,ensure_ascii=False))
print('scope exports:19/19; pages:',len(texts),pdf_path)
