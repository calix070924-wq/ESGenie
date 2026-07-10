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


class TestForceMockOverridesKeys:
    """force_mock=1은 실 키가 있어도 모든 외부 연동을 mock으로 강제해야 한다.

    테스트 결정성 보장 — .env에 실 키가 존재하는 환경에서도 mock 경로로만 돌게 한다.
    use_mock_llm/use_mock_dart 와 ocr_router의 키 게터가 force_mock을 대칭으로 존중해야 한다.
    """

    def _settings(self, **overrides):
        from esgenie.config import Settings
        base = dict(
            openai_api_key="real-openai",
            anthropic_api_key="real-anthropic",
            dart_api_key="real-dart",
            openai_model="gpt-4.1-mini",
            anthropic_model="claude-haiku-4-5-20251001",
            embed_model="paraphrase-multilingual-MiniLM-L12-v2",
        )
        base.update(overrides)
        return Settings(**base)

    def test_use_mock_dart_respects_force_mock(self):
        s = self._settings(force_mock=True)
        assert s.use_mock_dart is True

    def test_use_mock_llm_respects_force_mock(self):
        s = self._settings(force_mock=True)
        assert s.use_mock_llm is True

    def test_upstage_key_none_under_force_mock(self, monkeypatch):
        """conftest가 ESGENIE_FORCE_MOCK=1을 걸어 SETTINGS.force_mock=True 이므로,
        실 UPSTAGE_API_KEY가 있어도 _get_upstage_key()는 None이어야 한다."""
        import esgenie.ssot.ocr_router as router
        monkeypatch.setenv("UPSTAGE_API_KEY", "real-upstage")
        assert router._get_upstage_key() is None

    def test_no_force_mock_still_reads_keys(self, monkeypatch):
        """force_mock=False일 땐 종전과 동일하게 실 키를 읽어야 한다 (런타임 동작 불변)."""
        s = self._settings(force_mock=False)
        assert s.use_mock_dart is False
        assert s.use_mock_llm is False
