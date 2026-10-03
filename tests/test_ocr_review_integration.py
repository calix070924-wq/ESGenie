"""OCR 실패·출처가 실제 pipeline의 확인 목록과 산출물까지 보존되는지 검증한다.

LLM은 공통 테스트 설정의 mock이고 외부 네트워크는 conftest에서 차단한다.
출처 전달 입력은 합성 fixture이며 실제 OCR 품질이나 모델 성능을 주장하지 않는다.
"""
import json
from pathlib import Path

import pytest

from esgenie import pipeline
from esgenie.config import SETTINGS
from esgenie.layer6_report import assemble_report
from esgenie.llm import LLMUnavailableError
from esgenie.source_review import review_markdown
from esgenie.ssot import ocr_router as o


def _fixture_extraction(*, mock=False):
    metric_quote = "2024년 환경 법규 위반 건수 2건"
    clause_quote = "환경 법규 위반 이후 개선 조치를 시행하고 재발 방지 교육을 실시했다."
    return o.OcrExtraction(
        "evidence.pdf", o.DocChannel.UNSTRUCTURED, "esg_report",
        metrics=[o.ExtractedMetric("환경 법규 위반 건수", 2, "건", "2024", "E-8-1",
                                   page=0, confidence=.9, quote=metric_quote, page_source="chunk")],
        clauses=[o.ExtractedClause("환경 법규 위반 조치", clause_quote, "E-8-1", page=3,
                                   quote=clause_quote, page_source="quote")],
        raw_text=metric_quote + "\n" + clause_quote,
        router_meta={"extraction_status": "mock" if mock else "complete", "mock": mock},
    )


@pytest.fixture
def isolated_outputs(tmp_path, monkeypatch):
    # 실제 trace/JSON/Markdown 저장을 tmp로 격리한다. PDF 렌더러만 경계 대역으로 둔다.
    monkeypatch.setattr("esgenie.layer5_audit_trace.OUTPUT_DIR", tmp_path / "traces")
    monkeypatch.setattr("esgenie.exporters.report_pdf.export_report_pdf",
                        lambda doc, directory: str(directory / "pdf_renderer_not_run.pdf"))
    return tmp_path


@pytest.mark.parametrize("stage", ["route", "extract", "gate", "consistency", "tag"])
def test_collect_strict_preserves_each_stage_exception(monkeypatch, stage):
    error = LLMUnavailableError(f"{stage} failed")
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(SETTINGS, "strict_llm", True)
    monkeypatch.setattr(o, "route_document", lambda *a, **k: None)
    monkeypatch.setattr(o, "extract_document", lambda *a, **k: _fixture_extraction())
    monkeypatch.setattr(pipeline.ocr_table_gate, "apply_table_gate", lambda *a, **k: None)
    monkeypatch.setattr("esgenie.ssot.ocr_consistency.validate_consistency", lambda *a, **k: [])
    monkeypatch.setattr(o, "tag_rba_codes", lambda *a, **k: None)
    targets = {
        "route": "esgenie.ssot.ocr_router.route_document",
        "extract": "esgenie.ssot.ocr_router.extract_document",
        "gate": "esgenie.ssot.ocr_table_gate.apply_table_gate",
        "consistency": "esgenie.ssot.ocr_consistency.validate_consistency",
        "tag": "esgenie.ssot.ocr_router.tag_rba_codes",
    }
    monkeypatch.setattr(targets[stage], fail)
    with pytest.raises(LLMUnavailableError) as caught:
        pipeline._collect_ocr_extractions({"uploaded.pdf": "local.pdf"})
    assert caught.value is error


def test_collect_nonstrict_preserves_each_failed_filename_and_reason(monkeypatch):
    monkeypatch.setattr(SETTINGS, "strict_llm", False)
    monkeypatch.setattr(o, "route_document", lambda *a, **k: None)
    def fail(*args, **kwargs):
        raise OSError("입력 문서를 읽을 수 없음")
    monkeypatch.setattr(o, "extract_document", fail)
    result = pipeline._collect_ocr_extractions({"one.pdf": "a.pdf", "two.pdf": "b.pdf"})
    assert [ext.source_file for ext in result] == ["one.pdf", "two.pdf"]
    for ext in result:
        assert ext.metrics == ext.clauses == []
        assert ext.router_meta["extraction_status"] == "failed"
        assert ext.router_meta["chunk_failures"] == [
            {"page": None, "reason": "OSError", "detail": "입력 문서를 읽을 수 없음"}]


