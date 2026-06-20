"""Pipeline end-to-end 테스트.

검증 기준 (v10 체크리스트):
- 3사(005930/005380/005490) 모두 audit_trace.json 생성까지 통과
- Mock LLM(키 없음)에서도 6-Layer 전체 통과
- PipelineOutput 스키마 검증
- audit_trace 구조 (sentences / summary / hitl_status)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from esgenie.config import DATA_DIR
from esgenie.industry import IndustryModule, register
from esgenie.pipeline import PipelineOutput, run

CORP_CODES = ["005930", "005380", "005490"]


# ---- 3사 e2e ----------------------------------------------------------------

@pytest.mark.parametrize("corp_code", CORP_CODES)
def test_pipeline_runs_all_layers(corp_code: str, tmp_path: pytest.TempPathFactory) -> None:
    """L0 ~ L5 전체가 에러 없이 완료되어야 한다."""
    output = run(corp_code, areas=["E"], save_traces=False)
    assert isinstance(output, PipelineOutput)


@pytest.mark.parametrize("corp_code", CORP_CODES)
def test_pipeline_output_schema(corp_code: str) -> None:
    output = run(corp_code, areas=["E"], save_traces=False)

    # L0
    assert len(output.evidence_graph.nodes) > 0

    # L1
    assert output.extraction.coverage_pct > 0
    assert len(output.extraction.mapped) > 0
    assert output.issb_gap is not None
    assert len(output.issb_gap.rows) > 0
    # evidence_node_ids 필드 존재 확인
    for entry in output.extraction.mapped.values():
        assert "evidence_node_ids" in entry

    # L4
    assert "E" in output.sections
    verify = output.sections["E"]
    assert len(verify.steps) >= 1
    assert 0.0 <= verify.final_score <= 100.0

    # L5
    assert "E" in output.audit_traces
    trace = output.audit_traces["E"]
    assert trace.ticker == corp_code
    assert len(trace.sentences) > 0


@pytest.mark.parametrize("corp_code", CORP_CODES)
def test_audit_trace_saved_to_disk(corp_code: str, tmp_path) -> None:
    """save_traces=True 시 outputs/에 파일이 생성돼야 한다."""
    output = run(corp_code, areas=["E"], save_traces=True)
    assert "E" in output.trace_paths
    path = Path(output.trace_paths["E"])
    assert path.exists(), f"audit_trace 파일 없음: {path}"
    with open(path, encoding="utf-8") as fp:
        data = json.load(fp)
    assert data["ticker"] == corp_code
    assert "sentences" in data
    assert "summary" in data


# ---- audit_trace 스키마 상세 -----------------------------------------------

def test_audit_trace_sentence_schema() -> None:
    output = run("005930", areas=["E"], save_traces=False)
    trace = output.audit_traces["E"]
    for s in trace.sentences:
        d = s.to_dict()
        for key in ("sentence_id", "sentence_text", "evidence_node_ids",
                    "retrieved_chunk_ids", "hitl_status", "timestamps", "model_versions"):
            assert key in d, f"AuditSentence 필드 누락: {key}"
        assert d["hitl_status"] in ("ok", "HITL_REQUIRED")


def test_audit_trace_summary_schema() -> None:
    output = run("005930", areas=["E"], save_traces=False)
    trace = output.audit_traces["E"]
    summary = trace.summary
    for key in ("total_sentences", "hitl_count", "avg_risk_score", "converged"):
        assert key in summary, f"summary 필드 누락: {key}"
    assert summary["total_sentences"] == len(trace.sentences)


# ---- Mock LLM 동작 확인 ----------------------------------------------------

def test_pipeline_works_without_api_key() -> None:
    """OPENAI_API_KEY 없는 Mock LLM 환경에서도 전체 파이프라인 통과."""
    from esgenie.config import SETTINGS
    assert SETTINGS.use_mock_llm, "테스트는 Mock LLM 환경에서 실행되어야 합니다"

    output = run("005930", areas=["E"], save_traces=False)
    assert output.sections["E"].final_text
    assert len(output.audit_traces["E"].sentences) > 0


# ---- 3사 전체 영역 smoke test -----------------------------------------------

@pytest.mark.parametrize("corp_code", CORP_CODES)
def test_all_areas_three_companies(corp_code: str) -> None:
    """E/S/G 3개 영역 모두 audit_trace 생성."""
    output = run(corp_code, areas=["E", "S", "G"], save_traces=False)
    for area in ("E", "S", "G"):
        assert area in output.audit_traces
        assert len(output.audit_traces[area].sentences) > 0


def test_pipeline_merges_uploaded_evidence_into_ssot() -> None:
    evidence = {
        "kepco_bill_2025_12.pdf": str(DATA_DIR / "test_docs" / "kepco_bill_2025_12.pdf"),
    }
    output = run(
        "SME001",
        areas=["E"],
        save_traces=False,
        evidence_files=evidence,
        export_outputs=False,
    )
    assert any(n.origin == "ocr_structured" for n in output.evidence_graph.nodes.values())
    assert output.v15_trace is not None
    assert output.ocr_extractions


def test_pipeline_supports_local_ssot_without_dart() -> None:
    evidence = {
        "waste_ledger_2025.pdf": str(DATA_DIR / "test_docs" / "waste_ledger_2025.pdf"),
        "safety_policy_2025.pdf": str(DATA_DIR / "test_docs" / "safety_policy_2025.pdf"),
    }
    output = run(
        "",
        areas=["E"],
        corp_name="로컬기업",
        industry="금속가공",
        report_year=2025,
        use_dart=False,
        save_traces=False,
        evidence_files=evidence,
        export_outputs=False,
    )
    assert output.report is None
    assert output.sections == {}
    assert output.extraction is not None
    assert output.v15_trace is not None
    assert len(output.evidence_graph.nodes) > 0
    assert len(output.evidence_graph.text_nodes) > 0
    assert output.extraction.coverage_pct > 0
    assert any(
        any(str(nid).startswith(f"{output.evidence_graph.corp_code}_TXT_") for nid in entry.get("evidence_node_ids", []))
        for entry in output.extraction.mapped.values()
    )


def test_pipeline_passes_resolved_industry_module_to_verify(monkeypatch) -> None:
    key = "test_pipeline_industry"
    register(IndustryModule(key=key, lexicon_extra={"env": ("친환경 성과",)}))

    seen: dict[str, str | None] = {}

    def fake_verify_and_refine(report, area, rag, **kwargs):
        module = kwargs.get("industry_module")
        seen[area] = module.key if module is not None else None
        from types import SimpleNamespace
        return SimpleNamespace(final_score=0.0, converged=True, hitl_required=False)

    monkeypatch.setattr("esgenie.pipeline.verify_and_refine", fake_verify_and_refine)
    monkeypatch.setattr("esgenie.pipeline.build_audit_trace", lambda **kwargs: None)

    output = run(
        "005930",
        areas=["E"],
        save_traces=False,
        active_industry=key,
    )

    assert output.industry_module_key == key
    assert seen["E"] == key
