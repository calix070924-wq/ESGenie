from __future__ import annotations

from types import SimpleNamespace

from esgenie.embeddings import BM25Index, IndexedDoc, VectorIndex
from esgenie.layer2_rag import CorpIndex, GenerationResult, RAGContext
from esgenie.layer3_detect import DetectionResult
from esgenie.layer4_verify import VerificationResult, VerificationStep
from esgenie.layer5_audit_trace import build_audit_trace
from esgenie.rag_gates import evaluate_grounding, evaluate_retrieval
from esgenie.schemas import RetrievalDecision


def test_vector_index_assigns_chunk_ids_and_meta_ids() -> None:
    docs = [
        IndexedDoc(text="alpha", meta={"source": "dart_raw", "corp_code": "005930"}),
        IndexedDoc(text="beta", meta={"source": "dart_raw", "corp_code": "005930"}),
    ]
    index = VectorIndex()
    index.build(docs)

    assert docs[0].chunk_id.startswith("dart_raw_005930")
    assert docs[1].chunk_id.startswith("dart_raw_005930")
    assert docs[0].chunk_id != docs[1].chunk_id
    assert docs[0].meta["id"] == docs[0].chunk_id
    assert docs[1].meta["id"] == docs[1].chunk_id


def test_grounding_gate_accepts_cited_supported_numbers() -> None:
    answer = (
        "온실가스 배출량은 120입니다 [corp_1]\n"
        "재생에너지 비율은 31입니다 [corp_2]"
    )
    cited_chunks = [
        {"id": "corp_1", "text": "온실가스 배출량 120"},
        {"id": "corp_2", "text": "재생에너지 비율 31"},
    ]

    result = evaluate_grounding(answer, cited_chunks)

    assert result.decision == "ACCEPT"
    assert result.g1_uncited_sentences == []
    assert result.g2_orphan_numbers == []
    assert result.faithfulness == 1.0


def test_grounding_gate_flags_uncited_and_orphan_numbers() -> None:
    answer = (
        "온실가스 배출량은 120입니다\n"
        "재생에너지 비율은 31입니다 [corp_2]"
    )
    cited_chunks = [
        {"id": "corp_2", "text": "재생에너지 비율 30"},
    ]

    result = evaluate_grounding(answer, cited_chunks)

    assert result.decision == "ESCALATE"
    assert "온실가스 배출량은 120입니다" in result.g1_uncited_sentences
    assert result.g2_orphan_numbers == ["31"]
    assert "G1_uncited_claims" in result.hard_fails
    assert "G2_orphan_numbers" in result.hard_fails


def test_audit_trace_prefers_explicit_citations() -> None:
    cited_doc = IndexedDoc(text="온실가스 배출량 120", meta={"id": "corp_1"}, chunk_id="corp_1")
    distractor = IndexedDoc(text="온실가스 배출량 999", meta={"id": "corp_2"}, chunk_id="corp_2")
    context = RAGContext(kesg_hits=[], industry_hits=[], corp_hits=[(cited_doc, 0.91), (distractor, 0.55)])
    generation = GenerationResult(
        area="E",
        text="온실가스 배출량은 120입니다 [corp_1]",
        context=context,
        used_mock_llm=True,
    )
    detection = DetectionResult(
        text="온실가스 배출량은 120입니다",
        sentences=["온실가스 배출량은 120입니다"],
        numeric_claims=[],
        claim_checks=[],
        vague_phrases=[],
        semantic_similarity=1.0,
        risk_score=0.0,
    )
    step = VerificationStep(iteration=0, generation=generation, detection=detection, grounding=None, instruction="")
    verification = VerificationResult(area="E", steps=[step], final=step, converged=True)
    extraction = SimpleNamespace(mapped={"E-3-1": {"name": "온실가스 배출량", "note": "온실가스 배출량"}})
    report = SimpleNamespace(corp_code="005930", corp_name="삼성전자", report_year=2024)

    trace = build_audit_trace(
        report=report,
        area="E",
        verification=verification,
        extraction=extraction,
        evidence_graph=None,
        industry_stats=None,
        llm_judge=False,
    )

    assert len(trace.sentences) == 1
    assert trace.sentences[0].sentence_text == "온실가스 배출량은 120입니다"
    assert trace.sentences[0].retrieved_chunk_ids == ["corp_1"]
    assert trace.sentences[0].grounding_status == "grounded"
    assert trace.sentences[0].retrieval_scores == [0.91]
    assert verification.final_text == "온실가스 배출량은 120입니다"