def test_all_failed_inputs_create_review_json_without_a_success_report(monkeypatch, isolated_outputs):
    monkeypatch.setattr(SETTINGS, "strict_llm", False)
    def fail(*args, **kwargs):
        raise RuntimeError("OCR upstream unavailable")
    monkeypatch.setattr(o, "route_document", fail)
    output = pipeline.run("FAIL-INPUT", corp_name="실패 검증", areas=["E"], use_dart=False,
                          evidence_files={"broken.pdf": "broken.pdf"}, save_traces=False,
                          export_outputs=False, export_report=True, export_root=isolated_outputs)
    assert output.report is None and output.sections == {} and output.audit_traces == {}
    assert not output.evidence_graph.nodes and not output.evidence_graph.text_nodes
    assert "report_md" not in output.export_paths and "report_pdf" not in output.export_paths
    assert output.report_export["status"] == "unavailable"
    assert any(f.category == "extraction" for f in output.review_findings)
    stored = json.loads(Path(output.export_paths["source_review_json"]).read_text())
    assert stored["ocr_status"][0]["metadata"]["extraction_status"] == "failed"
    assert "OCR upstream unavailable" in json.dumps(stored, ensure_ascii=False)


def test_empty_input_does_not_generate_a_normal_success_report(isolated_outputs):
    output = pipeline.run("EMPTY-INPUT", corp_name="빈 입력", areas=["E"], use_dart=False,
                          save_traces=False, export_outputs=False, export_report=True,
                          export_root=isolated_outputs)
    assert output.report is None and output.extraction is None
    assert output.sections == {} and output.audit_traces == {}
    assert "report_md" not in output.export_paths and "report_pdf" not in output.export_paths
    assert not any(f.category == "source_fact" for f in output.review_findings)


def test_metric_and_clause_sources_survive_pipeline_graph_index_and_review(monkeypatch):
    captured = []
    original = pipeline.build_rag_with_ssot
    def capture(*args, **kwargs):
        corp = original(*args, **kwargs)
        captured.extend(corp.vector._docs)
        return corp
    monkeypatch.setattr(pipeline, "build_rag_with_ssot", capture)
    ext = _fixture_extraction()
    output = pipeline.run("SOURCE-TEST", corp_name="출처 검증", areas=["E"], report_year=2024,
                          use_dart=False, ocr_extractions=[ext], save_traces=False,
                          export_outputs=False, max_iter=0, profile="full")
    metric = next(n for n in output.evidence_graph.nodes.values() if n.metric == "E-8-1")
    clause = next(iter(output.evidence_graph.text_nodes.values()))
    assert (metric.page, metric.quote, metric.page_source) == (0, ext.metrics[0].quote, "chunk")
    assert (clause.page, clause.quote, clause.page_source) == (3, ext.clauses[0].quote, "quote")
    for node in (metric, clause):
        docs = [d for d in captured if d.meta.get("node_id") == node.id]
        assert docs
        assert all((d.meta["page"], d.meta["quote"], d.meta["page_source"]) ==
                   (node.page, node.quote, node.page_source) for d in docs)
    fact = next(f for f in output.review_findings if f.category == "source_fact" and f.code == "E-8-1")
    ref = next(r for r in fact.evidence if r.node_id == metric.id)
    assert ref.page == 0 and ref.quote == ext.metrics[0].quote
    markdown = review_markdown([fact])
    assert "1쪽" in markdown and "0쪽" not in markdown
    assert ext.metrics[0].quote in markdown
    assert output.item_retrievals
    assert {r["area"] for r in output.item_retrievals} == {"E"}


