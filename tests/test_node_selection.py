"""L1 대표 노드 선택 규칙 회귀 — feature/l1-node-selection.

근거 문서
---------
  docs/작업0b_hint분석_선택규칙설계_2026-07-25.md   §5 선택 규칙 설계
  docs/집계어휘_실태_2026-07-26.md                  hint 전수 조사(어휘 확정 근거)
  docs/라벨링_발견_수정목록_2026-07-19.md §3-1       "잘못된 값보다 미공시가 낫다"

원장 값이 틀린 원인은 오염이 아니라 **선택 실패**였다. 정답 노드가 같은 코드·같은 풀에
나란히 있는데 파생값·부분값을 골랐다. 선택 규칙이 연도 하나뿐이라 같은 연도 안에서는
사실상 임의 선택이었기 때문이다.

이 파일은 세 가지를 고정한다.
  1) 실측 4사례 — 현대모비스(012330) 2025 ESG보고서 hint 원문 그대로
  2) 원장·D1 대칭 — 두 경로가 같은 노드를 고르는가 (가장 중요)
  3) 과차단 방지 음성 테스트 + 미공시 폴백

실측 hint는 outputs/ledger_provenance_012330_v2.json에서 그대로 옮겼다(라이브 재실행 없음).
"""
from __future__ import annotations

from esgenie.dart_client import CompanyReport
from esgenie.ssot.evidence_graph import EvidenceGraph, EvidenceNode
from esgenie.ssot.node_select import (
    normalize_to_item_unit,
    select_representative_node,
)
from esgenie.ssot.ssot_pipeline import extract_with_ssot

REPORT_YEAR = 2025


# =====================================================================
# 헬퍼
# =====================================================================

def _n(code: str, value: float, unit: str, period: int, hint: str) -> EvidenceNode:
    """실측 노드 재현 — hint는 merge_ocr_extraction과 같은 raw_text 형식으로 넣는다.

    EvidenceNode는 hint 필드를 따로 갖지 않고 raw_text에
    `"{metric_hint}={value}{unit} ({source_file})"`로 담는다. 선택 규칙이 실제
    파이프라인과 같은 경로로 hint를 읽는지까지 이 헬퍼가 검증한다.
    """
    return EvidenceNode(
        id=f"00164788_{code}_{period}__ocr_unstructured__{abs(hash(hint + str(value))) % 10**10:010d}",
        metric=code, value=value, unit=unit, period=period,
        source="ocr/esg_report",
        raw_text=f"{hint}={value}{unit} (012330_mobis_2025.pdf)",
        origin="ocr_unstructured", source_file="012330_mobis_2025.pdf",
        confidence=1.0,
    )


def _graph(*nodes: EvidenceNode, report_year: int | None = REPORT_YEAR) -> EvidenceGraph:
    g = EvidenceGraph(corp_code="00164788", corp_name="현대모비스(주)")
    g.report_year = report_year
    for node in nodes:
        g.add_node(node)
    return g


def _empty_report(report_year: int = REPORT_YEAR) -> CompanyReport:
    """DART 미공시 상태 — OCR 노드만으로 원장이 채워지는 경로."""
    return CompanyReport(
        corp_code="00164788", corp_name="현대모비스(주)", industry="",
        report_year=report_year, financials={}, kesg_data={},
        raw_text_snippets=[], source="test",
    )


def _ledger_pick(*nodes: EvidenceNode) -> dict:
    """원장 경로(_merge_ssot_evidence)가 만든 항목 entry."""
    code = nodes[0].metric
    result = extract_with_ssot(_empty_report(), _graph(*nodes))
    return result.mapped.get(code, {})


# =====================================================================
# 1. 실측 4사례 고정
# =====================================================================

