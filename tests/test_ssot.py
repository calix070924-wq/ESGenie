"""esgenie.ssot (구 v15) 통합 테스트 — SSOT 그래프·OCR 라우터·검출기·브리지."""
from __future__ import annotations

import json

import pytest

from esgenie.dart_client import load_report
from esgenie.schemas import AxisScore
from esgenie.ssot import detector_5axis as det
from esgenie.ssot.audit_trace import build_audit_trace_v15, build_data_points
from esgenie.ssot.evidence_graph import (
    EvidenceGraph,
    build_from_dart,
    build_unified_graph,
    merge_ocr_extraction,
)
from esgenie.ssot.ocr_router import (
    DocChannel,
    ExtractedClause,
    ExtractedMetric,
    OcrExtraction,
    extract_structured,
    extract_unstructured,
    route_document,
)
from esgenie.ssot import ocr_router as ocr_router_mod
from esgenie.ssot.ssot_pipeline import build_rag_with_ssot, extract_local_with_ssot, extract_with_ssot, ssot_summary


# ---- 헬퍼 ----------------------------------------------------------------------

def _kepco_extraction(value: float = 128400.0) -> OcrExtraction:
    return OcrExtraction(
        source_file="한전고지서_2025_12.pdf",
        channel=DocChannel.STRUCTURED,
        doc_type="kepco_bill",
        metrics=[ExtractedMetric(
            metric_hint="사용전력량", value=value, unit="kWh",
            period="2025-12", kesg_code_guess="E-4-1", confidence=0.93,
        )],
    )


def _policy_extraction() -> OcrExtraction:
    return OcrExtraction(
        source_file="사내규정집.pdf",
        channel=DocChannel.UNSTRUCTURED,
        doc_type="policy_manual",
        clauses=[ExtractedClause(
            section="안전보건 방침", text="회사는 산업안전보건위원회를 분기별로 운영한다.",
            kesg_code_guess="S-4-1", page=3,
        )],
    )


