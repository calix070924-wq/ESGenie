"""Local project storage and a serial analysis worker."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import logging
from pathlib import Path
import re
from threading import RLock
from uuid import uuid4

from .demo import populate_example
from .presenter import timestamp
from .review import affected_by_document, invalidate, reconcile

logger = logging.getLogger(__name__)
ID = re.compile(r"^[a-f0-9]{32}$")


class WorkspaceError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message, self.status = message, status


class ProjectStore:
    def __init__(self, root: Path, runner):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.runner = runner
        self.lock = RLock()
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="esgenie-analysis")
        for path in self.root.glob("*/project.json"):
            try:
                project = json.loads(path.read_text())
                if project.get("job", {}).get("status") in {"queued", "running"}:
                    project["job"] = {"status": "failed", "stage": "중단된 작업", "error": "화면을 실행하는 중 분석이 중단됐어요. 다시 시작해 주세요."}
                    self.save(project)
            except (ValueError, OSError):
                logger.warning("Unreadable project record: %s", path.name)

    def directory(self, project_id: str) -> Path:
        if not ID.fullmatch(project_id):
            raise WorkspaceError("작업을 찾지 못했어요.", 404)
        return self.root / project_id

    def read(self, project_id: str) -> dict:
        with self.lock:
            try:
                return json.loads((self.directory(project_id) / "project.json").read_text())
            except (FileNotFoundError, ValueError):
                raise WorkspaceError("작업을 찾지 못했어요.", 404) from None

    def save(self, project: dict):
        with self.lock:
            directory = self.directory(project["id"])
            directory.mkdir(parents=True, exist_ok=True)
            project["updated_at"] = timestamp()
            temporary = directory / "project.json.tmp"
            temporary.write_text(json.dumps(project, ensure_ascii=False, indent=2, allow_nan=False))
            temporary.replace(directory / "project.json")

    def list(self) -> list[dict]:
        projects = []
        with self.lock:
            for path in self.root.glob("*/project.json"):
                try:
                    project = self.read(path.parent.name)
                except WorkspaceError:
                    continue
                projects.append({key: project[key] for key in ("id", "company_name", "year", "mode", "updated_at")})
        return sorted(projects, key=lambda p: p["updated_at"], reverse=True)

    def create(self, values: dict, example: bool = False) -> dict:
        project = {"id": uuid4().hex, "company_name": values.get("company_name", ""),
                   "year": values.get("year", 2026), "industry": values.get("industry", "기타"),
                   "framework": values.get("framework", "rba42"), "mode": "live",
                   "documents": [], "input_revision": 1, "result_revision": None, "result": None,
                   "notes": {}, "reviews": {}, "affected_questions": [], "job": {"status": "idle", "stage": "자료 준비", "error": ""}}
        if example:
            populate_example(project)
        self.save(project)
        return project

    @staticmethod
    def editable(project: dict, *, inputs: bool = True):
        if project["job"]["status"] in {"queued", "running"}:
            raise WorkspaceError("자료를 읽고 있어요. 작업이 끝난 뒤 변경해 주세요.", 409)
        if inputs and project["mode"] == "example":
            raise WorkspaceError("예시의 자료는 바꿀 수 없어요. 회사 서류로 새 작업을 시작해 주세요.", 409)

    def patch(self, project_id: str, values: dict) -> dict:
        with self.lock:
            project = self.read(project_id)
            self.editable(project)
            if any(project[k] != v for k, v in values.items()):
                invalidate(project, [a["qid"] for a in (project.get("result") or {}).get("sheet", {}).get("answers", [])])
                project.update(values)
                project["input_revision"] += 1
                self.save(project)
            return project

    def document_path(self, project: dict, document_id: str, version: int | None = None) -> tuple[dict, Path]:
        document = next((d for d in project["documents"] if d["id"] == document_id), None)
        if not document or document.get("example"):
            raise WorkspaceError("원본 파일이 없는 예시이거나 자료를 찾지 못했어요.", 404)
        selected = document
        if version is not None and version != document.get("version", 1):
            selected = next((v for v in document.get("versions", []) if v["version"] == version), None)
            if selected is None:
                raise WorkspaceError("해당 원본 버전이 없어요.", 404)
        relative = selected.get("path") or str(Path("uploads") / document_id / selected["name"])
        return selected, self.directory(project["id"]) / relative

    def change_document(self, project_id: str, document_id: str, role: str | None, included: bool | None = None) -> dict:
        with self.lock:
            project = self.read(project_id)
            self.editable(project)
            document, _ = self.document_path(project, document_id)
            if project.get("result") and "documents" not in project["result"]:
                project["result"]["documents"] = deepcopy(project["documents"])
            if role is None and included is None:
                document["included"] = False
            elif role is not None and document["role"] == role and included is None:
                return project
            else:
                if role is not None:
                    document["role"] = role
                if included is not None:
                    if document.get("included", True) == included and role is None:
                        return project
                    document["included"] = included
            invalidate(project, affected_by_document(project, document))
            project["input_revision"] += 1
            self.save(project)
            return project

    def save_review(self, project_id: str, question_id: str, values: dict, complete: bool = False) -> dict:
        with self.lock:
            project = self.read(project_id)
            self.editable(project, inputs=False)
            if not project.get("result") or question_id not in {a["qid"] for a in project["result"]["sheet"]["answers"]}:
                raise WorkspaceError("해당 문항을 찾지 못했어요.", 404)
            if project["input_revision"] != project["result_revision"]:
                raise WorkspaceError("자료가 바뀌었어요. 재분석 후 저장해 주세요.", 409)
            if complete and not values["answer"].strip() and not values["memo"].strip():
                raise WorkspaceError("답변을 작성하거나 자료 부족 사유를 메모에 남겨 주세요.")
            # Resolve sources against server-held originals, never accept arbitrary paths or quotes.
            from .presenter import presented_answers
            row = next(a for a in presented_answers(project) if a["id"] == question_id)
            allowed = row["sources"] + row["automatic"]["sources"] + row["reference_sources"]
            allowed += [s for c in row["candidates"] for s in c["sources"]]
            resolved = []
            for source in values["sources"]:
                match = next((s for s in allowed if (s.get("document_id"), s.get("version"), s.get("name"), s.get("page"), s.get("quote")) ==
                              (source.get("document_id"), source.get("version"), source.get("name"), source.get("page"), source.get("quote"))), None)
                if match is None and source.get("manual") and source.get("document_id"):
                    original, _ = self.document_path(project, source["document_id"], source.get("version"))
                    if original["name"] != source["name"] or source.get("page") is None or not 0 <= source["page"] < original.get("pages", 0):
                        raise WorkspaceError("원문의 파일명과 페이지를 확인해 주세요.")
                    doc = next(d for d in project["documents"] if d["id"] == source["document_id"])
                    match = {"name": original["name"], "page": source["page"], "version": original.get("version", 1),
                             "document_id": doc["id"], "quote": "담당자 직접 연결 · 해당 원문 페이지 확인 필요", "bbox": None,
                             "independent": doc["role"] == "evidence", "preview_available": True, "manual": True}
                if match is None:
                    raise WorkspaceError("선택한 근거를 확인할 수 없어요.")
                if complete and source.get("document_id"):
                    doc = next((d for d in project["documents"] if d["id"] == source["document_id"]), None)
                    if doc and (not doc.get("included", True) or doc.get("version", 1) != source.get("version", 1)):
                        raise WorkspaceError("최신의 분석 포함 자료를 연결하거나 이전 근거를 해제해 주세요.")
                resolved.append(deepcopy(match))
            values["sources"] = resolved
            previous = project.setdefault("reviews", {}).get(question_id)
            history = deepcopy(previous.get("history", []) if previous else [])
            history.append({"at": timestamp(), "action": "검토 완료" if complete else "저장", "reason": values["reason"],
                            "before": deepcopy(previous["values"] if previous else row["automatic"]), "after": deepcopy(values)})
            status = "complete" if complete else ("missing" if not values["answer"].strip() else "pending")
            project["reviews"][question_id] = {"values": values, "status": status, "revision": project["result_revision"],
                                               "framework": project["framework"], "history": history, "updated_at": timestamp(),
                                               "method": "담당자 수정" if any(values[k] != row["automatic"][k] for k in ("answer", "unit", "scope", "sources")) else "자동 입력"}
            if complete:
                project["affected_questions"] = [q for q in project.get("affected_questions", []) if q != question_id]
            self.save(project)
            return project

    def save_note(self, project_id: str, question_id: str, values: dict) -> dict:
        with self.lock:
            project = self.read(project_id)
            self.editable(project, inputs=False)
            if not project["result"] or question_id not in {a["id"] for a in project["result"]["answers"]}:
                raise WorkspaceError("해당 질문을 찾지 못했어요.", 404)
            if project["input_revision"] != project["result_revision"]:
                raise WorkspaceError("자료가 바뀌었어요. 다시 분석한 뒤 메모를 남겨 주세요.", 409)
            project["notes"][question_id] = {**values, "revision": project["result_revision"], "updated_at": timestamp()}
            self.save(project)
            return project

    def start(self, project_id: str) -> dict:
        with self.lock:
            project = self.read(project_id)
            self.editable(project)
            if not any(d.get("included", True) and not d.get("error") for d in project["documents"]):
                raise WorkspaceError("가지고 있는 서류를 한 개 이상 올려 주세요.")
            project["job"] = {"status": "queued", "stage": "자료 읽기를 준비하고 있어요", "error": ""}
            self.save(project)
            self.worker.submit(self._run, deepcopy(project))
            return project

    def _run(self, snapshot: dict):
        project_id = snapshot["id"]
        with self.lock:
            project = self.read(project_id)
            project["job"] = {"status": "running", "stage": "서류를 읽고 질문에 맞는 답변과 근거를 찾고 있어요", "error": ""}
            self.save(project)
        try:
            result = self.runner(snapshot, self.directory(project_id))
            with self.lock:
                project = self.read(project_id)
                previous = project.get("result")
                from .presenter import presented_answers
                if previous:
                    history_dir = self.directory(project_id) / "analyses"
                    history_dir.mkdir(exist_ok=True)
                    previous_rows = presented_answers(project)
                    (history_dir / f"{project['result_revision']}.json").write_text(json.dumps(
                        {"result": previous, "answers": previous_rows}, ensure_ascii=False, allow_nan=False), encoding="utf-8")
                    latest = {**deepcopy(project), "result": result}
                    latest["result"]["documents"] = deepcopy(snapshot["documents"])
                    fresh = {r["id"]: r for r in presented_answers(latest)}
                    for before in previous_rows:
                        after = fresh.get(before["id"])
                        if after and before["automatic"] != after["automatic"]:
                            project.setdefault("analysis_history", {}).setdefault(before["id"], []).append(
                                {"at": timestamp(), "action": "재분석 · 기존 답변 보관", "reason": "새 분석 값과 근거를 비교하세요. 담당자 수정값은 유지됩니다.",
                                 "before": before["automatic"], "after": after["automatic"]})
                reconcile(project, project.get("result"), result)
                result["documents"] = deepcopy(snapshot["documents"])
                project["result"] = result
                project["result_revision"] = snapshot["input_revision"]
                project["job"] = {"status": "complete", "stage": "확인할 내용을 정리했어요", "error": ""}
                self.save(project)
        except Exception:
            logger.exception("Analysis failed for project %s", project_id)
            with self.lock:
                project = self.read(project_id)
                project["job"] = {"status": "failed", "stage": "자료 읽기를 마치지 못했어요", "error": "자료 읽기를 마치지 못했어요. 연결 상태와 파일이 잘 열리는지 확인한 뒤 다시 시도해 주세요. 이전 결과는 보관되어 있어요."}
                self.save(project)

    def close(self):
        self.worker.shutdown(wait=True)
