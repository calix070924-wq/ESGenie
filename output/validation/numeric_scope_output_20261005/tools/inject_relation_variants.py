"""PR71 재검토 §9.2 — 실제 LLM 응답(캐시 적중)에 A~D 관계 오답·정상 대조를 넣어 **실제 제품 처리 경로**로 최종 파일을 만든다.

사용:
  python inject_relation_variants.py [--variant-set ad|site] [--saved-summary <LLM 캐시 항목.json>] [--saved-sections <LLM 캐시 폴더>] core --run-dir ... \
      --code-path ... --replay-upstage ...
  (`ad`: A~D 오답·정상 대조, 기본값. `site`: 다른 사업장 오답 보류·원본 사업장 정상 보존 필수 대조)
  본문에 문장을 더하면 영역 위험도가 바뀌어 요약 프롬프트가 캐시와 달라진다(미스 → 결정적 대체 문구). 그때는
  `--saved-summary`로 준 같은 단계의 실제 요약 응답(직전 실호출 캐시 항목)에 요약 변형을 붙여 넣는다(저장 응답 주입).

- 실행기(`scripts/live_numeric_rehearsal.py core --replay-upstage`)를 그대로 쓴다 — 네트워크 차단, Upstage 테이프·캐시 사본만.
- 사회(S) 영역 서술부 응답의 `### 주요 활동` 아래와 요약 응답 끝에 아래 문장·표·제목을 더한다(원래 응답은 그대로 두고 뒤에
  붙인다). 원문 청크 인용은 그 생성 입력(프롬프트)의 실제 청크 번호 가운데 출석 기록 청크를 쓴다.
- `--saved-sections`는 생성 입력 변경으로 캐시 미스가 난 영역에 같은 단계의 저장 본문을 주입한다. 원본 키·시각을 기록하고 실호출로 집계하지 않는다.
- 이 실행은 **의도적인 오답 주입 검증 실행**이다. 실호출·실제 실행 성공으로 집계하지 않는다. 주입 전후 응답과 넣은 문장을
  `injection_events.json`에 남긴다. 보류 문구는 테스트가 붙이지 않는다 — 제품 경로가 만든 최종 파일을 그대로 검사한다.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

argv = sys.argv[1:]
variant_set = "ad"
if "--variant-set" in argv:
    i = argv.index("--variant-set")
    variant_set = argv[i + 1]
    del argv[i:i + 2]
saved_summary = None
if "--saved-summary" in argv:
    i = argv.index("--saved-summary")
    saved_summary = Path(argv[i + 1])
    del argv[i:i + 2]
saved_sections = {}
if "--saved-sections" in argv:
    i = argv.index("--saved-sections")
    saved_dir = Path(argv[i + 1])
    del argv[i:i + 2]
    for path in sorted(saved_dir.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        for area, title in (("E", "## 환경 성과"), ("S", "## 사회 성과"), ("G", "## 지배구조 성과")):
            if str(record.get("content") or "").startswith(title):
                created = str((record.get("meta") or {}).get("created_at") or "")
                if area not in saved_sections or created > saved_sections[area][0]:
                    saved_sections[area] = (created, path, record)
code_path = Path(argv[argv.index("--code-path") + 1]).resolve()
run_dir = Path(argv[argv.index("--run-dir") + 1]).resolve()
stage = argv[argv.index("--stage") + 1]
assert "--replay-upstage" in argv and "--live-llm" not in argv, "주입은 네트워크 차단 재생에서만 쓴다"

# (구분, 문장 틀 — {chunk}는 출석 기록 청크 번호, 기대: hold=제품이 확정 서술로 남기지 않아야 함 / keep=보존)
BODY_VARIANTS = [
    ("A 날짜 오기·원문 청크 인용", "2026년 4월 27일 교육에는 46명이 참석하였다 [{chunk}].", "hold"),
    ("A 날짜 오기·인용 없음", "2026년 4월 27일 교육에는 46명이 참석하였다.", "hold"),
    ("A 날짜 오기·구조화 사실 인용", "2026년 4월 27일 교육에는 46명이 참석하였다 [source_facts_S].", "hold"),
    ("B 인용 없는 표의 근거 없는 수량", "| 항목 | 실적 |\n|---|---|\n| 교육 참석 인원 | 999명 |", "hold"),
    ("B 제목의 날짜 오기", "#### 2026년 4월 27일 교육 참석 46명", "hold"),
    ("D 고용형태 뒤바꿈·구조화 사실 인용", "2026년 4월 22일 교육에 정규직 6명과 기간제 40명이 출석했다 [source_facts_S].", "hold"),
    ("D 고용형태 뒤바꿈·인용 없음", "2026년 4월 22일 교육에 정규직 6명과 기간제 40명이 출석했다.", "hold"),
    ("D 고용형태 표(열 머리)", "| 구분 | 정규직 | 기간제 |\n|---|---|---|\n| 2026년 4월 22일 출석 | 6명 | 40명 |", "hold"),
    # 원문(생성 문맥)에 고용형태별 참석 인원이 적혀 있지 않다 — 정답이어도 내역 주장은 보류하고 합계를 보존해야 한다.
    ("D 정답 내역이나 원문 미기재", "2026년 4월 22일 교육에 정규직 40명과 기간제 6명이 출석했다.", "hold"),
    ("정상 4/22 참석 46명", "2026년 4월 22일 교육 참석 인원은 46명이다 [source_facts_S].", "keep"),
    ("정상 4/27 추가 참석 4명", "2026년 4월 27일 추가 교육에는 4명이 참석했다.", "keep"),
]
SITE_VARIANTS = [
    ("F1 다른 사업장 오답", "2026년 4월 22일 양산 제2공장 교육에는 46명이 참석하였다.", "hold"),
    ("F1 원본 사업장 정상", "2026년 4월 22일 김해 제1공장 교육에는 46명이 참석하였다.", "keep"),
]
SUMMARY_VARIANTS = [
    ("요약 고용형태 뒤바꿈", "2026년 4월 22일 교육에는 정규직 6명과 기간제 40명이 출석했다.", "hold"),
    ("요약 날짜 오기", "2026년 4월 27일 교육에는 46명이 참석하였다.", "hold"),
]
if variant_set == "site":
    BODY_VARIANTS = SITE_VARIANTS
    SUMMARY_VARIANTS = [("요약 F1 다른 사업장 오답", SITE_VARIANTS[0][1], "hold")]

events: list[dict] = []
spec = importlib.util.spec_from_file_location("live_numeric_rehearsal", code_path / "scripts/live_numeric_rehearsal.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
original_import = runner.import_from


def _attendance_chunk(user: str) -> str:
    """생성 입력의 원문 청크 가운데 출석 기록 청크 번호(없으면 첫 원문 청크)."""
    ids = re.findall(r"^- \[([^\]]+_txt_\d+)\] (.*)$", str(user), re.M)
    for cid, text in ids:
        if re.search(r"출석|참석", text):
            return cid
    return ids[0][0] if ids else "source_facts_S"


def import_from(path, env_file):
    loaded, settings = original_import(path, env_file)
    import esgenie.llm as llm
    assert code_path in Path(llm.__file__).resolve().parents, llm.__file__
    original = llm.LLMClient.complete

    def complete(self, system, user, **kw):
        resp = original(self, system, user, **kw)
        source = "cache_hit"
        area = re.search(r"^영역: ([ESG]) ", str(user), re.M)
        if resp.used_mock and area and "영역 보고서의 서술부만" in str(user) and area.group(1) in saved_sections:
            created, path, saved = saved_sections[area.group(1)]
            resp = llm.LLMResponse(content=str(saved["content"]), used_mock=False,
                                   meta={"provider": "saved_response_injection", "saved_key": path.stem})
            source = "saved_section"
            events.append({"kind": "saved_section", "area": area.group(1), "saved_key": path.stem,
                           "saved_created_at": created, "variants": []})
        if (resp.used_mock or not str(resp.content or "").strip()) and "Executive Summary" in str(system) \
                and saved_summary is not None and SUMMARY_VARIANTS:
            # 본문 주입으로 바뀐 요약 프롬프트의 미스 — 같은 단계 실제 요약 응답을 저장 응답으로 넣는다(실호출 아님).
            saved = json.loads(saved_summary.read_text(encoding="utf-8"))
            resp = llm.LLMResponse(content=str(saved["content"]), used_mock=False,
                                   meta={"provider": "saved_response_injection", "saved_key": saved_summary.stem})
            source = "saved_summary"
        if resp.used_mock or not str(resp.content or "").strip():
            return resp                     # 캐시 미스(모의 응답)는 바꾸지 않는다 — 실행기가 미스로 기록한다
        content = str(resp.content)
        area = re.search(r"^영역: ([ESG]) ", str(user), re.M)
        if area and area.group(1) == "S" and "영역 보고서의 서술부만" in str(user) and "### 주요 활동" in content:
            chunk = _attendance_chunk(user)
            lines = [text.format(chunk=chunk) for _n, text, _e in BODY_VARIANTS]
            block = "\n\n".join(lines)
            head = re.search(r"### 주요 활동[^\n]*\n", content)
            mutated = content[:head.end()] + block + "\n\n" + content[head.end():]
        elif "Executive Summary" in str(system) and SUMMARY_VARIANTS:
            mutated = content.rstrip() + " " + " ".join(text for _n, text, _e in SUMMARY_VARIANTS)
        else:
            return resp
        events.append({"kind": "summary" if "Executive Summary" in str(system) else "section_S", "response_source": source,
                       "original_content": content, "mutated_content": mutated,
                       "variants": [{"name": n, "text": t.format(chunk=_attendance_chunk(user)), "expect": e}
                                    for n, t, e in (SUMMARY_VARIANTS if "Executive Summary" in str(system)
                                                    else BODY_VARIANTS)],
                       "response_meta": {k: v for k, v in (resp.meta or {}).items() if k != "content"}})
        return llm.LLMResponse(content=mutated, used_mock=False,
                               meta={**(resp.meta or {}), "cache": "injected_relation_variant"})

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
        "variant_set": variant_set, "events": events, "note": "A~D 관계 오답·정상 대조 주입 검증 실행 — 실호출 아님, 실제 실행 성공으로 집계하지 않음"},
        ensure_ascii=False, indent=2, default=str), encoding="utf-8")
