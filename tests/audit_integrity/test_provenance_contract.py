from types import SimpleNamespace

import pytest

from esgenie.pipeline import _apply_survey_answers, _collect_ocr_extractions
from esgenie.dart_client import CompanyReport
from esgenie.layer1_extract import evidence_coverage_pct
from esgenie.ssot.evidence_graph import build_unified_graph, TextNode
from esgenie.ssot.ssot_pipeline import extract_with_ssot
from esgenie.supplychain.responder import build_response_sheet


@pytest.mark.parametrize("yn,expected", [("예", True), ("아니오", False)])
@pytest.mark.parametrize("note", ["", "응답 메모"])
def test_survey_semantics_and_independent_coverage(yn, expected, note):
    survey = {"E-1-1": {"yn": yn, "text": note}}
    graph = build_unified_graph(None, _collect_ocr_extractions(None, survey_answers=survey),
                                corp_code="T", corp_name="T", report_year=2025)
    report = CompanyReport("T", "T", "", 2025, {}, {}, [], "synthetic")
    ledger = extract_with_ssot(report, graph, profile="sme")
    _apply_survey_answers(ledger, survey)
    ans = build_response_sheet("saq5_env", extraction=ledger, evidence_graph=graph).answers[0]
    assert ans.value is expected and ans.status == "self_reported"
    assert ledger.coverage_pct > 0 and evidence_coverage_pct(ledger) == 0
    assert ans.self_reports[0]["yn"] == yn
    assert all(n.origin == "survey" for n in graph.text_nodes.values())


def test_negative_survey_and_positive_document_preserve_both_sources():
    graph = build_unified_graph(None, [], corp_code="T", corp_name="T", report_year=2025)
    graph.add_text_node(TextNode("policy", "환경방침", "환경방침을 수립하고 점검한다.",
                                "E-1-1", "policy.pdf", 2))
    report = CompanyReport("T", "T", "", 2025, {}, {}, [], "synthetic")
    ledger = extract_with_ssot(report, graph, profile="sme")
    _apply_survey_answers(ledger, {"E-1-1": {"yn": "아니오", "text": "방침 없음"}})
    ans = build_response_sheet("saq5_env", extraction=ledger, evidence_graph=graph).answers[0]
    assert ans.value is False and ans.status == "flagged"
    assert ledger.mapped["E-1-1"]["value"] == "문서 조항 확인"
    assert ans.self_reports[0]["text"] == "방침 없음"
    assert ans.evidence_links[0].quote == "환경방침을 수립하고 점검한다."
    assert ans.evidence_links[0].page == 2


def test_mapped_code_without_answer_is_not_a_yes():
    ledger = SimpleNamespace(mapped={"E-1-1": {"evidence_node_ids": []}}, missing=[])
    ans = build_response_sheet("saq5_env", extraction=ledger).answers[0]
    assert ans.value is None and ans.status == "insufficient"


def test_legacy_survey_form_node_cannot_be_document_evidence():
    graph = build_unified_graph(None, [], corp_code="T", corp_name="T", report_year=2025)
    graph.add_text_node(TextNode("T_TXT_001", "E-1-1", "[설문] 아니오", "E-1-1",
                                "survey_form", 0, origin="ocr_unstructured"))
    report = CompanyReport("T", "T", "", 2025, {}, {}, [], "synthetic")
    ledger = extract_with_ssot(report, graph, profile="sme")
    ans = build_response_sheet("saq5_env", extraction=ledger, evidence_graph=graph).answers[0]
    assert ans.value is False and ans.status == "self_reported"
    assert evidence_coverage_pct(ledger) == 0
