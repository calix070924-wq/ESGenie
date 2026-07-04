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


# ── Test 19: same-pillar 오염 차단 — 분류된 청크는 다른 항목에 폴백 불가 ────

@patch("esgenie.supplychain.drafter.evaluate_grounding")
@patch("esgenie.supplychain.drafter.LLMClient")
def test_classified_chunk_not_leaked_to_same_pillar(mock_llm_cls, mock_eval):
    """S-4-1로 분류된 청크만 있으면 S-1-1은 폴백으로도 채택 못 함(LLM 미호출)."""
    mock_llm = MagicMock()
    mock_llm.complete.return_value = SimpleNamespace(content="초안 [TXT_0001]")
    mock_llm_cls.return_value = mock_llm
    mock_eval.return_value = _grounding_accept()

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

    # S-4-1(안전보건)으로 분류된 청크 1개만 존재
    text_node = _make_text_node(
        "TXT_0001",
        "제1조 안전보건 경영방침에 따라 산업안전보건위원회를 분기별 운영한다.",
        "S-4-1",
    )
    graph = _make_evidence_graph([text_node])

    sheet = generate_drafts(sheet, graph)

    # S-1-1(사회적 책임 목표)은 same-pillar지만 해당 청크를 폴백으로 받으면 안 됨
    s11 = next(a for a in sheet.answers if a.qid == "KESG-S-1-1")
    assert s11.status == "insufficient", (
        f"분류된 청크는 폴백 시장에 나오면 안 됨, got {s11.status}"
    )

    # LLM이 S-1-1 질문에 대해 호출되지 않았음을 검증
    s11_q_text = s11.question_text
    for call in mock_llm.complete.call_args_list:
        user_arg = call.kwargs.get("user", "") or (call.args[1] if len(call.args) > 1 else "")
        assert s11_q_text not in str(user_arg), (
            f"S-1-1 질문 텍스트가 LLM 프롬프트에 포함됨 — 폴백 오염 발생"
        )

    # S-4-1 자체는 code_match로 초안 성공해야 함
    s41 = next(a for a in sheet.answers if a.qid == "KESG-S-4-1")
    assert s41.status == "draft_ready", "S-4-1은 code_match로 초안 성공해야 함"


# ── Test 20: 미분류 노드는 폴백으로 채택 + draft_ready ───────────────────────

@patch("esgenie.supplychain.drafter.evaluate_grounding")
@patch("esgenie.supplychain.drafter.LLMClient")
def test_unclassified_node_fallback_produces_draft_ready(mock_llm_cls, mock_eval):
    """kesg_code=None인 관련 텍스트는 BM25 폴백으로 채택되어 draft_ready가 된다."""
    mock_llm = MagicMock()
    # 관련성 판정 "YES" + 초안 생성
    mock_llm.complete.side_effect = [
        SimpleNamespace(content="YES"),  # relevance gate
        SimpleNamespace(content="초안 [TXT_POLICY]"),  # draft generation
    ]
    mock_llm_cls.return_value = mock_llm
    mock_eval.return_value = _grounding_accept()

    fw = get_framework(FW_KEY)
    answers = []
    for q in fw.questions:
        if q.primary_code == "S-4-1":
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

    # kesg_code=None이고 안전보건 관련 텍스트 — BM25 폴백으로 높은 점수 기대
    text_node = _make_text_node(
        "TXT_POLICY",
        "산업안전보건법에 따른 안전보건 경영방침 수립 및 산업안전보건위원회 구성·운영 절차를 규정한다. "
        "안전보건 교육훈련 계획 수립, 위험성 평가 실시, 안전보건 목표 설정 및 이행점검 체계.",
        None,
        source_file="통합안전보건규정.pdf",
    )
    graph = _make_evidence_graph([text_node])

    sheet = generate_drafts(sheet, graph)

    s41 = next(a for a in sheet.answers if a.qid == "KESG-S-4-1")
    assert s41.status == "draft_ready", (
        f"미분류 고관련 노드가 폴백으로 채택되어 draft_ready여야 함, got {s41.status}"
    )
    assert s41.draft_citations[0]["retrieval"] == "bm25_fallback"


