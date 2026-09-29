"""대표값의 지표 범위·확정 연도와 원장 → D1 계약 회귀.

현대모비스 원본 PDF 53쪽: 2024 전체 에너지 9,075 TJ.
55쪽: 같은 연도 전력 7,929 TJ. 숫자가 맞아도 범위가 다른 경우를 분리한다.
원본 대조: outputs/diagnostics/20260918_mobis/selector_reaudit.md.
"""
from itertools import permutations

import pytest

from esgenie.dart_client import CompanyReport
from esgenie.layer3_detect import score_d1_numeric
from esgenie.ssot.audit_trace import build_data_points
from esgenie.ssot.boundary import Boundary, derive_boundary
from esgenie.ssot.detector_5axis import detect_d1_numeric
from esgenie.ssot.evidence_graph import EvidenceGraph, EvidenceNode
from esgenie.ssot.node_select import classify_value_role, select_representative_node
from esgenie.ssot.ssot_pipeline import extract_with_ssot


def node(nid, hint, value, *, code="E-4-1", unit="TJ", year=2024, inferred=False,
         scoped=False):
    """증빙 노드 하나. `scoped=True`면 `merge_ocr_extraction`과 같은 방식으로 경계를 채운다.

    기본값은 경계를 비워 둔다 — 구버전 원장과 경계를 못 읽은 증빙이 실제로 그렇고,
    그 입력에서 D1이 무엇을 하는지도 계약이다(§scope_unconfirmed 테스트).
    """
    return EvidenceNode(
        id=nid, metric=code, value=value, unit=unit, period=year,
        source="ocr/report", raw_text=f"{hint}={value}{unit} (report.pdf)",
        origin="ocr_unstructured", source_file="report.pdf", confidence=.9,
        period_inferred=inferred,
        boundary=derive_boundary(hint, hint, unit=unit) if scoped else Boundary(),
    )


def ledger(nodes, year=2025):
    graph = EvidenceGraph("TEST", "테스트")
    graph.report_year = year
    for item in nodes:
        graph.add_node(item)
    report = CompanyReport("TEST", "테스트", "", year, {}, {}, [], "test")
    return extract_with_ssot(report, graph, profile="full"), graph


@pytest.mark.parametrize("total,power", [(9075, 7929), (1500, 900)])
@pytest.mark.parametrize("hint", ["에너지 사용량", "에너지 사용량 합계"])
def test_total_energy_is_not_replaced_by_electricity(total, power, hint):
    energy = node("energy", hint, total)
    electricity = node("electricity", "전력 사용량", power)
    for candidates in permutations([energy, electricity]):
        assert select_representative_node("E-4-1", candidates, report_year=2025) is energy


@pytest.mark.parametrize("hint", ["전력 사용량", "전력 소비량 합계 2024", "전기사용량 총계"])
def test_electricity_total_is_still_only_an_energy_component(hint):
    assert classify_value_role("E-4-1", hint, report_year=2025) == "component"


def test_energy_total_can_explicitly_include_electricity():
    assert classify_value_role(
        "E-4-1", "전체 에너지 사용량(전력 사용량 포함)", report_year=2025) == "total"


def test_electricity_only_remains_available_with_partial_flag():
    result, graph = ledger([node("power", "전력 사용량 합계", 900)])
    fact = graph.resolved_facts["E-4-1"]
    assert result.mapped["E-4-1"]["value"] == fact.value == 900
    assert fact.value_role == "component"
    assert "partial_value" in fact.flags
    assert build_data_points(graph, {}, target_codes=["E-4-1"])[0].verification != "verified"


@pytest.mark.parametrize("old_year,inferred", [(2022, False), (2025, True)])
def test_actual_year_precedes_scope2_method_preference(old_year, inferred):
    current = node("current", "온실가스 배출량 Scope 1 + 시장 기반 Scope 2 합계", 401502,
                   code="E-3-1", unit="tCO2eq")
    older = node("old", "온실가스 배출량 Scope 1 + 지역 기반 Scope 2 합계", 396152,
                 code="E-3-1", unit="tCO2eq", year=old_year, inferred=inferred)
    for candidates in permutations([current, older]):
        assert select_representative_node("E-3-1", candidates, report_year=2025) is current


