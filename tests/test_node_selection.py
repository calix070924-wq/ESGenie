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
    is_partial_aggregate,
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

    def test_symmetry_holds_when_dart_node_is_in_the_pool(self) -> None:
        """★ 공용 함수만으로는 대칭이 성립하지 않는다 — **두 호출부가 다른 풀을 넘긴다**.

        원장 `_merge_ssot_evidence`는 `origin in (ocr_structured, ocr_unstructured)`만
        후보로 쓴다(DART 값은 이미 report.kesg_data → mapped 경로로 들어와 있어
        이중 처리를 피하려는 의도적 설계다). D1은 `graph.search_nodes(keywords=[c])`로
        **DART 노드까지 포함한** 풀을 넘긴다.

        실측 재현 구조: DART 노드가 '합계'(집계 1순위)를, OCR 노드가 '국내(별도)'(부분)를
        가지면 같은 규칙이 서로 다른 노드를 가리킨다.
          원장(OCR만)     →   623,648
          D1(DART 포함)   → 1,992,921
        현대모비스 E는 DART가 E 코드를 안 채워서 안 터졌을 뿐, 정규식이 채우는
        E-3-1·E-4-1·E-5-1·E-6-1과 구조화 API가 채우는 G 코드에서는 재현된다.

        해결은 풀 통일이 아니라 **원장의 결정을 그래프에 기록하고 D1이 그것을 따르는
        것**이다(graph.representative_node_ids). 규칙을 두 번 돌리지 않으므로 풀이
        달라도 구조적으로 어긋날 수 없다.
        """
        from esgenie.layer3_detect import _score_d1_numeric

        ocr = _n("E-5-1", 623_648.0, "ton", 2024, "용수 사용량(취수량) 국내(별도) 2024년")
        dart = EvidenceNode(
            id="00164788_E-5-1_2024__dart", metric="E-5-1", value=1_992_921.0,
            unit="ton", period=2024, source="dart/사업보고서",
            raw_text="용수 사용량(취수량) 합계=1992921.0ton (dart)",
            origin="dart", source_file="", confidence=1.0,
        )
        graph = _graph(ocr, dart)

        ledger_value = extract_with_ssot(_empty_report(), graph).mapped["E-5-1"]["value"]
        assert ledger_value == 623_648.0, "원장 풀은 OCR-only라는 기존 설계 확인"

        # D1은 DART 노드가 섞인 풀을 받지만, 원장이 기록한 대표 노드를 따라야 한다.
        d1_node = select_representative_node(
            "E-5-1", graph.search_nodes(keywords=["E-5-1"]), report_year=REPORT_YEAR)
        assert d1_node is not None
        assert d1_node.value == 1_992_921.0, "풀 구성 차이 자체는 그대로 재현된다"

        assert graph.representative_node_ids.get("E-5-1") == ocr.id, \
            "원장이 고른 노드가 그래프에 기록돼야 한다"
        d1 = _score_d1_numeric(f"용수 사용량은 {ledger_value:,.0f} 톤이다.", graph)
        assert d1.score == 0.0, f"D1이 원장 대표노드를 쓰지 않았다 — {d1.detail}"

    def test_d1_falls_back_when_ledger_has_no_representative(self) -> None:
        """원장이 미공시(대표노드 None)인 코드 — D1은 폴백으로 정상 동작해야 한다.

        기록이 없으면 예외 없이 기존 경로(공용 규칙 재실행)로 간다.
        여기서는 전 후보가 파생 어휘라 규칙이 None을 돌리므로 D1도 비교를 건너뛴다.
        """
        from esgenie.layer3_detect import _score_d1_numeric

        pool = [
            _n("E-3-1", 1_161_214.0, "tCO2eq", 2025, "온실가스 감축 효과"),
            _n("E-3-1", 1_493.0, "tCO2eq", 2025, "연간 온실가스 감축 예상량 (태양광 발전설비)"),
        ]
        graph = _graph(*pool)
        result = extract_with_ssot(_empty_report(), graph)

        assert "E-3-1" not in result.mapped
        assert "E-3-1" not in graph.representative_node_ids, "미공시는 기록하지 않는다"

        d1 = _score_d1_numeric("온실가스 배출량은 396,152 tCO2eq이다.", graph)
        assert d1.score == 0.0, f"폴백 경로에서 오탐 — {d1.detail}"

    def test_d1_falls_back_when_claim_unit_is_incompatible(self) -> None:
        """원장 대표노드가 TJ인데 claim이 %면 환산군이 달라 그 노드로는 비교가 무의미하다.

        원장은 항목 정의 단위로 정규화해 저장하지만 노드 자체는 원 단위다. 기록된
        노드가 claim 단위 필터(compat)에서 걸러지면 기록을 쓰지 않고 **기존 폴백**
        (공용 규칙 재실행)으로 가고, 그 사실을 detail에 남긴다 — 조용히 넘기면
        D1이 왜 그 노드를 골랐는지 추적할 수 없다.

        같은 코드에 % 노드가 함께 있어야 이 분기에 닿는다. 호환 노드가 아예 없으면
        기존 '단위 불일치 스킵'이 먼저 걸린다.
        """
        from esgenie.layer3_detect import _score_d1_numeric

        tj = _n("E-4-1", 7_497.0, "TJ", 2024, "전력 사용량")
        pct = _n("E-4-1", 12.9, "%", 2024, "재생에너지 사용률")
        graph = _graph(tj, pct)
        extract_with_ssot(_empty_report(), graph)
        assert graph.representative_node_ids.get("E-4-1") == tj.id, "원장은 TJ 노드를 채택"

        d1 = _score_d1_numeric("에너지 사용량 비중은 12.9 %이다.", graph)
        assert "단위 비호환 → 폴백" in d1.detail, f"폴백 사실이 기록되지 않았다 — {d1.detail}"
        # 폴백은 기존 동작 그대로 — % claim은 % 노드와 비교된다(TJ 노드와 비교하지 않는다).
        assert d1.score == 0.0, d1.detail
        assert pct.id in d1.evidence


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

    def test_single_candidate_wins_when_it_survives_hard_exclusion(self) -> None:
        """후보가 1개면 순위 축(3~7단계)과 무관하게 그것을 쓴다 — 유일 증빙을 버리지 않는다.

        2026-07-28 계약 변경: 종전에는 hard 배제(1·2단계)까지 우회해 파생 hint여도
        채택했다. 그 우회가 LG화학 E-5-1 '일평균 산업용수 공급량'을 통과시켰으므로
        **hard 배제는 후보 1개에도 적용된다**(아래 TestTimeUnitDerived). 순위 축만
        우회 대상이다 — 부분값·분해값 하나뿐이면 여전히 채택된다.
        """
        only = _n("E-4-1", 5_104.0, "TJ", 2025, "비재생 전력 소비량 해외 2025")
        picked = select_representative_node("E-4-1", [only], report_year=REPORT_YEAR)
        assert picked is only, "부분값 하나뿐이면 순위 축을 우회해 채택된다(폐기 아님)"

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
# 7. 총량 후보가 없는 풀 — 값은 싣고 부분값 표기 (결함 (a), 2026-07-28)
# =====================================================================

