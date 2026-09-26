"""증빙 안내가 '문항'에 맞는지 — 2026-09-20 §5-2 회귀.

근거 문서
---------
  docs/작업지시서_HMC_응답서_검증정합성_개선_2026-09-20.md §5-2
  docs/reviews/HMC_출력_지적사항_검토_2026-09-20.md

고친 결함(HMC 응답서 실측)
  · D-5 공정거래(정성)와 C-1 환경 인허가(정성)에 "해당 수치를 입증할 고지서·명세서·
    산정표를 올려주세요"가 안내됐다. 원인은 문항의 primary_code가 K-ESG 크로스워크
    코드(G-6-1 공정거래 위반건수 / E-8-1 환경법규 위반건수)라서, 위반 '건수' 코드의
    정량 폴백을 정성 조항이 물려받은 것.
  · 같은 이유로 초안 게이트(_is_draft_candidate)가 이 문항들을 quantitative로 읽어
    AI 초안 대상에서 제외했다 — 안내문과 게이트가 서로 다른 유형을 봤다.
  · C-5 대기배출은 관리체계 존재 여부를 kg 수치 한 칸으로 묻고 있었다.

고정하는 계약
  1. 안내 선택 규칙 순서: 문항별 명시 요구 → 문항 유형에 맞는 폴백.
  2. K-ESG 원래 수치 문항의 정량 안내는 그대로 유지한다(정책 안내로 바꾸지 않는다).
  3. 안내는 응답 정보(Answer.rationale·evidence_needed)에 실려 화면·엑셀·PDF·
     체크리스트가 같은 문장을 쓴다 — exporter에서 문구를 따로 만들지 않는다.

입력 구분: 선언적 양식 정의 + 손으로 만든 통제 실험(빈 추출 결과). OCR/LLM 호출 없음.
"""
from __future__ import annotations

import pytest

from esgenie.knowledge.kesg_evidence_requirements import (
    requirement_for,
    requirement_for_question,
)
from esgenie.supplychain.checklist import checklist_rows
from esgenie.supplychain.frameworks import get_framework
from esgenie.supplychain.mapping import derive_answer
from esgenie.supplychain.responder import build_response_sheet

# 정량 폴백 문구 — 정성 문항에 이 문장이 나오면 결함 재발이다.
_QUANT_FALLBACK = "해당 수치를 입증할 고지서·명세서·산정표를 올려주세요."


def _question(framework_key: str, qid: str):
    return next(q for q in get_framework(framework_key).questions if q.qid == qid)


def _answer(qid: str, framework_key: str = "hmc"):
    """증빙이 하나도 없는 상태의 답변 — 미해소 안내문이 그대로 드러난다."""
    return derive_answer(_question(framework_key, qid), mapped={}, missing=set(),
                         dp_by_code={})


class TestQualitativeClausesDoNotGetBillGuidance:
    @pytest.mark.parametrize("qid", ["HMC-D-5", "HMC-C-1", "HMC-C-5"])
    def test_no_bill_or_invoice_request_on_a_yes_no_clause(self, qid):
        ans = _answer(qid)
        assert _question("hmc", qid).qtype == "yes_no_evidence"
        assert ans.rationale != _QUANT_FALLBACK
        assert "고지서" not in ans.rationale and "명세서" not in ans.rationale

    def test_fair_trade_asks_for_a_compliance_programme(self):
        ans = _answer("HMC-D-5")
        assert "공정거래 자율준수 방침" in ans.rationale
        assert any("하도급" in e for e in ans.evidence_needed)

    def test_environmental_permit_asks_for_the_permit_itself(self):
        ans = _answer("HMC-C-1")
        assert "인허가" in ans.rationale
        assert any("인허가증" in e for e in ans.evidence_needed)

    def test_air_emission_clause_points_the_number_at_its_own_question(self):
        ans = _answer("HMC-C-5")
        assert "별도 수치 문항" in ans.rationale
        assert any("허가" in e for e in ans.evidence_needed)


class TestNumericQuestionsKeepQuantitativeGuidance:
    """정성 문항을 고치면서 수치 문항의 정량 안내를 정책 안내로 바꾸지 않는다."""

    @pytest.mark.parametrize("qid,needle", [
        ("HMC-C-5-E-7-1", "배출량"),
        ("HMC-C-4-E-6-2", "재활용"),
        ("HMC-C-8-E-4-1", "에너지"),
        ("HMC-C-8-E-3-1", "Scope"),
    ])
    def test_metric_rows_still_ask_for_the_number(self, qid, needle):
        q = _question("hmc", qid)
        assert q.qtype == "numeric"
        req = requirement_for_question(q.kesg_codes, quantitative=True)
        assert req.kind == "quantitative"
        assert needle in req.request

    def test_kesg_frameworks_are_untouched(self):
        """K-ESG 공시 양식(28·61)의 안내는 한 문항도 바뀌지 않는다."""
        for key in ("kesg28", "kesg61"):
            for q in get_framework(key).questions:
                if not q.kesg_codes:
                    continue
                assert requirement_for_question(
                    q.kesg_codes, quantitative=q.qtype == "numeric"
                ) == requirement_for(q.primary_code)


class TestRuleOrder:
    def test_explicit_requirement_wins_over_a_crosswalk_fallback(self):
        # D-5는 명시 등록, G-6-1은 정량 폴백. 정성 문항이면 D-5가 이긴다.
        req = requirement_for_question(("G-6-1", "D-5"), quantitative=False)
        assert req.code == "D-5"

    def test_same_codes_resolve_differently_for_a_numeric_question(self):
        """규칙은 코드가 아니라 문항 유형으로 갈린다 — 같은 코드 묶음, 다른 답."""
        codes = ("E-7-1", "C-5")
        assert requirement_for_question(codes, quantitative=False).code == "C-5"
        assert requirement_for_question(codes, quantitative=True).code == "E-7-1"

    def test_unknown_codes_fall_back_to_the_question_type(self):
        assert requirement_for_question(("Z-9-9",), quantitative=False).kind == "policy"
        assert requirement_for_question(("Z-9-9",), quantitative=True).kind == "quantitative"
        assert requirement_for_question((), quantitative=False).kind == "policy"


class TestChecklistSharesTheSameSentence:
    def test_checklist_row_repeats_the_answer_guidance(self):
        sheet = build_response_sheet("hmc", corp_name="한울정밀공업")
        rows = {r["문항 ID"]: r for r in checklist_rows(sheet)}
        answers = {a.qid: a for a in sheet.answers}
        for qid in ("HMC-D-5", "HMC-C-1", "HMC-C-5"):
            a = answers[qid]
            if a.status not in ("insufficient", "hitl_required"):
                continue  # 초안이 붙었으면 체크리스트 문구는 초안 검토 안내다
            assert rows[qid]["안내"] == a.rationale
            assert "고지서" not in rows[qid]["올릴 문서 / 작성 사항"]
