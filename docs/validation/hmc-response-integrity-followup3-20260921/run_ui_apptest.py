"""followup2 산출물로 UI AppTest 검증을 재실행한다.

scripts/hmc_integrity_ui_validation.py는 직전 회차(followup-20260921) 폴더를
상수로 읽는다. 그 기록을 덮어쓰지 않으려면 모듈 상수만 이 회차 폴더로 바꿔
같은 스크립트를 그대로 실행한다 — 검증 논리는 손대지 않는다.

  venv/bin/python docs/validation/hmc-response-integrity-followup3-20260921/run_ui_apptest.py
"""
import runpy
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

import scripts.hmc_integrity_validation as base

base.OUT_DIR = REPO / "outputs/validation/hmc-response-integrity-followup3-20260921"
base.LOG_DIR = REPO / "docs/validation/hmc-response-integrity-followup3-20260921"
runpy.run_path(str(REPO / "scripts/hmc_integrity_ui_validation.py"), run_name="__main__")