class TestPartialValueFlagging:
    """`_PARTIAL_TERMS`는 후순위 축이지 배제가 아니다 — 전부 부분값이면 하나가 이긴다.

    실측(5개사 일반화):
      · LG화학 E-4-1 — 36노드에 '합계/총계'가 0개 → '비재생 전력 소비량 해외 2025' 5,104 TJ
      · NAVER  E-3-2 — 노드 1개 → 'Scope 3 - Upstream 구매 제품 및 서비스' 71,385
        (docs/라벨링_발견_수정목록_2026-07-19.md §1에서 Scope3 Category 1로 지목된 값)

    결정(2026-07-28 사용자 확정): 미공시로 버리지 않고 `partial_value` 표기.
    커버리지가 이미 5~7항목/17이라 배제는 더 나쁘고, D1은 이 오류를 못 잡는다
    (원장·노드가 같은 값이라 Δ=0) — 표기가 유일한 방어선이다.
    """

    def test_all_partial_pool_still_yields_value_with_flag(self) -> None:
        """★ (a) 전부 부분값 — 값이 뽑히고 partial_value가 붙는다. 미공시가 되면 안 된다."""
        pool = [
            _n("E-4-1", 21_374.0, "TJ", 2025, "비재생 전력 소비량 글로벌 2025"),
            _n("E-4-1", 16_270.0, "TJ", 2025, "비재생 전력 소비량 국내 2025"),
            _n("E-4-1", 5_104.0, "TJ", 2025, "비재생 전력 소비량 해외 2025"),
        ]
        picked = select_representative_node("E-4-1", pool, report_year=REPORT_YEAR)
        assert picked is not None, "총량 후보가 없다고 미공시가 되면 안 된다(결정 ㄴ)"
        assert is_partial_aggregate(picked), "부분값임이 조회 가능해야 한다"

        result = extract_with_ssot(_empty_report(), _graph(*pool))
        assert result.mapped["E-4-1"]["value"] is not None, "값이 실려야 한다"
        assert "partial_value" in result.confidence_flags.get("E-4-1", [])

    def test_naver_scope3_category_is_flagged_partial(self) -> None:
        """NAVER E-3-2 실측 — 노드 1개(카테고리 1)여도 부분값 표기가 붙는다."""
        only = _n("E-3-2", 71_385.0, "tCO2 eq", 2025,
                  "Scope 3 온실가스 배출량 - Upstream 구매 제품 및 서비스")
        result = extract_with_ssot(_empty_report(), _graph(only))

        assert result.mapped["E-3-2"]["value"] == 71_385.0, "유일 증빙을 버리지 않는다"
        assert "partial_value" in result.confidence_flags.get("E-3-2", [])

    def test_total_candidate_present_means_no_flag(self) -> None:
        """★ (a) 총량이 있으면 그걸 고르고 플래그가 안 붙는다 — 과표기 방지."""
        pool = [
            _n("E-4-1", 24_506.0, "TJ", 2025, "전력 소비량 합계 2025"),
            _n("E-4-1", 5_104.0, "TJ", 2025, "비재생 전력 소비량 해외 2025"),
        ]
        picked = select_representative_node("E-4-1", pool, report_year=REPORT_YEAR)
        assert picked is not None and picked.value == 24_506.0
        assert not is_partial_aggregate(picked)

        result = extract_with_ssot(_empty_report(), _graph(*pool))
        assert "partial_value" not in result.confidence_flags.get("E-4-1", [])

    def test_flag_reaches_ledger_table_status_string(self) -> None:
        """★ 완료 기준 1 — 플래그가 원장 표 상태 문자열('·부분값')까지 노출된다.

        `unit_suspect` → '·단위확인'과 같은 자리다(Phase 2 표기 관행).
        플래그만 달고 끝내면 산출물에는 아무것도 드러나지 않는다.
        """
        from esgenie.layer2_rag import _area_item_rows

        only = _n("E-3-2", 71_385.0, "tCO2 eq", 2025,
                  "Scope 3 온실가스 배출량 - Upstream 구매 제품 및 서비스")
        result = extract_with_ssot(_empty_report(), _graph(only))
        covered, _ = _area_item_rows(result, "E")

        row = next(r for r in covered if r["code"] == "E-3-2")
        assert "·부분값" in row["status"], f"원장 표에 부분값 표기가 없다 — {row['status']}"

    def test_region_alone_loses_to_plain_metric(self) -> None:
        """★ (a-2) `해외` 단독 — 조직어 없는 지역어도 부분값으로 후순위여야 한다.

        사전에 '해외 자회사'·'해외 사업장'만 있어 LG 실측 hint는 안 걸렸다.
        """
        pool = [
            _n("E-4-1", 5_104.0, "TJ", 2025, "비재생 전력 소비량 해외"),
            _n("E-4-1", 24_506.0, "TJ", 2025, "전력 사용량"),
        ]
        picked = select_representative_node("E-4-1", pool, report_year=REPORT_YEAR)
        assert picked is not None
        assert picked.value == 24_506.0, "단독 지역어가 후순위로 안 밀렸다"

    def test_region_reinforcement_does_not_block_whole_scope(self) -> None:
        """음성 — `국내` 보강이 정상 항목을 막지 않는가.

        두 방향으로 고정한다:
          ① '국내외'는 전 범위 → 부분값이 아니다('국내'가 부분문자열로 걸리면 안 된다)
          ② '국내(별도)'는 종전대로 부분값 (기존 계약 유지)
          ③ 지역어가 붙은 부분값 하나뿐인 풀은 여전히 값이 뽑힌다(배제 아님)
        """
        whole = _n("E-5-1", 1_992_921.0, "ton", 2025, "국내외 용수 사용량(취수량)")
        part = _n("E-5-1", 623_648.0, "ton", 2025, "용수 사용량(취수량) 국내(별도)")
        assert not is_partial_aggregate(whole), "'국내외'가 부분값으로 오판정됐다"
        assert is_partial_aggregate(part)

        picked = select_representative_node("E-5-1", [part, whole], report_year=REPORT_YEAR)
        assert picked is whole

        # 부분값만 있어도 값은 나온다 — (a)는 표기이지 배제가 아니다.
        alone = select_representative_node("E-5-1", [part], report_year=REPORT_YEAR)
        assert alone is part

    def test_scope_expanding_suffix_is_not_partial(self) -> None:
        """음성 — '… 포함'은 범위 확대다.

        실측 오표기: LG화학 E-6-2 '폐기물 재활용률(열회수소각 포함)' 91%가
        `_BREAKDOWN_TERMS`의 '소각'에 걸려 부분값으로 표기됐다.
        """
        node = _n("E-6-2", 91.0, "%", 2025, "폐기물 재활용률 (열회수소각 포함)")
        assert not is_partial_aggregate(node)


