"""저장 응답 주입의 제품 출력·변형 검사와 PDF 페이지 렌더링."""
from pathlib import Path
import subprocess,sys,fitz,json
out=Path(__file__).resolve().parents[1];w=Path('/private/tmp/ESGenie-numeric-scope-output-20261005');tools=w/'output/validation/numeric_scope_output_20261005/tools'
for run in ['RVS','RV']:
    target=out/'runs'/run/'followup'
    for checker,suffix in [('check_outputs.py',''),('check_relation_variants.py','_variants')]:
        with (out/'checks'/f'{run}{suffix}.log').open('w') as log:
            subprocess.run([sys.executable,str(tools/checker),str(target),'--json',str(out/'checks'/f'{run}{suffix}.json')],cwd=w,stdout=log,stderr=subprocess.STDOUT,check=True)
    pdf=next((target/'exports').rglob('ESG보고서*.pdf'))
    with fitz.open(pdf) as doc:
        pages=[0,3,8] if run=='RVS' else [0,3,9]
        for i in pages:doc[i].get_pixmap(matrix=fitz.Matrix(1,1)).save(out/'render'/f'{run}_page_{i+1}.png')
    r=json.loads((out/'checks'/f'{run}.json').read_text());v=json.loads((out/'checks'/f'{run}_variants.json').read_text())
    print(run,r['passed'],r['total']);print((out/'checks'/f'{run}_variants.log').read_text().splitlines()[-1])
