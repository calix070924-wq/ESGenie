"""Layer 6 통합 보고서 조립 + PDF 내보내기 테스트.

증명 목표:
- assemble_report가 PipelineOutput의 분석 결과를 빠짐없이 블록으로 엮는다.
- 하이브리드 계약: LLM 블록은 exec_summary·benchmark 2개뿐이며, used_mock일 때
  llm.py의 ESG 템플릿이 새지 않고 모듈 자체 결정적 fallback으로 대체된다.
- ReportDoc → PDF가 한글 임베드로 안전하게 생성된다.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from esgenie import layer6_report
from esgenie.exporters.report_pdf import export_report_pdf
from esgenie.layer6_report import ReportBlock, ReportDoc, assemble_report
from esgenie.llm import LLMResponse
from esgenie.pipeline import run


@pytest.fixture(scope="module")
def output():
    # 삼성전자: report·industry 벤치마크가 존재 → benchmark 블록까지 포함된다.
    return run(corp_code="005930", areas=["E", "S", "G"], save_traces=False)


# ── 1. 핵심 블록이 모두 조립된다 ──
def test_blocks_present(output):
    doc = assemble_report(output)
    ids = {b.id for b in doc.blocks}
    for required in ("cover", "exec_summary", "esg_E", "esg_S", "esg_G",
                     "issb", "disclosure", "risk", "evidence"):
        assert required in ids, f"블록 누락: {required}"


# ── 2. 통합 마크다운이 풍부하다(재활용 본문 + 결정적 표 + 요약) ──
def test_markdown_is_rich(output):
    md = assemble_report(output).to_markdown()
    assert len(md) > 2000
    assert "Executive Summary" in md
    assert "## 환경 성과" in md          # E/S/G 본문 재활용
    assert "ISSB" in md
    assert "|" in md                      # 표가 포함됨


# ── 3. 하이브리드: LLM 블록은 정확히 exec_summary·benchmark 2개 ──
def test_llm_blocks_tagged(output):
    doc = assemble_report(output)
    llm_ids = {b.id for b in doc.blocks if b.kind == "llm"}
    assert llm_ids <= {"exec_summary", "benchmark"}
    assert "exec_summary" in llm_ids


# ── 4. used_mock이면 결정적 fallback으로 대체(ESG 템플릿 누수 차단) ──
def test_llm_fallback_on_mock(output, monkeypatch):
    monkeypatch.setattr(
        layer6_report.CLIENT, "complete",
        lambda system, user, **kw: LLMResponse(
            content="## 환경 성과\n### 핵심 지표\n잘못된 mock 템플릿", used_mock=True, meta={}),
    )
    block = layer6_report._block_exec_summary(output)
    # mock 템플릿 문자열이 보고서에 새면 안 된다.
    assert "### 핵심 지표" not in block.body_md
    assert "커버리지" in block.body_md  # 결정적 fallback 본문


# ── 5. 실제 LLM 응답(used_mock=False)은 그대로 사용된다 ──
def test_llm_content_used_when_real(output, monkeypatch):
    monkeypatch.setattr(
        layer6_report.CLIENT, "complete",
        lambda system, user, **kw: LLMResponse(
            content="REAL_NARRATIVE_XYZ", used_mock=False, meta={}),
    )
    block = layer6_report._block_exec_summary(output)
    assert block.body_md == "REAL_NARRATIVE_XYZ"


# ── 6. PDF 스모크: 유효한 %PDF + 비자명한 크기 ──
def test_pdf_smoke(output, tmp_path: Path):
    pytest.importorskip("reportlab")
    doc = assemble_report(output)
    path = export_report_pdf(doc, tmp_path)
    assert path.endswith(".pdf")
    with open(path, "rb") as f:
        assert f.read(5) == b"%PDF-"
    assert Path(path).stat().st_size > 5000


# ── 7. PDF 본문에 한글이 실제로 박힌다 ──
def test_pdf_contains_korean(output, tmp_path: Path):
    pytest.importorskip("reportlab")
    fitz = pytest.importorskip("fitz")
    doc = assemble_report(output)
    path = export_report_pdf(doc, tmp_path)
    pdf = fitz.open(path)
    text = "".join(pdf.load_page(i).get_text() for i in range(pdf.page_count))
    pdf.close()
    for kw in ("Executive Summary", "환경 성과", "ISSB"):
        assert kw in text, f"PDF 본문에 '{kw}' 없음"


# ── 8. 데이터가 없는 블록은 안전하게 생략된다(빈 output) ──
def test_assemble_handles_sparse_output():
    sparse = SimpleNamespace(
        report=None,
        extraction=None,
        sections={},
        disclosure=None,
        issb_gap=None,
        risk_rows=[],
        policy_drafts={},
        trace_paths={},
        evidence_graph=SimpleNamespace(nodes=[], text_nodes=[], edges=[]),
        industry_module_key=None,
        requested_areas=[],
    )
    doc = assemble_report(sparse)
    ids = {b.id for b in doc.blocks}
    # 최소한 표지·요약·증빙은 항상 생성, 데이터 없는 블록은 생략
    assert "cover" in ids and "exec_summary" in ids and "evidence" in ids
    assert "issb" not in ids and "risk" not in ids and "benchmark" not in ids
    assert isinstance(doc.to_markdown(), str) and doc.to_markdown().strip()
