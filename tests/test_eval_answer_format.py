"""공통 답안 형식 v1과 ESGenie 어댑터 테스트 — 전부 합성 입력이다.

여기 쓰인 값은 검증용 합성 fixture이며 한울정밀 BM 개편 세트의 정답이 아니다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from esgenie.eval import answer_format as af
from esgenie.eval import esgenie_adapter as ad

SYNTHETIC_QIDS = ("SYN-1", "SYN-2")


def _doc(answers, **meta):
    base = {"system": "synthetic", "run_id": "SYN-RUN-1", "stage": "initial",
            "framework": "rba42", "data_source": "synthetic"}
    base.update(meta)
    return af.new_document(answers=answers, **base)


def _esgenie(**over):
    row = {"qid": "SYN-1", "status": "verified", "value": 7.5, "unit": "톤",
           "comparison": "compared"}
    row.update(over)
    return {"sheet": {"answers": [row]}}


def _convert(**over):
    doc = ad.convert_result(_esgenie(**over), stage="initial", run_id="SYN-RUN-1",
                            framework="rba42", data_source="synthetic")
    return doc["answers"][0]


# ── 형식 검증 ─────────────────────────────────────────────────────────────
def test_valid_document_has_no_issues():
    doc = _doc([af.new_answer(qid="SYN-1", decision=af.D_CONFIRMED, value=1, unit="건",
                              evidence=[{"file_name": "08.pdf", "page": 1, "quote": "표 3"}]),
                af.new_answer(qid="SYN-2", decision=af.D_HOLD, value=None)])
    assert af.validate_document(doc, SYNTHETIC_QIDS) == []


def test_missing_required_meta_is_reported():
    doc = _doc([])
    del doc["meta"]["data_source"]
    codes = {(i["code"], i["where"]) for i in af.validate_document(doc)}
    assert ("missing_field", "meta.data_source") in codes


def test_unsupported_format_version_is_reported():
    doc = _doc([])
    doc["format_version"] = "2.0"
    assert any(i["code"] == "unsupported_version" for i in af.validate_document(doc))


def test_bad_stage_is_reported():
    doc = _doc([], stage="initial")
    doc["meta"]["stage"] = "final"
    assert any(i["where"] == "meta.stage" for i in af.validate_document(doc))


def test_unknown_decision_is_reported():
    doc = _doc([af.new_answer(qid="SYN-1", decision="answered")])
    assert any(i["code"] == "bad_value" and ".decision" in i["where"]
               for i in af.validate_document(doc))


def test_duplicate_missing_and_unknown_qids_are_reported():
    doc = _doc([af.new_answer(qid="SYN-1", decision=af.D_HOLD),
                af.new_answer(qid="SYN-1", decision=af.D_HOLD),
                af.new_answer(qid="SYN-9", decision=af.D_HOLD)])
    codes = {i["code"] for i in af.validate_document(doc, SYNTHETIC_QIDS)}
    assert {"duplicate_qid", "unknown_qid", "missing_qid", "row_count"} <= codes


@pytest.mark.parametrize("page, code", [(0, "bad_value"), (-1, "bad_value"),
                                        ("3", "bad_type"), (True, "bad_type")])
def test_page_must_be_a_one_based_integer_or_null(page, code):
    doc = _doc([af.new_answer(qid="SYN-1", decision=af.D_CONFIRMED, value=1,
                              evidence=[{"file_name": "08.pdf", "page": page}])])
    assert any(i["code"] == code for i in af.validate_document(doc))


def test_missing_page_is_allowed_as_unknown():
    doc = _doc([af.new_answer(qid="SYN-1", decision=af.D_CONFIRMED, value=1,
                              evidence=[{"file_name": "08.pdf", "page": None,
                                         "quote": None}])])
    assert af.validate_document(doc) == []


def test_evidence_without_file_name_is_reported():
    doc = _doc([af.new_answer(qid="SYN-1", decision=af.D_CONFIRMED, value=1,
                              evidence=[{"file_name": "", "page": 2}])])
    assert any(i["code"] == "missing_field" and "file_name" in i["detail"]
               for i in af.validate_document(doc))


def test_boundary_allows_null_for_unknown_but_rejects_unknown_fields():
    good = _doc([af.new_answer(qid="SYN-1", decision=af.D_HOLD,
                               boundary={"period": None, "site": None, "target": None,
                                         "denominator": None, "label": None,
                                         "completeness": None})])
    assert af.validate_document(good) == []
    bad = _doc([af.new_answer(qid="SYN-1", decision=af.D_HOLD,
                              boundary={"scope": "2026"})])
    assert any(i["code"] == "unknown_field" for i in af.validate_document(bad))


def test_boundary_completeness_is_restricted():
    doc = _doc([af.new_answer(qid="SYN-1", decision=af.D_HOLD,
                              boundary={"completeness": "거의"})])
    assert any(i["where"].endswith("completeness") for i in af.validate_document(doc))


def test_unreadable_file_raises_instead_of_becoming_a_hold(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(af.FormatError, match="JSON 파싱 실패"):
        af.load_document(path)


def test_rba42_document_of_48_rows_validates(tmp_path):
    from esgenie.supplychain.frameworks import get_framework
    qids = tuple(q.qid for q in get_framework("rba42").questions)
    doc = _doc([af.new_answer(qid=q, decision=af.D_HOLD) for q in qids])
    assert af.validate_document(doc, qids) == []
    assert len(doc["answers"]) == 48
    # 왕복 저장·로드에서 내용이 변하지 않는다.
    path = af.dump_document(doc, tmp_path / "doc.json")
    assert af.load_document(path) == doc


EXAMPLE_DIR = Path(__file__).resolve().parents[1] / "data" / "eval" / "examples"


@pytest.mark.parametrize("name", ["common_v1_esgenie_excerpt.json",
                                  "common_v1_control_excerpt.json"])
def test_committed_examples_stay_valid(name):
    """B에게 보여 주는 예시가 형식과 어긋나지 않게 고정한다(발췌라 행 수는 보지 않는다)."""
    doc = af.load_document(EXAMPLE_DIR / name)
    assert af.validate_document(doc) == []
    assert "합성 예시" in doc["meta"]["notice"]


def test_example_documents_are_scored_by_the_same_reader():
    """예시 문서가 채점기 입력으로 그대로 읽힌다."""
    from esgenie.eval import response_scoring as rs
    for name in ("common_v1_esgenie_excerpt.json", "common_v1_control_excerpt.json"):
        answers = rs.answers_from_document(af.load_document(EXAMPLE_DIR / name))
        assert [a.qid for a in answers] == ["RBA-A-1", "RBA-A-2", "RBA-C-5-E-7-1"]
        assert all(a.stage == "initial" for a in answers)


def test_identity_keys_separate_runs():
    meta = {"system": "esgenie", "run_id": "RUN-A", "stage": "initial"}
    assert af.document_key(meta) == ("esgenie", "RUN-A", "initial")
    assert af.row_key(meta, "SYN-1") == ("esgenie", "RUN-A", "initial", "SYN-1")
    assert af.row_key({**meta, "run_id": "RUN-B"}, "SYN-1") != af.row_key(meta, "SYN-1")


# ── 어댑터: §6.1 판정 매핑 ───────────────────────────────────────────────
def test_verified_with_value_is_confirmed():
    assert ad.classify_decision("verified", "compared", 7.5)[0] == af.D_CONFIRMED


@pytest.mark.parametrize("empty", [None, "", [], {}])
def test_verified_without_value_is_hold_not_confirmed(empty):
    decision, basis = ad.classify_decision("verified", "", empty)
    assert decision == af.D_HOLD
    assert "값이 비어" in basis


@pytest.mark.parametrize("zero", [0, 0.0, False])
def test_zero_and_false_are_filled_values(zero):
    """값 0은 빈 값이 아니다 — verified + 0은 확정이다(§6.1)."""
    assert af.is_filled(zero) is True
    assert ad.classify_decision("verified", "", zero)[0] == af.D_CONFIRMED


@pytest.mark.parametrize("comparison", sorted(ad.BLOCKING_COMPARISONS))
def test_blocking_comparison_forces_hold(comparison):
    for status in ("verified", "self_reported"):
        decision, basis = ad.classify_decision(status, comparison, 63.0)
        assert decision == af.D_HOLD, (status, comparison)
        assert comparison in basis


def test_self_reported_is_never_confirmed():
    assert ad.classify_decision("self_reported", "compared", 63.0)[0] == af.D_UNVERIFIED


@pytest.mark.parametrize("status", ["insufficient", "hitl_required", "draft_ready", "flagged"])
def test_other_statuses_are_hold(status):
    assert ad.classify_decision(status, "", 1.0)[0] == af.D_HOLD


def test_not_applicable_is_its_own_decision():
    assert ad.classify_decision("not_applicable", "", None)[0] == af.D_NOT_APPLICABLE


def test_not_applicable_with_blocking_comparison_is_undetermined():
    """계약에 우선순위가 없는 조합은 임의로 한쪽에 넣지 않는다."""
    decision, basis = ad.classify_decision("not_applicable", "mismatch", None)
    assert decision == af.D_UNDETERMINED
    assert "우선순위" in basis


# ── 어댑터: 페이지 변환은 한 번만 ────────────────────────────────────────
def test_page_conversion_is_zero_to_one_based():
    assert ad.to_common_page(0) == 1
    assert ad.to_common_page(2) == 3
    assert ad.to_common_page(None) is None, "페이지 누락을 첫 페이지로 간주하지 않는다"
    assert ad.to_common_page(True) is None, "bool을 페이지로 읽지 않는다"
    assert ad.to_common_page("세 번째") is None


def test_adapter_converts_page_exactly_once():
    row = _convert(evidence_links=[{"file_name": "08.pdf", "page": 2},
                                   {"file_name": "09.pdf"}])
    assert [e["page"] for e in row["evidence"]] == [3, None]
    # 이미 변환된 값을 다시 통과시키면 어긋난다 — 변환 지점이 하나임을 고정한다.
    assert ad.to_common_page(row["evidence"][0]["page"]) == 4


def test_converted_pages_pass_format_validation():
    doc = ad.convert_result(
        _esgenie(evidence_links=[{"file_name": "08.pdf", "page": 0}]),
        stage="initial", run_id="SYN-RUN-1", framework="rba42", data_source="synthetic")
    assert af.validate_document(doc) == []
    assert doc["answers"][0]["evidence"][0]["page"] == 1


# ── 어댑터: 원본 보존·경계·누락 ──────────────────────────────────────────
def test_original_status_and_comparison_are_preserved():
    row = _convert(status="self_reported", comparison="mismatch",
                   comparison_reason="기간이 다르다")
    assert row["source"]["status"] == "self_reported"
    assert row["source"]["comparison"] == "mismatch"
    assert row["source"]["comparison_reason"] == "기간이 다르다"
    assert row["decision"] == af.D_HOLD


def test_boundary_fields_are_mapped_and_unknowns_stay_null():
    row = _convert(boundary={"period_text": "2026-05", "site": "", "measure": "사용전력량",
                             "denominator": ""},
                   completeness="partial", boundary_label="2026-05 · 월간 · 부분")
    assert row["boundary"] == {"period": "2026-05", "site": None, "target": "사용전력량",
                               "denominator": None, "label": "2026-05 · 월간 · 부분",
                               "completeness": "partial"}


def test_period_year_is_used_when_boundary_text_is_absent():
    assert _convert(period=2026)["boundary"]["period"] == "2026"


def test_reference_links_are_kept_apart_from_evidence():
    row = _convert(evidence_links=[{"file_name": "08.pdf", "page": 0}],
                   reference_links=[{"file_name": "13_보완.pdf", "page": 1}])
    assert [e["file_name"] for e in row["evidence"]] == ["08.pdf"]
    assert [e["file_name"] for e in row["references"]] == ["13_보완.pdf"]
    assert row["references"][0]["page"] == 2


def test_unreadable_answer_row_becomes_unparsed_not_hold():
    doc = ad.convert_result({"sheet": {"answers": ["쓰레기", {"status": "verified"}]}},
                            stage="initial", run_id="SYN-RUN-1", framework="rba42",
                            data_source="synthetic")
    assert [a["decision"] for a in doc["answers"]] == [af.D_UNPARSED, af.D_UNPARSED]
    assert "qid가 없다" in doc["answers"][1]["decision_basis"]


def test_missing_answers_are_not_invented():
    doc = ad.convert_result({"sheet": {"answers": []}}, stage="initial", run_id="R",
                            framework="rba42", data_source="synthetic")
    assert doc["answers"] == []
    # 누락은 검증에서 드러난다 — 보류 행으로 채우지 않는다.
    assert {i["code"] for i in af.validate_document(doc, SYNTHETIC_QIDS)} == {
        "missing_qid", "row_count"}


def test_list_value_is_kept_in_source_not_in_value():
    row = _convert(value=["가", "나"])
    assert row["value"] is None
    assert row["source"]["value_list"] == ["가", "나"]


@pytest.mark.parametrize("broken, needle", [
    ({}, "sheet"),
    ({"sheet": {}}, "answers"),
])
def test_malformed_result_is_rejected(broken, needle):
    with pytest.raises(af.FormatError, match=needle):
        ad.convert_result(broken, stage="initial", run_id="R", framework="rba42",
                          data_source="synthetic")


def test_bad_stage_is_rejected():
    with pytest.raises(ValueError, match="stage"):
        ad.convert_result(_esgenie(), stage="final", run_id="R", framework="rba42",
                          data_source="synthetic")


def test_convert_result_file_records_source_path_and_hash(tmp_path):
    path = tmp_path / "result.json"
    path.write_text(json.dumps(_esgenie()), encoding="utf-8")
    doc = ad.convert_result_file(path, stage="initial", run_id="SYN-RUN-1",
                                 framework="rba42", data_source="synthetic")
    assert doc["meta"]["source_ref"]["path"] == str(path)
    assert len(doc["meta"]["source_ref"]["sha256"]) == 64
    assert doc["meta"]["adapter"] == {"name": ad.ADAPTER_NAME, "version": ad.ADAPTER_VERSION}


def test_adapter_cli_writes_a_common_format_document(tmp_path, capsys):
    src = tmp_path / "result.json"
    src.write_text(json.dumps(_esgenie()), encoding="utf-8")
    out = tmp_path / "common.json"
    assert ad.main(["--result", str(src), "--stage", "initial", "--run-id", "SYN-RUN-1",
                    "--data-source", "synthetic", "--out", str(out)]) == 0
    assert "wrote" in capsys.readouterr().out
    doc = af.load_document(out)
    assert doc["meta"]["system"] == "esgenie"
    assert doc["answers"][0]["decision"] == af.D_CONFIRMED
