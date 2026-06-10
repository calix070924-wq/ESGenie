"""그린워싱 벤치마크 데이터셋·하네스 테스트 (mock 모드)."""
from __future__ import annotations

import pytest

from esgenie.benchmark import (
    DetectorReport,
    CaseResult,
    load_benchmark,
    run_benchmark,
    format_report,
)


# ---- 데이터셋 무결성 -----------------------------------------------------------

class TestDataset:
    def test_loads_and_schema(self):
        bench = load_benchmark()
        assert len(bench["cases"]) >= 50
        for c in bench["cases"]:
            assert c["label"] in ("greenwash", "clean")
            assert c["sentence"].strip()
            assert c["id"] and c["category"]

    def test_unique_ids(self):
        bench = load_benchmark()
        ids = [c["id"] for c in bench["cases"]]
        assert len(ids) == len(set(ids))

    def test_label_balance(self):
        """극단적 클래스 불균형 방지 (한쪽이 30% 미만이면 안 됨)."""
        bench = load_benchmark()
        gw = sum(1 for c in bench["cases"] if c["label"] == "greenwash")
        ratio = gw / len(bench["cases"])
        assert 0.3 <= ratio <= 0.7


# ---- 지표 계산 -----------------------------------------------------------------

class TestMetrics:
    def _report(self) -> DetectorReport:
        rep = DetectorReport(name="t")
        # TP, FP, FN, TN
        rep.cases = [
            CaseResult("1", "c", "greenwash", True, 0.9),
            CaseResult("2", "c", "clean", True, 0.9),
            CaseResult("3", "c", "greenwash", False, 0.1),
            CaseResult("4", "c", "clean", False, 0.1),
        ]
        return rep

    def test_precision_recall_f1(self):
        m = self._report().metrics()
        assert m["precision"] == pytest.approx(0.5)
        assert m["recall"] == pytest.approx(0.5)
        assert m["f1"] == pytest.approx(0.5)
        assert m["accuracy"] == pytest.approx(0.5)
        assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (1, 1, 1, 1)

    def test_by_category(self):
        bc = self._report().by_category()
        assert bc["c"]["total"] == 4
        assert bc["c"]["correct"] == 2


# ---- E2E (mock) ----------------------------------------------------------------

class TestRunBenchmark:
    @pytest.fixture(scope="class")
    def reports(self):
        return run_benchmark(["rule", "hybrid", "llm_only"])

    def test_all_detectors_cover_all_cases(self, reports):
        n = len(load_benchmark()["cases"])
        for rep in reports.values():
            assert len(rep.cases) == n

    def test_hybrid_not_worse_than_rule_mock(self, reports):
        """mock 모드 기준: 하이브리드 F1 >= 룰 단독 F1 (회귀 가드)."""
        assert reports["hybrid"].metrics()["f1"] >= reports["rule"].metrics()["f1"]

    def test_hybrid_fixes_backed_modifier(self, reports):
        """룰의 구조적 약점(근거 수반 수식어 오탐)을 하이브리드가 해소하는지."""
        rule_bc = reports["rule"].by_category()["backed_modifier"]
        hyb_bc = reports["hybrid"].by_category()["backed_modifier"]
        assert hyb_bc["correct"] > rule_bc["correct"]

    def test_hybrid_cheaper_than_llm_only(self, reports):
        """트리거 게이트 덕에 하이브리드 LLM 호출 수 < 전수 호출."""
        assert 0 < reports["hybrid"].llm_calls < reports["llm_only"].llm_calls

    def test_rule_uses_no_llm(self, reports):
        assert reports["rule"].llm_calls == 0

    def test_format_report_renders(self, reports):
        text = format_report(reports, n_cases=len(load_benchmark()["cases"]))
        assert "Precision" in text and "카테고리별" in text
        assert "MOCK" in text   # mock 모드 경고 필수
