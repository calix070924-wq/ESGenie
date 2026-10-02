"""PR #68 6차 검토(R8-3 후속) 짝 검사 실행기 — 5차 실행기(r8_3_support)에 근거 성격(basis) 검사를 더한다.

같은 폴더의 r8_3_support.py를 파일 경로로 불러온다(수정 전 코드 폴더에서 재현할 때도 이 파일들만 복사해 쓰기
위해서다). 근거 그래프는 5차 실행기가 만든 것을 그대로 받아 노드의 boundary.basis를 본다. 외부 API를 호출하지 않는다.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("r8_3_support", Path(__file__).with_name("r8_3_support.py"))
_r5 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_r5)
_base = _r5._base

FIXTURE = Path(__file__).parent / "fixtures" / "ocr_numeric_review_r6" / "pair_cases.json"
load_cases, run_path = _r5.load_cases, _r5.run_path
_graphs: list = []
_pipeline = _base._pipeline


def _capturing_pipeline(exts):
    out = _pipeline(exts)
    _graphs.append(out[0])
    return out


_base._pipeline = _capturing_pipeline


def observe(ext, forbidden=()):
    _graphs.clear()
    obs = _base.observe(ext, forbidden)
    g = _graphs[-1]
    obs["node_basis"] = sorted({(n.value, n.boundary.basis) for n in g.nodes.values()
                                if isinstance(n.value, (int, float))})
    return obs


def violations(case, obs):
    out = _r5.violations(case, obs)
    for value, basis in case.get("basis", ()):
        got = sorted({b for v, b in obs["node_basis"] if abs(v - value) < 1e-6})
        if got != [basis]:
            out.append(f"basis of {value} {got} != [{basis}]")
    return out