class TestRealWorldCases:
    """현대모비스 2025 ESG보고서 E영역 — 원장이 실제로 틀렸던 4건."""

    def test_e3_1_picks_scope1_plus_2_total_not_reduction_effect(self) -> None:
        """E-3-1: '온실가스 감축 효과' 1,161,214를 배출량으로 올렸다 → Scope1+2 합계.

        같은 풀에 Scope 3·(1+2+3) 노드도 섞여 있어 값만 보면 3,560,074를 정답으로
        오독하게 된다(설계 문서 §1의 자기정정). hint 기준으로 골라야 396,152가 나온다.
        Scope 2 이중보고에서는 지역 기반(location-based)이 GHG Protocol 필수 기준이다.
        """
        pool = [
            _n("E-3-1", 1_161_214.0, "tCO2eq", 2025, "온실가스 감축 효과"),
            _n("E-3-1", 1_161_214.0, "tCO2eq", 2024,
               "2024년 친환경차 부품 적용에 따른 온실가스 감축 효과"),
            _n("E-3-1", 3_560_074.0, "tCO2 eq", 2024,
               "총 온실가스 배출량 (Scope 1+2+3) 지역기반"),
            _n("E-3-1", 3_136_024.0, "tCO2 eq", 2024, "Scope 3 온실가스 배출량 연결(일부)"),
            _n("E-3-1", 396_152.0, "tCO2 eq", 2025,
               "온실가스 배출량 (Scope 1 + 지역 기반 Scope 2) 합계"),
            _n("E-3-1", 389_933.0, "tCO2 eq", 2025,
               "온실가스 배출량 (Scope 1 + 시장 기반 Scope 2) 합계"),
            _n("E-3-1", 371_059.0, "tCO2eq", 2024, "Scope 2 배출량 합계"),
            _n("E-3-1", 86_468.0, "tCO2 eq", 2025, "Scope 2 배출량(시장 기반) 국내(별도)"),
        ]
        picked = select_representative_node("E-3-1", pool, report_year=REPORT_YEAR)
        assert picked is not None
        assert picked.value == 396_152.0

    def test_e5_1_picks_total_not_domestic_separate(self) -> None:
        """E-5-1: '국내(별도)' 623,648을 골랐다 → '합계' 1,992,921.

        '용수 재활용·재사용량 합계'도 '합계' 어휘를 갖지만 취수량이 아니라
        E-5-1 negative keyword('재활용'·'재사용')로 배제된다.
        """
        pool = [
            _n("E-5-1", 1_992_921.0, "ton", 2024, "용수 사용량(취수량) 합계 2024년"),
            _n("E-5-1", 1_693_098.0, "ton", 2023, "용수 사용량(취수량) 합계 2023년"),
            _n("E-5-1", 813_375.0, "ton", 2024, "용수 사용량(취수량) 해외 자회사 2024년"),
            _n("E-5-1", 623_648.0, "ton", 2024, "용수 사용량(취수량) 국내(별도) 2024년"),
            _n("E-5-1", 147_439.0, "ton", 2024, "물 위험/스트레스 지역에서의 용수 소비량 합계"),
            _n("E-5-1", 114_884.0, "ton", 2024, "용수 재활용·재사용량 합계"),
        ]
        picked = select_representative_node("E-5-1", pool, report_year=REPORT_YEAR)
        assert picked is not None
        assert picked.value == 1_992_921.0

    def test_e6_1_picks_generation_total_not_disposal_partial(self) -> None:
        """E-6-1: '폐기물 처리량… 국내(별도)' 1,693을 골랐다 → 발생량 총량 72,463.

        항목 정의가 '연간 폐기물 배출량(총량)'이므로 발생량이 정답이다(2026-07-26 확정).
        처리량은 처분 경로별 부분값이다 — 실측이 이를 뒷받침한다:
          처리량(매립·소각) 합계 17,694 + 미폐기 처리량(재활용·재사용) 합계 52,806
          ≈ 발생량 72,463
        미폐기 처리량은 E-6-1 negative keyword('재활용'·'재사용')로도 배제된다.
        """
        pool = [
            _n("E-6-1", 72_463.0, "ton", 2024, "폐기물 발생량"),
            _n("E-6-1", 57_719.0, "ton", 2024, "일반 폐기물 발생량"),
            _n("E-6-1", 52_806.0, "ton", 2025, "폐기물 미폐기 처리량(재활용, 재사용) 합계"),
            _n("E-6-1", 19_352.0, "ton", 2025, "폐기물 처리량(매립, 소각 등) 합계"),
            _n("E-6-1", 17_694.0, "ton", 2025, "폐기물 처리량(매립, 소각 등) 합계"),
            _n("E-6-1", 13_072.0, "ton", 2025, "일반 폐기물 합계"),
            _n("E-6-1", 1_693.0, "ton", 2025, "폐기물 처리량(매립, 소각 등) 국내(별도)"),
        ]
        picked = select_representative_node("E-6-1", pool, report_year=REPORT_YEAR)
        assert picked is not None
        assert picked.value == 72_463.0

    def test_e4_1_prefers_item_unit_tj_over_mwh_breakdown(self) -> None:
        """E-4-1: 'PPA' 4,654 MWh를 골랐다 → '전력 사용량' 7,497 TJ.

        E-4-1은 24개 노드 중 '합계' 어휘가 **0개**다(집계어휘 실태 §코드별 비율).
        집계 축만으로는 안 갈리므로 세부 분해 축(조달방식별 PPA/vPPA/녹색요금제)과
        단위 정합 축(항목 단위 TJ)이 함께 작동해야 한다.
        """
        pool = [
            _n("E-4-1", 827_967.0, "MWh", 2024, "전력 사용량"),
            _n("E-4-1", 721_222.0, "MWh", 2024, "구매한 전력량"),
            _n("E-4-1", 106_745.0, "MWh", 2024, "재생 전력"),
            _n("E-4-1", 7_497.0, "TJ", 2023, "전력 사용량"),
            _n("E-4-1", 4_654.0, "MWh", 2025, "전력구매계약(On-site PPA)"),
            _n("E-4-1", 2_841.0, "MWh", 2025, "녹색요금제(녹색전력상품)"),
            _n("E-4-1", 431.0, "TJ", 2024, "2023년 대비 에너지 사용량 증감"),
            _n("E-4-1", 390.0, "TJ", 2025, "전력구매계약(On-site PPA)"),
        ]
        picked = select_representative_node("E-4-1", pool, report_year=REPORT_YEAR)
        assert picked is not None
        assert picked.value == 7_497.0
        assert picked.unit == "TJ"


