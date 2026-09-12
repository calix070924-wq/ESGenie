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
                   "notes": {}, "job": {"status": "idle", "stage": "자료 준비", "error": ""}}
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
                project.update(values)
                project["input_revision"] += 1
                self.save(project)
            return project

    def document_path(self, project: dict, document_id: str) -> tuple[dict, Path]:
        document = next((d for d in project["documents"] if d["id"] == document_id), None)
        if not document or document.get("example"):
            raise WorkspaceError("원본 파일이 없는 예시이거나 자료를 찾지 못했어요.", 404)
        return document, self.directory(project["id"]) / "uploads" / document_id / document["name"]

    def change_document(self, project_id: str, document_id: str, role: str | None) -> dict:
        with self.lock:
            project = self.read(project_id)
            self.editable(project)
            document, _ = self.document_path(project, document_id)
            if role is None:
                project["documents"].remove(document)
            elif document["role"] == role:
                return project
            else:
                document["role"] = role
            project["input_revision"] += 1
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
            if not project["documents"]:
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
