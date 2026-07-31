"""L1 원장 값 공급 경로 회귀 테스트 — feature/ledger-provenance.

근거: docs/역추적_claim오염_경로분리_2026-07-25.md
본문 claim은 L1 원장에서 나오고 D1 비교 대상은 그래프 노드에서 나온다. 두 경로가
따로 놀아 PR #44(G1~G6) 이후에도 D1이 잔존한 문제를 세 지점에서 고정한다.

  작업1  우선순위 역전 — 무게이트 DART 정규식 값 < 게이트 통과 OCR 노드
  작업2  대표노드 선택 — 원장도 D1(G5)과 같은 노드를 고른다
         (2026-07-26: 선택 기준이 'report_year 최근접' → hint 기반 공용 규칙으로 교체.
          연도는 최후 tie-breaker로 강등 — tests/test_node_selection.py 참조)
  작업3  정규식 방어선 — 가드어휘 스킵 + 값 상식범위

과차단 방지를 위해 각 항목에 음성 테스트(기존 동작 유지)를 둔다.
"""
from __future__ import annotations

from esgenie.dart_client import (
    SOURCE_DART_REGEX,
    CompanyReport,
    _regex_extract_kesg,
)
from esgenie.ssot.evidence_graph import EvidenceGraph, EvidenceNode
from esgenie.ssot.ssot_pipeline import SOURCE_OCR_GATED, extract_with_ssot


# =====================================================================
# 헬퍼
# =====================================================================

def _graph(report_year: int, *nodes: EvidenceNode) -> EvidenceGraph:
    g = EvidenceGraph(corp_code="00164788", corp_name="테스트")
    g.report_year = report_year
    for n in nodes:
        g.add_node(n)
    return g


def _node(code: str, value: float, unit: str, period: int, *,
          nid: str | None = None, confidence: float = 1.0) -> EvidenceNode:
    return EvidenceNode(
        id=nid or f"n_{code}_{period}_{value}",
        metric=code, value=value, unit=unit, period=period,
        source="ocr/report.pdf", origin="ocr_unstructured",
        source_file="report.pdf", confidence=confidence,
    )


def _report(report_year: int, kesg_data: dict) -> CompanyReport:
    return CompanyReport(
        corp_code="00164788", corp_name="테스트", industry="",
        report_year=report_year, financials={}, kesg_data=kesg_data,
        raw_text_snippets=[], source="test",
    )


# =====================================================================
# 작업1 — 우선순위 역전 해소
# =====================================================================

class TestPriorityInversion:
    """무게이트 DART 정규식 값이 G1~G6를 통과한 OCR 노드를 이기면 안 된다."""

    def test_ungated_regex_value_is_superseded_by_ocr_node(self) -> None:
        """현대모비스 E-5-1 실사례: 정규식이 '누적 목표 감축량' 114,884를 용수로 집었다."""
        report = _report(2025, {"E-5-1": {
            "value": 114884.0, "unit": "ton",
            "note": "DART 원문 정규식 추출", "source_tier": SOURCE_DART_REGEX,
        }})
        graph = _graph(2025, _node("E-5-1", 623648.0, "ton", 2025))

        result = extract_with_ssot(report, graph)
        entry = result.mapped["E-5-1"]

        assert entry["value"] == 623648.0, "게이트 통과 OCR 노드가 이겨야 한다"
        assert entry["source_tier"] == SOURCE_OCR_GATED
        assert entry["superseded_value"] == 114884.0, "강등된 값은 감사추적용으로 보존"
        assert "regex_superseded" in result.confidence_flags["E-5-1"]

    def test_untagged_dart_value_is_not_superseded(self) -> None:
        """구조화 API·사외이사 재계산 등 태그 없는 값은 강등 대상이 아니다(회귀 가드)."""
        report = _report(2025, {"G-1-2": {
            "value": 81.8, "unit": "%", "note": "DART 사외이사 구조화 API(9/11인)",
        }})
        graph = _graph(2025, _node("G-1-2", 55.0, "%", 2025))

        entry = extract_with_ssot(report, graph).mapped["G-1-2"]
        assert entry["value"] == 81.8
        assert entry.get("source_tier") in ("", None)

    def test_regex_value_kept_when_no_ocr_node(self) -> None:
        """OCR 증빙이 없으면 정규식 값이 그대로 남는다 — 커버리지 손실 없음."""
        report = _report(2025, {"E-5-1": {
            "value": 114884.0, "unit": "ton",
            "note": "DART 원문 정규식 추출", "source_tier": SOURCE_DART_REGEX,
        }})
        entry = extract_with_ssot(report, _graph(2025)).mapped["E-5-1"]
        assert entry["value"] == 114884.0


# =====================================================================
# 작업2 — 대표노드 선택을 D1(G5)과 통일
# =====================================================================

