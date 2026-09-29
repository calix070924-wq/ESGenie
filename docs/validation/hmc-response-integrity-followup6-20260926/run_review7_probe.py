"""7차 검토의 probe를 수정 후 코드로 그대로 실행한다.

검토자 스크립트(outputs/reviews/hmc-integrity-seventh-review-20260926/probe_denial_forms.py)는
**정상 동작을 기대하는 검사**라 수정 전에는 10건 중 2건(`취득하지 않았으며/못했으며`)이 실패했다.
원문을 고치지 않고 대상 작업 폴더와 출력 폴더만 바꿔 실행한다 — 검토자의 수정 전 기록
(`denial_forms_results.json`)을 덮지 않는다.

  python3 docs/validation/hmc-response-integrity-followup6-20260926/run_review7_probe.py
"""
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PROBE = Path('/Users/heojeongmin/Documents/Claude/Projects/ESGenie'
             '/outputs/reviews/hmc-integrity-seventh-review-20260926/probe_denial_forms.py')

source = (PROBE.read_text()
          .replace("Path('/private/tmp/esgenie-hmc-review-checkout-20260926')", f"Path({str(ROOT)!r})")
          .replace("OUT = Path(__file__).resolve().parent", f"OUT = Path({str(HERE)!r})"))
assert "OUT = Path(" + repr(str(HERE)) in source, "출력 폴더 치환 실패 — 검토자 기록을 덮어쓸 위험"
assert "WT = Path(" + repr(str(ROOT)) in source, "대상 폴더 치환 실패 — 검토용 사본을 검사할 위험"
os.environ.setdefault('ESGENIE_FORCE_MOCK', '1')
exec(compile(source, str(PROBE), 'exec'), {})      # noqa: S102 — 검토자 원문 그대로 실행
