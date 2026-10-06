"""PR71 257d133: 같은 범위 정의가 인용 길이·줄 배치 때문에 무효화되지 않아야 한다."""
import socket
from types import SimpleNamespace as NS

import pytest

from esgenie.embeddings import IndexedDoc
from esgenie.layer2_rag import RAGContext, _source_facts_chunk
from esgenie.layer6_report import annotate_generated_text
from esgenie.ssot.boundary import Boundary
from esgenie.ssot.evidence_graph import EvidenceGraph, EvidenceNode
from esgenie.ssot.ocr_router import _map_vlm_json

PREFIX = "교육 기록: 김해 제1공장 / 2026-06-03\n참석 15명\n"
CLAIM = "2026년 6월 3일 김해 제1공장 교육에는 27명이 참석했다 [source_facts_S]."
SCOPES = {
    "entity_heading": "전사 교육 집계 / 2026-06-03\n참석 27명",
    "unknown_heading": "교육 기록: 사업장 미기록 / 2026-06-03\n참석 27명",
    "entity_same_line": "전사 교육 집계 / 2026-06-03 / 참석 27명",
    "unknown_same_line": "사업장 미기록 / 2026-06-03 / 참석 27명",
    "heading_with_count": "전사 교육 집계 / 2026-06-03 (대상 30명)\n참석 27명",
    "scope_table": "적용 범위 | 전체 사업장\n참석 27명",
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("This review does not call a provider")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


def mapped_review(text, quote):
    metrics, _ = _map_vlm_json({"metrics": [{
        "metric_hint": "교육 참석 인원", "value": 27, "unit": "명", "period": "2026-06-03",
        "quote": quote, "boundary": {"period_text": "2026-06-03"},
    }]}, source_text=text, page_no=0)
    assert len(metrics) == 1
    metric = metrics[0]
    graph = EvidenceGraph("TEST", "검토용")
    graph.add_node(EvidenceNode(
        id="n27", metric=metric.metric_hint, value=metric.value, unit=metric.unit, period=2026,
        source="ocr/test", origin="ocr_unstructured", source_file="교육집계.pdf", page=metric.page,
        quote=metric.quote, boundary=Boundary.from_dict(metric.boundary)))
    raw = IndexedDoc(text=text, meta={"source_file": "교육집계.pdf"}, chunk_id="c1")
    ctx = RAGContext(kesg_hits=[], industry_hits=[], corp_hits=[(raw, 1.0)])
    facts = _source_facts_chunk(graph, ctx, "S")
    assert facts is not None
    generation = NS(text=CLAIM, context=NS(all_hits=lambda: [(raw, 1.0), (facts, 1.0)]))
    body, marks = annotate_generated_text(NS(extraction=NS(mapped={})), "S",
                                         NS(final=NS(generation=generation), final_text=CLAIM))
    return {"boundary": metric.boundary, "facts": facts.meta["facts"], "body": body, "marks": marks}


@pytest.mark.parametrize("case", list(SCOPES))
@pytest.mark.parametrize("quote_extent", ["quantity", "scope_and_quantity"])
def test_an_explicit_scope_survives_quote_extent_and_layout(case, quote_extent):
    tail = SCOPES[case]
    quote = "참석 27명" if quote_extent == "quantity" else tail
    result = mapped_review(PREFIX + tail, quote)
    assert any(m.get("action") == "replaced" for m in result["marks"]), result
    assert any(r["value"] == 27 for r in result["facts"]), "The original count must survive."


@pytest.mark.parametrize("quote", ["참석 27명", "교육 내용\n참석 27명"])
def test_a_normal_quantity_still_uses_its_real_parent_site(quote):
    text = "교육 기록: 김해 제1공장 / 2026-06-03\n교육 내용\n참석 27명"
    result = mapped_review(text, quote)
    assert not any(m.get("action") == "replaced" for m in result["marks"]), result
    assert "김해 제1공장 교육에는 27명이 참석했다" in result["body"]


@pytest.mark.parametrize("case", list(SCOPES))
def test_a_quote_including_the_previous_section_cannot_restore_its_site(case):
    text = PREFIX + SCOPES[case]
    result = mapped_review(text, text)
    assert result["boundary"]["site"] == ""
    assert not result["facts"][0]["site_identity"]
    assert any(m.get("action") == "replaced" for m in result["marks"])


@pytest.mark.parametrize("sentence", [
    "전사 교육에 31명이 참석했다.",
    "안내: 전사 교육에 31명이 참석했다",
    "교육 참석 인원 31명 (사업장 미기록).",
])
def test_a_sentence_scope_applies_locally_and_keeps_the_next_rows_parent(sentence):
    from esgenie.report_claims import chunk_occurrences
    from esgenie.ssot.ocr_router import _source_quantity_site_boundary
    text = "교육 기록: 김해 제1공장 / 2026-06-03\n" + sentence + "\n참석 27명"
    counts = {o.q.value: o for o in chunk_occurrences(text)}
    assert not counts[31].sites
    assert counts[27].sites == frozenset({"김해1공장"})
    assert _source_quantity_site_boundary({}, sentence, text, 0, value=31, unit="명")["site"] == ""
    assert mapped_review(text, "참석 27명")["boundary"]["site"] == "김해 제1공장"


@pytest.mark.parametrize("tail", [
    "전사 교육 집계 / 대상 30명\n참석 | 27명",
    "적용 범위 | 전체 사업장 | 대상 30명\n참석 | 27명",
    "사업장 | 미기록 | 대상 30명\n참석 | 27명",
    "항목 | 수량\n전체 사업장 참석 | 27명",
])
def test_scope_in_quantity_tables_reaches_both_paths(tail):
    from esgenie.report_claims import chunk_occurrences
    text = PREFIX + tail
    quote = tail.splitlines()[-1]
    result = mapped_review(text, quote)
    assert not next(o for o in chunk_occurrences(text) if o.q.value == 27).sites
    assert result["boundary"]["site"] == ""
    assert any(m.get("action") == "replaced" for m in result["marks"])


@pytest.mark.parametrize("tail,expected", [
    ("전사 교육 집계 / 대상 30명\n부산 제1공장 참석 27명", "부산 제1공장"),
    ("사업장 미기록\n제1공장 참석 27명", "제1공장"),
    ("전사 교육 집계 / 대상 30명\n교육 기록: 부산 제1공장\n참석 27명", "부산 제1공장"),
    ("김해 제1공장 참석 15명, 전사 참석 27명", ""),
    ("전사 대상 30명, 부산 제1공장 참석 27명", "부산 제1공장"),
])
def test_a_local_explicit_site_or_scope_belongs_to_its_own_quantity(tail, expected):
    from esgenie.report_claims import chunk_occurrences
    result = mapped_review(PREFIX + tail, tail)
    assert result["boundary"]["site"] == expected
    from esgenie.ssot.ocr_router import _site_keys
    assert next(o for o in chunk_occurrences(PREFIX + tail) if o.q.value == 27).sites == frozenset(_site_keys(expected))


def test_an_ambiguous_same_value_quote_does_not_choose_a_section():
    from esgenie.ssot.ocr_router import _source_quantity_site_boundary
    text = "교육 기록: 김해 제1공장\n참석 27명\n전사 교육 집계\n참석 27명"
    assert _source_quantity_site_boundary({}, text, text, 0, value=27, unit="명") == {}


def test_an_intervening_other_site_cannot_supply_the_old_heading():
    from esgenie.ssot.ocr_router import _source_quantity_site_boundary
    text = "교육 기록: 김해 제1공장\n부산 제1공장 참석 15명\n참석 27명"
    assert _source_quantity_site_boundary({}, "참석 27명", text, 0).get("site", "") == ""
