"""원장 누락 두 결함의 회귀 테스트 (2026-09-20 전량 실측에서 발견).

(1) 전각 '％' 단위가 K-ESG 정의의 반각 '%'와 '명백히 다른 단위'로 기각돼
    수치 3건이 코드 미부여로 원장에서 사라졌다.
(2) 코드가 붙은 정량 근거가 있는데도 승격되지 않는 항목이 플래그·사유 없이
    조용히 사라져, 산출물이 '왜 비었는지'를 설명할 수 없었다.
"""
from __future__ import annotations

from esgenie.dart_client import _empty_report
from esgenie.layer1_extract import _relaxed_unit, _unit_suspect
from esgenie.layer2_rag import _area_item_rows
from esgenie.rag_gates.units import normalize_unit
from esgenie.ssot.evidence_graph import build_unified_graph
from esgenie.ssot.ocr_router import DocChannel, ExtractedMetric, OcrExtraction
from esgenie.ssot.ssot_pipeline import extract_with_ssot


# ---- (1) 전각 단위 ---------------------------------------------------------------

class TestFullwidthUnit:
    def test_relaxed_unit_folds_fullwidth_percent(self):
        assert _relaxed_unit("％") == "%"

    def test_normalize_unit_accepts_fullwidth_percent(self):
        assert normalize_unit("％") == "%"

    def test_fullwidth_percent_is_not_unit_suspect(self):
        """전각 ％ vs 반각 % 는 표기 차이지 다른 단위가 아니다."""
        assert _unit_suspect("％", "%") is False

    def test_permille_stays_a_different_unit(self):
        """‰는 %와 1,000배 차이다 — 전각 축약이 여기까지 번지면 안 된다."""
        assert _unit_suspect("‰", "%") is True
        assert normalize_unit("‰") == "‰"

    def test_fullwidth_percent_metric_reaches_ledger(self):
        """전각 ％ 수치가 K-ESG 코드를 받아 원장 확정값이 된다."""
        extraction = OcrExtraction(
            source_file="report.pdf", channel=DocChannel.UNSTRUCTURED,
            doc_type="sustainability_report",
            metrics=[ExtractedMetric(
                metric_hint="재생 원부자재 사용(구매) 비율", value=2.8, unit="％",
                period="2024", kesg_code_guess="E-2-2", confidence=0.75, page=68,
            )],
        )
        report = _empty_report("012330", 2024)
        report.source = "ssot_local"
        graph = build_unified_graph(report, [extraction], corp_code="012330",
                                    corp_name="테스트사", report_year=2024)
        result = extract_with_ssot(report, graph, profile="full")

        assert "E-2-2" in result.mapped
        fact = graph.resolved_facts["E-2-2"]
        assert fact is not None
        assert fact.value == 2.8
        # 원장 단위는 항목 정의 단위(반각)로 정규화된다 — 값은 환산되지 않는다.
        assert fact.unit == "%"
        assert "unit_suspect" not in result.confidence_flags.get("E-2-2", [])


# ---- (2) 보류 사유 기록 -----------------------------------------------------------

def _qualitative_only_extraction() -> OcrExtraction:
    """정성 항목(S-4-1 안전보건 추진체계)에 정량 근거만 붙은 입력."""
    return OcrExtraction(
        source_file="report.pdf", channel=DocChannel.UNSTRUCTURED,
        doc_type="sustainability_report",
        metrics=[ExtractedMetric(
            metric_hint="산업안전보건위원회 개최 횟수", value=4.0, unit="건",
            period="2024", kesg_code_guess="S-4-1", confidence=0.8, page=41,
        )],
    )


class TestHoldReason:
    def _run(self):
        report = _empty_report("012330", 2024)
        report.source = "ssot_local"
        graph = build_unified_graph(report, [_qualitative_only_extraction()],
                                    corp_code="012330", corp_name="테스트사",
                                    report_year=2024)
        return graph, extract_with_ssot(report, graph, profile="full")

    def test_qualitative_item_keeps_no_value(self):
        """사유를 남기는 수정이 정성 항목에 수치를 채우는 회귀를 만들면 안 된다."""
        graph, result = self._run()
        assert "S-4-1" not in result.mapped
        assert graph.resolved_facts.get("S-4-1") is None

    def test_hold_flag_and_note_are_recorded(self):
        graph, result = self._run()
        assert "qualitative_item_needs_clause" in result.confidence_flags.get("S-4-1", [])
        notes = [n for n in result.notes if n.startswith("[보류] S-4-1")]
        assert len(notes) == 1
        assert "조항 근거가 없어" in notes[0]

    def test_hold_reason_is_visible_in_item_table(self):
        """화면·보고서가 쓰는 항목 표에 사유가 나타난다(미공시 판단 자체는 유지)."""
        _, result = self._run()
        _, missing = _area_item_rows(result, "S")
        row = next(r for r in missing if r["code"] == "S-4-1")
        assert row["status"].startswith("미공시")
        assert "보류" in row["status"]
        assert row["value"] is None

    def test_codes_without_evidence_stay_plain_missing(self):
        """근거가 아예 없는 항목에는 사유 표기를 붙이지 않는다(허위 근거 암시 방지)."""
        _, result = self._run()
        _, missing = _area_item_rows(result, "S")
        others = [r for r in missing if r["code"] != "S-4-1"]
        assert others, "비교할 다른 미공시 항목이 있어야 한다"
        assert all(r["status"] == "미공시" for r in others)
