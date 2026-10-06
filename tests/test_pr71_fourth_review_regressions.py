"""PR71 82f475e 독립 대조: 새 범위의 시작과 라벨보다 구체적인 원문 사업장.

공급자 호출 없이 실제 매핑·그래프·생성 입력·본문 대조 함수를 연결한다.
값과 정답은 이 파일의 원문/Boundary에 명시하며 제품 결과에서 추출하지 않는다.
"""
import socket
from types import SimpleNamespace as NS

import pytest

from esgenie.embeddings import IndexedDoc
from esgenie.layer2_rag import RAGContext, _source_facts_chunk, _render_source_facts_table
from esgenie.layer6_report import annotate_generated_text
from esgenie.report_claims import facts_from_rows, table_rows
from esgenie.ssot.boundary import Boundary
from esgenie.ssot.evidence_graph import EvidenceGraph, EvidenceNode
from esgenie.ssot.ocr_router import _map_vlm_json


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("No provider/network calls in this review")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


NEW_SCOPES = {
    "entity": "전사 교육 집계 / 2026-06-03",
    "unknown": "교육 기록: 사업장 미기록 / 2026-06-03",
}


def source_text(new_heading):
    return "교육 기록: 김해 제1공장 / 2026-06-03\n참석 15명\n" + new_heading + "\n참석 27명"


def map_metric(text):
    metrics, _ = _map_vlm_json({"metrics": [{
        "metric_hint": "교육 참석 인원", "value": 27, "unit": "명", "period": "2026-06-03",
        "quote": "참석 27명", "boundary": {"period_text": "2026-06-03"},
    }]}, source_text=text, page_no=0)
    assert len(metrics) == 1
    return metrics[0]


def make_chunk(nodes, raw_text="사업장별 교육 집계"):
    graph = EvidenceGraph("TEST", "검토용")
    for i, (label, value, site, quote) in enumerate(nodes):
        graph.add_node(EvidenceNode(
            id=f"node-{i}", metric=label, value=float(value), unit="명", period=2026,
            source="ocr/test", origin="ocr_unstructured", source_file="교육집계.pdf", page=i,
            quote=quote, boundary=Boundary.from_dict({"period_text": "2026-06-03", "site": site})))
    raw = IndexedDoc(text=raw_text, meta={"source_file": "교육집계.pdf"}, chunk_id="c1")
    ctx = RAGContext(kesg_hits=[], industry_hits=[], corp_hits=[(raw, 1.0)])
    return _source_facts_chunk(graph, ctx, "S"), raw


def review_claim(text, chunks):
    gen = NS(text=text, context=NS(all_hits=lambda: [(c, 1.0) for c in chunks]))
    return annotate_generated_text(NS(extraction=NS(mapped={})), "S",
                                   NS(final=NS(generation=gen), final_text=text))


@pytest.mark.parametrize("kind", list(NEW_SCOPES))
def test_a_new_scope_must_not_receive_the_previous_site(kind):
    m = map_metric(source_text(NEW_SCOPES[kind]))
    # 전사/미기록의 표현 방식은 제한하지 않는다. 앞 절의 김해를 확정 상속하면 안 된다.
    assert m.boundary.get("site") != "김해 제1공장", m.boundary


@pytest.mark.parametrize("kind", list(NEW_SCOPES))
def test_the_borrowed_scope_cannot_confirm_a_generated_claim(kind):
    text = source_text(NEW_SCOPES[kind])
    m = map_metric(text)
    chunk, raw = make_chunk([(m.metric_hint, m.value, m.boundary.get("site", ""), m.quote)], text)
    claim = "2026년 6월 3일 김해 제1공장 교육에는 27명이 참석했다 [source_facts_S]."
    body, marks = review_claim(claim, [chunk, raw])
    assert any(r.get("action") == "replaced" for r in marks), (body, marks, chunk.meta["facts"])


@pytest.mark.parametrize("between", ["", "교육 내용\n"])
def test_a_real_site_heading_remains_valid_through_an_internal_subheading(between):
    text = "교육 기록: 김해 제1공장 / 2026-06-03\n" + between + "참석 27명"
    assert map_metric(text).boundary.get("site") == "김해 제1공장"


@pytest.mark.parametrize("sites", [("김해 제1공장", "부산 제1공장"), ("부산 제1공장", "김해 제1공장")])
def test_a_short_site_in_the_label_cannot_erase_the_full_boundary(sites):
    chunk, _ = make_chunk([("제1공장 교육 참석 인원", 27, site, f"{site} 교육 참석 인원 27명") for site in sites])
    rows = chunk.meta["facts"]
    assert len(rows) == 2 and {r["site"] for r in rows} == set(sites), rows


def explicit_rows():
    return [{"label": "제1공장 교육 참석 인원", "value": 27.0, "unit": "명", "role": "참석",
             "period_text": "2026-06-03", "site": site, "source_file": "교육집계.pdf", "page": i}
            for i, site in enumerate(["김해 제1공장", "부산 제1공장"])]


