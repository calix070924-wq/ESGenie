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


def test_area_coverage_uses_search_terms_source() -> None:
    """영역 어휘가 하드코딩이 아니라 kesg_items.search_terms에서 파생되는지.

    '탄소중립 로드맵 … 넷제로, RE100'은 명백한 E 내용인데 구 하드코딩 어휘
    (온실가스·배출·재생에너지·폐기물·용수·환경·에너지·scope)에는 해당 단어가
    없어 R3_area_keyword_missing으로 오차단됐다(005930 E 실측).
    """
    doc = IndexedDoc(
        text="[DART/E-1-1] 중장기 탄소중립 로드맵 이사회 승인 수치: 2050 넷제로, 2027 DX부문 RE100",
        meta={"source": "dart_struct", "code": "E-1-1", "report_year": 2024},
        chunk_id="corp_1",
    )

    decision = evaluate_retrieval(
        "E",
        [(doc, 1.0)],
        query="삼성전자 탄소중립 로드맵",
        tier=1,
        max_tier=2,
        bm25_hits=[(doc, 9.0)],
        embed_hits=[(doc, 0.9)],
    )

    assert decision.field_coverage["area"] is True
    assert "R3_area_keyword_missing" not in decision.hard_fails


def test_area_coverage_still_rejects_other_area_chunk() -> None:
    """어휘 확장이 영역 변별력을 없애지 않는지 — G 청크는 E에서 여전히 불일치."""
    doc = IndexedDoc(
        text="[DART/G-2-1] 이사회 출석률 수치: 98.0 %",
        meta={"source": "dart_struct", "code": "G-2-1", "report_year": 2024},
        chunk_id="corp_1",
    )

    decision = evaluate_retrieval(
        "E",
        [(doc, 1.0)],
        query="삼성전자 온실가스 배출량",
        tier=1,
        max_tier=2,
        bm25_hits=[(doc, 9.0)],
        embed_hits=[(doc, 0.9)],
    )

    assert decision.field_coverage["area"] is False
    assert "R3_area_keyword_missing" in decision.hard_fails


def test_qualitative_item_exempt_from_numeric_requirement() -> None:
    """단위 없는 정성 항목에 수치 증빙을 요구하면 구조적으로 통과 불가(005380 E·G 실측).

    field_coverage["value"]는 사실대로 False로 남기고 hard fail만 면제한다.
    """
    doc = IndexedDoc(
        text="[DART/E-1-2] C레벨 ESG위원회 분기 보고 수치: 그룹 환경안전본부 + 사업장별 환경위원회",
        meta={"source": "dart_struct", "code": "E-1-2", "report_year": 2024},
        chunk_id="corp_1",
    )

    decision = evaluate_retrieval(
        "E",
        [(doc, 1.0)],
        query="현대자동차 환경경영 추진체계",
        tier=1,
        max_tier=2,
        bm25_hits=[(doc, 9.0)],
        embed_hits=[(doc, 0.9)],
    )

    assert decision.field_coverage["value"] is False
    assert "R3_numeric_evidence_missing" not in decision.hard_fails


def test_quantitative_item_still_requires_numeric() -> None:
    """정량 항목(단위 있음)은 수치 증빙 요구가 그대로 유지되는지."""
    doc = IndexedDoc(
        text="[DART/E-6-1] 폐기물 배출량 집계 예정",
        meta={"source": "dart_struct", "code": "E-6-1", "report_year": 2024},
        chunk_id="corp_1",
    )

    decision = evaluate_retrieval(
        "E",
        [(doc, 1.0)],
        query="폐기물 배출량",
        tier=1,
        max_tier=2,
        bm25_hits=[(doc, 9.0)],
        embed_hits=[(doc, 0.9)],
    )

    assert "R3_numeric_evidence_missing" in decision.hard_fails
