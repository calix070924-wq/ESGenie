"""Execution isolation and fault injection; no paid API/OCR requests."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from esgenie.progress import CompanyFiles, EvidenceFiles, emit, observe
from esgenie.web.progress_state import apply_event
from esgenie.web.store import ProjectStore
from esgenie import llm, llm_cache, pipeline
from esgenie.config import SETTINGS


def result():
    return {'sheet': {'answers': []}, 'answers': [], 'limitations': [], 'mode': 'live'}


def create(store):
    p = store.create({'company_name': '검증 회사'})
    p['documents'] = [{'id': 'a', 'name': 'a.pdf', 'role': 'evidence'}]
    store.save(p)
    return p


def drain(store):
    store.worker.submit(lambda: None).result(timeout=5)


def test_sequential_file_boundaries_do_not_count_chunks_and_preserve_pipeline_extraction(monkeypatch):
    # Real pipeline collector, only external extraction/validation replaced.
    from esgenie.ssot import ocr_router, ocr_table_gate, ocr_consistency
    monkeypatch.setattr(ocr_router, 'route_document', lambda path: path)
    monkeypatch.setattr(ocr_router, 'extract_document', lambda path, decision: SimpleNamespace(source_file='', path=path))
    monkeypatch.setattr(ocr_table_gate, 'apply_table_gate', lambda *a, **k: None)
    monkeypatch.setattr(ocr_consistency, 'validate_consistency', lambda *a: None)
    monkeypatch.setattr(ocr_router, 'tag_rba_codes', lambda *a: None)
    files = {'a.pdf': 'a', 'b.pdf': 'b'}
    documents = {name: {'id': path} for name, path in files.items()}
    baseline = pipeline._collect_ocr_extractions(files)
    events = []
    with observe('run', events.append):
        actual = pipeline._collect_ocr_extractions(EvidenceFiles(files, documents))
    assert [vars(e) for e in baseline] == [vars(e) for e in actual]
    assert [(e['kind'], e.get('file_name')) for e in events] == [
        ('file_started', 'a.pdf'), ('file_finished', 'a.pdf'),
        ('file_started', 'b.pdf'), ('file_finished', 'b.pdf'), ('stage', None)]
    assert events[-1]['phase'] == 'analyzing'


@pytest.mark.parametrize('texts, expected_value, expected_period, status, candidates, rejected', [
    (['2026년 재활용률 20%', '2026년 재활용률 20%'], 20, 2026, 'reported', [(20, 2026), (20, 2026)], []),
    (['2026년 재활용률 20%', '2026년 재활용률 30%'], None, None, 'ambiguous', [(20, 2026), (30, 2026)], []),
    (['2025년 재활용률 20%', '2026년 재활용률 20%'], None, None, 'ambiguous', [(20, 2025), (20, 2026)], []),
    (['2026년 재활용률 20%', '2027년 재활용률 60% 목표\n2026년 재활용률 120%'],
     20, 2026, 'reported', [(20, 2026)], ['target_not_actual', 'invalid_rate']),
    (['2027년 재활용률 60% 목표\n2026년 재활용률 120%', '2026년 재활용률 20%인가?'],
     None, None, None, [], ['target_not_actual', 'invalid_rate', 'question_not_answer']),
])
def test_company_parser_cross_file_claims_are_identical(tmp_path, texts, expected_value, expected_period,
                                                        status, candidates, rejected):
    from esgenie.supplychain import parse_saq_claims
    import fitz
    paths = []
    font = Path(__file__).resolve().parents[1] / 'esgenie/assets/fonts/NotoSansKR-Regular.ttf'
    for i, text in enumerate(texts):
        path = tmp_path / f'answer-{i}.pdf'
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_text((40, 60), text, fontname='korean', fontfile=str(font), fontsize=12)
            doc.subset_fonts()
            doc.save(path, deflate=True)
        with fitz.open(path) as doc:
            assert text in doc[0].get_text().replace('\xa0', ' ')  # Exercise real PDF extraction with readable Korean.
        paths.append(str(path))
    docs = {p: {'id': str(i), 'name': Path(p).name} for i, p in enumerate(paths)}
    baseline = parse_saq_claims(paths)
    events = []
    with observe('run', events.append):
        actual = parse_saq_claims(CompanyFiles(paths, docs))
    assert actual == baseline and actual.diagnostics == baseline.diagnostics
    assert [d['reason'] for d in actual.diagnostics] == rejected
    assert all(d['source'].startswith('saq:answer-') and '재활용률' in d['raw'] for d in actual.diagnostics)
    if status is None:
        assert not actual
    else:
        assert set(actual) == {'E-6-2'}  # Empty ClaimSets cannot pass the equality check.
        claim = actual['E-6-2']
        assert (claim.value, claim.period, claim.status, claim.unit) == (expected_value, expected_period, status, '%')
        assert [(c['value'], c['period']) for c in claim.candidates] == candidates
        assert [c['source'] for c in claim.candidates] == [f'saq:answer-{i}.pdf' for i in range(len(candidates))]
        assert all(c['page'] == 0 and c['position'] >= 0 and '재활용률' in c['raw'] for c in claim.candidates)
        assert claim.source == ' / '.join(dict.fromkeys(c['source'] for c in claim.candidates))
        assert claim.diagnostics == (['여러 실적 주장값/연도가 상충하여 확정 불가'] if status == 'ambiguous' else [])
    assert len([e for e in events if e['kind'] == 'file_finished']) == len(paths)
    assert all(e['file_role'] == 'company_answer' for e in events)


def test_partial_file_is_not_finished_on_exception():
    events = []
    with observe('run', events.append):
        with pytest.raises(ValueError):
            for _ in EvidenceFiles({'a.pdf': 'a'}, {'a.pdf': {'id': 'a'}}).items():
                raise ValueError('read failure')
    assert [e['kind'] for e in events] == ['file_started']


def test_multiple_active_files_and_requests_stale_events_and_terminal_guard():
    job = {'run_id': 'new', 'status': 'running'}
    events = []
    with observe('new', events.append):
        for fid in ('a', 'b'):
            emit('file_started', file_id=fid, file_name=fid+'.pdf', file_role='evidence')
            emit('request_started', request_id=fid, provider='openai', attempt=1, max_attempts=3)
        emit('file_finished', file_id='a')
        emit('file_finished', file_id='a')
        emit('request_retry', request_id='b', provider='openai', attempt=2, max_attempts=3, delay_seconds=2, reason='잠시 대기')
    for event in events:
        assert apply_event(job, event)
    assert job['completed_files'] == 1
    assert [f['id'] for f in job['active_files']] == ['b']
    assert len(job['requests']) == 2 and job['requests'][1]['state'] == 'retrying'
    before = deepcopy(job)
    assert not apply_event(job, events[0])
    assert not apply_event(job, {**events[-1], 'run_id': 'old', 'sequence': 100})
    assert job == before
    job['status'] = 'complete'
    assert not apply_event(job, {**events[-1], 'sequence': 100})


def test_observer_failure_does_not_affect_response_or_leak_to_another_thread():
    def broken(_):
        raise OSError('unavailable progress storage')
    with observe('run', broken):
        emit('stage', phase='reading', stage='자료 읽기')
        assert list(EvidenceFiles({'a.pdf': 'a'}, {'a.pdf': {'id': 'a'}}).items()) == [('a.pdf', 'a')]
    events = []
    with observe('parent', events.append):
        with ThreadPoolExecutor(1) as worker:
            worker.submit(emit, 'stage', phase='reading', stage='other worker').result()
        emit('stage', phase='reading', stage='parent')
    emit('stage', phase='reading', stage='outside')
    assert len(events) == 1 and events[0]['run_id'] == 'parent'


def test_store_live_updates_refresh_failure_retry_and_previous_result(tmp_path):
    started, release = Event(), Event()
    fail = [True]
    def runner(p, directory):
        emit('targets', total_files=2)
        emit('file_started', file_id='a', file_name='a.pdf', file_role='evidence')
        emit('request_retry', request_id='r', provider='openai', attempt=2, max_attempts=3, delay_seconds=2, reason='시간 초과')
        started.set()
        assert release.wait(5)
        if fail[0]:
            raise TimeoutError('SECRET prompt and key must never be persisted')
        emit('file_finished', file_id='a')
        return result()
    store = ProjectStore(tmp_path, runner)
    try:
        p = create(store)
        p.update(result=result(), result_revision=1)
        store.save(p)
        queued = store.start(p['id'])
        assert queued['job']['status'] == 'queued'
        assert started.wait(5)
        progress = store.read(p['id'])['job']
        assert progress['requests'][0]['attempt'] == 2
        assert json.loads((store.directory(p['id'])/'project.json').read_text())['job'] == progress
        release.set()
        drain(store)
        failed = store.read(p['id'])
        assert failed['job']['status'] == 'failed' and failed['job']['ended_at']
        assert not failed['job']['requests'] and not failed['job']['active_files']
        assert '초과' in failed['job']['error'] and 'SECRET' not in json.dumps(failed)
        assert failed['result'] == p['result']
        fail[0] = False
        second = store.start(p['id'])
        assert second['job']['run_id'] != queued['job']['run_id']
        assert second['job']['events'] == [] and second['job']['completed_files'] == 0
        drain(store)
        complete = store.read(p['id'])
        assert complete['job']['status'] == 'complete'
        # No invented completion for the second, unreported file.
        assert complete['job']['completed_files'] == 1
        store._progress(p['id'], {**progress['events'][0], 'sequence': 999})
        assert store.read(p['id']) == complete
    finally:
        release.set()
        store.close()


def test_queue_isolation_and_restart_clears_active_state(tmp_path):
    release, started = Event(), Event()
    def runner(p, directory):
        emit('file_started', file_id=p['id'], file_name=p['company_name'], file_role='evidence')
        started.set()
        assert release.wait(5)
        return result()
    store = ProjectStore(tmp_path, runner)
    try:
        a, b = create(store), create(store)
        store.start(a['id'])
        assert started.wait(5)
        store.start(b['id'])
        queued = store.read(b['id'])['job']
        assert queued['status'] == 'queued' and queued['active_files'] == []
        release.set()
        drain(store)
        assert store.read(a['id'])['job']['run_id'] != store.read(b['id'])['job']['run_id']
        p = store.read(a['id'])
        p['job'].update(status='running', requests=[{'id': 'old'}], active_files=[{'id': 'a'}])
        p['result'] = result()
        store.save(p)
    finally:
        release.set()
        store.close()
    restored = ProjectStore(tmp_path, runner)
    try:
        p = restored.read(a['id'])
        assert p['job']['status'] == 'failed' and p['job']['ended_at']
        assert p['job']['requests'] == [] and p['job']['active_files'] == []
        assert p['result'] == result()
        del p['job']
        restored.save(p)
        assert restored.read(a['id'])['job']['status'] == 'idle'
    finally:
        restored.close()


@pytest.mark.parametrize('provider', ['openai', 'anthropic'])
@pytest.mark.parametrize('status, successes, attempts', [(429, True, 2), (503, False, 3), (401, False, 1), (400, False, 1), (408, True, 2)])
def test_actual_llm_retry_path_events_match_calls(provider, status, successes, attempts, monkeypatch):
    class Error(Exception):
        status_code = status
    calls = []
    def request(**kwargs):
        calls.append(kwargs)
        if successes and len(calls) == 2:
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='unchanged'))],
                                   content=[SimpleNamespace(type='text', text='unchanged')])
        raise Error('SECRET sdk error')
    monkeypatch.setattr(SETTINGS, 'strict_llm', True)
    monkeypatch.setattr(llm.time, 'sleep', lambda _: None)
    monkeypatch.setattr(llm_cache, 'lookup', lambda *a, **k: None)
    monkeypatch.setattr(llm_cache, 'store', lambda *a, **k: None)
    client = llm.LLMClient()
    if provider == 'openai':
        client._openai_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=request)))
    else:
        client._anthropic_client = SimpleNamespace(messages=SimpleNamespace(create=request))
    events = []
    with observe('run', events.append):
        if successes:
            response = client.complete('system', 'user')
            assert response.content == 'unchanged' and not response.used_mock
        else:
            with pytest.raises(llm.LLMUnavailableError):
                client.complete('system', 'user')
    assert len(calls) == attempts
    assert [e['attempt'] for e in events if e['kind'] == 'request_started'] == list(range(1, attempts+1))
    assert [e['attempt'] for e in events if e['kind'] == 'request_retry'] == list(range(2, attempts+1))
    assert events[-1]['kind'] == 'request_finished'
    assert len({e['request_id'] for e in events}) == 1
    assert 'SECRET' not in json.dumps(events)


def test_observer_does_not_change_cached_response_or_cache_key(monkeypatch):
    client = llm.LLMClient()
    client._openai_client = SimpleNamespace()
    keys = []
    def cached(key, **kwargs):
        keys.append(key)
        return {'content': 'same cached answer', 'meta': {}}
    monkeypatch.setattr(llm_cache, 'lookup', cached)
    baseline = client.complete('system', 'user')
    events = []
    with observe('run', events.append):
        actual = client.complete('system', 'user')
    assert actual == baseline and keys[0] == keys[1] and events == []


def test_non_transient_quota_is_not_retried():
    class Error(Exception):
        status_code = 429
        body = {'error': {'code': 'insufficient_quota'}}
    assert not llm._is_retryable(Error())


def test_old_worker_result_cannot_replace_new_execution(tmp_path):
    started, release = Event(), Event()
    def runner(p, directory):
        started.set()
        assert release.wait(5)
        return result()
    store = ProjectStore(tmp_path, runner)
    try:
        p = create(store)
        store.start(p['id'])
        assert started.wait(5)
        newer = store.read(p['id'])
        newer['job'].update(run_id='replacement', status='running')
        store.save(newer)
        release.set()
        drain(store)
        assert store.read(p['id']) == newer
        assert newer['result'] is None
    finally:
        release.set()
        store.close()


def test_reusing_file_mapping_does_not_replay_extraction_events():
    files = EvidenceFiles({'a.pdf': 'a'}, {'a.pdf': {'id': 'a'}})
    events = []
    with observe('run', events.append):
        assert list(files.items()) == list(files.items()) == [('a.pdf', 'a')]
    assert [e['kind'] for e in events] == ['file_started', 'file_finished', 'stage']


@pytest.mark.parametrize('snapshots', [False, True])
def test_retry_reason_survives_the_next_attempt_and_is_scoped_to_request(snapshots):
    job = {'run_id': 'run', 'status': 'running'}
    def observer(event):
        if not snapshots:
            event = {k: v for k, v in event.items() if k != 'snapshot'}
        apply_event(job, event)
    with observe('run', observer):
        emit('request_started', request_id='r', provider='openai', attempt=1, max_attempts=3)
        emit('request_retry', request_id='r', provider='openai', attempt=2, max_attempts=3,
             delay_seconds=1, reason='시간 초과')
        emit('request_started', request_id='r', provider='openai', attempt=2, max_attempts=3)
        # A poll that entirely missed backoff still sees the actual retry and its cause.
        request = json.loads(json.dumps(job))['requests'][0]
        assert request['state'] == 'waiting' and request['attempt'] == 2
        assert request['last_retry']['reason'] == '시간 초과'
        assert request['last_retry']['attempt'] == 2 and request['last_retry']['delay_seconds'] == 1
        emit('request_retry', request_id='r', provider='openai', attempt=3, max_attempts=3,
             delay_seconds=2, reason='요청 한도')
        emit('request_started', request_id='r', provider='openai', attempt=3, max_attempts=3)
        assert job['requests'][0]['last_retry']['reason'] == '요청 한도'
        emit('request_finished', request_id='r')
        emit('request_started', request_id='next', provider='openai', attempt=1, max_attempts=3)
        assert job['requests'][0]['last_retry'] is None


def test_missed_file_completion_is_recovered_before_next_file_finishes(tmp_path, monkeypatch):
    started, release = Event(), Event()
    def runner(p, directory):
        emit('targets', total_files=2)
        emit('file_started', file_id='a', file_name='a.pdf', file_role='evidence')
        emit('file_finished', file_id='a')
        emit('file_started', file_id='b', file_name='b.pdf', file_role='company_answer')
        started.set()
        assert release.wait(5)
        emit('file_finished', file_id='b')
        return result()
    store = ProjectStore(tmp_path, runner)
    save = store.save
    failures = []
    def flaky_save(p):
        if p['job']['status'] == 'running' and p['job'].get('sequence') == 3 and not failures:
            failures.append(3)
            raise OSError('injected lost file completion')
        save(p)
    monkeypatch.setattr(store, 'save', flaky_save)
    try:
        p = create(store)
        store.start(p['id'])
        assert started.wait(5)
        job = store.read(p['id'])['job']
        assert failures == [3]
        assert job['completed_files'] == 1 and job['total_files'] == 2
        assert [f['id'] for f in job['active_files']] == ['b']
        assert [e['sequence'] for e in job['events']] == [1, 2, 3, 4]
        release.set()
        drain(store)
        job = store.read(p['id'])['job']
        assert job['status'] == 'complete' and job['completed_files'] == 2
    finally:
        release.set()
        store.close()


@pytest.mark.parametrize('fail_analysis', [False, True])
def test_terminal_save_uses_snapshot_even_when_all_progress_writes_fail(tmp_path, monkeypatch, fail_analysis):
    failures = []
    def runner(p, directory):
        emit('targets', total_files=2)
        for fid in ('a', 'b'):
            emit('file_started', file_id=fid, file_name=fid+'.pdf', file_role='evidence')
            emit('file_finished', file_id=fid)
        for _ in range(110):
            emit('stage', phase='organizing', stage='정리 중', secret='not part of public contract')
        if fail_analysis:
            raise TimeoutError('injected final failure')
        return result()
    store = ProjectStore(tmp_path, runner)
    save = store.save
    def unavailable_progress(p):
        if p['job']['status'] == 'running' and p['job'].get('sequence', 0) > 0:
            failures.append(p['job']['sequence'])
            raise OSError('progress unavailable')
        save(p)
    monkeypatch.setattr(store, 'save', unavailable_progress)
    try:
        p = create(store)
        p['result'] = result()
        store.save(p)
        store.start(p['id'])
        drain(store)
        job = store.read(p['id'])['job']
        assert len(failures) == 115
        assert job['status'] == ('failed' if fail_analysis else 'complete')
        assert job['total_files'] == job['completed_files'] == 2
        assert job['completed_file_ids'] == ['a', 'b']
        assert job['active_files'] == job['requests'] == []
        assert len(job['events']) == 100 and job['sequence'] == 115
        assert 'snapshot' not in json.dumps(job) and 'secret' not in json.dumps(job)
        if fail_analysis:
            assert store.read(p['id'])['result'] == p['result']
        else:
            assert store.read(p['id'])['result'] is not None
    finally:
        store.close()
