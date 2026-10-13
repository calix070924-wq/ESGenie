#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""일반 문서 AI 대조군 실행기 (작업지시서 B §4.2).

ESGenie 가 같은 실행에서 쓴 OCR 결과를 그대로 일반 LLM 에 넣어, 같은 48문항에 답하게 한다.
차이가 OCR 이 아니라 검증 구조에서 나오게 하기 위한 조건이다.

설계 원칙
---------
* **ESGenie 의 LLM 경로를 재사용하지 않는다.** `esgenie/llm.py` 의 `LLMClient.complete` 는
  응답 캐시(`llm_cache.lookup/store`)·PII 마스킹·내장 재시도(`LLM_MAX_ATTEMPTS`)·mock 폴백을 거치고
  모델을 `SETTINGS.openai_model` 로 고정한다. 대조군은 호출마다 모델을 지정해야 하고 캐시·후처리가
  끼면 "같은 입력을 일반 LLM 에 넣었다"가 성립하지 않는다. 그래서 SDK 를 직접 호출한다.
  엔드포인트 해석만 `esgenie.openai_client.create_openai_client`(캐시·마스킹 없음, max_retries=0)를 쓴다.
* 프롬프트 문구를 코드에 넣지 않는다. `data/eval/prompts/baseline_prompt_v1.md` 에서 읽는다.
* 회사 답변(05)은 증빙 OCR 경로가 아니라 `esgenie.supplychain.claims._extract_text` 를 **불러서**
  추출한다(다시 구현하지 않는다). `\f` 를 쪽 경계로 본다.
* 자동 재시도·자동 분할·프롬프트 수정을 하지 않는다. 실패는 그대로 기록한다.
* 공통 답안 형식은 **A 의 v1.1 확정판**(c1bfa67, 2026-10-10)을 따른다. 행은 `decision_detail`(6값)만 정하고
  외부 `decision`(3값/null)은 A 모듈의 `DETAIL_TO_DECISION` 표가 계산한다 — 표를 여기 다시
  쓰지 않는다. 누락 qid 는 **행을 만들지 않고** 검증이 누락으로 드러낸다.
  내보내기 전에 A 의 `validate_document` 로 검증하고, 실패하면 고치지 않고 기록한다.

출력 (`<out>/<run_id>/rep_<k>/`)
-------------------------------
* `request.json`        프롬프트 sha256·모델·파라미터·입력 길이 (키 값 없음)
* `response_raw.json`   응답 원문 전체
* `common_v1_<stage>.json`  A 공통 답안 형식 v1.1 (JSON 객체 1개 = 한 시스템·한 실행·한 단계)
* `run_log.json`        토큰·시각(KST)·파싱 결과·검증 결과

사용 예
-------
    python scripts/baseline_generic_llm.py \
        --pipeline-json <run>/initial/pipeline.json --stage initial \
        --framework rba42 --model gpt-4.1-mini --provider openai \
        --repeats 5 --out outputs/eval/baseline/<run_id>

    # 저장한 응답만으로 공통 형식을 다시 만든다(호출 없음)
    python scripts/baseline_generic_llm.py --rescore-only outputs/eval/baseline/<run_id>

    # 수동 채팅 실행(API 호출 없음) — 붙여넣기 입력 내보내기 → 사람이 채팅 실행 → 가져오기
    python scripts/baseline_generic_llm.py --provider manual \
        --pipeline-json <run>/initial/pipeline.json --stage initial \
        --repeats 3 --out outputs/eval/baseline
    python scripts/baseline_generic_llm.py --provider manual \
        --import outputs/eval/baseline/MANUAL_claude_initial_rep1
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import hashlib
import importlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

KST = _dt.timezone(_dt.timedelta(hours=9))

DEFAULT_CASES = "data/eval/cases/baseline_cases_v1.csv"
DEFAULT_PROMPT = "data/eval/prompts/baseline_prompt_v1.md"

#: 단계별 `meta.data_source` — v1.1 에서 **선택 필드**(사람이 읽는 설명, 집계에 쓰지 않는다).
#: 확정(A c1bfa67, 2026-10-10) — B 제시값을 A가 그대로 받았다. 바뀌면 이 상수만 고친다.
DATA_SOURCE = {
    "initial": "촬영증빙_17건",
    "followup": "촬영증빙_17건+보완_1건",
}

#: `meta.dataset_tag` — v1.1 **필수**. 허용값 `virtual`/`public_report`/`real_pilot`.
#: 확정(A c1bfa67, 2026-10-10) — 이 비교는 팀이 만든 가상 세트라 `virtual`.
DATASET_TAG = "virtual"

#: `meta.material_kind` — v1.1 선택. `dataset_tag` 와 다른 축(입력 자료 구성)이다.
#: 확정(A c1bfa67, 2026-10-10) — `mixed`(B 제시값을 A가 그대로 받음. 형식 문서 §9.1:
#: "B 제시값을 받았다. A가 원본 구성을 따로 확인한 값은 아니다"). 이전 `photo_evidence`에서 변경.
MATERIAL_KIND = "mixed"

#: A 공통 답안 형식 모듈을 꺼낼 기준 ref. **고정 SHA**로 둔다(브랜치 이름은 움직인다).
#: `origin/codex/eval-scoring-20261006` 의 2026-10-10 확정 커밋(`FORMAT_VERSION = "1.1"`,
#: B 회신 C-1~C-6 반영. f6d2c57 → c1bfa67 diff는 FORMAT_VERSION 문자열과 주석뿐 — 정의 불변).
ANSWER_FORMAT_GIT_REF = "c1bfa6730bb3f77bfeefd74405f293a1db8acbb0"
ANSWER_FORMAT_GIT_PATH = "esgenie/eval/answer_format.py"

#: 회사 답변 구획의 제목·표지. 증빙 블록과 표지를 다르게 둔다.
COMPANY_SECTION_TITLE = "회사 자가 답변 초안 — 검증된 증빙 아님"
COMPANY_NOTICE = "05는 회사 자가 답변 원문, 증빙 아님"

# ── 수동 채팅 실행(--provider manual) ────────────────────────────────────────
#: 채팅 화면에는 system 역할이 없어서 system·user 를 한 덩어리로 붙인다. 이 이어 붙이기
#: 형식이 수동 실행에서 **유일하게** 더해지는 글자다(프롬프트 파일 내용은 그대로).
MANUAL_SYSTEM_HEADER = "[시스템 지시]"
MANUAL_USER_HEADER = "[사용자 입력]"
#: 폴더 이름: MANUAL_<label>_<stage>_rep<k> — 폴더 이름이 그대로 run_id 다.
MANUAL_DIR_FMT = "MANUAL_{label}_{stage}_rep{k}"
MANUAL_PASTE_FILE = "paste_input.txt"
MANUAL_RESPONSE_FILE = "response.txt"
MANUAL_META_FILE = "meta.txt"
#: meta.txt 에 반드시 있어야 하는 줄(`키: 값`). 하나라도 없으면 import 를 거부한다.
MANUAL_META_KEYS = ("모델", "실행 시각", "이어받음")
MANUAL_CONTINUED_VALUES = {"예": True, "아니오": False}
MANUAL_TEMPERATURE_NOTE = "채팅 기본값, 사람이 제어 못 함"
MANUAL_NOTICE = (COMPANY_NOTICE + " / 수동 채팅 실행(claude.ai) — temperature·토큰·"
                 "채팅 서비스의 숨은 시스템 지시를 통제하지 못함")
DOC_OPEN, DOC_CLOSE = "[문서 시작]", "[문서 끝]"
CA_OPEN, CA_CLOSE = "[회사 답변 시작]", "[회사 답변 끝]"
PAGE_UNKNOWN = "미상"

