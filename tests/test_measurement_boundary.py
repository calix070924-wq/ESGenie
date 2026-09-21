"""측정 경계·제한적 합산·비교 가능성 회귀 — 2026-09-20.

근거 문서
---------
  docs/작업지시서_HMC_응답서_검증정합성_개선_2026-09-20.md  §2 경계 보존, §3 부분 비율, §4 비교
  docs/reviews/HMC_출력_지적사항_검토_2026-09-20.md

고친 결함(HMC 응답서 실측)
  · `period`가 연도 정수뿐이라 1개월 고지서(142,560 kWh)·상반기 월평균(805 MWh)·
    연간 목표(540 MWh)가 모두 `period=2026`으로 같은 후보 풀에 앉았다.
  · 비율 표현을 무조건 total로 처리해 '태양광 5.6%'·'기타 재생에너지 1.2%'가
    전체 재생에너지 비율 자리에서 verified로 나갔다.
  · E-4-1이 대표 노드 하나만 골라, 전기·가스 고지서 두 장을 올려도 전력만 실렸다.

입력 구분(작업지시서 §1)
  · reconstructed — ExtractedMetric을 손으로 넣은 재구성 입력(합성 아님. 값·문구는
    데모 PDF 원문과 저장 OCR 캐시에서 그대로 옮겼다. 새 OCR/LLM 호출 없음)
  · 12번 문서 hint 원문은 data/_cache/ocr의 저장 응답에서 그대로 옮겼다.
"""
from __future__ import annotations

import pytest

from esgenie.ssot.boundary import (
    Boundary,
    comparable,
    covers_full_year,
    derive_boundary,
    merge_boundaries,
    plan_sum,
)
from esgenie.ssot.node_select import classify_common_value_role
from esgenie.ssot.ocr_router import DocChannel, ExtractedMetric, OcrExtraction
from esgenie.ssot.evidence_graph import build_unified_graph
from esgenie.ssot.audit_trace import build_data_points
from esgenie.ssot.selection import plan_energy_sum
from esgenie.ssot.ssot_pipeline import extract_with_ssot

REPORT_YEAR = 2026

# 12번 재생에너지 현황 문서(저장 OCR 원문 그대로) — hint / 기간표기 쌍.
DOC12_HINTS: list[tuple[str, str]] = [
    ("태양광 월평균 사용량", "2026년 1~6월 월평균"),
    ("태양광 비율", "2026년 1~6월 월평균"),
    ("그린 프리미엄 월평균 사용량", "2026년 1~6월 월평균"),
    ("그린 프리미엄 비율", "2026년 1~6월 월평균"),
    ("기타 재생에너지 비율", "2026년 1~6월 월평균"),
    ("화석연료 기반 전력 비율", "2026년 1~6월 월평균"),
    ("총 전력 사용량 월평균", "2026년 1~6월 월평균"),
    ("태양광 연간 발전 목표", "2026년 하반기 이후 연간"),
    ("재생에너지 비율 현재", "2026년 상반기"),
    ("재생에너지 비율 목표", "2030년 목표"),
]


# =====================================================================
# 헬퍼 — 재구성 입력
# =====================================================================

def _extraction(metrics, *, source_file, doc_type="kepco_bill",
                channel=DocChannel.STRUCTURED) -> OcrExtraction:
    from esgenie.ssot.ocr_router import _backfill_kesg_codes

    ext = OcrExtraction(
        source_file=source_file, channel=channel, doc_type=doc_type,
        metrics=list(metrics), clauses=[], raw_text="제1공장",
        router_meta={"source": "reconstructed_input"},
    )
    _backfill_kesg_codes(ext)
    return ext


class _LocalReport:
    """DART 없는 로컬 보고서 — ssot_local 경로(비상장 중소기업)."""
    source = "ssot_local"

    def __init__(self):
        self.corp_code = "HANWOOL"
        self.corp_name = "한울정밀공업"
        self.report_year = REPORT_YEAR
        self.fiscal_year = REPORT_YEAR
        self.kesg_data: dict = {}
        self.sections: dict = {}
        self.raw_text = ""


_ELEC = ExtractedMetric(metric_hint="사용전력량", value=142560, unit="kWh",
                        period="2026-05", kesg_code_guess="E-4-1", confidence=0.9)
_GAS_MJ = ExtractedMetric(metric_hint="사용열량", value=360772, unit="MJ",
                          period="2026-05", kesg_code_guess="E-4-1", confidence=0.9)
