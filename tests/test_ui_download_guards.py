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

    assert empty_state.call_args_list[-1].args == ("보고서 조립 실패", "로그를 확인해 주세요.")
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

    assert empty_state.call_args_list[-1].args == ("보고서 조립 실패", "로그를 확인해 주세요.")
    download_tiles.assert_not_called()


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
