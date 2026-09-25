"""UI 크래시 가드(M1/M2/M3) 회귀 테스트.

tests/test_ui_pillar_split.py 의 tabs_module/_fake_streamlit 패턴을 그대로 따른다:
sys.modules에 가짜 streamlit을 주입한 뒤 esgenie.ui.tabs를 재임포트하고,
st.download_button/st.caption/st.info 등의 call_args_list로 렌더 호출을 검증한다.
"""
from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from esgenie.ssot.audit_trace import DataPoint, EvidenceLink


def _fake_streamlit() -> MagicMock:
    st = MagicMock(name="streamlit")
    st.columns.side_effect = lambda spec, *a, **k: [
        MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))
    ]
    st.selectbox.side_effect = lambda label, options, **k: options[0]
    st.session_state = {}
    return st


@pytest.fixture
def tabs_module(monkeypatch):
    monkeypatch.setitem(sys.modules, "streamlit", _fake_streamlit())
    plotly = types.ModuleType("plotly")
    go = types.ModuleType("plotly.graph_objects")
    plotly.graph_objects = go
    monkeypatch.setitem(sys.modules, "plotly", plotly)
    monkeypatch.setitem(sys.modules, "plotly.graph_objects", go)
    sys.modules.pop("esgenie.ui.tabs", None)
    import esgenie.ui.tabs as tabs
    return tabs


def _raise_assemble(*a, **k):
    raise RuntimeError("assemble boom")


# ── M3: _download_if_exists ──────────────────────────────────────────

def test_download_if_exists_with_real_file_calls_download_button(tabs_module, tmp_path):
    p = tmp_path / "report.xlsx"
    p.write_bytes(b"data")

    tabs_module._download_if_exists("label", str(p), "application/octet-stream")

    assert tabs_module.st.download_button.called
    assert not tabs_module.st.caption.called


def test_download_if_exists_missing_path_falls_back_to_caption(tabs_module, tmp_path):
    missing = str(tmp_path / "does_not_exist.xlsx")

    tabs_module._download_if_exists("label", missing, "application/octet-stream")

    assert not tabs_module.st.download_button.called
    caption_texts = [str(c.args[0]) for c in tabs_module.st.caption.call_args_list]
    assert any("파일 없음" in t for t in caption_texts)


def test_download_if_exists_container_receives_download_button(tabs_module, tmp_path):
    p = tmp_path / "report.pdf"
    p.write_bytes(b"data")
    container = MagicMock()

    tabs_module._download_if_exists("label", str(p), "application/pdf", container=container)

    assert container.download_button.called
    assert not tabs_module.st.download_button.called


def test_download_if_exists_container_receives_caption(tabs_module, tmp_path):
    missing = str(tmp_path / "does_not_exist.pdf")
    container = MagicMock()

    tabs_module._download_if_exists("label", missing, "application/pdf", container=container)

    assert container.caption.called
    assert not tabs_module.st.caption.called


def test_download_if_exists_race_between_check_and_open(tabs_module, tmp_path, monkeypatch):
    """exists-then-deleted 레이스: 파일은 실재하지만 open() 시점에 사라진 상황을 재현한다.

    os.path.exists를 건드리지 않고 open 자체가 FileNotFoundError를 던지도록 만들어,
    선체크가 없어도(또는 선체크가 통과한 뒤에도) open 실패가 캡션 폴백으로 흡수되는지 검증한다.
    """
    p = tmp_path / "report.xlsx"
    p.write_bytes(b"data")

    def _raising_open(*a, **k):
        raise FileNotFoundError("race: file removed between check and open")

    monkeypatch.setattr(tabs_module, "open", _raising_open, raising=False)

    tabs_module._download_if_exists("label", str(p), "application/octet-stream")

    assert not tabs_module.st.download_button.called
    caption_texts = [str(c.args[0]) for c in tabs_module.st.caption.call_args_list]
    assert any("파일 없음" in t for t in caption_texts)


# ── M2: _get_assembled_report + 호출부 ────────────────────────────────

def test_get_assembled_report_returns_none_tuple_on_assemble_failure(tabs_module, monkeypatch):
    monkeypatch.setattr("esgenie.layer6_report.assemble_report", _raise_assemble)
    result = SimpleNamespace(
        sections={"E": object()},
        report=SimpleNamespace(corp_name="테스트"),
        risk_rows=[],
    )

    doc, pdf_path = tabs_module._get_assembled_report(result)

    assert doc is None
    assert pdf_path is None
    assert result.report_export["preview"] == {"status": "failed", "stage": "assemble",
                                               "reason": "RuntimeError", "detail": "assemble boom"}


