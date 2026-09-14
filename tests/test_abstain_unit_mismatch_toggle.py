"""Legacy A/B toggles remain readable but cannot erase actual D1 non-verification."""
import json
import os
import subprocess
import sys
import pytest
from esgenie.layer3_detect import score_d1_numeric
from esgenie.ssot.detector_5axis import detect_d1_numeric
from tests.d1_fixtures import make_evidence


def test_default_config_and_real_default_execution():
    env = dict(os.environ)
    env.pop('ABSTAIN_ENABLED', None)
    env.pop('ABSTAIN_UNIT_MISMATCH', None)
    out = subprocess.check_output([sys.executable, '-c', '''
import json
from esgenie.config import ABSTAIN_ENABLED, ABSTAIN_UNIT_MISMATCH
from esgenie.layer3_detect import score_d1_numeric
print(json.dumps([ABSTAIN_ENABLED, ABSTAIN_UNIT_MISMATCH,
    score_d1_numeric("용수 사용량은 100톤이다.", None).abstain]))
'''], env=env, text=True)
    assert json.loads(out) == [True, True, True]

@pytest.mark.parametrize('enabled,unit_toggle', [(False,False),(True,False),(True,True),(False,True)])
@pytest.mark.parametrize('sentence,code,reason', [
    ('용수 사용량 100톤', 'E-5-1', 'no_evidence'),
    ('재생에너지 비율 31%p', 'E-4-2', 'unit_mismatch'),
    ('재생에너지 비율 31%', 'E-4-2', None),
])
@pytest.mark.parametrize('ssot', [False,True])
def test_toggle_matrix_preserves_coverage(monkeypatch, enabled, unit_toggle, sentence, code, reason, ssot):
    for module in ('esgenie.layer3_detect', 'esgenie.ssot.detector_5axis'):
        monkeypatch.setattr(f'{module}.ABSTAIN_ENABLED', enabled)
    monkeypatch.setattr('esgenie.layer3_detect.ABSTAIN_UNIT_MISMATCH', unit_toggle)
    _, graph, _ = make_evidence()
    axis = detect_d1_numeric(sentence, code, graph) if ssot else score_d1_numeric(sentence, graph)
    assert axis.abstain == bool(reason) and axis.abstain_reason == reason
    assert axis.evaluation['unverified_claims'] == int(bool(reason))