_GAS_M3 = ExtractedMetric(metric_hint="도시가스 사용량", value=9100, unit="m3",
                          period="2026-05", kesg_code_guess="E-4-1", confidence=0.9)


def _run(extractions, *, codes=("E-4-1", "E-4-2", "E-3-1")):
    graph = build_unified_graph(
        None, list(extractions), corp_code="HANWOOL",
        corp_name="한울정밀공업", report_year=REPORT_YEAR)
    result = extract_with_ssot(_LocalReport(), graph, profile="sme")
    return graph, result, build_data_points(graph, {}, target_codes=list(codes))


def _dp(points, code):
    for p in points:
        if p.kesg_code == code:
            return p
    return None


# =====================================================================
# §2-1 — 경계를 실제로 읽어내는가
# =====================================================================

class TestBoundaryDerivation:
    """새 OCR/LLM 호출 없이, 이미 추출된 문구만으로 경계 축을 읽는다."""

    @pytest.mark.parametrize("hint,period,agg,months", [
        ("태양광 월평균 사용량", "2026년 1~6월 월평균", "monthly_average", 6),
        ("사용전력량", "2026-05", "monthly", 1),
        ("태양광 연간 발전 목표", "2026년 하반기 이후 연간", "annual", 12),
        ("재생에너지 비율 현재", "2026년 상반기", "period_total", 6),
    ])
    def test_aggregation_and_span(self, hint, period, agg, months):
        b = derive_boundary(hint, period)
        assert (b.aggregation, b.coverage_months) == (agg, months)

    @pytest.mark.parametrize("hint,kind", [
        ("태양광 월평균 사용량", "electricity_solar"),
        ("그린 프리미엄 월평균 사용량", "electricity_green_tariff"),
        ("화석연료 기반 전력 비율", "electricity_fossil"),
        ("총 전력 사용량 월평균", "electricity_total"),
        ("사용전력량", "electricity_total"),
        ("기타 재생에너지 비율", "renewable_residual"),
    ])
    def test_measure_kind(self, hint, kind):
        assert derive_boundary(hint, "2026").measure_kind == kind

    def test_city_gas_heat_shares_kind_with_volume(self):
        """도시가스 고지서의 사용열량(MJ)과 사용량(m³)은 같은 물리량의 두 표기다."""
        heat = derive_boundary("사용열량", "2026-05", unit="MJ", doc_type="gas_bill",
                               doc_context="02_도시가스요금고지서_2026-05.pdf gas_bill")
        volume = derive_boundary("도시가스 사용량", "2026-05", unit="m3",
                                 doc_type="gas_bill")
        assert heat.measure_kind == volume.measure_kind == "fuel_city_gas"

    @pytest.mark.parametrize("hint,period,basis", [
        ("태양광 연간 발전 목표", "2026년 하반기 이후 연간", "target"),
        ("재생에너지 비율 목표", "2030년 목표", "target"),
        ("사용전력량", "2026-05", "actual"),
    ])
    def test_basis(self, hint, period, basis):
        assert derive_boundary(hint, period).basis == basis

    def test_unknown_axes_are_marked_not_defaulted(self):
        """문서에 없는 범위를 전사·연간으로 조용히 채우지 않는다(§2-1 마지막 항)."""
        b = derive_boundary("사용전력량", "2026-05")
        assert b.site_scope == "unknown"
        assert "site_scope" in b.inferred
        assert b.aggregation == "monthly"          # 읽은 축은 inferred에 없다
        assert "aggregation" not in b.inferred

    def test_empty_boundary_is_unknown_not_equal(self):
        """구버전 노드·저장 JSON은 빈 Boundary로 읽히고, 같다고 취급되지 않는다."""
        assert Boundary.from_dict(None) == Boundary()
        assert Boundary.from_dict({"이상한키": 1}).is_known is False
        assert covers_full_year(Boundary()) is None

    def test_doc12_every_hint_reads_at_least_one_axis(self):
        for hint, period in DOC12_HINTS:
            assert derive_boundary(hint, period).is_known, hint


# =====================================================================
# §3 — 비율의 분자를 보고 역할을 정한다
# =====================================================================

class TestRatioRole:
    """에너지원별 구성비를 전체 비율의 대체값으로 승격하지 않는다."""

    @pytest.mark.parametrize("hint", [
        "태양광 비율", "그린 프리미엄 비율", "화석연료 기반 전력 비율",
        "기타 재생에너지 비율",
    ])
    def test_source_ratio_is_component(self, hint):
        assert classify_common_value_role(hint) == "component"

    @pytest.mark.parametrize("hint", [
        "재생에너지 비율 현재",       # 12번 문서의 명시된 전체 비율
        "재생에너지 사용·전환율",     # 현대모비스 12.9% — 기존 정상값
        "폐기물 재활용률",
        "여성 관리자 비율",
        "재생에너지 비율(태양광 포함)",   # '포함'은 범위 확대 — 잔여 행이 아니다
    ])
    def test_whole_ratio_stays_total(self, hint):
        assert classify_common_value_role(hint) == "total"


