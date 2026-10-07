"""실행 코드·원본의 전후 동일성 및 산출물 해시를 기록하고 검증한다."""
from pathlib import Path
import hashlib,json,subprocess,xml.etree.ElementTree as ET
out=Path(__file__).resolve().parents[1]
w=Path('/private/tmp/ESGenie-numeric-scope-output-20261005')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
before=json.loads((out/'code_before.json').read_text())
after={'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=w,text=True).strip(),'status':subprocess.check_output(['git','status','--porcelain'],cwd=w,text=True).strip(),'sha256':{p:sha(w/p) for p in before['sha256']}}
assert before==after
(out/'code_after.json').write_text(json.dumps(after,indent=2,ensure_ascii=False))
sources=json.loads((out/'source_before.json').read_text()); current={p:sha(Path(p)) for p in sources};assert sources==current
(out/'source_after.json').write_text(json.dumps(current,indent=2,ensure_ascii=False))
manifest={'product_commit':after['commit'],'code_manifest_equal':True,'source_manifest_equal':True,'code_files':len(after['sha256']),'source_files':len(sources),'tests':{},'runs':{},'outputs_sha256':{},'verification_sha256':{}}
for p in (out/'tests').glob('*.xml'):
    suites=ET.parse(p).getroot().findall('testsuite')
    counts={k:sum(int(s.get(k,0)) for s in suites) for k in ['tests','failures','errors','skipped']}
    counts['passed']=counts['tests']-counts['failures']-counts['errors']-counts['skipped'];manifest['tests'][p.name]=counts
for name in ['RVS','RV']:
    run=out/'runs'/name/'followup';env=json.loads((run/'environment.json').read_text());stats=json.loads((run/'run_stats.json').read_text())
    assert env['commit']==after['commit'] and not env['dirty']
    manifest['runs'][name]={'commit':env['commit'],'dirty':env['dirty'],'mode':env['mode'],'llm':stats['llm'],'upstage_tape_replays':len(stats['upstage_tape']),'note':'LLM live_calls counts blocked cache misses, not successful provider calls. Saved E/S/G and summary responses injected.'}
    for p in (run/'exports').rglob('*'):
        if p.is_file():manifest['outputs_sha256'][str(p.relative_to(out))]=sha(p)
for p in (out/'edge_output').glob('*'):
    if p.is_file():manifest['outputs_sha256'][str(p.relative_to(out))]=sha(p)
for part in ['tools','tests','checks','render']:
    for p in (out/part).rglob('*'):
        if p.is_file():manifest['verification_sha256'][str(p.relative_to(out))]=sha(p)
(out/'manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False))
print('code',manifest['code_files'],'sources',manifest['source_files'],'outputs',len(manifest['outputs_sha256']),'verification',len(manifest['verification_sha256']))
print(manifest['tests'])
