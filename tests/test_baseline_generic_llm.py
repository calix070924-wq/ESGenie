# -*- coding: utf-8 -*-
"""대조군 실행기 회귀 테스트 — LLM 은 mock, 네트워크 호출 없음.

`tests/conftest.py` 가 외부 소켓을 막으므로 이 테스트는 실호출을 할 수 없다.
모델 호출은 전부 저장된 문자열(또는 monkeypatch 한 가짜 호출)로 대신한다.
"""
from __future__ import annotations

import csv
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


# ── 최소 answer_format 대역 ─────────────────────────────────────────────────
# A 가 소유하는 모듈이 저장소에 아직 없다(A 브랜치에 있다). 변환 단계가 요구하는
# 생성 함수와 검증 규칙만 공통 형식 v1 초안 문서(§2·§3·§4·§5·§6)대로 세운 대역을 쓴다.
# 실제 실행에서는 `--answer-format` 또는 `ESGENIE_EVAL_ANSWER_FORMAT` 로 A 의 모듈을 쓴다.
_AF_SOURCE = '''
FORMAT_VERSION = "1.0-draft"
SUPPORTED_MAJOR = "1"
STAGES = ("initial", "followup")
DECISIONS = ("confirmed", "unverified_submitted", "hold", "not_applicable",
             "unparsed", "undetermined")
COMPLETENESS = ("total", "partial", "unknown")
META_REQUIRED = ("system", "run_id", "stage", "framework", "data_source")
BOUNDARY_FIELDS = ("period", "site", "target", "denominator", "label", "completeness")


def _issue(code, where, detail):
    return {"code": code, "where": where, "detail": detail}


def _check_evidence(items, where, field):
    out = []
    if items is None:
        return out
    if not isinstance(items, list):
        return [_issue("bad_type", where, field + "는 배열이어야 한다")]
    for i, item in enumerate(items):
        at = "%s.%s[%d]" % (where, field, i)
        if not isinstance(item, dict):
            out.append(_issue("bad_type", at, "근거 항목은 객체여야 한다"))
            continue
        name = item.get("file_name")
        if not isinstance(name, str) or not name.strip():
            out.append(_issue("missing_field", at, "file_name이 비어 있다"))
        page = item.get("page")
        if page is None:
            pass
        elif isinstance(page, bool) or not isinstance(page, int):
            out.append(_issue("bad_type", at, "page는 1-기준 정수 또는 null"))
        elif page < 1:
            out.append(_issue("bad_value", at, "page는 1 이상"))
        quote = item.get("quote")
        if quote is not None and not isinstance(quote, str):
            out.append(_issue("bad_type", at, "quote는 문자열 또는 null"))
    return out


def validate_document(doc, expected_qids=None):
    issues = []
    version = doc.get("format_version")
    if not isinstance(version, str) or not version:
        issues.append(_issue("missing_field", "format_version", "없다"))
    elif version.split(".")[0] != SUPPORTED_MAJOR:
        issues.append(_issue("unsupported_version", "format_version", version))
    meta = doc.get("meta")
    if not isinstance(meta, dict):
        issues.append(_issue("missing_field", "meta", "meta가 없다"))
        meta = {}
    for key in META_REQUIRED:
        v = meta.get(key)
        if not isinstance(v, str) or not v.strip():
            issues.append(_issue("missing_field", "meta." + key, "비어 있다"))
    if isinstance(meta.get("stage"), str) and meta["stage"] not in STAGES:
        issues.append(_issue("bad_value", "meta.stage", "initial|followup"))
    answers = doc.get("answers")
    if not isinstance(answers, list):
        issues.append(_issue("missing_field", "answers", "배열이 없다"))
        return issues
    seen = {}
    for i, a in enumerate(answers):
        where = "answers[%d]" % i
        if not isinstance(a, dict):
            issues.append(_issue("bad_type", where, "객체여야 한다"))
            continue
        qid = a.get("qid")
        if not isinstance(qid, str) or not qid.strip():
            issues.append(_issue("missing_field", where + ".qid", "비어 있다"))
        else:
            seen[qid] = seen.get(qid, 0) + 1
            where = "answers[%d](%s)" % (i, qid)
        if a.get("decision") not in DECISIONS:
            issues.append(_issue("bad_value", where + ".decision", repr(a.get("decision"))))
        if not isinstance(a.get("value"), (int, float, bool, str, type(None))):
            issues.append(_issue("bad_type", where + ".value", "자료형 위반"))
        if not isinstance(a.get("unit", ""), str):
            issues.append(_issue("bad_type", where + ".unit", "문자열이어야 한다"))
        b = a.get("boundary")
        if b is not None:
            if not isinstance(b, dict):
                issues.append(_issue("bad_type", where + ".boundary", "객체여야 한다"))
            else:
                for k, v in b.items():
                    if k not in BOUNDARY_FIELDS:
                        issues.append(_issue("unknown_field", where + ".boundary." + k, "허용 밖"))
                    elif v is not None and not isinstance(v, str):
                        issues.append(_issue("bad_type", where + ".boundary." + k, "문자열/null"))
                c = b.get("completeness")
                if c is not None and c not in COMPLETENESS:
                    issues.append(_issue("bad_value", where + ".boundary.completeness", str(c)))
        issues += _check_evidence(a.get("evidence"), where, "evidence")
        issues += _check_evidence(a.get("references"), where, "references")
        src = a.get("source")
        if src is not None and not isinstance(src, dict):
            issues.append(_issue("bad_type", where + ".source", "객체여야 한다"))
    for qid, n in sorted(seen.items()):
        if n > 1:
            issues.append(_issue("duplicate_qid", "answers(%s)" % qid, "%d번" % n))
    if expected_qids is not None:
        expected = set(expected_qids)
        for qid in sorted(set(seen) - expected):
            issues.append(_issue("unknown_qid", "answers(%s)" % qid, "양식에 없다"))
        for qid in sorted(expected - set(seen)):
            issues.append(_issue("missing_qid", "answers(%s)" % qid, "응답이 없다"))
        if len(answers) != len(expected):
            issues.append(_issue("row_count", "answers", "%d행" % len(answers)))
    return issues


def new_document(*, system, run_id, stage, framework, data_source, model=None,
                 produced_at=None, adapter=None, source_ref=None, notice=None,
                 answers=None):
    meta = {"system": system, "run_id": run_id, "stage": stage,
            "framework": framework, "data_source": data_source}
    for k, v in (("model", model), ("produced_at", produced_at), ("adapter", adapter),
                 ("source_ref", source_ref), ("notice", notice)):
        if v is not None:
            meta[k] = v
    return {"format_version": FORMAT_VERSION, "meta": meta, "answers": answers or []}


def new_answer(*, qid, decision, decision_basis="", value=None, unit="",
               value_text=None, boundary=None, evidence=None, references=None,
               source=None, notes=None):
    row = {"qid": qid, "decision": decision, "value": value, "unit": unit,
           "evidence": list(evidence or [])}
    if decision_basis:
        row["decision_basis"] = decision_basis
    if value_text is not None:
        row["value_text"] = value_text
    if boundary is not None:
        row["boundary"] = boundary
    if references:
        row["references"] = list(references)
    if source is not None:
        row["source"] = source
    if notes is not None:
        row["notes"] = notes
    return row
'''