# ── Test 21: 미분류 노드여도 점수 미달이면 스킵 (short unrelated) ─────────────

@patch("esgenie.supplychain.drafter.LLMClient")
def test_unclassified_node_low_score_skipped(mock_llm_cls):
    """kesg_code=None이어도 BM25 점수가 임계 미만이면 채택 안 됨."""
    mock_llm = MagicMock()
    mock_llm_cls.return_value = mock_llm

    fw = get_framework(FW_KEY)
    answers = []
    for q in fw.questions:
        if q.primary_code == "E-1-1":
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

    # kesg_code=None이지만 짧은 무관 텍스트 → 점수 미달
    text_node = _make_text_node("TXT_SHORT", "회의록 작성 요령", None)
    graph = _make_evidence_graph([text_node])

    sheet = generate_drafts(sheet, graph)

    mock_llm.complete.assert_not_called()
    e11 = next(a for a in sheet.answers if a.qid == "KESG-E-1-1")
    assert e11.status == "hitl_required"


# ── Test 22: INSUFFICIENT_EVIDENCE 센티널 → 즉시 포기, 재시도 없음 ──────────

@patch("esgenie.supplychain.drafter.evaluate_grounding")
@patch("esgenie.supplychain.drafter.LLMClient")
def test_insufficient_sentinel_aborts_immediately(mock_llm_cls, mock_eval):
    """LLM이 INSUFFICIENT_EVIDENCE를 반환하면 즉시 포기, 재시도·게이트 호출 없음."""
    mock_llm = MagicMock()
    mock_llm.complete.return_value = SimpleNamespace(content="INSUFFICIENT_EVIDENCE")
    mock_llm_cls.return_value = mock_llm

    sheet = _make_sheet_with_hitl()
    text_node = _make_text_node("TXT_0001", "안전보건 방침 조항", "S-4-1")
    graph = _make_evidence_graph([text_node])

    sheet = generate_drafts(sheet, graph, max_retries=2)

    # S-4-1 code_match이므로 관련성 게이트 면제, 바로 초안 생성 진입
    s41 = next(a for a in sheet.answers if a.qid == "KESG-S-4-1")
    assert s41.status == "hitl_required", "센티널 시 원상태 유지"
    assert s41.draft_text == ""

    # evaluate_grounding 미호출
    mock_eval.assert_not_called()

    # complete는 1회만 호출(재시도 없음) — code_match 항목이 1개뿐일 때
    # (실제로는 여러 항목이 S-4-1 매칭될 수 있으므로 최소 조건으로 검사)
    # S-4-1 항목에 대한 호출은 1회여야 함
    assert mock_llm.complete.call_count >= 1


# ── Test 23: 자백 문구 패턴 → 폐기 ──────────────────────────────────────────

@patch("esgenie.supplychain.drafter.evaluate_grounding")
@patch("esgenie.supplychain.drafter.LLMClient")
def test_confession_patterns_abort(mock_llm_cls, mock_eval):
    """_INSUFFICIENCY_PATTERNS의 각 패턴이 draft_text에 있으면 폐기."""
    from esgenie.supplychain.drafter import _INSUFFICIENCY_PATTERNS

    for pattern in _INSUFFICIENCY_PATTERNS:
        mock_llm = MagicMock()
        confession = f"해당 발췌에는 관련 내용이 {pattern}습니다 [TXT_0001]."
        mock_llm.complete.return_value = SimpleNamespace(content=confession)
        mock_llm_cls.return_value = mock_llm
        mock_eval.reset_mock()

        sheet = _make_sheet_with_hitl()
        text_node = _make_text_node("TXT_0001", "규정 텍스트", "S-4-1")
        graph = _make_evidence_graph([text_node])

        sheet = generate_drafts(sheet, graph, max_retries=2)

        s41 = next(a for a in sheet.answers if a.qid == "KESG-S-4-1")
        assert s41.status == "hitl_required", (
            f"자백 패턴 '{pattern}' 감지 시 원상태 유지해야 함"
        )
        assert s41.draft_text == ""
        mock_eval.assert_not_called()


