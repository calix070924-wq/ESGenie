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

    def test_table_ratio_rescues_weak_keyword_to_structured(self):
        """약한 키워드(1개)라도 표비율 신호가 붙으면 정형으로 승격된다.

        라이브 경로는 preview_text 없이 호출되므로 estimate_layout_features가
        자동 주입돼야 한다(이전엔 미배선이라 table_ratio가 항상 0이었음).
        """
        from unittest import mock
        from esgenie.ssot import ocr_router as R
        with mock.patch.object(R, "_quick_preview", return_value="전기요금"), \
             mock.patch.object(R, "estimate_layout_features",
                               return_value={"table_area_ratio": 0.5}) as est:
            d = route_document("x.pdf")
        assert est.called  # preview_text 미주입 → 자동 추정 호출
        assert d.channel is DocChannel.STRUCTURED
        assert "table_ratio=0.50" in d.rationale

    def test_estimate_layout_features_safe_fallbacks(self):
        """없는 파일·PDF 외 확장자는 빈 dict(신호 없음)로 안전 폴백."""
        from esgenie.ssot.ocr_router import estimate_layout_features
        assert estimate_layout_features("nope_does_not_exist.pdf") == {}
        assert estimate_layout_features("/tmp/foo.png") == {}

    def test_explicit_preview_text_skips_auto_estimate(self):
        """preview_text를 직접 준 호출은 자동 추정을 건너뛴다(결정성·비용 보존)."""
        from unittest import mock
        from esgenie.ssot import ocr_router as R
        with mock.patch.object(R, "estimate_layout_features") as est:
            route_document("dummy.pdf", preview_text="한국전력 전기요금 kWh")
        assert not est.called

    def test_ocr_preview_returns_empty_without_upstage_key(self):
        """Upstage 키 미설정 시 OCR 에스컬레이션은 빈 문자열(→ 파일명 폴백, 안전)."""
        from unittest import mock
        from esgenie.ssot import ocr_router as R
        with mock.patch.object(R, "_get_upstage_key", return_value=None):
            assert R._ocr_preview_first_page("scan.pdf") == ""

    def test_ocr_preview_uses_upstage_one_page(self):
        """스캔본 에스컬레이션은 Upstage DP를 1페이지(pages='1')로만 호출한다(과금 최소)."""
        from unittest import mock
        from esgenie.ssot import ocr_router as R
        with mock.patch.object(R, "_get_upstage_key", return_value="k"), \
             mock.patch.object(R, "_call_upstage_dp",
                               return_value=[{"text": "한국전력 전기요금 kWh"}]) as call:
            txt = R._ocr_preview_first_page("scan.pdf")
        assert "전기요금" in txt
        assert call.call_args.kwargs["pages"] == "1"
        assert call.call_args.kwargs["ocr_mode"] == "force"

    def test_quick_preview_escalates_when_no_embedded_text(self, tmp_path):
        """임베디드 텍스트 없음(스캔본/미설치) → _quick_preview가 OCR 에스컬레이션 텍스트 사용."""
        from unittest import mock
        from esgenie.ssot import ocr_router as R
        f = tmp_path / "scan_001.pdf"
        f.write_bytes(b"%PDF-1.4 fake")  # 텍스트레이어 없는 더미
        with mock.patch.object(R, "_ocr_preview_first_page",
                               return_value="한국전력 전기요금 사용전력량 kWh") as esc:
            txt = R._quick_preview(str(f))
        assert esc.called           # 임베디드 텍스트가 없어 에스컬레이션을 탐
        assert "전기요금" in txt    # 파일명 stem이 아닌 OCR 본문 신호를 반환

    def test_scanned_pdf_filename_meaningless_still_routes_structured(self):
        """파일명이 무의미해도 OCR 본문 신호로 정형 라우팅된다(A 회귀 가드)."""
        d = route_document("scan_001.pdf", preview_text="한국전력 전기요금 사용전력량 kWh")
        assert d.channel is DocChannel.STRUCTURED
        assert d.doc_type == "kepco_bill"

    def test_structured_mock_without_keys(self):
        """OCR 키 없음 → mock 추출이 동작하고 단위 있는 수치를 반환."""
        ext = extract_structured("한전고지서.pdf", doc_type="kepco_bill")
        assert ext.metrics
        assert ext.router_meta.get("mock") or any(m.value for m in ext.metrics)

    def test_unstructured_mock_without_keys(self):
        ext = extract_unstructured("회의록.pdf", doc_type="safety_minutes")
        assert ext.clauses or ext.metrics

    def test_policy_manual_mock_exposes_policy_codes(self):
        ext = ocr_router_mod._mock_unstructured("policy.pdf", "policy_manual")
        codes = {clause.kesg_code_guess for clause in ext.clauses}
        assert {"E-1-1", "E-1-2", "G-4-1"} <= codes

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


