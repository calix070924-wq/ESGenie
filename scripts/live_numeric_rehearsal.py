#!/usr/bin/env python3
"""수치 인식 수정(2026-09-29)의 실제 AI 재검증 도구 — 코드 경로·서버 주소·저장 경로를 명시적으로 받는다.

기존 `serve_hanwool_live_rehearsal.py`·`rehearse_hanwool_web.py`는 PR #65 작업 폴더와 과거
결과 폴더를 고정 경로로 쓴다. 그대로 실행하면 고친 브랜치 대신 PR #65 코드를 검증하거나
과거 기록을 덮어쓸 수 있어, 이 도구는 모든 경로를 인수로 받고 다음을 거부한다.
  · 저장 경로가 기존 리허설 폴더(output/rehearsal/…) 안이거나 이미 내용이 있는 경우
  · 실제로 import된 esgenie가 --code-path 밖에서 온 경우
  · 입력 PDF 해시가 원본 구성 목록과 다르거나, 촬영 배경용 요청서(00_)가 섞인 경우

하위 명령:
  core   코어 브랜치 단독 — esgenie.pipeline.run + 응답서 생성을 직접 호출(웹 계층 없음).
  serve  웹 통합 검증 서버 — --code-path의 esgenie.web을 띄우고 실행마다 관측 기록.
  drive  serve로 띄운 서버에 12건 업로드→분석→내려받기, 13건 보완→재분석→내려받기.

기록: 불러온 코드 경로, 커밋 SHA, 미커밋 변경, 입력 해시, 모델·공급자, 캐시 모드·적중,
Upstage 요청·성공·실패 수, LLM 실호출·성공·실패 수, 실행 시간. 비밀키 값은 남기지 않는다
(설정 여부만 불리언으로 기록).
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

PROTECTED = [Path("/Users/heojeongmin/Documents/Claude/Projects/ESGenie/output/rehearsal").resolve()]
INITIAL_DIR = "01_처음업로드_12건"
FOLLOWUP_DIR = "02_보완할때추가_1건"
DONE = {"complete", "done"}
MANIFEST = "00_촬영안내_업로드하지않음/구성_검산_목록.json"


def json_default(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (Path, set)):
        return sorted(value) if isinstance(value, set) else str(value)
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return str(value)


def dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def prepare_run_dir(run_dir: Path, *, allow_existing: bool = False) -> Path:
    run_dir = run_dir.resolve()
    for p in PROTECTED:
        if run_dir == p or p in run_dir.parents:
            raise SystemExit(f"기존 리허설 경로에는 저장하지 않는다: {run_dir}")
    if run_dir.exists() and any(run_dir.iterdir()) and not allow_existing:
        raise SystemExit(f"저장 경로가 비어 있지 않다(덮어쓰기 금지): {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def code_state(code_path: Path) -> dict:
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=code_path, text=True).strip()
    status = git("status", "--porcelain")
    return {"code_path": str(code_path), "commit": git("rev-parse", "HEAD"),
            "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "log": git("log", "--oneline", "-5").splitlines(),
            "dirty": bool(status), "dirty_files": status.splitlines()}


def input_files(pack: Path, stage: str) -> tuple[list[dict], dict]:
    """원본 구성 목록과 해시를 대조한 입력 목록. 요청서(context_only)는 넣지 않는다."""
    manifest = json.loads((pack / MANIFEST).read_text(encoding="utf-8"))
    by_file = {f["file"]: f for f in manifest["files"]}
    dirs = [INITIAL_DIR] + ([FOLLOWUP_DIR] if stage == "followup" else [])
    out = []
    for d in dirs:
        for path in sorted((pack / d).glob("*.pdf")):
            rel = f"{d}/{path.name}"
            entry = by_file.get(rel)
            if entry is None or entry["role"] == "context_only":
                raise SystemExit(f"구성 목록에 없는 입력 또는 요청서: {rel}")
            digest = sha256(path)
            if digest != entry["sha256"]:
                raise SystemExit(f"입력 해시 불일치: {rel}")
            out.append({"file": rel, "path": str(path), "name": path.name, "role": entry["role"],
                        "sha256": digest, "manifest_sha256_match": True})
    expected = manifest["counts"]["initial_upload"] + (manifest["counts"]["followup_evidence"] if stage == "followup" else 0)
    if len(out) != expected or sum(f["role"] == "company_answer" for f in out) != 1:
        raise SystemExit(f"입력 구성 불일치: {len(out)}건 (기대 {expected}), 회사 답변 1건 필요")
    return out, {"manifest": str(pack / MANIFEST), "manifest_version": manifest["version"]}


def live_env(cache_dir: Path) -> None:
    os.environ["ESGENIE_FORCE_MOCK"] = "0"
    os.environ["ESGENIE_STRICT"] = "1"
    os.environ["ESGENIE_OCR_CACHE_DIR"] = str(cache_dir / "ocr")
    os.environ["ESGENIE_LLM_CACHE_DIR"] = str(cache_dir / "llm")
    os.environ["ESGENIE_OCR_CACHE"] = "1"
    os.environ["ESGENIE_LLM_CACHE"] = "1"
    os.environ["ESGENIE_OCR_CACHE_REFRESH"] = "0"
    os.environ["ESGENIE_LLM_CACHE_REFRESH"] = "0"


def import_from(code_path: Path, env_file: Path):
    sys.path.insert(0, str(code_path))
    from dotenv import load_dotenv
    load_dotenv(env_file)
    import esgenie
    loaded = Path(esgenie.__file__).resolve()
    if code_path.resolve() not in loaded.parents:
        raise SystemExit(f"esgenie가 --code-path 밖에서 import됨: {loaded}")
    from esgenie.config import SETTINGS
    if SETTINGS.use_mock_llm or SETTINGS.force_mock or not os.getenv("UPSTAGE_API_KEY"):
        raise SystemExit("실제 LLM·OCR 자격 증명이 필요하다 — 모의 실행은 하지 않는다")
    return loaded, SETTINGS


def environment_record(code_path: Path, loaded: Path, settings, cache_dir: Path, extra: dict) -> dict:
    return {"started_at": now(), "pid": os.getpid(), **code_state(code_path),
            "imported_esgenie": str(loaded), "force_mock": settings.force_mock,
            "strict_llm": settings.strict_llm, "model": settings.openai_model,
            "llm_provider": "azure_openai" if settings.azure_openai_endpoint else "openai",
            "ocr_provider": "upstage_document_parse", "ocr_key_present": bool(os.getenv("UPSTAGE_API_KEY")),
            "llm_key_present": bool(os.getenv("OPENAI_API_KEY") or os.getenv("AZURE_OPENAI_ENDPOINT")),
            "cache_dir": str(cache_dir), "cache_files_at_start": count_cache(cache_dir), **extra}


def count_cache(cache_dir: Path) -> dict:
    return {k: len(list((cache_dir / k).glob("*.json"))) if (cache_dir / k).is_dir() else 0 for k in ("ocr", "llm")}


class UpstageCounter:
    """Upstage 호출 수·성공·실패·시간을 관측만 한다(반환값·예외는 그대로 전달)."""

    def __init__(self):
        from esgenie.ssot import ocr_router
        self.router = ocr_router
        self.events: list[dict] = []
        self.originals = {}
        for name in ("_call_upstage_dp_payload", "_call_upstage_dp"):
            self.originals[name] = getattr(ocr_router, name)
            setattr(ocr_router, name, self._wrap(name, self.originals[name]))

    def _wrap(self, name, fn):
        def wrapped(file_path, *a, **kw):
            started = time.monotonic()
            event = {"function": name, "file": Path(str(file_path)).name, "pages": kw.get("pages")}
            try:
                result = fn(file_path, *a, **kw)
                event["status"] = "success"
                return result
            except Exception as exc:
                event["status"] = "failure"
                event["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
                raise
            finally:
                event["seconds"] = round(time.monotonic() - started, 3)
                self.events.append(event)
        return wrapped

    def reset(self):
        self.events.clear()

    def summary(self) -> dict:
        # _call_upstage_dp는 내부에서 _call_upstage_dp_payload를 부를 수 있어 바깥 호출만 센다.
        outer = [e for e in self.events if e["function"] == "_call_upstage_dp_payload" or e.get("pages")]
        return {"requests": len(outer), "successes": sum(e["status"] == "success" for e in outer),
                "failures": sum(e["status"] == "failure" for e in outer), "events": self.events}


class UpstageTape:
    """Upstage 원시 응답(HTTP JSON 본문)을 기록하거나, 기록한 본문으로 재생한다.

    키는 (보낸 문서 바이트 sha256, model, ocr) — 같은 PDF의 1쪽 미리보기와 전체 문서를 가른다.
    재생에서 기록이 없으면 예외로 멈춘다(실호출로 대체하지 않는다). 응답 본문에 키 값은 없다.
    """

    def __init__(self, directory: Path, mode: str):
        import requests
        self.directory, self.mode, self.requests = directory, mode, requests
        self.original = requests.post
        self.events: list[dict] = []
        directory.mkdir(parents=True, exist_ok=True)
        requests.post = self.post

    def _key(self, data, files) -> tuple[str, str]:
        name, payload = files["document"]
        digest = hashlib.sha256(payload).hexdigest()
        key = hashlib.sha256(f"{digest}|{data.get('model')}|{data.get('ocr')}".encode()).hexdigest()[:24]
        return key, name

    def post(self, url, *a, data=None, files=None, **kw):
        if not files or "document" not in files:
            return self.original(url, *a, data=data, files=files, **kw)
        key, name = self._key(data or {}, files)
        path = self.directory / f"{key}.json"
        if self.mode == "replay":
            if not path.exists():
                self.events.append({"file": name, "key": key, "status": "replay_miss"})
                raise RuntimeError(f"replay: Upstage 기록 없음 — 실호출 금지 ({name})")
            record = json.loads(path.read_text(encoding="utf-8"))
            self.events.append({"file": name, "key": key, "status": "replayed"})
            return _TapeResponse(record)
        resp = self.original(url, *a, data=data, files=files, **kw)
        if resp.ok:
            dump(path, {"file": name, "model": (data or {}).get("model"), "ocr": (data or {}).get("ocr"),
                        "status_code": resp.status_code, "recorded_at": now(),
                        "request_id": resp.headers.get("x-request-id") or resp.headers.get("request-id"),
                        "body": resp.json()})
        self.events.append({"file": name, "key": key, "status": "recorded" if resp.ok else f"http_{resp.status_code}"})
        return resp


class _TapeResponse:
    def __init__(self, record: dict):
        self.status_code, self.ok, self._body = record.get("status_code", 200), True, record["body"]
        self.headers = {"x-request-id": record.get("request_id") or ""}

    def json(self):
        return self._body

    def raise_for_status(self):
        return None


def block_network(llm_misses: list[dict]) -> None:
    """재생 전용: 소켓을 막고 LLM 실호출을 캐시 미스 기록으로 바꾼다(캐시 적중만 응답)."""
    import socket

    def blocked(*a, **k):
        raise RuntimeError("replay: network forbidden")

    socket.socket.connect = blocked
    socket.create_connection = blocked
    from openai.resources.chat import completions

    def miss(self, *a, **kw):
        user = next((m["content"] for m in kw.get("messages", []) if m.get("role") == "user"), "")
        llm_misses.append({"user_sha256": hashlib.sha256(user.encode()).hexdigest(), "user_head": user[:160]})
        raise RuntimeError("replay: LLM cache miss — live call forbidden")

    completions.Completions.create = miss


def run_stats(started, llm_cache, ocr_cache, upstage, output, extra) -> dict:
    stats = {"elapsed_seconds": round(time.monotonic() - started, 3), "llm": llm_cache.stats(),
             "llm_response_events": llm_cache.response_events(), "upstage": upstage.summary(), **extra}
    if output is not None:
        hits, misses, mode = ocr_cache.summarize(output.ocr_extractions)
        stats["ocr_vlm_cache"] = {"hits": hits, "misses": misses, "mode": mode}
    return stats


def pipeline_record(output) -> dict:
    return {"ocr_extractions": [item.to_dict() for item in output.ocr_extractions],
            "extraction": output.extraction, "evidence_graph": output.evidence_graph,
            "sections": output.sections, "policy_results": output.policy_results,
            "risk_rows": output.risk_rows, "disclosure": output.disclosure,
            "industry_module_key": output.industry_module_key,
            # 확인 목록과 원장 소비값(DataPoint) — 답변·내보내기와 같은 실행에서 대조한다.
            "review_findings": [f.to_dict() for f in getattr(output, "review_findings", [])],
            "data_points": getattr(output.v15_trace, "data_points", None) if output.v15_trace else None,
            "report_export": getattr(output, "report_export", None)}


# ---- core ------------------------------------------------------------------------

def cmd_core(args) -> None:
    code_path = args.code_path.resolve()
    run_dir = prepare_run_dir(args.run_dir, allow_existing=args.stage == "followup")
    target = run_dir / args.stage
    if target.exists():
        raise SystemExit(f"이미 실행한 단계(덮어쓰기 금지): {target}")
    cache_dir = args.cache_dir.resolve()
    live_env(cache_dir)
    if args.replay_upstage:
        # 소켓을 막으면 임베딩 모델 확인이 실패해 hash-fallback으로 바뀌고 검색 문맥(=LLM 프롬프트)이 달라진다.
        # 로컬에 받아 둔 모델을 그대로 쓰도록 허브 조회를 끈다(import 전에 정해야 한다).
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    files, manifest = input_files(args.pack_dir, args.stage)
    for extra in args.extra_evidence or []:
        # 검증용 가상 변형본만 받는다 — 원본 세트에 섞여 원본으로 오인되지 않게 이름에 표시를 요구한다.
        if "변형본" not in extra.name:
            raise SystemExit(f"추가 증빙은 '변형본' 표시가 있는 검증용 파일만 받는다: {extra}")
        files.append({"file": str(extra), "path": str(extra.resolve()), "name": extra.name,
                      "role": "variant_evidence", "sha256": sha256(extra), "manifest_sha256_match": None})
    loaded, settings = import_from(code_path, args.env_file)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        handlers=[logging.FileHandler(run_dir / f"{args.stage}.log", encoding="utf-8")])
    for name in ["httpx", "httpcore", "openai", "urllib3"]:
        logging.getLogger(name).setLevel(logging.WARNING)
    # 재생: 기록한 Upstage 응답 + LLM 캐시 사본만 쓴다. LLM 미스는 기록하고 strict를 풀어
    # 결정적 단계(추출·원장·답변·내보내기)까지 진행한다 — 미스 본문은 재생 결과로 쓰지 않는다.
    llm_misses: list[dict] = []
    replay = bool(args.replay_upstage)
    # `--live-llm`: Upstage는 기록 재생(새 OCR 호출 없음), LLM은 캐시 적중 + 미스만 실호출 — 생성 프롬프트를
    # 바꾼 경로만 새로 부른다. 네트워크는 막지 않지만 Upstage 요청은 기록 테이프만 쓴다(미스면 예외).
    live_llm = replay and bool(args.live_llm)
    if replay and not live_llm:
        block_network(llm_misses)
    tape = (UpstageTape(args.replay_upstage.resolve(), "replay") if replay
            else UpstageTape(target / "raw_upstage", "record") if args.record_upstage else None)
    from esgenie import llm_cache
    from esgenie.pipeline import run
    from esgenie.run_info import build_run_info
    from esgenie.ssot import ocr_cache
    from esgenie.supplychain import (copy_evidence_pack, export_response_sheet, export_response_sheet_pdf,
                                     parse_saq_claims, respond_from_pipeline)

    exports = target / "exports"
    dump(target / "environment.json", environment_record(code_path, loaded, settings, cache_dir, {
        "mode": ("core_upstage_replay_live_llm" if live_llm else "core_replay" if replay else "core_direct"),
        "stage": args.stage, "inputs": files, **manifest,
        "call": "esgenie.pipeline.run(areas=E,S,G, use_dart=False, profile=sme"
                + (", export_outputs=True, export_report=True" if args.export else "") + ") + "
                "respond_from_pipeline(framework, supplier_claims=parse_saq_claims(company_answer))"
                + (" + export_response_sheet(.xlsx/.pdf)" if args.export else ""),
        "framework": args.framework, "run_id": args.run_id or target.parent.name,
        "upstage_tape": {"mode": tape.mode, "directory": str(tape.directory)} if tape else None}))
    evidence = {f["name"]: f["path"] for f in files if f["role"] != "company_answer"}
    company = [f["path"] for f in files if f["role"] == "company_answer"]
    upstage = UpstageCounter()
    llm_cache.reset_stats()
    # llm_cache.stats()는 프로세스 누적값이다 — 실행 시작 스냅샷을 떠 두고 차이로 센다.
    llm_stats_start = dict(llm_cache.stats())
    started, output = time.monotonic(), None
    settings.strict_llm = not replay or live_llm
    try:
        claims = parse_saq_claims(company)
        output = run(f"hanwool-core-{args.stage}", areas=["E", "S", "G"], corp_name=args.company,
                     industry=args.industry, report_year=args.year, use_dart=False,
                     evidence_files=evidence, demo_greenwash=False, save_traces=False,
                     export_outputs=args.export, export_report=args.export, export_root=str(exports),
                     profile="sme")
        dump(target / "pipeline.json", pipeline_record(output))
        sheet = respond_from_pipeline(output, args.framework, supplier_claims=claims, enable_drafts=False)
        sheet.corp_name = args.company
        # 실행 출처(B-2) — 어느 코드로, 신규 처리인지 캐시 재생인지. 내보내기와 같은 값을 쓴다.
        info = build_run_info(llm_stats_end=llm_cache.stats(), llm_stats_start=llm_stats_start,
                              ocr_extractions=output.ocr_extractions, upstage_replay=replay,
                              code_path=code_path, timings=getattr(output, "timings", None))
        dump(target / "result.json", {"sheet": sheet.to_dict(), "generated_at": now(),
                                      "run_info": info})
        if args.export:
            sheet_dir = exports / "response_sheet"
            copy_evidence_pack(sheet, sheet_dir, evidence)
            paths = {"xlsx": export_response_sheet(sheet, sheet_dir, run_info=info),
                     "pdf": export_response_sheet_pdf(sheet, sheet_dir, evidence_base_dir=sheet_dir,
                                                      run_info=info)}
            for framework in args.also_framework or []:
                other = respond_from_pipeline(output, framework, supplier_claims=claims, enable_drafts=False)
                other.corp_name = args.company
                other_dir = exports / f"response_sheet_{framework}"
                copy_evidence_pack(other, other_dir, evidence)
                dump(target / f"result_{framework}.json", {"sheet": other.to_dict(),
                                                           "generated_at": now(), "run_info": info})
                paths[framework] = {"xlsx": export_response_sheet(other, other_dir, run_info=info),
                                    "pdf": export_response_sheet_pdf(other, other_dir,
                                                                     evidence_base_dir=other_dir,
                                                                     run_info=info)}
            sections = {area: {"final_text": v.final_text, "used_mock_llm": getattr(v, "used_mock_llm", None),
                               "final_score": v.final_score, "converged": v.converged,
                               "hitl_required": v.hitl_required}
                        for area, v in output.sections.items()}
            dump(target / "report_sections.json", sections)
            dump(target / "export_paths.json", {"pipeline": output.export_paths, "response_sheet": paths,
                                                "report_export": output.report_export})
    except Exception:
        (target / "failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
        raise
    finally:
        dump(target / "run_stats.json", run_stats(started, llm_cache, ocr_cache, upstage, output, {
            "stage": args.stage, "document_count": len(files), "evidence_count": len(evidence),
            "company_answer_count": len(company), "cache_files_at_end": count_cache(cache_dir),
            "upstage_tape": tape.events if tape else None,
            "replay_llm_misses": llm_misses if replay and not live_llm else None}))
    print(json.dumps({"stage": args.stage, "run_dir": str(target),
                      "elapsed": round(time.monotonic() - started, 1)}, ensure_ascii=False))


# ---- serve / drive -----------------------------------------------------------------

def cmd_serve(args) -> None:
    code_path = args.code_path.resolve()
    run_dir = prepare_run_dir(args.run_dir)
    cache_dir = args.cache_dir.resolve()
    live_env(cache_dir)
    loaded, settings = import_from(code_path, args.env_file)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        handlers=[logging.FileHandler(run_dir / "server.log", encoding="utf-8")])
    for name in ["httpx", "httpcore", "openai", "urllib3"]:
        logging.getLogger(name).setLevel(logging.WARNING)
    from esgenie import llm_cache, pipeline
    from esgenie.ssot import ocr_cache
    from esgenie.web.app import create_app
    from esgenie.web.engine import run_analysis
    import uvicorn

    dump(run_dir / "environment.json", environment_record(code_path, loaded, settings, cache_dir, {
        "mode": "web_integration", "host": args.host, "port": args.port,
        "data_root": str(args.data_root or run_dir / "web_workspace"),
        "observer": "unmodified esgenie.web.engine.run_analysis; PipelineOutput captured without changes"}))
    upstage = UpstageCounter()

    def observed_runner(project, directory):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = run_dir / "runs" / f"{stamp}_rev{project['input_revision']}"
        dump(target / "input_project.json", project)
        upstage.reset()
        llm_cache.reset_stats()
        started, captured, original = time.monotonic(), [], pipeline.run

        def observe(*a, **kw):
            output = original(*a, **kw)
            captured.append(output)
            try:
                dump(target / "pipeline.json", pipeline_record(output))
            except Exception:
                logging.exception("관측 기록 실패 — 실제 결과는 바꾸지 않는다")
            return output

        pipeline.run = observe
        try:
            result = run_analysis(project, directory)
            dump(target / "result.json", result)
            return result
        except Exception:
            (target / "failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
            raise
        finally:
            pipeline.run = original
            stats = run_stats(started, llm_cache, ocr_cache, upstage, captured[0] if captured else None, {
                "input_revision": project["input_revision"], "document_count": len(project["documents"]),
                "run_directory": str(target), "cache_files_at_end": count_cache(cache_dir)})
            dump(target / "run_stats.json", stats)
            dump(run_dir / "latest_run.json", stats)

    # --data-root: 이미 분석한 작업 공간을 화면 확인용으로 다시 띄울 때(분석 재실행 없음).
    app = create_app(data_root=(args.data_root or run_dir / "web_workspace").resolve(), runner=observed_runner)
    uvicorn.run(app, host=args.host, port=args.port, access_log=False)


def cmd_drive(args) -> None:
    import requests
    run_dir = prepare_run_dir(args.run_dir, allow_existing=bool(args.resume_project_id))
    session = requests.Session()
    session.headers["x-esgenie-client"] = "workspace"

    def call(method, path, **kw):
        r = session.request(method, args.base_url + path, timeout=120, **kw)
        if not r.ok:
            raise RuntimeError(f"{method} {path}: {r.status_code} {r.text[:500]}")
        return r

    def wait(pid, stage):
        started = time.monotonic()
        while True:
            project = call("GET", f"/api/projects/{pid}").json()
            if project["job"]["status"] not in {"running", "queued"}:
                dump(run_dir / f"{stage}_project.json", project)
                print(json.dumps({"stage": stage, "status": project["job"]["status"],
                                  "seconds": round(time.monotonic() - started)}, ensure_ascii=False), flush=True)
                return project
            time.sleep(10)

    def upload(pid, f):
        with open(f["path"], "rb") as fh:
            return call("POST", f"/api/projects/{pid}/documents",
                        files={"file": (f["name"], fh, "application/pdf")}).json()

    def export(pid, stage):
        folder = run_dir / "downloads" / stage
        folder.mkdir(parents=True, exist_ok=True)
        for kind, ext in [("xlsx", "xlsx"), ("bundle", "zip")]:
            p = folder / f"검토용초안_{stage}.{ext}"
            p.write_bytes(call("GET", f"/api/projects/{pid}/download/{kind}").content)

    initial, manifest = input_files(args.pack_dir, "initial")
    followup, _ = input_files(args.pack_dir, "followup")
    expected = {f["name"]: f["role"] for f in initial}
    if args.resume_project_id:
        # 최초 분석이 이미 끝난 프로젝트를 이어서 내려받기·보완 분석만 한다(유료 재분석 방지).
        pid = args.resume_project_id
        if json.loads((run_dir / "session.json").read_text(encoding="utf-8"))["project_id"] != pid:
            raise SystemExit("session.json의 프로젝트와 다르다")
        if wait(pid, "initial")["job"]["status"] not in DONE:
            raise SystemExit("최초 분석 실패")
    else:
        config = call("GET", "/api/config").json()
        assert config["analysis_available"] is True
        project = call("POST", "/api/projects", json={"company_name": args.company, "year": args.year,
                       "industry": args.industry, "framework": args.framework}).json()
        pid = project["id"]
        dump(run_dir / "session.json", {"project_id": pid, "base_url": args.base_url, "framework": args.framework,
                                        "inputs_initial": initial, **manifest, "started_at": now()})
        for f in initial:
            project = upload(pid, f)
        roles = {d["name"]: d["role"] for d in project["documents"]}
        dump(run_dir / "initial_uploaded.json", project)
        if roles != expected:
            raise SystemExit(f"업로드 역할이 구성 목록과 다르다: {roles}")
        call("POST", f"/api/projects/{pid}/analysis")
        if wait(pid, "initial")["job"]["status"] not in DONE:
            raise SystemExit("최초 분석 실패")
    export(pid, "initial")
    extra = [f for f in followup if f["name"] not in expected]
    assert len(extra) == 1
    project = upload(pid, extra[0])
    dump(run_dir / "followup_uploaded.json", project)
    stale = session.get(args.base_url + f"/api/projects/{pid}/download/xlsx", timeout=30)
    dump(run_dir / "followup_stale_download_check.json", {"status_code": stale.status_code})
    call("POST", f"/api/projects/{pid}/analysis")
    if wait(pid, "followup")["job"]["status"] not in DONE:
        raise SystemExit("보완 분석 실패")
    export(pid, "followup")
    print(json.dumps({"done": True, "run_dir": str(run_dir)}, ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, *, code=True, cache=True):
        p.add_argument("--run-dir", type=Path, required=True)
        if code:
            p.add_argument("--code-path", type=Path, required=True)
            p.add_argument("--env-file", type=Path, required=True)
        if cache:
            p.add_argument("--cache-dir", type=Path, required=True)

    core = sub.add_parser("core")
    common(core)
    core.add_argument("--stage", choices=["initial", "followup"], required=True)
    # 보고서(.md/.pdf)·데이터시트(.xlsx)·응답서(.xlsx/.pdf)를 <run>/<stage>/exports에 남긴다.
    core.add_argument("--export", action="store_true")
    core.add_argument("--record-upstage", action="store_true")
    core.add_argument("--replay-upstage", type=Path, default=None,
                      help="기록한 Upstage 응답 폴더. 주면 네트워크를 막고 LLM은 캐시 적중만 쓴다")
    core.add_argument("--live-llm", action="store_true",
                      help="--replay-upstage와 함께: Upstage는 기록 재생, LLM 캐시 미스는 실호출(strict)")
    core.add_argument("--run-id", default="")
    core.add_argument("--extra-evidence", type=Path, action="append",
                      help="검증용 가상 변형본(파일명에 '변형본') — 원본 세트 뒤에 증빙으로 더한다")
    core.add_argument("--also-framework", action="append", help="같은 분석으로 추가 양식 응답서도 만든다")
    serve = sub.add_parser("serve")
    common(serve)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, required=True)
    serve.add_argument("--data-root", type=Path, default=None)
    drive = sub.add_parser("drive")
    common(drive, code=False, cache=False)
    drive.add_argument("--base-url", required=True)
    drive.add_argument("--resume-project-id", default="")
    for p in (core, drive):
        p.add_argument("--pack-dir", type=Path, required=True)
        p.add_argument("--company", default="한울정밀공업(주)")
        p.add_argument("--industry", default="자동차 차체부품")
        p.add_argument("--year", type=int, default=2026)
        p.add_argument("--framework", default="rba42")
    args = ap.parse_args()
    {"core": cmd_core, "serve": cmd_serve, "drive": cmd_drive}[args.cmd](args)


if __name__ == "__main__":
    main()