def test_pipeline_saves_same_review_and_item_search_to_trace_json_and_report(isolated_outputs):
    ext = _fixture_extraction()
    output = pipeline.run("EXPORT-TEST", corp_name="내보내기 검증", areas=["E"], report_year=2024,
                          use_dart=False, ocr_extractions=[ext], save_traces=True,
                          export_outputs=False, export_report=True, export_root=isolated_outputs,
                          max_iter=0, profile="full")
    expected = [f.to_dict() for f in output.review_findings]
    assert expected and output.item_retrievals
    memory = output.audit_traces["E"].summary
    assert memory["source_review"] == expected
    assert memory["item_retrievals"] == output.item_retrievals
    trace = json.loads(Path(output.trace_paths["E"]).read_text())
    assert trace["summary"]["source_review"] == expected
    assert trace["summary"]["item_retrievals"] == output.item_retrievals
    review = json.loads(Path(output.export_paths["source_review_json"]).read_text())
    assert review["findings"] == expected
    assert review["item_retrievals"] == output.item_retrievals
    assert review["ocr_status"][0]["metadata"]["extraction_status"] == "complete"
    assert output.report_export == {"status": "complete"}
    assert trace["summary"]["report_export"] == review["report_export"] == output.report_export
    markdown = Path(output.export_paths["report_md"]).read_text()
    assert "확인 필요 사항" in markdown
    assert review_markdown(output.review_findings) in markdown
    assert "1쪽" in markdown and ext.metrics[0].quote in markdown


@pytest.mark.parametrize("stage", ["assemble", "markdown", "pdf"])
def test_report_export_failure_is_preserved_in_result_and_saved_review(monkeypatch, isolated_outputs, stage):
    error = OSError(f"{stage} output unavailable")
    def fail(*args, **kwargs):
        raise error
    if stage == "assemble":
        monkeypatch.setattr("esgenie.layer6_report.assemble_report", fail)
    elif stage == "markdown":
        write_text = Path.write_text
        def fail_markdown(path, *args, **kwargs):
            if path.suffix == ".md":
                raise error
            return write_text(path, *args, **kwargs)
        monkeypatch.setattr(Path, "write_text", fail_markdown)
    else:
        monkeypatch.setattr("esgenie.exporters.report_pdf.export_report_pdf", fail)

    output = pipeline.run("EXPORT-FAIL", corp_name="내보내기 실패", areas=["E"], report_year=2024,
                          use_dart=False, ocr_extractions=[_fixture_extraction()], save_traces=True,
                          export_outputs=False, export_report=True, export_root=isolated_outputs,
                          max_iter=0, profile="full")

    expected = {"status": "partial" if stage == "pdf" else "failed", "stage": stage,
                "reason": "OSError", "detail": str(error)}
    assert output.report_export == expected
    assert output.sections["E"].final_text and output.audit_traces["E"]
    assert any(f.category == "source_fact" for f in output.review_findings)
    assert "report_pdf" not in output.export_paths
    if stage == "pdf":
        assert "2024년 환경 법규 위반 건수 2건" in Path(output.export_paths["report_md"]).read_text()
    else:
        assert "report_md" not in output.export_paths
    stored = json.loads(Path(output.export_paths["source_review_json"]).read_text())
    trace = json.loads(Path(output.trace_paths["E"]).read_text())
    assert stored["report_export"] == trace["summary"]["report_export"] == expected


def test_strict_report_export_preserves_original_exception_and_failure_record(monkeypatch, isolated_outputs):
    error = RuntimeError("PDF renderer unavailable")
    def fail(*args, **kwargs):
        # OCR/서술 생성 mock은 허용하되, 내보내기 경계에서 strict 계약을 검증한다.
        monkeypatch.setattr(SETTINGS, "strict_llm", True)
        raise error
    monkeypatch.setattr("esgenie.exporters.report_pdf.export_report_pdf", fail)
    with pytest.raises(RuntimeError) as caught:
        pipeline.run("STRICT-EXPORT", corp_name="내보내기 검증", areas=["E"], report_year=2024,
                     use_dart=False, ocr_extractions=[_fixture_extraction()], save_traces=False,
                     export_outputs=False, export_report=True, export_root=isolated_outputs,
                     max_iter=0, profile="full")
    assert caught.value is error
    stored = json.loads((isolated_outputs / "STRICT-EXPORT_2024" / "source_review.json").read_text())
    assert stored["report_export"] == {"status": "partial", "stage": "pdf",
                                        "reason": "RuntimeError", "detail": str(error)}


