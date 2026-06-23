"""RBA 고유 조항 추출(Track 2) 회귀 테스트.

RBA 42항목 중 K-ESG 크로스워크가 없는 10개(근로시간·분쟁광물·IP 등)는 그동안
K-ESG 증빙풀에 안 걸려 항상 insufficient였다. clause를 RBA 코드로 태깅 →
responder가 해당 칸을 채우는 end-to-end를 고정한다.
"""
from __future__ import annotations

import pytest

from esgenie.knowledge.rba_items import resolve_rba_code
from esgenie.ssot.ocr_router import (
    OcrExtraction, ExtractedClause, DocChannel, tag_rba_codes,
)
from esgenie.ssot.evidence_graph import EvidenceGraph, merge_ocr_extraction
from esgenie.supplychain.responder import build_response_sheet
from esgenie.supplychain.frameworks.rba_self import RBA42


# ── 1) resolve_rba_code: 고유 조항 텍스트 → RBA 코드 ──────────────────────────
@pytest.mark.parametrize("text,expected", [
    ("주 52시간 근로시간 상한을 준수하고 연장근로는 동의 하에 운영한다.", "A-3"),
    ("강제노동과 인신매매를 금지하고 신분증 압류를 하지 않는다.", "A-1"),
    ("분쟁광물 3TG 책임광물 실사를 CMRT 양식으로 수행한다.", "D-7"),
    ("영업비밀과 지식재산을 보호한다.", "D-4"),
])
def test_resolve_rba_code(text, expected):
    code, _, _ = resolve_rba_code(text)
    assert code == expected, f"{text[:20]} → {code}"


def test_resolve_rba_irrelevant_none():
    code, _, _ = resolve_rba_code("점심 메뉴는 김치찌개입니다.")
    assert code is None


# ── 2) tag_rba_codes: clause에 코드 부여 ─────────────────────────────────────
def test_tag_clauses():
    ext = OcrExtraction(
        source_file="규정.pdf", channel=DocChannel.UNSTRUCTURED, doc_type="policy_manual",
        clauses=[
            ExtractedClause(section="근로시간", text="주 52시간 근로시간 상한 준수."),
            ExtractedClause(section="잡담", text="점심은 김치찌개."),
        ])
    tag_rba_codes(ext)
    assert ext.clauses[0].rba_code_guess == "A-3"
    assert ext.clauses[1].rba_code_guess is None      # 무관 → None(거짓경보 방지)
    assert "rba_tagging" in ext.router_meta


# ── 3) end-to-end: 태깅 clause → RBA 응답서 칸 채움 ───────────────────────────
class _FakeExtraction:
    mapped: dict = {}
    missing: list = []
    corp_name = "한울정밀"


def _sheet_from_clauses(clauses):
    ext = OcrExtraction(source_file="규정.pdf", channel=DocChannel.UNSTRUCTURED,
                        doc_type="policy_manual", clauses=clauses)
    tag_rba_codes(ext)
    g = EvidenceGraph(corp_name="한울정밀", corp_code="SME001")
    merge_ocr_extraction(g, ext, report_year=2026)
    return build_response_sheet(RBA42, corp_name="한울정밀",
                                extraction=_FakeExtraction(), evidence_graph=g)


def test_rba_unique_item_filled_by_clause():
    sheet = _sheet_from_clauses([
        ExtractedClause(section="근로시간", text="주 52시간 근로시간 상한 준수, 연장근로 동의 하 운영."),
        ExtractedClause(section="분쟁광물", text="분쟁광물 3TG 책임광물 실사를 CMRT로 수행."),
    ])
    by_qid = {a.qid: a for a in sheet.answers}
    assert by_qid["RBA-A-3"].status == "verified"      # 근로시간(고유) 채워짐
    assert len(by_qid["RBA-A-3"].evidence_links) >= 1
    assert by_qid["RBA-D-7"].status == "verified"      # 분쟁광물(고유) 채워짐


def test_rba_crosswalk_item_also_uses_clause():
    """크로스워크 보유 항목(A-1 강제노동→S-5-1)도 RBA 태깅 clause로 채워진다."""
    sheet = _sheet_from_clauses([
        ExtractedClause(section="강제노동", text="강제노동과 인신매매 금지, 신분증 압류 금지."),
    ])
    by_qid = {a.qid: a for a in sheet.answers}
    assert by_qid["RBA-A-1"].status in ("verified", "self_reported")


def test_rba_item_without_evidence_stays_insufficient():
    """증빙 없는 RBA 고유항목은 그대로 insufficient(거짓 채움 없음)."""
    sheet = _sheet_from_clauses([
        ExtractedClause(section="근로시간", text="주 52시간 근로시간 상한 준수."),
    ])
    by_qid = {a.qid: a for a in sheet.answers}
    assert by_qid["RBA-D-4"].status == "insufficient"   # IP 보호 — 증빙 없음


def test_empty_evidence_graph_safe():
    """그래프 없어도 안전(회귀 가드)."""
    sheet = build_response_sheet(RBA42, corp_name="x", extraction=_FakeExtraction(),
                                 evidence_graph=None)
    assert len(sheet.answers) == len(RBA42.questions)
