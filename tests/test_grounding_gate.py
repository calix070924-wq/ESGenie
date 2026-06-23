from __future__ import annotations

from types import SimpleNamespace

from esgenie.embeddings import IndexedDoc, VectorIndex
from esgenie.layer2_rag import GenerationResult, RAGContext
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
        def retrieve_for_area(self, area: str, k: int = 5):
            return ctx

        def generate_section(self, report, area, extra_instruction=None, *, demo_greenwash=False, context=None):
            return GenerationResult(
                area=area,
                text="## 환경 성과\n\n검색 근거가 부족하여 자동 생성하지 않았습니다.",
                context=context or ctx,
                used_mock_llm=True,
            )

    result = verify_and_refine(
        report=SimpleNamespace(corp_name="테스트", industry="전자", report_year=2024),
        area="E",
        rag=FakeRAG(),
    )

    assert result.hitl_required is True
    assert result.converged is False
    assert result.iterations_used == 0
    assert result.final_score == 100.0
    assert result.metadata["retrieval_decision"]["decision"] == "HUMAN"
