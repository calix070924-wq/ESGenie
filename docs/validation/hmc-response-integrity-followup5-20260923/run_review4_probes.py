"""4차 검토의 조합 probe를 수정 후 코드로 그대로 실행한다.

검토자 스크립트(outputs/reviews/hmc-integrity-fourth-review-20260921/probe_combinations.py)는
**정상 동작을 기대하는 검사**라 수정 전에는 13건 중 7건이 실패했다. 여기서는 원문을
고치지 않고 출력 폴더만 이 회차 폴더로 바꿔 실행한다 — 검토자의 수정 전 기록
(`combination_results.json`)을 덮지 않는다.

  venv/bin/python docs/validation/hmc-response-integrity-followup5-20260923/run_review4_probes.py
"""
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROBE = Path('/Users/heojeongmin/Documents/Claude/Projects/ESGenie'
             '/outputs/reviews/hmc-integrity-fourth-review-20260921/probe_combinations.py')

source = PROBE.read_text().replace(
    "OUT = Path(__file__).resolve().parent", f"OUT = Path({str(HERE)!r})")
assert "OUT = Path(" + repr(str(HERE)) in source, "출력 폴더 치환 실패 — 검토자 기록을 덮어쓸 위험"
os.environ.setdefault('ESGENIE_FORCE_MOCK', '1')
exec(compile(source, str(PROBE), 'exec'), {})      # noqa: S102 — 검토자 원문 그대로 실행
