"""L0/L1 매칭 정합성 게이트(G1~G6) 회귀 테스트 — feature/l0-matching-integrity.

개편안 §2의 실측 8사례를 고정한다. 각 사례는 "이 metric_hint+unit+period가 들어오면
코드는 None(또는 projection)이어야 한다" 형태. 출처: 정답셋 라벨링(2026-07-18 완료) +
docs/라벨링_발견_수정목록_2026-07-19.md.

핵심 원칙(개편안 §4): 매칭 실패 시 노드를 폐기하지 않고 metric_hint로 보존, 코드만 None.
과차단(정상 항목까지 막힘) 방지를 위해 음성 테스트를 함께 둔다.
"""
from __future__ import annotations

import pytest

from esgenie.ssot.evidence_graph import (
    EvidenceGraph,
    _resolve_kesg_code,
    merge_ocr_extraction,
)
from esgenie.ssot.ocr_router import (
    DocChannel,
    ExtractedMetric,
    OcrExtraction,
    _is_footnote_marker_value,
)


def _m(hint: str, unit: str, *, value: float = 1.0, period: str = "2025",
       guess: str | None = None) -> ExtractedMetric:
    return ExtractedMetric(metric_hint=hint, value=value, unit=unit,
                           period=period, kesg_code_guess=guess)


# =====================================================================
# G1~G3 — _resolve_kesg_code 코드 확정 게이트 (실측 사례)
# =====================================================================

class TestResolveGates:
    """개편안 §2 사례 1·2·3·5·6·8을 _resolve_kesg_code 수준에서 고정."""

    def test_case1_guard_target_reduction(self):
        """사례1 모비스 E-3-1: '누적 온실가스 목표 감축량'(tCO2eq). 실제 3,560,074 →
        우리값 382,946은 목표 감축량. G1 가드('목표'·'감축량')로 코드 미부여."""
        assert _resolve_kesg_code(_m("누적 온실가스 목표 감축량", "tCO2eq", guess="E-3-1")) is None

    def test_case2_guard_conversion_partial(self):
        """사례2 모비스 E-4-1: '재생에너지 전환량'(TJ). 실제 총 9,075 → 우리값 1,005는
        전환량(부분값). G1 가드('전환량')로 코드 미부여."""
        assert _resolve_kesg_code(_m("재생에너지 전환량", "TJ", guess="E-4-1")) is None

    def test_case3_unit_person_vs_ton(self):
        """사례3 모비스 E-5-1: 용수 사용량에 임직원수(3,910 '명')가 유입. G3 단위 검증
        (ton 기대 vs 명)으로 코드 미부여."""
        assert _resolve_kesg_code(_m("용수 사용량", "명", guess="E-5-1")) is None

    def test_case5_unit_tco2_vs_ton(self):
        """사례5 네이버 E-5-1: 용수 표가 없는데 온실가스 배출량(tCO2eq)이 용수 코드로
        유입. G3 단위 검증(ton 기대 vs tCO2eq)으로 코드 미부여(→ 미공시가 오답보다 낫다)."""
        assert _resolve_kesg_code(_m("용수 사용량", "tCO2eq", guess="E-5-1")) is None

    def test_case6_water_reuse_rate_corrected_to_e52(self):
        """사례6 LG화학: '용수 재이용률'(%)이 용수 사용량(E-5-1, ton)으로 오매핑됐다.
        G2 최장 일치로 E-5-2(재사용 용수 비율)로 정정된다(차단이 아니라 올바른 코드)."""
        assert _resolve_kesg_code(_m("용수 재이용률", "%")) == "E-5-2"

    def test_case8_voluntary_turnover_resolves_normally(self):
        """사례8 모비스 S-2-3: '자발적 이직률'은 코드 자체는 S-2-3로 정상 매칭된다.
        오류(6.3 vs 실제 12.7)는 코드가 아니라 값 선택 문제라 이번 매칭 게이트 범위 밖.
        여기서는 '코드는 정상 부여됨'을 고정한다(과차단 없음 확인)."""
        assert _resolve_kesg_code(_m("자발적 이직률", "%", guess="S-2-3")) == "S-2-3"

    def test_llm_guess_not_trusted_blindly(self):
        """LLM 추정 코드도 G1·G3 검증을 거친다(무검증 통과 제거)."""
        # 가드 어휘가 있으면 LLM이 E-3-1을 줘도 기각
        assert _resolve_kesg_code(_m("온실가스 감축 목표", "tCO2eq", guess="E-3-1")) is None
        # 단위가 안 맞으면 LLM 추정도 기각
        assert _resolve_kesg_code(_m("용수 사용량", "%", guess="E-5-1")) is None


# =====================================================================
# G4 — 미래 기간 분리 (merge_ocr_extraction)
# =====================================================================

