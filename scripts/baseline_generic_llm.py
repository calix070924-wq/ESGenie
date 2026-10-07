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
* 공통 답안 형식 문서는 내보내기 전에 A 의 `validate_document` 로 검증한다.

출력 (`<out>/<run_id>/rep_<k>/`)
-------------------------------
* `request.json`        프롬프트 sha256·모델·파라미터·입력 길이 (키 값 없음)
* `response_raw.json`   응답 원문 전체
* `common_v1_<stage>.json`  A 공통 답안 형식 v1 (JSON 객체 1개 = 한 시스템·한 실행·한 단계)
* `run_log.json`        토큰·시각(KST)·파싱 결과·검증 결과

사용 예
-------
    python scripts/baseline_generic_llm.py \
        --pipeline-json <run>/initial/pipeline.json --stage initial \
        --framework rba42 --model gpt-4.1-mini --provider openai \
        --repeats 5 --out outputs/eval/baseline/<run_id>

    # 저장한 응답만으로 공통 형식을 다시 만든다(호출 없음)
    python scripts/baseline_generic_llm.py --rescore-only outputs/eval/baseline/<run_id>
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

#: 단계별 `meta.data_source`. A 확인 대기 — 바뀌면 이 상수만 고친다.
DATA_SOURCE = {
    "initial": "촬영증빙_17건",
    "followup": "촬영증빙_17건+보완_1건",
}

#: 회사 답변 구획의 제목·표지. 증빙 블록과 표지를 다르게 둔다.
COMPANY_SECTION_TITLE = "회사 자가 답변 초안 — 검증된 증빙 아님"
COMPANY_NOTICE = "05는 회사 자가 답변 원문, 증빙 아님"
DOC_OPEN, DOC_CLOSE = "[문서 시작]", "[문서 끝]"
CA_OPEN, CA_CLOSE = "[회사 답변 시작]", "[회사 답변 끝]"
PAGE_UNKNOWN = "미상"

#: 모델이 내는 판정값 → 공통 형식 판정. 구조로만 옮긴다(단서 문구로 바꾸지 않는다).
MODEL_DECISIONS = ("answer", "hold", "not_applicable")

#: 입력 토큰 추정 계수. tiktoken 은 requirements.txt 에 없어 쓰지 않는다(새 의존성 금지).
#: 한국어 혼합 텍스트에서 보수적으로 잡은 값이고, 호출 뒤에는 API 가 보고한 prompt_tokens 를 기록한다.
TOKEN_CHARS_PER_TOKEN = 2.0
TOKEN_ESTIMATE_METHOD = (
    "chars/%.1f (tiktoken 미설치·requirements.txt 에 없음. 호출 후 API prompt_tokens 로 대체 기록)"
    % TOKEN_CHARS_PER_TOKEN
)


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
def load_answer_format(explicit: str | None = None):
    """A 가 소유하는 `answer_format` 모듈을 찾는다.

    해석 순서
      1. `--answer-format <경로>` (파일 또는 패키지 루트)
      2. 환경변수 `ESGENIE_EVAL_ANSWER_FORMAT`
      3. 저장소의 `esgenie.eval.answer_format` (A 브랜치가 병합되면 여기 있다)

    못 찾으면 예외를 올린다. 검증을 건너뛰지 않는다.
    """
    candidates = [c for c in (explicit, os.getenv("ESGENIE_EVAL_ANSWER_FORMAT")) if c]
    tried: list[str] = []
    for cand in candidates:
        p = Path(cand)
        if p.is_dir():
            for sub in (p / "esgenie" / "eval" / "answer_format.py", p / "answer_format.py"):
                if sub.is_file():
                    p = sub
                    break
        if p.is_file():
            spec = importlib.util.spec_from_file_location("_b3_answer_format", p)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                mod.__source_path__ = str(p)       # type: ignore[attr-defined]
                mod.__source_sha256__ = sha256_file(p)  # type: ignore[attr-defined]
                return mod
        tried.append(str(p))
    if candidates:
        raise FileNotFoundError("answer_format 를 찾을 수 없다: " + ", ".join(tried))
    mod = importlib.import_module("esgenie.eval.answer_format")
    src = Path(mod.__file__)  # type: ignore[arg-type]
    mod.__source_path__ = str(src)             # type: ignore[attr-defined]
    mod.__source_sha256__ = sha256_file(src)   # type: ignore[attr-defined]
    return mod


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