def test_get_assembled_report_logs_exception_on_assemble_failure(tabs_module, monkeypatch):
    monkeypatch.setattr("esgenie.layer6_report.assemble_report", _raise_assemble)
    fake_logger = MagicMock()
    monkeypatch.setattr(tabs_module, "logger", fake_logger)
    result = SimpleNamespace(
        sections={"E": object()},
        report=SimpleNamespace(corp_name="테스트"),
        risk_rows=[],
    )

    doc, pdf_path = tabs_module._get_assembled_report(result)

    assert doc is None and pdf_path is None
    fake_logger.exception.assert_called_once()


def test_render_deliverables_workspace_handles_assemble_failure(tabs_module, monkeypatch):
    monkeypatch.setattr("esgenie.layer6_report.assemble_report", _raise_assemble)
    empty_state = MagicMock()
    download_tiles = MagicMock()
    monkeypatch.setattr(tabs_module, "render_empty_state", empty_state)
    monkeypatch.setattr(tabs_module, "render_download_tiles", download_tiles)

    result = SimpleNamespace(
        sections={"E": object()},
        report=SimpleNamespace(corp_name="테스트"),
        risk_rows=[],
        export_paths={},
    )

    tabs_module.render_deliverables_workspace(result, "E", "")

    assert empty_state.call_args_list[-1].args == (
        "보고서 생성 실패", "보고서 내용을 구성하지 못했습니다. 사유: assemble boom")
    download_tiles.assert_not_called()


def test_render_submission_workspace_handles_assemble_failure(tabs_module, monkeypatch):
    monkeypatch.setattr("esgenie.layer6_report.assemble_report", _raise_assemble)
    empty_state = MagicMock()
    download_tiles = MagicMock()
    monkeypatch.setattr(tabs_module, "render_empty_state", empty_state)
    monkeypatch.setattr(tabs_module, "render_download_tiles", download_tiles)

    result = SimpleNamespace(
        sections={"E": object()},
        report=SimpleNamespace(corp_name="테스트"),
        risk_rows=[],
        export_paths={},
    )

    tabs_module.render_submission_workspace(result, "E")

    assert empty_state.call_args_list[-1].args == (
        "보고서 생성 실패", "보고서 내용을 구성하지 못했습니다. 사유: assemble boom")
    download_tiles.assert_not_called()


def test_pdf_failure_preserves_document_and_exposes_reason(tabs_module, monkeypatch):
    doc = SimpleNamespace(to_markdown=lambda: "검토할 보고서 본문")
    monkeypatch.setattr("esgenie.layer6_report.assemble_report", lambda result: doc)
    def fail(*args, **kwargs):
        raise OSError("PDF output directory unavailable")
    monkeypatch.setattr("esgenie.exporters.report_pdf.export_report_pdf", fail)
    result = SimpleNamespace(sections={"E": object()}, report=None, risk_rows=[])

    actual_doc, pdf_path = tabs_module._get_assembled_report(result)

    assert actual_doc is doc and pdf_path is None
    assert result.report_export["preview"] == {"status": "partial", "stage": "pdf", "reason": "OSError",
                                               "detail": "PDF output directory unavailable"}
    assert "문서(.md)는 내려받을 수 있습니다" in tabs_module._report_export_notice(result)
    assert "PDF output directory unavailable" in tabs_module._report_export_notice(result)


@pytest.mark.parametrize("stage", ["assemble", "pdf"])
def test_preview_strict_failure_preserves_original_exception(tabs_module, monkeypatch, stage):
    error = RuntimeError(f"{stage} strict failure")
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(tabs_module.SETTINGS, "strict_llm", True)
    monkeypatch.setattr("esgenie.layer6_report.assemble_report", lambda result: object())
    target = "esgenie.layer6_report.assemble_report" if stage == "assemble" else \
             "esgenie.exporters.report_pdf.export_report_pdf"
    monkeypatch.setattr(target, fail)
    result = SimpleNamespace(sections={"E": object()}, report=None, risk_rows=[])
    with pytest.raises(RuntimeError) as caught:
        tabs_module._get_assembled_report(result)
    assert caught.value is error
    assert result.report_export["preview"]["stage"] == stage