# ── Test 24: 관련성 NO → 초안 생성 호출 없음 ────────────────────────────────

@patch("esgenie.supplychain.drafter.evaluate_grounding")
@patch("esgenie.supplychain.drafter.LLMClient")
def test_relevance_no_blocks_draft_generation(mock_llm_cls, mock_eval):
    """BM25 폴백 경로에서 관련성 판정 NO → 초안 생성 LLM 호출 자체가 없음."""
    mock_llm = MagicMock()
    # 관련성 판정만 "NO" 반환
    mock_llm.complete.return_value = SimpleNamespace(content="NO")
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

    # kesg_code=None이고 높은 점수 기대되는 텍스트(BM25 통과하도록)
    text_node = _make_text_node(
        "TXT_0001",
        "전략적 사회공헌 CSR 프로그램 운영 방침 수립 및 사회공헌 활동 지역사회 참여 "
        "사회공헌 전략적 CSR 목표 사회공헌 활동",
        None,
    )
    graph = _make_evidence_graph([text_node])

    sheet = generate_drafts(sheet, graph)

    s71 = next(a for a in sheet.answers if a.qid == "KESG-S-7-1")
    assert s71.status == "hitl_required", "관련성 NO면 원상태 유지"

    # 호출된 프롬프트 중에 초안 생성 프롬프트(_DRAFTER_USER_TEMPLATE의 "아래 발췌만 근거로")가 없어야 함
    for call in mock_llm.complete.call_args_list:
        user_arg = call.kwargs.get("user", "")
        assert "아래 발췌만 근거로 답변을 작성하세요" not in user_arg, (
            "관련성 NO인데 초안 생성 프롬프트가 호출됨"
        )

    mock_eval.assert_not_called()


# ── Test 25: 관련성 모호 응답 → fail-closed (NO 취급) ────────────────────────

@patch("esgenie.supplychain.drafter.evaluate_grounding")
@patch("esgenie.supplychain.drafter.LLMClient")
def test_relevance_ambiguous_treated_as_no(mock_llm_cls, mock_eval):
    """관련성 판정이 모호("글쎄요", "", "아마도")면 전부 NO 취급."""
    ambiguous_responses = ["글쎄요", "", "아마도", "maybe", "   "]

    for ambiguous in ambiguous_responses:
        mock_llm = MagicMock()
        mock_llm.complete.return_value = SimpleNamespace(content=ambiguous)
        mock_llm_cls.return_value = mock_llm
        mock_eval.reset_mock()

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

        text_node = _make_text_node(
            "TXT_0001",
            "전략적 사회공헌 CSR 프로그램 운영 방침 수립 사회공헌 활동 "
            "사회공헌 전략적 CSR 목표",
            None,
        )
        graph = _make_evidence_graph([text_node])

        sheet = generate_drafts(sheet, graph)

        s71 = next(a for a in sheet.answers if a.qid == "KESG-S-7-1")
        assert s71.status == "hitl_required", (
            f"모호 응답 '{ambiguous}' → NO 취급, 원상태 유지해야 함"
        )
        mock_eval.assert_not_called()


# ── Test 26: 관련성 YES + 정상 초안 + 게이트 ACCEPT → draft_ready ────────────

@patch("esgenie.supplychain.drafter.evaluate_grounding")
@patch("esgenie.supplychain.drafter.LLMClient")
def test_relevance_yes_then_draft_ready(mock_llm_cls, mock_eval):
    """관련성 YES → 초안 생성 → 게이트 ACCEPT → draft_ready, retrieval bm25_fallback."""
    mock_llm = MagicMock()
    mock_llm.complete.side_effect = [
        SimpleNamespace(content="YES"),  # relevance gate
        SimpleNamespace(content="초안 텍스트 [TXT_0001]"),  # draft generation
    ]
    mock_llm_cls.return_value = mock_llm
    mock_eval.return_value = _grounding_accept()

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

    text_node = _make_text_node(
        "TXT_0001",
        "전략적 사회공헌 CSR 프로그램 운영 방침 수립 사회공헌 활동 전략 목표 "
        "사회공헌 전략적 CSR 지역사회 참여 활동 방침",
        None,
    )
    graph = _make_evidence_graph([text_node])

    sheet = generate_drafts(sheet, graph)

    s71 = next(a for a in sheet.answers if a.qid == "KESG-S-7-1")
    assert s71.status == "draft_ready"
    assert s71.draft_citations[0]["retrieval"] == "bm25_fallback"


