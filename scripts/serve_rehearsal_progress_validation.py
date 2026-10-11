"""Isolated Chrome fault-injection server; no real AI/OCR service calls."""
import os
from pathlib import Path
import time
from types import SimpleNamespace

import uvicorn

from esgenie import llm, llm_cache
from esgenie.config import SETTINGS
from esgenie.progress import emit
from esgenie.web.app import create_app
from scripts.serve_ui_validation import fixture_runner


def progress_fixture(project, directory):
    documents = [d for d in project['documents'] if d.get('included', True) and not d.get('error')]
    emit('targets', total_files=len(documents))
    emit('stage', phase='reading', stage='회사 작성 답변과 증빙 자료를 읽고 있어요')
    fail = '실패' in project['company_name'] and bool(project.get('result'))
    missed_poll = '폴링 간격' in project['company_name']
    for document in documents:
        emit('file_started', file_id=document['id'], file_name=document['name'], file_role=document['role'])
        calls = 0
        def request(**kwargs):
            nonlocal calls
            calls += 1
            time.sleep((0.1 if calls == 1 else 5) if missed_poll else 2)
            if calls == 1 or fail:
                raise TimeoutError('injected timeout, no external request')
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='fixture response'))])
        client = llm.LLMClient()
        client._openai_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=request)))
        client.complete('progress fixture', document['name'])
        emit('file_finished', file_id=document['id'], file_name=document['name'], file_role=document['role'])
    emit('stage', phase='analyzing', stage='읽은 자료를 분석하고 있어요')
    time.sleep(2)
    emit('stage', phase='organizing', stage='문항별 답변과 근거를 정리하고 있어요')
    time.sleep(2)
    return fixture_runner(project, directory)


if __name__ == '__main__':
    SETTINGS.strict_llm = True
    llm_cache.lookup = lambda *a, **k: None
    llm_cache.store = lambda *a, **k: None
    app = create_app(data_root=Path(os.environ['ESGENIE_WEB_DATA_DIR']), runner=progress_fixture, available=lambda: True)
    uvicorn.run(app, host='127.0.0.1', port=int(os.getenv('ESGENIE_PROGRESS_TEST_PORT', '8784')))
