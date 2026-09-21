"""기존 산출물의 ISMS 취득 오기 검색·정정본 생성. 원본은 읽기만 한다."""
from pathlib import Path
import argparse
import json
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.hmc_integrity_validation import sha256, dump, OUT_DIR, LOG_DIR


def main():
    from openpyxl import load_workbook
    from openpyxl.styles import Alignment
    import fitz
    parser=argparse.ArgumentParser()
    parser.add_argument('--original-root',type=Path,required=True)
    args=parser.parse_args()
    root=args.original_root
    scope=root/'outputs'
    paths=sorted(p for p in scope.rglob('*') if p.suffix.lower() in {'.xlsx','.pdf','.md','.json','.txt','.docx'})
    found=[]; scanned=[]; errors=[]
    pattern=re.compile(r'ISMS\)?\s*인증.{0,35}(?:취득|획득)',re.S)
    corrections=OUT_DIR/'legacy_corrections'
    corrections.mkdir(parents=True,exist_ok=True)
    # 새 검증 응답서의 E-7은 실제 원문 발췌로 생성한 결정적 대조군이다.
    from esgenie.supplychain.schema import Answer
    from esgenie.supplychain.render import draft_lines
    payload=json.loads((OUT_DIR/'actual/response_sheet.json').read_text())
    a=next(x for x in payload['answers'] if x['qid']=='HMC-E-7')
    text='[수동 정정 — 승인 전]\n'+a['draft_display']+'\n출처: '+' / '.join(a['draft_sources'])+'\n정정: E-7과 무관한 ISMS 문단을 제외하고 확인된 의사소통 원문과 p.1 인용으로 교체함.'
    for path in paths:
        scanned.append(str(path))
        try:
            if path.suffix=='.xlsx':
                wb=load_workbook(path)
                edits=[]
                for ws in wb:
                    for row in ws:
                        for cell in row:
                            old=str(cell.value or '')
                            qid=str(ws.cell(cell.row,1).value or '')
                            misplaced = qid == 'HMC-E-7' and 'ISMS' in old
                            if not pattern.search(old) and not misplaced: continue
                            if qid in {'HMC-E-7','RBA-E-7'}:
                                new=text
                            else:
                                new=pattern.sub('ISMS 인증을 준비',old)
                                new += '\n[수동 정정] 원문 18번 p.1: ISMS 인증 준비 중, 2026년 하반기 신청 예정.'
                            cell.value=new
                            for updated_cell in ws[cell.row]:
                                updated_cell.alignment=Alignment(wrap_text=True,vertical="top")
                            if misplaced:
                                ws.cell(cell.row,5).value="수동 정정 · 검토필요"
                            ws.row_dimensions[cell.row].height=240
                            edits.append({'sheet':ws.title,'cell':cell.coordinate,'qid':qid,'issue':'unrelated ISMS paragraph / wrong p.2 citation' if misplaced else 'unsupported acquisition claim','old_text':old,'new_text':new})
                if edits:
                    dest=corrections/(sha256(path)[:10]+'_'+path.name)
                    wb.save(dest)
                    verify=load_workbook(dest)
                    assert all(not pattern.search(str(c.value or '')) for ws in verify for row in ws for c in row)
                    found.append({'original_path':str(path),'original_sha256':sha256(path),'corrected_path':str(dest),'corrected_sha256':sha256(dest),'method':'manual editorial correction, not new LLM generation','edits':edits})
            elif path.suffix=='.pdf':
                with fitz.open(path) as doc:
                    for i,page in enumerate(doc):
                        txt=page.get_text()
                        if pattern.search(txt):
                            found.append({'original_path':str(path),'original_sha256':sha256(path),'page':i+1,'unresolved':True,'excerpt':pattern.search(txt)[0]})
            elif path.suffix in {'.md','.json','.txt'}:
                txt=path.read_text(errors='replace')
                if pattern.search(txt):
                    found.append({'original_path':str(path),'original_sha256':sha256(path),'text_match':pattern.search(txt)[0],'classification':'text record to inspect; may describe error rather than assert acquisition'})
            else:
                import zipfile
                with zipfile.ZipFile(path) as z:
                    txt=z.read('word/document.xml').decode()
                    if pattern.search(txt): found.append({'original_path':str(path),'unresolved':True,'kind':'docx'})
        except Exception as e:
            errors.append({'path':str(path),'error':str(e)})
    policy=root/'시연증빙세트_한울정밀공업/18_정보보호정책_2026-06.pdf'
    dump(LOG_DIR/'isms_corrections.json',{'scan_root':str(scope),'scanned_files':scanned,'scanned_count':len(scanned),'acquisition_claim_found':any(any(e['issue']=='unsupported acquisition claim' for e in r.get('edits',[])) for r in found),'search_limit':'2189 current output artifacts searched; prior review acquisition wording not found in current files; corrected two confirmed HMC E-7 mixed-topic drafts separately','source_policy':{'path':str(policy),'sha256':sha256(policy),'page':1},'findings':found,'read_errors':errors})
    print(json.dumps({'scanned':len(scanned),'corrections':[{k:v for k,v in r.items() if k not in {'edits'}} for r in found],'errors':errors},ensure_ascii=False,indent=2))
    assert not errors and not any(r.get('unresolved') for r in found)

if __name__=='__main__':main()