def test_audit_trace_builds_d3_index_once_for_all_sentences(monkeypatch) -> None:
    """같은 RAG 청크를 문장마다 다시 임베딩하지 않는다."""
    import esgenie.embeddings as embeddings

    monkeypatch.setattr(embeddings, "_get_st_model", lambda _name: None)
    monkeypatch.setattr(embeddings, "_get_faiss", lambda: None)

    build_calls = 0
    original_build = embeddings.VectorIndex.build

    def counted_build(index, docs):
        nonlocal build_calls
        build_calls += 1
        return original_build(index, docs)

    monkeypatch.setattr(embeddings.VectorIndex, "build", counted_build)

    docs = [
        IndexedDoc(text="온실가스 배출량 120", meta={"id": "corp_1"}, chunk_id="corp_1"),
        IndexedDoc(text="재생에너지 비율 31", meta={"id": "corp_2"}, chunk_id="corp_2"),
    ]
    context = RAGContext(
        kesg_hits=[], industry_hits=[], corp_hits=[(docs[0], 0.91), (docs[1], 0.82)])
    generation = GenerationResult(
        area="E",
        text="온실가스 배출량은 120톤입니다.\n재생에너지 비율은 31%입니다.",
        context=context,
        used_mock_llm=True,
    )
    detection = DetectionResult(
        text=generation.text,
        sentences=[],
        numeric_claims=[],
        claim_checks=[],
        vague_phrases=[],
        semantic_similarity=1.0,
        risk_score=0.0,
    )
    step = VerificationStep(
        iteration=0, generation=generation, detection=detection,
        grounding=None, instruction="",
    )
    verification = VerificationResult(area="E", steps=[step], final=step, converged=True)
    extraction = SimpleNamespace(mapped={
        "E-3-1": {"name": "온실가스 배출량", "note": "온실가스 배출량"},
        "E-4-2": {"name": "재생에너지 비율", "note": "재생에너지 비율"},
    })
    report = SimpleNamespace(corp_code="TEST", corp_name="테스트", report_year=2024)

    trace = build_audit_trace(report, "E", verification, extraction)

    assert len(trace.sentences) == 2
    assert all(sentence.risk_vector is not None for sentence in trace.sentences)
    assert build_calls == 1


def test_retrieval_gate_accepts_supported_corp_hits() -> None:
    corp_hits = [
        (IndexedDoc(text="온실가스 배출량 120 tCO2eq 2024년", meta={"source": "dart_raw"}, chunk_id="corp_1"), 0.91),
        (IndexedDoc(text="재생에너지 사용 비율 31% 2024년", meta={"source": "dart_raw"}, chunk_id="corp_2"), 0.55),
    ]

    decision = evaluate_retrieval("E", corp_hits)

    assert decision.decision == "ACCEPT"
    assert decision.top1_score == 0.91
    assert decision.field_coverage["area"] is True
    assert decision.field_coverage["value"] is True
    assert decision.chunk_ids == ["corp_1", "corp_2"]


def test_retrieval_gate_returns_escalate_on_low_score_before_last_tier() -> None:
    corp_hits = [
        (IndexedDoc(text="환경 활동 서술", meta={"source": "dart_raw"}, chunk_id="corp_1"), 0.01),
    ]

    decision = evaluate_retrieval("E", corp_hits)

    assert decision.decision == "ESCALATE"
    assert "R1_low_top1_score" in decision.hard_fails
    assert "R3_numeric_evidence_missing" in decision.hard_fails


def test_retrieval_gate_blocks_query_keyword_mismatch() -> None:
    corp_hits = [
        (IndexedDoc(text="온실가스 배출량 120 tCO2eq 2024년", meta={"source": "dart_raw"}, chunk_id="corp_1"), 0.9),
    ]

    decision = evaluate_retrieval("E", corp_hits, query="환경영향평가 인증 등급")

    assert "R3_query_keyword_missing" in decision.hard_fails
    assert decision.decision == "ESCALATE"


