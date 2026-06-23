"""PII 마스킹 모듈 테스트 (tests/test_pii.py).

pytest tests/test_pii.py -q
"""
from __future__ import annotations

import pytest
from esgenie.pii import mask_pii, mask_pii_obj


# ── 양성 케이스 ──────────────────────────────────────────────────────────────

class TestPositive:
    def test_rrn(self):
        assert mask_pii("850101-1234567") == "[RRN]"
        assert mask_pii("주민번호: 850101-1234567입니다") == "주민번호: [RRN]입니다"

    def test_brn(self):
        assert mask_pii("사업자번호 123-45-67890") == "사업자번호 [BRN]"
        assert mask_pii("등록번호: 000-00-00000") == "등록번호: [BRN]"

    def test_phone_mobile(self):
        assert mask_pii("010-1234-5678") == "[PHONE]"
        assert mask_pii("010-123-4567") == "[PHONE]"
        assert mask_pii("011-234-5678") == "[PHONE]"
        assert mask_pii("연락처 016-1234-5678 문의") == "연락처 [PHONE] 문의"

    def test_phone_landline(self):
        assert mask_pii("02-1234-5678") == "[PHONE]"
        assert mask_pii("031-123-4567") == "[PHONE]"
        assert mask_pii("051-9999-1234") == "[PHONE]"

    def test_email(self):
        assert mask_pii("user@example.com") == "[EMAIL]"
        assert mask_pii("contact: admin@company.co.kr 문의") == "contact: [EMAIL] 문의"
        assert mask_pii("ir@samsung.com") == "[EMAIL]"

    def test_card(self):
        assert mask_pii("1234-5678-9012-3456") == "[CARD]"
        assert mask_pii("카드: 0000-1111-2222-3333") == "카드: [CARD]"

    def test_acct(self):
        assert mask_pii("123456-78-901234") == "[ACCT]"   # KB 계좌 형태 (6-2-6=14자리)
        assert mask_pii("100-024-123456") == "[ACCT]"      # 신한 형태 (3-3-6=12자리)


# ── ESG 수치 회귀 (음성 케이스) ──────────────────────────────────────────────

class TestEsgNumericsNotMasked:
    @pytest.mark.parametrize("text", [
        "온실가스 배출량은 16,700,000 tCO2eq",
        "2025년 31.0%",
        "에너지 355 TJ",
        "전년 대비 18% 감소",
        "2024-01-15 기준",           # 날짜 (8자리 미만)
        "E-3-1 항목",                # K-ESG 코드
        "재생에너지 비율 31.0%",
        "Scope 1+2: 200,000 tCO2eq",
    ])
    def test_esg_not_masked(self, text: str):
        assert mask_pii(text) == text, f"ESG 수치/날짜가 마스킹됨: {text!r}"


# ── mask_pii_obj 재귀 처리 ───────────────────────────────────────────────────

class TestMaskPiiObj:
    def test_nested_dict_and_list(self):
        obj = {
            "sentence_text": "연락처 010-1234-5678",
            "nested": {
                "email": "admin@company.com",
                "value": 12345,
            },
            "items": ["user@test.com", 42],
        }
        result = mask_pii_obj(obj)

        assert result["sentence_text"] == "연락처 [PHONE]"
        assert result["nested"]["email"] == "[EMAIL]"
        assert result["nested"]["value"] == 12345       # 정수 — 그대로
        assert result["items"][0] == "[EMAIL]"
        assert result["items"][1] == 42                  # 정수 — 그대로

    def test_keys_preserved(self):
        obj = {"sentence_text": "ok", "risk_score": 0.42}
        result = mask_pii_obj(obj)
        assert "sentence_text" in result
        assert "risk_score" in result

    def test_plain_string(self):
        assert mask_pii_obj("user@test.com") == "[EMAIL]"

    def test_list_top_level(self):
        result = mask_pii_obj(["010-1234-5678", "clean text", 99])
        assert result == ["[PHONE]", "clean text", 99]

    def test_non_string_passthrough(self):
        assert mask_pii_obj(42) == 42
        assert mask_pii_obj(3.14) == 3.14
        assert mask_pii_obj(None) is None


# ── 토글: ESGENIE_PII_MASK=0 이면 pii_mask=False ────────────────────────────

class TestToggle:
    def test_default_enabled(self):
        import esgenie.config as cfg
        settings = cfg.load_settings()
        assert settings.pii_mask is True

    def test_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("ESGENIE_PII_MASK", "0")
        import esgenie.config as cfg
        settings = cfg.load_settings()
        assert settings.pii_mask is False

    def test_enabled_explicitly(self, monkeypatch):
        monkeypatch.setenv("ESGENIE_PII_MASK", "1")
        import esgenie.config as cfg
        settings = cfg.load_settings()
        assert settings.pii_mask is True
