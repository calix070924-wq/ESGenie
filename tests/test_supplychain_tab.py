"""실사 응답서 탭 렌더 스모크 — 가짜 Streamlit으로 예외 없이 도는지 검증.

샌드박스/CI에 streamlit·plotly가 없을 수 있으므로 sys.modules에 경량 스텁을
주입한 뒤 render_supplychain_tab을 실제 PipelineOutput 형태로 호출한다.
(실제 위젯 렌더가 아니라 '데이터 경로가 끊기지 않는가'를 본다.)
"""
from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from esgenie.layer3_disclosure import DisclosureReport, OrphanRatio
from esgenie.ssot.audit_trace import DataPoint, EvidenceLink
from esgenie.supplychain.claims import SupplierClaim, is_saq_upload


def _fake_streamlit() -> MagicMock:
    st = MagicMock(name="streamlit")
    st.columns.side_effect = lambda spec, *a, **k: [
        MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))
    ]
    st.selectbox.side_effect = lambda label, options, **k: options[0]
    return st


@pytest.fixture
def tabs_module(monkeypatch):
    monkeypatch.setitem(sys.modules, "streamlit", _fake_streamlit())
    # plotly는 tabs.py 임포트 시점에만 필요 → 빈 스텁
    plotly = types.ModuleType("plotly")
    go = types.ModuleType("plotly.graph_objects")
    plotly.graph_objects = go
    monkeypatch.setitem(sys.modules, "plotly", plotly)
    monkeypatch.setitem(sys.modules, "plotly.graph_objects", go)
    # 깨끗한 재임포트
    sys.modules.pop("esgenie.ui.tabs", None)
    import esgenie.ui.tabs as tabs
    return tabs


def _fake_result():
    energy = DataPoint(
        kesg_code="E-4-1", kesg_name="에너지 사용량", value=128400.0, unit=" kWh",
        period=2025, confidence=0.95, verification="verified", d1_risk=0.05,
        evidence_files=[EvidenceLink(
            file_name="한전고지서_2025_03.pdf", relative_path="evidence_pack/x.pdf",
            origin="ocr_structured", bbox=[0.08, 0.23, 0.3, 0.24], page=0,
            node_id="n1")],
    )
    waste = DataPoint(
        kesg_code="E-6-2", kesg_name="폐기물 재활용 비율", value=92.0, unit="%",
        period=2025, confidence=0.9, verification="verified", d1_risk=0.1,
        evidence_files=[],
    )
    disclosure = DisclosureReport(
        score=0.6, level="high",
        orphan_ratios=[OrphanRatio(
            ratio_code="E-6-2", ratio_name="폐기물 재활용 비율",
            missing_context=["E-6-1"], detail="분모(폐기물 총량) 누락")],
    )
    extraction = SimpleNamespace(
        corp_name="한국정밀",
        mapped={c: {"code": c, "name": c, "evidence_node_ids": []}
                for c in ["E-1-1", "E-1-2", "E-4-1", "E-6-2"]},
        missing=["E-6-1"],
    )
    v15 = SimpleNamespace(data_points=[energy, waste])
    return SimpleNamespace(
        report=SimpleNamespace(corp_name="한국정밀"),
        extraction=extraction, disclosure=disclosure, v15_trace=v15,
    )


def test_render_with_none_result(tabs_module):
    # result 없음 → info 안내만, 예외 없음
    tabs_module.render_supplychain_tab(None, "<div></div>")


def test_render_with_full_result(tabs_module, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)   # export가 outputs/ 에 쓰므로 격리
    tabs_module.render_supplychain_tab(_fake_result(), "<div></div>")
    # 응답서 xlsx가 생성됐는지
    produced = list(tmp_path.glob("outputs/_supplychain/**/*.xlsx"))
    assert produced, "응답서 xlsx가 생성되지 않음"


def test_is_saq_upload_uses_filename_or_text_hints(monkeypatch, tmp_path):
    saq = tmp_path / "partner_form.pdf"
    evidence = tmp_path / "waste_ledger.pdf"
    image = tmp_path / "OEM_ESG자가진단설문.png"
    saq.write_bytes(b"%PDF-1.4")
    evidence.write_bytes(b"%PDF-1.4")
    image.write_bytes(b"fake")

    monkeypatch.setattr(
        "esgenie.supplychain.claims._extract_text",
        lambda path: (
            "Drive Sustainability supplier questionnaire"
            if path == str(saq) else
            "폐기물 재활용 비율 29.3%"
        ),
    )

    assert is_saq_upload(str(saq), file_name="partner_form.pdf")
    assert is_saq_upload(str(saq), file_name="OEM_ESG자가진단설문.pdf")
    assert not is_saq_upload(str(evidence), file_name="03_사업장폐기물_위탁처리명세.pdf")
    assert not is_saq_upload(str(image), file_name="OEM_ESG자가진단설문.png")


def test_render_with_supplier_claims_flags_d1_mismatch(tabs_module, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _fake_result()
    result.disclosure = None
    result.v15_trace = SimpleNamespace(data_points=[
        DataPoint(
            kesg_code="E-6-2", kesg_name="폐기물 재활용 비율", value=29.3, unit="%",
            period=2025, confidence=0.9, verification="verified", d1_risk=0.0,
            evidence_files=[],
        )
    ])
    result.supplier_claims = {
        "E-6-2": SupplierClaim(
            code="E-6-2", value=92.0, unit="%",
            raw="재활용률 92%", source="saq:OEM_ESG자가진단설문.pdf",
        )
    }
    result.supplier_claim_files = ["OEM_ESG자가진단설문.pdf"]

    tabs_module.render_supplychain_tab(result, "<div></div>")

    error_texts = [str(call.args[0]) for call in tabs_module.st.error.call_args_list]
    caption_texts = [str(call.args[0]) for call in tabs_module.st.caption.call_args_list]

    assert any("92.0%" in text and "29.3%" in text for text in error_texts)
    assert any("협력사 자가주장 1건 연동됨" in text for text in caption_texts)
