# -*- coding: utf-8 -*-
"""대조군 실행기 회귀 테스트 — LLM 은 mock, 네트워크 호출 없음.

`tests/conftest.py` 가 외부 소켓을 막으므로 이 테스트는 실호출을 할 수 없다.
모델 호출은 전부 저장된 문자열(또는 monkeypatch 한 가짜 호출)로 대신한다.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_SPEC = importlib.util.spec_from_file_location(
    "baseline_generic_llm", REPO_ROOT / "scripts" / "baseline_generic_llm.py")
bg = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(bg)


# ── A 의 v1.1 모듈을 git 에서 꺼내 쓴다 (저장소에 남기지 않는다) ─────────────
# 대역을 쓰지 않는다. 검증 규칙을 우리가 다시 쓰면 "통과했다"가 거짓 신호가 된다.
# scripts/baseline_generic_llm.py 의 기본 해석 경로(고정 git ref)를 그대로 쓴다.


@pytest.fixture(scope="session")
def af():
    return bg.load_answer_format()


CASES = [
    {"qid": "Q-1", "question_text": "[Q-1] 첫 문항 — 설명."},
    {"qid": "Q-2", "question_text": "[Q-2] 둘째 문항 — 설명."},
    {"qid": "Q-3", "question_text": "[Q-3] 셋째 문항 — 설명."},
]
EXPECTED = tuple(c["qid"] for c in CASES)


def _convert(af_mod, parsed, parse_error="", unrecovered=frozenset(), cases=None):
    return bg.to_common_document(
        af_mod, parsed=parsed, parse_error=parse_error, cases=cases or CASES,
        stage="initial", framework="rba42", run_id="RUN_rep1",
        model_id="mock-model-1", data_source=bg.DATA_SOURCE["initial"],
        source_ref={"path": "outputs/eval/x/response_raw.json", "sha256": "0" * 64},
        unrecovered=set(unrecovered), company_answer_file="05_회사답변.pdf",
        produced_at="2026-10-07T00:00:00+09:00",
    )


# ── 1. 입력 조립: 문서 구분과 쪽 1-기준 ────────────────────────────────────
def test_recover_pages_from_chunk_sources_is_one_based():
    extraction = {
        "raw_text": "p1-a\np1-b\np2-a\np2-b\np2-c",
        "router_meta": {"chunk_sources": [
            {"page": 0, "line_start": 1, "line_end": 2},
            {"page": 1, "line_start": 1, "line_end": 3},
        ]},
    }
    pages, method, count = bg.recover_pages(extraction)
    assert method == "chunk_sources"
    assert count == 2
    assert pages == ["p1-a\np1-b", "p2-a\np2-b\np2-c"]


def test_recover_pages_single_page_from_upstage_usage():
    extraction = {"raw_text": "표 내용", "router_meta": {"dp_response": {"usage": {"pages": 1}}}}
    pages, method, count = bg.recover_pages(extraction)
    assert (method, count, pages) == ("single_page_upstage", 1, ["표 내용"])


def test_recover_pages_unrecovered_when_no_hint():
    extraction = {"raw_text": "본문", "router_meta": {}}
    pages, method, count = bg.recover_pages(extraction)
    assert method == "unrecovered"
    assert count is None
    assert pages == ["본문"]


def test_recover_pages_rejects_line_count_mismatch():
    extraction = {
        "raw_text": "a\nb\nc",
        "router_meta": {"chunk_sources": [{"page": 0, "line_start": 1, "line_end": 9}],
                        "dp_response": {"usage": {"pages": 1}}},
    }
    _, method, _ = bg.recover_pages(extraction)
    assert method == "single_page_upstage"      # 줄 수가 안 맞으면 쓰지 않는다


def test_documents_block_labels_pages_and_unknown():
    docs = [
        {"file_name": "01.pdf", "pages": ["가", "나"], "page_method": "chunk_sources"},
        {"file_name": "99.pdf", "pages": ["다"], "page_method": "unrecovered"},
    ]
    block = bg.build_documents_block(docs)
    assert "[문서 시작] 파일: 01.pdf | 쪽: 1" in block
    assert "[문서 끝] 파일: 01.pdf | 쪽: 2" in block
    assert "[문서 시작] 파일: 99.pdf | 쪽: 미상" in block
    assert "쪽: 0" not in block


def test_company_block_uses_different_markers():
    block = bg.build_company_block({"file_name": "05_회사답변.pdf", "pages": ["첫 쪽", "둘째 쪽"]})
    assert "[회사 답변 시작] 파일: 05_회사답변.pdf | 쪽: 1" in block
    assert "[회사 답변 끝] 파일: 05_회사답변.pdf | 쪽: 2" in block
    assert "[문서 시작]" not in block and "[문서 끝]" not in block


def test_render_user_prompt_substitutes_all_placeholders():
    tpl = "A {{DOCUMENTS}} B {{COMPANY_ANSWER}} C {{QUESTIONS}} D {{QUESTION_COUNT}}"
    out = bg.render_user_prompt(tpl, documents="DOC", company_answer="CA",
                                questions="QS", question_count=3)
    assert out == "A DOC B CA C QS D 3"


def test_render_user_prompt_fails_on_missing_placeholder():
    with pytest.raises(ValueError):
        bg.render_user_prompt("{{DOCUMENTS}}", documents="d", company_answer="c",
                              questions="q", question_count=1)


def test_questions_block_keeps_question_text_verbatim():
    block = bg.build_questions_block(CASES)
    assert block.splitlines()[0] == "- qid: Q-1 | 질문: [Q-1] 첫 문항 — 설명."
    assert len(block.splitlines()) == 3


def test_real_prompt_file_parses_and_renders():
    prompt = bg.load_prompt(REPO_ROOT / bg.DEFAULT_PROMPT)
    assert prompt["system"].strip()
    user = bg.render_user_prompt(prompt["user_template"], documents="D",
                                 company_answer="C", questions="Q", question_count=48)
    assert "D" in user and "C" in user and "Q" in user and "48" in user
    assert len(prompt["sha256"]) == 64


def test_real_cases_file_has_48_rows_in_framework_order():
    cases = bg.load_cases(REPO_ROOT / bg.DEFAULT_CASES)
    from esgenie.supplychain.frameworks.rba_self import RBA42
    assert [c["qid"] for c in cases] == [q.qid for q in RBA42.questions]
    assert [c["question_text"] for c in cases] == [q.text for q in RBA42.questions]
    assert len(cases) == 48


# ── 2. 저장 응답 → 공통 형식 변환 ──────────────────────────────────────────
def test_convert_structural_decisions(af):
    parsed = {"answers": [
        {"qid": "Q-1", "decision": "answer", "value": 12.5, "unit": "%",
         "period": "2026", "scope": "제1공장", "answer_text": "값 제출",
         "evidence": [{"file_name": "01.pdf", "page": 2, "quote": "원문"}]},
        {"qid": "Q-2", "decision": "answer", "value": True, "unit": "",
         "answer_text": "근거 없이 값만", "evidence": []},
        {"qid": "Q-3", "decision": "hold", "value": None, "unit": "",
         "answer_text": "자료에서 찾을 수 없다", "evidence": []},
    ]}
    doc, stats = _convert(af, parsed)
    assert af.validate_document(doc, EXPECTED) == []
    details = {a["qid"]: a["decision_detail"] for a in doc["answers"]}
    decisions = {a["qid"]: a["decision"] for a in doc["answers"]}
    assert details == {"Q-1": "confirmed", "Q-2": "unverified_submitted", "Q-3": "hold"}
    assert decisions == {"Q-1": "answer", "Q-2": "answer", "Q-3": "hold"}
    assert stats["detail_counts"]["confirmed"] == 1
    assert stats["detail_counts"]["unverified_submitted"] == 1
    assert stats["detail_counts"]["hold"] == 1
    assert stats["decision_counts"] == {"answer": 2, "hold": 1}
    q1 = doc["answers"][0]
    assert q1["boundary"] == {"period": "2026", "site": None, "target": None,
                              "denominator": None, "label": "제1공장", "completeness": None}
    assert q1["evidence"] == [{"file_name": "01.pdf", "page": 2, "quote": "원문"}]
    assert json.loads(q1["source"]["raw"])["qid"] == "Q-1"


def test_convert_not_applicable_and_meta(af):
    parsed = {"answers": [
        {"qid": "Q-1", "decision": "not_applicable", "value": None, "evidence": []},
        {"qid": "Q-2", "decision": "hold", "value": None, "evidence": []},
        {"qid": "Q-3", "decision": "hold", "value": None, "evidence": []},
    ]}
    doc, stats = _convert(af, parsed)
    assert af.validate_document(doc, EXPECTED) == []
    assert doc["answers"][0]["decision_detail"] == "not_applicable"
    assert doc["answers"][0]["decision"] == "na"
    assert stats["detail_counts"]["not_applicable"] == 1
    assert stats["decision_counts"] == {"na": 1, "hold": 2}
    assert doc["meta"]["system"] == "baseline_mock-model-1"
    assert doc["meta"]["model"] == "mock-model-1"
    assert doc["meta"]["stage"] == "initial"
    assert doc["meta"]["dataset_tag"] == "virtual" == bg.DATASET_TAG
    assert doc["meta"]["material_kind"] == "mixed" == bg.MATERIAL_KIND
    assert doc["meta"]["data_source"] == "촬영증빙_17건"
    assert doc["meta"]["notice"] == bg.COMPANY_NOTICE
    assert doc["meta"]["source_ref"]["sha256"] == "0" * 64
    assert doc["format_version"] == af.FORMAT_VERSION == "1.1"


def test_hedging_text_does_not_change_decision(af):
    """단서 문구는 decision_basis·source.raw 에만 남고 판정을 바꾸지 않는다."""
    parsed = {"answers": [
        {"qid": "Q-1", "decision": "answer", "value": 1, "unit": "건",
         "answer_text": "값은 1건이지만 추가 확인이 필요하다",
         "evidence": [{"file_name": "01.pdf", "page": 1, "quote": "q"}]},
        {"qid": "Q-2", "decision": "hold", "value": None, "evidence": []},
        {"qid": "Q-3", "decision": "hold", "value": None, "evidence": []},
    ]}
    doc, _ = _convert(af, parsed)
    assert doc["answers"][0]["decision_detail"] == "confirmed"
    assert doc["answers"][0]["decision"] == "answer"
    assert "추가 확인이 필요" in doc["answers"][0]["value_text"]
    assert "추가 확인이 필요" in doc["answers"][0]["source"]["raw"]


def test_unrecovered_file_citation_becomes_null_page(af):
    parsed = {"answers": [
        {"qid": "Q-1", "decision": "answer", "value": 1, "unit": "",
         "evidence": [{"file_name": "99.pdf", "page": 1, "quote": "q"},
                      {"file_name": "01.pdf", "page": 3, "quote": "q2"}]},
        {"qid": "Q-2", "decision": "hold", "evidence": []},
        {"qid": "Q-3", "decision": "hold", "evidence": []},
    ]}
    doc, stats = _convert(af, parsed, unrecovered={"99.pdf"})
    pages = {e["file_name"]: e["page"] for e in doc["answers"][0]["evidence"]}
    assert pages == {"99.pdf": None, "01.pdf": 3}
    assert stats["evidence_page_null"] == 1
    assert af.validate_document(doc, EXPECTED) == []


def test_bad_page_values_become_null_not_one(af):
    parsed = {"answers": [
        {"qid": "Q-1", "decision": "answer", "value": 1, "unit": "",
         "evidence": [{"file_name": "01.pdf", "page": 0, "quote": "q"},
                      {"file_name": "01.pdf", "page": "둘", "quote": "q"},
                      {"file_name": "01.pdf", "page": None, "quote": "q"}]},
        {"qid": "Q-2", "decision": "hold", "evidence": []},
        {"qid": "Q-3", "decision": "hold", "evidence": []},
    ]}
    doc, _ = _convert(af, parsed)
    assert [e["page"] for e in doc["answers"][0]["evidence"]] == [None, None, None]
    assert af.validate_document(doc, EXPECTED) == []


def test_company_answer_citation_is_counted_not_removed(af):
    parsed = {"answers": [
        {"qid": "Q-1", "decision": "answer", "value": 92, "unit": "%",
         "evidence": [{"file_name": "05_회사답변.pdf", "page": 1, "quote": "재활용률 92%"}]},
        {"qid": "Q-2", "decision": "hold", "evidence": []},
        {"qid": "Q-3", "decision": "hold", "evidence": []},
    ]}
    doc, stats = _convert(af, parsed)
    assert stats["cited_company_answer"] == 1
    assert doc["answers"][0]["decision_detail"] == "confirmed"   # 출력을 고치지 않는다
    assert doc["answers"][0]["decision"] == "answer"
    assert doc["answers"][0]["evidence"][0]["file_name"] == "05_회사답변.pdf"


# ── 3. 깨진 JSON ───────────────────────────────────────────────────────────
def test_parse_plain_json():
    obj, err = bg.parse_model_output('{"answers": []}')
    assert err == "" and obj == {"answers": []}


def test_parse_json_wrapped_in_code_fence():
    text = '설명입니다.\n```json\n{"answers": [{"qid": "Q-1"}]}\n```\n끝.'
    obj, err = bg.parse_model_output(text)
    assert err == ""
    assert obj["answers"][0]["qid"] == "Q-1"


def test_parse_truncated_json_is_failure_not_repaired():
    obj, err = bg.parse_model_output('{"answers": [{"qid": "Q-1", "decision": "ans')
    assert obj is None
    assert "json_decode_error" in err


def test_parse_empty_response():
    for text in ("", "   ", None):
        obj, err = bg.parse_model_output(text)
        assert obj is None and err == "empty_response"


def test_parse_top_level_list_is_failure():
    # 최상위가 배열이면 실패다. 중괄호 구간을 떼어 봐도 answers 가 없어 실패로 남는다.
    obj, err = bg.parse_model_output('[{"qid": "Q-1"}]')
    assert obj is None
    assert err in ("top_level_not_object", "answers_not_list", "no_json_found")


def test_parse_object_without_answers_is_failure():
    obj, err = bg.parse_model_output('{"result": "ok"}')
    assert obj is None and err == "answers_not_list"


def test_broken_json_makes_no_rows_and_validation_reports_it(af):
    """v1.1: 전체 파싱 실패면 어느 qid 도 행이 없다. 보류로 채우지 않는다."""
    obj, err = bg.parse_model_output("응답을 만들 수 없습니다")
    doc, stats = _convert(af, obj, parse_error=err)
    assert obj is None
    assert stats["rows"] == 0
    assert doc["answers"] == []
    assert sorted(stats["missing_qid"]) == ["Q-1", "Q-2", "Q-3"]
    assert stats["parse_error"]
    # 검증 실패를 그대로 드러낸다(고치지 않는다).
    codes = sorted({i["code"] for i in af.validate_document(doc, EXPECTED)})
    assert codes == ["missing_qid", "row_count"]


# ── 4. 문항 누락·중복·허용값 밖 ───────────────────────────────────────────
def test_missing_qid_makes_no_row_and_is_recorded(af):
    """v1.1 계약: 누락 qid 는 행을 만들지 않고 검증에서 누락으로 드러낸다."""
    parsed = {"answers": [{"qid": "Q-1", "decision": "hold", "evidence": []}]}
    doc, stats = _convert(af, parsed)
    assert stats["missing_qid"] == ["Q-2", "Q-3"]
    assert stats["rows"] == 1
    assert [a["qid"] for a in doc["answers"]] == ["Q-1"]      # 보류로 채우지 않는다
    issues = af.validate_document(doc, EXPECTED)
    missing = sorted(i["where"] for i in issues if i["code"] == "missing_qid")
    assert missing == ["answers(Q-2)", "answers(Q-3)"]
    assert any(i["code"] == "row_count" for i in issues)


def test_duplicate_qid_recorded_and_first_row_used(af):
    parsed = {"answers": [
        {"qid": "Q-1", "decision": "hold", "evidence": []},
        {"qid": "Q-1", "decision": "answer", "value": 9, "evidence": []},
        {"qid": "Q-2", "decision": "hold", "evidence": []},
        {"qid": "Q-3", "decision": "hold", "evidence": []},
    ]}
    doc, stats = _convert(af, parsed)
    assert stats["duplicate_qid"] == ["Q-1"]
    q1 = doc["answers"][0]
    assert q1["decision"] == "hold"
    assert "중복 응답 2건" in q1["decision_basis"]
    assert len(doc["answers"]) == 3        # 출력 문서는 48행 자리 그대로(중복을 늘리지 않는다)
    assert af.validate_document(doc, EXPECTED) == []


def test_unknown_qid_recorded(af):
    parsed = {"answers": [
        {"qid": "Q-1", "decision": "hold", "evidence": []},
        {"qid": "Q-2", "decision": "hold", "evidence": []},
        {"qid": "Q-3", "decision": "hold", "evidence": []},
        {"qid": "Q-99", "decision": "hold", "evidence": []},
    ]}
    doc, stats = _convert(af, parsed)
    assert stats["unknown_qid"] == ["Q-99"]
    assert all(a["qid"] != "Q-99" for a in doc["answers"])


def test_out_of_range_model_decision_is_unparsed(af):
    parsed = {"answers": [
        {"qid": "Q-1", "decision": "maybe", "evidence": []},
        {"qid": "Q-2", "decision": "hold", "evidence": []},
        {"qid": "Q-3", "decision": "hold", "evidence": []},
    ]}
    doc, stats = _convert(af, parsed)
    assert doc["answers"][0]["decision_detail"] == "unparsed"
    assert doc["answers"][0]["decision"] is None          # 보류와 외부 값부터 다르다
    assert "decision" in doc["answers"][0]                # 필드를 생략하지 않는다
    assert "허용값" in doc["answers"][0]["decision_basis"]
    assert stats["detail_counts"]["unparsed"] == 1
    assert stats["decision_counts"] == {"null": 1, "hold": 2}
    assert af.validate_document(doc, EXPECTED) == []


# ── 5. 반복 간 판정 변경 수 ────────────────────────────────────────────────
def test_count_decision_flips_uses_detail_not_external_three_values():
    def mk(d1, d2, d3):
        return {"answers": [{"qid": "Q-1", "decision_detail": d1},
                            {"qid": "Q-2", "decision_detail": d2},
                            {"qid": "Q-3", "decision_detail": d3}]}
    out = bg.count_decision_flips([mk("hold", "confirmed", "hold"),
                                   mk("confirmed", "confirmed", "hold"),
                                   mk("hold", "confirmed", "hold")])
    assert out["repeats"] == 3
    assert out["changed_rows"] == 1
    assert out["changed_qids"] == ["Q-1"]

    # confirmed ↔ unverified_submitted 는 외부 3값으로는 둘 다 answer 라 안 보인다.
    out2 = bg.count_decision_flips([mk("confirmed", "hold", "hold"),
                                    mk("unverified_submitted", "hold", "hold")])
    assert out2["changed_rows"] == 1 and out2["changed_qids"] == ["Q-1"]


def test_count_decision_flips_counts_absent_row_as_change():
    docs = [{"answers": [{"qid": "Q-1", "decision_detail": "hold"},
                         {"qid": "Q-2", "decision_detail": "hold"}]},
            {"answers": [{"qid": "Q-1", "decision_detail": "hold"}]}]
    out = bg.count_decision_flips(docs)
    assert out["changed_rows"] == 1 and out["changed_qids"] == ["Q-2"]


def test_count_decision_flips_single_repeat():
    out = bg.count_decision_flips([{"answers": [{"qid": "Q-1", "decision_detail": "hold"}]}])
    assert out == {"repeats": 1, "changed_rows": 0, "changed_qids": []}


# ── 6. 키 값이 저장 파일에 남지 않는다 ────────────────────────────────────
def _fake_pipeline(tmp_path: Path) -> Path:
    stage = tmp_path / "initial"
    stage.mkdir()
    pdf = tmp_path / "05_회사답변.pdf"
    pdf.write_bytes(b"%PDF-1.4 not-a-real-pdf")
    (stage / "pipeline.json").write_text(json.dumps({"ocr_extractions": [
        {"source_file": "01.pdf", "channel": "unstructured", "raw_text": "가\n나",
         "router_meta": {"chunk_sources": [{"page": 0, "line_start": 1, "line_end": 2}],
                         "source_sha256": "a" * 64}},
        {"source_file": "02.pdf", "channel": "structured", "raw_text": "표",
         "router_meta": {"dp_response": {"usage": {"pages": 1}}, "source_sha256": "b" * 64}},
    ]}, ensure_ascii=False), encoding="utf-8")
    import hashlib
    (stage / "environment.json").write_text(json.dumps({"inputs": [
        {"name": "05_회사답변.pdf", "role": "company_answer", "path": str(pdf),
         "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest()},
    ]}, ensure_ascii=False), encoding="utf-8")
    return stage / "pipeline.json"


def _fake_ca(path):
    """05 추출 대역 — PDF 가 진짜가 아니어도 sha256 은 실제 파일로 맞춘다."""
    import hashlib
    return {
        "file_name": "05_회사답변.pdf", "pdf_path": str(path),
        "pdf_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "pages": ["회사 답변 본문"], "page_count": 1, "chars": 8,
        "text": "회사 답변 본문", "text_sha256": "c" * 64,
        "extractor": {"function": "esgenie/supplychain/claims.py::_extract_text",
                      "route_taken": "pymupdf"},
    }


def _cases_file(tmp_path: Path) -> Path:
    p = tmp_path / "cases.csv"
    with p.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["qid", "question_text"], lineterminator="\n")
        w.writeheader()
        w.writerows(CASES)
    return p


def test_saved_files_contain_no_api_key(tmp_path, monkeypatch, af):
    secret = "sk-TESTKEY-must-not-appear-1234567890"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    monkeypatch.setattr(bg, "extract_company_answer", lambda path: {
        "file_name": "05_회사답변.pdf", "pdf_path": str(path),
        "pdf_sha256": __import__("hashlib").sha256(Path(path).read_bytes()).hexdigest(),
        "pages": ["회사 답변 본문"], "page_count": 1, "chars": 8,
        "text": "회사 답변 본문", "text_sha256": "c" * 64,
        "extractor": {"function": "esgenie/supplychain/claims.py::_extract_text",
                      "route_taken": "pymupdf"},
    })
    monkeypatch.setattr(bg, "call_model", lambda provider, model, system, user: {
        "text": json.dumps({"answers": [
            {"qid": "Q-1", "decision": "answer", "value": 1, "unit": "",
             "evidence": [{"file_name": "01.pdf", "page": 1, "quote": "가"}]},
            {"qid": "Q-2", "decision": "hold", "evidence": []},
            {"qid": "Q-3", "decision": "not_applicable", "evidence": []},
        ]}, ensure_ascii=False),
        "model": "mock-model-1", "prompt_tokens": 11, "completion_tokens": 7,
        "total_tokens": 18, "response_id": "resp_1", "raw": {"id": "resp_1"},
    })

    af_path = Path(af.__source_path__)
    out = tmp_path / "out"
    rc = bg.main([
        "--pipeline-json", str(_fake_pipeline(tmp_path)), "--stage", "initial",
        "--model", "mock-model-1", "--provider", "openai", "--repeats", "2",
        "--out", str(out), "--run-id", "RUN", "--cases", str(_cases_file(tmp_path)),
        "--prompt", str(REPO_ROOT / bg.DEFAULT_PROMPT), "--answer-format", str(af_path),
        "--max-input-tokens", "100000",
    ])
    assert rc == 0

    for path in sorted(out.rglob("*")):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8"), path

    rep1 = out / "RUN" / "rep_1"
    doc = json.loads((rep1 / "common_v1_initial.json").read_text(encoding="utf-8"))
    assert af.validate_document(doc, EXPECTED) == []
    assert doc["meta"]["run_id"] == "RUN_rep1"
    assert json.loads((out / "RUN" / "rep_2" / "common_v1_initial.json")
                      .read_text(encoding="utf-8"))["meta"]["run_id"] == "RUN_rep2"
    log = json.loads((rep1 / "run_log.json").read_text(encoding="utf-8"))
    assert log["prompt_tokens"] == 11 and log["completion_tokens"] == 7
    assert log["model_returned"] == "mock-model-1"
    assert log["called_at_kst"].endswith("+09:00")
    req = json.loads((rep1 / "request.json").read_text(encoding="utf-8"))
    assert len(req["prompt_sha256"]) == 64
    assert req["page_methods"] == {"01.pdf": "chunk_sources", "02.pdf": "single_page_upstage"}
    summary = json.loads((out / "RUN" / "repeat_summary.json").read_text(encoding="utf-8"))
    assert summary["repeats"] == 2 and summary["changed_rows"] == 0


# ── 7. --rescore-only 는 저장 응답만 쓴다 ─────────────────────────────────
def test_rescore_only_rebuilds_from_saved_response(tmp_path, monkeypatch, af):
    monkeypatch.setattr(bg, "extract_company_answer", lambda path: {
        "file_name": "05_회사답변.pdf", "pdf_path": str(path),
        "pdf_sha256": __import__("hashlib").sha256(Path(path).read_bytes()).hexdigest(),
        "pages": ["본문"], "page_count": 1, "chars": 2, "text": "본문",
        "text_sha256": "d" * 64, "extractor": {"route_taken": "pymupdf"},
    })
    monkeypatch.setattr(bg, "call_model", lambda provider, model, system, user: {
        "text": '{"answers": [{"qid": "Q-1", "decision": "hold", "evidence": []},'
                '{"qid": "Q-2", "decision": "hold", "evidence": []},'
                '{"qid": "Q-3", "decision": "hold", "evidence": []}]}',
        "model": "mock-model-1", "prompt_tokens": 5, "completion_tokens": 3,
        "total_tokens": 8, "response_id": "r", "raw": None,
    })
    af_path = Path(af.__source_path__)
    out = tmp_path / "out"
    cases = _cases_file(tmp_path)
    assert bg.main([
        "--pipeline-json", str(_fake_pipeline(tmp_path)), "--stage", "initial",
        "--model", "mock-model-1", "--out", str(out), "--run-id", "RUN",
        "--cases", str(cases), "--prompt", str(REPO_ROOT / bg.DEFAULT_PROMPT),
        "--answer-format", str(af_path), "--max-input-tokens", "100000",
    ]) == 0

    target = out / "RUN" / "rep_1" / "common_v1_initial.json"
    target.unlink()

    def boom(*a, **k):                       # 재채점은 호출하지 않는다
        raise AssertionError("재채점이 모델을 호출했다")
    monkeypatch.setattr(bg, "call_model", boom)
    assert bg.main(["--rescore-only", str(out / "RUN"), "--cases", str(cases),
                    "--answer-format", str(af_path)]) == 0
    assert target.is_file()
    doc = json.loads(target.read_text(encoding="utf-8"))
    assert af.validate_document(doc, EXPECTED) == []
    assert all(a["decision"] == "hold" for a in doc["answers"])
    assert (out / "RUN" / "rep_1" / "rescore_log.json").is_file()


# ── 8. 컨텍스트 초과면 중단한다(자동 분할 금지) ───────────────────────────
def test_context_limit_aborts_without_calling(tmp_path, monkeypatch, af):
    monkeypatch.setattr(bg, "extract_company_answer", lambda path: {
        "file_name": "05_회사답변.pdf", "pdf_path": str(path),
        "pdf_sha256": __import__("hashlib").sha256(Path(path).read_bytes()).hexdigest(),
        "pages": ["본문"], "page_count": 1, "chars": 2, "text": "본문",
        "text_sha256": "e" * 64, "extractor": {"route_taken": "pymupdf"},
    })

    def boom(*a, **k):
        raise AssertionError("중단해야 하는데 호출했다")
    monkeypatch.setattr(bg, "call_model", boom)
    out = tmp_path / "out"
    rc = bg.main([
        "--pipeline-json", str(_fake_pipeline(tmp_path)), "--stage", "initial",
        "--model", "m", "--out", str(out), "--run-id", "RUN",
        "--cases", str(_cases_file(tmp_path)),
        "--prompt", str(REPO_ROOT / bg.DEFAULT_PROMPT),
        "--answer-format", str(Path(af.__source_path__)), "--max-input-tokens", "1",
    ])
    assert rc == 2
    log = json.loads((out / "RUN" / "run_log.json").read_text(encoding="utf-8"))
    assert log["status"] == "aborted_context_limit"
    assert log["max_input_tokens"] == 1
    assert not list((out / "RUN").glob("rep_*"))


# ── 9. 05 추출은 ESGenie 함수를 그대로 쓴다 ───────────────────────────────
def test_company_answer_uses_esgenie_claims_extract_text(tmp_path, monkeypatch):
    from esgenie.supplychain import claims as _claims
    monkeypatch.setattr(_claims, "_extract_text", lambda p: "쪽1 본문\f쪽2 본문")
    pdf = tmp_path / "05_x.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    ca = bg.extract_company_answer(pdf)
    assert ca["pages"] == ["쪽1 본문", "쪽2 본문"]
    assert ca["page_count"] == 2
    assert ca["extractor"]["function"] == "esgenie/supplychain/claims.py::_extract_text"
    assert ca["extractor"]["page_separator"] == "\\f"
    assert len(ca["pdf_sha256"]) == 64 and len(ca["text_sha256"]) == 64


# ── 10. v1.1 — 6값 → 3값 대응과 A 모듈 의존 ────────────────────────────────
def test_all_six_details_map_to_three_values_via_a_module(af):
    """대응은 A 모듈의 표 하나뿐이다. 우리 코드에 다시 쓰지 않는다."""
    expected = {
        "confirmed": "answer",
        "unverified_submitted": "answer",
        "hold": "hold",
        "not_applicable": "na",
        "unparsed": None,
        "undetermined": None,
    }
    assert set(af.DECISION_DETAILS) == set(expected)
    assert {d: af.decision_for_detail(d) for d in af.DECISION_DETAILS} == expected
    assert dict(af.DETAIL_TO_DECISION) == expected
    assert set(af.DECISIONS) == {"answer", "hold", "na"}


def test_rows_carry_both_fields_and_match_the_table(af):
    """만든 모든 행에서 decision 이 decision_detail 의 표 값과 같다."""
    cases = [{"qid": "Q-%d" % i, "question_text": "t"} for i in range(1, 5)]
    parsed = {"answers": [
        {"qid": "Q-1", "decision": "answer", "value": 1, "unit": "",
         "evidence": [{"file_name": "01.pdf", "page": 1, "quote": "q"}]},
        {"qid": "Q-2", "decision": "answer", "value": 2, "unit": "", "evidence": []},
        {"qid": "Q-3", "decision": "hold", "evidence": []},
        {"qid": "Q-4", "decision": "not_applicable", "evidence": []},
    ]}
    doc, _ = _convert(af, parsed, cases=cases)
    for a in doc["answers"]:
        assert a["decision"] == af.decision_for_detail(a["decision_detail"])
        assert a["decision_detail"] in af.DECISION_DETAILS
    assert af.validate_document(doc, tuple(c["qid"] for c in cases)) == []


def test_unparsed_row_has_null_decision_and_detail_unparsed(af):
    """파싱 실패 행: decision=null 을 **명시**하고 detail 로 사유를 남긴다."""
    parsed = {"answers": [
        {"qid": "Q-1", "decision": 123, "evidence": []},      # 허용값 밖
        {"qid": "Q-2", "decision": "hold", "evidence": []},
        {"qid": "Q-3", "decision": "hold", "evidence": []},
    ]}
    doc, _ = _convert(af, parsed)
    row = doc["answers"][0]
    assert row["decision"] is None and "decision" in row
    assert row["decision_detail"] == "unparsed"
    assert af.validate_document(doc, EXPECTED) == []


def test_dataset_tag_is_required_by_validator(af, monkeypatch):
    """dataset_tag 가 비면 v1.1 검증이 missing_field 로 떨어진다."""
    parsed = {"answers": [{"qid": q, "decision": "hold", "evidence": []} for q in EXPECTED]}
    doc, _ = _convert(af, parsed)
    assert af.validate_document(doc, EXPECTED) == []
    broken = json.loads(json.dumps(doc))
    del broken["meta"]["dataset_tag"]
    issues = af.validate_document(broken, EXPECTED)
    assert any(i["code"] == "missing_field" and i["where"] == "meta.dataset_tag"
               for i in issues)
    bad = json.loads(json.dumps(doc))
    bad["meta"]["dataset_tag"] = "made_up"
    assert any(i["code"] == "bad_value" and i["where"] == "meta.dataset_tag"
               for i in af.validate_document(bad, EXPECTED))


def test_constants_are_inside_validator_enums(af):
    assert bg.DATASET_TAG in af.DATASET_TAGS
    assert bg.MATERIAL_KIND in af.MATERIAL_KINDS


def test_answer_format_loader_fails_when_module_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        bg.load_answer_format(str(tmp_path / "없는경로"))


def test_answer_format_loader_fails_when_required_names_absent(tmp_path):
    """표를 우리가 다시 쓰지 않으므로, 이름이 없으면 건너뛰지 않고 실패한다."""
    stub = tmp_path / "answer_format.py"
    stub.write_text("FORMAT_VERSION = '1.1-draft'\n", encoding="utf-8")
    with pytest.raises(AttributeError) as exc:
        bg.load_answer_format(str(stub))
    assert "decision_for_detail" in str(exc.value)


def test_answer_format_default_is_a_v11_from_pinned_ref(af):
    """확정판(c1bfa67, 2026-10-10)을 쓴다. FORMAT_VERSION 에 -draft 접미사가 없다."""
    rec = bg.answer_format_record(af)
    assert rec["format_version"] == "1.1"
    assert rec["origin"].startswith("git:") or rec["origin"].startswith("repo:")
    assert len(rec["sha256"]) == 64
    if rec["origin"].startswith("git:"):
        assert rec["git_commit"] == bg.ANSWER_FORMAT_GIT_REF
        assert len(rec["git_blob"]) == 40


def test_format_version_is_1_1_not_draft(af):
    """P3-2c: A가 2026-10-10에 -draft를 뗐다(c1bfa67). SUPPORTED_MAJOR는 그대로 '1'이다."""
    assert af.FORMAT_VERSION == "1.1"
    assert "draft" not in af.FORMAT_VERSION
    assert af.SUPPORTED_MAJOR == "1"
    assert af.FORMAT_VERSION.split(".")[0] == af.SUPPORTED_MAJOR


# ── 11. 컨텍스트 한도를 모르면 실호출을 거부한다 ──────────────────────────
def test_resolve_input_limit_refuses_unknown_model():
    """P3-2c: gpt-4.1-mini·gpt-4.1 은 공식 문서로 확인해 표에 넣었다(README §6).
    표에 없는 모델은 여전히 거부해야 한다 — 추측해 넣지 않는다."""
    assert "모르는-모델" not in bg.MODEL_CONTEXT_LIMITS
    with pytest.raises(RuntimeError, match="컨텍스트 한도를 모른다"):
        bg.resolve_input_limit("모르는-모델", None)


def test_model_context_limits_has_confirmed_azure_gpt41_family():
    """P3-2c: 같은 모델(gpt-4.1-mini)·상위 모델(gpt-4.1)의 한도가 출처·확인 날짜와 함께 있다."""
    for model in ("gpt-4.1-mini", "gpt-4.1"):
        row = bg.MODEL_CONTEXT_LIMITS[model]
        assert row["max_input_tokens"] > 0
        assert row["source"]
        assert row["checked_on"]


def test_resolve_input_limit_uses_cli_then_table(monkeypatch):
    assert bg.resolve_input_limit("아무모델", 1234) == (1234, "cli")
    with pytest.raises(ValueError):
        bg.resolve_input_limit("아무모델", 0)
    monkeypatch.setitem(bg.MODEL_CONTEXT_LIMITS, "표에있는모델",
                        {"max_input_tokens": 999, "source": "u", "checked_on": "2026-10-09"})
    assert bg.resolve_input_limit("표에있는모델", None) == (999, "table:2026-10-09")


def test_live_run_refuses_without_limit(tmp_path, monkeypatch, af):
    monkeypatch.setattr(bg, "extract_company_answer", _fake_ca)

    def boom(*a, **k):
        raise AssertionError("한도를 모르는데 호출했다")
    monkeypatch.setattr(bg, "call_model", boom)
    rc = bg.main([
        "--pipeline-json", str(_fake_pipeline(tmp_path)), "--stage", "initial",
        "--model", "표에-없는-모델", "--out", str(tmp_path / "out"), "--run-id", "RUN",
        "--cases", str(_cases_file(tmp_path)),
        "--prompt", str(REPO_ROOT / bg.DEFAULT_PROMPT),
        "--answer-format", str(Path(af.__source_path__)),
    ])
    assert rc == 2
    assert not (tmp_path / "out" / "RUN" / "rep_1").exists()


def test_dry_run_allowed_without_limit(tmp_path, monkeypatch, af):
    """dry-run 은 호출이 없고, 표를 채울 입력 길이를 재는 것이 목적이다."""
    monkeypatch.setattr(bg, "extract_company_answer", _fake_ca)

    def boom(*a, **k):
        raise AssertionError("dry-run 이 호출했다")
    monkeypatch.setattr(bg, "call_model", boom)
    out = tmp_path / "out"
    rc = bg.main([
        "--pipeline-json", str(_fake_pipeline(tmp_path)), "--stage", "initial",
        "--model", "표에-없는-모델", "--out", str(out), "--run-id", "RUN",
        "--cases", str(_cases_file(tmp_path)),
        "--prompt", str(REPO_ROOT / bg.DEFAULT_PROMPT),
        "--answer-format", str(Path(af.__source_path__)), "--dry-run",
    ])
    assert rc == 0
    req = json.loads((out / "RUN" / "rep_1" / "request.json").read_text(encoding="utf-8"))
    assert req["max_input_tokens"] is None
    assert req["max_input_tokens_source"] == "dry_run_unchecked"
    assert req["dataset_tag"] == bg.DATASET_TAG
    assert req["material_kind"] == bg.MATERIAL_KIND
    assert req["answer_format"]["format_version"] == "1.1"


def test_company_answer_sha_mismatch_aborts(tmp_path, monkeypatch, af):
    monkeypatch.setattr(bg, "extract_company_answer", lambda path: {
        "file_name": "05_회사답변.pdf", "pdf_path": str(path),
        "pdf_sha256": "f" * 64,                      # environment.json 과 다르다
        "pages": ["본문"], "page_count": 1, "chars": 2, "text": "본문",
        "text_sha256": "f" * 64, "extractor": {},
    })
    pipeline = _fake_pipeline(tmp_path)

    class _Args:
        pipeline_json = str(pipeline)
        cases = str(_cases_file(tmp_path))
        prompt = str(REPO_ROOT / bg.DEFAULT_PROMPT)

    with pytest.raises(RuntimeError, match="sha256"):
        bg.build_inputs(_Args())


# ── 12. 수동 채팅 실행(--provider manual) — API 호출 없음 ─────────────────
def _manual_export(pipeline, cases, out, repeats=3):
    return bg.main([
        "--provider", "manual", "--pipeline-json", str(pipeline), "--stage", "initial",
        "--repeats", str(repeats), "--out", str(out), "--cases", str(cases),
        "--prompt", str(REPO_ROOT / bg.DEFAULT_PROMPT),
    ])


def _no_call(*a, **k):
    raise AssertionError("수동 실행이 API 를 불렀다")


def test_manual_export_matches_api_path_messages(tmp_path, monkeypatch, af):
    """붙여넣기 파일의 system·user 가 API 경로에서 보낼 문자열과 한 글자도 다르지 않다."""
    monkeypatch.setattr(bg, "extract_company_answer", _fake_ca)
    pipeline, cases = _fake_pipeline(tmp_path), _cases_file(tmp_path)

    sent = {}

    def capture(provider, model, system, user):
        sent["system"], sent["user"] = system, user
        return {"text": '{"answers": []}', "model": "mock-model-1", "prompt_tokens": 1,
                "completion_tokens": 1, "total_tokens": 2, "response_id": "r", "raw": None}
    monkeypatch.setattr(bg, "call_model", capture)
    rc = bg.main([
        "--pipeline-json", str(pipeline), "--stage", "initial", "--model", "mock-model-1",
        "--out", str(tmp_path / "api"), "--run-id", "API", "--cases", str(cases),
        "--prompt", str(REPO_ROOT / bg.DEFAULT_PROMPT), "--max-input-tokens", "100000",
    ])
    assert rc == 1                       # 빈 answers → 누락 검증 실패(정상), 메시지는 잡혔다
    assert sent["system"] and sent["user"]

    monkeypatch.setattr(bg, "call_model", _no_call)
    out = tmp_path / "manual"
    assert _manual_export(pipeline, cases, out) == 0

    expected = (bg.MANUAL_SYSTEM_HEADER + "\n" + sent["system"] + "\n\n"
                + bg.MANUAL_USER_HEADER + "\n" + sent["user"])
    shas = set()
    for k in (1, 2, 3):
        d = out / f"MANUAL_claude_initial_rep{k}"
        raw = (d / "paste_input.txt").read_bytes()
        assert raw == expected.encode("utf-8")        # 바이트까지 같다(줄바꿈 변환 없음)
        sha = hashlib.sha256(raw).hexdigest()
        shas.add(sha)
        req = json.loads((d / "request.json").read_text(encoding="utf-8"))
        assert req["provider"] == "manual" and req["run_id"] == d.name
        assert req["temperature"] is None
        assert req["temperature_note"] == bg.MANUAL_TEMPERATURE_NOTE
        assert req["paste_input_sha256"] == sha
        assert req["system_sha256"] == hashlib.sha256(sent["system"].encode()).hexdigest()
        assert req["user_sha256"] == hashlib.sha256(sent["user"].encode()).hexdigest()
        assert req["input_tokens_estimate"] > 0 and len(req["prompt_sha256"]) == 64
    assert len(shas) == 1                             # 같은 단계 rep1~3 은 같은 입력


def test_manual_export_refuses_to_overwrite_human_response(tmp_path, monkeypatch, af):
    monkeypatch.setattr(bg, "extract_company_answer", _fake_ca)
    monkeypatch.setattr(bg, "call_model", _no_call)
    pipeline, cases = _fake_pipeline(tmp_path), _cases_file(tmp_path)
    out = tmp_path / "manual"
    assert _manual_export(pipeline, cases, out, repeats=1) == 0
    d = out / "MANUAL_claude_initial_rep1"
    (d / "response.txt").write_text("사람이 넣은 답", encoding="utf-8")
    assert _manual_export(pipeline, cases, out, repeats=1) == 2
    assert (d / "response.txt").read_text(encoding="utf-8") == "사람이 넣은 답"


def _exported_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(bg, "extract_company_answer", _fake_ca)
    monkeypatch.setattr(bg, "call_model", _no_call)
    pipeline, cases = _fake_pipeline(tmp_path), _cases_file(tmp_path)
    out = tmp_path / "manual"
    assert _manual_export(pipeline, cases, out, repeats=1) == 0
    return out / "MANUAL_claude_initial_rep1", cases


def _write_meta(d, model="Claude Opus 4.1", run_at="2026-10-13 15:00", cont="아니오"):
    (d / "meta.txt").write_text(f"모델: {model}\n실행 시각: {run_at}\n이어받음: {cont}\n",
                                encoding="utf-8")


def _import(d, cases):
    return bg.main(["--provider", "manual", "--import", str(d), "--cases", str(cases)])


_GOOD_RESPONSE = json.dumps({"answers": [
    {"qid": "Q-1", "decision": "answer", "value": 1, "unit": "",
     "evidence": [{"file_name": "01.pdf", "page": 1, "quote": "가"}]},
    {"qid": "Q-2", "decision": "hold", "evidence": []},
    {"qid": "Q-3", "decision": "not_applicable", "evidence": []},
]}, ensure_ascii=False)


def test_manual_import_builds_valid_common_doc(tmp_path, monkeypatch, af):
    d, cases = _exported_dir(tmp_path, monkeypatch)
    (d / "response.txt").write_text("```json\n" + _GOOD_RESPONSE + "\n```", encoding="utf-8")
    _write_meta(d)
    assert _import(d, cases) == 0

    doc = json.loads((d / "common_v1_initial.json").read_text(encoding="utf-8"))
    assert af.FORMAT_VERSION == "1.1"
    assert af.validate_document(doc, EXPECTED) == []
    assert doc["meta"]["system"] == "baseline_claude-opus-4-1"
    assert doc["meta"]["model"] == "Claude Opus 4.1"          # meta.txt 값 그대로
    assert doc["meta"]["run_id"] == "MANUAL_claude_initial_rep1"
    assert doc["meta"]["adapter"]["provider"] == "manual"
    assert doc["meta"]["notice"] == bg.MANUAL_NOTICE
    assert {a["qid"]: a["decision_detail"] for a in doc["answers"]} == {
        "Q-1": "confirmed", "Q-2": "hold", "Q-3": "not_applicable"}

    raw = json.loads((d / "response_raw.json").read_text(encoding="utf-8"))
    assert raw["response"]["prompt_tokens"] is None
    assert raw["response"]["completion_tokens"] is None
    assert raw["temperature"] is None
    assert raw["temperature_note"] == bg.MANUAL_TEMPERATURE_NOTE
    assert raw["called_at_kst"] == "2026-10-13 15:00"
    assert raw["continued"] is False
    log = json.loads((d / "run_log.json").read_text(encoding="utf-8"))
    assert log["prompt_tokens"] is None and log["temperature"] is None
    assert log["validate_issues"] == []
    assert not (d / "validate_issues.json").exists()


def test_manual_import_parse_failure_is_recorded_not_fixed(tmp_path, monkeypatch, af):
    d, cases = _exported_dir(tmp_path, monkeypatch)
    broken = '응답이 중간에 끊겼습니다 {"answers": [{"qid": "Q-1", "decis'
    (d / "response.txt").write_text(broken, encoding="utf-8")
    _write_meta(d)
    assert _import(d, cases) == 1                     # 검증 실패를 그대로 드러낸다

    doc = json.loads((d / "common_v1_initial.json").read_text(encoding="utf-8"))
    assert doc["answers"] == []                       # 보류로 채우지 않는다
    issues = json.loads((d / "validate_issues.json").read_text(encoding="utf-8"))
    assert sorted({i["code"] for i in issues}) == ["missing_qid", "row_count"]
    log = json.loads((d / "run_log.json").read_text(encoding="utf-8"))
    assert log["parse"]["parse_error"]
    assert sorted(log["parse"]["missing_qid"]) == ["Q-1", "Q-2", "Q-3"]
    raw = json.loads((d / "response_raw.json").read_text(encoding="utf-8"))
    assert raw["response"]["text"] == broken          # 원문을 고치지 않는다


def test_manual_import_continued_response(tmp_path, monkeypatch, af):
    """'계속'으로 이어 받은 두 조각을 사람이 이어 붙인 응답. 이어받음 표시가 기록에 남는다."""
    d, cases = _exported_dir(tmp_path, monkeypatch)
    half = len(_GOOD_RESPONSE) // 2
    (d / "response.txt").write_text(_GOOD_RESPONSE[:half] + _GOOD_RESPONSE[half:],
                                    encoding="utf-8")
    _write_meta(d, cont="예")
    assert _import(d, cases) == 0
    raw = json.loads((d / "response_raw.json").read_text(encoding="utf-8"))
    log = json.loads((d / "run_log.json").read_text(encoding="utf-8"))
    assert raw["continued"] is True and log["continued"] is True
    assert "이어받음: 예" in raw["meta_txt"]


@pytest.mark.parametrize("meta_text", [
    None,                                                           # meta.txt 없음
    "모델: Claude Opus 4.1\n이어받음: 아니오\n",                        # 실행 시각 없음
    "실행 시각: 2026-10-13 15:00\n이어받음: 아니오\n",                   # 모델 없음
    "모델: Claude Opus 4.1\n실행 시각: 2026-10-13\n",                   # 이어받음 없음
    "모델: Claude Opus 4.1\n실행 시각: 2026-10-13\n이어받음: 아마\n",     # 값 틀림
])
def test_manual_import_fails_without_complete_meta(tmp_path, monkeypatch, af, meta_text):
    d, cases = _exported_dir(tmp_path, monkeypatch)
    (d / "response.txt").write_text(_GOOD_RESPONSE, encoding="utf-8")
    if meta_text is not None:
        (d / "meta.txt").write_text(meta_text, encoding="utf-8")
    assert _import(d, cases) == 2
    for name in ("response_raw.json", "common_v1_initial.json", "run_log.json"):
        assert not (d / name).exists(), name           # 아무것도 쓰기 전에 멈춘다


def test_manual_import_requires_response_file(tmp_path, monkeypatch, af):
    d, cases = _exported_dir(tmp_path, monkeypatch)
    _write_meta(d)
    assert _import(d, cases) == 2
    assert not (d / "common_v1_initial.json").exists()


def test_model_slug():
    assert bg.model_slug("Claude Opus 4.1") == "claude-opus-4-1"
    assert bg.model_slug("  Claude   Sonnet 4.5 ") == "claude-sonnet-4-5"
    with pytest.raises(ValueError):
        bg.model_slug("  ---  ")
