from types import SimpleNamespace

import pytest

from esgenie.pipeline import _apply_survey_answers, _collect_ocr_extractions
from esgenie.dart_client import CompanyReport
from esgenie.layer1_extract import evidence_coverage_pct
from esgenie.ssot.evidence_graph import build_unified_graph, TextNode
from esgenie.ssot.ssot_pipeline import extract_with_ssot
from esgenie.supplychain.responder import build_response_sheet


@pytest.mark.parametrize("yn", ["예", "아니오"])
def test_local_pipeline_does_not_reimport_survey_as_dart_evidence(monkeypatch, yn):
    from esgenie import pipeline, layer2_rag

    # 설치된 임베딩 모델 유무와 무관하게 운영용 검색 차단 계약을 검사한다.
    monkeypatch.setattr(layer2_rag, "RAG_GATE_FALLBACK_BYPASS", False)

    captured = []
    build_corp = pipeline.build_rag_with_ssot

    def capture_corp(*args, **kwargs):
        corp = build_corp(*args, **kwargs)
        captured.extend(corp.vector._docs)
        return corp

    monkeypatch.setattr(pipeline, "build_rag_with_ssot", capture_corp)
    result = pipeline.run(
        "SURVEY-ONLY", corp_name="설문 전용 검증", areas=["G"],
        use_dart=False, save_traces=False, export_outputs=False,
        survey_answers={"G-3-1": {"yn": yn, "text": "주주총회 소집 공고 자가응답"}},
    )
    assert result.extraction.mapped["G-3-1"]["survey_answer"]["yn"] == yn
    assert captured == [], "설문 자가응답이 독립 검색 근거로 다시 들어옴"
    assert result.report.raw_text_snippets == []
    assert result.sections["G"].final_score is None
    assert result.sections["G"].hitl_required


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


def test_clause_pages_are_resolved_against_actual_pdf(tmp_path):
    import fitz
    from esgenie.ssot.ocr_router import OcrExtraction, ExtractedClause, DocChannel, _resolve_clause_pages
    path=tmp_path/'policy.pdf'
    with fitz.open() as doc:
        doc.new_page().insert_text((50,50),'Policy actual text')
        doc.new_page().insert_text((50,50),'Safety actual text')
        doc.save(path)
    ext=OcrExtraction('policy.pdf',DocChannel.UNSTRUCTURED,'policy_manual',
        clauses=[ExtractedClause('policy','Safety actual text','S-4-1',page=2),
                 ExtractedClause('unknown','Absent quotation','E-1-1',page=2)])
    _resolve_clause_pages(ext,str(path))
    assert ext.clauses[0].page == 1
    assert ext.clauses[1].page is None
    single=tmp_path/'single.pdf'
    with fitz.open() as doc:
        doc.new_page().insert_text((50,50),'Single-page policy');doc.save(single)
    _resolve_clause_pages(ext,str(single))
    assert all(c.page==0 for c in ext.clauses)
