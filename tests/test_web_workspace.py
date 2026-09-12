"""Guided UI boundaries: provenance, persistence, stale inputs, and downloads."""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from zipfile import ZipFile

import fitz
from fastapi.testclient import TestClient
from openpyxl import load_workbook
import pytest

from esgenie.web.app import create_app
from esgenie.web.demo import populate_example
from esgenie.web.presenter import present_sheet

HEADERS = {"X-ESGenie-Client": "workspace"}
COMPANY = {"company_name": "테스트 회사", "year": 2026, "industry": "금속가공", "framework": "rba42"}


def pdf_bytes():
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((40, 60), "Electricity 2026 January: 142560 kWh")
        return document.tobytes()


def fixture_result(project, directory):
    example = populate_example(deepcopy(project))
    return example["result"]


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_root=tmp_path, runner=fixture_result, available=lambda: True), headers=HEADERS) as client:
        yield client


def create(client):
    response = client.post("/api/projects", json=COMPANY)
    assert response.status_code == 201
    return response.json()["id"]


def upload(client, pid, name="electricity.pdf"):
    return client.post(f"/api/projects/{pid}/documents", files={"file": (name, pdf_bytes(), "application/pdf")})


def wait_for_analysis(client, pid):
    # A barrier queued after analysis avoids timing-sensitive polling in tests.
    client.app.state.store.worker.submit(lambda: None).result(timeout=10)
    return client.get(f"/api/projects/{pid}").json()


def test_upload_preview_roles_and_project_isolation(client):
    pid = create(client)
    project = upload(client, pid, "전력.pdf").json()
    doc = project["documents"][0]
    assert doc["role"] == "evidence"
    assert project["input_revision"] == 2
    image = client.get(f"/api/projects/{pid}/documents/{doc['id']}/pages/0")
    assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
    assert client.get(f"/api/projects/{pid}/documents/{doc['id']}/pages/1").status_code == 404
    other = create(client)
    assert client.get(f"/api/projects/{other}/documents/{doc['id']}/original").status_code == 404
    assert upload(client, pid, "전력.pdf").status_code == 409
    claimed = upload(client, pid, "자가진단 설문.pdf").json()["documents"][-1]
    assert claimed["role"] == "company_answer"
    revised = client.patch(f"/api/projects/{pid}/documents/{doc['id']}", json={"role": "company_answer"}).json()
    assert revised["documents"][0]["role"] == "company_answer"
    assert client.delete(f"/api/projects/{pid}/documents/{doc['id']}").status_code == 200
    assert client.get(f"/api/projects/{pid}/documents/{doc['id']}/original").status_code == 404


@pytest.mark.parametrize("filename,data", [("../escape.pdf", b"x"), ("bad.txt", b"text"), ("bad.pdf", b"not a pdf"), ("..\\escape.pdf", b"x")])
def test_invalid_files_do_not_enter_project(client, filename, data):
    pid = create(client)
    response = client.post(f"/api/projects/{pid}/documents", files={"file": (filename, data)})
    assert response.status_code == 400
    assert client.get(f"/api/projects/{pid}").json()["documents"] == []


def test_processing_locks_inputs_and_failures_preserve_previous_result(tmp_path):
    started, release = Event(), Event()
    calls = []

    def runner(project, directory):
        calls.append(deepcopy(project))
        started.set()
        assert release.wait(5)
        raise RuntimeError("private diagnostic that must not reach UI")

    with TestClient(create_app(data_root=tmp_path, runner=runner, available=lambda: True), headers=HEADERS) as client:
        pid = create(client)
        uploaded = upload(client, pid).json()
        previous_result = fixture_result(uploaded, tmp_path)
        stored = client.app.state.store.read(pid)
        stored["result"] = previous_result
        stored["result_revision"] = stored["input_revision"]
        client.app.state.store.save(stored)
        try:
            assert client.post(f"/api/projects/{pid}/analysis").status_code == 202
            assert started.wait(5)
            assert client.post(f"/api/projects/{pid}/analysis").status_code == 409
            assert client.patch(f"/api/projects/{pid}", json={**COMPANY, "year": 2025}).status_code == 409
            doc = uploaded["documents"][0]
            assert client.delete(f"/api/projects/{pid}/documents/{doc['id']}").status_code == 409
        finally:
            release.set()
        result = wait_for_analysis(client, pid)
        assert len(calls) == 1 and result["job"]["status"] == "failed"
        assert "private diagnostic" not in json.dumps(result)
        assert result["result"]["answers"] == previous_result["answers"]


def test_analysis_stale_export_blocking_and_note_versions(client):
    pid = create(client)
    assert client.post(f"/api/projects/{pid}/analysis").status_code == 400
    upload(client, pid)
    assert client.post(f"/api/projects/{pid}/analysis").status_code == 202
    project = wait_for_analysis(client, pid)
    qid = project["result"]["answers"][0]["id"]
    saved = client.put(f"/api/projects/{pid}/notes/{qid}", json={"text": "표 확인하기", "answer": "직접 작성한 답변"}).json()
    assert saved["result"]["answers"][0]["original_status"] == "flagged"
    assert saved["notes"][qid]["revision"] == saved["result_revision"]
    stale = client.patch(f"/api/projects/{pid}", json={**COMPANY, "year": 2025}).json()
    assert stale["stale"] and stale["result"] == saved["result"]
    assert client.get(f"/api/projects/{pid}/download/xlsx").status_code == 409
    assert client.put(f"/api/projects/{pid}/notes/{qid}", json={"text": "outdated"}).status_code == 409
    client.post(f"/api/projects/{pid}/analysis")
    fresh = wait_for_analysis(client, pid)
    assert not fresh["stale"]
    assert fresh["notes"][qid]["revision"] != fresh["result_revision"]


