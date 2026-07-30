"""연도 복원 회귀 게이트 — feature/l0-period-recovery (2026-07-29).

두 변경을 고정한다:
  · 작업2 `_attach_column_headers`의 **2단 헤더**(연도 행 위 / 집계 행 아래) 인식.
    실측(모비스 p.70) 12컬럼 표에 `(합계)`가 세 번 똑같이 붙어 하류 LLM이 3세트를
    1세트로 접었다 → `17,694(합계|2024)`로 연도를 갈라 준다.
  · 작업1 `period_inferred` — 원문에 연도가 없어 report_year로 채운 값임을 노드에 남긴다.

★ 표시 테스트는 **최우선 가드**다: 잘 되던 표(모비스 1단 헤더·신한 단일연도)를 건드리지
않는다는 것을 고정한다. 모비스가 좋아져도 나머지가 흔들리면 실패다.

근거: docs/연도미상_원인조사_2026-07-29.md · docs/연도복원_결과_2026-07-29.md
"""
from __future__ import annotations

from esgenie.ssot.evidence_graph import (
    EvidenceGraph,
    EvidenceNode,
    _normalize_period,
    merge_ocr_extraction,
)
from esgenie.ssot.node_select import select_representative_node
from esgenie.ssot.ocr_router import (
    DocChannel,
    ExtractedMetric,
    OcrExtraction,
    _attach_column_headers,
    _map_vlm_json,
)


def _row(*cells: tuple[float, str]) -> list[tuple[float, str]]:
    return list(cells)


def _join(rows: list[list[tuple[float, str]]]) -> list[str]:
    return [" | ".join(t for _x, t in r) for r in rows]


# 모비스 p.70 실측 기하 — 연도 x중심은 자기 집계 그룹의 중앙에 놓인다.
_MOBIS_YEARS = _row((333.1, "2022"), (517.3, "2023"), (701.6, "2024"))
_MOBIS_AGGS = _row(
    (264.0, "국내(별도)"), (308.5, "국내 자회사"), (354.2, "해외 자회사"), (400.3, "합계"),
    (448.2, "국내(별도)"), (494.3, "국내 자회사"), (540.4, "해외 자회사"), (584.5, "합계"),
    (632.5, "국내(별도)"), (678.5, "국내 자회사"), (724.6, "해외 자회사"), (770.7, "합계"),
)
# 값은 실측 그대로다(p.71 '폐기물 처리량(매립, 소각 등)'). 합계 열 세 개가
# 17,694 / 17,129 / 19,352 이고, 2022 합계는 미폐기 처리량 52,806과 더해
# 발생량 70,500이 된다 — 연도가 붙어야 확인되는 합 항등식이다.
_MOBIS_VALUES = _row(
    (60.0, "폐기물 처리량"), (120.0, "ton"),
    (264.0, "1,693"), (308.5, "2,052"), (354.2, "13,949"), (400.3, "17,694"),
    (448.2, "1,208"), (494.3, "3,102"), (540.4, "12,818"), (584.5, "17,129"),
    (632.5, "502"), (678.5, "3,657"), (724.6, "15,193"), (770.7, "19,352"),
)


# =====================================================================
# 작업2 — 2단 헤더 인식
# =====================================================================