def test_recycle_mass_does_not_clobber_recycle_rate():
    """재활용량(톤, 코드없음)이 재활용 비율(%) E-6-2를 덮어쓰지 않는지 회귀 가드.

    OCR이 '재활용 비율 29.3%'를 정확히 뽑아도, '재활용량 5.4톤'(hint에 '재활용' 포함)이
    _HINT_TO_KESG의 부분문자열 매칭으로 E-6-2를 차지해 5.4로 덮어쓰던 버그(한울정밀 시연).
    """
    mk = lambda h, v, u, g: ExtractedMetric(
        metric_hint=h, value=v, unit=u, period="2026", kesg_code_guess=g)
    metrics = [
        mk("폐기물 처리량", 18400.0, "ton", "E-6-1"),
        mk("재활용량", 5.4, "ton", None),       # 보조수치 — E-6-2로 가면 안 됨
        mk("재활용 비율", 29.3, "%", "E-6-2"),   # 정답 비율
    ]
    ext = OcrExtraction(
        source_file="03_waste.pdf", channel=DocChannel.STRUCTURED,
        doc_type="waste_ledger", metrics=metrics, raw_text="", router_meta={})
    g = EvidenceGraph(corp_code="HANUL", corp_name="한울정밀")
    merge_ocr_extraction(g, ext, report_year=2026)

    e62 = [(n.value, n.unit) for n in g.nodes.values() if n.metric == "E-6-2"]
    assert e62 == [(29.3, "%")], f"E-6-2는 29.3%만 있어야 함, 실제={e62}"
    # 재활용량은 코드 없는 보조수치로만 남는다
    assert any(n.metric == "재활용량" and n.value == 5.4 for n in g.nodes.values())


def test_designated_waste_not_counted_as_total():
    """지정폐기물(하위 분류)이 E-6-1 총량 코드로 잡혀 노드가 중복되지 않는지 가드.

    hint '지정폐기물'은 '폐기물'(E-6-1) 부분문자열에 걸려 총량으로 둔갑하던 버그.
    추정코드가 E-6-1로 와도 _HINT_EXCLUDE로 차단되어 보조수치로만 남아야 한다.
    """
    mk = lambda h, v, u, g: ExtractedMetric(
        metric_hint=h, value=v, unit=u, period="2026", kesg_code_guess=g)
    metrics = [
        mk("폐기물 처리량", 18400.0, "ton", "E-6-1"),
        mk("지정 폐기물", 18.4, "ton", "E-6-1"),   # 추정 E-6-1로 와도 제외돼야
    ]
    g = EvidenceGraph(corp_code="HANUL", corp_name="한울정밀")
    merge_ocr_extraction(g, OcrExtraction(
        source_file="03_waste.pdf", channel=DocChannel.STRUCTURED,
        doc_type="waste_ledger", metrics=metrics, raw_text="", router_meta={}),
        report_year=2026)
    e61 = [n.value for n in g.nodes.values() if n.metric == "E-6-1"]
    assert e61 == [18400.0], f"E-6-1 총량 노드는 1개여야 함, 실제={e61}"


def test_pin_totals_from_raw_fixes_billing_cells():
    """청구서 본문 명시값으로 전력·가스·폐기물 대표수치를 결정적 교정(표 오집 교정)."""
    from esgenie.ssot.ocr_router import _pin_totals_from_raw, ExtractedMetric
    tok = lambda s: [{"text": s, "bbox": None, "page": 0}]
    mk = lambda v, u, g: ExtractedMetric(metric_hint="x", value=v, unit=u, period="", kesg_code_guess=g)
    # 전력: 전월지침(48,210) 오집 → 사용량 142,560kWh
    out = _pin_totals_from_raw([mk(48210.0, "kWh", "E-4-1")],
        tok("유효전력 48,210 50,586 60 142,560 전력량요금 (142,560kWh)"), "kepco_bill")
    assert [(m.value, m.unit) for m in out if m.kesg_code_guess == "E-4-1"] == [(142560.0, "kWh")]
    # 가스: 2.0 오추출 → 360,772MJ
    out = _pin_totals_from_raw([mk(2.0, "MJ", "E-4-1")],
        tok("사용요금 (360,772MJ × 20.13원)"), "gas_bill")
    assert [(m.value, m.unit) for m in out if m.kesg_code_guess == "E-4-1"] == [(360772.0, "MJ")]
    # 폐기물: 18400 ton(kg오인) → 총 위탁량 18,400kg = 18.4톤
    out = _pin_totals_from_raw([mk(18400.0, "ton", "E-6-1")],
        tok("합계 총 위탁량 18,400 재활용"), "waste_ledger")
    assert [(m.value, m.unit) for m in out if m.kesg_code_guess == "E-6-1"] == [(18.4, "ton")]


def test_scope12_sums_electricity_and_gas():
    """E-3-1(Scope1+2)은 전력 파생 + 가스 파생을 합산한다(코드당 1노드 선택 → 합산)."""
    from esgenie.ssot.audit_trace import build_data_points
    mk = lambda v, u: ExtractedMetric(metric_hint="E-4-1 본문확정", value=v, unit=u,
                                      period="2026", kesg_code_guess="E-4-1")
    g = EvidenceGraph(corp_code="HANUL", corp_name="한울정밀")
    merge_ocr_extraction(g, OcrExtraction(source_file="01.pdf", channel=DocChannel.STRUCTURED,
        doc_type="kepco_bill", metrics=[mk(142560.0, "kWh")], raw_text="", router_meta={}), report_year=2026)
    merge_ocr_extraction(g, OcrExtraction(source_file="02.pdf", channel=DocChannel.STRUCTURED,
        doc_type="gas_bill", metrics=[mk(360772.0, "MJ")], raw_text="", router_meta={}), report_year=2026)
    dps = {d.kesg_code: d for d in build_data_points(g, {}, target_codes=["E-3-1"])}
    assert dps["E-3-1"].value == 88.397, dps["E-3-1"].value