def test_same_year_scope2_method_preference_is_preserved():
    market = node("market", "온실가스 배출량 Scope 1 + 시장 기반 Scope 2 합계", 95,
                  code="E-3-1", unit="tCO2eq")
    location = node("location", "온실가스 배출량 Scope 1 + 지역 기반 Scope 2 합계", 100,
                    code="E-3-1", unit="tCO2eq")
    assert select_representative_node("E-3-1", [market, location], report_year=2025) is location


@pytest.mark.parametrize("code,unit,reported_hint,other_hint", [
    ("E-6-1", "ton", "폐기물 발생량 합계", "폐기물 처리량 합계"),
    ("E-6-2", "%", "폐기물 재활용률", "종이 재활용률"),
])
def test_newer_different_waste_measure_cannot_replace_reported_metric(code, unit, reported_hint, other_hint):
    reported = node("reported", reported_hint, 100, code=code, unit=unit, year=2023)
    other = node("other", other_hint, 60, code=code, unit=unit, year=2024)
    # 둘 다 총량 어휘/동일 단위를 갖춰도 발생량·처리량 및 재활용률의 분모는 다르다.
    for candidates in permutations([reported, other]):
        assert select_representative_node(code, candidates, report_year=2025) is reported


@pytest.mark.parametrize("code,unit,reported_hint,other_hint,claim_label", [
    ("E-6-1", "ton", "폐기물 발생량 합계", "폐기물 처리량 합계", "폐기물 발생량"),
    ("E-6-2", "%", "폐기물 재활용률", "종이 재활용률", "폐기물 재활용률"),
])
def test_waste_ledger_and_both_d1_paths_keep_the_metric_definition(code, unit, reported_hint, other_hint, claim_label):
    result, graph = ledger([
        node("reported", reported_hint, 100, code=code, unit=unit, year=2023),
        node("other", other_hint, 60, code=code, unit=unit, year=2024),
    ])
    fact = graph.resolved_facts[code]
    assert result.mapped[code]["value"] == fact.value == 100
    assert fact.period == 2023 and fact.representative_node_ids == ["reported"]
    for value, correct in [(100, True), (60, False)]:
        sentence = f"{claim_label}은 {value} {unit}입니다."
        for score in (score_d1_numeric(sentence, graph), detect_d1_numeric(sentence, code, graph)):
            assert (score.score == 0) is correct
            claim = score.evaluation["claims"][0]
            assert claim["evidence_ids"] == ["reported"]
            assert claim["reason"] == ("match" if correct else "mismatch")


@pytest.mark.parametrize("code,unit,hint", [
    ("E-6-1", "ton", "폐기물 발생량 합계"),
    ("E-6-2", "%", "폐기물 재활용률"),
])
def test_same_waste_measure_still_prefers_the_newer_year(code, unit, hint):
    older = node("older", hint, 100, code=code, unit=unit, year=2023)
    newer = node("newer", hint, 60, code=code, unit=unit, year=2024)
    for candidates in permutations([older, newer]):
        assert select_representative_node(code, candidates, report_year=2025) is newer


@pytest.mark.parametrize("scope", ["국내(별도)", "해외 자회사"])
def test_combined_scopes_do_not_erase_a_geographic_restriction(scope):
    partial = node("partial", f"온실가스 배출량 Scope 1+2 합계 {scope}", 30,
                   code="E-3-1", unit="tCO2eq", year=2025)
    total = node("total", "온실가스 배출량 Scope 1+2 합계", 100,
                 code="E-3-1", unit="tCO2eq")
    assert classify_value_role("E-3-1", partial, report_year=2025) == "component"
    assert select_representative_node("E-3-1", [partial, total], report_year=2025) is total


def test_recent_convertible_unit_precedes_older_exact_unit():
    current = node("current", "에너지 사용량 합계", 1000, unit="MWh")
    older = node("old", "에너지 사용량 합계", 5, year=2023)
    result, graph = ledger([older, current])
    assert result.mapped["E-4-1"]["value"] == pytest.approx(3.6)
    assert graph.resolved_facts["E-4-1"].representative_node_ids == ["current"]


def test_incompatible_quantity_cannot_beat_energy_with_total_word():
    electricity = node("power", "전력 사용량", 900)
    ratio = node("ratio", "재생에너지 사용률 합계", 25, unit="%", year=2025)
    result, graph = ledger([ratio, electricity])
    assert result.mapped["E-4-1"]["value"] == 900
    assert graph.resolved_facts["E-4-1"].representative_node_ids == ["power"]


