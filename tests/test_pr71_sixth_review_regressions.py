"""PR71 1296df7: 원문에서 확정한 전사/미기록 범위를 모델 라벨이 뒤집으면 안 된다."""
import socket
from types import SimpleNamespace as NS

import pytest

from esgenie.embeddings import IndexedDoc
from esgenie.layer2_rag import RAGContext, _source_facts_chunk, _render_source_facts_table
from esgenie.layer6_report import annotate_generated_text
from esgenie.ssot.boundary import Boundary
from esgenie.ssot.evidence_graph import EvidenceGraph, EvidenceNode
from esgenie.ssot.ocr_router import _map_vlm_json

HEADINGS = {"entity": "전사 교육 집계 / 2026-06-03", "unknown": "사업장 미기록 / 2026-06-03"}
CLAIM = "2026년 6월 3일 김해 제1공장 교육에는 27명이 참석했다 [source_facts_S]."


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("No network/provider call in review")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


def run_case(label, text, quote):
    metrics, _ = _map_vlm_json({"metrics": [{
        "metric_hint": label, "value": 27, "unit": "명", "period": "2026-06-03", "quote": quote,
        "boundary": {"period_text": "2026-06-03"},
    }]}, source_text=text, page_no=0)
    assert len(metrics) == 1
    m = metrics[0]
    graph = EvidenceGraph("TEST", "검토용")
    graph.add_node(EvidenceNode(id="n27", metric=m.metric_hint, value=m.value, unit=m.unit, period=2026,
                               source="ocr/test", origin="ocr_unstructured", source_file="교육집계.pdf",
                               page=m.page, quote=m.quote, boundary=Boundary.from_dict(m.boundary)))
    raw = IndexedDoc(text=text, meta={"source_file": "교육집계.pdf"}, chunk_id="c1")
    context = RAGContext(kesg_hits=[], industry_hits=[], corp_hits=[(raw, 1.0)])
    facts = _source_facts_chunk(graph, context, "S")
    assert facts is not None
    generation = NS(text=CLAIM, context=NS(all_hits=lambda: [(raw, 1.0), (facts, 1.0)]))
    body, marks = annotate_generated_text(NS(extraction=NS(mapped={})), "S",
                                         NS(final=NS(generation=generation), final_text=CLAIM))
    return {"boundary": m.boundary, "facts": facts.meta["facts"], "body": body, "marks": marks,
            "table": _render_source_facts_table(facts.meta["facts"]), "prompt_facts": facts.text}


def source(scope):
    return "교육 기록: 김해 제1공장 / 2026-06-03\n참석 15명\n" + HEADINGS[scope] + "\n참석 27명"


@pytest.mark.parametrize("scope", list(HEADINGS))
@pytest.mark.parametrize("label", ["김해 제1공장 교육 참석 인원", "제1공장 교육 참석 인원"])
@pytest.mark.parametrize("quote_extent", ["count", "whole_source"])
def test_a_model_label_cannot_restore_a_site_cleared_by_source_scope(scope, label, quote_extent):
    text = source(scope)
    result = run_case(label, text, "참석 27명" if quote_extent == "count" else text)
    assert result["boundary"]["site"] == "" and result["boundary"]["site_scope"] == scope
    assert any(r.get("action") == "replaced" for r in result["marks"]), result
    assert any(r["value"] == 27 for r in result["facts"]), "Keep the original count."


@pytest.mark.parametrize("scope", list(HEADINGS))
def test_a_neutral_label_preserves_the_scope_and_original_count(scope):
    result = run_case("교육 참석 인원", source(scope), "참석 27명")
    assert any(r.get("action") == "replaced" for r in result["marks"]), result
    assert result["facts"][0]["value"] == 27
    assert ("전체 사업장" if scope == "entity" else "원문 미기록") in result["table"]


@pytest.mark.parametrize("label", ["김해 제1공장 교육 참석 인원", "제1공장 교육 참석 인원"])
def test_a_label_with_a_genuinely_supported_site_is_preserved(label):
    text = "교육 기록: 김해 제1공장 / 2026-06-03\n참석 27명"
    result = run_case(label, text, "참석 27명")
    assert not any(r.get("action") == "replaced" for r in result["marks"]), result
    assert "김해 제1공장 교육에는 27명이 참석했다" in result["body"]
    assert "김해 제1공장" in result["table"]


