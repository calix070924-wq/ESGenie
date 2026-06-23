"""S(사회) 영역 수치 추출 전용 단위 테스트.

검증 항목:
- 4가지 정규식 패턴 (인원/비율/금액/건수) 개별 매칭
- 레이블 → K-ESG 코드 매핑 정확성
- 금액 단위 정규화 (만 원 → 원)
- 중복 방지 (kesg_data에 이미 존재하는 노드는 건너뜀)
- _METRIC_KEYWORDS S 영역 키워드 coverage
- SSOT 통합 경로 (build_from_dart → S 노드 전달)
"""
from __future__ import annotations

import pytest

from esgenie.dart_client import CompanyReport, load_sample_report
from esgenie.layer0_evidence_graph import (
    EvidenceGraph,
    EvidenceNode,
    _S_HEADCOUNT_PATTERN,
    _S_RATIO_PATTERN,
    _S_MONEY_PATTERN,
    _S_COUNT_PATTERN,
    _S_LABEL_TO_KESG,
    _METRIC_KEYWORDS,
    _extract_social_nodes,
    build_evidence_graph,
)


# ---- 정규식 패턴 직접 매칭 테스트 -----------------------------------------------

class TestHeadcountPattern:
    @pytest.mark.parametrize("text,label,value", [
        ("신규 채용 인원은 320명", "신규 채용", "320"),
        ("정규직 2,500명", "정규직", "2,500"),
        ("비정규직 180명으로 감소", "비정규직", "180"),
        ("장애인 고용 인원 45명", "장애인 고용", "45"),
        ("여성 인력 1,200명", "여성 인력", "1,200"),
        ("여성 임직원 890명", "여성 임직원", "890"),
        ("신규채용 500명", "신규채용", "500"),
    ])
    def test_matches(self, text: str, label: str, value: str) -> None:
        m = _S_HEADCOUNT_PATTERN.search(text)
        assert m is not None, f"'{text}' 매칭 실패"
        assert m.group("label").replace(" ", "") == label.replace(" ", "")
        assert m.group("value") == value


class TestRatioPattern:
    @pytest.mark.parametrize("text,label,value", [
        ("정규직 비율 97.3%", "정규직 비율", "97.3"),
        ("이직률 2.4%", "이직률", "2.4"),
        ("재해율 0.098%", "재해율", "0.098"),
        ("여성 비율은 24.8%", "여성 비율", "24.8"),
        ("장애인 고용률 3.2%", "장애인 고용률", "3.2"),
        ("노조 가입률 68.5%", "노조 가입률", "68.5"),
        ("봉사 참여율은 42.3%", "봉사 참여율", "42.3"),
        ("LTIFR 0.32%", "LTIFR", "0.32"),
    ])
    def test_matches(self, text: str, label: str, value: str) -> None:
        m = _S_RATIO_PATTERN.search(text)
        assert m is not None, f"'{text}' 매칭 실패"
        assert m.group("value") == value


class TestMoneyPattern:
    @pytest.mark.parametrize("text,label,value,unit", [
        ("교육훈련비 185만 원", "교육훈련비", "185", "만 원"),
        ("1인당 교육비 200만 원", "1인당 교육비", "200", "만 원"),
        ("복리후생비 425만 원", "복리후생비", "425", "만 원"),
        ("1인당 복리후생 380만 원", "1인당 복리후생", "380", "만 원"),
    ])
    def test_matches(self, text: str, label: str, value: str, unit: str) -> None:
        m = _S_MONEY_PATTERN.search(text)
        assert m is not None, f"'{text}' 매칭 실패"
        assert m.group("value") == value
        assert m.group("unit") == unit


class TestCountPattern:
    @pytest.mark.parametrize("text,label,value", [
        ("개인정보 유출 건수는 0건", "개인정보 유출", "0"),
        ("개인정보 침해 2건", "개인정보 침해", "2"),
        ("사회 법규 위반 3건", "사회 법규 위반", "3"),
        ("노동 법규 위반 1건", "노동 법규 위반", "1"),
    ])
    def test_matches(self, text: str, label: str, value: str) -> None:
        m = _S_COUNT_PATTERN.search(text)
        assert m is not None, f"'{text}' 매칭 실패"
        assert m.group("value") == value


# ---- 레이블 → K-ESG 코드 매핑 테스트 -------------------------------------------

class TestLabelToKesgMapping:
    @pytest.mark.parametrize("label,expected_code", [
        ("신규 채용", "S-2-1"),
        ("정규직", "S-2-2"),
        ("이직률", "S-2-3"),
        ("교육훈련비", "S-2-4"),
        ("복리후생비", "S-2-5"),
        ("노조 가입률", "S-2-6"),
        ("여성 비율", "S-3-1"),
        ("여성 급여 비율", "S-3-2"),
        ("장애인 고용률", "S-3-3"),
        ("재해율", "S-4-2"),
        ("봉사 참여율", "S-7-2"),
        ("개인정보 침해", "S-8-2"),
        ("사회 법규 위반", "S-9-1"),
    ])
    def test_mapping_exists(self, label: str, expected_code: str) -> None:
        assert label in _S_LABEL_TO_KESG
        assert _S_LABEL_TO_KESG[label] == expected_code

    def test_all_labels_map_to_valid_s_codes(self) -> None:
        for label, code in _S_LABEL_TO_KESG.items():
            assert code.startswith("S-"), f"'{label}' → '{code}' 는 S 코드가 아님"


# ---- _METRIC_KEYWORDS S 영역 커버리지 테스트 ------------------------------------