#: 모델이 내는 판정값 → `decision_detail`. 구조로만 옮긴다(단서 문구로 바꾸지 않는다).
#: 외부 `decision` 은 A 모듈의 대응 표가 계산한다.
MODEL_DECISIONS = ("answer", "hold", "not_applicable")

#: 입력 토큰 추정 계수. tiktoken 은 requirements.txt 에 없어 쓰지 않는다(새 의존성 금지).
#: 한국어 혼합 텍스트에서 보수적으로 잡은 값이고, 호출 뒤에는 API 가 보고한 prompt_tokens 를 기록한다.
TOKEN_CHARS_PER_TOKEN = 2.0
TOKEN_ESTIMATE_METHOD = (
    "chars/%.1f (tiktoken 미설치·requirements.txt 에 없음. 호출 후 API prompt_tokens 로 대체 기록)"
    % TOKEN_CHARS_PER_TOKEN
)

#: 입력 토큰 한도 표. **값은 공급자 공식 문서에서 확인해 넣는다.**
#: 확인하지 않은 모델은 **넣지 않는다** — 추측해 넣으면 컨텍스트 초과를 못 잡는다.
#: 표가 비어 있으면 모든 모델이 `--max-input-tokens` 를 요구한다(fail closed).
#: 출처 URL·확인 날짜는 `docs/validation/eval-baseline-20261006/README.md` §6 표에 함께 적는다.
CONTEXT_LIMIT_DOC = "docs/validation/eval-baseline-20261006/README.md §6"
#: 출처: Microsoft Learn "Foundry Models sold by Azure" — GPT-4.1 series 표, 2026-10-12 확인.
#: 공식 "Context window" 값(입력+출력 합계, 표준(Standard/Global Standard) 배포)은
#: 1,047,576 / 300,000(표준 배포) / 128,000(provisioned·batch) 세 단계다. ESGenie LIVE 실행은
#: 표준 배포(pay-per-token, environment.json 의 llm_provider=azure_openai, 실제 응답 모델
#: gpt-4.1-mini-2025-04-14)라 300,000 을 쓴다. "Max output tokens" 는 두 모델 모두 32,768 —
#: 입력 한도는 가장 보수적으로 "총 한도 − 최대 출력"으로 계산한다
#: (300,000 − 32,768 = 267,232). 단가는 동적 렌더링 페이지(azure.microsoft.com/…/openai-service/)
#: 라 조사 도구로 확인하지 못했다 — README §6·리뷰 기록에 미확인으로 남긴다.
_AZURE_GPT41_SOURCE = (
    "https://learn.microsoft.com/en-us/azure/ai-services/openai/concepts/models"
    " (GPT-4.1 series 표)"
)
MODEL_CONTEXT_LIMITS: dict[str, dict[str, Any]] = {
    "gpt-4.1-mini": {"max_input_tokens": 267232, "source": _AZURE_GPT41_SOURCE,
                     "checked_on": "2026-10-12"},
    "gpt-4.1": {"max_input_tokens": 267232, "source": _AZURE_GPT41_SOURCE,
                "checked_on": "2026-10-12"},
    # "<model-id>": {"max_input_tokens": <int>, "source": "<URL>", "checked_on": "YYYY-MM-DD"},
}


# ── 작은 유틸 ────────────────────────────────────────────────────────────────
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def now_kst() -> str:
    return _dt.datetime.now(KST).isoformat()


def estimate_tokens(text: str) -> int:
    return int(len(text) / TOKEN_CHARS_PER_TOKEN) + 1


