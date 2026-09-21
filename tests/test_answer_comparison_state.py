"""비교 4상태가 DataPoint → Answer까지 전달되는지 — 2026-09-20.

근거 문서
---------
  docs/작업지시서_HMC_응답서_검증정합성_개선_2026-09-20.md §4-2
  docs/reviews/HMC_출력_지적사항_검토_2026-09-20.md

고친 결함(HMC 응답서 실측)
  · 재생에너지 비율 10.6%(상반기 부분값)와 협력사 자가주장 10.6%가 숫자 허용오차
    안에 들어 '자가신고 일치'로 찍혔다. 전체 비율과 부분값의 의미 차이가 사라졌다.
  · 실제 불일치·비교 불가·범위 확인 필요가 모두 같은 자가신고 배지로 뭉개졌다.

입력 구분: 손으로 만든 통제 실험(DataPoint·SupplierClaim 직접 구성). OCR/LLM 호출 없음.
"""
from __future__ import annotations

import pytest

from esgenie.ssot.audit_trace import DataPoint, EvidenceLink
from esgenie.supplychain.claims import SupplierClaim
from esgenie.supplychain.mapping import derive_answer
from esgenie.supplychain.schema import Question

_Q = Question(qid="E-4-2", section="환경", text="재생에너지 사용 비율",
              qtype="numeric", kesg_codes=("E-4-2",), unit_hint="%")


def _link(node_id="OCR_0001", code="E-4-2", fname="12_재생에너지사용현황_2026-06.pdf"):
    return EvidenceLink(file_name=fname, relative_path=f"evidence_pack/{fname}",
                        origin="ocr_unstructured", page=0, node_id=node_id,
                        kesg_codes=[code], quote="재생에너지 비율 10.6%",
                        resolved=True, independent=True)


def _dp(**kw):
    base = dict(
        kesg_code="E-4-2", kesg_name="재생에너지 사용 비율", value=10.6, unit="%",
        period=2026, confidence=0.85, verification="estimated", d1_risk=0.0,
        evidence_files=[_link()],
    )
    base.update(kw)
    return DataPoint(**base)


def _answer(dp, claim=None):
    links = {e.node_id: e for e in [*dp.evidence_files, *dp.reference_files]}
    return derive_answer(_Q, mapped={}, missing=set(), dp_by_code={"E-4-2": dp},
                         evidence_index=links,
                         claims={"E-4-2": claim} if claim is not None else {})


class TestBoundaryReachesTheAnswer:
    """경계·완전성·검토 사유는 UI·Excel·PDF·체크리스트가 같은 필드에서 읽는다."""

    def test_partial_boundary_and_notes_are_carried(self):
        ans = _answer(_dp(
            completeness="partial",
            boundary_label="2026년 상반기 · 기간합계 · 재생에너지 · 부분",
            scope_notes=["E-4-2: 부분값 — 2026년 상반기 · 기간합계 · 재생에너지 · 부분. "
                         "전사·연간·전체 범위 총량임이 입증되지 않아 검증 보류"]))
        assert ans.completeness == "partial"
        assert "상반기" in ans.boundary_label
        assert ans.scope_notes and "검증 보류" in ans.review_note

    def test_reference_links_stay_out_of_evidence_links(self):
        """합산에 쓰이지 않은 문서는 산정 근거 목록에 섞이지 않는다(§2-2)."""
        ref = _link(node_id="OCR_0099", fname="02_도시가스요금고지서_2026-05.pdf")
        ans = _answer(_dp(reference_files=[ref]))
        assert [e.node_id for e in ans.evidence_links] == ["OCR_0001"]
        assert [e.node_id for e in ans.reference_links] == ["OCR_0099"]

    def test_comparison_state_and_label_are_carried(self):
        ans = _answer(_dp(comparison="not_comparable",
                          comparison_reason="단위 차원 상이(MJ ↔ kWh)"))
        assert ans.comparison == "not_comparable"
        assert ans.comparison_label == "비교 불가"
        assert "MJ" in ans.review_note

    def test_no_comparison_leaves_the_fields_empty(self):
        """판정이 없으면 빈 값이다 — 없는 대조를 있었던 것처럼 쓰지 않는다."""
        ans = _answer(_dp(completeness="total"))  # 전달 계약: 검증 자격은 별도 원장 회귀에서 확인
        assert ans.comparison == ""
        assert ans.comparison_label == ""
        assert ans.review_note == ""


class TestClaimReconciliationStates:
    """자가주장 대조 — 기존 임계값(비율 10%p)은 그대로, 판정 이름만 정확해진다."""

    def _claim(self, value=10.6, unit="%"):
        return SupplierClaim(code="E-4-2", value=value, unit=unit,
                             raw=f"재생에너지 비율 {value}%",
                             source="saq:OEM_ESG자가진단설문.pdf")

    def test_partial_evidence_is_scope_unconfirmed_not_agreement(self):
        ans = _answer(_dp(completeness="partial",
                          boundary_label="2026년 상반기 · 기간합계 · 재생에너지 · 부분"),
                      self._claim())
        assert ans.comparison == "scope_unconfirmed"
        assert ans.comparison_label == "범위 확인 필요"
        assert not any("자가신고 일치" in f for f in ans.flags)
        assert any("범위 확인 필요" in f and "부분값" in f for f in ans.flags)

    def test_total_evidence_within_tolerance_is_compared(self):
        ans = _answer(_dp(completeness="total"), self._claim())
        assert ans.comparison == "compared"
        assert ans.comparison_label == "대조 완료"
        assert any("자가신고 일치" in f for f in ans.flags)

    def test_real_discrepancy_is_mismatch_and_flagged(self):
        ans = _answer(_dp(completeness="total"), self._claim(value=92.0))
        assert ans.comparison == "mismatch"
        assert ans.comparison_label == "실제 불일치"
        assert ans.status == "flagged"
        assert "%p" in ans.comparison_reason

    def test_unit_dimension_gap_is_not_comparable(self):
        """MJ 자가주장 ↔ % 증빙 — 불일치가 아니라 비교 불가다."""
        ans = _answer(_dp(completeness="total"), self._claim(value=360772, unit="MJ"))
        assert ans.comparison == "not_comparable"
        assert ans.status == "flagged"

    def test_ledger_comparison_is_not_downgraded_by_a_matching_claim(self):
        """상위 레이어가 더 심한 상태를 실었으면 숫자 일치가 그것을 덮지 않는다."""
        ans = _answer(_dp(completeness="total", comparison="not_comparable",
                          comparison_reason="범위 상이 — scope_gap"),
                      self._claim())
        assert ans.comparison == "not_comparable"


