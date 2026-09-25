"""추가 항목 검색의 원문 보존, 게이트, 기존 회사 인덱스 격리를 확인한다."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from esgenie.embeddings import BM25Index, IndexedDoc, VectorIndex
from esgenie.knowledge.kesg_items import by_code
from esgenie.layer2_rag import CorpIndex, HybridRAG, RAGContext
from esgenie.rag_gates import evaluate_retrieval


class _CharacterTokenizer:
    def __call__(self, text, *, add_special_tokens=True, truncation=False):
        assert not truncation
        return {"input_ids": list(range(len(text) + (2 if add_special_tokens else 0)))}


def _split_index(limit=40):
    index = object.__new__(VectorIndex)
    index._st_model = SimpleNamespace(tokenizer=_CharacterTokenizer(), max_seq_length=limit)
    return index


def _corp(docs):
    vector, bm25 = VectorIndex(), BM25Index()
    vector.build(docs)
    bm25.build(docs)
    return CorpIndex(vector, bm25)


def test_embedding_parts_cover_source_and_preserve_location_without_mutation():
    source = "경영 방침을 설명한다. " * 12 + "\n산업재해 사망 2건을 보고했다."
    parent = IndexedDoc(source, {"source_file": "증빙.pdf", "page": 7, "node_id": "node_9"}, "parent_9")
    parts = _split_index().split_documents([parent])

    assert len(parts) > 1
    assert len({p.chunk_id for p in parts}) == len(parts)
    covered = set()
    for part in parts:
        start, end = part.meta["char_start"], part.meta["char_end"]
        assert part.text == source[start:end]
        assert len(part.text) + 2 <= 40
        assert part.meta["parent_chunk_id"] == "parent_9"
        assert part.meta["source_file"] == "증빙.pdf"
        assert part.meta["page"] == 7 and part.meta["node_id"] == "node_9"
        assert part.meta["id"] == part.chunk_id
        covered.update(range(start, end))
    assert covered == set(range(len(source)))
    assert any("사망 2건" in p.text for p in parts)
    assert parent.meta == {"source_file": "증빙.pdf", "page": 7, "node_id": "node_9"}
    assert parent.chunk_id == "parent_9" and parent.text == source


def test_embedding_split_finds_tail_and_returns_parent_document():
    """토큰 한도 뒤에 있는 대목으로 검색해도 부모 문서가 한 번만 회수된다.

    종전에는 encode()가 한도 뒤를 조용히 버려 이 질의로 문서를 찾을 수 없었다.
    회수 단위는 부모 문서여야 한다 — 게이트가 보는 본문과 감사 기록의 청크 id가
    달라지면 안 된다.
    """
    index = _split_index(limit=40)
    index._faiss = None
    index._index = None
    index._docs = []
    index._row_docs = []
    index._vectors = None
    index._st_model.encode = lambda texts, **kwargs: index._fallback_embed(list(texts))

    head = "경영 방침을 설명한다. " * 12
    long_doc = IndexedDoc(head + "\n산업재해 사망 2건을 보고했다.", {"node_id": "n9"}, "long")
    short_doc = IndexedDoc("교육 과정을 운영한다.", {"node_id": "n1"}, "short")
    index.build([long_doc, short_doc], embedding_split=True)

    assert len(index._row_docs) > len(index._docs)
    assert [d.chunk_id for d in index._docs] == ["long", "short"]

    hits = index.search("산업재해 사망 2건을 보고했다.", k=2)
    ids = [doc.chunk_id for doc, _ in hits]
    assert ids[0] == "long"
    assert len(ids) == len(set(ids)), "같은 부모가 상위 k를 중복 점유하면 안 된다"
    assert hits[0][0].text == long_doc.text  # 조각이 아니라 부모 원문


def test_resplitting_parts_keeps_original_parent_and_offsets():
    """이미 조각난 문서를 다시 나눠도 원래 부모와 원문 위치를 잃지 않는다.

    영역 검색 인덱스를 분할한 뒤 `retrieve_for_items`가 같은 문서를 한 번 더
    통과시킨다. 그때 부모가 조각 자신으로 덮어써지면 원장·감사 기록에서 원문을
    되짚을 수 없다.
    """
    source = "경영 방침을 설명한다. " * 12 + "\n산업재해 사망 2건을 보고했다."
    parent = IndexedDoc(source, {"node_id": "node_9"}, "parent_9")
    first = _split_index().split_documents([parent])
    assert len(first) > 1

    second = _split_index().split_documents(first)

    assert [d.chunk_id for d in second] == [d.chunk_id for d in first]
    for part in second:
        assert part.meta["parent_chunk_id"] == "parent_9"
        assert part.meta["node_id"] == "node_9"
        start, end = part.meta["char_start"], part.meta["char_end"]
        assert part.text == source[start:end]


def test_short_document_keeps_id_and_text():
    parent = IndexedDoc("짧은 근거", {"node_id": "n1"}, "original")
    parts = _split_index().split_documents([parent])
    assert len(parts) == 1
    assert parts[0].chunk_id == "original"
    assert parts[0].text == parent.text
    assert parts[0] is not parent
    assert parts[0].meta["char_start"] == 0
    assert parts[0].meta["char_end"] == len(parent.text)


def test_item_search_uses_catalog_query_and_keeps_original_corpus_unchanged():
    doc = IndexedDoc(
        "2024년 환경 법규 위반 2건 및 환경 과징금 내역을 공시했다.",
        {"source": "ssot_text", "source_file": "환경증빙.pdf", "page": 3, "kesg_code": "E-8-1"},
        "environment_fact",
    )
    corp = _corp([doc])
    before_meta = dict(doc.meta)
    before_vectors = corp.vector._vectors.copy()
    before_hits = corp.vector.search("환경 법규 위반", k=1)
    rag = object.__new__(HybridRAG)

    result = rag.retrieve_for_items([by_code("E-8-1")], corp=corp)[0]

    assert result.item_code == "E-8-1"
    assert "환경 법규 위반" in result.query and "환경 과징금" in result.query
    assert "2024" not in result.query and "2건" not in result.query
    assert result.accepted_hits
    assert result.accepted_hits[0][0].meta["parent_chunk_id"] == "environment_fact"
    assert doc.meta == before_meta
    assert corp.vector._docs == [doc]
    np.testing.assert_array_equal(corp.vector._vectors, before_vectors)
    assert corp.vector.search("환경 법규 위반", k=1) == before_hits
    assert [c["id"] for c in result.as_chunk_dicts()] == result.to_dict()["accepted_chunk_ids"]


def test_item_search_rejects_unrelated_and_same_area_generic_evidence():
    rag = object.__new__(HybridRAG)
    for text in (
        "2024년 이사회 사외이사 출석률은 98%이다.",
        "2024년 환경 관리 담당 인원은 8명이다.",
    ):
        corp = _corp([IndexedDoc(text, {"source": "ssot_text"}, "unrelated")])
        result = rag.retrieve_for_items([by_code("E-8-1")], corp=corp)[0]
        assert not result.accepted_hits
        assert result.decision.decision != "ACCEPT"


def test_zero_violation_is_retrievable_evidence_without_a_risk_label():
    corp = _corp([IndexedDoc(
        "2024년 환경 법규 위반은 0건이고 환경 과징금은 0원이다.",
        {"source": "ssot_text"}, "zero_incidents",
    )])
    result = object.__new__(HybridRAG).retrieve_for_items([by_code("E-8-1")], corp=corp)[0]
    assert result.accepted_hits
    assert "risk" not in result.to_dict()


def test_top1_acceptance_does_not_accept_an_unrelated_tail(monkeypatch):
    from esgenie import layer2_rag

    good = IndexedDoc("2024년 환경 법규 위반 2건, 환경 과징금 내역을 공시한다.", {"source": "ssot_text"}, "good")
    unrelated = IndexedDoc("2024년 이사회 사외이사 출석률은 98%이다.", {"source": "ssot_text"}, "other")
    hits = [(good, 1.0), (unrelated, 0.9)]
    decision = evaluate_retrieval("E", hits, query="환경 법규 위반, 환경 과징금", tier=2)
    assert decision.decision == "ACCEPT"
    monkeypatch.setattr(layer2_rag, "run_retrieval_cascade", lambda **kwargs: SimpleNamespace(
        hits=hits, decision=decision, tier=2, queries_tried=[kwargs["query"]], tier_hits=hits,
    ))

    result = object.__new__(HybridRAG).retrieve_for_items([by_code("E-8-1")], corp=_corp([good, unrelated]))[0]

    assert [d.chunk_id for d, _ in result.accepted_hits] == ["good"]
    assert result.hit_decisions["other"].decision != "ACCEPT"


def test_scale_only_failures_do_not_relax_content_gates_below_top1(monkeypatch):
    """2위 이하 후보는 점수 척도 차이로만 구제하고 내용 게이트는 그대로 적용한다."""
    from esgenie import layer2_rag

    top = IndexedDoc("2024년 환경 법규 위반 0건, 환경 과징금 0원으로 집계했다.", {"source": "ssot_text"}, "top")
    good = IndexedDoc("2024년 국내 환경 법규 위반 1건, 환경 과징금 80만원을 공시한다.", {"source": "ssot_text"}, "good")
    no_number = IndexedDoc("환경 법규 위반과 환경 과징금 관리 방침을 운영한다.", {"source": "ssot_text"}, "policy")
    query = "환경 법규 위반, 환경 과징금"
    hits = [(top, 1.0), (good, 0.47), (no_number, 0.46)]
    # 2위 후보의 유일한 탈락 사유는 tier별 점수 척도 차이(R1)뿐이며 내용 게이트는 통과한다.
    assert evaluate_retrieval("E", [hits[1]], query=query, tier=1).hard_fails == ["R1_low_top1_score"]
    decision = evaluate_retrieval("E", hits, query=query, tier=1)
    assert decision.decision == "ACCEPT"
    monkeypatch.setattr(layer2_rag, "run_retrieval_cascade", lambda **kwargs: SimpleNamespace(
        hits=hits, decision=decision, tier=1, queries_tried=[kwargs["query"]], tier_hits=hits,
    ))

    result = object.__new__(HybridRAG).retrieve_for_items(
        [by_code("E-8-1")], corp=_corp([top, good, no_number]))[0]

    # 점수 척도로만 떨어진 2위는 살리고, 수치 없는 방침 문구는 내용 게이트로 탈락한다.
    assert [d.chunk_id for d, _ in result.accepted_hits] == ["top", "good"]
    assert "R3_numeric_evidence_missing" in result.hit_decisions["policy"].hard_fails


def test_single_correct_hit_is_accepted_on_its_own():
    corp = _corp([IndexedDoc("2024년 환경 법규 위반 1건과 환경 과징금 80만원을 공시했다.",
                             {"source": "ssot_text"}, "only")])
    result = object.__new__(HybridRAG).retrieve_for_items([by_code("E-8-1")], corp=corp)[0]
    assert [d.meta["parent_chunk_id"] for d, _ in result.accepted_hits] == ["only"]


def test_policy_text_without_an_incident_is_not_accepted_as_incident_evidence():
    corp = _corp([IndexedDoc("환경 법규 준수 모니터링 체계와 환경 과징금 대응 방침을 운영합니다.",
                             {"source": "ssot_text"}, "policy_only")])
    result = object.__new__(HybridRAG).retrieve_for_items([by_code("E-8-1")], corp=corp)[0]
    assert not result.accepted_hits
    assert "R3_numeric_evidence_missing" in result.decision.hard_fails


def test_fact_at_the_end_of_a_long_document_is_still_retrievable():
    """긴 문서의 꼬리에 있는 수치가 임베딩 절단으로 사라지지 않는다(분할 인덱스)."""
    tail = "2024년 환경 법규 위반은 1건이며 환경 과징금은 80만원이다."
    long_doc = IndexedDoc("환경 경영 추진 체계와 조직 운영 방향을 설명합니다. " * 20 + tail,
                          {"source": "ssot_text"}, "long")
    result = object.__new__(HybridRAG).retrieve_for_items(
        [by_code("E-8-1")], corp=_corp([long_doc]))[0]
    accepted = [d for d, _ in result.accepted_hits]
    assert accepted, "꼬리 수치를 담은 조각이 채택되어야 한다"
    assert any(tail in d.text for d in accepted)
    assert all(d.meta["parent_chunk_id"] == "long" for d in accepted)  # 원문 위치를 잃지 않는다


def test_table_body_and_footnote_in_separate_chunks_are_both_reachable():
    body = IndexedDoc("환경 법규 위반 건수는 2024년 1건, 환경 과징금은 80만원이다.",
                      {"source": "ssot_text", "page": 38}, "body")
    footnote = IndexedDoc("주) 환경 법규 위반 건수는 2024년부터 환경 과징금 산정 기준을 변경해 집계했다.",
                          {"source": "ssot_text", "page": 38}, "footnote")
    result = object.__new__(HybridRAG).retrieve_for_items(
        [by_code("E-8-1")], corp=_corp([body, footnote]))[0]
    reachable = {d.meta.get("parent_chunk_id") for d, _ in result.hits}
    assert {"body", "footnote"} <= reachable, "표 본문과 각주가 다른 청크여도 후보에 함께 남는다"
    assert "body" in {d.meta.get("parent_chunk_id") for d, _ in result.accepted_hits}


def test_same_looking_metrics_with_different_year_or_region_are_kept_separate():
    docs = [
        IndexedDoc("2023년 국내 환경 법규 위반 3건, 환경 과징금 30만원", {"source": "ssot_text"}, "kr_2023"),
        IndexedDoc("2024년 국내 환경 법규 위반 1건, 환경 과징금 80만원", {"source": "ssot_text"}, "kr_2024"),
        IndexedDoc("2024년 해외 환경 법규 위반 0건, 환경 과징금 0원", {"source": "ssot_text"}, "overseas_2024"),
    ]
    result = object.__new__(HybridRAG).retrieve_for_items([by_code("E-8-1")], corp=_corp(docs))[0]
    accepted = [d for d, _ in result.accepted_hits]
    # 어느 것도 서로 합쳐지거나 한 값으로 대체되지 않는다. 각 원문과 위치가 보존된다.
    assert len({d.meta["parent_chunk_id"] for d in accepted}) == len(accepted)
    for doc in accepted:
        parent = next(d for d in docs if d.chunk_id == doc.meta["parent_chunk_id"])
        assert doc.text in parent.text


def test_company_name_number_format_and_clause_order_do_not_change_the_evidence():
    """같은 사실을 회사명·숫자 표기·어순만 달리 적어도 같은 근거로 채택된다."""
    variants = {
        "plain": "2024년 환경 법규 위반 1건, 환경 과징금 800000원",
        "company": "테스트기업은 2024년 환경 법규 위반 1건과 환경 과징금 800,000원을 공시했다.",
        "comma": "2024년 환경 과징금 80만원, 환경 법규 위반 1건",
        "reordered": "환경 과징금 800,000원과 환경 법규 위반 1건이 2024년에 발생했다.",
    }
    for label, text in variants.items():
        corp = _corp([IndexedDoc(text, {"source": "ssot_text"}, label)])
        result = object.__new__(HybridRAG).retrieve_for_items([by_code("E-8-1")], corp=corp)[0]
        assert [d.meta["parent_chunk_id"] for d, _ in result.accepted_hits] == [label], label


def test_unsupported_area_is_explicitly_unassessed():
    result = object.__new__(HybridRAG).retrieve_for_items([by_code("P-1-1")], corp=_corp([]))[0]
    assert result.decision.decision == "HUMAN"
    assert result.decision.hard_fails == ["R0_unsupported_area"]
    assert not result.accepted_hits


def test_generation_context_default_has_every_evidence_seen_by_grounding():
    def group(prefix):
        return [(IndexedDoc(f"근거 {i}", {}, f"{prefix}_{i}"), 1.0) for i in range(5)]

    context = RAGContext(group("rule"), group("industry"), group("company"))
    rendered = context.as_context_text()
    assert len(context.as_chunk_dicts()) == 15
    assert all(f"[{chunk['id']}]" in rendered for chunk in context.as_chunk_dicts())


def test_v2_prompt_does_not_authorize_extra_indicator_numbers(monkeypatch):
    from esgenie import layer2_rag

    prompts = []
    monkeypatch.setattr(layer2_rag.CLIENT, "complete", lambda system, user, **kwargs: (
        prompts.append(user) or SimpleNamespace(content="## 환경 성과\n### 지표 해설\n확인 필요", used_mock=True)
    ))
    report = SimpleNamespace(corp_name="테스트 기업", industry="제조업", report_year=2024,
                             to_context_dict=lambda: {})
    context = RAGContext([], [], [(IndexedDoc("교육 이수율 76.2%", {}, "education"), 1.0)])

    object.__new__(HybridRAG)._generate_section_v2(
        report, "E", "환경", context,
        covered=[{"code": "E-8-1", "name": "환경 법규 위반", "value": 1, "unit": "건", "status": "공시"}],
        missing=[], extra_instruction=None, demo_greenwash=False, system="",
    )

    assert "원장 밖 지표 수치를 추가하지 마시오" in prompts[0]
    assert "원장과 인용 청크에 없는 숫자" not in prompts[0]
    assert "[education]" in prompts[0]  # 자료를 숨기는 대신 공시 지표의 허용 범위를 명시한다.
