"""사전 통합 이후 대표값 자격·D6 3상태 회귀 테스트.

실측 입력은 outputs/lp7_*.json에서 최소 재현 형태로 고정했다. 코드 배정은 넓게
유지하되, 총량/구성요소/미래목표 역할이 대표값과 D6에서 구분되는지를 검증한다.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from esgenie.layer3_disclosure import detect_selective_disclosure
from esgenie.ssot.evidence_graph import (
    EvidenceGraph,
    EvidenceNode,
    _normalize_period,
    merge_ocr_extraction,
)
from esgenie.ssot.ocr_router import DocChannel, ExtractedMetric, OcrExtraction
from esgenie.ssot.node_select import (
    classify_common_value_role,
    classify_value_role,
    normalize_to_item_unit,
    select_representative_node,
)


def _node(code: str, value: float, unit: str, hint: str, *,
          period: int = 2024, node_id: str = "n") -> EvidenceNode:
    return EvidenceNode(
        id=node_id, metric=code, value=value, unit=unit, period=period,
        source="ocr/test", raw_text=f"{hint}={value}{unit} (report.pdf)",
        origin="ocr_unstructured", source_file="report.pdf",
    )


@pytest.mark.parametrize(("hint", "expected"), [
    ("원부자재 사용(구매)량 합계", "total"),
    ("재생 원자재 사용량 합계", "component"),
    ("비재생에너지 사용량 합계", "component"),
    ("지표수 취수량 합계 2024", "component"),
    ("수질오염물질 배출량 부유물질(SS) 2024", "component"),
    ("2040년 RE100 달성률", "target"),
])
def test_common_role_vocabulary_covers_observed_patterns(hint: str, expected: str) -> None:
    """공통 어휘가 코드별 사전 없이 실측 6유형 중 5유형과 총량을 가른다."""
    assert classify_common_value_role(hint, report_year=2025) == expected


def test_e3_scope_exception_prefers_scope1_plus_2_total() -> None:
    """유일한 코드별 예외: E-3-1에서 Scope 1/2 단독은 구성요소다."""
    assert classify_value_role("E-3-1", "Scope 1 배출량 합계", report_year=2025) == "component"
    assert classify_value_role("E-3-1", "Scope 2 배출량(지역 기반) 합계",
                               report_year=2025) == "component"
    assert classify_value_role("E-3-1", "총 온실가스 배출량 (Scope 1, 2)",
                               report_year=2025) == "total"
    assert classify_value_role("E-3-1", "Category 1 구매 제품 온실가스 배출량",
                               report_year=2025) == "component"


@pytest.mark.parametrize(("code", "expected", "bad"), [
    ("E-2-1",
     _node("E-2-1", 102_462, "톤", "원부자재 사용(구매)량 합계", node_id="z"),
     _node("E-2-1", 0, "톤", "재생 원자재 사용량 합계", node_id="a")),
    ("E-3-1",
     _node("E-3-1", 401_502, "tCO2eq", "온실가스 배출(Scope 1+2) 2024년 배출량", node_id="z"),
     _node("E-3-1", 371_059, "tCO2eq", "Scope 2 배출량(지역 기반) 합계", node_id="a")),
    ("E-4-1",
     _node("E-4-1", 7_929, "TJ", "전력 사용량", node_id="z"),
     _node("E-4-1", 8_070, "TJ", "비재생에너지 사용량 합계", node_id="a")),
    ("E-5-1",
     _node("E-5-1", 1_992_921, "ton", "용수 사용량(취수량) 합계 2024", node_id="z"),
     _node("E-5-1", 55_647, "ton", "지표수 취수량 합계 2024", node_id="a")),
])
def test_total_role_beats_component_even_when_component_id_is_first(
    code: str, expected: EvidenceNode, bad: EvidenceNode,
) -> None:
    picked = select_representative_node(code, [bad, expected], report_year=2025)
    assert picked is expected


def test_future_hint_year_is_projection_and_not_current_representative() -> None:
    assert _normalize_period("", fallback=2025, hint="2040년 RE100 달성률") == (2040, False)
    current = _node("E-4-2", 12.9, "%", "사업장 재생에너지 사용·전환률",
                    period=2024, node_id="z")
    future = _node("E-4-2", 100.0, "%", "2040년 RE100 달성률",
                   period=2025, node_id="a")
    assert select_representative_node(
        "E-4-2", [future, current], report_year=2025) is current

    graph = EvidenceGraph("X", "테스트")
    merge_ocr_extraction(graph, OcrExtraction(
        source_file="report.pdf", channel=DocChannel.UNSTRUCTURED,
        doc_type="esg_report", metrics=[ExtractedMetric(
            metric_hint="2040년 RE100 달성률", value=100.0, unit="%", period="",
            kesg_code_guess="E-4-2")],
    ), report_year=2025)
    projected = next(iter(graph.nodes.values()))
    assert projected.metric == "E-4-2__projection"
    assert projected.period == 2040
    assert projected.value_role == "target"


def test_water_m3_is_normalized_only_for_water_code() -> None:
    assert normalize_to_item_unit("E-5-1", 40_000_000, "m³") == (
        40_000_000, "ton", None)
    assert normalize_to_item_unit("E-6-1", 10, "m³")[2] == "unit_suspect"


def test_zero_value_guard_keeps_violation_zero() -> None:
    material_zero = _node("E-2-1", 0, "톤", "재생 원자재 사용량 합계")
    assert select_representative_node("E-2-1", [material_zero], report_year=2025) is None

    violation_zero = _node("E-8-1", 0, "건", "환경 법규 위반 0건")
    assert select_representative_node("E-8-1", [violation_zero], report_year=2025) is violation_zero


def _d6(role: str, factor: float = 0.5):
    ext = SimpleNamespace(
        mapped={"E-7-1": {"code": "E-7-1", "value_role": role}},
        missing=[], confidence_flags={},
    )
    return detect_selective_disclosure(ext, partial_weight_factor=factor)


def test_d6_distinguishes_total_partial_and_missing() -> None:
    total = _d6("total")
    partial = _d6("component")
    missing = detect_selective_disclosure(SimpleNamespace(
        mapped={}, missing=["E-7-1"], confidence_flags={}), partial_weight_factor=0.5)

    assert total.asymmetry["disclosure_states"]["total"] == 1
    assert partial.asymmetry["disclosure_states"]["partial"] == 1
    assert missing.asymmetry["disclosure_states"]["missing"] == 1
    assert total.score < partial.score < missing.score


def test_d6_partial_factor_sensitivity_is_monotonic() -> None:
    scores = [_d6("component", factor).score for factor in (0.3, 0.5, 0.7)]
    assert scores[0] < scores[1] < scores[2]


# ---- 단위 배율 접두 (2026-08-02) --------------------------------------------

def test_scale_prefix_with_space_is_converted():
    """'백만 원'처럼 배율과 단위 사이에 공백이 있어도 환산돼야 한다.

    `rag_gates.units._UNIT_ALIASES`에는 '백만원'(공백 없음)만 있어 OCR이 뽑는
    '백만 원'이 매칭에 실패했다. 그 결과 모비스 S-2-4 교육훈련비가
    21,116 백만 원 → 21,116원으로 실려 **10억 배 축소**됐다(unit_suspect만 붙고
    값은 그대로). 5개사 S·G에서 이 유형이 7건이다.
    """
    from esgenie.ssot.node_select import normalize_to_item_unit

    assert normalize_to_item_unit("S-2-4", 21_116.0, "백만 원") == (21_116_000_000.0, "원", None)
    assert normalize_to_item_unit("S-2-5", 425_674.0, "백만 원") == (425_674_000_000.0, "원", None)
    assert normalize_to_item_unit("S-2-4", 100.0, "억 원") == (10_000_000_000.0, "원", None)


def test_scale_prefix_does_not_break_plain_units():
    """음성 — 배율 없는 단위와 기존 환산은 그대로여야 한다(과통합 방지)."""
    from esgenie.ssot.node_select import normalize_to_item_unit

    assert normalize_to_item_unit("S-2-4", 100.0, "원") == (100.0, "원", None)
    assert normalize_to_item_unit("E-7-1", 210.68, "ton") == (210_680.0, "kg", None)
    assert normalize_to_item_unit("G-1-2", 50.0, "퍼센트") == (50.0, "%", None)


def test_unit_alias_keys_do_not_collide_when_spaces_stripped():
    """공백 제거가 서로 다른 별칭을 같은 키로 뭉개지 않는가 — 수정의 안전 근거."""
    import re
    from collections import defaultdict
    from esgenie.rag_gates.units import _UNIT_ALIASES

    by_key = defaultdict(set)
    for raw, canon in _UNIT_ALIASES.items():
        by_key[re.sub(r"\s+", "", raw)].add(canon)
    collisions = {k: v for k, v in by_key.items() if len(v) > 1}
    assert not collisions, f"공백 제거 시 별칭 충돌: {collisions}"