def repo_relpath(path: str | Path) -> str:
    p = Path(path).resolve()
    try:
        return p.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def write_json(path: str | Path, obj: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ── A 공통 답안 형식 모듈 로딩 ───────────────────────────────────────────────
#: 변환기가 반드시 불러 써야 하는 A 모듈의 이름. 없으면 실패한다(우리가 다시 구현하지 않는다).
AF_REQUIRED_NAMES = (
    "FORMAT_VERSION", "DECISION_DETAILS", "DETAIL_TO_DECISION",
    "decision_for_detail", "new_document", "new_answer", "validate_document",
)


def _load_module_from_file(path: Path, *, origin: str):
    spec = importlib.util.spec_from_file_location("_b3_answer_format", path)
    if not (spec and spec.loader):
        raise ImportError(f"answer_format 를 불러올 수 없다: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.__source_path__ = str(path)              # type: ignore[attr-defined]
    mod.__source_sha256__ = sha256_file(path)    # type: ignore[attr-defined]
    mod.__source_origin__ = origin               # type: ignore[attr-defined]
    return mod


def _answer_format_from_git(ref: str, *, tmp_root: Path | None = None):
    """고정 ref 에서 A 의 모듈을 꺼내 임시 파일로 불러온다(저장소에 복사하지 않는다)."""
    import subprocess
    import tempfile

    def _git(*args: str) -> str:
        out = subprocess.run(["git", *args], cwd=str(REPO_ROOT),
                             capture_output=True, check=True)
        return out.stdout.decode("utf-8", "replace").strip()

    commit = _git("rev-parse", ref)
    blob = _git("rev-parse", f"{ref}:{ANSWER_FORMAT_GIT_PATH}")
    raw = subprocess.run(["git", "cat-file", "blob", blob], cwd=str(REPO_ROOT),
                         capture_output=True, check=True).stdout
    root = Path(tmp_root) if tmp_root else Path(tempfile.mkdtemp(prefix="b3_af_"))
    root.mkdir(parents=True, exist_ok=True)
    path = root / "answer_format.py"
    path.write_bytes(raw)
    mod = _load_module_from_file(path, origin=f"git:{ref}@{commit}:{ANSWER_FORMAT_GIT_PATH}")
    mod.__git_commit__ = commit          # type: ignore[attr-defined]
    mod.__git_blob__ = blob              # type: ignore[attr-defined]
    return mod


def load_answer_format(explicit: str | None = None, *, git_ref: str | None = None):
    """A 가 소유하는 `answer_format` 모듈을 찾는다.

    해석 순서
      1. `--answer-format <경로>` (파일 또는 패키지 루트)
      2. 환경변수 `ESGENIE_EVAL_ANSWER_FORMAT`
      3. 저장소의 `esgenie.eval.answer_format` (A 브랜치가 병합되면 여기 있다)
      4. **고정 ref `ANSWER_FORMAT_GIT_REF`** 에서 `git cat-file` 로 꺼내 임시 파일로 로딩

    v1.1 로 갈아탄 뒤의 기본 대상은 4번(= A 브랜치 v1.1)이다. 3번이 생기면 그쪽이 먼저지만,
    A 브랜치가 병합된 뒤의 저장소 모듈은 같은 v1.1 이다.

    못 찾거나 필요한 이름이 없으면 예외를 올린다. **검증을 건너뛰지 않는다.**
    """
    candidates = [c for c in (explicit, os.getenv("ESGENIE_EVAL_ANSWER_FORMAT")) if c]
    mod = None
    if candidates:
        tried: list[str] = []
        for cand in candidates:
            p = Path(cand)
            if p.is_dir():
                for sub in (p / "esgenie" / "eval" / "answer_format.py",
                            p / "answer_format.py"):
                    if sub.is_file():
                        p = sub
                        break
            if p.is_file():
                mod = _load_module_from_file(p, origin=f"path:{p}")
                break
            tried.append(str(p))
        if mod is None:
            raise FileNotFoundError("answer_format 를 찾을 수 없다: " + ", ".join(tried))
    else:
        try:
            repo_mod = importlib.import_module("esgenie.eval.answer_format")
        except ModuleNotFoundError:
            repo_mod = None
        if repo_mod is not None:
            src = Path(repo_mod.__file__)  # type: ignore[arg-type]
            repo_mod.__source_path__ = str(src)            # type: ignore[attr-defined]
            repo_mod.__source_sha256__ = sha256_file(src)  # type: ignore[attr-defined]
            repo_mod.__source_origin__ = f"repo:{repo_relpath(src)}"  # type: ignore[attr-defined]
            mod = repo_mod
        else:
            mod = _answer_format_from_git(git_ref or ANSWER_FORMAT_GIT_REF)

    missing = [n for n in AF_REQUIRED_NAMES if not hasattr(mod, n)]
    if missing:
        raise AttributeError(
            "answer_format 에 필요한 이름이 없다: %s (경로 %s). "
            "대응 표를 우리 코드에 다시 쓰지 않는다 — 모듈을 고쳐 받아야 한다"
            % (", ".join(missing), getattr(mod, "__source_path__", "?")))
    return mod


def answer_format_record(af) -> dict[str, Any]:
    """어느 판으로 검증했는지 실행 기록에 남길 식별자."""
    return {
        "path": getattr(af, "__source_path__", None),
        "sha256": getattr(af, "__source_sha256__", None),
        "origin": getattr(af, "__source_origin__", None),
        "git_commit": getattr(af, "__git_commit__", None),
        "git_blob": getattr(af, "__git_blob__", None),
        "format_version": getattr(af, "FORMAT_VERSION", None),
    }


# ── 사례 파일(문항 목록) ────────────────────────────────────────────────────
def load_cases(path: str | Path) -> list[dict[str, str]]:
    """사전 등록 사례 CSV 에서 qid·question_text 를 순서대로 읽는다(읽기만 한다)."""
    with Path(path).open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        if not r.get("qid") or not r.get("question_text"):
            raise ValueError(f"사례 파일에 qid/question_text 가 비어 있다: {r}")
    return [{"qid": r["qid"], "question_text": r["question_text"]} for r in rows]


# ── 프롬프트 파일 ───────────────────────────────────────────────────────────
_FENCE = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.S)


def load_prompt(path: str | Path) -> dict[str, str]:
    """프롬프트 md 의 §1 시스템·§2 사용자 템플릿을 첫 코드펜스에서 읽는다."""
    text = Path(path).read_text(encoding="utf-8")
    sections = re.split(r"(?m)^##\s+", text)
    system = user = None
    for sec in sections:
        head = sec.splitlines()[0] if sec.strip() else ""
        blocks = _FENCE.findall(sec)
        if not blocks:
            continue
        if system is None and "시스템 프롬프트" in head:
            system = blocks[0].rstrip("\n")
        elif user is None and "사용자 프롬프트" in head:
            user = blocks[0].rstrip("\n")
    if not system or not user:
        raise ValueError(f"{path}: 시스템/사용자 프롬프트 코드블록을 찾지 못했다")
    return {
        "system": system,
        "user_template": user,
        "path": repo_relpath(path),
        "sha256": sha256_file(path),
    }


# ── 증빙 텍스트와 쪽 복원 ──────────────────────────────────────────────────
def recover_pages(extraction: dict[str, Any]) -> tuple[list[str], str, int | None]:
    """한 증빙 문서의 `raw_text` 를 쪽별로 쪼갠다.

    반환: (쪽 텍스트 목록, 복원 방식, 복원한 쪽 수 또는 None)
      방식 `chunk_sources`       — router_meta.chunk_sources 의 줄 범위로 배분
      방식 `single_page_upstage` — 정형 채널에서 쪽 수가 1쪽으로 확인됨
      방식 `unrecovered`         — 복원 못 함. 전체를 한 덩어리로 두고 쪽은 미상
    """
    raw = extraction.get("raw_text") or ""
    meta = extraction.get("router_meta") or {}

    chunks = meta.get("chunk_sources")
    if isinstance(chunks, list) and chunks and not any(c.get("context_repeated") for c in chunks):
        lines = raw.split("\n")
        spans = []
        ok = True
        for c in chunks:
            try:
                n = int(c["line_end"]) - int(c["line_start"]) + 1
                page = int(c["page"]) + 1  # 0-기준 → 1-기준
            except (KeyError, TypeError, ValueError):
                ok = False
                break
            if n <= 0:
                ok = False
                break
            spans.append((page, n))
        if ok and sum(n for _, n in spans) == len(lines):
            pages: dict[int, list[str]] = {}
            cursor = 0
            for page, n in spans:
                pages.setdefault(page, []).extend(lines[cursor:cursor + n])
                cursor += n
            ordered = [("\n".join(pages[p])) for p in sorted(pages)]
            return ordered, "chunk_sources", len(ordered)

    dp = meta.get("dp_response") or {}
    usage = dp.get("usage") or {}
    if usage.get("pages") == 1:
        return [raw], "single_page_upstage", 1

    return [raw], "unrecovered", None


def load_stage_documents(pipeline_json: str | Path) -> list[dict[str, Any]]:
    """단계 pipeline.json 의 증빙 문서를 순서대로 읽고 쪽을 복원한다."""
    data = read_json(pipeline_json)
    out = []
    for e in data.get("ocr_extractions") or []:
        pages, method, page_count = recover_pages(e)
        out.append({
            "file_name": e.get("source_file") or "",
            "channel": e.get("channel"),
            "pages": pages,
            "page_method": method,
            "page_count": page_count,
            "chars": len(e.get("raw_text") or ""),
            "source_sha256": (e.get("router_meta") or {}).get("source_sha256"),
        })
    return out


def crosscheck_upstage_pages(run_stage_dir: str | Path) -> dict[str, Any]:
    """`raw_upstage/*.json` 에서 파일별 쪽 수를 읽어 교차 확인용으로 돌려준다."""
    out: dict[str, Any] = {}
    d = Path(run_stage_dir) / "raw_upstage"
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        try:
            rec = read_json(p)
        except Exception as exc:                      # noqa: BLE001 - 기록만 한다
            out[p.name] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        body = rec.get("body") or {}
        element_pages = sorted({e.get("page") for e in (body.get("elements") or [])
                                if isinstance(e.get("page"), int)})
        out[str(rec.get("file") or p.name)] = {
            "tape_file": p.name,
            "usage_pages": (body.get("usage") or {}).get("pages"),
            "element_pages": element_pages,
        }
    return out


# ── 회사 답변(05) ──────────────────────────────────────────────────────────
def company_answer_input(run_stage_dir: str | Path) -> dict[str, Any]:
    """`environment.json` 에서 role=company_answer 입력을 찾는다."""
    env = read_json(Path(run_stage_dir) / "environment.json")
    rows = [i for i in (env.get("inputs") or []) if i.get("role") == "company_answer"]
    if len(rows) != 1:
        raise ValueError(f"company_answer 입력이 1건이 아니다: {len(rows)}건")
    return rows[0]


def extract_company_answer(pdf_path: str | Path) -> dict[str, Any]:
    """ESGenie 와 같은 함수로 05 원문을 추출한다(다시 구현하지 않는다)."""
    from esgenie.supplychain import claims as _claims

    fn = _claims._extract_text
    text = fn(str(pdf_path))
    pages = text.split("\f") if text else []

    # `_extract_text` 는 pymupdf 를 먼저 쓰고 실패하면 pdftotext 로 간다. 어느 경로를 탔는지
    # 같은 방식으로 다시 읽어 결과를 대조해 확인한다(기록용. 판정에 쓰지 않는다).
    route = "unknown"
    pymupdf_version = None
    try:
        import fitz
        pymupdf_version = getattr(fitz, "__version__", None) or getattr(fitz, "VersionBind", None)
        with fitz.open(str(pdf_path)) as doc:
            probe = "\f".join(pg.get_text() for pg in doc)
        route = "pymupdf" if probe == text else "not_pymupdf(pdftotext_or_empty)"
    except Exception as exc:                          # noqa: BLE001 - 기록만 한다
        route = f"probe_failed:{type(exc).__name__}"

    return {
        "file_name": Path(pdf_path).name,
        "pdf_path": str(pdf_path),
        "pdf_sha256": sha256_file(pdf_path),
        "pages": pages,
        "page_count": len(pages),
        "chars": len(text),
        "text": text,
        "text_sha256": sha256_bytes(text.encode("utf-8")),
        "extractor": {
            "function": "esgenie/supplychain/claims.py::_extract_text",
            "module_file": repo_relpath(_claims.__file__),
            "route_taken": route,
            "pymupdf_version": str(pymupdf_version),
            "page_separator": "\\f",
        },
    }


# ── 입력 블록 조립 ─────────────────────────────────────────────────────────
def _page_label(page: int | None) -> str:
    return PAGE_UNKNOWN if page is None else str(page)


def build_documents_block(docs: list[dict[str, Any]]) -> str:
    parts = []
    for d in docs:
        name = d["file_name"]
        unrecovered = d["page_method"] == "unrecovered"
        for idx, body in enumerate(d["pages"], start=1):
            label = _page_label(None if unrecovered else idx)
            parts.append(
                f"{DOC_OPEN} 파일: {name} | 쪽: {label}\n{body}\n"
                f"{DOC_CLOSE} 파일: {name} | 쪽: {label}"
            )
    return "\n\n".join(parts)


def build_company_block(ca: dict[str, Any]) -> str:
    name = ca["file_name"]
    parts = []
    for idx, body in enumerate(ca["pages"], start=1):
        parts.append(
            f"{CA_OPEN} 파일: {name} | 쪽: {idx}\n{body}\n"
            f"{CA_CLOSE} 파일: {name} | 쪽: {idx}"
        )
    return "\n\n".join(parts)


def build_questions_block(cases: list[dict[str, str]]) -> str:
    return "\n".join(f"- qid: {c['qid']} | 질문: {c['question_text']}" for c in cases)


def render_user_prompt(template: str, *, documents: str, company_answer: str,
                       questions: str, question_count: int) -> str:
    out = template
    for key, value in (
        ("{{DOCUMENTS}}", documents),
        ("{{COMPANY_ANSWER}}", company_answer),
        ("{{QUESTIONS}}", questions),
        ("{{QUESTION_COUNT}}", str(question_count)),
    ):
        if key not in out:
            raise ValueError(f"프롬프트 템플릿에 치환 자리 {key} 가 없다")
        out = out.replace(key, value)
    left = re.findall(r"\{\{[A-Z_]+\}\}", out)
    if left:
        raise ValueError(f"치환하지 못한 자리가 남았다: {left}")
    return out


def unrecovered_files(docs: list[dict[str, Any]]) -> set[str]:
    return {d["file_name"] for d in docs if d["page_method"] == "unrecovered"}


# ── 모델 출력 파싱 ─────────────────────────────────────────────────────────
def parse_model_output(text: str) -> tuple[dict[str, Any] | None, str]:
    """모델 출력에서 JSON 객체를 꺼낸다. 고치지 않고, 실패하면 이유를 돌려준다.

    코드펜스로 감싼 경우와 앞뒤에 말이 붙은 경우만 벗겨 본다. 잘린 JSON 은 복구하지 않는다.
    """
    if text is None or not str(text).strip():
        return None, "empty_response"
    raw = str(text).strip()

    candidates = [raw]
    fenced = _FENCE.findall(raw)
    candidates.extend(b.strip() for b in fenced)
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        candidates.append(raw[start:end + 1])

    last = ""
    for cand in candidates:
        if not cand:
            continue
        try:
            obj = json.loads(cand)
        except json.JSONDecodeError as exc:
            last = f"json_decode_error: {exc.msg} (pos {exc.pos})"
            continue
        if not isinstance(obj, dict):
            last = "top_level_not_object"
            continue
        if not isinstance(obj.get("answers"), list):
            last = "answers_not_list"
            continue
        return obj, ""
    return None, last or "no_json_found"


def _row_detail(row: dict[str, Any], evidence: list[dict[str, Any]]) -> tuple[str, str]:
    """구조만 보고 `decision_detail`(6값)을 정한다. 단서 문구로 바꾸지 않는다.

    외부 `decision`(3값 또는 null)은 **여기서 정하지 않는다** — A 모듈의
    `DETAIL_TO_DECISION` 표가 `new_answer()` 안에서 계산한다. 표를 다시 쓰지 않는다.
    """
    model_decision = row.get("decision")
    if model_decision == "hold":
        return "hold", "모델 decision=hold"
    if model_decision == "not_applicable":
        return "not_applicable", "모델 decision=not_applicable"
    if model_decision == "answer":
        if evidence:
            return "confirmed", f"모델 decision=answer, evidence {len(evidence)}건"
        return "unverified_submitted", "모델 decision=answer, evidence 0건"
    return "unparsed", f"모델 decision={model_decision!r} — 허용값 {MODEL_DECISIONS} 밖"


def _clean_evidence(items: Any, unrecovered: set[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        name = it.get("file_name")
        if not isinstance(name, str) or not name.strip():
            continue
        page = it.get("page")
        if name in unrecovered:
            page = None                       # 쪽 복원 못 한 문서 — 1 로 채우지 않는다
        elif isinstance(page, bool) or not isinstance(page, int) or page < 1:
            page = None
        quote = it.get("quote")
        out.append({
            "file_name": name,
            "page": page,
            "quote": quote if isinstance(quote, str) else None,
        })
    return out


def to_common_document(
    af,
    *,
    parsed: dict[str, Any] | None,
    parse_error: str,
    cases: list[dict[str, str]],
    stage: str,
    framework: str,
    run_id: str,
    model_id: str,
    data_source: str,
    source_ref: dict[str, Any],
    unrecovered: set[str],
    company_answer_file: str,
    produced_at: str | None = None,
    adapter_extra: dict[str, Any] | None = None,
    notice: str | None = None,
    system: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """저장한 응답 → A 공통 답안 형식 v1.1 문서. (문서, 집계) 를 돌려준다.

    v1.1: 행은 `decision_detail`(6값)만 정하고 외부 `decision`(3값/null)은 A 모듈의
    `DETAIL_TO_DECISION` 표가 계산한다. **누락 qid 는 행을 만들지 않는다**
    (v1.1 계약: "누락된 qid는 행을 만들지 않고 검증에서 누락으로 드러낸다").
    """
    by_qid: dict[str, list[dict[str, Any]]] = {}
    if parsed:
        for row in parsed.get("answers") or []:
            if isinstance(row, dict) and isinstance(row.get("qid"), str):
                by_qid.setdefault(row["qid"], []).append(row)

    answers: list[dict[str, Any]] = []
    stats: dict[str, Any] = {
        "rows": 0,
        "detail_counts": {d: 0 for d in af.DECISION_DETAILS},
        "decision_counts": {},
        "missing_qid": [], "duplicate_qid": [], "unknown_qid": [],
        "cited_company_answer": 0, "evidence_page_null": 0,
        "parse_error": parse_error,
    }

    for case in cases:
        qid = case["qid"]
        rows = by_qid.get(qid, [])
        if not rows:
            # v1.1 — 행을 만들지 않는다. 검증이 missing_qid·row_count 로 드러낸다.
            stats["missing_qid"].append(qid)
            continue
        if len(rows) > 1:
            stats["duplicate_qid"].append(qid)
        row = rows[0]                       # 중복은 첫 행만 쓰고 중복 사실을 남긴다
        evidence = _clean_evidence(row.get("evidence"), unrecovered)
        detail, basis = _row_detail(row, evidence)
        if len(rows) > 1:
            basis += f" (중복 응답 {len(rows)}건 — 첫 행 사용)"
        unit = row.get("unit")
        value = row.get("value")
        if not isinstance(value, (int, float, bool, str, type(None))):
            value = None
        period = row.get("period")
        label = row.get("scope")
        boundary = {
            "period": period if isinstance(period, str) and period.strip() else None,
            "site": None,
            "target": None,
            "denominator": None,
            "label": label if isinstance(label, str) and label.strip() else None,
            "completeness": None,
        }
        notes = row.get("note")
        # `decision` 은 넘기지 않는다 — A 모듈이 DETAIL_TO_DECISION 으로 계산한다.
        answers.append(af.new_answer(
            qid=qid, decision_detail=detail, decision_basis=basis,
            value=value, unit=unit if isinstance(unit, str) else "",
            value_text=row.get("answer_text") if isinstance(row.get("answer_text"), str) else None,
            boundary=boundary, evidence=evidence,
            source={"raw": json.dumps(row, ensure_ascii=False)},
            notes=notes if isinstance(notes, str) and notes.strip() else None,
        ))
        stats["detail_counts"][detail] = stats["detail_counts"].get(detail, 0) + 1
        decision = af.decision_for_detail(detail)    # 표를 다시 쓰지 않고 불러 쓴다
        key = "null" if decision is None else str(decision)
        stats["decision_counts"][key] = stats["decision_counts"].get(key, 0) + 1
        stats["rows"] += 1
        if any(e["file_name"] == company_answer_file for e in evidence):
            stats["cited_company_answer"] += 1
        stats["evidence_page_null"] += sum(1 for e in evidence if e["page"] is None)

    known = {c["qid"] for c in cases}
    stats["unknown_qid"] = sorted(set(by_qid) - known)

    doc = af.new_document(
        system=system or f"baseline_{model_id}",
        run_id=run_id,
        stage=stage,
        framework=framework,
        dataset_tag=DATASET_TAG,          # v1.1 필수 (확정, A c1bfa67)
        material_kind=MATERIAL_KIND,      # dataset_tag 와 다른 축 (확정, A c1bfa67)
        data_source=data_source,          # v1.1 선택 — 사람이 읽는 설명
        model=model_id,
        produced_at=produced_at or now_kst(),
        adapter={"name": "scripts/baseline_generic_llm.py", "version": "2",
                 **(adapter_extra or {})},
        source_ref=source_ref,
        notice=notice or COMPANY_NOTICE,
        answers=answers,
    )
    return doc, stats


def count_decision_flips(docs: list[dict[str, Any]]) -> dict[str, Any]:
    """반복 간 판정이 바뀐 행 수. 문서는 공통 형식 문서 목록이다.

    v1.1 에서는 `decision_detail` 로 센다 — 외부 3값은 `confirmed` 와
    `unverified_submitted` 를 모두 `answer` 로 묶어 변동을 가린다.
    어느 반복에서 행 자체가 없으면(누락 qid) `__absent__` 로 세어 변동으로 잡는다.
    """
    if len(docs) < 2:
        return {"repeats": len(docs), "changed_rows": 0, "changed_qids": []}
    qids: set[str] = set()
    per_doc: list[dict[str, str]] = []
    for doc in docs:
        row_map = {str(a.get("qid")): str(a.get("decision_detail"))
                   for a in (doc.get("answers") or [])}
        per_doc.append(row_map)
        qids |= set(row_map)
    per_qid: dict[str, set[str]] = {}
    for qid in qids:
        per_qid[qid] = {m.get(qid, "__absent__") for m in per_doc}
    changed = sorted(q for q, vals in per_qid.items() if len(vals) > 1)
    return {"repeats": len(docs), "changed_rows": len(changed), "changed_qids": changed}


# ── 모델 호출 ──────────────────────────────────────────────────────────────
def call_model(provider: str, model: str, system: str, user: str) -> dict[str, Any]:
    """SDK 직접 호출. 캐시·마스킹·재시도·mock 폴백 없음. temperature 0."""
    if provider == "openai":
        from esgenie.config import SETTINGS
        from esgenie.openai_client import create_openai_client

        if not SETTINGS.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY 가 없다")
        client = create_openai_client(SETTINGS)
        resp = client.chat.completions.create(
            model=model,
            temperature=0,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        usage = getattr(resp, "usage", None)
        return {
            "text": (resp.choices[0].message.content or ""),
            "model": getattr(resp, "model", None) or model,
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
            "response_id": getattr(resp, "id", None),
            "raw": resp.model_dump() if hasattr(resp, "model_dump") else None,
        }
    if provider == "anthropic":
        import anthropic

        from esgenie.config import SETTINGS
        if not SETTINGS.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY 가 없다")
        client = anthropic.Anthropic(api_key=SETTINGS.anthropic_api_key, max_retries=0)
        resp = client.messages.create(
            model=model, temperature=0, max_tokens=16000,
            system=system, messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        usage = getattr(resp, "usage", None)
        return {
            "text": text,
            "model": getattr(resp, "model", None) or model,
            "prompt_tokens": getattr(usage, "input_tokens", None),
            "completion_tokens": getattr(usage, "output_tokens", None),
            "total_tokens": None,
            "response_id": getattr(resp, "id", None),
            "raw": resp.model_dump() if hasattr(resp, "model_dump") else None,
        }
    raise ValueError(f"알 수 없는 provider: {provider}")


# ── 실행 ───────────────────────────────────────────────────────────────────
def build_inputs(args) -> dict[str, Any]:
    """호출 전까지의 모든 준비. 호출은 하지 않는다."""
    stage_dir = Path(args.pipeline_json).resolve().parent
    docs = load_stage_documents(args.pipeline_json)
    ca_input = company_answer_input(stage_dir)
    ca = extract_company_answer(ca_input["path"])
    if ca["pdf_sha256"] != ca_input["sha256"]:
        raise RuntimeError(
            "회사 답변 PDF sha256 이 environment.json 과 다르다: "
            f"{ca['pdf_sha256']} != {ca_input['sha256']}"
        )
    cases = load_cases(args.cases)
    prompt = load_prompt(args.prompt)

    documents_block = build_documents_block(docs)
    company_block = build_company_block(ca)
    questions_block = build_questions_block(cases)
    user = render_user_prompt(
        prompt["user_template"], documents=documents_block,
        company_answer=company_block, questions=questions_block,
        question_count=len(cases),
    )
    full_chars = len(prompt["system"]) + len(user)
    return {
        "stage_dir": stage_dir,
        "docs": docs,
        "company_answer": ca,
        "company_answer_input": {k: ca_input.get(k) for k in ("name", "role", "sha256")},
        "cases": cases,
        "prompt": prompt,
        "system": prompt["system"],
        "user": user,
        "blocks": {
            "documents_chars": len(documents_block),
            "company_answer_chars": len(company_block),
            "questions_chars": len(questions_block),
        },
        "input_chars": full_chars,
        "input_tokens_estimate": estimate_tokens(prompt["system"] + user),
        "token_estimate_method": TOKEN_ESTIMATE_METHOD,
        "upstage_pages": crosscheck_upstage_pages(stage_dir),
        "unrecovered_files": sorted(unrecovered_files(docs)),
        "page_methods": {d["file_name"]: d["page_method"] for d in docs},
    }


def resolve_input_limit(model: str, explicit: int | None) -> tuple[int, str]:
    """입력 토큰 한도를 정한다. 모르면 실행을 거부한다.

    ① `--max-input-tokens` 를 주면 그 값(출처 `cli`)
    ② `MODEL_CONTEXT_LIMITS` 표에 있으면 그 값(출처 `table:<README 확인 날짜>`)
    ③ 둘 다 없으면 **거부한다.** 한도를 모르는 채로 호출하면 컨텍스트 초과를 알 수 없다.
    """
    if explicit is not None:
        if explicit <= 0:
            raise ValueError("--max-input-tokens 는 1 이상이어야 한다")
        return explicit, "cli"
    row = MODEL_CONTEXT_LIMITS.get(model)
    if row:
        return int(row["max_input_tokens"]), f"table:{row.get('checked_on', '미확인')}"
    raise RuntimeError(
        f"모델 '{model}' 의 컨텍스트 한도를 모른다. "
        f"{CONTEXT_LIMIT_DOC} 표에 출처 URL·확인 날짜와 함께 적고 "
        f"MODEL_CONTEXT_LIMITS 에 넣거나, --max-input-tokens 로 직접 준다. "
        "한도를 모르는 채로 실행하지 않는다(자동 분할도 하지 않는다)")


def run_live(args) -> int:
    af = load_answer_format(args.answer_format, git_ref=args.answer_format_ref)
    # 한도를 모르면 **실호출은 거부**한다. `--dry-run` 은 호출이 없고, 표를 채우기 위한
    # 입력 길이 측정 자체가 dry-run 의 목적이라 한도 없이도 진행한다(기록에 남긴다).
    try:
        limit, limit_source = resolve_input_limit(args.model, args.max_input_tokens)
    except RuntimeError:
        if not args.dry_run:
            raise
        limit, limit_source = None, "dry_run_unchecked"
    prep = build_inputs(args)
    stage = args.stage
    data_source = DATA_SOURCE[stage]

    if limit is not None and prep["input_tokens_estimate"] > limit:
        out = Path(args.out) / (args.run_id or "aborted")
        write_json(out / "run_log.json", {
            "status": "aborted_context_limit",
            "input_chars": prep["input_chars"],
            "input_tokens_estimate": prep["input_tokens_estimate"],
            "token_estimate_method": prep["token_estimate_method"],
            "max_input_tokens": limit,
            "max_input_tokens_source": limit_source,
            "detail": "모델 컨텍스트를 넘었다. 자동 분할하지 않고 중단했다.",
            "aborted_at": now_kst(),
        })
        print("중단: 입력 추정 토큰 %d > 한도 %d (출처 %s, 자동 분할하지 않는다)"
              % (prep["input_tokens_estimate"], limit, limit_source), file=sys.stderr)
        return 2

    base_run_id = args.run_id or f"BASE_{stage}_{_dt.datetime.now(KST):%Y%m%d%H%M%S}"
    out_root = Path(args.out)
    common_docs: list[dict[str, Any]] = []
    failures = 0

    # 회사 답변 추출 원문 전체를 파일로 보존한다(요약·정제하지 않은 그대로).
    run_dir = out_root / base_run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "company_answer_extracted.txt").write_text(
        prep["company_answer"]["text"], encoding="utf-8")
    write_json(run_dir / "input_manifest.json", {
        "stage": stage, "data_source": data_source,
        "pipeline_json": str(Path(args.pipeline_json).resolve()),
        "documents": [{k: d[k] for k in
                       ("file_name", "channel", "page_method", "page_count", "chars",
                        "source_sha256")} for d in prep["docs"]],
        "upstage_page_crosscheck": prep["upstage_pages"],
        "unrecovered_files": prep["unrecovered_files"],
        "company_answer": {k: v for k, v in prep["company_answer"].items()
                           if k not in ("pages", "text")},
        "company_answer_input": prep["company_answer_input"],
        "company_answer_text_file": "company_answer_extracted.txt",
        "input_chars": prep["input_chars"],
        "input_tokens_estimate": prep["input_tokens_estimate"],
        "token_estimate_method": prep["token_estimate_method"],
        "blocks": prep["blocks"],
        "built_at_kst": now_kst(),
    })

    for k in range(1, args.repeats + 1):
        run_id = f"{base_run_id}_rep{k}"          # 반복마다 다른 run_id
        rep_dir = out_root / base_run_id / f"rep_{k}"
        write_json(rep_dir / "request.json", {
            "run_id": run_id, "stage": stage, "framework": args.framework,
            "provider": args.provider, "model_requested": args.model,
            "temperature": 0,
            "prompt_file": prep["prompt"]["path"],
            "prompt_sha256": prep["prompt"]["sha256"],
            "cases_file": repo_relpath(args.cases),
            "cases_sha256": sha256_file(args.cases),
            "pipeline_json": str(Path(args.pipeline_json).resolve()),
            "question_count": len(prep["cases"]),
            "input_chars": prep["input_chars"],
            "input_tokens_estimate": prep["input_tokens_estimate"],
            "token_estimate_method": prep["token_estimate_method"],
            "max_input_tokens": limit,
            "max_input_tokens_source": limit_source,
            "answer_format": answer_format_record(af),
            "dataset_tag": DATASET_TAG,
            "material_kind": MATERIAL_KIND,
            "data_source": data_source,
            "blocks": prep["blocks"],
            "page_methods": prep["page_methods"],
            "unrecovered_files": prep["unrecovered_files"],
            "company_answer": {k2: v for k2, v in prep["company_answer"].items()
                               if k2 not in ("pages", "text")},
            "note": "API 키 값은 저장하지 않는다",
        })

        if args.dry_run:
            (rep_dir / "prompt_user.txt").write_text(prep["user"], encoding="utf-8")
            (rep_dir / "prompt_system.txt").write_text(prep["system"], encoding="utf-8")
            print(f"[dry-run] {rep_dir} — 호출하지 않았다")
            continue

        called_at = now_kst()
        try:
            res = call_model(args.provider, args.model, prep["system"], prep["user"])
            error = None
        except Exception as exc:                  # noqa: BLE001 - 재시도하지 않고 기록한다
            res = None
            error = f"{type(exc).__name__}: {exc}"
        finished_at = now_kst()

        write_json(rep_dir / "response_raw.json", {
            "run_id": run_id, "called_at_kst": called_at, "finished_at_kst": finished_at,
            "provider": args.provider, "model_requested": args.model,
            "error": error,
            "response": res,
        })
        if res is None:
            failures += 1
            write_json(rep_dir / "run_log.json", {
                "run_id": run_id, "status": "call_failed", "error": error,
                "called_at_kst": called_at, "finished_at_kst": finished_at,
                "detail": "자동 재시도하지 않았다",
            })
            print(f"호출 실패({run_id}): {error}", file=sys.stderr)
            continue

        doc, stats, issues = _rescore_one(
            af, rep_dir, prep["cases"], stage, args.framework, run_id,
            res["model"], data_source, set(prep["unrecovered_files"]),
            prep["company_answer"]["file_name"],
        )
        common_docs.append(doc)
        write_json(rep_dir / "run_log.json", {
            "run_id": run_id, "status": "ok", "stage": stage,
            "called_at_kst": called_at, "finished_at_kst": finished_at,
            "model_requested": args.model, "model_returned": res["model"],
            "prompt_tokens": res["prompt_tokens"],
            "completion_tokens": res["completion_tokens"],
            "total_tokens": res["total_tokens"],
            "input_chars": prep["input_chars"],
            "input_tokens_estimate": prep["input_tokens_estimate"],
            "token_estimate_method": prep["token_estimate_method"],
            "parse": stats,
            "validate_issues": issues,
            "answer_format": answer_format_record(af),
        })
        if issues:
            failures += 1

    if common_docs:
        write_json(out_root / base_run_id / "repeat_summary.json", {
            "stage": stage, "repeats": len(common_docs),
            **count_decision_flips(common_docs),
        })
    print(f"저장: {out_root / base_run_id}")
    return 1 if failures else 0


def _rescore_one(af, rep_dir: Path, cases, stage, framework, run_id, model_id,
                 data_source, unrecovered, company_answer_file, *,
                 adapter_extra: dict[str, Any] | None = None,
                 notice: str | None = None,
                 system: str | None = None):
    """한 반복의 저장된 응답 → 공통 형식 문서. 검증 결과까지 돌려준다."""
    saved = read_json(rep_dir / "response_raw.json")
    res = saved.get("response") or {}
    text = res.get("text") or ""
    parsed, parse_error = parse_model_output(text)
    raw_rel = repo_relpath(rep_dir / "response_raw.json")
    doc, stats = to_common_document(
        af, parsed=parsed, parse_error=parse_error, cases=cases, stage=stage,
        framework=framework, run_id=run_id, model_id=model_id,
        data_source=data_source,
        source_ref={"path": raw_rel, "sha256": sha256_file(rep_dir / "response_raw.json")},
        unrecovered=unrecovered, company_answer_file=company_answer_file,
        adapter_extra=adapter_extra, notice=notice, system=system,
    )
    expected = tuple(c["qid"] for c in cases)
    issues = af.validate_document(doc, expected)
    write_json(rep_dir / f"common_v1_{stage}.json", doc)
    if issues:
        write_json(rep_dir / "validate_issues.json", issues)
    return doc, stats, issues


def run_rescore(args) -> int:
    """저장된 response_raw.json 만으로 공통 형식을 다시 만든다(호출 없음)."""
    af = load_answer_format(args.answer_format, git_ref=args.answer_format_ref)
    root = Path(args.rescore_only)
    reps = sorted(p for p in root.glob("rep_*") if (p / "response_raw.json").is_file())
    if not reps:
        print(f"{root}: rep_*/response_raw.json 이 없다", file=sys.stderr)
        return 2
    cases = load_cases(args.cases)
    docs, failures = [], 0
    for rep in reps:
        req = read_json(rep / "request.json")
        saved = read_json(rep / "response_raw.json")
        stage = req["stage"]
        model_id = ((saved.get("response") or {}).get("model")
                    or req.get("model_requested") or "unknown")
        doc, stats, issues = _rescore_one(
            af, rep, cases, stage, req.get("framework", "rba42"), req["run_id"],
            model_id, DATA_SOURCE[stage], set(req.get("unrecovered_files") or []),
            (req.get("company_answer") or {}).get("file_name", ""),
        )
        docs.append(doc)
        write_json(rep / "rescore_log.json", {
            "run_id": req["run_id"], "rescored_at_kst": now_kst(),
            "parse": stats, "validate_issues": issues,
            "answer_format": answer_format_record(af),
        })
        if issues:
            failures += 1
        print("%s: rows=%d missing=%d unparsed=%d issues=%d"
              % (rep.name, stats["rows"], len(stats["missing_qid"]),
                 stats["detail_counts"].get("unparsed", 0), len(issues)))
    write_json(root / "repeat_summary.json", {
        "rescored_at_kst": now_kst(), **count_decision_flips(docs)})
    return 1 if failures else 0


# ── 수동 채팅 실행: 내보내기·가져오기 ──────────────────────────────────────
def build_paste_input(system: str, user: str) -> str:
    """채팅 창에 붙일 한 덩어리. system·user 는 API 경로와 **같은 문자열**이다."""
    return f"{MANUAL_SYSTEM_HEADER}\n{system}\n\n{MANUAL_USER_HEADER}\n{user}"


def write_text_exact(path: Path, text: str) -> None:
    """줄바꿈을 바꾸지 않고 UTF-8 로 쓴다(Windows 의 \\n→\\r\\n 변환을 막는다)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def model_slug(name: str) -> str:
    """meta.txt 의 모델 이름 → 소문자·하이픈 (예: 'Claude Opus 4.1' → 'claude-opus-4-1')."""
    out, prev_dash = [], False
    for ch in name.strip().lower():
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif not prev_dash:
            out.append("-")
            prev_dash = True
    slug = "".join(out).strip("-")
    if not slug:
        raise ValueError(f"모델 이름에서 식별자를 만들 수 없다: {name!r}")
    return slug


def parse_manual_meta(path: Path) -> dict[str, Any]:
    """meta.txt(`키: 값` 줄)를 읽는다. 필수 키가 없거나 값이 틀리면 실패한다."""
    if not path.is_file():
        raise ValueError(f"{path.name} 이 없다 — 모델 이름·실행 시각·이어받음을 적어야 한다")
    raw = path.read_text(encoding="utf-8-sig")
    values: dict[str, str] = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        sep = ":" if ":" in line else ("：" if "：" in line else None)
        if sep is None:
            continue
        key, value = line.split(sep, 1)
        values[key.strip()] = value.strip()
    missing = [k for k in MANUAL_META_KEYS if not values.get(k)]
    if missing:
        raise ValueError(f"{path.name} 에 빠진 줄: {', '.join(missing)}")
    cont = values["이어받음"]
    if cont not in MANUAL_CONTINUED_VALUES:
        raise ValueError(f"{path.name} 의 '이어받음' 은 '예' 또는 '아니오' 여야 한다: {cont!r}")
    return {
        "model": values["모델"],
        "run_at": values["실행 시각"],
        "continued": MANUAL_CONTINUED_VALUES[cont],
        "raw": raw,
        "sha256": sha256_file(path),
    }


def run_manual_export(args) -> int:
    """API 를 부르지 않고, API 경로에서 보낼 system·user 를 그대로 붙여넣기 파일로 쓴다."""
    af = load_answer_format(args.answer_format, git_ref=args.answer_format_ref)
    prep = build_inputs(args)
    stage = args.stage
    out_root = Path(args.out)
    paste = build_paste_input(prep["system"], prep["user"])
    paste_bytes = paste.encode("utf-8")

    targets = [out_root / MANUAL_DIR_FMT.format(label=args.manual_label, stage=stage, k=k)
               for k in range(1, args.repeats + 1)]
    for d in targets:                       # 사람이 넣은 응답을 덮어쓰지 않는다
        for name in (MANUAL_RESPONSE_FILE, MANUAL_META_FILE, "response_raw.json"):
            if (d / name).exists():
                raise RuntimeError(f"{d / name} 이 이미 있다 — 수동 실행 결과를 덮어쓰지 않는다")

    written = []
    for k, d in enumerate(targets, start=1):
        write_text_exact(d / MANUAL_PASTE_FILE, paste)
        write_json(d / "request.json", {
            "run_id": d.name, "stage": stage, "framework": args.framework,
            "provider": "manual", "model_requested": None,
            "temperature": None, "temperature_note": MANUAL_TEMPERATURE_NOTE,
            "prompt_file": prep["prompt"]["path"],
            "prompt_sha256": prep["prompt"]["sha256"],
            "cases_file": repo_relpath(args.cases),
            "cases_sha256": sha256_file(args.cases),
            "pipeline_json": str(Path(args.pipeline_json).resolve()),
            "question_count": len(prep["cases"]),
            "system_sha256": sha256_bytes(prep["system"].encode("utf-8")),
            "user_sha256": sha256_bytes(prep["user"].encode("utf-8")),
            "paste_input_file": MANUAL_PASTE_FILE,
            "paste_input_sha256": sha256_bytes(paste_bytes),
            "paste_input_chars": len(paste),
            "paste_format": (f"{MANUAL_SYSTEM_HEADER}\\n<system>\\n\\n"
                             f"{MANUAL_USER_HEADER}\\n<user>"),
            "input_chars": prep["input_chars"],
            "input_tokens_estimate": prep["input_tokens_estimate"],
            "token_estimate_method": prep["token_estimate_method"],
            "answer_format": answer_format_record(af),
            "dataset_tag": DATASET_TAG,
            "material_kind": MATERIAL_KIND,
            "data_source": DATA_SOURCE[stage],
            "blocks": prep["blocks"],
            "page_methods": prep["page_methods"],
            "unrecovered_files": prep["unrecovered_files"],
            "company_answer": {k2: v for k2, v in prep["company_answer"].items()
                               if k2 not in ("pages", "text")},
            "exported_at_kst": now_kst(),
            "note": "API 를 부르지 않았다. 사람이 claude.ai 채팅에 붙여 넣어 실행한다",
        })
        written.append({"run_id": d.name, "dir": str(d),
                        "paste_input_sha256": sha256_file(d / MANUAL_PASTE_FILE)})
        print(f"[manual export] {d}")

    shas = {w["paste_input_sha256"] for w in written}
    if len(shas) != 1:                      # 같은 단계의 반복은 같은 입력이어야 한다
        raise RuntimeError(f"같은 단계의 paste_input.txt 가 서로 다르다: {sorted(shas)}")
    return 0


def run_manual_import(args) -> int:
    """사람이 넣은 response.txt·meta.txt → response_raw.json → common_v1_<stage>.json."""
    af = load_answer_format(args.answer_format, git_ref=args.answer_format_ref)
    d = Path(args.import_dir)
    req_path = d / "request.json"
    if not req_path.is_file():
        raise ValueError(f"{req_path} 가 없다 — 먼저 --provider manual 로 내보내야 한다")
    req = read_json(req_path)
    if req.get("provider") != "manual":
        raise ValueError(f"{req_path} 는 수동 실행 폴더가 아니다(provider={req.get('provider')!r})")
    meta = parse_manual_meta(d / MANUAL_META_FILE)
    system_name = f"baseline_{model_slug(meta['model'])}"   # 실패하면 아무것도 쓰기 전에 멈춘다
    resp_path = d / MANUAL_RESPONSE_FILE
    if not resp_path.is_file():
        raise ValueError(f"{resp_path.name} 이 없다")
    # BOM 만 벗긴다(메모장 저장 흔적). 응답 내용은 고치지 않는다.
    text = resp_path.read_text(encoding="utf-8-sig")

    stage = req["stage"]
    run_id = d.name
    write_json(d / "response_raw.json", {
        "run_id": run_id, "provider": "manual",
        "model_requested": None, "called_at_kst": meta["run_at"],
        "finished_at_kst": None,
        "temperature": None, "temperature_note": MANUAL_TEMPERATURE_NOTE,
        "continued": meta["continued"],
        "response_txt_sha256": sha256_file(resp_path),
        "meta_txt_sha256": meta["sha256"], "meta_txt": meta["raw"],
        "error": None,
        "response": {
            "text": text, "model": meta["model"],
            "prompt_tokens": None, "completion_tokens": None, "total_tokens": None,
            "response_id": None, "raw": None,
        },
        "imported_at_kst": now_kst(),
    })
    cases = load_cases(args.cases)
    adapter_extra = {"provider": "manual", "entry": "claude.ai chat",
                     "paste_input_sha256": req.get("paste_input_sha256")}
    # meta.system 은 baseline_<슬러그>, meta.model 은 meta.txt 값 그대로.
    stale = d / "validate_issues.json"      # 다시 가져올 때 이전 실패 목록이 남지 않게
    if stale.exists():
        stale.unlink()
    doc, stats, issues = _rescore_one(
        af, d, cases, stage, req.get("framework", "rba42"), run_id,
        meta["model"], DATA_SOURCE[stage], set(req.get("unrecovered_files") or []),
        (req.get("company_answer") or {}).get("file_name", ""),
        adapter_extra=adapter_extra, notice=MANUAL_NOTICE, system=system_name,
    )
    write_json(d / "run_log.json", {
        "run_id": run_id, "status": "imported", "stage": stage, "provider": "manual",
        "model_returned": meta["model"], "system": doc["meta"]["system"],
        "run_at": meta["run_at"], "continued": meta["continued"],
        "prompt_tokens": None, "completion_tokens": None, "total_tokens": None,
        "token_note": "채팅이라 토큰 수를 알 수 없다",
        "temperature": None, "temperature_note": MANUAL_TEMPERATURE_NOTE,
        "input_chars": req.get("input_chars"),
        "input_tokens_estimate": req.get("input_tokens_estimate"),
        "paste_input_sha256": req.get("paste_input_sha256"),
        "parse": stats, "validate_issues": issues,
        "answer_format": answer_format_record(af),
        "imported_at_kst": now_kst(),
    })
    print("%s: rows=%d missing=%d unparsed=%d issues=%d continued=%s"
          % (run_id, stats["rows"], len(stats["missing_qid"]),
             stats["detail_counts"].get("unparsed", 0), len(issues), meta["continued"]))
    return 1 if issues else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="일반 문서 AI 대조군 실행기 (작업지시서 B §4.2)")
    p.add_argument("--pipeline-json", help="단계 pipeline.json 경로")
    p.add_argument("--stage", choices=sorted(DATA_SOURCE), help="initial | followup")
    p.add_argument("--framework", default="rba42")
    p.add_argument("--model", help="모델 식별자")
    p.add_argument("--provider", default="openai", choices=("openai", "anthropic", "manual"),
                   help="manual: API 를 부르지 않고 채팅에 붙여 넣을 입력 파일만 쓴다")
    p.add_argument("--import", dest="import_dir", default=None,
                   help="(--provider manual) 사람이 response.txt·meta.txt 를 넣은 폴더를 가져온다")
    p.add_argument("--manual-label", default="claude",
                   help="수동 실행 폴더 이름의 라벨: MANUAL_<label>_<stage>_rep<k>")
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--out", help="출력 루트")
    p.add_argument("--run-id", default=None)
    p.add_argument("--cases", default=str(REPO_ROOT / DEFAULT_CASES))
    p.add_argument("--prompt", default=str(REPO_ROOT / DEFAULT_PROMPT))
    p.add_argument("--answer-format", default=None,
                   help="A 의 answer_format.py 또는 패키지 루트 (환경변수 "
                        "ESGENIE_EVAL_ANSWER_FORMAT 도 쓴다)")
    p.add_argument("--answer-format-ref", default=ANSWER_FORMAT_GIT_REF,
                   help="A 모듈을 꺼낼 git ref. 기본은 고정 SHA(v1.1)")
    p.add_argument("--max-input-tokens", type=int, default=None,
                   help="추정 입력 토큰이 이 값을 넘으면 중단한다. 주지 않으면 "
                        "MODEL_CONTEXT_LIMITS 표에서 찾고, 표에 없으면 실행을 거부한다")
    p.add_argument("--dry-run", action="store_true", help="호출하지 않고 입력만 저장한다")
    p.add_argument("--rescore-only", default=None, help="<out>/<run_id> 폴더에서 재채점만")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.rescore_only:
        return run_rescore(args)
    if args.import_dir:
        if args.provider != "manual":
            print("--import 는 --provider manual 과 함께만 쓴다", file=sys.stderr)
            return 2
        try:
            return run_manual_import(args)
        except (RuntimeError, ValueError, UnicodeDecodeError) as exc:
            print("가져오기 중단: %s" % exc, file=sys.stderr)
            return 2
    required = ("pipeline_json", "stage", "out") if args.provider == "manual" \
        else ("pipeline_json", "stage", "model", "out")
    missing = [n for n in required if not getattr(args, n)]
    if missing:
        print("필수 인자 누락: " + ", ".join("--" + m.replace("_", "-") for m in missing),
              file=sys.stderr)
        return 2
    if args.repeats < 1:
        print("--repeats 는 1 이상", file=sys.stderr)
        return 2
    try:
        if args.provider == "manual":
            return run_manual_export(args)
        return run_live(args)
    except (RuntimeError, ValueError) as exc:
        print("중단: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
