"""최종 커밋의 RVS/RV 저장 응답 주입 재현. 원본 캐시를 매 실행 별도 복사한다."""
from pathlib import Path
import shutil,subprocess,sys
w=Path('/private/tmp/ESGenie-numeric-scope-output-20261005')
root=Path('/Users/heojeongmin/Documents/Claude/Projects/ESGenie')
target=Path(sys.argv[1]).resolve()
prior=root/'output/reviews/pr71_20261005/followup_fix_validation/02_final_0b770df/caches/LIVE_61b6167'
tape=root/'output/validation/numeric_scope_output_20261005/runs/R2_fix_4faabb0_live/followup/raw_upstage'
tools=w/'output/validation/numeric_scope_output_20261005/tools'
for run,variant in [('RVS','site'),('RV','ad')]:
    assert not (target/'runs'/run).exists(), '재현은 새 출력 폴더를 사용한다.'
    shutil.copytree(prior,target/'caches'/run)
    cmd=[sys.executable,str(tools/'inject_relation_variants.py'),'--variant-set',variant,'--saved-sections',str(prior/'llm'),'--saved-summary',str(prior/'llm/8e1d99f1805ef421d1ad9ab8f1f93519623d0f2a4237497a4c250dd96381dddc.json'),'core','--stage','followup','--export','--replay-upstage',str(tape),'--run-id',run+'_sixth_final','--code-path',str(w),'--env-file',str(root/'.env'),'--cache-dir',str(target/'caches'/run),'--run-dir',str(target/'runs'/run),'--pack-dir',str(root/'output/pdf/한울정밀_촬영세트_BM개편_20260928')]
    (target/'runs').mkdir(parents=True,exist_ok=True)
    with (target/'runs'/f'{run}_console.txt').open('w') as log: subprocess.run(cmd,cwd=w,stdout=log,stderr=subprocess.STDOUT,check=True)