def test_verify_and_refine_short_circuits_when_retrieval_gate_blocks() -> None:
    from esgenie.layer4_verify import verify_and_refine

    blocked = RetrievalDecision(
        decision="HUMAN",
        tier=0,
        top1_score=0.01,
        field_coverage={"area": False, "value": False, "period": False, "source": True},
        hard_fails=["R1_low_top1_score", "R3_area_keyword_missing"],
        soft_flags=[],
        chunk_ids=[],
        scores=[],
    )
    ctx = RAGContext(kesg_hits=[], industry_hits=[], corp_hits=[], retrieval_tier=0, retrieval_decision=blocked)

    class FakeRAG:
        def retrieve_for_area(self, area: str, k: int = 5, *, corp: CorpIndex):
            return ctx

        def generate_section(self, report, area, extra_instruction=None, *, demo_greenwash=False, context=None, corp: CorpIndex, extraction=None):
            return GenerationResult(
                area=area,
                text="## 환경 성과\n\n검색 근거가 부족하여 자동 생성하지 않았습니다.",
                context=context or ctx,
                used_mock_llm=True,
            )

    dummy_corp = CorpIndex(vector=VectorIndex(), bm25=BM25Index())
    result = verify_and_refine(
        report=SimpleNamespace(corp_name="테스트", industry="전자", report_year=2024),
        area="E",
        rag=FakeRAG(),
        corp=dummy_corp,
    )

    assert result.hitl_required is True
    assert result.converged is False
    assert result.iterations_used == 0
    assert result.final_score is None
    assert result.final_band == "평가불가"
    assert result.final.detection.risk_vector.aggregate["evaluation_status"] == "unavailable"
    assert result.metadata["retrieval_decision"]["decision"] == "HUMAN"


# ---- 한국어 자릿수 복합 표기 (2026-09-20 실측) ---------------------------------

def test_korean_scale_composite_is_one_number_not_two() -> None:
    """'211억 1,600만 원'은 금액 하나다. 211과 1600으로 쪼개면 근거 대조가 깨진다."""
    from esgenie.rag_gates.signals import extract_numbers

    assert extract_numbers("교육훈련비는 211억 1,600만 원이다") == ["211억 1600만"]


def test_korean_scale_composite_matches_the_plain_ledger_value() -> None:
    from esgenie.rag_gates.signals import number_in_text

    assert number_in_text("211억 1,600만", "교육훈련비=21,116,000,000원") is True


def test_korean_scale_composite_still_fails_on_a_wrong_value() -> None:
    """합치는 계산은 정확한 곱셈·덧셈이므로 틀린 금액은 그대로 걸린다."""
    from esgenie.rag_gates.signals import number_in_text

    assert number_in_text("211억 1,600만", "교육훈련비=2,111,600,000원") is False


def test_plain_number_sequence_is_not_merged() -> None:
    """자릿수 글자가 없는 숫자 나열은 종전처럼 각각 유지된다."""
    from esgenie.rag_gates.signals import extract_numbers

    assert extract_numbers("신규 채용 2,774명, 이사회 7회, 이직률 22.9%") == ["2774", "7", "22.9"]


def test_grounding_gate_accepts_korean_scale_amount_backed_by_evidence() -> None:
    answer = "교육훈련비는 211억 1,600만 원이다. [c1]"
    chunks = [{"id": "c1", "text": "교육훈련비 21,116,000,000원 (2024)"}]

    result = evaluate_grounding(answer, chunks)

    assert result.g2_orphan_numbers == []


# ---- 원장의 축약 금액 표기 대조 (2026-09-26 실측) -------------------------------

