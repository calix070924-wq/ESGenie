"""R8-3 판정 충돌 검사 재현(4차 repro_pairs.py와 같은 절차, 실행기만 r8_3_support.py) — 대상 코드 폴더를 지정해 모든 사례·경로를 실행하고 결과 JSON을 쓴다.

사용: cd <대상 코드 폴더> && python <이 파일> <대상 코드 폴더> <pair_cases.json> <r8_followup_support.py> <out.json>
대상 폴더의 esgenie·tests 모듈을 쓰고, 실행기(support)만 이 작업 폴더의 파일을 불러온다. 외부 API 호출 없음.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

repo, cases_path, support_path, out_path = sys.argv[1:5]
sys.path.insert(0, repo)
for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "UPSTAGE_API_KEY"):
    os.environ.pop(k, None)
from esgenie.ssot import ocr_router as R  # noqa: E402

R._get_openai_key = R._get_anthropic_key = R._get_upstage_key = lambda: None
spec = importlib.util.spec_from_file_location("r8_followup_support", support_path)
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)

cases = S.load_cases(cases_path)
results, fails = {}, 0
for name, case in cases.items():
    for path in case["paths"]:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                obs = S.observe(S.run_path(case, path, tmp), case["forbidden_values"])
                v = S.violations(case, obs)
            except Exception as e:  # 경로가 없는 옛 코드 등
                obs, v = {"error": repr(e)}, [f"error {e!r}"]
        results[f"{name}@{path}"] = {"pass": not v, "violations": v, **obs}
        fails += bool(v)
sha = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
json.dump({"code_sha": sha, "code_path": repo, "total": len(results), "failed": fails, "results": results},
          open(out_path, "w"), ensure_ascii=False, indent=1, default=str)
print(sha[:7], "total", len(results), "failed", fails)
for k, r in results.items():
    if not r["pass"]:
        print(" FAIL", k, "|", "; ".join(r["violations"])[:220])