# =====================================================================
# 8. 정성 항목에 정량값 차단 (결함 (c), 2026-07-28)
# =====================================================================

class TestQualitativeItemNoQuantitativeValue:
    """`E-1-2 환경경영 추진체계`는 정성(존재형) 항목 — 숫자가 들어갈 자리가 아니다.

    실측: 삼성전기 2.0 회 'ESG위원회 정기회의 횟수' · LG화학 5.0 명 '1차 개최 출석률'.
    """

    def test_qualitative_code_rejects_ocr_quantitative_node(self) -> None:
        """★ (c) data_type='정성' 코드는 OCR 정량 노드가 있어도 값이 안 실린다."""
        from esgenie.knowledge.kesg_items import by_code

        assert by_code("E-1-2").data_type == "정성", "전제 확인 — 항목 정의가 정성이다"

        pool = [
            _n("E-1-2", 2.0, "회", 2025, "ESG위원회 정기회의 횟수"),
            _n("E-1-2", 5.0, "명", 2025, "2025년 ESG위원회 1차 개최 출석률"),
        ]
        result = extract_with_ssot(_empty_report(), _graph(*pool))
        assert "E-1-2" not in result.mapped, "정성 항목에 정량값이 실렸다"

    def test_text_node_path_still_fills_clause_marker(self) -> None:
        """음성 — TextNode(존재형 증빙) 경로의 '문서 조항 확인'은 그대로 들어간다.

        정성 항목의 정상 동작이다. OCR 정량 노드가 같이 있어도 이 경로가 이긴다.
        """
        from esgenie.ssot.evidence_graph import TextNode

        graph = _graph(_n("E-1-2", 2.0, "회", 2025, "ESG위원회 정기회의 횟수"))
        graph.add_text_node(TextNode(
            id="t_e12", section="환경경영", kesg_code="E-1-2",
            text="환경경영 추진체계는 ESG위원회 산하 환경분과가 총괄한다.",
            source_file="규정집.pdf",
        ))
        result = extract_with_ssot(_empty_report(), graph)

        assert result.mapped["E-1-2"]["value"] == "문서 조항 확인"


