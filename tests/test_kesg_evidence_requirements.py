"""STEP 2: K-ESG 증빙요구 룩업 테이블 테스트.

불변식
  1) BASIC_28 전 코드가 명시 매핑되어 있고, 각 entry는 유효한 kind·비어있지 않은
     evidence_types·request 를 갖는다.
  2) 자동(quantitative+disclosure) ~17 / 정성(policy) ~11 — 계획서 분류와 정합.
  3) 미등재 코드는 data_type 기반 기본값으로 안전하게 폴백한다(폴백이 폭주하지 않음).
  4) human_narrative=True 인 항목은 증빙으로 자동해소되지 않는다(hitl 경로).
"""
from __future__ import annotations

from esgenie.knowledge import kesg_items
from esgenie.knowledge.kesg_evidence_requirements import (
    EvidenceRequirement,
    derive_kind_for,
    requirement_for,
)

_VALID_KINDS = {"quantitative", "disclosure", "policy"}


def test_all_basic28_explicitly_mapped_and_well_formed():
    for code in kesg_items.BASIC_28_CODES:
        req = requirement_for(code)
        assert isinstance(req, EvidenceRequirement)
        assert req.code == code
        assert req.kind in _VALID_KINDS
        assert req.evidence_types, f"{code}: evidence_types 비어있음"
        assert req.request.strip(), f"{code}: request 비어있음"


def test_basic28_auto_vs_policy_split():
    kinds = [requirement_for(c).kind for c in kesg_items.BASIC_28_CODES]
    auto = sum(1 for k in kinds if k in ("quantitative", "disclosure"))
    policy = sum(1 for k in kinds if k == "policy")
    assert auto + policy == 28
    # 계획서: 자동 ~17 / 정성 ~10. 근방인지 확인(설계 의도 회귀 가드).
    assert 15 <= auto <= 19, f"자동 분류 {auto}개 — 설계(~17)에서 벗어남"
    assert 9 <= policy <= 13, f"정성 분류 {policy}개 — 설계(~11)에서 벗어남"


def test_human_narrative_items_are_not_evidence_resolvable():
    hitl = [c for c in kesg_items.BASIC_28_CODES
            if requirement_for(c).human_narrative]
    assert hitl, "hitl(작성필요) 항목이 최소 1개는 있어야 데모에서 '작성필요' 버킷이 비지 않음"
    for code in hitl:
        req = requirement_for(code)
        assert req.kind == "policy"          # 서술필요는 정성 항목에서만
        assert not req.resolvable_by_evidence


def test_quantitative_items_are_evidence_resolvable():
    for code in kesg_items.BASIC_28_CODES:
        req = requirement_for(code)
        if req.kind == "quantitative":
            assert req.resolvable_by_evidence


def test_unlisted_code_falls_back_by_data_type():
    # E-7-1(대기오염물질, 정량)은 BASIC_28에 없음 → quantitative 폴백.
    assert "E-7-1" not in kesg_items.BASIC_28_CODES
    assert derive_kind_for("E-7-1") == "quantitative"
    # P-2-1(중대성평가, 정성)도 BASIC_28에 없음 → policy 폴백.
    assert "P-2-1" not in kesg_items.BASIC_28_CODES
    assert derive_kind_for("P-2-1") == "policy"


def test_unknown_code_safe_default():
    req = requirement_for("Z-9-9")
    assert req.kind == "policy"
    assert req.evidence_types and req.request


# ── RBA 고유 10항목 증빙 안내 테스트 ─────────────────────────────────────────

_RBA_UNIQUE_CODES = ("A-3", "B-7", "C-3", "C-6", "D-4", "D-7", "E-3", "E-7", "E-10", "E-11")


def test_rba_unique_items_explicitly_mapped():
    """RBA 고유 10항목이 명시 매핑되어 있고 well-formed이다."""
    for code in _RBA_UNIQUE_CODES:
        req = requirement_for(code)
        assert isinstance(req, EvidenceRequirement)
        assert req.code == code
        assert req.kind == "policy"
        assert len(req.evidence_types) >= 2, f"{code}: evidence_types 너무 적음"
        assert req.request.strip(), f"{code}: request 비어있음"
        assert not req.human_narrative, f"{code}: RBA 고유항목은 증빙형(human_narrative=False)"


def test_rba_items_are_evidence_resolvable():
    """RBA 고유 10항목은 증빙 업로드로 자동 해소된다(insufficient 경로)."""
    for code in _RBA_UNIQUE_CODES:
        req = requirement_for(code)
        assert req.resolvable_by_evidence, f"{code}: 증빙으로 풀려야 함"


def test_rba_requirement_request_is_actionable():
    """RBA 증빙 안내문이 구체적이고 실행 가능하다(단순 폴백이 아님)."""
    from esgenie.knowledge.kesg_evidence_requirements import _DEFAULT_POLICY_REQUEST
    for code in _RBA_UNIQUE_CODES:
        req = requirement_for(code)
        assert req.request != _DEFAULT_POLICY_REQUEST, (
            f"{code}: 기본 폴백 안내문이 아닌 구체적 안내여야 함"
        )


def test_rba_derive_path_uses_explicit_requirement():
    """RBA 고유항목이 insufficient일 때 명시 안내문이 derive에 반영된다."""
    from esgenie.supplychain.responder import build_response_sheet
    from esgenie.supplychain.frameworks.rba_self import RBA42

    class _FakeExt:
        mapped = {}
        missing = []
        corp_name = "test"

    sheet = build_response_sheet(RBA42, corp_name="test",
                                 extraction=_FakeExt(), evidence_graph=None)
    by_qid = {a.qid: a for a in sheet.answers}
    for code in _RBA_UNIQUE_CODES:
        qid = f"RBA-{code}"
        a = by_qid.get(qid)
        if a is None:
            continue
        assert a.status == "insufficient"
        req = requirement_for(code)
        assert req.request in a.rationale, (
            f"{qid}: derive 안내문에 명시 requirement가 반영되지 않음"
        )
        assert a.evidence_needed, f"{qid}: evidence_needed가 비어있음"
