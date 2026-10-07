"""PR #68 5차 검토(R8-3) 판정 충돌 검사 실행기 — 4차 실행기(r8_followup_support)에 검토 사유 검사를 더한다.

같은 폴더의 r8_followup_support.py를 파일 경로로 불러온다(수정 전 코드 폴더에서 재현할 때도 이 두 파일만
복사해 쓰기 위해서다). 외부 API를 호출하지 않는다.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("r8_followup_support", Path(__file__).with_name("r8_followup_support.py"))
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)

FIXTURE = Path(__file__).parent / "fixtures" / "ocr_numeric_review_r5" / "pair_cases.json"
load_cases, run_path, observe = _base.load_cases, _base.run_path, _base.observe


def violations(case, obs):
    out = _base.violations(case, obs)
    reasons = set(obs["review_reasons"])
    for r in case.get("review_includes", ()):
        if r not in reasons:
            out.append(f"review lacks {r}")
    for r in case.get("review_excludes", ()):
        if r in reasons:
            out.append(f"review has {r}")
    return out
