"""Loopback API for the guided workspace; all user files stay in its project root."""
from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
import re
from typing import Literal
from urllib.parse import quote, urlsplit
from uuid import uuid4

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .downloads import build_download
from .engine import analysis_available, run_analysis
from .presenter import public_project
from .store import ProjectStore, WorkspaceError

ROOT = Path(__file__).resolve().parents[2]
MAX_FILE = 20 * 1024 * 1024
MAX_PROJECT = 100 * 1024 * 1024
logger = logging.getLogger(__name__)
FrameworkKey = Literal["rba42", "hmc", "kesg28", "kesg61", "saq5", "saq5_env"]


class Company(BaseModel):
    company_name: str = Field(min_length=1, max_length=100)
    year: int = Field(ge=2000, le=2100)
    industry: str = Field(default="기타", max_length=60)
    framework: FrameworkKey = "rba42"

    @field_validator("company_name")
    @classmethod
    def company_text(cls, value):
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("회사명을 입력해 주세요.")
        return value.strip()


class DocumentRole(BaseModel):
    role: Literal["evidence", "company_answer"]


class Note(BaseModel):
    text: str = Field(default="", max_length=5000)
    answer: str = Field(default="", max_length=5000)


def create_app(*, data_root: Path | None = None, runner=None, available=None, static_dir: Path | None = None) -> FastAPI:
    store = ProjectStore(data_root or Path(os.getenv("ESGENIE_WEB_DATA_DIR", ROOT / "outputs" / "web_workspace")), runner or run_analysis)
    available = available or analysis_available

    @asynccontextmanager
    async def lifespan(app):
        yield
        store.close()

    app = FastAPI(title="ESGenie workspace", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.store = store
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1", "testserver"}:
                return JSONResponse({"detail": "작업 화면에서 다시 시도해 주세요."}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("x-esgenie-client") != "workspace":
            return JSONResponse({"detail": "작업 화면에서 다시 시도해 주세요."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(WorkspaceError)
    async def workspace_error(request, exc):
        return JSONResponse({"detail": exc.message}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"detail": "입력한 내용을 확인해 주세요. 회사명, 연도 또는 글자 수가 올바르지 않아요."}, status_code=422)

    @app.get("/api/config")
    def config():
        return {"analysis_available": available(), "max_file_mb": 20, "max_project_mb": 100,
                "frameworks": [{"key": "rba42", "label": "고객사 실사 답변 준비", "description": "무엇을 골라야 할지 모르면 여기서 시작하세요. 기본 실사 질문에 답합니다."},
                               {"key": "hmc", "label": "현대차 협력사 질문", "description": "현대차 공급망 질문으로 응답서를 준비합니다."},
                               {"key": "kesg28", "label": "우리 회사 기본 현황 정리", "description": "중소기업에 맞춘 기본 질문으로 환경·사회·경영 현황을 정리합니다."},
                               {"key": "saq5_env", "label": "환경 질문부터 준비", "description": "에너지와 폐기물 등 환경 질문부터 살펴봅니다."}]}

    @app.get("/api/projects")
    def projects():
        return store.list()

    @app.post("/api/projects", status_code=201)
    def create(company: Company):
        return public_project(store.create(company.model_dump()))

    @app.post("/api/examples", status_code=201)
    def example():
        return public_project(store.create({}, example=True))

    @app.get("/api/projects/{project_id}")
    def project(project_id: str):
        return public_project(store.read(project_id))

    @app.patch("/api/projects/{project_id}")
    def patch(project_id: str, company: Company):
        return public_project(store.patch(project_id, company.model_dump()))

    @app.post("/api/projects/{project_id}/documents", status_code=201)
    def upload(project_id: str, file: UploadFile = File()):
        name = file.filename or ""
        if len(name) > 160 or name in {"", ".", ".."} or re.search(r'[\\/\x00-\x1f]', name) or name.startswith("."):
            raise WorkspaceError("파일 이름에 사용할 수 없는 문자가 있어요. 이름을 바꿔 올려 주세요.")
        if Path(name).suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg"}:
            raise WorkspaceError("PDF 또는 사진 파일(PNG, JPG)을 올려 주세요.")
        data = file.file.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            raise WorkspaceError("파일 한 개는 20MB까지 올릴 수 있어요.", 413)
        try:
            import fitz
            with fitz.open(stream=data, filetype=Path(name).suffix[1:]) as doc:
                if doc.needs_pass or not 1 <= doc.page_count <= 200:
                    raise ValueError("encrypted or too many pages")
                pages = doc.page_count
        except Exception:
            raise WorkspaceError("파일을 읽을 수 없어요. 암호를 해제하거나 잘 열리는 PDF·사진으로 다시 올려 주세요.") from None
        with store.lock:
            current = store.read(project_id)
            store.editable(current)
            if len(current["documents"]) >= 20 or sum(d["size"] for d in current["documents"]) + len(data) > MAX_PROJECT:
                raise WorkspaceError("한 작업에는 최대 20개, 합계 100MB까지 올릴 수 있어요.", 413)
            if any(d["name"].casefold() == name.casefold() for d in current["documents"]):
                raise WorkspaceError("같은 이름의 자료가 있어요. 기존 자료를 빼거나 파일 이름을 바꿔 주세요.", 409)
            document_id = uuid4().hex
            path = store.directory(project_id) / "uploads" / document_id / name
            path.parent.mkdir(parents=True)
            path.write_bytes(data)
            from esgenie.supplychain import is_saq_upload
            role = "company_answer" if is_saq_upload(str(path), file_name=name) else "evidence"
            current["documents"].append({"id": document_id, "name": name, "role": role, "size": len(data), "pages": pages, "example": False})
            current["input_revision"] += 1
            store.save(current)
            return public_project(current)

    @app.patch("/api/projects/{project_id}/documents/{document_id}")
    def role(project_id: str, document_id: str, value: DocumentRole):
        return public_project(store.change_document(project_id, document_id, value.role))

    @app.delete("/api/projects/{project_id}/documents/{document_id}")
    def remove(project_id: str, document_id: str):
        return public_project(store.change_document(project_id, document_id, None))

    @app.get("/api/projects/{project_id}/documents/{document_id}/pages/{page}")
    def page_image(project_id: str, document_id: str, page: int):
        document, path = store.document_path(store.read(project_id), document_id)
        if not 0 <= page < document["pages"]:
            raise WorkspaceError("해당 페이지가 없어요.", 404)
        import fitz
        try:
            with fitz.open(path) as pdf:
                source = pdf[page]
                scale = min(1.5, 1800 / max(source.rect.width, source.rect.height))
                return Response(source.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png"), media_type="image/png")
        except Exception:
            raise WorkspaceError("이 페이지를 표시하지 못했어요. 원본 파일을 확인해 주세요.", 422) from None

    @app.get("/api/projects/{project_id}/documents/{document_id}/original")
    def original(project_id: str, document_id: str):
        document, path = store.document_path(store.read(project_id), document_id)
        return FileResponse(path, filename=document["name"], content_disposition_type="attachment")

    @app.post("/api/projects/{project_id}/analysis", status_code=202)
    def analysis(project_id: str):
        if not available():
            raise WorkspaceError("분석 연결이 아직 준비되지 않았어요. 예시로 사용법을 먼저 살펴볼 수 있어요.", 503)
        return public_project(store.start(project_id))

    @app.put("/api/projects/{project_id}/notes/{question_id}")
    def note(project_id: str, question_id: str, value: Note):
        return public_project(store.save_note(project_id, question_id, value.model_dump()))

    @app.get("/api/projects/{project_id}/download/{kind}")
    def download(project_id: str, kind: Literal["xlsx", "bundle"]):
        current = store.read(project_id)
        try:
            content, name, media_type = build_download(current, store.directory(project_id), kind)
        except WorkspaceError:
            raise
        except Exception:
            logger.exception("Export failed for %s", project_id)
            raise WorkspaceError("파일을 준비하지 못했어요. 저장된 내용은 그대로 있으니 다시 시도해 주세요.", 503) from None
        return Response(content, media_type=media_type, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})

    dist = static_dir or ROOT / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    else:
        @app.get("/")
        def build_required():
            return JSONResponse({"detail": "화면 빌드가 필요합니다. frontend에서 npm install 후 npm run build를 실행하세요."}, status_code=503)
    return app