def test_downstream_table_and_facts_keep_the_full_sites():
    rows = explicit_rows()
    table = _render_source_facts_table(rows)
    assert len(table_rows(rows)) == 2, table
    assert {frozenset(f.sites) for f in facts_from_rows(rows)} == {
        frozenset({"김해1공장"}), frozenset({"부산1공장"})}
    assert "김해 제1공장" in table and "부산 제1공장" in table


def test_a_kimhae_fact_with_a_short_label_cannot_confirm_busan():
    chunk, _ = make_chunk([("제1공장 교육 참석 인원", 27, "김해 제1공장", "김해 제1공장 교육 참석 인원 27명")])
    claim = "2026년 6월 3일 부산 제1공장 교육에는 27명이 참석했다 [source_facts_S]."
    body, marks = review_claim(claim, [chunk])
    assert any(r.get("action") == "replaced" for r in marks), (body, marks, chunk.meta["facts"])


def test_generic_labels_keep_two_different_sites():
    sites = {"김해 제1공장", "부산 제1공장"}
    chunk, _ = make_chunk([("교육 참석 인원", 27, site, f"{site} 교육 참석 인원 27명") for site in sorted(sites)])
    rows = chunk.meta["facts"]
    assert len(rows) == 2 and {r["site"] for r in rows} == sites
    assert len(table_rows(rows)) == 2


def test_a_true_duplicate_is_still_one_fact():
    node = ("제1공장 교육 참석 인원", 27, "김해 제1공장", "김해 제1공장 교육 참석 인원 27명")
    chunk, _ = make_chunk([node, node])
    assert len(chunk.meta["facts"]) == 1


@pytest.mark.parametrize("heading", [*NEW_SCOPES.values(), "적용 범위: 전체 사업장", "사업장: 미상", "전사 교육 집계 / 2026-06-10"])
@pytest.mark.parametrize("citation", [" [c1]", " [source_facts_S]", ""])
def test_raw_and_structured_paths_stop_at_a_scope_transition(heading, citation):
    text = source_text(heading)
    m = map_metric(text)
    chunk, raw = make_chunk([(m.metric_hint, m.value, m.boundary.get("site", ""), m.quote)], text)
    body, marks = review_claim("2026년 6월 3일 김해 제1공장 교육에는 27명이 참석했다" + citation + ".", [chunk, raw])
    assert any(r.get("action") == "replaced" for r in marks)
    assert "27명" in _render_source_facts_table(chunk.meta["facts"])
    from esgenie.report_claims import chunk_occurrences
    occ = next(o for o in chunk_occurrences(text) if o.q.value == 27)
    assert not occ.sites
    if "06-10" in heading:
        assert occ.when.start.isoformat() == "2026-06-10"


@pytest.mark.parametrize("subheading", ["교육 내용", "참석 현황", "세부 내역", "실시 내용"])
def test_internal_subheadings_preserve_the_site_in_both_paths(subheading):
    text = "교육 기록: 김해 제1공장 / 2026-06-03\n" + subheading + "\n참석 27명"
    from esgenie.report_claims import chunk_occurrences
    assert map_metric(text).boundary["site"] == "김해 제1공장"
    assert next(o for o in chunk_occurrences(text) if o.q.value == 27).sites == frozenset({"김해1공장"})


@pytest.mark.parametrize("label,site,quote", [
    ("김해 제1공장 교육 참석 인원", "부산 제1공장", ""),
    ("부산 제1공장 교육 참석 인원", "김해 제1공장", ""),
    ("제1공장 교육 참석 인원", "김해 제1공장", "부산 제1공장 교육 참석 인원 27명"),
])
def test_conflicting_source_fields_are_recorded_and_not_adopted(label, site, quote):
    from esgenie.report_claims import fact_line, related_facts
    row = {"label": label, "value": 27.0, "unit": "명", "role": "참석", "period_text": "2026-06-03",
           "site": site, "quote": quote, "source_file": "교육집계.pdf", "page": 0}
    facts = facts_from_rows([row])
    assert not facts[0].sites and facts[0].site_conflicts
    assert "사업장 충돌" in fact_line(row) and "사업장 충돌" in _render_source_facts_table([row])
    chunk = IndexedDoc(text=fact_line(row), meta={"source": "source_facts", "facts": [row]}, chunk_id="source_facts_S")
    for target in ["김해 제1공장", "부산 제1공장"]:
        body, marks = review_claim(f"2026년 6월 3일 {target} 교육에는 27명이 참석했다 [source_facts_S].", [chunk])
        assert any("site_conflict" in m.get("problems", []) for m in marks)
    assert "사업장 충돌" in related_facts("교육 참석", None, {"명"}, facts)[0].describe()