class TestTwoTierHeader:
    def test_two_tier_splits_twelve_columns_into_three_years(self):
        """3연도 × 4집계 = 12컬럼이 연도별로 갈린다 — 이 변경의 본체.

        실측 p.70: '17,694'은 2022 합계가 아니라 **2022 합계**여야 한다(그룹 중앙 매핑).
        종전에는 세 세트 전부 `(합계)`라 LLM이 첫 세트만 뽑고 period='미상'을 달았다.
        """
        out = _join(_attach_column_headers([_MOBIS_YEARS, _MOBIS_AGGS, _MOBIS_VALUES]))
        line = out[-1]
        assert "17,694(합계|2022)" in line
        assert "17,129(합계|2023)" in line
        assert "19,352(합계|2024)" in line
        # 그룹별 4개씩 — 균등 매핑이 유지돼야 한다.
        for year, n in (("2022", 4), ("2023", 4), ("2024", 4)):
            assert line.count(f"|{year})") == n, f"{year} 그룹이 {n}개가 아니다: {line}"

    def test_mobis_one_tier_table_unchanged(self):
        """★ 최우선 가드 — 1단 헤더(집계 라벨만) 표는 종전 그대로 `(합계)`만 붙는다.

        실측 p.53 '재생에너지 사용·전환율' 12컬럼 표가 이 형태다. 연도 전용 행이 없으면
        연도를 붙이지 않는다 — 없는 연도를 만들면 미상보다 나쁘다.
        """
        out = _join(_attach_column_headers([_MOBIS_AGGS, _MOBIS_VALUES]))
        line = out[-1]
        assert "17,694(합계)" in line
        assert "|2022" not in line and "|2024" not in line

    def test_single_year_table_untouched(self):
        """★ 최우선 가드 — 열이 연도가 아닌 표(신한 p.160 구조)는 아무것도 안 바뀐다.

        Scope 구분처럼 컬럼이 연도가 아니면 연도 부착 대상이 아니다. 5개사 실측에서
        신한·NAVER·삼성전기·LG화학 텍스트는 바이트 단위로 불변이었다(길이 델타 0).
        """
        rows = [
            _row((100.0, "구분"), (300.0, "Scope 1"), (400.0, "Scope 2"), (500.0, "합계")),
            _row((100.0, "배출량"), (300.0, "1,234"), (400.0, "5,678"), (500.0, "6,912")),
        ]
        before = _join(rows)
        after = _join(_attach_column_headers(rows))
        assert "|20" not in " ".join(after), "연도가 없는 표에 연도가 붙었다"
        # 집계 부착 자체는 종전 동작이므로 라벨은 붙어도 연도는 없어야 한다.
        assert before[0] == after[0]

    def test_uneven_year_groups_skip_year_attachment(self):
        """매핑이 모호하면 **연도 부착만 생략**한다 — 틀린 연도가 미상보다 나쁘다.

        집계 컬럼 5개를 연도 2개로 나눌 수 없으므로(5 % 2 != 0) 연도를 붙이지 않는다.
        집계 부착은 종전대로 유지돼 정보가 줄지 않는다.
        """
        rows = [
            _row((200.0, "2023"), (500.0, "2024")),
            _row((150.0, "국내"), (200.0, "해외"), (250.0, "합계"),
                 (450.0, "국내"), (500.0, "합계")),
            _row((60.0, "배출량"), (150.0, "10"), (200.0, "20"), (250.0, "30"),
                 (450.0, "40"), (500.0, "50")),
        ]
        line = _join(_attach_column_headers(rows))[-1]
        assert "|2023" not in line and "|2024" not in line
        assert "(합계)" in line, "연도 생략이 집계 부착까지 없애면 안 된다"

    def test_map_vlm_json_routes_year_to_period(self):
        """하류 분리 — LLM이 라벨을 hint에 통째로 복사해도 연도는 period로 간다.

        연도를 hint에 남기면 node_select의 수식어·집계 판정 문맥이 흐려진다.
        프롬프트만 믿지 않고 파싱에서도 가른다(방어 이중화).
        """
        metrics, _ = _map_vlm_json({"metrics": [
            {"metric_hint": "폐기물 처리량(합계|2022)", "value": 17694, "unit": "ton",
             "period": ""},
        ]})
        assert len(metrics) == 1
        assert metrics[0].period == "2022"
        assert "2022" not in metrics[0].metric_hint
        assert "합계" in metrics[0].metric_hint

    def test_map_vlm_json_keeps_explicit_period(self):
        """LLM이 이미 구체적 period를 읽었으면 덮어쓰지 않는다(정보 손실 방지)."""
        metrics, _ = _map_vlm_json({"metrics": [
            {"metric_hint": "폐기물 처리량(합계|2022)", "value": 17694, "unit": "ton",
             "period": "2022-12"},
        ]})
        assert metrics[0].period == "2022-12"
        assert "2022" not in metrics[0].metric_hint


# =====================================================================
# 작업1 — period_inferred
# =====================================================================

class TestPeriodInferred:
    def test_fallback_marks_inferred_and_keeps_value(self):
        """연도를 못 읽으면 값은 report_year로 채우되 **추론임을 남긴다**.

        하위 호환으로 period 값 자체는 종전과 같다 — 원장 표시·D1 비교가 안 깨진다.
        """
        assert _normalize_period("", fallback=2025) == (2025, True)
        assert _normalize_period("미상", fallback=2025) == (2025, True)
        assert _normalize_period("2022 합계", fallback=2025) == (2022, False)

        g = EvidenceGraph("X", "테스트")
        ext = OcrExtraction(
            source_file="x.pdf", channel=DocChannel.UNSTRUCTURED, doc_type="policy",
            metrics=[ExtractedMetric(metric_hint="폐기물 발생량", value=70500.0,
                                     unit="ton", period="", kesg_code_guess="E-6-1")],
        )
        merge_ocr_extraction(g, ext, report_year=2025)
        node = next(n for n in g.nodes.values() if n.value == 70500.0)
        assert node.period == 2025          # 값은 종전과 동일(하위 호환)
        assert node.period_inferred is True  # 사실은 드러난다

    def test_real_period_not_marked_inferred(self):
        """음성 테스트 — 원문에 연도가 있으면 추론 표시가 붙지 않는다(과표시 방지)."""
        g = EvidenceGraph("X", "테스트")
        ext = OcrExtraction(
            source_file="x.pdf", channel=DocChannel.UNSTRUCTURED, doc_type="policy",
            metrics=[ExtractedMetric(metric_hint="폐기물 발생량", value=70500.0,
                                     unit="ton", period="2022", kesg_code_guess="E-6-1")],
        )
        merge_ocr_extraction(g, ext, report_year=2025)
        node = next(n for n in g.nodes.values() if n.value == 70500.0)
        assert node.period == 2022
        assert node.period_inferred is False

    def test_g4_projection_not_triggered_by_inferred_period(self):
        """G4는 추론 연도로 '미래 전망'을 판정하지 않는다.

        폴백값은 report_year와 같아 지금도 임계값을 넘지 않지만, 폴백 기준이 바뀌어도
        근거 없는 projection 분리가 나지 않도록 고정한다(D1 비교 대상을 잘못 줄인다).
        """
        g = EvidenceGraph("X", "테스트")
        ext = OcrExtraction(
            source_file="x.pdf", channel=DocChannel.UNSTRUCTURED, doc_type="policy",
            metrics=[ExtractedMetric(metric_hint="온실가스 배출량", value=100000.0,
                                     unit="tCO2eq", period="", kesg_code_guess="E-3-1")],
        )
        merge_ocr_extraction(g, ext, report_year=2025)
        metrics = [n.metric for n in g.nodes.values()]
        assert "E-3-1" in metrics
        assert not any(m.endswith("__projection") for m in metrics)