@pytest.fixture()
def af(tmp_path):
    path = tmp_path / "answer_format.py"
    path.write_text(_AF_SOURCE, encoding="utf-8")
    return bg.load_answer_format(str(path))


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
    decisions = {a["qid"]: a["decision"] for a in doc["answers"]}
    assert decisions == {"Q-1": "confirmed", "Q-2": "unverified_submitted", "Q-3": "hold"}
    assert stats["confirmed"] == 1 and stats["unverified_submitted"] == 1 and stats["hold"] == 1
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
    assert doc["answers"][0]["decision"] == "not_applicable"
    assert stats["not_applicable"] == 1
    assert doc["meta"]["system"] == "baseline_mock-model-1"
    assert doc["meta"]["model"] == "mock-model-1"
    assert doc["meta"]["stage"] == "initial"
    assert doc["meta"]["data_source"] == "촬영증빙_17건"
    assert doc["meta"]["notice"] == bg.COMPANY_NOTICE
    assert doc["meta"]["source_ref"]["sha256"] == "0" * 64
    assert doc["format_version"] == af.FORMAT_VERSION


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
    assert doc["answers"][0]["decision"] == "confirmed"
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
    assert doc["answers"][0]["decision"] == "confirmed"      # 출력을 고치지 않는다
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


def test_broken_json_yields_all_rows_unparsed(af):
    obj, err = bg.parse_model_output("응답을 만들 수 없습니다")
    doc, stats = _convert(af, obj, parse_error=err)
    assert obj is None
    assert stats["unparsed"] == 3 and stats["rows"] == 3
    assert sorted(stats["missing_qid"]) == ["Q-1", "Q-2", "Q-3"]
    assert all(a["decision"] == "unparsed" for a in doc["answers"])
    assert af.validate_document(doc, EXPECTED) == []       # 48행 자리는 유지된다


# ── 4. 문항 누락·중복·허용값 밖 ───────────────────────────────────────────
def test_missing_qid_is_unparsed_not_hold(af):
    parsed = {"answers": [{"qid": "Q-1", "decision": "hold", "evidence": []}]}
    doc, stats = _convert(af, parsed)
    assert stats["missing_qid"] == ["Q-2", "Q-3"]
    rows = {a["qid"]: a["decision"] for a in doc["answers"]}
    assert rows == {"Q-1": "hold", "Q-2": "unparsed", "Q-3": "unparsed"}
    assert af.validate_document(doc, EXPECTED) == []


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
    assert doc["answers"][0]["decision"] == "unparsed"
    assert "허용값" in doc["answers"][0]["decision_basis"]
    assert stats["unparsed"] == 1
    assert af.validate_document(doc, EXPECTED) == []


# ── 5. 반복 간 판정 변경 수 ────────────────────────────────────────────────
def test_count_decision_flips():
    def mk(d1, d2, d3):
        return {"answers": [{"qid": "Q-1", "decision": d1},
                            {"qid": "Q-2", "decision": d2},
                            {"qid": "Q-3", "decision": d3}]}
    out = bg.count_decision_flips([mk("hold", "confirmed", "hold"),
                                   mk("confirmed", "confirmed", "hold"),
                                   mk("hold", "confirmed", "hold")])
    assert out["repeats"] == 3
    assert out["changed_rows"] == 1
    assert out["changed_qids"] == ["Q-1"]


def test_count_decision_flips_single_repeat():
    out = bg.count_decision_flips([{"answers": [{"qid": "Q-1", "decision": "hold"}]}])
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
        "--answer-format", str(af_path),
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
