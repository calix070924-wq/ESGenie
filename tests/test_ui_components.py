"""UI 컴포넌트 렌더 헬퍼 테스트."""
from __future__ import annotations

import sys

from esgenie.ui.components import render_pipeline_loading, render_report_card


class _FakeContainer:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeStreamlit:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def container(self, *, border: bool = False):
        self.calls.append(("container", border))
        return _FakeContainer()

    def markdown(self, body: str, unsafe_allow_html: bool = False):
        self.calls.append(("markdown", body, unsafe_allow_html))


def test_render_report_card_keeps_native_markdown_and_emits_style_marker(monkeypatch):
    fake_st = _FakeStreamlit()
    monkeypatch.setitem(sys.modules, "streamlit", fake_st)

    render_report_card("### FINAL BODY", kind="final", tag_label="FINAL")

    assert fake_st.calls[0] == ("container", True)
    assert fake_st.calls[1][0] == "markdown"
    assert "eg-report-card-marker final" in fake_st.calls[1][1]
    assert fake_st.calls[1][2] is True
    assert fake_st.calls[2][0] == "markdown"
    assert 'esg-report-tag final">FINAL<' in fake_st.calls[2][1]
    assert fake_st.calls[2][2] is True
    assert fake_st.calls[3] == ("markdown", "### FINAL BODY", False)


def test_render_pipeline_loading_uses_css_fallback_markup_only(monkeypatch):
    fake_st = _FakeStreamlit()
    monkeypatch.setitem(sys.modules, "streamlit", fake_st)

    render_pipeline_loading("Loading pipeline")

    assert fake_st.calls == [
        ("markdown", '<span class="eg-shimmer">Loading pipeline</span>', True)
    ]