def _row_decision(row: dict[str, Any], evidence: list[dict[str, Any]]) -> tuple[str, str]:
    """구조만 보고 공통 판정을 정한다. 단서 문구로 바꾸지 않는다."""
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
) -> tuple[dict[str, Any], dict[str, Any]]:
    """저장한 응답 → A 공통 답안 형식 v1 문서. (문서, 집계) 를 돌려준다."""
    by_qid: dict[str, list[dict[str, Any]]] = {}
    if parsed:
        for row in parsed.get("answers") or []:
            if isinstance(row, dict) and isinstance(row.get("qid"), str):
                by_qid.setdefault(row["qid"], []).append(row)

    answers: list[dict[str, Any]] = []
    stats = {
        "rows": 0, "confirmed": 0, "unverified_submitted": 0, "hold": 0,
        "not_applicable": 0, "unparsed": 0,
        "missing_qid": [], "duplicate_qid": [], "unknown_qid": [],
        "cited_company_answer": 0, "evidence_page_null": 0,
        "parse_error": parse_error,
    }

    for case in cases:
        qid = case["qid"]
        rows = by_qid.get(qid, [])
        if not rows:
            stats["missing_qid"].append(qid)
            answers.append(af.new_answer(
                qid=qid, decision="unparsed",
                decision_basis="모델 출력에 이 qid 의 응답이 없다",
                value=None, unit="", evidence=[],
                source={"raw": None, "parse_error": parse_error or "missing_qid"},
            ))
            stats["unparsed"] += 1
            stats["rows"] += 1
            continue
        if len(rows) > 1:
            stats["duplicate_qid"].append(qid)
        row = rows[0]                       # 중복은 첫 행만 쓰고 중복 사실을 남긴다
        evidence = _clean_evidence(row.get("evidence"), unrecovered)
        decision, basis = _row_decision(row, evidence)
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
        answers.append(af.new_answer(
            qid=qid, decision=decision, decision_basis=basis,
            value=value, unit=unit if isinstance(unit, str) else "",
            value_text=row.get("answer_text") if isinstance(row.get("answer_text"), str) else None,
            boundary=boundary, evidence=evidence,
            source={"raw": json.dumps(row, ensure_ascii=False)},
            notes=notes if isinstance(notes, str) and notes.strip() else None,
        ))
        stats[decision] = stats.get(decision, 0) + 1
        stats["rows"] += 1
        if any(e["file_name"] == company_answer_file for e in evidence):
            stats["cited_company_answer"] += 1
        stats["evidence_page_null"] += sum(1 for e in evidence if e["page"] is None)

    known = {c["qid"] for c in cases}
    stats["unknown_qid"] = sorted(set(by_qid) - known)

    doc = af.new_document(
        system=f"baseline_{model_id}",
        run_id=run_id,
        stage=stage,
        framework=framework,
        data_source=data_source,
        model=model_id,
        produced_at=produced_at or now_kst(),
        adapter={"name": "scripts/baseline_generic_llm.py", "version": "1"},
        source_ref=source_ref,
        notice=COMPANY_NOTICE,
        answers=answers,
    )
    return doc, stats


def count_decision_flips(docs: list[dict[str, Any]]) -> dict[str, Any]:
    """반복 간 판정이 바뀐 행 수. 문서는 공통 형식 문서 목록이다."""
    if len(docs) < 2:
        return {"repeats": len(docs), "changed_rows": 0, "changed_qids": []}
    per_qid: dict[str, set[str]] = {}
    for doc in docs:
        for a in doc.get("answers") or []:
            per_qid.setdefault(str(a.get("qid")), set()).add(str(a.get("decision")))
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


