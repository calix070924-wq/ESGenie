"""Rehearsal fix 2: serialization differences versus substantive review changes."""
from copy import deepcopy
from dataclasses import asdict
import json

import fitz
from fastapi.testclient import TestClient
import pytest

from esgenie.supplychain.claims import parse_saq_claims
from esgenie.web.app import create_app
from esgenie.web.presenter import timestamp
from esgenie.web.review import invalidate, reconcile

WASTE = "RBA-C-4-E-6-2"
TRAINING = "RBA-E-6"
HEADERS = {"X-ESGenie-Client": "workspace"}


@pytest.fixture
def claim(monkeypatch):
    # Exercise the production company-answer parser; only PDF text I/O is stubbed.
    monkeypatch.setattr("esgenie.supplychain.claims._extract_text",
                        lambda path: "2026년 4월 김해 제1공장 외부 위탁 폐기물 재활용률 92%")
    parsed = asdict(parse_saq_claims(["05_자가진단.pdf"])["E-6-2"])
    assert isinstance(parsed["boundary"]["site_path"], tuple)
    return parsed


def answer(claim):
    return {"qid": WASTE, "section": "환경", "question_text": "폐기물 재활용률",
            "value": 29.3, "unit": "%", "period": 2026, "status": "flagged",
            "boundary_label": "2026년 4월 · 김해 제1공장 · 외부 위탁 폐기물",
            "boundary": {"site_path": ("제1공장",), "period_start": "2026-04-01"},
            "completeness": "total", "comparison": "mismatch",
            "comparison_reason": "회사 주장과 외부 위탁 재활용률의 분모가 다름",
            "comparisons": [{"comparison": "mismatch", "boundary": claim["boundary"]}],
            "scope_notes": ["월간 값", "내부 스크랩과 분모 구분"],
            "confidence_flags": ["claim_mismatch"], "flags": ["분모 확인"],
            "rationale": "5400 / 18400 * 100", "evidence_needed": [],
            "evidence_links": [{"file_name": "waste.pdf", "page": 0,
                                "quote": "외부 위탁 18,400 kg, 재활용 5,400 kg",
                                "bbox": (0.1, 0.2, 0.5, 0.6), "independent": True,
                                "node_id": "waste", "resolved": True}],
            "reference_links": [{"file_name": "scrap.pdf", "page": 1, "quote": "내부 스크랩 92%"}],
            "self_reports": [deepcopy(claim)], "option_evidence": {},
            "draft_text": "", "draft_citations": [], "draft_grounding": None}


def completed_project():
    return {"reviews": {WASTE: {"status": "complete", "values": {
        "answer": "29.3", "unit": "%", "scope": "담당자 확인 범위",
        "sources": [{"document_id": "waste", "version": 1, "page": 0}],
        "memo": "내부 스크랩과 구분", "reason": "원문 계산식 확인"},
        "history": [{"action": "검토 완료", "at": "2026-10-08"}]}},
        "affected_questions": [], "analysis_history": {WASTE: [{"action": "이전 분석 보관"}]}}


def test_parser_result_json_round_trip_and_object_key_order_preserve_completion(claim):
    fresh = answer(claim)
    stored = json.loads(json.dumps(fresh, ensure_ascii=False))
    stored = dict(reversed(list(stored.items())))
    stored["self_reports"][0]["boundary"] = dict(reversed(list(stored["self_reports"][0]["boundary"].items())))
    project = completed_project()
    untouched = deepcopy((project, fresh, stored))
    reconcile(project, {"sheet": {"answers": [stored]}}, {"sheet": {"answers": [fresh]}})
    assert (project, fresh, stored) == untouched
    assert project["reviews"][WASTE]["status"] == "complete"
    assert isinstance(fresh["self_reports"][0]["boundary"]["site_path"], tuple)