# =====================================================================
# 9. 시간 원단위 배제 (결함 (d), 2026-07-28)
# =====================================================================

class TestTimeUnitDerived:
    """`일평균`은 연간 사용량이 아니다 — 365배 차이.

    실측: LG화학 E-5-1 '일평균 산업용수 공급량' 540,000 ton. 노드가 1개뿐이라
    '후보 1개면 무조건 채택' 우회에 걸려 규칙이 개입하지 못했다.
    """

    def test_daily_average_alone_yields_undisclosed(self) -> None:
        """★ (d) 유일 후보여도 hard 배제가 적용돼 미공시가 된다.

        365배 틀린 값보다 미공시가 낫다(라벨링 §3-1).
        """
        only = _n("E-5-1", 540_000.0, "톤", 2025, "일평균 산업용수 공급량")
        assert select_representative_node("E-5-1", [only], report_year=REPORT_YEAR) is None

        result = extract_with_ssot(_empty_report(), _graph(only))
        assert "E-5-1" not in result.mapped
        assert "no_representative_node" in result.confidence_flags.get("E-5-1", [])

    def test_annual_total_beats_daily_average_in_mixed_pool(self) -> None:
        """섞인 풀에서는 연간 총량이 이긴다(hard 배제 → 후보에서 빠진다)."""
        pool = [
            _n("E-5-1", 540_000.0, "톤", 2025, "일평균 산업용수 공급량"),
            _n("E-5-1", 1_992_921.0, "톤", 2025, "용수 사용량(취수량) 합계"),
        ]
        picked = select_representative_node("E-5-1", pool, report_year=REPORT_YEAR)
        assert picked is not None and picked.value == 1_992_921.0

    def test_plain_average_metrics_are_not_excluded(self) -> None:
        """음성 — '평균' 단독은 배제하지 않는다. 시간 원단위 표현만 잡는다.

        '평균 근속연수'(S-3-3 계열)처럼 '평균'이 정상인 지표가 있다.
        """
        from esgenie.ssot.node_select import is_derived_hint

        assert not is_derived_hint("평균 근속연수")
        assert not is_derived_hint("1인 평균 교육시간")
        assert is_derived_hint("일평균 산업용수 공급량")
        assert is_derived_hint("월평균 폐기물 발생량")
        assert is_derived_hint("1일당 용수 취수량")


