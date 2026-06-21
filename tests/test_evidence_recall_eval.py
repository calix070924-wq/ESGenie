from __future__ import annotations

from scripts import evidence_recall_eval as eval_mod

from esgenie.ssot.evidence_graph import EvidenceGraph, EvidenceNode, TextNode


def _graph() -> EvidenceGraph:
    graph = EvidenceGraph("HANUL-SME", "한울정밀공업")
    graph.add_node(EvidenceNode(
        id="HANUL-SME_E-4-1_2026__ocr_01",
        metric="E-4-1",
        value=142560.0,
        unit="kWh",
        period=2026,
        source="ocr/kepco_bill",
        raw_text="사용전력량=142560kWh (01_전기요금청구서_2026-05.pdf)",
        origin="ocr_structured",
        source_file="01_전기요금청구서_2026-05.pdf",
    ))
    graph.add_node(EvidenceNode(
        id="HANUL-SME_E-3-1_2026__derived_01",
        metric="E-3-1",
        value=68.157,
        unit="tCO2eq",
        period=2026,
        source="derived_from:HANUL-SME_E-4-1_2026__ocr_01",
        raw_text="사용전력량=142560kWh (01_전기요금청구서_2026-05.pdf) → 배출계수 환산",
        origin="ocr_structured",
        source_file="01_전기요금청구서_2026-05.pdf",
    ))
    graph.add_node(EvidenceNode(
        id="HANUL-SME_E-6-1_2026__ocr_03",
        metric="E-6-1",
        value=18400.0,
        unit="kg",
        period=2026,
        source="ocr/waste_ledger",
        raw_text="폐기물처리량=18400kg (03_사업장폐기물_위탁처리명세_2026-04.pdf)",
        origin="ocr_structured",
        source_file="03_사업장폐기물_위탁처리명세_2026-04.pdf",
    ))
    graph.add_node(EvidenceNode(
        id="HANUL-SME_E-6-2_2026__ocr_03",
        metric="E-6-2",
        value=29.3,
        unit="%",
        period=2026,
        source="ocr/waste_ledger",
        raw_text="재활용률=29.3% (03_사업장폐기물_위탁처리명세_2026-04.pdf)",
        origin="ocr_structured",
        source_file="03_사업장폐기물_위탁처리명세_2026-04.pdf",
    ))
    graph.add_text_node(TextNode(
        id="HANUL-SME_TXT_0001",
        section="환경경영 방침",
        text="회사는 환경법규 준수와 환경영향 최소화를 기본방침으로 한다.",
        kesg_code="E-1-1",
        source_file="04_사내_환경ESG경영방침_규정.pdf",
        page=1,
    ))
    graph.add_text_node(TextNode(
        id="HANUL-SME_TXT_0002",
        section="환경경영 추진체계",
        text="주관 부서 ESG경영팀 / 환경안전팀",
        kesg_code="E-1-2",
        source_file="04_사내_환경ESG경영방침_규정.pdf",
        page=1,
    ))
    graph.add_text_node(TextNode(
        id="HANUL-SME_TXT_0003",
        section="윤리경영",
        text="회사는 공정·윤리 원칙을 준수한다.",
        kesg_code="G-4-1",
        source_file="04_사내_환경ESG경영방침_규정.pdf",
        page=2,
    ))
    return graph


def _gold() -> dict:
    return {
        "report_year": 2026,
        "gold": [
            {
                "code": "E-4-1",
                "name": "에너지 사용량",
                "target_kind": "metric_node",
                "evidence_files": ["전기요금"],
                "relevant_terms": ["사용전력량"],
                "rationale": "에너지 계량 노드",
            },
            {
                "code": "E-3-1",
                "name": "온실가스 배출량",
                "target_kind": "derived_node",
                "depends_on": ["E-4-1"],
                "evidence_files": ["전기요금"],
                "relevant_terms": ["배출계수 환산"],
                "rationale": "파생 노드",
            },
            {
                "code": "E-6-1",
                "name": "폐기물 배출량",
                "target_kind": "metric_node",
                "evidence_files": ["폐기물"],
                "relevant_terms": ["폐기물처리량"],
                "rationale": "폐기물 총배출량",
            },
            {
                "code": "E-6-2",
                "name": "폐기물 재활용률",
                "target_kind": "metric_node",
                "evidence_files": ["폐기물"],
                "relevant_terms": ["재활용률"],
                "rationale": "재활용률",
            },
            {
                "code": "E-1-1",
                "name": "환경경영 목표 수립",
                "target_kind": "text_node",
                "evidence_files": ["환경ESG경영방침"],
                "relevant_terms": ["환경법규 준수", "기본방침"],
                "rationale": "환경방침 조항",
            },
            {
                "code": "E-1-2",
                "name": "환경경영 추진체계",
                "target_kind": "text_node",
                "evidence_files": ["환경ESG경영방침"],
                "relevant_terms": ["ESG경영팀", "환경안전팀"],
                "rationale": "환경조직 조항",
            },
            {
                "code": "G-4-1",
                "name": "윤리규범 위반사항 공시",
                "target_kind": "text_node",
                "evidence_files": ["환경ESG경영방침"],
                "relevant_terms": ["윤리", "공정·윤리"],
                "rationale": "윤리 조항",
            },
        ],
    }


def test_measure_supports_text_nodes_and_derived_dependencies():
    rows, agg = eval_mod.measure(_graph(), _gold())

    assert agg["n"] == 7
    assert agg["n_extracted"] == 7
    assert agg["before"][0] == 1.0
    assert agg["after"][0] == 1.0

    row_by_code = {row["code"]: row for row in rows}
    assert row_by_code["E-3-1"]["target_kind"] == "derived_node"
    assert row_by_code["E-3-1"]["depends_on"] == ["E-4-1"]
    assert row_by_code["E-1-1"]["hit_before"] == 1
    assert row_by_code["E-1-2"]["hit_after"] == 1
    assert row_by_code["G-4-1"]["n_relevant"] == 1


def test_report_surfaces_kind_dependency_and_rationale():
    graph = _graph()
    rows, agg = eval_mod.measure(graph, _gold())
    text = eval_mod.report(
        rows,
        agg,
        mode="REAL-KEY (strict)",
        engines=[("04_사내_환경ESG경영방침_규정.pdf", "gpt-4.1-mini-text")],
        n_nodes=len(graph.nodes),
        n_text_nodes=len(graph.text_nodes),
    )

    assert "TextNode 수: 3" in text
    assert "| E-3-1 | 온실가스 배출량 | 파생 |" in text
    assert "파생 ← E-4-1" in text
    assert "환경방침 조항" in text