# =====================================================================
# 2. 원장 · D1 대칭  ★ 이번 작업의 핵심 산출물
# =====================================================================

class TestLedgerD1Symmetry:
    """원장과 D1이 같은 노드를 골라야 한다. 어긋나면 데이터가 옳아도 D1이 발화한다."""

    def test_both_paths_pick_same_node_on_real_pool(self) -> None:
        """실측 E-5-1 풀 — 원장 표시값과 D1 비교 대상이 같은 노드인가."""
        pool = [
            _n("E-5-1", 1_992_921.0, "ton", 2024, "용수 사용량(취수량) 합계 2024년"),
            _n("E-5-1", 623_648.0, "ton", 2024, "용수 사용량(취수량) 국내(별도) 2024년"),
            _n("E-5-1", 114_884.0, "ton", 2024, "용수 재활용·재사용량 합계"),
        ]
        graph = _graph(*pool)

        # 원장 경로 — _merge_ssot_evidence
        ledger_value = extract_with_ssot(_empty_report(), graph).mapped["E-5-1"]["value"]

        # D1 경로 — _score_d1_numeric의 G5가 호출하는 것과 동일한 공용 함수
        d1_node = select_representative_node(
            "E-5-1", graph.search_nodes(keywords=["E-5-1"]), report_year=REPORT_YEAR)

        assert d1_node is not None
        assert ledger_value == d1_node.value, "원장·D1이 같은 노드를 골라야 한다"

    def test_d1_does_not_fire_when_claim_matches_ledger(self) -> None:
        """구조적 D1 오탐 회귀 — 원장 값을 그대로 주장하면 D1이 0이어야 한다.

        선택 규칙이 비대칭이던 시절엔 원장이 '합계'를, D1이 다른 노드를 골라
        같은 값을 말해도 오차가 났다.
        """
        from esgenie.layer3_detect import _score_d1_numeric

        pool = [
            _n("E-5-1", 1_992_921.0, "ton", 2024, "용수 사용량(취수량) 합계 2024년"),
            _n("E-5-1", 623_648.0, "ton", 2024, "용수 사용량(취수량) 국내(별도) 2024년"),
        ]
        graph = _graph(*pool)
        ledger_value = extract_with_ssot(_empty_report(), graph).mapped["E-5-1"]["value"]

        d1 = _score_d1_numeric(f"용수 사용량은 {ledger_value:,.0f} 톤이다.", graph)
        assert d1.score == 0.0, f"원장 값과 일치하는 주장에 D1이 발화했다 — {d1.detail}"

    def test_symmetry_holds_when_derived_node_is_newest(self) -> None:
        """파생 노드가 최신 연도라도 양쪽이 동일하게 배제해야 한다(E-3-1 실사례 구조)."""
        from esgenie.layer3_detect import _score_d1_numeric

        pool = [
            _n("E-3-1", 1_161_214.0, "tCO2eq", 2025, "온실가스 감축 효과"),
            _n("E-3-1", 396_152.0, "tCO2 eq", 2024,
               "온실가스 배출량 (Scope 1 + 지역 기반 Scope 2) 합계"),
        ]
        graph = _graph(*pool)

        ledger_value = extract_with_ssot(_empty_report(), graph).mapped["E-3-1"]["value"]
        assert ledger_value == 396_152.0, "최신 연도라도 '감축 효과'는 배제된다"

        d1 = _score_d1_numeric("온실가스 배출량은 396,152 tCO2eq이다.", graph)
        assert d1.score == 0.0, f"D1도 같은 노드를 골라야 한다 — {d1.detail}"


