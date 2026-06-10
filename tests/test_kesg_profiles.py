"""K-ESG 프로파일 (sme 28 / full 61) 테스트."""
from __future__ import annotations

import pytest

from esgenie.dart_client import load_report
from esgenie.knowledge.kesg_items import (
    ALL_ITEMS,
    BASIC_28_ITEMS,
    PROFILES,
    detect_profile,
    items_for_profile,
)
from esgenie.layer1_extract import extract


# ---- 프로파일 정의 -------------------------------------------------------------

class TestProfileDefinition:
    def test_profile_sizes(self):
        assert len(items_for_profile("sme")) == 28
        assert len(items_for_profile("full")) == 61

    def test_sme_subset_of_full(self):
        full_codes = {it.code for it in PROFILES["full"]}
        assert all(it.code in full_codes for it in PROFILES["sme"])

    def test_unknown_profile_raises(self):
        with pytest.raises(ValueError):
            items_for_profile("mid")

    def test_sme_covers_all_areas(self):
        """기본형도 P/E/S/G 전 영역을 포함해야 한다."""
        areas = {it.area for it in BASIC_28_ITEMS}
        assert areas == {"P", "E", "S", "G"}


# ---- 자동 판별 -----------------------------------------------------------------

class TestDetectProfile:
    def test_listed_ticker_is_full(self):
        assert detect_profile("005930") == "full"
        assert detect_profile("005380") == "full"

    def test_sme_code_is_sme(self):
        assert detect_profile("SME001") == "sme"
        assert detect_profile("LOCAL") == "sme"


# ---- 커버리지 분모 ---------------------------------------------------------------

class TestExtractWithProfile:
    @pytest.fixture(scope="class")
    def samsung(self):
        return load_report("005930")

    @pytest.fixture(scope="class")
    def sme(self):
        return load_report("SME001")

    def test_auto_full_for_listed(self, samsung):
        res = extract(samsung)
        assert res.profile == "full"
        # full 분모 = 61
        in_profile = len(res.mapped) - len(res.beyond_profile)
        assert res.coverage_pct == pytest.approx(100 * in_profile / 61)

    def test_auto_sme_for_unlisted(self, sme):
        res = extract(sme)
        assert res.profile == "sme"
        in_profile = len(res.mapped) - len(res.beyond_profile)
        assert res.coverage_pct == pytest.approx(100 * in_profile / 28)

    def test_sme_profile_raises_coverage_vs_full(self, sme):
        """같은 중소기업 데이터 — 분모가 28이면 61일 때보다 커버리지가 높아야 한다."""
        sme_res = extract(sme, profile="sme")
        full_res = extract(sme, profile="full")
        assert sme_res.coverage_pct > full_res.coverage_pct

    def test_explicit_profile_override(self, samsung):
        res = extract(samsung, profile="sme")
        assert res.profile == "sme"
        # 삼성은 28개 기본형을 모두 공시 → 100%에 근접해야 함
        assert res.coverage_pct >= 90

    def test_beyond_profile_not_in_denominator(self, samsung):
        """프로파일 밖 추가 공시는 mapped에 있되 커버리지엔 미반영."""
        res = extract(samsung, profile="sme")
        assert res.beyond_profile, "삼성은 28개 외 항목도 공시하므로 beyond가 있어야 함"
        for code in res.beyond_profile:
            assert res.mapped[code]["beyond_profile"] is True
        in_profile = len(res.mapped) - len(res.beyond_profile)
        assert in_profile <= 28

    def test_missing_only_within_profile(self, sme):
        res = extract(sme, profile="sme")
        sme_codes = {it.code for it in items_for_profile("sme")}
        assert all(c in sme_codes for c in res.missing)

    def test_profile_label_in_notes(self, sme):
        res = extract(sme)
        assert any("기본형" in n for n in res.notes)
        assert res.profile_label