# =====================================================================
# 작업1 — node_select 연도 축의 추론 후순위
# =====================================================================

def _node(nid: str, *, value: float, period: int, hint: str,
          inferred: bool = False) -> EvidenceNode:
    return EvidenceNode(
        id=nid, metric="E-6-1", value=value, unit="ton", period=period,
        source="ocr/test", raw_text=f"{hint}={value}ton (x.pdf)",
        origin="ocr_unstructured", confidence=0.75, period_inferred=inferred,
    )


class TestNodeSelectInferredDemotion:
    def test_inferred_loses_tie_within_same_year_rank(self):
        """같은 연도 순위에서 추론 노드는 확정 노드에 진다.

        폴백 노드는 근접도가 0으로 나와 확정 노드와 동률이 되는데, 그 동률을 근거 없이
        이기면 안 된다. 여기서는 값·hint가 같아 연도 축이 유일한 갈림이다.
        """
        confirmed = _node("a-confirmed", value=100.0, period=2025, hint="폐기물 발생량 합계")
        inferred = _node("b-inferred", value=200.0, period=2025, hint="폐기물 발생량 합계",
                         inferred=True)
        pick = select_representative_node("E-6-1", [inferred, confirmed], report_year=2025)
        assert pick.id == "a-confirmed"

    def test_higher_axes_still_beat_inferred_flag(self):
        """음성 테스트 — 축 순서는 그대로다. 추론 표시가 **상위 축을 뒤집지 않는다**.

        집계 축(5단계)은 연도 축(7단계)보다 앞이므로, '합계'를 가진 추론 노드가
        부분값('국내(별도)')인 확정 노드를 이겨야 한다. 이게 깨지면 연도 강등이
        선택 규칙 전체를 오염시킨 것이다.
        """
        partial = _node("a-partial", value=7403.0, period=2025, hint="폐기물 발생량 국내(별도)")
        total_inferred = _node("b-total", value=70500.0, period=2025,
                               hint="폐기물 발생량 합계", inferred=True)
        pick = select_representative_node("E-6-1", [partial, total_inferred],
                                          report_year=2025)
        assert pick.id == "b-total"

    def test_year_axis_still_ahead_of_value_mode(self):
        """음성 테스트 — 연도(7)가 값 최빈(8)보다 앞이라는 불변식.

        2025 노드 1개 vs 2023 노드 2개(같은 값). 최빈이 앞서면 2023이 이긴다 —
        시계열 정체를 반복 언급으로 오독하는 회귀다(node_select docstring §값 최빈).
        """
        recent = _node("a-2025", value=999.0, period=2025, hint="폐기물 발생량 합계")
        old1 = _node("b-2023", value=111.0, period=2023, hint="폐기물 발생량 합계")
        old2 = _node("c-2023", value=111.0, period=2023, hint="폐기물 발생량 합계")
        pick = select_representative_node("E-6-1", [recent, old1, old2], report_year=2025)
        assert pick.id == "a-2025"

    def test_node_without_field_behaves_as_confirmed(self):
        """하위 호환 — `period_inferred` 필드가 없는 객체도 종전과 같이 동작한다.

        저장된 덤프·목 객체가 이 필드 없이 들어와도 getattr 기본값 False로 읽혀
        선택 결과가 바뀌지 않아야 한다.
        """
        class Bare:
            def __init__(self, nid, value, period, hint):
                self.id, self.value, self.period = nid, value, period
                self.unit, self.confidence = "ton", 0.75
                self.raw_text = f"{hint}={value}ton (x.pdf)"

        a = Bare("a", 100.0, 2025, "폐기물 발생량 합계")
        b = Bare("b", 200.0, 2024, "폐기물 발생량 합계")
        pick = select_representative_node("E-6-1", [a, b], report_year=2025)
        assert pick.id == "a"