@pytest.mark.parametrize("energy_hint", [
    "2024년 1~12월 전사 에너지 사용량 실적",
    "2024년 1~12월 전사 에너지 사용량 실적 합계",
])
def test_actual_ledger_and_both_d1_paths_use_total_energy(energy_hint):
    result, graph = ledger([
        node("energy", energy_hint, 9075, scoped=True),
        node("power", "2024년 1~12월 전사 전력 사용량 실적", 7929, scoped=True),
    ])
    fact = graph.resolved_facts["E-4-1"]
    assert result.mapped["E-4-1"]["value"] == fact.value == 9075
    assert fact.representative_node_ids == ["energy"]
    # 경계가 연간·전사·실적·전체 에너지원을 다 채우면 완전성이 입증되어 D1이 비교에 들어간다.
    assert fact.completeness == "total"
    for sentence, correct in [("에너지 사용량은 9,075 TJ입니다.", True),
                              ("에너지 사용량은 7,929 TJ입니다.", False)]:
        generated = score_d1_numeric(sentence, graph)
        ssot = detect_d1_numeric(sentence, "E-4-1", graph)
        for score in (generated, ssot):
            assert (score.score == 0) is correct
        claim = generated.evaluation["claims"][0]
        assert claim["evidence_ids"] == ["energy"]
        assert claim["reason"] == ("match" if correct else "mismatch")


# 위 테스트가 경계를 채운 입력을 쓰는 까닭 — 2026-09-29 머지 기록.
#
# main이 측정 경계 축을 들여오면서 E-4-1·E-4-2는 원장값의 완전성이 `total`로 **입증**되지
# 않으면 D1이 수치 비교를 건너뛴다(`boundary.claim_scope_status` → `scope_unconfirmed`).
# 이 파일의 `node()`는 경계를 비워 두었으므로 위 테스트가 검사하려던 값-대조가 아예
# 돌지 않았다. 기대값을 `scope_unconfirmed`로 낮추는 대신, 실제 OCR 경로
# (`merge_ocr_extraction`이 `derive_boundary`로 채운다)와 같은 입력을 주어 원래 계약을
# 그대로 세웠다. 경계를 비운 입력의 동작은 아래에서 따로 고정한다.


@pytest.mark.parametrize("energy_hint,partial", [("에너지 사용량", True), ("에너지 사용량 합계", False)])
def test_ledger_without_recorded_boundary_refuses_to_compare_instead_of_passing(energy_hint, partial):
    """경계 미기록 원장에서 D1은 **비교하지 않는다** — 맞는 값도 틀린 값도 통과시킨다.

    바람직한 상태가 아니라 현재 상태의 기록이다. 틀린 값 7,929도 위험으로 잡히지 않는
    것이 `scope_unconfirmed`의 대가다. 경계가 채워지면 위 테스트대로 되살아난다.
    """
    result, graph = ledger([
        node("energy", energy_hint, 9075), node("power", "전력 사용량", 7929),
    ])
    fact = graph.resolved_facts["E-4-1"]
    # 대표값 선정 자체는 경계와 무관하게 총 에너지를 고른다.
    assert result.mapped["E-4-1"]["value"] == fact.value == 9075
    assert fact.representative_node_ids == ["energy"]
    assert ("partial_value" in fact.flags) is partial
    assert fact.completeness == "unknown"
    assert "incomplete_scope" in fact.flags
    for sentence in ("에너지 사용량은 9,075 TJ입니다.", "에너지 사용량은 7,929 TJ입니다."):
        generated = score_d1_numeric(sentence, graph)
        ssot = detect_d1_numeric(sentence, "E-4-1", graph)
        for score in (generated, ssot):
            assert score.score == 0
        claim = generated.evaluation["claims"][0]
        assert claim["evidence_ids"] == ["energy"]
        assert claim["reason"] == "scope_unconfirmed"
        assert claim["comparison"] == "scope_unconfirmed"


def test_explicit_missing_selection_is_not_recreated_by_d1():
    _, graph = ledger([node("energy", "에너지 사용량 합계", 100)])
    graph.resolved_facts["E-4-1"] = None
    sentence = "에너지 사용량은 100 TJ입니다."
    generated = score_d1_numeric(sentence, graph)
    assert generated.abstain is True
    assert generated.abstain_reason == "no_evidence"
    assert detect_d1_numeric(sentence, "E-4-1", graph).evaluation["claims"][0]["reason"] == "no_evidence"
    assert build_data_points(graph, {}, target_codes=["E-4-1"]) == []