@pytest.mark.parametrize("path,new_value", [
    (("value",), 30.0), (("unit",), "kg"), (("period",), 2025),
    (("boundary_label",), "다른 공장"), (("boundary", "period_start"), "2026-03-01"),
    (("boundary", "site_path"), ["다른 공장"]), (("completeness",), "partial"),
    (("evidence_links", 0, "file_name"), "new.pdf"), (("evidence_links", 0, "page"), 1),
    (("evidence_links", 0, "quote"), "다른 원문"), (("evidence_links", 0, "bbox"), [0, 0, 1, 1]),
    (("evidence_links", 0, "resolved"), False), (("evidence_links", 0, "independent"), False),
    (("reference_links", 0, "page"), 2), (("status",), "verified"),
    (("scope_notes",), ["변경된 범위 사유"]), (("confidence_flags",), []),
    (("flags",), []), (("rationale",), "변경된 판정 이유"),
    (("evidence_needed",), ["추가 기록 필요"]), (("comparison",), "compared"),
    (("comparison_reason",), "주장과 일치"), (("comparisons", 0, "comparison"), "compared"),
    (("self_reports", 0, "value"), 91.0), (("self_reports", 0, "unit"), "kg"),
    (("self_reports", 0, "boundary", "site_path"), ["다른 사업장"]),
    (("self_reports", 0, "source"), "saq:new.pdf"),
    (("draft_text",), "변경된 답변 초안"), (("draft_citations",), [{"node_id": "new"}]),
    (("draft_grounding",), {"passed": False}), (("option_evidence",), {"보기": {"passed": False}}),
])
def test_substantive_changes_invalidate_without_overwriting_human_values(claim, path, new_value):
    old = json.loads(json.dumps(answer(claim)))
    new = deepcopy(old)
    target = new
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = new_value
    project = completed_project()
    before = deepcopy((project["reviews"][WASTE], project["analysis_history"], old, new))
    reconcile(project, {"sheet": {"answers": [old]}}, {"sheet": {"answers": [new]}})
    assert project["affected_questions"] == [WASTE]
    assert project["reviews"][WASTE]["status"] == "again"
    assert project["reviews"][WASTE]["values"] == before[0]["values"]
    assert project["reviews"][WASTE]["history"] == before[0]["history"]
    assert project["analysis_history"] == before[1]
    assert (old, new) == before[2:]


@pytest.mark.parametrize("field", ["value", "scope_notes", "evidence_links", "self_reports"])
def test_sequence_order_is_not_erased(claim, field):
    old = answer(claim)
    if field == "value":
        old[field] = ["첫 번째 보기", "두 번째 보기"]
    elif field == "evidence_links":
        old[field].append({**old[field][0], "page": 2})
    elif field == "self_reports":
        old[field].append({**deepcopy(claim), "value": 91.0})
    new = deepcopy(old)
    new[field] = list(reversed(new[field]))
    project = completed_project()
    reconcile(project, {"sheet": {"answers": [old]}}, {"sheet": {"answers": [new]}})
    assert project["reviews"][WASTE]["status"] == "again"


def test_equal_result_never_restores_previously_invalidated_completion(claim):
    project = completed_project()
    invalidate(project, [WASTE])
    before = deepcopy(project)
    result = {"sheet": {"answers": [answer(claim)]}}
    reconcile(project, json.loads(json.dumps(result)), result)
    assert project == before
    assert project["reviews"][WASTE]["status"] == "again"


def pdf_bytes():
    with fitz.open() as document:
        document.new_page().insert_text((40, 60), "Rehearsal source")
        return document.tobytes()


@pytest.fixture
def client(tmp_path, claim):
    def runner(project, directory):
        # Deterministic analysis with the real parser's tuple-bearing company claim.
        waste = answer(claim)
        waste["reference_links"] = []
        completed_training = any(d["name"] == "13_training.pdf" for d in project["documents"])
        training = {"qid": TRAINING, "section": "사회", "question_text": "교육 참석 인원",
                    "value": 50 if completed_training else 46, "unit": "명", "status": "verified",
                    "boundary_label": "2026년 4월 · 김해 제1공장", "evidence_links": []}
        return {"sheet": {"framework_key": "rba42", "framework_label": "RBA", "answers": [waste, training]},
                "answers": [], "generated_at": timestamp(), "mode": "live", "limitations": []}

    with TestClient(create_app(data_root=tmp_path, runner=runner, available=lambda: True), headers=HEADERS) as client:
        yield client


def upload(client, pid, name):
    response = client.post(f"/api/projects/{pid}/documents", files={"file": (name, pdf_bytes(), "application/pdf")})
    assert response.status_code == 201, response.text
    return response.json()


