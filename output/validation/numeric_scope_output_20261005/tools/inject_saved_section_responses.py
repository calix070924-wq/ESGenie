"""PR71 후속 §9.2 — 저장된 실제 본문 생성 응답(검토한 오답 포함)을 최종 제품 경로에 넣어 교정·보류를 확인한다.

사용:
  python inject_saved_section_responses.py --saved-cache <이전 실행 LLM 캐시> --saved-from <ISO> --saved-until <ISO> \
      core --run-dir ... --code-path ... --replay-upstage ... (실행기 인자 그대로)

- 실행기(`scripts/live_numeric_rehearsal.py core --replay-upstage`)를 그대로 쓴다 — 네트워크 차단, Upstage 테이프·
  OCR/LLM 캐시 사본만 사용. **본문 생성 호출(영역 서술부)이 캐시 미스여서 모의 응답으로 떨어질 때만** 같은 단계·영역의
  저장 응답을 생성 순서대로 돌려준다. 저장 응답은 손대지 않는다(오답을 미리 고치지 않는다).
- 이 실행은 실호출이 아니다. 주입 기록(`injection_events.json`)에 영역·순번·저장 키를 남기고, 실제 실행 성공으로 집계하지 않는다.
- 다른 LLM 호출의 미스(요약 등)는 실행기 규칙대로 미스로 기록되고 결정적 대체 문구가 쓰인다.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

argv = sys.argv[1:]


def take(flag: str) -> str:
    i = argv.index(flag)
    value = argv[i + 1]
    del argv[i:i + 2]
    return value


saved_dir = Path(take("--saved-cache")).resolve()
# 저장 응답을 고를 생성 시각 구간. 여러 번 주면 영역마다 **응답이 있는 마지막 구간**을 쓴다 — 이전 실행이 앞 단계의
# 캐시 적중으로 받은 영역(예: 변형본 실행의 S·G)은 그 앞 단계 구간의 응답이 실제로 쓰인 응답이다.
windows = []
while "--saved-window" in argv:
    windows.append(tuple(take("--saved-window").split(",")))
if "--saved-from" in argv:
    windows.append((take("--saved-from"), take("--saved-until")))
code_path = Path(argv[argv.index("--code-path") + 1]).resolve()
run_dir = Path(argv[argv.index("--run-dir") + 1]).resolve()
stage = argv[argv.index("--stage") + 1]
assert "--replay-upstage" in argv and "--live-llm" not in argv, "주입은 네트워크 차단 재생에서만 쓴다"

HEADERS = {"E": "## 환경 성과", "S": "## 사회 성과", "G": "## 지배구조 성과"}
by_window: list[dict[str, list[tuple[str, str, str]]]] = []
records = [(p, json.loads(p.read_text(encoding="utf-8"))) for p in sorted(saved_dir.glob("*.json"))]
for start, until in windows:
    found: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for path, record in records:
        created = str((record.get("meta") or {}).get("created_at") or "")
        if not (start <= created < until):
            continue
        for area, header in HEADERS.items():
            if str(record.get("content") or "").startswith(header):
                found[area].append((created, path.stem, record["content"]))
    by_window.append(found)
saved: dict[str, list[tuple[str, str, str]]] = {}
for found in by_window:
    for area, items in found.items():
        saved[area] = sorted(items)       # 뒤 구간이 앞 구간을 덮는다

events: list[dict] = []
counters: dict[str, int] = defaultdict(int)

spec = importlib.util.spec_from_file_location("live_numeric_rehearsal", code_path / "scripts/live_numeric_rehearsal.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
original_import = runner.import_from


def import_from(path, env_file):
    """실행기가 환경·자격 증명을 확인한 뒤에만 본문 생성 호출을 감싼다(설정 읽기 순서를 바꾸지 않는다)."""
    loaded, settings = original_import(path, env_file)
    import esgenie.llm as llm
    assert code_path in Path(llm.__file__).resolve().parents, llm.__file__
    original = llm.LLMClient.complete

    def complete(self, system, user, **kw):
        resp = original(self, system, user, **kw)
        m = re.search(r"^영역: ([ESG]) ", str(user), re.M)
        if not (resp.used_mock and m and "영역 보고서의 서술부만" in str(user)):
            return resp
        area = m.group(1)
        queue = saved.get(area) or []
        attempt = counters[area]
        counters[area] += 1
        if not queue:
            events.append({"area": area, "attempt": attempt, "status": "no_saved_response"})
            return resp
        created, key, content = queue[min(attempt, len(queue) - 1)]
        events.append({"area": area, "attempt": attempt, "status": "injected_saved_response",
                       "saved_key": key, "saved_created_at": created, "content_head": content[:80]})
        return llm.LLMResponse(content=content, used_mock=False,
                               meta={"provider": "saved_response_injection", "cache": "injected", "saved_key": key})

    llm.LLMClient.complete = complete
    return loaded, settings


runner.import_from = import_from
sys.argv = [str(code_path / "scripts/live_numeric_rehearsal.py"), *argv]
try:
    runner.main()
finally:
    target = run_dir / stage
    target.mkdir(parents=True, exist_ok=True)
    (target / "injection_events.json").write_text(json.dumps({
        "saved_cache": str(saved_dir), "saved_windows": windows,
        "saved_keys": {a: [k for _c, k, _t in v] for a, v in saved.items()},
        "events": events, "note": "저장 응답 주입 — 실호출 아님, 실제 실행 성공으로 집계하지 않음"},
        ensure_ascii=False, indent=2), encoding="utf-8")
