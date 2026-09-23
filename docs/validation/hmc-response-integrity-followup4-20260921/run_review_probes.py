"""3차 검토의 추가 probe를 수정 후 코드로 그대로 실행한다.

검토자 스크립트(outputs/reviews/hmc-integrity-third-review-20260921/probe_remaining.py)는
**정상 동작을 기대하는 검사** 14건이다. 3차 수정으로 전부 통과했고, 이번 4차 수정이
그 정상 기준을 깨지 않았음을 다시 확인한다. 원문은 고치지 않고 출력 폴더만 이 회차
폴더로 바꿔 실행한다 — 검토자의 수정 전 기록을 덮지 않는다.

  venv/bin/python docs/validation/hmc-response-integrity-followup4-20260921/run_review_probes.py
"""
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROBE = Path('/Users/heojeongmin/Documents/Claude/Projects/ESGenie'
             '/outputs/reviews/hmc-integrity-third-review-20260921/probe_remaining.py')

source = PROBE.read_text().replace(
    "OUT = Path(__file__).resolve().parent", f"OUT = Path({str(HERE)!r})")
assert "OUT = Path(" + repr(str(HERE)) in source, "출력 폴더 치환 실패 — 검토자 기록을 덮어쓸 위험"
os.environ.setdefault('ESGENIE_FORCE_MOCK', '1')
exec(compile(source, str(PROBE), 'exec'), {})      # noqa: S102 — 검토자 원문 그대로 실행