# =====================================================================
# §2-2 — 제한적 합산과 이중계상 3종 방어
# =====================================================================

class TestLimitedSum:
    def _node(self, hint, value, unit, period="2026-05", doc_type="kepco_bill",
              context="제1공장"):
        from types import SimpleNamespace
        return SimpleNamespace(
            id=f"n_{hint}", value=value, unit=unit,
            boundary=derive_boundary(hint, period, unit=unit, doc_type=doc_type,
                                     doc_context="제1공장" if "gas_bill" in context else context))

    def test_same_physical_quantity_in_two_units_is_not_summed(self):
        """도시가스 m³ + MJ 이중계상 — 단위 차원과 측정 대상 양쪽에서 막힌다."""
        items = [self._node("사용열량", 360772, "MJ", doc_type="gas_bill",
                            context="도시가스요금고지서 gas_bill"),
                 self._node("도시가스 사용량", 9100, "m3", doc_type="gas_bill")]
        decision = plan_sum(items)
        assert len(decision.summable) <= 1
        assert not decision.ok

    def test_family_total_plus_component_is_blocked(self):
        """총 전력 + 태양광 + 그린 프리미엄 + 기타 재생 — 총량만 남는다."""
        items = [
            self._node("총 전력 사용량 월평균", 805, "MWh", "2026년 1~6월 월평균"),
            self._node("태양광 월평균 사용량", 45, "MWh", "2026년 1~6월 월평균"),
            self._node("그린 프리미엄 월평균 사용량", 30, "MWh", "2026년 1~6월 월평균"),
            self._node("기타 재생에너지 월평균 사용량", 10, "MWh", "2026년 1~6월 월평균"),
        ]
        decision = plan_sum(items)
        assert [x.id for x in decision.summable] == ["n_총 전력 사용량 월평균"]
        assert len(decision.blocked) == 3
        assert all("이중계상" in why for _, why in decision.blocked)

    def test_duplicate_upload_of_same_bill_is_blocked(self):
        items = [self._node("사용전력량", 142560, "kWh"),
                 self._node("사용 전력량", 142560, "kWh")]
        decision = plan_sum(items)
        assert len(decision.summable) == 1
        assert "중복" in decision.blocked[0][1]

    def test_target_is_excluded_from_actual_sum(self):
        items = [self._node("태양광 월평균 사용량", 45, "MWh", "2026년 1~6월 월평균"),
                 self._node("태양광 연간 발전 목표", 540, "MWh", "2026년 하반기 이후 연간")]
        decision = plan_sum(items)
        assert any("목표" in why for _, why in decision.reference)

    def test_unknown_measure_target_fails_closed(self):
        """측정 대상을 못 읽으면 합산하지 않는다 — 중복 여부를 확인할 수 없다."""
        from types import SimpleNamespace
        items = [SimpleNamespace(id="a", value=1.0, unit="TJ", boundary=Boundary()),
                 SimpleNamespace(id="b", value=2.0, unit="TJ", boundary=Boundary())]
        decision = plan_sum(items)
        assert decision.summable == []
        assert all("미상" in why for _, why in decision.blocked)

    def test_sum_never_claims_total_completeness(self):
        items = [self._node("사용전력량", 142560, "kWh"),
                 self._node("사용열량", 360772, "MJ", doc_type="gas_bill",
                            context="도시가스요금고지서 gas_bill")]
        decision = plan_sum(items)
        assert decision.ok
        assert decision.completeness == "partial"

    def test_merge_boundaries_drops_axes_that_disagree(self):
        a = derive_boundary("사용전력량", "2026-05", doc_context="제1공장")
        b = derive_boundary("사용열량", "2026-05", unit="MJ", doc_type="gas_bill",
                            doc_context="제2공장 도장라인 gas_bill")
        merged = merge_boundaries([a, b])
        assert merged.site == ""            # 갈린 축은 미상으로 되돌린다
        assert merged.aggregation == "monthly"


# =====================================================================
# §4-1 — 같은 것끼리만 비교한다
# =====================================================================

