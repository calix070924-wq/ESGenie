"""라벨 → K-ESG 코드 동의어 해소기 + OCR backfill 회귀 테스트.

회사·파일마다 라벨 표기가 달라도("전력사용량"/"전기소비량"/"사용전력") 같은 코드로
수렴하는지, 모호/무관 라벨은 안전하게 None을 돌리는지(오부여 방지) 검증한다.
"""
from __future__ import annotations

import pytest

from esgenie.knowledge.kesg_items import resolve_kesg_code


# ── 1) 전력 동의어는 전부 E-4-1 (결정적) ──────────────────────────────────────
@pytest.mark.parametrize("label", [
    "전력사용량", "전기소비량", "사용전력", "사용전력량", "소비전력",
    "전기 사용량", "전기사용량", "전력소비량", "에너지소비량", "전력 사용량",
])
def test_power_synonyms_resolve_to_e41(label):
    code, score, method = resolve_kesg_code(label)
    assert code == "E-4-1", f"{label} → {code} ({method})"
    assert method == "exact"


# ── 2) 폐기물·용수 동의어 ─────────────────────────────────────────────────────
@pytest.mark.parametrize("label,expected", [
    ("폐기물 처리량", "E-6-1"),
    ("총 위탁량", "E-6-1"),
    ("위탁수량", "E-6-1"),
    ("재활용률", "E-6-2"),
    ("폐기물 재활용 비율", "E-6-2"),
    ("순환이용률", "E-6-2"),
    ("용수 사용량", "E-5-1"),
    ("급수량", "E-5-1"),
    ("온실가스 배출량", "E-3-1"),
])
def test_other_synonyms(label, expected):
    code, _, _ = resolve_kesg_code(label)
    assert code == expected, f"{label} → {code} (기대 {expected})"


# ── 3) 무관/모호 라벨은 None (오부여 방지) ────────────────────────────────────
@pytest.mark.parametrize("label", [
    "청구금액", "납기일", "고객번호", "대표이사 성명", "사업자번호", "뜬금없는라벨",
])
def test_irrelevant_labels_return_none(label):
    code, _, _ = resolve_kesg_code(label)
    assert code is None, f"{label} → {code} (None 이어야 함)"


def test_bare_usage_is_ambiguous():
    """단위 없는 '사용량'은 전기/가스/용수 모두 가능 → 결정적 부여 금지(None)."""
    code, _, _ = resolve_kesg_code("사용량(kWh)")
    assert code is None  # doc_type 컨텍스트(템플릿)가 해소해야 함


def test_determinism():
    """동일 입력 → 항상 동일 출력 (시연·감사 재현성)."""
    a = resolve_kesg_code("전기소비량")
    b = resolve_kesg_code("전기소비량")
    assert a == b


# ── 4) OCR backfill 통합: 코드 미부여 metric을 사전이 채운다 ──────────────────
def test_backfill_fills_missing_code():
    from esgenie.ssot.ocr_router import _backfill_kesg_codes, OcrExtraction, \
        ExtractedMetric, DocChannel

    ext = OcrExtraction(
        source_file="x.pdf", channel=DocChannel.STRUCTURED, doc_type="kepco_bill",
        metrics=[
            ExtractedMetric(metric_hint="전기소비량", value=142560, unit="kWh",
                            period="2026-05", kesg_code_guess=None, confidence=0.8),
            ExtractedMetric(metric_hint="청구금액", value=27748160, unit="원",
                            period="2026-05", kesg_code_guess=None, confidence=0.8),
        ],
    )
    _backfill_kesg_codes(ext)
    by_hint = {m.metric_hint: m for m in ext.metrics}
    assert by_hint["전기소비량"].kesg_code_guess == "E-4-1"   # 동의어로 채워짐
    assert by_hint["청구금액"].kesg_code_guess is None         # 무관 → 그대로 None
    assert "alias_backfill" in ext.router_meta


def test_backfill_preserves_existing_code():
    """이미 코드가 있으면 사전이 덮어쓰지 않는다."""
    from esgenie.ssot.ocr_router import _backfill_kesg_codes, OcrExtraction, \
        ExtractedMetric, DocChannel

    ext = OcrExtraction(
        source_file="x.pdf", channel=DocChannel.STRUCTURED, doc_type="waste_ledger",
        metrics=[ExtractedMetric(metric_hint="재활용 비율", value=29.3, unit="%",
                                 period="", kesg_code_guess="E-6-2", confidence=0.9)],
    )
    _backfill_kesg_codes(ext)
    assert ext.metrics[0].kesg_code_guess == "E-6-2"
