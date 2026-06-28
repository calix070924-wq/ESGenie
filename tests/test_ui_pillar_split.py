"""pillar 분리 검증 — 데이터 레이어 단언 + 렌더 스모크.

test_supplychain_tab.py 의 헬퍼/픽스처(tabs_module, _fake_streamlit, _fake_result)
패턴을 그대로 따른다.
"""
from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from esgenie.supplychain.frameworks import keys_by_pillar
from esgenie.supplychain.frameworks.rba_self import RBA42
from esgenie.supplychain.frameworks.hmc import HMC
from esgenie.supplychain.frameworks.kesg_self import KESG28, KESG61
from esgenie.supplychain.frameworks.saq5 import SAQ5, SAQ5_ENV
from esgenie.ssot.audit_trace import DataPoint, EvidenceLink


# ── 데이터 레이어 ────────────────────────────────────────────────────

def test_pillar_values():
    assert RBA42.pillar == "due_diligence"
    assert HMC.pillar == "due_diligence"
    for fw in (KESG28, KESG61, SAQ5, SAQ5_ENV):
        assert fw.pillar == "disclosure"


def test_keys_by_pillar_order():
    assert keys_by_pillar("due_diligence") == ["rba42", "hmc"]
    dis = keys_by_pillar("disclosure")
    assert "rba42" not in dis and "hmc" not in dis


# ── 렌더 스모크 ──────────────────────────────────────────────────────

def _fake_streamlit() -> MagicMock:
    st = MagicMock(name="streamlit")
    st.columns.side_effect = lambda spec, *a, **k: [
        MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))
    ]
    st.selectbox.side_effect = lambda label, options, **k: options[k.get("index", 0)]
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


def _fake_result():
    energy = DataPoint(
        kesg_code="E-4-1", kesg_name="에너지 사용량", value=128400.0, unit=" kWh",
        period=2025, confidence=0.95, verification="verified", d1_risk=0.05,
        evidence_files=[EvidenceLink(
            file_name="한전고지서.pdf", relative_path="evidence_pack/x.pdf",
            origin="ocr_structured", bbox=[0.08, 0.23, 0.3, 0.24], page=0,
            node_id="n1")],
    )
    extraction = SimpleNamespace(
        corp_name="테스트",
        mapped={"E-4-1": {"code": "E-4-1", "name": "에너지 사용량", "evidence_node_ids": []}},
        missing=[],
    )
    v15 = SimpleNamespace(data_points=[energy])
    return SimpleNamespace(
        report=SimpleNamespace(corp_name="테스트"),
        extraction=extraction,
        disclosure=None,
        v15_trace=v15,
    )


def test_render_deliverables_workspace_no_result(tabs_module):
    tabs_module.render_deliverables_workspace(None, "E", "")


def test_render_due_diligence_workspace_no_result(tabs_module):
    tabs_module.render_due_diligence_workspace(None, "E", "")


def test_render_due_diligence_workspace_with_result(tabs_module, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tabs_module.render_due_diligence_workspace(_fake_result(), "E", "")


def test_due_diligence_workspace_default_is_rba42(tabs_module, tmp_path, monkeypatch):
    """실사 워크스페이스 드롭다운의 첫 옵션이 rba42여야 한다."""
    monkeypatch.chdir(tmp_path)
    tabs_module.render_due_diligence_workspace(_fake_result(), "E", "")

    selectbox_calls = tabs_module.st.selectbox.call_args_list
    responder_call = next(
        (c for c in selectbox_calls if c.args and "양식" in str(c.args[0])),
        None,
    )
    assert responder_call is not None, "제출 양식 selectbox가 호출되지 않음"
    options = responder_call.args[1]
    assert options[0] == "rba42", f"기본 선택이 rba42가 아님: {options}"