# =====================================================================
# 3. 음성 테스트 — 과차단 방지
# =====================================================================

class TestNoOverBlocking:
    """규칙이 정상 값을 막으면 커버리지가 죽는다. 네 방향으로 고정한다."""

    def test_pool_without_any_total_term_still_yields_value(self) -> None:
        """E-4-1 유형 — '합계' 어휘가 하나도 없는 풀에서도 값이 뽑혀야 한다."""
        pool = [
            _n("E-4-1", 7_497.0, "TJ", 2023, "전력 사용량"),
            _n("E-4-1", 827_967.0, "MWh", 2024, "전력 사용량"),
        ]
        picked = select_representative_node("E-4-1", pool, report_year=REPORT_YEAR)
        assert picked is not None, "총량 어휘가 없다고 미공시가 되면 안 된다"
        assert picked.value == 7_497.0

    def test_e6_2_recycling_rate_not_blocked_by_e6_1_negative(self) -> None:
        """E-6-1의 negative('재활용')가 E-6-2로 새면 재활용률 항목이 통째로 막힌다.

        negative keyword는 자기 코드에만 적용된다는 계약을 고정한다.
        """
        pool = [
            _n("E-6-2", 92.9, "%", 2024, "2024년 국내 사업장 폐기물 재활용률"),
            _n("E-6-2", 56.9, "%", 2024, "2024년 플라스틱 재활용률"),
            _n("E-6-2", 77.1, "%", 2022, "폐기물 매립 제로화(재활용률)"),
        ]
        picked = select_representative_node("E-6-2", pool, report_year=REPORT_YEAR)
        assert picked is not None, "'재활용'이 E-6-2를 막아선 안 된다"
        # '제로화'는 파생 어휘로 배제, 자재별(플라스틱)보다 폐기물 재활용률이 우선.
        assert picked.value == 92.9

    def test_single_candidate_wins_regardless_of_rules(self) -> None:
        """후보가 1개면 규칙과 무관하게 그것을 쓴다 — 유일 증빙을 버리지 않는다."""
        only = _n("E-3-1", 1_161_214.0, "tCO2eq", 2025, "온실가스 감축 효과")
        picked = select_representative_node("E-3-1", [only], report_year=REPORT_YEAR)
        assert picked is only, "유일 후보는 파생 hint라도 선택된다(폐기 아님)"

    def test_hintless_pool_falls_back_to_year_proximity(self) -> None:
        """hint가 없는 얇은 노드(정형 채널 등)는 최후 기준인 연도로 갈린다."""
        a = EvidenceNode(id="a", metric="E-6-2", value=56.9, unit="%", period=2025,
                         source="ocr/x", origin="ocr_unstructured")
        b = EvidenceNode(id="b", metric="E-6-2", value=92.9, unit="%", period=2026,
                         source="ocr/x", origin="ocr_unstructured")
        picked = select_representative_node("E-6-2", [a, b], report_year=2025)
        assert picked is not None
        assert picked.value == 56.9, "동률이면 보고 연도 근접"