@pytest.mark.parametrize("scope", list(HEADINGS))
@pytest.mark.parametrize("label", ["김해 제1공장 교육 참석 인원", "제1공장 교육 참석 인원"])
@pytest.mark.parametrize("quote_extent", ["count", "whole_source"])
def test_prompt_table_related_facts_and_audit_use_the_same_corrected_scope(scope, label, quote_extent):
    from esgenie.report_claims import facts_from_rows, related_facts, row_site_identity
    text = source(scope)
    result = run_case(label, text, "참석 27명" if quote_extent == "count" else text)
    row = result["facts"][0]
    assert row["label"] == "교육 참석 인원" and row["site_scope"] == scope
    assert not row_site_identity(row)[0]
    correction = row["scope_corrections"][0]
    assert correction["original_label"] == label and correction["corrected_label"] == row["label"]
    assert correction["scope_evidence"] == HEADINGS[scope]
    assert row["scope_provenance"]["quote"] == ("참석 27명" if quote_extent == "count" else text)
    for public in [result["table"], result["prompt_facts"], *[f.describe() for f in related_facts("교육 참석",None,{"명"},facts_from_rows([row]))]]:
        assert "제1공장" not in public, public
        assert ("전체 사업장" if scope == "entity" else "원문 미기록") in public
    assert any(c["original_label"] == label for m in result["marks"] for c in m.get("scope_corrections", []))


@pytest.mark.parametrize("scope", list(HEADINGS))
@pytest.mark.parametrize("stored_identity", [True, False])
def test_consumers_reconcile_a_stale_row_before_reading_its_fields(scope, stored_identity):
    from esgenie.report_claims import normalize_source_fact, facts_from_rows, fact_line, table_rows, row_site_identity
    row = {"label":"김해 제1공장 교육 참석 인원", "value":27.0,"unit":"명","role":"참석",
           "period_text":"2026-06-03", "site":"김해 제1공장", "site_scope":scope,
           "site_scope_verified":True, "scope_provenance":{"source":"quantity_scope", "site_scope":scope,
           "scope_heading":HEADINGS[scope]}, "source_file":"교육집계.pdf", "page":0,
           "quote":source(scope)}
    if stored_identity: row["site_identity"]=["김해1공장"]
    original = dict(row)
    clean=normalize_source_fact(row)
    assert row==original and normalize_source_fact(clean)==clean
    assert not row_site_identity(row)[0] and not facts_from_rows([row])[0].sites
    assert "제1공장" not in fact_line(row) and "제1공장" not in _render_source_facts_table([row])
    assert table_rows([row])[0]["label"]=="교육 참석 인원"


@pytest.mark.parametrize("scope", ["", "unknown", "site"])
def test_unknown_default_without_source_verification_does_not_erase_r2(scope):
    from esgenie.report_claims import normalize_source_fact,row_site_identity
    row={"label":"제1공장 교육 참석 인원","site":"김해 제1공장","site_scope":scope}
    assert normalize_source_fact(row)==row
    assert row_site_identity(row)==(frozenset({"김해1공장"}),())


def test_table_and_reference_do_not_merge_entity_and_unknown_counts():
    from esgenie.report_claims import facts_from_rows, table_rows, related_facts
    rows=[run_case("교육 참석 인원",source(s),"참석 27명")["facts"][0] for s in HEADINGS]
    assert len(table_rows(rows))==2
    assert len(related_facts("교육 참석",None,{"명"},facts_from_rows(rows)))==2


@pytest.mark.parametrize("scope", list(HEADINGS))
def test_corrected_scope_and_original_count_reach_final_markdown_and_pdf(scope, tmp_path):
    import fitz
    from esgenie.layer6_report import ReportBlock,ReportDoc
    from esgenie.exporters.report_pdf import export_report_pdf
    label="김해 제1공장 교육 참석 인원"
    held=run_case(label,source(scope),source(scope))
    normal=run_case(label,"교육 기록: 김해 제1공장 / 2026-06-03\n참석 27명","참석 27명")
    doc=ReportDoc("검증용","",2026,"2026-10-06",[
        ReportBlock("scope","원문 범위 대조",held["body"]+"\n\n"+held["table"],"deterministic",held["marks"]),
        ReportBlock("normal","정상 사업장 대조",normal["body"]+"\n\n"+normal["table"],"deterministic",normal["marks"])])
    flat=lambda s:"".join(s.split())
    wrong="2026년 6월 3일 김해 제1공장 교육에는 27명이 참석했다"
    assert flat(doc.to_markdown()).count(flat(wrong))==1
    with fitz.open(export_report_pdf(doc,tmp_path)) as pdf:
        text="".join(p.get_text() for p in pdf)
    assert flat(text).count(flat(wrong))==1
    assert flat("27명") in flat(text)
    assert flat("전체 사업장" if scope=="entity" else "원문 미기록") in flat(text)
    assert any(c["original_label"]==label for m in doc.blocks[0].reviews for c in m.get("scope_corrections",[]))