class TestComparable:
    def test_actual_vs_target_is_not_comparable(self):
        a = derive_boundary("태양광 월평균 사용량", "2026년 1~6월 월평균")
        b = derive_boundary("태양광 연간 발전 목표", "2026년 하반기 이후 연간")
        status, reason = comparable(a, b)
        assert status == "not_comparable" and reason

    def test_monthly_bill_vs_annual_disclosure_is_not_comparable(self):
        month = derive_boundary("사용전력량", "2026-05")
        year = derive_boundary("전력 사용량", "2026년 연간")
        assert comparable(month, year)[0] == "not_comparable"

    def test_different_energy_source_is_not_comparable(self):
        assert comparable(derive_boundary("태양광 월평균 사용량", "2026-05"),
                          derive_boundary("사용열량", "2026-05",
                                          unit="MJ", doc_type="gas_bill"))[0] == "not_comparable"

    def test_missing_scope_is_confirmation_needed_not_blocked(self):
        """한쪽 경계를 못 읽은 경우는 '범위 확인 필요'다. 비교 자체를 막지 않는다."""
        known = derive_boundary("사용전력량", "2026-05", doc_context="제1공장")
        status, _ = comparable(known, derive_boundary("사용전력량", "2026-05"))
        assert status == "scope_unconfirmed"

    def test_same_boundary_compares(self):
        b = derive_boundary("사용전력량", "2026-05", doc_context="제1공장")
        assert comparable(b, b)[0] == "compared"


# =====================================================================
# §2-2 / §3 — 원장·DataPoint까지 전달되는가 (재구성 입력 end-to-end)
# =====================================================================

class TestLedgerCarriesBoundary:
    def test_two_energy_sources_are_summed_with_both_evidence(self):
        """전기 0.513216 TJ + 가스 0.360772 TJ = 0.873988 TJ. 반올림 없음."""
        graph, result, points = _run([
            _extraction([_ELEC], source_file="01_전기요금청구서_2026-05.pdf"),
            _extraction([_GAS_MJ], source_file="02_도시가스요금고지서_2026-05.pdf",
                        doc_type="gas_bill"),
        ])
        dp = _dp(points, "E-4-1")
        assert dp is not None
        assert dp.value == pytest.approx(0.873988, abs=1e-9)
        assert dp.unit == "TJ"
        files = {e.file_name for e in dp.evidence_files}
        assert files == {"01_전기요금청구서_2026-05.pdf",
                         "02_도시가스요금고지서_2026-05.pdf"}

    def test_partial_sum_is_not_verified_and_says_why(self):
        graph, result, points = _run([
            _extraction([_ELEC], source_file="01_전기요금청구서_2026-05.pdf"),
            _extraction([_GAS_MJ], source_file="02_도시가스요금고지서_2026-05.pdf",
                        doc_type="gas_bill"),
        ])
        dp = _dp(points, "E-4-1")
        assert dp.completeness == "partial"
        assert dp.verification != "verified"
        assert "2026-05" in dp.boundary_label
        assert any("입증되지 않아" in n for n in dp.scope_notes)

    def test_single_electricity_bill_does_not_attach_gas_document(self):
        """쓰지 않은 가스 문서를 전력 단독값의 산정 근거로 붙이지 않는다(§2-2 마지막 항)."""
        graph, result, points = _run([
            _extraction([_ELEC], source_file="01_전기요금청구서_2026-05.pdf"),
        ])
        dp = _dp(points, "E-4-1")
        assert dp.value == pytest.approx(0.513216, abs=1e-9)
        assert [e.file_name for e in dp.evidence_files] == [
            "01_전기요금청구서_2026-05.pdf"]

    def test_gas_volume_does_not_double_count_with_heat(self):
        """같은 도시가스 고지서의 m³와 MJ가 함께 추출돼도 합산되지 않는다."""
        graph, result, points = _run([
            _extraction([_GAS_MJ, _GAS_M3],
                        source_file="02_도시가스요금고지서_2026-05.pdf",
                        doc_type="gas_bill"),
        ])
        dp = _dp(points, "E-4-1")
        assert dp is not None
        decision = plan_energy_sum(graph, "E-4-1")
        assert decision is None                 # 합산 성립 자체를 막는다
        assert dp.value < 0.4                   # 열량 단독값(0.360772 TJ) 수준

    def test_monthly_value_is_not_scaled_to_annual(self):
        graph, result, points = _run([
            _extraction([_ELEC], source_file="01_전기요금청구서_2026-05.pdf"),
        ])
        dp = _dp(points, "E-4-1")
        assert dp.value == pytest.approx(0.513216, abs=1e-9)   # ×12 확대 없음
        assert dp.period == REPORT_YEAR