# =====================================================================
# 4. 미공시 폴백 — 잘못된 값보다 미공시가 낫다 (라벨링 §3-1)
# =====================================================================

class TestUndisclosedFallback:
    """전 후보가 배제되면 값을 채우지 않고 플래그를 남긴다."""

    def test_returns_none_when_all_candidates_excluded(self) -> None:
        pool = [
            _n("E-3-1", 1_161_214.0, "tCO2eq", 2025, "온실가스 감축 효과"),
            _n("E-3-1", 1_493.0, "tCO2eq", 2025, "연간 온실가스 감축 예상량 (태양광 발전설비)"),
            _n("E-3-1", 16.5, "톤 CO2eq", 2023, "알루미늄 1톤당 기존 온실가스 배출량"),
        ]
        assert select_representative_node("E-3-1", pool, report_year=REPORT_YEAR) is None

    def test_ledger_leaves_code_undisclosed_with_flag(self) -> None:
        """원장은 값을 채우지 않고 'no_representative_node' 플래그를 남긴다."""
        pool = [
            _n("E-3-1", 1_161_214.0, "tCO2eq", 2025, "온실가스 감축 효과"),
            _n("E-3-1", 1_493.0, "tCO2eq", 2025, "연간 온실가스 감축 예상량 (태양광 발전설비)"),
        ]
        result = extract_with_ssot(_empty_report(), _graph(*pool))

        assert "E-3-1" not in result.mapped, "배제된 코드에 값을 채우면 안 된다"
        assert "no_representative_node" in result.confidence_flags.get("E-3-1", [])

    def test_excluded_nodes_are_preserved_for_audit(self) -> None:
        """선택 실패가 노드 폐기를 뜻하지는 않는다 — 그래프에 그대로 남는다."""
        pool = [_n("E-3-1", 1_161_214.0, "tCO2eq", 2025, "온실가스 감축 효과"),
                _n("E-3-1", 1_493.0, "tCO2eq", 2025, "연간 온실가스 감축 예상량 (태양광 발전설비)")]
        graph = _graph(*pool)
        extract_with_ssot(_empty_report(), graph)
        assert len(graph.search_nodes(keywords=["E-3-1"])) == 2

    def test_empty_pool_returns_none(self) -> None:
        assert select_representative_node("E-3-1", [], report_year=REPORT_YEAR) is None


# =====================================================================
# 5. 단위 정규화 — 항목 단위로 환산해 원장에 저장
# =====================================================================

class TestUnitNormalization:
    """실측 단위 불일치 5건: 실환산 3건 + 표기 정규화 2건."""

    def test_e7_1_ton_to_kg_real_conversion(self) -> None:
        """E-7-1 대기오염물질 항목 단위는 kg, 실측은 ton → 1,000배 실환산."""
        assert normalize_to_item_unit("E-7-1", 150.67, "ton") == (150_670.0, "kg", None)

    def test_e7_2_ton_to_kg_real_conversion(self) -> None:
        assert normalize_to_item_unit("E-7-2", 555.124, "ton") == (555_124.0, "kg", None)

    def test_e4_1_mwh_to_tj_real_conversion(self) -> None:
        value, unit, flag = normalize_to_item_unit("E-4-1", 827_967.0, "MWh")
        assert unit == "TJ" and flag is None
        assert abs(value - 2_980.6812) < 1e-3      # 1 TJ = 277.778 MWh

    def test_e3_2_spacing_only_is_harmless(self) -> None:
        """'tCO2 eq' vs 'tCO2eq' — 값은 그대로, 표기만 통일."""
        assert normalize_to_item_unit("E-3-2", 3_077_693.0, "tCO2 eq") == (
            3_077_693.0, "tCO2eq", None)

    def test_e6_1_ton_hangul_is_harmless(self) -> None:
        """'ton' vs '톤' — 같은 단위의 표기 차이. 1,000배 환산이 일어나선 안 된다."""
        assert normalize_to_item_unit("E-6-1", 72_463.0, "ton") == (72_463.0, "톤", None)

    def test_incompatible_unit_keeps_original_with_flag(self) -> None:
        """환산 불가면 원 단위 유지 + unit_suspect(기존 동작)."""
        value, unit, flag = normalize_to_item_unit("E-4-1", 12.9, "%")
        assert (value, unit) == (12.9, "%")
        assert flag == "unit_suspect"

    def test_ledger_stores_item_unit(self) -> None:
        """원장 저장 시점에 환산이 적용되는가 — E-7-1 ton → kg."""
        node = _n("E-7-1", 150.67, "ton", 2022, "대기오염물질 배출량 합계")
        entry = _ledger_pick(node)
        assert entry["unit"] == "kg"
        assert entry["value"] == 150_670.0


