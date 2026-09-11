import pytest

from esgenie.dart_client import CompanyReport
from esgenie.pipeline import _build_risk_rows
from esgenie.ssot.evidence_graph import EvidenceGraph, EvidenceNode, build_from_dart
from esgenie.ssot.ssot_pipeline import extract_with_ssot
from esgenie.ssot.audit_trace import build_data_points
from esgenie.ssot.detector_5axis import detect_d1_numeric
from esgenie.layer3_detect import score_d1_numeric


def report(data=None):
    return CompanyReport('T', 'T', '', 2025, {}, data or {}, [], 'test')


def node(nid, value, unit='TJ', code='E-4-1', source='ocr', **kwargs):
    return EvidenceNode(nid, code, value, unit, 2025, source, origin='ocr_structured',
                        source_file='energy.pdf', raw_text=f'{code} 전사 합계 {value}{unit}', **kwargs)


def test_structured_dart_value_survives_ocr_representative():
    rpt = report({'E-4-1': {'value': 248.5, 'unit': 'TJ'}})
    graph = build_from_dart(rpt)
    graph.report_year = 2025
    graph.add_node(node('ocr', 61.2, confidence=.99))
    ledger = extract_with_ssot(rpt, graph)
    scores, rows = _build_risk_rows(graph, target_codes=['E-4-1'])
    dp = build_data_points(graph, scores, target_codes=['E-4-1'])[0]
    assert ledger.mapped['E-4-1']['value'] == dp.value == 248.5
    assert rows[0]['값'] == '248.5 TJ'
    assert graph.nodes[dp.representative_node_ids[0]].origin == 'dart'


def test_normalized_units_and_inferred_year_are_preserved():
    graph = EvidenceGraph('T', 'T'); graph.report_year = 2025
    graph.add_node(node('ocr', 2, 'MWh', period_inferred=True))
    ledger = extract_with_ssot(report(), graph)
    scores, _ = _build_risk_rows(graph, target_codes=['E-4-1'])
    dp = build_data_points(graph, scores, target_codes=['E-4-1'])[0]
    assert dp.value == ledger.mapped['E-4-1']['value'] == .0072
    assert dp.unit == 'TJ' and dp.period == 2025 and dp.d1_risk == 0
    assert 'period_inferred' in dp.confidence_flags and dp.verification != 'verified'


def test_scope12_sum_is_finalized_in_ledger_with_both_sources():
    graph = EvidenceGraph('T', 'T'); graph.report_year = 2025
    graph.add_node(node('s1', 20, 'tCO2eq', 'E-3-1', 'derived_from:gas'))
    graph.add_node(node('s2', 60, 'tCO2eq', 'E-3-1', 'derived_from:electricity'))
    ledger = extract_with_ssot(report(), graph)
    dp = build_data_points(graph, {}, target_codes=['E-3-1'])[0]
    assert ledger.mapped['E-3-1']['value'] == dp.value == 80
    assert set(dp.representative_node_ids) == {'s1', 's2'}
    assert len(dp.evidence_files) == 2 and dp.verification == 'estimated'
    assert detect_d1_numeric('온실가스 배출량 80 tCO2eq', 'E-3-1', graph).score == 0


def test_reported_total_has_priority_over_derived_sum():
    graph = EvidenceGraph('T', 'T'); graph.report_year = 2025
    graph.add_node(node('total', 100, 'tCO2eq', 'E-3-1'))
    graph.add_node(node('s1', 20, 'tCO2eq', 'E-3-1', 'derived_from:gas'))
    graph.add_node(node('s2', 60, 'tCO2eq', 'E-3-1', 'derived_from:electricity'))
    ledger = extract_with_ssot(report(), graph)
    dp = build_data_points(graph, {}, target_codes=['E-3-1'])[0]
    assert ledger.mapped['E-3-1']['value'] == dp.value == 100
    assert dp.representative_node_ids == ['total']


def test_declared_no_selection_cannot_be_resurrected():
    graph = EvidenceGraph('T', 'T'); graph.report_year = 2025
    graph.add_node(node('candidate', 61.2))
    graph.resolved_facts['E-4-1'] = None
    assert build_data_points(graph, {}, target_codes=['E-4-1']) == []
    assert _build_risk_rows(graph, target_codes=['E-4-1']) == ({}, [])


def test_decoy_cannot_justify_d1_pass():
    graph = EvidenceGraph('T', 'T'); graph.report_year = 2025
    graph.add_node(node('total', 248.5))
    decoy = node('component', 61.2, value_role='component')
    decoy.raw_text = '에너지 사용량 국내 사업장 61.2 TJ'
    graph.add_node(decoy)
    extract_with_ssot(report(), graph)
    assert detect_d1_numeric('에너지 사용량 61.2 TJ', 'E-4-1', graph).score > 0
    assert score_d1_numeric('에너지 사용량 61.2 TJ', graph).score > 0