# =====================================================================
# 10. 단위 표기 변종 (결함 (b), 2026-07-28)
# =====================================================================

class TestUnitNotationVariants:
    """`tCO2 eq`(공백 1개)는 되는데 `ton CO2 eq`는 안 됐다 — 신한 2건.

    값은 맞고 표기만 다른데 `unit_suspect`가 붙어 '단위 불일치 0' 지표를 오염시켰다.
    원인: `normalize_to_item_unit`의 문자열 폴백이 `_norm`(소문자·공백제거)만 썼다.
    `layer1_extract._relaxed_unit`이 이미 `ton→t` 축약을 갖고 있어 그걸 재사용한다.
    """

    def test_ton_co2_eq_variants_all_normalize(self) -> None:
        """★ (b) 3종 변종 전부 unit_suspect 없이 tCO2eq로."""
        for raw in ("ton CO2 eq", "ton CO2eq", "tCO2 eq", "톤 CO2eq"):
            assert normalize_to_item_unit("E-3-1", 89_861.0, raw) == (
                89_861.0, "tCO2eq", None), f"{raw!r}에 unit_suspect가 붙었다"

    def test_shinhan_ledger_has_no_unit_suspect(self) -> None:
        """신한 실측 2건 — 원장 저장 시점에 플래그가 안 남는다."""
        e31 = _n("E-3-1", 89_861.0, "ton CO2 eq", 2025,
                 "온실가스 배출량 - 총 배출량 국내(지역 기반)")
        result = extract_with_ssot(_empty_report(), _graph(e31))
        assert result.mapped["E-3-1"]["unit"] == "tCO2eq"
        assert "unit_suspect" not in result.confidence_flags.get("E-3-1", [])

    def test_genuinely_different_unit_still_suspect(self) -> None:
        """음성 — 진짜 다른 단위는 여전히 unit_suspect. 표기 요동만 흡수한다."""
        value, unit, flag = normalize_to_item_unit("E-4-1", 12.0, "명")
        assert (value, unit, flag) == (12.0, "명", "unit_suspect")
        assert normalize_to_item_unit("E-3-1", 5.0, "건")[2] == "unit_suspect"

    def test_relaxed_unit_is_reused_not_reimplemented(self) -> None:
        """★ 완료 기준 2 — `_relaxed_unit` 재사용(중복 구현 없음)을 구조로 고정.

        node_select가 자체 별칭표를 다시 만들면 layer1과 판정이 갈린다.
        패치해서 실제로 호출되는지 확인한다.
        """
        from unittest.mock import patch

        with patch("esgenie.layer1_extract._relaxed_unit", side_effect=lambda u: u) as m:
            # 항등 함수로 바꾸면 'ton CO2 eq' != 'tCO2eq'가 되어 판정이 뒤집힌다.
            assert normalize_to_item_unit("E-3-1", 1.0, "ton CO2 eq")[2] == "unit_suspect"
        assert m.called, "node_select가 _relaxed_unit을 쓰지 않는다(중복 구현 의심)"


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