def test_fact_collection_keeps_conflict_and_both_sources():
    from esgenie.report_claims import related_facts
    chunk, _ = make_chunk([
        ("김해 제1공장 교육 참석 인원", 27, "부산 제1공장", "부산 제1공장 참석 27명"),
        ("제1공장 교육 참석 인원", 27, "김해 제1공장", "김해 제1공장 참석 27명"),
        ("제1공장 교육 참석 인원", 27, "부산 제1공장", "부산 제1공장 참석 27명"),
    ])
    rows = chunk.meta["facts"]
    assert len(rows) == len(table_rows(rows)) == 3
    assert any(r["site_conflicts"] for r in rows)
    assert len(related_facts("교육 참석", None, {"명"}, facts_from_rows(rows))) == 3
    assert all(f"교육집계.pdf {i}쪽" in _render_source_facts_table(rows) for i in [1, 2, 3])


def test_specific_source_site_and_short_alias_form_one_identity():
    from esgenie.report_claims import fact_site_identity, sites_compatible
    for label,site,quote in [("제1공장 교육 참석 인원", "김해 제1공장", ""),
                             ("김해 제1공장 교육 참석 인원", "제1공장", ""),
                             ("제1공장 교육 참석 인원", "", "김해 제1공장 참석 27명")]:
        assert fact_site_identity(label, site, quote) == (frozenset({"김해1공장"}), ())
    assert sites_compatible(frozenset({"1공장"}), frozenset({"김해11공장"})) is False


@pytest.mark.parametrize("raw", ["제1공장 교육 참석 인원 27명", "항목 | 값\n제1공장 교육 참석 인원 | 27명"])
def test_a_short_site_in_a_raw_quantity_keeps_its_specific_heading(raw):
    from esgenie.report_claims import chunk_occurrences
    text = "교육 기록: 김해 제1공장 / 2026-06-03\n" + raw
    occurrences = [o for o in chunk_occurrences(text) if o.q.unit == "명"]
    assert occurrences[0].sites == frozenset({"김해1공장"})
    source = IndexedDoc(text=text, meta={"source_file": "교육집계.pdf"}, chunk_id="c1")
    body, marks = review_claim("2026년 6월 3일 부산 제1공장 교육에는 27명이 참석했다 [c1].", [source])
    assert any(m.get("action") == "replaced" for m in marks)


def test_a_short_site_in_an_ocr_quote_keeps_the_source_heading():
    from esgenie.ssot.ocr_router import _source_quantity_site_boundary
    quote = "제1공장 교육 참석 인원 27명"
    boundary = _source_quantity_site_boundary({}, quote, "교육 기록: 김해 제1공장 / 2026-06-03\n" + quote, 0)
    assert boundary["site"] == "김해 제1공장"


def test_source_transition_clears_an_invented_model_site_and_records_why():
    from esgenie.ssot.ocr_router import _source_quantity_site_boundary
    boundary = _source_quantity_site_boundary({"site": "김해 제1공장"}, "참석 27명", source_text(NEW_SCOPES["entity"]), 0)
    assert boundary["site"] == "" and boundary["site_scope"] == "entity"
    record = boundary["provenance"][-1]
    assert record["scope_from"] == "scope_transition" and record["model_site"] == "김해 제1공장"


def test_scope_transfer_edges_reach_the_final_document_and_pdf(tmp_path):
    import fitz
    from esgenie.layer6_report import ReportDoc, ReportBlock
    from esgenie.exporters.report_pdf import export_report_pdf
    text = source_text(NEW_SCOPES["entity"])
    m = map_metric(text)
    chunk, raw = make_chunk([(m.metric_hint, m.value, m.boundary.get("site", ""), m.quote)], text)
    wrong = "2026년 6월 3일 김해 제1공장 교육에는 27명이 참석했다"
    held, marks = review_claim(wrong + " [c1].", [chunk, raw])
    rows = explicit_rows()
    normal_chunk = IndexedDoc(text="facts", meta={"source": "source_facts", "facts": rows}, chunk_id="source_facts_S")
    right = "2026년 6월 3일 부산 제1공장 교육에는 27명이 참석했다"
    kept, _ = review_claim(right + " [source_facts_S].", [normal_chunk])
    block = ReportBlock("scope", "사업장 대조", held + "\n\n" + kept + "\n" + _render_source_facts_table(rows), "deterministic", marks)
    doc = ReportDoc("검증", "", 2026, "2026-10-06", [block])
    markdown = doc.to_markdown()
    assert wrong not in markdown and right in markdown and marks
    with fitz.open(export_report_pdf(doc, tmp_path)) as pdf:
        body = "".join(page.get_text() for page in pdf)
    flat = lambda s: "".join(s.split())
    assert flat(wrong) not in flat(body) and flat(right) in flat(body)
    assert all(flat(s) in flat(body) for s in ["김해 제1공장", "부산 제1공장", "교육집계.pdf 1쪽", "교육집계.pdf 2쪽"])