class TestFuturePeriodSeparation:
    def test_case4_projection_node_separated(self):
        """사례4 네이버 E-3-1: 2040 전망 곡선 숫자가 실적 노드로 등록됐다. G4로 report_year
        +2 이상은 '{code}__projection'으로 분리 → 실적 검색에서 제외."""
        g = EvidenceGraph("X", "테스트")
        ext = OcrExtraction(
            source_file="x.pdf", channel=DocChannel.UNSTRUCTURED, doc_type="policy",
            metrics=[_m("온실가스 배출량", "tCO2eq", value=100000.0, period="2040", guess="E-3-1")],
        )
        merge_ocr_extraction(g, ext, report_year=2025)
        metrics = [n.metric for n in g.nodes.values()]
        assert "E-3-1__projection" in metrics
        assert "E-3-1" not in metrics
        # 실적 코드 검색에서 제외되는지
        assert g.search_nodes(keywords=["E-3-1"]) == []

    def test_recent_evidence_not_projection(self):
        """report_year+1(최신 고지서 등)은 실적으로 인정 — 정상 증빙 과분류 방지."""
        g = EvidenceGraph("X", "테스트")
        ext = OcrExtraction(
            source_file="x.pdf", channel=DocChannel.STRUCTURED, doc_type="kepco_bill",
            metrics=[_m("사용전력량", "kWh", value=128400.0, period="2025-12", guess="E-4-1")],
        )
        merge_ocr_extraction(g, ext, report_year=2024)
        assert any(n.metric == "E-4-1" for n in g.nodes.values())
        assert not any(n.metric.endswith("__projection") for n in g.nodes.values())


# =====================================================================
# G5 — D1 노드 선택 '보고연도 최근접' (layer3_detect)
# =====================================================================

class TestD1NodeSelection:
    def test_nearest_report_year_not_latest(self):
        """여러 연도 노드 중 report_year 최근접을 골라야 한다(max(period) 아님).

        claim이 실적(2025)과 일치하는데, 과거 max 규칙은 미래 노드를 골라 오탐냈다."""
        from esgenie.layer3_detect import _score_d1_numeric
        g = EvidenceGraph("Y", "테스트")
        g.report_year = 2025
        from esgenie.ssot.evidence_graph import EvidenceNode
        for p, v in [(2023, 140.0), (2025, 151.0), (2030, 60.0)]:
            g.add_node(EvidenceNode(id=f"n{p}", metric="E-3-1", value=v, unit="tCO2eq",
                                    period=p, source="dart", origin="dart"))
        r = _score_d1_numeric("온실가스 배출량은 151 tCO2eq이다.", g)
        assert "node=151.0" in r.detail   # 2025 노드 선택 → Δ0


# =====================================================================
# G6 — 각주 마커 배제 (ocr_router)
# =====================================================================

class TestFootnoteMarker:
    def test_case7_footnote_marker_value_excluded(self):
        """사례7 삼성전기 S-4-2: '재해율 4)'의 각주번호 4를 값(4.0)으로 오파싱. 실제 0.033%.
        마커 번호와 값이 같으면 배제."""
        assert _is_footnote_marker_value("재해율 4)", 4.0) is True
        assert _is_footnote_marker_value("도수율6)", 6.0) is True

    def test_normal_value_with_footnote_kept(self):
        """마커가 붙어 있어도 값이 마커 번호와 다르면(정상 실측값) 유지."""
        assert _is_footnote_marker_value("재해율 4)", 0.033) is False
        assert _is_footnote_marker_value("여성 비율", 23.8) is False


# =====================================================================
# 과차단 방지 (음성 테스트) — 정상 항목은 여전히 매칭돼야 한다
# =====================================================================

class TestNoOverBlocking:
    # 사전 정상 키 → 기대 코드(대표 항목). 가드 어휘와 글자가 겹치는 케이스 포함.
    _NORMAL = [
        ("재생에너지 사용 비율", "%", "E-4-2"),
        ("재생에너지 사용·전환율", "%", "E-4-2"),   # '전환' 포함하나 가드는 '전환량'
        ("폐기물 재활용률", "%", "E-6-2"),          # '재활용' 정상 비율
        ("여성 임직원 비율", "%", "S-3-1"),
        ("온실가스 배출량", "tCO2eq", "E-3-1"),
        ("에너지 사용량", "TJ", "E-4-1"),
        ("자발적 이직률", "%", "S-2-3"),
    ]

    @pytest.mark.parametrize("hint,unit,expected", _NORMAL)
    def test_normal_items_still_resolve(self, hint, unit, expected):
        """정상 항목이 게이트에 막히지 않고 기대 코드로 매칭되는지(LLM 추정 동반)."""
        got = _resolve_kesg_code(_m(hint, unit, guess=expected))
        assert got == expected, f"{hint!r} 과차단 — {got} (기대 {expected})"

    def test_assignment_dictionary_has_single_source(self):
        """손관리 _HINT_TO_KESG 없이 kesg_items alias만 배정 출처로 쓴다."""
        from esgenie.ssot import evidence_graph

        assert not hasattr(evidence_graph, "_HINT_TO_KESG")
        cases = (
            ("사용전력량", "kWh", "E-4-1"),
            ("용수 재이용률", "%", "E-5-2"),
            ("폐기물 재활용률", "%", "E-6-2"),
            ("Scope 3 배출량", "tCO2eq", "E-3-2"),
        )
        for hint, unit, expected in cases:
            assert _resolve_kesg_code(_m(hint, unit)) == expected