# =====================================================================
# 6. 코드 배정 일관성 — 동일 hint → 동일 코드 (작업 1)
# =====================================================================

class TestCodeAssignmentConsistency:
    """같은 hint가 연도마다 다른 코드로 가면 후보 풀이 오염된다."""

    def test_same_hint_always_resolves_to_same_code(self) -> None:
        """실사례: 'Scope 3 온실가스 배출량 연결(일부)' → 2022는 E-3-2, 2023·2024는 E-3-1.

        원인은 LLM 추정 경합이 아니라 _backfill_kesg_codes의 taken_codes 선착순 점유였다.
        첫 metric만 E-3-2를 받고 나머지는 코드 미부여 → evidence_graph의 _HINT_TO_KESG
        폴백에서 '온실가스'(E-3-1)에 걸렸다. Scope3 값이 Scope1+2 풀을 오염시킨 직접 원인.
        """
        from esgenie.ssot.evidence_graph import _resolve_kesg_code
        from esgenie.ssot.ocr_router import (
            DocChannel,
            ExtractedMetric,
            OcrExtraction,
            _backfill_kesg_codes,
        )

        hint = "Scope 3 온실가스 배출량 연결(일부)"
        ext = OcrExtraction(
            source_file="t.pdf", channel=DocChannel.UNSTRUCTURED, doc_type="esg_report",
            metrics=[
                ExtractedMetric(metric_hint=hint, value=3_344_082.0, unit="tCO2 eq", period="2023"),
                ExtractedMetric(metric_hint=hint, value=3_136_024.0, unit="tCO2 eq", period="2024"),
                ExtractedMetric(metric_hint=hint, value=3_077_693.0, unit="tCO2 eq", period="2022"),
            ],
        )
        _backfill_kesg_codes(ext)

        codes = {_resolve_kesg_code(m) for m in ext.metrics}
        assert codes == {"E-3-2"}, f"동일 hint가 여러 코드로 갈렸다: {codes}"

    def test_different_label_still_cannot_steal_taken_code(self) -> None:
        """중복 가드는 유지 — 다른 라벨은 이미 점유된 코드를 못 가져간다.

        보조수치 '지정폐기물'이 E-6-1로 해소돼 본문확정 18.4t와 1000× 어긋난
        유령 중복노드를 만들던 사례(기존 회귀 가드).
        """
        from esgenie.ssot.ocr_router import (
            DocChannel,
            ExtractedMetric,
            OcrExtraction,
            _backfill_kesg_codes,
        )

        ext = OcrExtraction(
            source_file="t.pdf", channel=DocChannel.STRUCTURED, doc_type="waste_ledger",
            metrics=[
                ExtractedMetric(metric_hint="E-6-1 본문확정", value=18.4, unit="ton",
                                period="2024", kesg_code_guess="E-6-1"),
                ExtractedMetric(metric_hint="폐기물 처리량", value=18_400.0, unit="kg",
                                period="2024"),
            ],
        )
        _backfill_kesg_codes(ext)
        assert ext.metrics[1].kesg_code_guess is None, "다른 라벨이 점유 코드를 가져갔다"
