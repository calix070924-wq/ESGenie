"""PR71 c18a9f3 독립 재검토: 명시 범위의 근거 부족과 사업장별 사실 보존."""
import socket
from types import SimpleNamespace as NS

import pytest

from esgenie.embeddings import IndexedDoc
from esgenie.layer2_rag import RAGContext, _source_facts_chunk
from esgenie.layer6_report import annotate_generated_text
from esgenie.ssot.boundary import Boundary
from esgenie.ssot.evidence_graph import EvidenceGraph, EvidenceNode


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("독립 검사 중 네트워크 호출 금지")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


def review(text, raw, fact):
    docs = [
        (IndexedDoc(text=raw, meta={"source_file": "a.pdf"}, chunk_id="c1"), 1.0),
        (IndexedDoc(text="facts", meta={"source": "source_facts", "facts": [fact]}, chunk_id="source_facts_S"), 1.0),
    ]
    generation = NS(text=text, context=NS(all_hits=lambda: docs))
    return annotate_generated_text(NS(extraction=NS(mapped={})), "S", NS(final=NS(generation=generation), final_text=text))


def source(axis, supported=False):
    fact = {"label": "교육 참석 인원", "value": 27.0, "unit": "명", "source_file": "a.pdf"}
    if axis == "site":
        fact["period_text"] = "2026-06-03"
        raw = "2026년 6월 3일 교육 참석 인원 27명."
        text = "2026년 6월 3일 양산 제2공장 교육에 27명이 참석했다"
        if supported:
            fact["site"] = "양산 제2공장"
            raw = "2026년 6월 3일 양산 제2공장 교육 참석 인원 27명."
    else:
        fact["site"] = "김해 제1공장"
        raw = "김해 제1공장 교육 참석 인원 27명."
        text = "2026년 6월 3일 김해 제1공장 교육에 27명이 참석했다"
        if supported:
            fact["period_text"] = "2026-06-03"
            raw = "2026년 6월 3일 김해 제1공장 교육 참석 인원 27명."
    return fact, raw, text


@pytest.mark.parametrize("axis", ["site", "date"])
@pytest.mark.parametrize("citation", [" [source_facts_S]", " [c1]", ""])
def test_an_explicit_scope_needs_support_not_just_no_contradiction(axis, citation):
    fact, raw, text = source(axis)
    body, marks = review(text + citation + ".", raw, fact)
    assert any(m.get("action") == "replaced" for m in marks) and text not in body, (body, marks)


@pytest.mark.parametrize("axis", ["site", "date"])
@pytest.mark.parametrize("citation", [" [source_facts_S]", " [c1]", ""])
def test_the_same_assertion_with_its_scope_supported_is_preserved(axis, citation):
    fact, raw, text = source(axis, supported=True)
    body, marks = review(text + citation + ".", raw, fact)
    # 정상 수량·범위가 교체/보류되지 않는지 본다. 기존 한계인 `제1공장` 숫자 1의 bare-number 표시와 구분한다.
    assert text in body and not any(m.get("action") == "replaced" for m in marks), (body, marks)


@pytest.mark.parametrize("axis", ["site", "date"])
def test_a_source_fact_without_an_added_scope_is_still_preserved(axis):
    fact, raw, _ = source(axis)
    body, marks = review(raw + " [c1]", raw, fact)
    assert "27명" in body and not marks, (body, marks)


def facts_chunk(nodes):
    graph = EvidenceGraph("TEST", "검토용")
    for index, (site, count) in enumerate(nodes):
        graph.add_node(EvidenceNode(
            id=f"node-{index}", metric="교육 참석 인원", value=float(count), unit="명", period=2026,
            source="ocr/test", origin="ocr_unstructured", source_file="교육집계.pdf", page=index,
            quote=f"2026년 6월 3일 {site} 교육 참석 인원 {count}명",
            boundary=Boundary.from_dict({"period_text": "2026-06-03", "site": site})))
    ctx = RAGContext(kesg_hits=[], industry_hits=[], corp_hits=[
        (IndexedDoc(text="사업장별 교육 집계", meta={"source_file": "교육집계.pdf"}, chunk_id="c1"), 1.0)])
    return _source_facts_chunk(graph, ctx, "S").meta["facts"]