def run_live(args) -> int:
    af = load_answer_format(args.answer_format)
    prep = build_inputs(args)
    stage = args.stage
    data_source = DATA_SOURCE[stage]

    if args.max_input_tokens and prep["input_tokens_estimate"] > args.max_input_tokens:
        out = Path(args.out) / (args.run_id or "aborted")
        write_json(out / "run_log.json", {
            "status": "aborted_context_limit",
            "input_chars": prep["input_chars"],
            "input_tokens_estimate": prep["input_tokens_estimate"],
            "token_estimate_method": prep["token_estimate_method"],
            "max_input_tokens": args.max_input_tokens,
            "detail": "모델 컨텍스트를 넘었다. 자동 분할하지 않고 중단했다.",
            "aborted_at": now_kst(),
        })
        print("중단: 입력 추정 토큰 %d > 한도 %d (자동 분할하지 않는다)"
              % (prep["input_tokens_estimate"], args.max_input_tokens), file=sys.stderr)
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
            "answer_format": {"path": getattr(af, "__source_path__", None),
                              "sha256": getattr(af, "__source_sha256__", None),
                              "format_version": af.FORMAT_VERSION},
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
                 data_source, unrecovered, company_answer_file):
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
    )
    expected = tuple(c["qid"] for c in cases)
    issues = af.validate_document(doc, expected)
    write_json(rep_dir / f"common_v1_{stage}.json", doc)
    if issues:
        write_json(rep_dir / "validate_issues.json", issues)
    return doc, stats, issues


def run_rescore(args) -> int:
    """저장된 response_raw.json 만으로 공통 형식을 다시 만든다(호출 없음)."""
    af = load_answer_format(args.answer_format)
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
            "answer_format": {"path": getattr(af, "__source_path__", None),
                              "sha256": getattr(af, "__source_sha256__", None),
                              "format_version": af.FORMAT_VERSION},
        })
        if issues:
            failures += 1
        print(f"{rep.name}: rows={stats['rows']} unparsed={stats['unparsed']} "
              f"issues={len(issues)}")
    write_json(root / "repeat_summary.json", {
        "rescored_at_kst": now_kst(), **count_decision_flips(docs)})
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="일반 문서 AI 대조군 실행기 (작업지시서 B §4.2)")
    p.add_argument("--pipeline-json", help="단계 pipeline.json 경로")
    p.add_argument("--stage", choices=sorted(DATA_SOURCE), help="initial | followup")
    p.add_argument("--framework", default="rba42")
    p.add_argument("--model", help="모델 식별자")
    p.add_argument("--provider", default="openai", choices=("openai", "anthropic"))
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--out", help="출력 루트")
    p.add_argument("--run-id", default=None)
    p.add_argument("--cases", default=str(REPO_ROOT / DEFAULT_CASES))
    p.add_argument("--prompt", default=str(REPO_ROOT / DEFAULT_PROMPT))
    p.add_argument("--answer-format", default=None,
                   help="A 의 answer_format.py 또는 패키지 루트 (환경변수 "
                        "ESGENIE_EVAL_ANSWER_FORMAT 도 쓴다)")
    p.add_argument("--max-input-tokens", type=int, default=0,
                   help="추정 입력 토큰이 이 값을 넘으면 중단한다(0=검사 안 함)")
    p.add_argument("--dry-run", action="store_true", help="호출하지 않고 입력만 저장한다")
    p.add_argument("--rescore-only", default=None, help="<out>/<run_id> 폴더에서 재채점만")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.rescore_only:
        return run_rescore(args)
    missing = [n for n in ("pipeline_json", "stage", "model", "out")
               if not getattr(args, n)]
    if missing:
        print("필수 인자 누락: " + ", ".join("--" + m.replace("_", "-") for m in missing),
              file=sys.stderr)
        return 2
    if args.repeats < 1:
        print("--repeats 는 1 이상", file=sys.stderr)
        return 2
    return run_live(args)


if __name__ == "__main__":
    sys.exit(main())
