"""환경 사전점검(doctor) + 임베딩 백엔드 가시화 테스트."""
from __future__ import annotations

from esgenie.doctor import check_data, check_keys, check_packages, diagnose
from esgenie.embeddings import backend_summary, embedding_backend


class TestBackendVisibility:
    def test_backend_is_known_value(self):
        assert embedding_backend() in ("sbert", "hash-fallback")

    def test_backend_summary_fields(self):
        s = backend_summary()
        assert {"embedding_backend", "embed_model", "faiss", "quality_note"} <= set(s)
        if s["embedding_backend"] != "sbert":
            assert "주의" in s["quality_note"]   # 폴백은 반드시 경고 문구 동반

    def test_audit_trace_records_backend(self):
        """audit_trace의 model_versions에 폴백 여부가 기록돼야 한다 (재현성)."""
        from esgenie.layer5_audit_trace import _model_versions
        mv = _model_versions()
        assert mv["embed_backend"] in ("sbert", "hash-fallback")
        assert mv["llm"]   # mock이면 "mock"


class TestDoctor:
    def test_check_packages_covers_required(self):
        rows = check_packages()
        names = {r["name"] for r in rows}
        assert {"numpy", "pandas", "sentence-transformers"} <= names
        # numpy는 테스트가 도는 환경이면 반드시 설치돼 있음
        numpy_row = next(r for r in rows if r["name"] == "numpy")
        assert numpy_row["status"] == "ok"

    def test_check_keys_reports_fallbacks(self):
        rows = check_keys()
        assert all(r["fallback"] for r in rows)

    def test_check_data_finds_samples(self):
        rows = check_data()
        sample = next(r for r in rows if r["name"] == "샘플 DART")
        assert sample["ok"] and sample["files"] >= 5

    def test_diagnose_verdict(self):
        r = diagnose(smoke=False)
        assert r["verdict"] in ("ok", "warn", "fail")
        # 필수 패키지가 모두 있으면 fail이 아니어야 함
        required_missing = [p for p in r["packages"]
                            if p["required"] and p["status"] == "missing"]
        if not required_missing:
            assert r["verdict"] != "fail"

    def test_diagnose_smoke(self):
        r = diagnose(smoke=True)
        assert r["smoke"]["ok"], r["smoke"].get("error")
        assert "커버리지" not in r["smoke"].get("error", "")