# =====================================================================
# §3 — 12번 재생에너지 문서(재구성 입력, 저장 OCR 원문 값 그대로)
# =====================================================================

_DOC12_ROWS: list[tuple[str, float, str, str]] = [
    ("태양광 월평균 사용량", 45, "MWh", "2026년 1~6월 월평균"),
    ("태양광 비율", 5.6, "%", "2026년 1~6월 월평균"),
    ("그린 프리미엄 월평균 사용량", 30, "MWh", "2026년 1~6월 월평균"),
    ("그린 프리미엄 비율", 3.8, "%", "2026년 1~6월 월평균"),
    ("기타 재생에너지 월평균 사용량", 10, "MWh", "2026년 1~6월 월평균"),
    ("기타 재생에너지 비율", 1.2, "%", "2026년 1~6월 월평균"),
    ("화석연료 기반 전력 월평균 사용량", 720, "MWh", "2026년 1~6월 월평균"),
    ("화석연료 기반 전력 비율", 89.4, "%", "2026년 1~6월 월평균"),
    ("총 전력 사용량 월평균", 805, "MWh", "2026년 1~6월 월평균"),
    ("태양광 연간 발전 목표", 540, "MWh", "2026년 하반기 이후 연간"),
    ("재생에너지 비율 현재", 10.6, "%", "2026년 상반기"),
    ("재생에너지 비율 목표", 20, "%", "2030년 목표"),
]
_DOC12_FILE = "12_재생에너지사용현황_2026-06.pdf"


def _doc12(*, keep=None, drop=()):
    rows = [r for r in _DOC12_ROWS if (keep is None or r[0] in keep) and r[0] not in drop]
    metrics = [ExtractedMetric(metric_hint=h, value=v, unit=u, period=p, confidence=0.85)
               for h, v, u, p in rows]
    return _extraction(metrics, source_file=_DOC12_FILE,
                       doc_type="ambiguous_fallback_vlm",
                       channel=DocChannel.UNSTRUCTURED)


class TestRenewableRatioEligibility:
    """태양광 5.6%·기타 1.2%가 전체 재생에너지 비율 자리에서 verified로 나가면 안 된다."""

    def test_stated_whole_ratio_wins_but_is_not_verified(self):
        _g, _r, points = _run([_doc12()])
        dp = _dp(points, "E-4-2")
        assert dp is not None
        assert dp.value == pytest.approx(10.6)     # 명시된 전체 비율을 우선한다
        assert dp.value_role == "total"
        # 상반기 값 하나로는 연간 전체성을 입증할 수 없다(§3 마지막 항).
        assert dp.completeness == "partial"
        assert dp.verification != "verified"
        assert "상반기" in dp.boundary_label

    @pytest.mark.parametrize("keep,value", [
        ({"태양광 비율"}, 5.6),
        ({"기타 재생에너지 비율"}, 1.2),
    ])
    def test_source_ratio_alone_is_component_not_verified(self, keep, value):
        _g, _r, points = _run([_doc12(keep=keep)])
        dp = _dp(points, "E-4-2")
        assert dp is not None
        assert dp.value == pytest.approx(value)
        assert dp.value_role == "component"
        assert dp.verification != "verified"
        assert "partial_value" in dp.confidence_flags

    def test_whole_ratio_removed_does_not_promote_a_component(self):
        """전체 비율 행을 지운 통제 실험 — 남은 구성비가 total 자리를 차지하지 않는다."""
        _g, _r, points = _run([_doc12(drop=("재생에너지 비율 현재",))])
        dp = _dp(points, "E-4-2")
        assert dp is None or dp.value_role == "component"
        if dp is not None:
            assert dp.verification != "verified"

    def test_green_premium_ratio_alone_does_not_fill_the_answer(self):
        """'그린 프리미엄 비율'만 있으면 E-4-2 코드로 승격되지 않는다.

        조달수단 이름만으로 재생에너지 비율을 채우지 않는 기존 동작을 고정한다
        (§3 — 그린프리미엄·PPA·REC의 포함 여부는 확인된 조달 증빙에 따른다).
        """
        _g, _r, points = _run([_doc12(keep={"그린 프리미엄 비율"})])
        assert _dp(points, "E-4-2") is None

    def test_target_ratio_never_becomes_the_answer(self):
        _g, _r, points = _run([_doc12()])
        dp = _dp(points, "E-4-2")
        assert dp.value != pytest.approx(20.0)     # 2030년 목표 20%는 실적이 아니다