class TestRepresentativeNodeSelection:
    """원장 대표노드 = D1(G5)과 같은 공용 규칙(node_select.select_representative_node).

    2026-07-26 규칙 갱신: 'report_year 최근접'은 단일 기준에서 **최후 tie-breaker(7순위)로
    강등**됐다. hint 기반 축(파생 배제 · 지표 정합 · 집계 · 단위)이 먼저 갈린다.
    아래 테스트들은 hint가 없는 얇은 노드를 쓰므로 앞 축이 전부 동률 → 연도 기준이 작동한다.
    상세 규칙 회귀는 tests/test_node_selection.py 참조.
    """

    def test_picks_report_year_node_not_latest(self) -> None:
        """네이버 계열 사례: max(period)는 미래 노드를 실적으로 골랐다.

        hint가 없어 앞 축이 동률이므로 연도(최후 기준)로 갈린다.
        """
        graph = _graph(
            2025,
            _node("E-6-2", 56.9, "%", 2025, nid="cur"),
            _node("E-6-2", 92.9, "%", 2026, nid="future"),
        )
        entry = extract_with_ssot(_report(2025, {}), graph).mapped["E-6-2"]
        assert entry["value"] == 56.9, "보고 연도 노드를 골라야 한다"

    def test_ledger_and_d1_pick_the_same_node(self) -> None:
        """같은 그래프에서 원장 선택과 D1(G5) 선택이 일치해야 구조적 오탐이 사라진다.

        규칙이 바뀌어도 이 단언은 유지된다 — 두 경로가 **같은 공용 함수**를 호출하므로
        선택식을 여기서 복제하지 않고 그 함수를 직접 부른다(중복 구현 시 대칭이 깨진다).
        """
        from esgenie.ssot.node_select import select_representative_node

        ref_year = 2025
        nodes = [
            _node("E-6-2", 56.9, "%", 2025, nid="a"),
            _node("E-6-2", 92.9, "%", 2026, nid="b"),
            _node("E-6-2", 40.1, "%", 2023, nid="c"),
        ]
        graph = _graph(ref_year, *nodes)

        ledger_value = extract_with_ssot(_report(ref_year, {}), graph).mapped["E-6-2"]["value"]
        d1_node = select_representative_node("E-6-2", nodes, report_year=ref_year)
        assert d1_node is not None
        assert ledger_value == d1_node.value

    def test_falls_back_to_latest_without_report_year(self) -> None:
        """report_year가 없는 얇은 그래프는 기존 동작(최신 노드) 유지."""
        graph = EvidenceGraph(corp_code="X", corp_name="X")
        graph.report_year = None
        for n in (_node("E-6-2", 56.9, "%", 2025, nid="a"),
                  _node("E-6-2", 92.9, "%", 2026, nid="b")):
            graph.add_node(n)
        entry = extract_with_ssot(_report(2025, {}), graph).mapped["E-6-2"]
        assert entry["value"] == 92.9


# =====================================================================
# 작업3 — DART 정규식 경로 최소 방어선
# =====================================================================

class TestRegexGuards:
    """가드어휘·상식범위를 통과하는 첫 매치만 채택한다."""

    def test_guard_term_skips_target_row(self) -> None:
        """'목표 감축량' 행을 건너뛰고 실적 행을 집는다(라벨링 §3-6).

        구 동작(re.search 첫 매치)은 114,884를 배출량으로 채웠다 — 실제 356만톤 대비 97% 축소.
        """
        text = ("온실가스 목표 감축량(Scope1+2) 114,884 tCO2eq 2030년 기준 · "
                "총 온실가스 배출량 3,560,074 tCO2eq")
        assert _regex_extract_kesg(text)["E-3-1"]["value"] == 3_560_074.0

    def test_sanity_range_skips_absurd_small_value(self) -> None:
        """각주번호·원단위 같은 극소값을 총배출량으로 쓰지 않는다(라벨링 §2-1). 구 동작=4.0."""
        text = ("온실가스 배출 원단위 4 tCO2eq 억원당 · "
                "총 온실가스 배출량 3,560,074 tCO2eq")
        assert _regex_extract_kesg(text)["E-3-1"]["value"] == 3_560_074.0

    def test_ratio_above_100_rejected(self) -> None:
        """비율 항목은 0~100 밖이면 다른 지표를 집은 것. 구 동작=320.0."""
        text = "재생에너지 사용 비율 320 % 이며 재생에너지 전환율은 12.9 %"
        assert _regex_extract_kesg(text)["E-4-2"]["value"] == 12.9

    def test_guard_does_not_leak_across_neighbouring_row(self) -> None:
        """앞 행의 '목표'가 뒤 행의 정상 실적까지 차단하면 안 된다(과차단 가드).

        _guard_window가 직전 숫자에서 멈추는 이유 — 고정 폭 되돌아보기는 여기서 오작동한다.
        """
        text = "용수 사용 절감 목표 5,000 톤 · 용수 사용량 1,992,921 톤"
        assert _regex_extract_kesg(text)["E-5-1"]["value"] == 1_992_921.0

    def test_clean_text_unchanged(self) -> None:
        """가드에 걸릴 것이 없으면 기존과 동일하게 첫 매치를 집는다(과차단 방지)."""
        text = "총 에너지 사용량은 9,075 TJ 이며 용수 사용량은 1,992,921 톤이다"
        got = _regex_extract_kesg(text)
        assert got["E-4-1"]["value"] == 9075.0
        assert got["E-5-1"]["value"] == 1_992_921.0

    def test_all_regex_entries_are_tagged(self) -> None:
        """정규식 경로 값은 전부 강등 가능하도록 태그가 붙어야 한다."""
        text = "총 에너지 사용량은 9,075 TJ 이며 용수 사용량은 1,992,921 톤이다"
        for code, entry in _regex_extract_kesg(text).items():
            if code == "G-1-2":
                continue  # 사외이사 비율은 재계산 값이라 태그 대상 아님
            assert entry.get("source_tier") == SOURCE_DART_REGEX, code