class TestEquivalentUnitsAgree:
    """환산 가능한 단위의 동등값은 불일치가 아니다(§8 회귀).

    비교 불가(차원 상이)만 검증하면, 환산 경로가 망가져도 아무 테스트가 울지 않는다 —
    142.56 MWh와 142,560 kWh를 불일치로 찍으면 협력사가 맞는 답에 소명을 쓴다.
    """

    def _energy(self, claim_value, claim_unit):
        q = Question(qid="E-4-1", section="환경", text="총 에너지 사용량",
                     qtype="numeric", kesg_codes=("E-4-1",), unit_hint="kWh")
        link = EvidenceLink(
            file_name="01_전기요금고지서_2026-05.pdf",
            relative_path="evidence_pack/01_전기요금고지서_2026-05.pdf",
            origin="ocr_unstructured", page=0, node_id="OCR_0001",
            kesg_codes=["E-4-1"], quote="사용전력량 142,560 kWh",
            resolved=True, independent=True)
        dp = DataPoint(kesg_code="E-4-1", kesg_name="총 에너지 사용량",
                       value=142560.0, unit="kWh", period=2026, confidence=0.9,
                       verification="verified", d1_risk=0.0, evidence_files=[link],
                       completeness="total")
        claim = SupplierClaim(code="E-4-1", value=claim_value, unit=claim_unit,
                              raw=f"{claim_value}{claim_unit}",
                              source="saq:OEM_ESG자가진단설문.pdf")
        return derive_answer(q, mapped={}, missing=set(), dp_by_code={"E-4-1": dp},
                             evidence_index={link.node_id: link},
                             claims={"E-4-1": claim})

    def test_mwh_claim_matches_a_kwh_bill_of_the_same_scope(self):
        ans = self._energy(142.56, "MWh")
        assert ans.comparison == "compared"
        assert ans.status != "flagged"

    @pytest.mark.parametrize("value", [200000, 90000])
    def test_a_real_gap_is_caught_in_both_directions(self, value):
        """과대 주장만 잡으면 절반이다 — 과소 신고도 D1 불일치다."""
        ans = self._energy(value, "kWh")
        assert ans.comparison == "mismatch"
        assert ans.status == "flagged"


class TestMismatchReachesTheChecklist:
    def test_flagged_answer_becomes_a_checklist_action(self):
        """불일치는 제출 전 체크리스트의 '검토·보완' 행으로 내려간다."""
        from esgenie.supplychain.checklist import build_checklist
        from esgenie.supplychain.schema import ResponseSheet

        ans = _answer(_dp(completeness="total"),
                      SupplierClaim(code="E-4-2", value=92.0, unit="%",
                                    raw="재생에너지 비율 92%", source="saq:설문.pdf"))
        items = build_checklist(ResponseSheet("hmc", "라벨", "한울정밀공업", [ans]))
        assert [i.action for i in items] == ["검토·보완"]
        assert "D1 불일치" in items[0].request


class TestLegacyLedgerStillReads:
    """옛 JSON(경계·완결성 키 없음)은 읽히고, 없는 범위를 발명하지 않는다(§8 회귀)."""

    def test_missing_boundary_keys_stay_empty_not_company_wide(self):
        ans = _answer(_dp(verification="verified", completeness="",
                          boundary_label="", scope_notes=[]))
        assert ans.completeness == ""
        assert ans.boundary_label == ""
        assert ans.boundary == {}  # 경계를 채운 것이 아니라 확인할 일을 안내한다.
        assert ans.status != "verified"
        assert ans.comparison == "scope_unconfirmed"
        assert "분모 미상" in ans.review_note

    def test_a_proven_whole_ratio_is_still_verified(self):
        """경계 게이트를 넣었다고 정상 전체 비율까지 막지 않는다(과차단 방지)."""
        ans = _answer(_dp(verification="verified", completeness="total"),
                      SupplierClaim(code="E-4-2", value=10.6, unit="%",
                                    raw="재생에너지 비율 10.6%", source="saq:설문.pdf"))
        assert ans.status == "verified"
        assert ans.comparison == "compared"


class TestSerialization:
    """Excel/PDF/체크리스트/JSON이 같은 키를 본다."""

    def test_to_dict_exposes_review_fields(self):
        ans = _answer(_dp(completeness="partial", comparison="scope_unconfirmed",
                          comparison_reason="원장값이 부분값",
                          boundary_label="2026년 상반기 · 기간합계",
                          reference_files=[_link(node_id="OCR_0099")]))
        d = ans.to_dict()
        assert d["completeness"] == "partial"
        assert d["comparison_label"] == "범위 확인 필요"
        assert d["boundary_label"].startswith("2026년 상반기")
        assert d["review_note"]
        assert d["reference_links"][0]["node_id"] == "OCR_0099"
        assert isinstance(d["evidence_links"], list)