def test_report_preview_cache_is_scoped_to_actual_analysis_result(tabs_module, monkeypatch):
    assemble = MagicMock(side_effect=["first report", "second report"])
    monkeypatch.setattr("esgenie.layer6_report.assemble_report", assemble)
    monkeypatch.setattr("esgenie.exporters.report_pdf.export_report_pdf", lambda *args: "report.pdf")
    first = SimpleNamespace(sections={"E": object()}, report=SimpleNamespace(corp_name="같은 기업"), risk_rows=[])
    second = SimpleNamespace(sections={"E": object()}, report=SimpleNamespace(corp_name="같은 기업"), risk_rows=[])
    assert tabs_module._get_assembled_report(first)[0] == "first report"
    assert tabs_module._get_assembled_report(first)[0] == "first report"
    assert tabs_module._get_assembled_report(second)[0] == "second report"
    assert assemble.call_count == 2


def test_preview_success_preserves_previous_saved_export_failure(tabs_module, monkeypatch):
    monkeypatch.setattr("esgenie.layer6_report.assemble_report", lambda result: object())
    monkeypatch.setattr("esgenie.exporters.report_pdf.export_report_pdf", lambda *args: "preview.pdf")
    failure = {"status": "failed", "stage": "markdown", "reason": "PermissionError", "detail": "읽기 전용 경로"}
    result = SimpleNamespace(sections={"E": object()}, report=None, risk_rows=[], report_export=failure)

    tabs_module._get_assembled_report(result)

    assert result.report_export == {**failure, "preview": {"status": "complete"}}
    assert failure == {"status": "failed", "stage": "markdown", "reason": "PermissionError", "detail": "읽기 전용 경로"}


def test_ocr_upload_summary_counts_actual_status_and_separates_supplier_claims(tabs_module):
    def ext(name, status=None, **meta):
        return SimpleNamespace(source_file=name, router_meta={"extraction_status": status, **meta})
    result = SimpleNamespace(supplier_claim_files=["saq.xlsx"], ocr_extractions=[
        ext("done.pdf", "complete"), ext("partial.pdf", "partial"), ext("failed.pdf", "failed"),
        ext("demo.pdf", "complete", mock=True), ext("legacy.pdf", engine="upstage_dp")])
    names = ["done.pdf", "partial.pdf", "failed.pdf", "demo.pdf", "legacy.pdf", "missing.pdf", "saq.xlsx"]

    assert tabs_module.ocr_upload_statuses(result, names) == {
        "done.pdf": "complete", "partial.pdf": "partial", "failed.pdf": "failed",
        "demo.pdf": "mock", "legacy.pdf": "complete", "missing.pdf": "unknown"}
    summary = tabs_module._ocr_upload_summary(result, names)
    for text in ("읽기 완료 2건", "일부 처리 1건", "처리 실패 1건", "시연 결과 1건", "처리 상태 미확인 1건",
                 "자가진단 응답 파일 1건"):
        assert text in summary
    assert "결과에 반영" not in summary


def test_ocr_status_messages_include_failed_page_reason_without_false_failure_for_blank_page(tabs_module):
    result = SimpleNamespace(ocr_extractions=[
        SimpleNamespace(source_file="partial.pdf", router_meta={"extraction_status": "partial",
            "chunk_failures": [{"page": 0, "reason": "ValueError", "detail": "응답을 읽을 수 없음"}]}),
        SimpleNamespace(source_file="failed.pdf", router_meta={"extraction_status": "failed",
            "failure_reason": "문서가 손상됨"}),
        SimpleNamespace(source_file="blank.pdf", router_meta={"extraction_status": "complete",
            "empty_source_pages": [4]}),
        SimpleNamespace(source_file="demo.pdf", router_meta={"mock": True}),
    ])
    messages = tabs_module.ocr_upload_messages(result, ["partial.pdf", "failed.pdf", "blank.pdf", "demo.pdf"])
    assert any(level == "warning" and "partial.pdf" in text and "1쪽" in text and "응답을 읽을 수 없음" in text
               for level, text in messages)
    assert any(level == "error" and "failed.pdf" in text and "문서가 손상됨" in text for level, text in messages)
    assert any(level == "warning" and "blank.pdf" in text and "5쪽" in text and "빈 페이지인지" in text
               for level, text in messages)
    assert any("demo.pdf" in text and "시연용 결과" in text for _, text in messages)
    assert not any(level == "error" and "blank.pdf" in text for level, text in messages)


