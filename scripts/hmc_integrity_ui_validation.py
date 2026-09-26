"""새 출력과 동일한 ResponseSheet를 production Streamlit 렌더러로 AppTest 확인."""
import json
import sys
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from streamlit.testing.v1 import AppTest
from esgenie.supplychain.schema import Answer, ResponseSheet
from esgenie.ssot.audit_trace import EvidenceLink
from scripts.hmc_integrity_validation import OUT_DIR, LOG_DIR, REQUIRED, dump

payload=json.loads((OUT_DIR/'controlled/response_sheet.json').read_text())
answers=[]
for a in payload['answers']:
    kwargs={f.name:a[f.name] for f in fields(Answer) if f.name in a}
    for key in ('evidence_links','reference_links'):
        kwargs[key]=[EvidenceLink(**v) for v in kwargs.get(key,[])]
    answers.append(Answer(**kwargs))
sheet=ResponseSheet(payload['framework_key'],payload['framework_label'],payload['corp_name'],answers)
# 렌더 소비 계약만 검사한다. 저장 답변을 재사용하는 제품 경로를 추가하지 않는다.
sys.modules.pop('esgenie.ui.tabs',None)
app=AppTest.from_string('''
import streamlit as st
from types import SimpleNamespace
from unittest.mock import patch
from esgenie.ui.tabs import _render_responder_workspace
with patch('esgenie.ui.tabs._get_cached_response_sheet', return_value=st.session_state['validated_sheet']):
    _render_responder_workspace(SimpleNamespace(v15_trace=True), '', default_key='hmc')
''',default_timeout=60)
app.session_state['validated_sheet']=sheet
app.run()
assert not app.exception, str(app.exception)
texts='\n'.join(str(block.value) for kind in ('markdown','caption','text','info','warning') for block in getattr(app,kind))
frames=[x.value.to_dict(orient='records') for x in app.dataframe]
flat=texts+'\n'+json.dumps(frames,ensure_ascii=False)
checks={s:s in flat for s in ('0.873988','88.397','29.3','10.6','2026-04-25','범위 확인','92','p.1')}
# 본문 화면의 인용과 검토사항, 체크리스트 및 4분할 집계를 모두 실제 소비한다.
checks['metrics']=len(app.metric)>=5
renewable=next(a for a in sheet.answers if a.qid==REQUIRED['renewable'])
checks['checklist']=any(row.get('문항')==renewable.question_text and row.get('할 일')=='범위 확인·보완' and '설비 동일성' in row.get('안내','') for f in frames for row in f)
for key,qid in REQUIRED.items():
    a=next(a for a in sheet.answers if a.qid==qid)
    checks['row:'+qid]=any(row.get('문항')==a.question_text and row.get('답변')==a.display_value and row.get('신뢰')==a.badge for f in frames for row in f)
dump(LOG_DIR/'ui_apptest.json',{'checks':checks,'metrics':[{'label':m.label,'value':m.value} for m in app.metric],'dataframes':frames,'text':texts,'exception_count':len(app.exception)})
print(json.dumps(checks,ensure_ascii=False,indent=2))
assert all(checks.values())