def analyze(client, pid):
    assert client.post(f"/api/projects/{pid}/analysis").status_code == 202
    client.app.state.store.worker.submit(lambda: None).result(timeout=10)
    project = client.get(f"/api/projects/{pid}").json()
    assert project["job"]["status"] == "complete"
    return project


def rows(project):
    return {a["id"]: a for a in project["result"]["answers"]}


def save(client, pid, qid, values, complete):
    response = client.put(f"/api/projects/{pid}/reviews/{qid}", json={"values": values, "complete": complete})
    assert response.status_code == 200, response.text
    return response.json()


def setup(client):
    pid = client.post("/api/projects", json={"company_name": "검토 상태 회귀", "year": 2026}).json()["id"]
    upload(client, pid, "waste.pdf")
    upload(client, pid, "training.pdf")
    return pid, analyze(client, pid)


def test_education_addition_preserves_waste_completion_and_human_edits(client):
    pid, project = setup(client)
    waste = rows(project)[WASTE]
    values = {**waste["saved"], "scope": "담당자 확인 · 외부 위탁 전체 폐기물",
              "memo": "내부 스크랩 92%와 분모 구분", "reason": "계산식 원문 대조"}
    project = save(client, pid, WASTE, values, False)
    assert rows(project)[WASTE]["review_status"] == "pending"
    project = save(client, pid, WASTE, values, True)
    training_values = {**rows(project)[TRAINING]["saved"], "answer": "담당자 확인 46명", "memo": "미참석자 보완 필요"}
    project = save(client, pid, TRAINING, training_values, True)
    before = deepcopy(project["reviews"])
    project = upload(client, pid, "13_training.pdf")
    assert project["stale"]
    assert rows(project)[WASTE]["review_status"] == "complete"
    for kind in ("xlsx", "pdf", "bundle"):
        assert client.get(f"/api/projects/{pid}/download/{kind}").status_code == 409
    project = analyze(client, pid)
    assert rows(project)[WASTE]["review_status"] == "complete"
    assert project["reviews"][WASTE] == before[WASTE]
    assert rows(project)[TRAINING]["review_status"] == "again"
    assert rows(project)[TRAINING]["saved"] == training_values
    assert rows(project)[TRAINING]["automatic"]["answer"] == "50"
    assert project["reviews"][TRAINING]["history"] == before[TRAINING]["history"]
    assert any(h["action"].startswith("재분석") for h in rows(project)[TRAINING]["history"])
    assert project["affected_questions"] == [TRAINING]
    assert client.get(f"/api/projects/{pid}").json()["reviews"][WASTE] == before[WASTE]


@pytest.mark.parametrize("manual", [False, True], ids=["automatic-original", "human-linked-original"])
def test_same_bytes_replacement_keeps_invalidation_and_old_human_sources(client, manual):
    pid, project = setup(client)
    waste = rows(project)[WASTE]
    values = deepcopy(waste["saved"])
    document = project["documents"][1 if manual else 0]
    if manual:
        values["sources"] = [{"document_id": document["id"], "name": document["name"],
                              "version": 1, "page": 0, "manual": True}]
    project = save(client, pid, WASTE, values, True)
    before = deepcopy(project["reviews"][WASTE])
    original = client.get(f"/api/projects/{pid}/documents/{document['id']}/original").content
    project = client.post(f"/api/projects/{pid}/documents/{document['id']}/replace",
                          files={"file": (document["name"], original, "application/pdf")}).json()
    assert project["affected_questions"] == [WASTE]
    assert rows(project)[WASTE]["review_status"] == "again"
    for kind in ("xlsx", "pdf", "bundle"):
        assert client.get(f"/api/projects/{pid}/download/{kind}").status_code == 409
    project = analyze(client, pid)
    assert rows(project)[WASTE]["automatic"]["answer"] == "29.3"
    assert rows(project)[WASTE]["review_status"] == "again"
    assert project["affected_questions"] == [WASTE]
    assert project["reviews"][WASTE]["values"] == before["values"]
    assert project["reviews"][WASTE]["history"] == before["history"]
    assert client.get(f"/api/projects/{pid}/documents/{document['id']}/original?version=1").content == original
    stale_completion = client.put(f"/api/projects/{pid}/reviews/{WASTE}",
                                  json={"values": before["values"], "complete": True})
    assert stale_completion.status_code == 400