@pytest.mark.parametrize("meta", [{"fallback": "pymupdf+regex", "upstage": False},
                                  {"engine": "pymupdf"}, {"engine": "pymupdf_text"}])
def test_local_structured_parser_warning_is_file_specific_and_explains_missing_key(tabs_module, meta):
    result = SimpleNamespace(ocr_extractions=[
        SimpleNamespace(source_file="bill.pdf", channel="structured", raw_text="요금 10,000원", router_meta=meta),
        SimpleNamespace(source_file="report.pdf", channel="unstructured",
                        router_meta={"extraction_status": "complete", "raw_text_source": "pymupdf"}),
    ])
    messages = tabs_module.ocr_upload_messages(result, ["bill.pdf", "report.pdf"], upstage_key_present=False)
    assert len(messages) == 1
    level, text = messages[0]
    assert level == "warning" and "bill.pdf" in text
    assert "연결 키가 설정되지 않았습니다" in text and "원본 표" in text
    assert "report.pdf" not in text


def test_local_parser_api_failure_reason_is_not_replaced_by_missing_key_warning(tabs_module):
    result = SimpleNamespace(ocr_extractions=[
        SimpleNamespace(source_file="bill.pdf", channel="structured", raw_text="요금 10,000원",
                        router_meta={"fallback": "pymupdf+regex", "upstage": False, "upstage_error": "HTTP 429"})])
    messages = tabs_module.ocr_upload_messages(result, ["bill.pdf"], upstage_key_present=True)
    assert len(messages) == 1 and "429" in messages[0][1]
    assert "연결 키가 설정되지 않았습니다" not in messages[0][1]


@pytest.mark.parametrize("workspace", ["render_overview_workspace", "render_diagnosis_workspace"])
def test_workspace_upload_notice_does_not_claim_failed_or_mock_files_were_processed(tabs_module, monkeypatch, workspace):
    for helper in ("_render_esg_coverage_strip", "render_stat_row", "render_diag_tab", "render_policy_tab"):
        monkeypatch.setattr(tabs_module, helper, MagicMock())
    monkeypatch.setattr(tabs_module, "_result_status_meta", lambda *args: [])
    callout = MagicMock(return_value="callout")
    monkeypatch.setattr(tabs_module, "callout_html", callout)
    result = SimpleNamespace(sections={}, ocr_extractions=[
        SimpleNamespace(source_file="broken.pdf", router_meta={"extraction_status": "failed"}),
        SimpleNamespace(source_file="demo.pdf", router_meta={"mock": True})])

    getattr(tabs_module, workspace)(result, "E", uploaded_names=["broken.pdf", "demo.pdf"])

    actions = callout.call_args_list[0].args[1]
    assert any("처리 실패 1건" in action and "시연 결과 1건" in action for action in actions)
    assert not any("결과에 반영" in action for action in actions)


# ── M1: export_paths 직접 인덱싱 제거 ──────────────────────────────────

def _fake_audit_result(export_paths: dict) -> SimpleNamespace:
    data_point = DataPoint(
        kesg_code="E-4-1", kesg_name="에너지 사용량", value=100.0, unit="kWh",
        period=2025, confidence=0.9, verification="verified", d1_risk=0.1,
        evidence_files=[EvidenceLink(
            file_name="missing.pdf", relative_path="evidence_pack/missing.pdf",
            origin="ocr_structured", bbox=[0.1, 0.1, 0.2, 0.2], page=0,
            node_id="n1",
        )],
    )
    return SimpleNamespace(
        audit_traces={},
        export_paths=export_paths,
        v15_trace=SimpleNamespace(data_points=[data_point]),
    )


def test_render_audit_tab_handles_empty_export_paths_without_keyerror(tabs_module, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _fake_audit_result(export_paths={})

    tabs_module.render_audit_tab(result, "E", "", show_header=False)

    info_texts = [str(c.args[0]) for c in tabs_module.st.info.call_args_list]
    assert not any("증빙 서류철" in t for t in info_texts)


def test_render_audit_tab_shows_evidence_dir_when_present(tabs_module, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _fake_audit_result(export_paths={"evidence_dir": str(tmp_path)})

    tabs_module.render_audit_tab(result, "E", "", show_header=False)

    info_texts = [str(c.args[0]) for c in tabs_module.st.info.call_args_list]
    assert any("증빙 서류철" in t for t in info_texts)