def test_ledger_abbreviated_amount_matches_the_korean_scale_notation() -> None:
    """원장이 '21116.0백만 원'으로 축약한 금액을 본문이 '211억 1,600만 원'으로 적는다.

    자릿수 수정을 촉발한 사례다(현대모비스 S-2-4 교육훈련비). 종전에는 근거 쪽 숫자만
    읽고 배율 단위를 무시해 21116과 21,116,000,000을 다른 값으로 봤고, 같은 금액인데
    G2 미확인 숫자로 보고됐다. 청크 문구는 `ssot_pipeline`이 실제로 만드는 형태다.
    """
    answer = "2024년 임직원 교육훈련비는 211억 1,600만 원입니다. [c1]"
    chunks = [{"id": "c1", "text": "[S-2-4] 21116.0백만 원 (2024년, 출처: mobis.pdf, 신뢰도: 0.75)"}]

    result = evaluate_grounding(answer, chunks)

    assert result.g2_orphan_numbers == []
    assert result.decision == "ACCEPT"


def test_ledger_abbreviated_amount_still_accepts_the_same_notation() -> None:
    """본문이 근거와 같은 축약 표기를 쓰는 기존 동작을 잃지 않는다."""
    answer = "2024년 임직원 교육훈련비는 21,116백만 원입니다. [c1]"
    chunks = [{"id": "c1", "text": "[S-2-4] 21116.0백만 원 (2024년)"}]

    assert evaluate_grounding(answer, chunks).g2_orphan_numbers == []


def test_ledger_abbreviated_amount_still_fails_on_a_wrong_amount() -> None:
    """배율을 반영해도 틀린 금액은 걸린다 — 느슨해지기만 한 것이 아니다."""
    from esgenie.rag_gates.signals import number_in_text

    assert number_in_text("211억 1,600만", "[S-2-4] 2111.6백만 원") is False


def test_scale_word_without_a_currency_is_not_read_as_a_multiplier() -> None:
    """통화 단위가 없으면 배율로 읽지 않는다 — '2,100 만 명'은 21,000,000이 아니다."""
    from esgenie.rag_gates.signals import number_in_text

    assert number_in_text("21000000", "누적 회원 2,100 만 명을 확보했다") is False


# ---- '조'는 자릿수이기도 하고 조항 번호이기도 하다 (2026-09-23 실측) -------------

def test_article_number_is_not_read_as_trillions() -> None:
    """'제26조'는 조항 번호다. 26조 원으로 읽으면 근거에서 찾을 수 없는 숫자가 된다.

    9/20 전량 추출물 실측: 조항문 647건 중 9건, 최종 보고서 문장 484건 중 12건이
    법·정관 조항 번호를 담고 있었다. 공급망 드래프터는 G2를 hard_fail로 다루므로
    (drafter.py) 이 오탐 하나가 정상 초안 폐기로 이어진다.
    """
    from esgenie.rag_gates.signals import extract_numbers

    assert extract_numbers("산업안전보건법 제26조에 따라 위원회를 설치한다") == ["26"]
    assert extract_numbers("상법 제388조 및 정관에 의거하여") == ["388"]
    assert extract_numbers("제6조(협력사 ESG 경영 및 공정거래 준수)") == ["6"]


def test_article_number_without_je_prefix_is_not_read_as_trillions() -> None:
    """'제'가 없어도 통화 단위가 뒤에 없으면 조항 번호로 본다(놓치는 쪽이 덜 나쁘다)."""
    from esgenie.rag_gates.signals import extract_numbers

    assert extract_numbers("환경경영 정책 2조 '기본원칙'의 사항") == ["2"]


def test_korean_trillion_amount_is_still_one_number() -> None:
    """통화 단위가 붙거나 더 작은 자릿수가 이어지면 금액으로 읽는다."""
    from esgenie.rag_gates.signals import extract_numbers, number_in_text

    assert extract_numbers("총 2.6조 원 규모의 주주환원을 시행하였습니다") == ["2.6조"]
    assert extract_numbers("매출 1조5000억 원을 기록했다") == ["1조5000억"]
    assert number_in_text("2.6조", "주주환원 2,600,000,000,000원") is True


def test_scale_letter_split_by_whitespace_is_not_merged() -> None:
    """표·줄바꿈이 끼어든 '114\\n조'는 한 금액이 아니다."""
    from esgenie.rag_gates.signals import extract_numbers

    assert extract_numbers("표 값 114\n조 원") == ["114"]
