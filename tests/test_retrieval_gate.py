from __future__ import annotations

from esgenie.embeddings import IndexedDoc
from esgenie.rag_gates.retrieval_gate import evaluate_retrieval


def test_query_coverage_ignores_leading_company_token() -> None:
    doc = IndexedDoc(
        text="[DART/E-4-2] 재생에너지 사용 비율 (글로벌) 수치: 31.0 %",
        meta={"source": "dart_struct", "report_year": 2024},
        chunk_id="corp_1",
    )

    decision = evaluate_retrieval(
        "E",
        [(doc, 1.0)],
        query="삼성전자 재생에너지 사용 비율",
        tier=1,
        max_tier=2,
        bm25_hits=[(doc, 9.0)],
        embed_hits=[(doc, 0.9)],
    )

    assert decision.field_coverage["query"] is True
    assert decision.decision == "ACCEPT"


def test_environment_area_coverage_accepts_energy_metric() -> None:
    doc = IndexedDoc(
        text="[DART/E-4-1] 연간 총 에너지 사용량 수치: 355 TJ",
        meta={"source": "dart_struct", "report_year": 2024},
        chunk_id="corp_1",
    )

    decision = evaluate_retrieval(
        "E",
        [(doc, 1.0)],
        query="삼성전자 연간 총 에너지 사용량",
        tier=1,
        max_tier=2,
        bm25_hits=[(doc, 9.0)],
        embed_hits=[(doc, 0.9)],
    )

    assert decision.field_coverage["area"] is True
    assert decision.decision == "ACCEPT"


def test_unrelated_metric_keeps_query_hard_fail() -> None:
    doc = IndexedDoc(
        text="[DART/E-9-1] 친환경 인증 제품 매출 비율 (에너지스타·에코라벨 등) 수치: 28.3 %",
        meta={"source": "dart_struct", "report_year": 2024},
        chunk_id="corp_1",
    )

    decision = evaluate_retrieval(
        "E",
        [(doc, 1.0)],
        query="삼성전자 환경영향평가 인증 등급",
        tier=2,
        max_tier=2,
        bm25_hits=[(doc, 9.0)],
        embed_hits=[(doc, 0.9)],
    )

    assert "R3_query_keyword_missing" in decision.hard_fails
    assert decision.decision == "HUMAN"


def test_structured_top1_can_pass_with_soft_flags_only() -> None:
    top_doc = IndexedDoc(
        text="[DART/S-2-2] 정규직 비율 수치: 99.1 %",
        meta={"source": "dart_struct", "report_year": 2024},
        chunk_id="corp_top",
    )
    tail_doc = IndexedDoc(
        text="[DART/G-1-4] 여성 이사 비율 수치: 16.7 %",
        meta={"source": "dart_struct", "report_year": 2024},
        chunk_id="corp_tail",
    )
    embed_only_doc = IndexedDoc(
        text="[DART/E-4-2] 재생에너지 사용 비율 수치: 12.4 %",
        meta={"source": "dart_struct", "report_year": 2024},
        chunk_id="corp_embed",
    )

    decision = evaluate_retrieval(
        "S",
        [(top_doc, 1.0), (tail_doc, 0.95)],
        query="포스코 정규직 비율",
        tier=2,
        max_tier=2,
        bm25_hits=[(top_doc, 9.0), (tail_doc, 8.5)],
        embed_hits=[(embed_only_doc, 0.92)],
    )

    assert "R2_low_margin" in decision.soft_flags
    assert "R4_low_method_overlap" in decision.soft_flags
    assert decision.hard_fails == []
    assert decision.decision == "ACCEPT"


def test_numeric_coverage_uses_top1_not_trailing_hits() -> None:
    top_doc = IndexedDoc(
        text="[DART/S-5-1] 공급망 인권 실사 포함 수치: 협력사 ESG 실사·평가 시스템 운영 -",
        meta={"source": "dart_struct", "report_year": 2024},
        chunk_id="corp_top",
    )
    later_doc = IndexedDoc(
        text="[DART/S-8-2] 중대 개인정보 침해 건수 수치: 0 건",
        meta={"source": "dart_struct", "report_year": 2024},
        chunk_id="corp_later",
    )

    decision = evaluate_retrieval(
        "S",
        [(top_doc, 1.0), (later_doc, 0.97)],
        query="삼성전자 공급망 인권 실사 건수",
        tier=2,
        max_tier=2,
        bm25_hits=[(top_doc, 9.0), (later_doc, 8.7)],
        embed_hits=[(top_doc, 0.95), (later_doc, 0.9)],
    )

    assert "R3_numeric_evidence_missing" in decision.hard_fails
    assert decision.field_coverage["value"] is False
    assert decision.decision == "HUMAN"