def test_restart_restores_manual_answers_without_promoting_trust(tmp_path):
    with TestClient(create_app(data_root=tmp_path), headers=HEADERS) as first:
        project = first.post("/api/examples").json()
        pid = project["id"]
        first.put(f"/api/projects/{pid}/notes/example-governance", json={"text": "담당자에게 확인", "answer": "월별 점검"})
    with TestClient(create_app(data_root=tmp_path), headers=HEADERS) as second:
        restored = second.get(f"/api/projects/{pid}").json()
        assert restored["notes"]["example-governance"]["answer"] == "월별 점검"
        answer = restored["result"]["answers"][-1]
        assert answer["original_status"] == "hitl_required"
        assert answer["value_text"] == "아직 확인하지 못했어요"


def test_bundle_preserves_limitations_and_separates_human_content(client):
    project = client.post("/api/examples").json()
    pid = project["id"]
    client.put(f"/api/projects/{pid}/notes/example-recycling", json={"text": "표 원문 재확인", "answer": '=HYPERLINK("https://invalid.example")'})
    response = client.get(f"/api/projects/{pid}/download/bundle")
    assert response.status_code == 200
    with ZipFile(BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert any(name.endswith(".pdf") for name in names)
        assert all(not name.startswith(("/", "..")) for name in names)
        raw = json.loads(archive.read("검증_근거.json"))
        assert raw["sheet"]["answers"][0]["value"] == 29.3
        assert raw["sheet"]["answers"][0]["self_reports"][0]["value"] == 92
        assert "period_inferred" in raw["sheet"]["answers"][0]["confidence_flags"]
        assert "표 원문 재확인" in archive.read("담당자_작성_및_확인사항.md").decode()
        workbook = load_workbook(BytesIO(archive.read(next(name for name in names if name.endswith(".xlsx")))))
        manual = workbook["담당자 작성"]
        assert manual["B2"].value.startswith("'=HYPERLINK")
        assert not any(cell.data_type == "f" for sheet in workbook for row in sheet for cell in row)
        assert "추정" in " ".join(str(cell.value or "") for row in workbook["응답서"] for cell in row)


def test_availability_and_same_origin_guards(tmp_path):
    with TestClient(create_app(data_root=tmp_path, available=lambda: False), headers=HEADERS) as client:
        pid = create(client)
        assert not client.get("/api/config").json()["analysis_available"]
        assert client.post(f"/api/projects/{pid}/analysis").status_code == 503
        assert client.post("/api/projects", json=COMPANY, headers={"Origin": "https://foreign.example"}).status_code == 403
        assert client.get("/api/projects", headers={"Host": "foreign.example"}).status_code == 400
        assert client.get("/api/projects/not-a-project").status_code == 404
        assert client.post("/api/projects", json={**COMPANY, "company_name": "   "}).status_code == 422
    with TestClient(create_app(data_root=tmp_path)) as no_header:
        assert no_header.post("/api/examples").status_code == 403


def test_presentation_keeps_false_zero_missing_and_pending_distinct():
    def answer(qid, value, **kwargs):
        return {"qid": qid, "question_text": qid, "section": "환경", "value": value, "status": "flagged", **kwargs}
    sources = [{"file_name": "table.pdf", "quote": "value = 0", "independent": True, "page": 0}]
    sheet = {"answers": [answer("false", False), answer("zero", 0), answer("none", None),
                         answer("pending", 0, status="verified", evidence_links=sources),
                         answer("year", 1, status="self_reported", period=2026, confidence_flags=["period_inferred"])]}
    shown = present_sheet(sheet, [], {"table.pdf"})
    assert [a["value_text"] for a in shown[:3]] == ["아니오", "0", "아직 확인하지 못했어요"]
    assert shown[3]["status"] == "review" and shown[3]["original_status"] == "verified"
    assert "표" in shown[3]["notices"][0]
    assert shown[4]["period_label"] == "2026년 · 추정"


def test_engine_adapter_separates_company_answers_and_disallows_fallback(monkeypatch, tmp_path):
    from esgenie import pipeline
    from esgenie import supplychain
    from esgenie.config import SETTINGS
    from esgenie.web.engine import run_analysis

    calls = {}
    previous = SETTINGS.strict_llm
    output = SimpleNamespace(ocr_extractions=[], sections={"G": SimpleNamespace(final_score=None)},
                             evidence_graph=SimpleNamespace(nodes={}, text_nodes={}))

    def run(*args, **kwargs):
        calls.update(kwargs)
        assert SETTINGS.strict_llm is True
        return output

    monkeypatch.setattr(pipeline, "run", run)
    monkeypatch.setattr(supplychain, "parse_saq_claims", lambda paths: calls.update(company_paths=paths) or {})
    monkeypatch.setattr(supplychain, "respond_from_pipeline", lambda *args, **kwargs: SimpleNamespace(corp_name="", to_dict=lambda: {"answers": []}))
    project = {"id": "a"*32, **COMPANY, "documents": [{"id": "a", "name": "bill.pdf", "role": "evidence"}, {"id": "b", "name": "survey.pdf", "role": "company_answer"}]}
    result = run_analysis(project, tmp_path)
    assert set(calls["evidence_files"]) == {"bill.pdf"}
    assert calls["company_paths"] == [str(tmp_path / "uploads/b/survey.pdf")]
    assert calls["use_dart"] is False and calls["demo_greenwash"] is False
    assert calls["save_traces"] is False
    assert SETTINGS.strict_llm == previous
    assert any("회사 운영" in item for item in result["limitations"])