@pytest.mark.parametrize("sites", [("김해 제1공장", "양산 제2공장"), ("양산 제2공장", "김해 제1공장")])
def test_same_counts_from_different_sites_survive_the_upstream_fact_builder(sites):
    rows = facts_chunk([(site, 27) for site in sites])
    assert len(rows) == 2 and {r["site"] for r in rows} == set(sites), rows


def test_different_counts_from_different_sites_are_preserved():
    rows = facts_chunk([("김해 제1공장", 27), ("양산 제2공장", 31)])
    assert len(rows) == 2


def test_an_identical_fact_can_still_be_deduplicated():
    rows = facts_chunk([("김해 제1공장", 27), ("김해 제1공장", 27)])
    assert len(rows) == 1 and rows[0]["site"] == "김해 제1공장"


@pytest.mark.parametrize("site", ["김해 제1공장", "양산 제2공장"])
def test_each_site_survives_generation_table_and_citation(site):
    from esgenie.layer2_rag import _render_source_facts_table
    from esgenie.report_claims import facts_from_rows, related_facts
    rows = facts_chunk([("김해 제1공장", 27), ("양산 제2공장", 27)])
    table = _render_source_facts_table(rows)
    assert f"2026-06-03 | {site} | 교육집계.pdf" in table
    body, marks = review(f"2026년 6월 3일 {site} 교육에 27명이 참석했다 [source_facts_S].",
                         f"2026년 6월 3일 {site} 교육 참석 인원 27명.", next(r for r in rows if r["site"] == site))
    assert site in body and not any(m.get("action") == "replaced" for m in marks)
    listed = related_facts("교육 참석 인원", None, {"명"}, facts_from_rows(rows))
    assert len(listed) == 2 and all(f.sites for f in listed)


def map_site(text, quote="참석 27명", boundary=None):
    from esgenie.ssot.ocr_router import _map_vlm_json
    metrics, _ = _map_vlm_json({"metrics": [{"metric_hint": "교육 참석 인원", "value": 27, "unit": "명",
                             "period": "2026-06-03", "quote": quote, "boundary": boundary or {}}], "clauses": []},
                             source_text=text, page_no=2)
    return metrics[0].boundary


@pytest.mark.parametrize("text,expected", [
    ("교육 기록: 김해 제1공장 / 2026-06-03\n대상 김해 제1공장 근무 인원 30명\n참석 27명", "김해 제1공장"),
    ("교육 기록: 김해 제1공장 / 2026-06-03\n교육 기록: 양산 제2공장 / 2026-06-03\n참석 27명", "양산 제2공장"),
    ("교육 기록: 김해 제1공장 / 2026-06-03\n양산 제2공장 근무 인원 30명\n참석 27명", ""),
    ("참석 27명\n교육 기록: 김해 제1공장 / 2026-06-03", ""),
    ("교육 기록: 김해 제1공장 / 2026-06-03\n참석 27명\n교육 기록: 양산 제2공장 / 2026-06-03\n참석 27명", ""),
    ("교육 기록 / 2026-06-03\n참석 27명", ""),
])
def test_source_site_is_linked_only_to_a_unique_quote_and_its_preceding_heading(text, expected):
    assert map_site(text).get("site", "") == expected


def test_model_site_is_corrected_from_the_source_and_recorded():
    boundary = map_site("교육 기록: 김해 제1공장 / 2026-06-03\n참석 27명", boundary={"site": "양산 제2공장"})
    assert boundary["site"] == "김해 제1공장"
    record = boundary["provenance"][-1]
    assert record["source"] == "quantity_scope" and record["page"] == 2
    assert record["model_site"] == "양산 제2공장" and "김해" in record["scope_heading"]


def test_a_date_in_an_unrelated_previous_sentence_is_not_quantity_support():
    fact = {"label": "교육 참석 인원", "value": 27.0, "unit": "명", "source_file": "a.pdf"}
    body, marks = review("2026년 6월 3일 교육에 27명이 참석했다 [c1].",
                         "2026년 6월 3일 교육을 예정했다. 참석 27명.", fact)
    assert any(m.get("action") == "replaced" for m in marks)
