"""D1 missed detections: actual graph → finalized ledger → claim verification."""
from esgenie.dart_client import CompanyReport
from esgenie.ssot.evidence_graph import build_from_dart
from esgenie.ssot.ssot_pipeline import extract_with_ssot
from esgenie.layer3_detect import score_d1_numeric, extract_numeric_claims

CASES = [
    ('normal', '재생에너지 사용 비율은 31%이며 폐기물 재활용 비율은 92%였다.', 0, 2, 0),
    ('swapped', '재생에너지 사용 비율은 92%이며 폐기물 재활용 비율은 31%였다.', 1, 2, 0),
    ('single_error', '재생에너지 사용 비율은 92%였다.', 1, 1, 0),
    ('comma_error', '온실가스 배출량은 9,999 tCO2eq이다.', 1, 1, 0),
    ('plain_error', '온실가스 배출량은 9999 tCO2eq이다.', 1, 1, 0),
    ('missing', '용수 사용량은 100톤이다.', 0, 0, 1),
    ('normal_missing', '재생에너지 사용 비율은 31%이며 용수 사용량은 100톤이다.', 0, 1, 1),
    ('error_missing', '재생에너지 사용 비율은 92%이며 용수 사용량은 100톤이다.', 1, 1, 1),
]

def make_evidence():
    report = CompanyReport('D1', '수치 검증 합성', '', 2025, {}, {
        'E-4-2': {'value': 31, 'unit': '%'},
        'E-6-2': {'value': 92, 'unit': '%'},
        'E-3-1': {'value': 100, 'unit': 'tCO2eq'},
    }, [], 'synthetic')
    graph = build_from_dart(report)
    graph.report_year = 2025
    ledger = extract_with_ssot(report, graph)
    return report, graph, ledger