@pytest.mark.parametrize("strict", [False, True])
def test_unwritable_report_directory_does_not_replace_original_export_failure(monkeypatch, isolated_outputs, strict):
    first_error = PermissionError("보고서 저장 경로에 쓰기 권한이 없음")
    second_error = PermissionError("검토 기록도 저장하지 못함")
    original_mkdir = Path.mkdir
    errors = iter([first_error, second_error])
    def fail_output_directory(path, *args, **kwargs):
        if path == isolated_outputs / "REPORT-BLOCKED_2024":
            monkeypatch.setattr(SETTINGS, "strict_llm", strict)
            raise next(errors)
        return original_mkdir(path, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", fail_output_directory)
    def run():
        return pipeline.run("REPORT-BLOCKED", corp_name="저장 경로 검증", areas=["E"], report_year=2024,
                            use_dart=False, ocr_extractions=[_fixture_extraction()], save_traces=False,
                            export_outputs=False, export_report=True, export_root=isolated_outputs,
                            max_iter=0, profile="full")
    if strict:
        with pytest.raises(PermissionError) as caught:
            run()
        assert caught.value is first_error
    else:
        output = run()
        assert output.report_export["status"] == "failed"
        assert output.report_export["stage"] == "markdown"
        assert output.report_export["detail"] == str(first_error)
        assert output.report_export["review_save_error"]["detail"] == str(second_error)
        assert "source_review_json" not in output.export_paths
        assert "report_md" not in output.export_paths and "report_pdf" not in output.export_paths


def test_mock_ocr_is_not_reported_as_a_confirmed_source_violation():
    output = pipeline.run("MOCK-SOURCE", corp_name="시연 자료 검증", areas=["E"], report_year=2024,
                          use_dart=False, ocr_extractions=[_fixture_extraction(mock=True)],
                          save_traces=False, export_outputs=False, max_iter=0, profile="full")
    assert any(f.category == "extraction" and "시연" in f.fact for f in output.review_findings)
    assert not any(f.category == "source_fact" for f in output.review_findings), \
        "mock OCR의 수치가 실제 공시된 위반 사실로 발표됨"
    markdown = assemble_report(output).to_markdown()
    assert "시연" in markdown


def test_existing_policy_mock_sample_is_explicit_in_review_and_report():
    ext = o._mock_unstructured("legacy_policy.pdf", "policy_manual")
    assert ext.router_meta["mock"] is True
    output = pipeline.run("LEGACY-MOCK", corp_name="기존 시연 자료", areas=["E"],
                          use_dart=False, ocr_extractions=[ext], save_traces=False,
                          export_outputs=False, max_iter=0)
    notices = [f for f in output.review_findings if f.category == "extraction"]
    assert any("시연" in finding.fact for finding in notices)
    assert not any(ref.quote for finding in notices for ref in finding.evidence)
    assert "시연" in assemble_report(output).to_markdown()


def test_unknown_page_stays_unknown_in_pipeline_review():
    ext = _fixture_extraction()
    for entry in ext.metrics + ext.clauses:
        entry.page = None
        entry.page_source = ""
    output = pipeline.run("UNKNOWN-PAGE", corp_name="출처 미확인", areas=["E"], report_year=2024,
                          use_dart=False, ocr_extractions=[ext], save_traces=False,
                          export_outputs=False, max_iter=0, profile="full")
    fact = next(f for f in output.review_findings if f.category == "source_fact")
    assert all(ref.page is None for ref in fact.evidence)
    markdown = review_markdown([fact])
    assert "페이지 미확인" in markdown
    assert "1쪽" not in markdown and "0쪽" not in markdown