class FakeLLM:
    """결정적 JSON 응답을 주는 판정용 LLM 대역."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    def complete(self, **kwargs):
        from esgenie.llm import LLMResponse
        self.calls += 1
        return LLMResponse(content=json.dumps(self.payload, ensure_ascii=False),
                           used_mock=True, meta={})


# ---- 임포트 단일화 ---------------------------------------------------------------

class TestUnifiedSchemas:
    def test_axis_score_is_shared(self):
        """v15 통합 후 AxisScore는 esgenie.schemas 단일 정의여야 한다."""
        assert det.AxisScore is AxisScore

    def test_no_v15_scaffold_references(self):
        import esgenie.ssot.ssot_pipeline as sp
        import inspect
        src = inspect.getsource(sp)
        assert "esgenie_v15" not in src


# ---- Evidence Graph (SSOT) -------------------------------------------------------

class TestSsotGraph:
    @pytest.fixture(scope="class")
    def sme_report(self):
        return load_report("SME001")

    def test_build_from_dart_origin(self, sme_report):
        g = build_from_dart(sme_report)
        assert g.nodes, "DART 노드가 있어야 함"
        assert all(n.origin == "dart" and n.confidence == 1.0 for n in g.nodes.values())

    def test_merge_ocr_adds_node_with_source_file(self, sme_report):
        g = build_from_dart(sme_report)
        merge_ocr_extraction(g, _kepco_extraction(), report_year=sme_report.report_year)
        ocr_nodes = [n for n in g.nodes.values() if n.origin == "ocr_structured"]
        assert ocr_nodes
        assert any(n.source_file == "한전고지서_2025_12.pdf" for n in ocr_nodes)

    def test_derived_emission_node(self, sme_report):
        """전력(kWh) 노드 → E-3-1 tCO2eq 파생 노드 자동 생성."""
        g = build_from_dart(sme_report)
        merge_ocr_extraction(g, _kepco_extraction(128400.0), report_year=2025)
        derived = [n for n in g.nodes.values() if "derived" in n.id and n.metric == "E-3-1"]
        assert derived
        # 128,400 kWh × 0.4781/1000 ≈ 61.39 tCO2eq
        assert derived[0].value == pytest.approx(61.39, abs=0.1)
        assert derived[0].unit == "tCO2eq"

    def test_cross_check_edge_on_same_metric_period(self):
        g = EvidenceGraph("SME001", "테스트")
        from esgenie.ssot.evidence_graph import EvidenceNode
        g.add_node(EvidenceNode(
            id="SME001_E-4-1_2025", metric="E-4-1", value=130000, unit="kWh",
            period=2025, source="dart", origin="dart",
        ))
        merge_ocr_extraction(g, _kepco_extraction(128400.0), report_year=2025)
        xc = [e for e in g.edges if e.edge_type == "cross_check"]
        assert xc, "동일 metric/period DART↔OCR → cross_check 엣지 생성"
        assert "오차" in xc[0].detail

    def test_text_node_from_clause(self):
        g = EvidenceGraph("SME001", "테스트")
        merge_ocr_extraction(g, _policy_extraction(), report_year=2025)
        assert len(g.text_nodes) == 1
        t = next(iter(g.text_nodes.values()))
        assert t.kesg_code == "S-4-1"
        assert t.source_file == "사내규정집.pdf"

    def test_build_unified_graph_without_dart(self):
        g = build_unified_graph(None, [_kepco_extraction()], corp_code="LOCAL",
                                corp_name="로컬", report_year=2025)
        assert g.corp_code == "LOCAL"
        assert any(n.origin == "ocr_structured" for n in g.nodes.values())

    def test_same_metric_from_multiple_files_keeps_distinct_nodes(self):
        g = EvidenceGraph("LOCAL", "로컬")
        merge_ocr_extraction(g, OcrExtraction(
            source_file="전기1.pdf",
            channel=DocChannel.STRUCTURED,
            doc_type="kepco_bill",
            metrics=[ExtractedMetric(
                metric_hint="사용전력량", value=100.0, unit="kWh",
                period="2025-01", kesg_code_guess="E-4-1", confidence=0.9,
            )],
        ), report_year=2025)
        merge_ocr_extraction(g, OcrExtraction(
            source_file="전기2.pdf",
            channel=DocChannel.STRUCTURED,
            doc_type="kepco_bill",
            metrics=[ExtractedMetric(
                metric_hint="사용전력량", value=200.0, unit="kWh",
                period="2025-01", kesg_code_guess="E-4-1", confidence=0.9,
            )],
        ), report_year=2025)
        e41_nodes = [n for n in g.nodes.values() if n.metric == "E-4-1"]
        assert len(e41_nodes) == 2
        assert len({n.id for n in e41_nodes}) == 2

    def test_search_nodes_v10_compat(self, sme_report):
        """layer1이 쓰는 search_nodes(keywords, period) 인터페이스 호환."""
        g = build_from_dart(sme_report)
        merge_ocr_extraction(g, _kepco_extraction(), report_year=sme_report.report_year)
        hits = g.search_nodes(keywords=["E-4-1"], period=sme_report.report_year)
        assert hits


# ---- OCR 라우터 -------------------------------------------------------------------

class TestOcrRouter:
    def test_route_structured_by_preview(self):
        d = route_document("dummy.pdf", preview_text="한국전력 전기요금 청구서 사용전력량 128,400 kWh")
        assert d.channel is DocChannel.STRUCTURED
        assert d.doc_type == "kepco_bill"

    def test_route_unstructured_by_preview(self):
        d = route_document("dummy.pdf", preview_text="산업안전보건위원회 회의록 — 안건 심의, 근로자 대표 참석")
        assert d.channel is DocChannel.UNSTRUCTURED
        assert d.doc_type == "safety_minutes"

    def test_route_ambiguous_falls_back_to_vlm(self):
        d = route_document("unknown.pdf", preview_text="별 내용 없는 문서")
        assert d.channel is DocChannel.UNSTRUCTURED
        assert "폴백" in d.rationale

    def test_structured_mock_without_keys(self):
        """CLOVA 키 없음 → mock 추출이 동작하고 단위 있는 수치를 반환."""
        ext = extract_structured("한전고지서.pdf", doc_type="kepco_bill")
        assert ext.metrics
        assert ext.router_meta.get("mock") or any(m.value for m in ext.metrics)

    def test_unstructured_mock_without_keys(self):
        ext = extract_unstructured("회의록.pdf", doc_type="safety_minutes")
        assert ext.clauses or ext.metrics

    def test_unstructured_text_fallback_promotes_policy_clauses(self, monkeypatch):
        class _EmptyLLM:
            def complete(self, **kwargs):
                from esgenie.llm import LLMResponse
                return LLMResponse(content="{}", used_mock=False, meta={"model": "stub"})

        monkeypatch.setattr(ocr_router_mod, "LLMClient", lambda: _EmptyLLM(), raising=False)
        ext = ocr_router_mod._extract_unstructured_text(
            "policy.pdf",
            doc_type="policy_manual",
            raw_text=(
                "환경·ESG 경영방침 규정\n"
                "주관 부서 ESG경영팀 / 환경안전팀\n"
                "회사는 환경법규 준수와 환경영향 최소화를 기본방침으로 한다.\n"
                "안전보건 체계를 운영하고 위험성평가를 연 1회 이상 실시한다.\n"
            ),
        )
        codes = {clause.kesg_code_guess for clause in ext.clauses}
        assert {"E-1-1", "E-1-2", "S-4-1"} <= codes


# ---- D1 / P축 검출기 ---------------------------------------------------------------

class TestDetector:
    def _graph_with_e41(self, value: float = 128400.0) -> EvidenceGraph:
        g = EvidenceGraph("LOCAL", "로컬")
        merge_ocr_extraction(g, _kepco_extraction(value), report_year=2025)
        return g

    def test_d1_no_claims(self):
        g = self._graph_with_e41()
        r = det.detect_d1_numeric("수치가 없는 문장입니다.", "E-4-1", g)
        assert r.score == 0.0

    def test_d1_no_kesg_code_is_risky(self):
        g = self._graph_with_e41()
        r = det.detect_d1_numeric("사용량은 128,400 kWh였습니다.", None, g)
        assert r.score >= 0.5

    def test_d1_no_evidence_nodes_high_risk(self):
        g = EvidenceGraph("LOCAL", "로컬")
        r = det.detect_d1_numeric("사용량은 128,400 kWh였습니다.", "E-4-1", g)
        assert r.score >= 0.9

    def test_d1_matching_value_low_risk(self):
        g = self._graph_with_e41(128400.0)
        r = det.detect_d1_numeric("당해 사용전력량은 128,400 kWh입니다.", "E-4-1", g)
        assert r.score < 0.5
        assert "한전고지서_2025_12.pdf" in r.evidence  # 증빙 파일 추적

    def test_detect_risk_axes_aggregate(self):
        g = self._graph_with_e41()
        out = det.detect_risk_axes("당해 사용전력량은 128,400 kWh입니다.", "E-4-1", g)
        assert set(out) == {"D1", "D2", "D3", "D5", "aggregate"}
        assert 0.0 <= out["aggregate"].score <= 1.0

    def test_policy_audit_no_documents_all_missing(self):
        g = EvidenceGraph("LOCAL", "로컬")
        res = det.audit_policy_documents("S-4-1", g, FakeLLM({}))
        assert res.passed is False
        assert all(f.status == "missing" for f in res.findings)

    def test_policy_audit_with_llm_findings(self):
        g = EvidenceGraph("LOCAL", "로컬")
        merge_ocr_extraction(g, _policy_extraction(), report_year=2025)
        fake = FakeLLM({
            "findings": [{
                "requirement": "근로자 대표 참여", "status": "missing",
                "evidence_quote": None, "gap_comment": "문구 없음",
                "suggested_fix": "근로자 대표 참여 조항 추가",
            }],
            "overall": {"pass": False},
        })
        # S-4-1은 policy_extraction의 kesg_code_guess와 일치해야 텍스트 노드가 잡힘
        res = det.audit_policy_documents("S-4-1", g, fake)
        assert fake.calls == 1
        assert res.findings[0].status == "missing"
        assert res.source_files == ["사내규정집.pdf"]


# ---- L1/L2 브리지 -------------------------------------------------------------------

class TestSsotPipeline:
    @pytest.fixture(scope="class")
    def setup(self):
        report = load_report("SME001")
        graph = build_unified_graph(
            report, [_kepco_extraction(), _policy_extraction()],
            corp_code=report.corp_code, corp_name=report.corp_name,
            report_year=report.report_year,
        )
        return report, graph

    def test_extract_with_ssot_attaches_ocr_evidence(self, setup):
        report, graph = setup
        res = extract_with_ssot(report, graph)
        assert res.profile == "sme"   # SME 코드 → 자동 sme 프로파일
        e41 = res.mapped.get("E-4-1")
        if e41 is not None:
            assert any("ocr" in nid for nid in e41["evidence_node_ids"])

    def test_extract_with_ssot_promotes_text_node_into_presence_item(self, setup):
        report, graph = setup
        res = extract_with_ssot(report, graph)
        s41 = res.mapped.get("S-4-1")
        assert s41 is not None
        assert s41["value"] == "문서 조항 확인"
        assert any(nid.startswith(f"{report.corp_code}_TXT_") for nid in s41["evidence_node_ids"])
        assert "S-4-1" not in res.missing

    def test_build_rag_with_ssot_extends_index(self, setup):
        report, graph = setup
        from esgenie.layer2_rag import HybridRAG
        rag = HybridRAG()
        build_rag_with_ssot(rag, report, graph)
        docs = getattr(rag.corp_index, "_docs", [])
        assert any(d.meta.get("source") == "ssot_ocr" for d in docs)
        assert any(d.meta.get("source") == "ssot_text" for d in docs)

    def test_ssot_summary(self, setup):
        _, graph = setup
        s = ssot_summary(graph)
        assert s["total_nodes"] == len(graph.nodes)
        assert "ocr_structured" in s["by_origin"]

    def test_extract_local_with_ssot_maps_presence_items_without_dart(self):
        graph = build_unified_graph(
            None,
            [
                _kepco_extraction(),
                OcrExtraction(
                    source_file="환경방침서.pdf",
                    channel=DocChannel.UNSTRUCTURED,
                    doc_type="policy_manual",
                    clauses=[
                        ExtractedClause(
                            section="환경경영 방침",
                            text="회사는 환경법규 준수와 지속적 개선 목표를 수립한다.",
                            kesg_code_guess="E-1-1",
                            page=1,
                        ),
                        ExtractedClause(
                            section="환경경영 추진체계",
                            text="주관 부서 ESG경영팀 / 환경안전팀이 체계를 운영한다.",
                            kesg_code_guess="E-1-2",
                            page=1,
                        ),
                        ExtractedClause(
                            section="안전보건 체계",
                            text="안전보건 체계를 운영하고 위험성평가를 정례화한다.",
                            kesg_code_guess="S-4-1",
                            page=2,
                        ),
                    ],
                ),
            ],
            corp_code="LOCAL",
            corp_name="로컬기업",
            report_year=2025,
        )
        res = extract_local_with_ssot(
            graph,
            corp_code="LOCAL",
            corp_name="로컬기업",
            report_year=2025,
            industry="자동차부품",
            profile="sme",
        )
        for code in ("E-1-1", "E-1-2", "S-4-1"):
            assert code in res.mapped
            assert res.mapped[code]["value"] == "문서 조항 확인"
            assert any(nid.startswith("LOCAL_TXT_") for nid in res.mapped[code]["evidence_node_ids"])
            assert code not in res.missing


# ---- 실사 산출물 (audit_trace_v15) ---------------------------------------------------

class TestAuditTraceV15:
    def test_data_points_dart_first_and_links(self):
        report = load_report("SME001")
        graph = build_unified_graph(
            report, [_kepco_extraction()],
            corp_code=report.corp_code, corp_name=report.corp_name,
            report_year=report.report_year,
        )
        pts = build_data_points(graph, {"E-4-1": 0.0}, target_codes=["E-4-1"])
        assert pts and pts[0].kesg_code == "E-4-1"
        assert pts[0].evidence_files, "증빙 링크가 붙어야 함"

    def test_trace_summary_counts(self):
        from esgenie.ssot.audit_trace import DataPoint
        dps = [DataPoint("E-4-1", "에너지", 1.0, "kWh", 2025, 0.9, "verified", 0.0)]
        trace = build_audit_trace_v15("SME001", "테스트", dps, [])
        d = trace.to_dict()
        assert d["schema_version"] == "v15"
        assert d["summary"]["verified_count"] == 1
