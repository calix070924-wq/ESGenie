"""Independent diagnostic suite; keep all generated state outside the project."""
import json
import os
from pathlib import Path
import socket
import sys

import pytest

os.environ['ESGENIE_FORCE_MOCK'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
_results = []


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setenv('ESGENIE_LLM_CACHE_DIR', str(tmp_path / 'llm'))
    monkeypatch.setenv('ESGENIE_OCR_CACHE_DIR', str(tmp_path / 'ocr'))
    monkeypatch.setenv('ESGENIE_LLM_CACHE', '1')
    monkeypatch.setenv('ESGENIE_LLM_CACHE_REFRESH', '0')
    attempts = []

    def no_network(*args, **kwargs):
        attempts.append(True)
        raise AssertionError('This diagnostic suite must not access the network')

    monkeypatch.setattr(socket.socket, 'connect', no_network)
    monkeypatch.setattr(socket.socket, 'connect_ex', no_network)
    monkeypatch.setattr(socket, 'create_connection', no_network)
    yield
    assert not attempts, 'An unexpected network operation was attempted'


@pytest.fixture
def observe(record_property):
    def record(**values):
        record_property('observed', json.dumps(values, ensure_ascii=False, default=str))
    return record


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == 'call' or (report.failed and report.when != 'call'):
        _results.append({
            'test': report.nodeid,
            'phase': report.when,
            'outcome': report.outcome,
            'observations': [json.loads(v) for k, v in report.user_properties if k == 'observed'],
            'failure': report.longreprtext if report.failed else None,
            'seconds': report.duration,
        })


def pytest_sessionfinish(session, exitstatus):
    destination = Path(os.environ.get('ESGENIE_AUDIT_RESULT',
        str(Path(__file__).resolve().parents[2] / 'outputs/audit_integrity_results.json')))
    destination.write_text(json.dumps({
        'exitstatus': int(exitstatus), 'network': 'blocked', 'tests': _results,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