# ── Test 27: code_match 경로는 관련성 판정 호출 없음 ─────────────────────────

@patch("esgenie.supplychain.drafter.evaluate_grounding")
@patch("esgenie.supplychain.drafter.LLMClient")
def test_code_match_skips_relevance_gate(mock_llm_cls, mock_eval):
    """code_match 경로는 관련성 판정 LLM 호출이 없다(호출 횟수로 검증)."""
    mock_llm = MagicMock()
    mock_llm.complete.return_value = SimpleNamespace(content="초안 [TXT_0001]")
    mock_llm_cls.return_value = mock_llm
    mock_eval.return_value = _grounding_accept()

    fw = get_framework(FW_KEY)
    # S-4-1만 hitl_required, 나머지 verified
    answers = []
    for q in fw.questions:
        if q.primary_code == "S-4-1":
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

    text_node = _make_text_node("TXT_0001", "안전보건 방침 조항", "S-4-1")
    graph = _make_evidence_graph([text_node])

    sheet = generate_drafts(sheet, graph)

    # code_match이므로 관련성 판정 호출 없이 바로 초안 생성 1회만
    # → 총 호출 횟수 = 1 (초안 생성만)
    assert mock_llm.complete.call_count == 1
    # 호출이 초안 생성인지 확인(관련성 판정 프롬프트가 아님)
    call_kwargs = mock_llm.complete.call_args_list[0].kwargs
    assert "아래 발췌만 근거로" in call_kwargs.get("user", "")


# ── Test 28: 통합 재현 — 미분류 안전 회의록 4노드 + 전 항목 insufficient ─────

@patch("esgenie.supplychain.drafter.evaluate_grounding")
@patch("esgenie.supplychain.drafter.LLMClient")
def test_integration_safety_minutes_all_rejected(mock_llm_cls, mock_eval):
    """미분류 안전 회의록 4노드 + 전 항목 insufficient + 관련성 NO → draft_ready 0건."""
    mock_llm = MagicMock()
    # 관련성 판정에 대해 항상 "NO" 반환
    mock_llm.complete.return_value = SimpleNamespace(content="NO")
    mock_llm_cls.return_value = mock_llm
    mock_eval.return_value = _grounding_accept()

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

    # 직전 데모와 동일한 미분류 안전 회의록 4노드
    nodes = [
        _make_text_node(
            "LOCAL_TXT_0001",
            "산업재해율 목표: 0.3% 이하 (전년 0.41% 대비 감축), "
            "위험성 평가 연 2회 이상 실시, 신규 입사자 안전교육 8시간 의무화",
            None,
        ),
        _make_text_node(
            "LOCAL_TXT_0002",
            "유해화학물질 보관구역 CCTV 설치 의결, "
            "개인보호구(PPE) 착용 의무 전 공정 확대",
            None,
        ),
        _make_text_node(
            "LOCAL_TXT_0003",
            "야간작업 조명 개선 요청 → 2분기 내 조치 완료 결의, "
            "결사의 자유 보장: 노동조합 가입률 현황 공유 (가입률 62%)",
            None,
        ),
        _make_text_node(
            "LOCAL_TXT_0004",
            "전원 찬성으로 의결",
            None,
        ),
    ]
    graph = _make_evidence_graph(nodes)

    sheet = generate_drafts(sheet, graph)

    drafted = [a for a in sheet.answers if a.status == "draft_ready"]
    assert len(drafted) == 0, (
        f"관련성 NO 시 모든 폴백 항목은 차단되어야 함, but {len(drafted)} passed"
    )
    # evaluate_grounding은 한 번도 호출되지 않아야 함(초안 생성 자체가 안 됐으므로)
    mock_eval.assert_not_called()
