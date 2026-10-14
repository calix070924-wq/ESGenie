"""Whole-question conclusions must not be inferred from partial policy evidence."""
from copy import deepcopy
from dataclasses import replace

import pytest

from esgenie.ssot.evidence_graph import EvidenceGraph, merge_ocr_extraction
from esgenie.ssot.ocr_router import DocChannel, ExtractedClause, OcrExtraction
from esgenie.supplychain.frameworks import get_framework
from esgenie.supplychain.qualitative_review import require_qualitative_review
from esgenie.supplychain.responder import build_response_sheet
from esgenie.supplychain.schema import Answer
from esgenie.web.presenter import present_sheet
from esgenie.web.review import reconcile, review_rows


PARTIAL = [
    ('C-2', '스크랩 기록', '금속 스크랩을 공정 내부에 재투입한다.'),
    ('C-4', '폐기물 기록', '외부 위탁 폐기물의 중량을 월별로 기록한다.'),
    ('D-2', '거래 규정', '금품·부당한 편의 제공을 금지한다.'),
    ('D-6', '신고 절차', '근무자는 익명 신고할 수 있고 신고자에게 보복하지 않는다.'),
    ('D-8', '자료 보호', '근태 제공본에서 임직원 실명과 서명을 제외한다.'),
    ('E-6', '안전 교육 실시', '파견 근로자에게 안전 교육을 실시하였다.'),
]


def sheet_for(code, section, text):
    graph = EvidenceGraph('SYN', '검증기업')
    extraction = OcrExtraction(
        '운영자료.pdf', DocChannel.UNSTRUCTURED, 'policy_manual',
        clauses=[ExtractedClause(section=section, text=text, page=0, rba_code_guess=code)],
        raw_text='2024년 8월 / 대전 제3공장\n' + section + '\n' + text,
    )
    merge_ocr_extraction(graph, extraction, report_year=2024)
    return build_response_sheet('rba42', evidence_graph=graph)


@pytest.mark.parametrize('code,section,text', PARTIAL)
def test_partial_evidence_is_visible_without_a_whole_question_yes(code, section, text):
    sheet = sheet_for(code, section, text)
    answer = next(a for a in sheet.answers if a.qid == 'RBA-' + code)
    assert answer.value is None and answer.status == 'flagged'
    assert any(text in link.quote for link in answer.evidence_links)
    assert not answer.evidence_needed  # A review limit is not missing evidence.
    assert '담당자 확인 필요' in answer.review_note
    assert '응답: 예' not in answer.rationale
    shown = next(row for row in present_sheet(sheet.to_dict(), []) if row['id'] == answer.qid)
    assert shown['status'] == 'review' and shown['needs_attention']
    assert shown['value_text'] != '예'
    assert shown['sources']


@pytest.mark.parametrize('code,section,text', PARTIAL)
def test_review_guard_keeps_source_facts_claims_scope_and_conflicts(code, section, text):
    q = next(q for q in get_framework('rba42').questions if q.qid == 'RBA-' + code)
    a = next(a for a in sheet_for(code, section, text).answers if a.qid == q.qid)
    a = replace(a, value=True, status='flagged',
                self_reports=[{'yn': '예', 'text': '회사 작성 답변'}],
                flags=['별도 조항 검사 미충족'], comparison='mismatch',
                comparison_reason='독립 기록과 회사 답변 충돌')
    before = deepcopy(a.to_dict())
    require_qualitative_review(a, q)
    for field in ('evidence_links', 'reference_links', 'self_reports', 'flags',
                  'boundary', 'boundary_label', 'comparison', 'comparison_reason'):
        assert a.to_dict()[field] == before[field]
    assert a.value is None and a.status == 'flagged'


@pytest.mark.parametrize('code,section,text', PARTIAL)
@pytest.mark.parametrize('value,status', [(False, 'verified'), (False, 'flagged'),
                                         (None, 'insufficient'), (None, 'not_applicable')])
def test_negative_unresolved_and_excluded_answers_are_not_rewritten(code, section, text, value, status):
    q = next(q for q in get_framework('rba42').questions if q.qid == 'RBA-' + code)
    a = Answer(q.qid, q.section, q.text, value, status, rationale='기존 판단')
    before = deepcopy(a.to_dict())
    require_qualitative_review(a, q)
    assert a.to_dict() == before


def test_even_explicit_training_program_remains_reviewable_and_human_completion_works():
    text = ('관리자와 근로자를 대상으로 방침·절차·개선목표 이행과 법규 충족을 위한 '
            '교육 프로그램을 실시하였다.')
    sheet = sheet_for('E-6', '교육 실시', text)
    raw = next(a for a in sheet.answers if a.qid == 'RBA-E-6')
    assert raw.value is None and raw.status == 'flagged'
    assert any(text in e.quote for e in raw.evidence_links)
    project = {'framework': 'rba42', 'documents': [], 'result': {'sheet': sheet.to_dict()}}
    shown = present_sheet(sheet.to_dict(), [])
    before = next(r for r in review_rows(project, shown) if r['id'] == raw.qid)
    values = {**before['saved'], 'answer': '예', 'reason': '대상과 교육 내용을 원문에서 확인'}
    project['reviews'] = {raw.qid: {'values': values, 'status': 'complete',
                                  'method': '담당자 수정', 'history': []}}
    after = next(r for r in review_rows(project, shown) if r['id'] == raw.qid)
    assert after['value_text'] == '예' and after['review_status'] == 'complete'
    assert after['automatic']['answer'] == ''
    assert raw.value is None and raw.status == 'flagged'


def test_reanalysis_requires_review_again_without_erasing_the_human_answer():
    sheet = sheet_for(*PARTIAL[0])
    current = {'sheet': sheet.to_dict()}
    previous = deepcopy(current)
    old = next(a for a in previous['sheet']['answers'] if a['qid'] == 'RBA-C-2')
    old.update(value=True, status='verified', rationale='응답: 예', scope_notes=[])
    project = {'reviews': {'RBA-C-2': {'status': 'complete', 'values': {'answer': '예'}}}}
    reconcile(project, previous, current)
    assert project['affected_questions'] == ['RBA-C-2']
    assert project['reviews']['RBA-C-2']['status'] == 'again'
    assert project['reviews']['RBA-C-2']['values']['answer'] == '예'


@pytest.mark.parametrize('qid,value,status,comparison', [
    ('RBA-C-4-E-6-1', 18.4, 'self_reported', 'scope_unconfirmed'),
    ('RBA-C-4-E-6-2', 29.3, 'flagged', 'not_comparable'),
    ('RBA-E-7', True, 'verified', ''),
])
def test_numeric_subquestions_and_communication_are_outside_review_limit(qid, value, status, comparison):
    q = next(q for q in get_framework('rba42').questions if q.qid == qid)
    a = Answer(q.qid, q.section, q.text, value, status, comparison=comparison)
    before = deepcopy(a.to_dict())
    require_qualitative_review(a, q)
    assert a.to_dict() == before