class TestMetricKeywords:
    def test_s_codes_have_keywords(self) -> None:
        s_codes = {code for code in _METRIC_KEYWORDS if code.startswith("S-")}
        expected_s_codes = {"S-2-1", "S-2-2", "S-2-3", "S-2-4", "S-2-5", "S-2-6",
                           "S-3-1", "S-3-2", "S-3-3", "S-4-1", "S-4-2",
                           "S-7-2", "S-8-2", "S-9-1"}
        assert expected_s_codes.issubset(s_codes), (
            f"누락 코드: {expected_s_codes - s_codes}"
        )

    def test_each_s_keyword_list_nonempty(self) -> None:
        for code, keywords in _METRIC_KEYWORDS.items():
            if code.startswith("S-"):
                assert len(keywords) > 0, f"{code}: 키워드 비어있음"


# ---- 금액 단위 정규화 테스트 ----------------------------------------------------

class TestUnitNormalize:
    def test_man_won(self) -> None:
        from esgenie.layer0_evidence_graph import _extract_social_nodes
        report = _make_report(["교육훈련비 185만 원"])
        graph = EvidenceGraph(corp_code="TEST", corp_name="테스트")
        nodes = _extract_social_nodes(graph, report)
        money_nodes = [n for n in nodes if n.unit == "원"]
        assert len(money_nodes) >= 1
        assert money_nodes[0].value == pytest.approx(185 * 10_000)


# ---- 중복 방지 테스트 -----------------------------------------------------------

class TestDeduplication:
    def test_existing_kesg_node_not_duplicated(self) -> None:
        """kesg_data에 이미 S-8-2 노드가 있으면 텍스트 추출 노드를 만들지 않음."""
        report = load_sample_report("005930")
        graph = build_evidence_graph(report)
        s82_nodes = [n for n in graph.nodes.values() if n.metric == "S-8-2"]
        # kesg_data에서 하나, 텍스트에서도 매칭 가능하지만 중복 방지로 1개만
        assert len(s82_nodes) == 1
        assert s82_nodes[0].source == "kesg_data/S-8-2"

    def test_text_node_created_when_no_kesg_data(self) -> None:
        """kesg_data에 없는 S 코드는 텍스트에서 추출해야 함."""
        report = _make_report(["봉사 참여율은 42.3%이다"])
        graph = EvidenceGraph(corp_code="TEST", corp_name="테스트")
        nodes = _extract_social_nodes(graph, report)
        assert any(n.metric == "S-7-2" for n in nodes)


# ---- 전체 통합 테스트 (샘플 3사) ------------------------------------------------

CORP_CODES = ["005930", "005380", "005490"]


@pytest.mark.parametrize("corp_code", CORP_CODES)
def test_social_nodes_exist(corp_code: str) -> None:
    """모든 샘플 회사에서 S 노드가 1개 이상 생성되어야 함."""
    report = load_sample_report(corp_code)
    graph = build_evidence_graph(report)
    s_nodes = [n for n in graph.nodes.values() if n.metric.startswith("S-")]
    assert len(s_nodes) >= 1


@pytest.mark.parametrize("corp_code", CORP_CODES)
def test_social_nodes_schema(corp_code: str) -> None:
    """S 노드 스키마 검증."""
    report = load_sample_report(corp_code)
    graph = build_evidence_graph(report)
    for node in graph.nodes.values():
        if not node.metric.startswith("S-"):
            continue
        assert isinstance(node.value, float)
        assert node.unit in ("명", "%", "원", "원/인", "건")
        assert node.period == report.report_year or "_inferred" in node.id
        assert node.source.startswith(("kesg_data/", "raw_text/", "dart_table/", "html_table/"))


@pytest.mark.parametrize("corp_code", CORP_CODES)
def test_social_text_extraction_node_id_format(corp_code: str) -> None:
    """텍스트 추출 S 노드의 ID 포맷 검증."""
    report = load_sample_report(corp_code)
    graph = build_evidence_graph(report)
    for node in graph.nodes.values():
        if "_social_text_" in node.id:
            assert node.source.endswith("_social")
            assert node.metric.startswith("S-")


# ---- SSOT 통합 경로 테스트 -----------------------------------------------------

def test_ssot_build_from_dart_includes_s_nodes() -> None:
    """SSOT의 build_from_dart가 S 노드를 올바르게 전달하는지 확인."""
    from esgenie.ssot.evidence_graph import build_from_dart
    report = load_sample_report("005930")
    ssot_graph = build_from_dart(report)
    s_nodes = [n for n in ssot_graph.nodes.values() if n.metric.startswith("S-")]
    assert len(s_nodes) >= 5
    for n in s_nodes:
        assert n.origin == "dart"
        assert n.confidence == 1.0


def test_ssot_s_node_values_match_v10() -> None:
    """SSOT S 노드 값이 v10 그래프와 일치하는지 확인."""
    from esgenie.ssot.evidence_graph import build_from_dart
    report = load_sample_report("005930")
    v10_graph = build_evidence_graph(report)
    ssot_graph = build_from_dart(report)

    v10_s = {n.id: n.value for n in v10_graph.nodes.values() if n.metric.startswith("S-")}
    ssot_s = {n.id: n.value for n in ssot_graph.nodes.values() if n.metric.startswith("S-")}

    for node_id, v10_val in v10_s.items():
        assert node_id in ssot_s, f"SSOT에 {node_id} 누락"
        assert ssot_s[node_id] == pytest.approx(v10_val)


# ---- 헬퍼 -----------------------------------------------------------------------

def _make_report(snippets: list[str]) -> CompanyReport:
    return CompanyReport(
        corp_code="TEST",
        corp_name="테스트기업",
        industry="제조업",
        report_year=2024,
        financials={},
        kesg_data={},
        raw_text_snippets=snippets,
        source="test",
    )
