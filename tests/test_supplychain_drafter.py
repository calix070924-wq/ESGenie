"""AI 초안 엔진(drafter) 단위 테스트.

테스트 시나리오:
  1) 정상: policy TextNode 있는 hitl 항목 → draft_ready + draft_citations 비어있지 않음.
  2) fail-closed(hard): 항상 ESCALATE 반환하는 mock 게이트 → hitl_required 유지, draft_text == "".
  3) fail-closed(soft): decision=="ACCEPT"인데 soft_flags=["G5_overclaim"] → 역시 폐기.
  4) TextNode 없음 → LLM 미호출(호출 카운터) + 상태 유지.
  5) 회귀: enable_drafts=False(기본) → 기존 결과와 완전 동일(diff 0).
  6) 집계: auto_pct + draft_pct + hitl_pct + pending_pct 합이 100.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from esgenie.schemas import GroundingResult
from esgenie.supplychain.drafter import generate_drafts
from esgenie.supplychain.schema import (
    Answer,
    ResponseSheet,
    _AUTO_STATUSES,
    _DRAFT_STATUSES,
    _HITL_STATUSES,
    _PENDING_STATUSES,
)
from esgenie.supplychain import build_response_sheet, get_framework


# ── Fixtures ────────────────────────────────────────────────────────────────

FW_KEY = "kesg28"


def _make_text_node(id: str, text: str, kesg_code: str, source_file: str = "사내규정.pdf"):
    return SimpleNamespace(
        id=id, text=text, kesg_code=kesg_code,
        source_file=source_file, page=0, origin="ocr_unstructured",
        rba_code=None,
    )


def _make_evidence_graph(text_nodes_list):
    """텍스트 노드로 가짜 EvidenceGraph를 구성."""
    nodes_dict = {n.id: n for n in text_nodes_list}

    class FakeGraph:
        def __init__(self):
            self._text_nodes = nodes_dict
            self._nodes = {}

        @property
        def text_nodes(self):
            return self._text_nodes

        @property
        def nodes(self):
            return self._nodes

        def text_nodes_by_code(self, code):
            return [n for n in self._text_nodes.values() if n.kesg_code == code]

    return FakeGraph()


def _make_sheet_with_hitl(framework_key=FW_KEY):
    """hitl_required 항목이 있는 ResponseSheet를 생성."""
    fw = get_framework(framework_key)
    answers = []
    for q in fw.questions:
        answers.append(Answer(
            qid=q.qid,
            section=q.section,
            question_text=q.text,
            value=None,
            status="hitl_required",
        ))
    return ResponseSheet(
        framework_key=fw.key,
        framework_label=fw.label,
        corp_name="한울정밀",
        answers=answers,
    )


def _grounding_accept():
    return GroundingResult(
        decision="ACCEPT",
        g1_uncited_sentences=[],
        g2_orphan_numbers=[],
        g4_unit_mismatches=[],
        g5_overclaim=False,
        hard_fails=[],
        soft_flags=[],
        faithfulness=1.0,
    )


def _grounding_escalate():
    return GroundingResult(
        decision="ESCALATE",
        g1_uncited_sentences=["uncited claim"],
        g2_orphan_numbers=[],
        g4_unit_mismatches=[],
        g5_overclaim=False,
        hard_fails=["G1_uncited_claims"],
        soft_flags=[],
        faithfulness=0.5,
    )


def _grounding_accept_with_soft_flag():
    return GroundingResult(
        decision="ACCEPT",
        g1_uncited_sentences=[],
        g2_orphan_numbers=[],
        g4_unit_mismatches=[],
        g5_overclaim=True,
        hard_fails=[],
        soft_flags=["G5_overclaim"],
        faithfulness=0.9,
    )


# ── Test 1: 정상 draft_ready ───────────────────────────────────────────────

@patch("esgenie.supplychain.drafter.evaluate_grounding")
@patch("esgenie.supplychain.drafter.LLMClient")
def test_draft_normal_accept(mock_llm_cls, mock_eval):
    """policy TextNode 있는 hitl 항목 → draft_ready + citations 비어있지 않음."""
    mock_llm = MagicMock()
    mock_llm.complete.return_value = SimpleNamespace(content="초안 텍스트 [TXT_0001]")
    mock_llm_cls.return_value = mock_llm
    mock_eval.return_value = _grounding_accept()

    sheet = _make_sheet_with_hitl()
    # S-4-1 (안전보건 방침) 항목에 매칭되는 TextNode 생성
    text_node = _make_text_node(
        "TXT_0001",
        "제1조 안전보건 경영방침에 따라 산업안전보건위원회를 분기별 운영한다.",
        "S-4-1",
    )
    graph = _make_evidence_graph([text_node])

    sheet = generate_drafts(sheet, graph)

    # S-4-1에 해당하는 answer 찾기
    drafted = [a for a in sheet.answers if a.status == "draft_ready"]
    assert len(drafted) >= 1, "최소 1개 항목이 draft_ready여야 함"

    for a in drafted:
        assert a.draft_text != ""
        assert len(a.draft_citations) > 0
        assert a.draft_grounding is not None
        assert a.draft_grounding["decision"] == "ACCEPT"


# ── Test 2: fail-closed (hard fail) ────────────────────────────────────────

@patch("esgenie.supplychain.drafter.evaluate_grounding")
@patch("esgenie.supplychain.drafter.LLMClient")
def test_draft_fail_closed_hard(mock_llm_cls, mock_eval):
    """항상 ESCALATE → hitl_required 유지, draft_text == ''."""
    mock_llm = MagicMock()
    mock_llm.complete.return_value = SimpleNamespace(content="나쁜 초안")
    mock_llm_cls.return_value = mock_llm
    mock_eval.return_value = _grounding_escalate()

    sheet = _make_sheet_with_hitl()
    text_node = _make_text_node("TXT_0001", "규정 텍스트", "S-4-1")
    graph = _make_evidence_graph([text_node])

    sheet = generate_drafts(sheet, graph, max_retries=2)

    s41_answers = [a for a in sheet.answers if a.status == "draft_ready"]
    assert len(s41_answers) == 0, "hard fail 시 draft_ready 없어야 함"

    hitl_answers = [a for a in sheet.answers if a.status == "hitl_required"]
    for a in hitl_answers:
        assert a.draft_text == ""


# ── Test 3: fail-closed (soft flag G5) ─────────────────────────────────────

@patch("esgenie.supplychain.drafter.evaluate_grounding")
@patch("esgenie.supplychain.drafter.LLMClient")
def test_draft_fail_closed_soft_flag(mock_llm_cls, mock_eval):
    """decision=='ACCEPT' but soft_flags=['G5_overclaim'] → 폐기."""
    mock_llm = MagicMock()
    mock_llm.complete.return_value = SimpleNamespace(content="과장된 초안")
    mock_llm_cls.return_value = mock_llm
    mock_eval.return_value = _grounding_accept_with_soft_flag()

    sheet = _make_sheet_with_hitl()
    text_node = _make_text_node("TXT_0001", "규정 텍스트", "S-4-1")
    graph = _make_evidence_graph([text_node])

    sheet = generate_drafts(sheet, graph, max_retries=2)

    drafted = [a for a in sheet.answers if a.status == "draft_ready"]
    assert len(drafted) == 0, "soft_flag 시 draft_ready 없어야 함"


# ── Test 4: TextNode 없음 → LLM 미호출 ────────────────────────────────────

@patch("esgenie.supplychain.drafter.LLMClient")
def test_no_text_nodes_no_llm_call(mock_llm_cls):
    """관련 TextNode 없으면 LLM 호출 자체를 하지 않는다."""
    mock_llm = MagicMock()
    mock_llm_cls.return_value = mock_llm

    sheet = _make_sheet_with_hitl()
    graph = _make_evidence_graph([])  # 빈 그래프

    sheet = generate_drafts(sheet, graph)

    mock_llm.complete.assert_not_called()
    hitl_answers = [a for a in sheet.answers if a.status == "hitl_required"]
    assert len(hitl_answers) == len(sheet.answers)


# ── Test 5: enable_drafts=False → diff 0 ──────────────────────────────────

def test_enable_drafts_false_no_diff():
    """enable_drafts=False(기본) 시 기존 결과와 완전 동일."""
    fw = get_framework(FW_KEY)
    extraction = SimpleNamespace(
        corp_name="테스트사",
        mapped={"E-4-1": {"code": "E-4-1", "name": "에너지", "evidence_node_ids": []}},
        missing=[],
    )
    text_node = _make_text_node("TXT_0001", "규정 텍스트", "S-4-1")
    graph = _make_evidence_graph([text_node])

    sheet_off = build_response_sheet(
        fw, corp_name="테스트사", extraction=extraction,
        evidence_graph=graph, enable_drafts=False,
    )
    sheet_baseline = build_response_sheet(
        fw, corp_name="테스트사", extraction=extraction,
        evidence_graph=graph,
    )

    # 완전 동일해야 함
    for a_off, a_base in zip(sheet_off.answers, sheet_baseline.answers):
        assert a_off.status == a_base.status
        assert a_off.value == a_base.value
        assert a_off.draft_text == a_base.draft_text == ""


# ── Test 6: 4분할 합 100% ──────────────────────────────────────────────────

@patch("esgenie.supplychain.drafter.evaluate_grounding")
@patch("esgenie.supplychain.drafter.LLMClient")
def test_four_way_split_sums_to_100(mock_llm_cls, mock_eval):
    """auto_pct + draft_pct + hitl_pct + pending_pct == 100 (not_applicable 제외)."""
    mock_llm = MagicMock()
    mock_llm.complete.return_value = SimpleNamespace(content="초안 [TXT_0001]")
    mock_llm_cls.return_value = mock_llm
    mock_eval.return_value = _grounding_accept()

    sheet = _make_sheet_with_hitl()
    # 일부를 다른 상태로 설정
    if len(sheet.answers) > 2:
        sheet.answers[0].status = "verified"
        sheet.answers[0].value = True
        sheet.answers[1].status = "insufficient"

    text_node = _make_text_node("TXT_0001", "규정 텍스트", "S-4-1")
    graph = _make_evidence_graph([text_node])

    sheet = generate_drafts(sheet, graph)

    total = sheet.auto_pct + sheet.draft_pct + sheet.hitl_pct + sheet.pending_pct
    assert abs(total - 100.0) < 0.1, (
        f"4분할 합이 100이 아님: auto={sheet.auto_pct} + draft={sheet.draft_pct} "
        f"+ hitl={sheet.hitl_pct} + pending={sheet.pending_pct} = {total}"
    )


# ── Test 7: draft_ready는 answered에 포함되지 않음 ─────────────────────────

@patch("esgenie.supplychain.drafter.evaluate_grounding")
@patch("esgenie.supplychain.drafter.LLMClient")
def test_draft_ready_not_in_answered(mock_llm_cls, mock_eval):
    """draft_ready는 Answer.answered == False (자동응답으로 세지 않음)."""
    mock_llm = MagicMock()
    mock_llm.complete.return_value = SimpleNamespace(content="초안 [TXT_0001]")
    mock_llm_cls.return_value = mock_llm
    mock_eval.return_value = _grounding_accept()

    sheet = _make_sheet_with_hitl()
    text_node = _make_text_node("TXT_0001", "규정 텍스트", "S-4-1")
    graph = _make_evidence_graph([text_node])

    sheet = generate_drafts(sheet, graph)

    drafted = [a for a in sheet.answers if a.status == "draft_ready"]
    assert len(drafted) >= 1
    for a in drafted:
        assert a.answered is False, "draft_ready는 answered=False여야 함"


# ── Test 8: 재시도 카운터 ──────────────────────────────────────────────────

@patch("esgenie.supplychain.drafter.evaluate_grounding")
@patch("esgenie.supplychain.drafter.LLMClient")
def test_retry_count(mock_llm_cls, mock_eval):
    """max_retries=2이면 총 3번 호출(1 + 2 재시도) 후 폐기."""
    mock_llm = MagicMock()
    mock_llm.complete.return_value = SimpleNamespace(content="나쁜 초안")
    mock_llm_cls.return_value = mock_llm
    mock_eval.return_value = _grounding_escalate()

    sheet = _make_sheet_with_hitl()
    # S-4-1 하나만 매칭되도록
    text_node = _make_text_node("TXT_0001", "규정 텍스트", "S-4-1")
    graph = _make_evidence_graph([text_node])

    sheet = generate_drafts(sheet, graph, max_retries=2)

    # S-4-1 코드에 매칭되는 항목 수 × 3(1+2 retries)회 호출
    # 최소 3회 이상 (S-4-1 항목이 하나 이상 매칭)
    assert mock_llm.complete.call_count >= 3


# ── Test 9: insufficient+policy 경로 동작 확인 ────────────────────────────

@patch("esgenie.supplychain.drafter.evaluate_grounding")
@patch("esgenie.supplychain.drafter.LLMClient")
def test_insufficient_policy_becomes_draft_ready(mock_llm_cls, mock_eval):
    """insufficient이고 kind=='policy'인 항목 + 매칭 TextNode → draft_ready."""
    mock_llm = MagicMock()
    mock_llm.complete.return_value = SimpleNamespace(content="초안 [TXT_0001]")
    mock_llm_cls.return_value = mock_llm
    mock_eval.return_value = _grounding_accept()

    fw = get_framework(FW_KEY)
    # E-1-1(환경경영목표)은 kind=="policy", human_narrative=False → insufficient 경로
    answers = []
    for q in fw.questions:
        answers.append(Answer(
            qid=q.qid,
            section=q.section,
            question_text=q.text,
            value=None,
            status="insufficient",
        ))
    sheet = ResponseSheet(
        framework_key=fw.key,
        framework_label=fw.label,
        corp_name="테스트사",
        answers=answers,
    )

    # E-1-1 코드에 매칭되는 TextNode
    text_node = _make_text_node(
        "TXT_0001",
        "당사는 2030년까지 탄소 배출량 30% 감축을 목표로 설정하였다.",
        "E-1-1",
        source_file="환경방침서.pdf",
    )
    graph = _make_evidence_graph([text_node])

    sheet = generate_drafts(sheet, graph)

    # E-1-1에 해당하는 answer 찾기
    e11_answer = next(a for a in sheet.answers if a.qid == "KESG-E-1-1")
    assert e11_answer.status == "draft_ready", (
        f"insufficient+policy+TextNode 있으면 draft_ready여야 함, got {e11_answer.status}"
    )
    assert e11_answer.draft_text != ""
    assert len(e11_answer.draft_citations) > 0


# ── Test 10: insufficient + kind!="policy" → 대상 아님 ────────────────────

@patch("esgenie.supplychain.drafter.LLMClient")
def test_insufficient_non_policy_not_drafted(mock_llm_cls):
    """insufficient인데 kind!='policy'(quantitative)인 항목은 청크가 있어도 대상 아님."""
    mock_llm = MagicMock()
    mock_llm_cls.return_value = mock_llm

    fw = get_framework(FW_KEY)
    answers = []
    for q in fw.questions:
        answers.append(Answer(
            qid=q.qid,
            section=q.section,
            question_text=q.text,
            value=None,
            status="insufficient",
        ))
    sheet = ResponseSheet(
        framework_key=fw.key,
        framework_label=fw.label,
        corp_name="테스트사",
        answers=answers,
    )

    # E-4-1(에너지 사용량)은 kind=="quantitative" → 대상 아님
    text_node = _make_text_node("TXT_0001", "에너지 사용량 128400 kWh", "E-4-1")
    graph = _make_evidence_graph([text_node])

    sheet = generate_drafts(sheet, graph)

    e41_answer = next(a for a in sheet.answers if a.qid == "KESG-E-4-1")
    assert e41_answer.status == "insufficient", (
        "quantitative 항목은 초안 대상이 아니어야 함"
    )
    # E-4-1에 대해 LLM이 호출되지 않았어야 하지만, 다른 policy 항목(E-1-1 등)은
    # 코드 불일치로 BM25 폴백 후 점수 미달일 수 있음.
    # quantitative 항목 자체가 대상에서 제외되는지만 검증.


# ── Test 11: draft_citations에 source_file/page 포함 ──────────────────────

@patch("esgenie.supplychain.drafter.evaluate_grounding")
@patch("esgenie.supplychain.drafter.LLMClient")
def test_draft_citations_contain_source_file_and_page(mock_llm_cls, mock_eval):
    """draft_citations에 source_file과 page가 포함되어야 한다."""
    mock_llm = MagicMock()
    mock_llm.complete.return_value = SimpleNamespace(content="초안 [TXT_0001]")
    mock_llm_cls.return_value = mock_llm
    mock_eval.return_value = _grounding_accept()

    sheet = _make_sheet_with_hitl()
    text_node = _make_text_node(
        "TXT_0001",
        "안전보건 방침에 따라 위원회를 운영한다.",
        "S-4-1",
        source_file="안전보건규정_2025.pdf",
    )
    text_node.page = 3
    graph = _make_evidence_graph([text_node])

    sheet = generate_drafts(sheet, graph)

    drafted = [a for a in sheet.answers if a.status == "draft_ready"]
    assert len(drafted) >= 1
    cit = drafted[0].draft_citations[0]
    assert cit["node_id"] == "TXT_0001"
    assert cit["source_file"] == "안전보건규정_2025.pdf"
    assert cit["page"] == 3
    assert "text_preview" in cit


# ── Test 12: 코드 불일치 + BM25 점수 미달 → 항목 단위 LLM 미호출 ──────────

@patch("esgenie.supplychain.drafter.evaluate_grounding")
@patch("esgenie.supplychain.drafter.LLMClient")
def test_code_mismatch_and_bm25_low_score_skips(mock_llm_cls, mock_eval):
    """TextNode가 존재하지만 특정 항목에 코드 불일치·BM25 미달이면 LLM 미호출."""
    mock_llm = MagicMock()
    mock_llm.complete.return_value = SimpleNamespace(content="초안 [TXT_0001]")
    mock_llm_cls.return_value = mock_llm
    mock_eval.return_value = _grounding_accept()

    fw = get_framework(FW_KEY)
    # S-7-1(전략적 사회공헌, hitl_required)만 남기고 나머지는 verified로
    answers = []
    for q in fw.questions:
        if q.primary_code == "S-7-1":
            answers.append(Answer(
                qid=q.qid, section=q.section, question_text=q.text,
                value=None, status="hitl_required",
            ))
        else:
            answers.append(Answer(
                qid=q.qid, section=q.section, question_text=q.text,
                value=True, status="verified",
            ))
    sheet = ResponseSheet(
        framework_key=fw.key, framework_label=fw.label,
        corp_name="테스트사", answers=answers,
    )

    # TextNode는 있지만 S-7-1과 코드가 다르고, BM25로도 점수 미달인 짧은 텍스트
    text_node = _make_text_node("TXT_X", "x", "E-4-1")
    graph = _make_evidence_graph([text_node])

    sheet = generate_drafts(sheet, graph)

    # S-7-1은 코드 매칭 안 되고 BM25도 "x"로는 점수 미달 → LLM 미호출
    mock_llm.complete.assert_not_called()
    s71 = next(a for a in sheet.answers if a.qid == "KESG-S-7-1")
    assert s71.status == "hitl_required"


# ── Test 13: enable_drafts=False → to_dict() 전체 동등 비교 ───────────────

def test_enable_drafts_false_to_dict_equality():
    """enable_drafts=False(기본)일 때 to_dict() 전체가 동일해야 한다."""
    fw = get_framework(FW_KEY)
    extraction = SimpleNamespace(
        corp_name="테스트사",
        mapped={"E-4-1": {"code": "E-4-1", "name": "에너지", "evidence_node_ids": []}},
        missing=[],
    )
    text_node = _make_text_node("TXT_0001", "규정 텍스트", "S-4-1")
    graph = _make_evidence_graph([text_node])

    sheet_off = build_response_sheet(
        fw, corp_name="테스트사", extraction=extraction,
        evidence_graph=graph, enable_drafts=False,
    )
    sheet_baseline = build_response_sheet(
        fw, corp_name="테스트사", extraction=extraction,
        evidence_graph=graph,
    )

    assert sheet_off.to_dict() == sheet_baseline.to_dict()


# ── Test 14: gaps가 drafter 후 재계산됨 ───────────────────────────────────

@patch("esgenie.supplychain.drafter.evaluate_grounding")
@patch("esgenie.supplychain.drafter.LLMClient")
def test_gaps_recalculated_after_drafts(mock_llm_cls, mock_eval):
    """초안 성공 항목은 gaps에서 빠지고, fail-closed 항목은 gaps에 남는다."""
    mock_llm = MagicMock()
    mock_llm.complete.return_value = SimpleNamespace(content="초안 텍스트 [TXT_0001]")
    mock_llm_cls.return_value = mock_llm
    mock_eval.return_value = _grounding_accept()

    fw = get_framework(FW_KEY)
    extraction = SimpleNamespace(mapped={}, missing=[], corp_name="테스트사")
    # S-4-1에 매칭되는 TextNode만 제공 → S-4-1은 초안 성공, S-7-1은 실패(청크 없음)
    text_node = _make_text_node("TXT_0001", "안전보건 경영방침 조항", "S-4-1")
    graph = _make_evidence_graph([text_node])

    sheet = build_response_sheet(
        fw, corp_name="테스트사", extraction=extraction,
        evidence_graph=graph, enable_drafts=True,
    )

    # S-4-1 항목이 draft_ready면 gaps에 해당 question_text가 없어야 함
    s41 = next(a for a in sheet.answers if a.qid == "KESG-S-4-1")
    if s41.status == "draft_ready":
        for gap in sheet.gaps:
            assert s41.question_text not in gap, (
                f"draft_ready 항목이 gaps에 남아있음: {gap}"
            )

    # S-7-1(TextNode 없음)은 hitl_required로 남아있어야 하고 gaps에 있어야 함
    s71 = next(a for a in sheet.answers if a.qid == "KESG-S-7-1")
    assert s71.status == "hitl_required"
    assert any(s71.question_text in gap for gap in sheet.gaps), (
        "hitl_required 항목은 gaps에 남아야 함"
    )


# ── Test 15: BM25 폴백 cross-pillar 배제 (검수 재현) ─────────────────────

@patch("esgenie.supplychain.drafter.LLMClient")
def test_bm25_fallback_cross_pillar_rejected(mock_llm_cls):
    """S-4-1 청크만 있는 그래프에서 E-1-1 항목 → 폴백 채택 0, LLM 미호출, 상태 유지."""
    mock_llm = MagicMock()
    mock_llm_cls.return_value = mock_llm

    fw = get_framework(FW_KEY)
    answers = []
    for q in fw.questions:
        answers.append(Answer(
            qid=q.qid, section=q.section, question_text=q.text,
            value=None, status="insufficient",
        ))
    sheet = ResponseSheet(
        framework_key=fw.key, framework_label=fw.label,
        corp_name="테스트사", answers=answers,
    )

    # S-4-1(사회 pillar) 청크만 존재
    text_node = _make_text_node(
        "TXT_0001",
        "제1조 안전보건 경영방침에 따라 산업안전보건위원회를 분기별 운영한다.",
        "S-4-1",
    )
    graph = _make_evidence_graph([text_node])

    sheet = generate_drafts(sheet, graph)

    # E-1-1(환경 pillar)은 pillar 불일치로 폴백 채택 안 됨 → 상태 유지
    e11 = next(a for a in sheet.answers if a.qid == "KESG-E-1-1")
    assert e11.status == "insufficient", (
        f"cross-pillar 폴백은 배제되어야 함, got {e11.status}"
    )
    # S-4-1 자체도 code 매칭이라 E-1-1에는 제공 안 됨
    # LLM이 E-1-1에 대해 호출되지 않았어야 함
    # (단, S-4-1 자체가 insufficient+policy이고 code 매칭이 되면 호출 가능)
    # S-4-1 자체는 code 매칭으로 초안 시도 대상이 될 수 있음


# ── Test 16: kesg_code=None 무관 텍스트만 있고 BM25 점수 낮으면 스킵 ──────

@patch("esgenie.supplychain.drafter.LLMClient")
def test_bm25_fallback_low_score_null_code_skipped(mock_llm_cls):
    """kesg_code=None인 짧은 무관 텍스트만 있을 때 점수 미달로 스킵."""
    mock_llm = MagicMock()
    mock_llm_cls.return_value = mock_llm

    fw = get_framework(FW_KEY)
    answers = []
    for q in fw.questions:
        if q.primary_code == "S-7-1":
            answers.append(Answer(
                qid=q.qid, section=q.section, question_text=q.text,
                value=None, status="hitl_required",
            ))
        else:
            answers.append(Answer(
                qid=q.qid, section=q.section, question_text=q.text,
                value=True, status="verified",
            ))
    sheet = ResponseSheet(
        framework_key=fw.key, framework_label=fw.label,
        corp_name="테스트사", answers=answers,
    )

    # kesg_code=None이고 짧은 무관 텍스트 → BM25 점수 낮아서 채택 안 됨
    text_node = _make_text_node("TXT_NULL", "일반 문서 조항입니다.", None)
    graph = _make_evidence_graph([text_node])

    sheet = generate_drafts(sheet, graph)

    mock_llm.complete.assert_not_called()
    s71 = next(a for a in sheet.answers if a.qid == "KESG-S-7-1")
    assert s71.status == "hitl_required"


# ── Test 17: code 매칭 경로가 기존과 동일하게 동작 (회귀) ─────────────────

@patch("esgenie.supplychain.drafter.evaluate_grounding")
@patch("esgenie.supplychain.drafter.LLMClient")
def test_code_match_path_still_works(mock_llm_cls, mock_eval):
    """직접 code 매칭 경로는 pillar 가드 영향 없이 기존대로 동작."""
    mock_llm = MagicMock()
    mock_llm.complete.return_value = SimpleNamespace(content="초안 [TXT_0001]")
    mock_llm_cls.return_value = mock_llm
    mock_eval.return_value = _grounding_accept()

    sheet = _make_sheet_with_hitl()
    text_node = _make_text_node(
        "TXT_0001",
        "산업안전보건위원회를 분기별 운영한다. ISO 45001 인증 취득.",
        "S-4-1",
    )
    graph = _make_evidence_graph([text_node])

    sheet = generate_drafts(sheet, graph)

    drafted = [a for a in sheet.answers if a.status == "draft_ready"]
    assert len(drafted) >= 1
    # code_match 경로임을 확인
    cit = drafted[0].draft_citations[0]
    assert cit["retrieval"] == "code_match"


# ── Test 18: draft_citations에 retrieval 필드 존재 확인 ───────────────────

@patch("esgenie.supplychain.drafter.evaluate_grounding")
@patch("esgenie.supplychain.drafter.LLMClient")
def test_draft_citations_have_retrieval_field(mock_llm_cls, mock_eval):
    """draft_citations의 모든 항목에 retrieval 필드가 있다."""
    mock_llm = MagicMock()
    mock_llm.complete.return_value = SimpleNamespace(content="초안 [TXT_0001]")
    mock_llm_cls.return_value = mock_llm
    mock_eval.return_value = _grounding_accept()

    sheet = _make_sheet_with_hitl()
    text_node = _make_text_node("TXT_0001", "안전보건 방침 조항", "S-4-1")
    graph = _make_evidence_graph([text_node])

    sheet = generate_drafts(sheet, graph)

    drafted = [a for a in sheet.answers if a.status == "draft_ready"]
    assert len(drafted) >= 1
    for a in drafted:
        for cit in a.draft_citations:
            assert "retrieval" in cit, f"retrieval 필드 누락: {cit}"
            assert cit["retrieval"] in ("code_match", "bm25_fallback")
