"""Offline, deterministic D1 regression snapshot (no fresh generalization claim)."""
import argparse
import importlib.util
import json
import os
import platform
import socket
from dataclasses import asdict
from pathlib import Path

os.environ['ESGENIE_FORCE_MOCK'] = '1'
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
def blocked(*args, **kwargs):
    raise AssertionError('External network forbidden during D1 validation')
socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.create_connection = blocked

import esgenie.layer3_detect as detector
from esgenie.dart_client import load_report
from esgenie.layer0_evidence_graph import build_evidence_graph
from esgenie.config import ABSTAIN_ENABLED, ABSTAIN_UNIT_MISMATCH, D1_THRESHOLD, D_WEIGHTS

ROOT = Path(__file__).resolve().parents[1]
def load_test(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tests' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def snapshot():
    regression = load_test('test_d1_validation_regression')
    _, graph, _ = regression.make_evidence()
    result = {'environment': {'python': platform.python_version(), 'platform': platform.platform(),
              'module': detector.__file__, 'external_network': 'blocked', 'llm': 'not called',
              'ABSTAIN_ENABLED': ABSTAIN_ENABLED, 'ABSTAIN_UNIT_MISMATCH': ABSTAIN_UNIT_MISMATCH,
              'D1_THRESHOLD': D1_THRESHOLD, 'D_WEIGHTS': D_WEIGHTS}, 'cases': []}
    for name, sentence, *_ in regression.CASES:
        axis = detector.score_d1_numeric(sentence, graph)
        result['cases'].append({'name': name, 'sentence': sentence, 'axis': axis.to_dict(),
                               'claims': [asdict(c) for c in detector.extract_numeric_claims(sentence)]})
    graphs = {}
    rows = []
    for case in json.loads((ROOT / 'data/benchmark_v2/batch_c_truth_holdout.json').read_text())['cases']:
        ticker = case['ticker']
        if ticker not in graphs:
            graphs[ticker] = build_evidence_graph(load_report(ticker))
        axis = detector.score_d1_numeric(case['sentence'], graphs[ticker])
        rows.append({'id': case['id'], 'sentence': case['sentence'], 'label': case['label'],
                     'prediction': axis.score >= .5, 'axis': axis.to_dict()})
    result['existing_truth_regression'] = rows
    result['confusion'] = {label: sum((r['label'] == 'greenwash') == actual and r['prediction'] == pred for r in rows)
                          for label, actual, pred in [('TP',True,True),('FP',False,True),('FN',True,False),('TN',False,False)]}
    live = load_test('test_d1_topic_match')
    result['stored_live_sentence'] = {'sentence': live.LIVE_SENTENCE,
        'axis': detector.score_d1_numeric(live.LIVE_SENTENCE, live._LedgerGraph()).to_dict()}
    return result

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = snapshot()
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    print(result['environment'])
    print(result['confusion'])
    print([(c['name'], c['axis']['score'], c['axis']['abstain']) for c in result['cases']])
