"""Independent synthetic controls for requirement coverage, polarity and source scope.

No OCR/LLM calls or rehearsal labels. Saved real extraction checks live in the replay
script; these fixtures deliberately use different names, periods and expressions.
"""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from esgenie.ssot.evidence_graph import EvidenceGraph, merge_ocr_extraction
from esgenie.ssot.ocr_router import OcrExtraction, ExtractedClause, DocChannel
from esgenie.supplychain.responder import build_response_sheet


NORMAL = {
    'B-6': [
        ('설비 위험평가', '생산 기계의 끼임 위험을 평가하고 방호장치를 설치해 접근을 차단한다.'),
        ('기계 전수 기능시험', '모든 기계의 방호장치 기능을 전수 점검했다. 누락은 없었다.'),
        ('정비 실행', '08-19 기계 방호덮개 예방정비와 재체결을 완료하고 기능을 재시험했다.'),
    ],
    'E-2': [
        ('경영 책임', '대표이사의 경영 책임과 권한을 지정한다. 대표이사는 월 1회 경영진 검토 회의를 주재한다.'),
        ('경영진 검토 실시 기록', '08-30 회의를 실시하고 대표이사와 업무 담당자가 결정 사항을 확인했다.'),
    ],
    'E-7': [
        ('전달 절차', 'ESG 방침·관행·기대·성과를 근로자, 공급사, 고객에게 전달한다. 경영지원이 작성하고 승인 뒤 배포한다.'),
        ('전달 대장', '근로자, 공급사, 고객 모두 안내를 수령·이해 확인했다. 문의를 접수해 담당자가 회신했다.'),
        ('수신자별 전달 확인', '전자 열람 기록에서 수신자별 안내 수령과 이해 확인 완료를 대조했다.'),
    ],
    'E-10': [
        ('시정 절차', '내부 점검과 외부 평가에서 발견된 미흡사항을 접수한다. 담당과 기한을 지정해 시정조치를 한다.'),
        ('시정 종결 기록', '08-22 조치 효과를 다른 확인자가 검증하고 종결했다. 기한 내 완료를 확인했다.'),
    ],
    'E-11': [
        ('문서 절차', '원본 문서를 생성하고 승인한다. 정정 버전과 원본을 보관한다. 개인정보 접근권한을 제한한다. '
         '기록 보존기간 이후 승인받아 폐기한다. 백업 표본을 복구하고 원본과 대조한다.'),
        ('실제 보호·복구 점검', '08-21 원본 백업을 복구해 문자 일치를 확인했다. 접근권한 제한 유지와 접근 차단을 확인했다.'),
    ],
}
HEADER = '2024년 8월 기록 / 대전 제3공장·아산 제4공장'


def graph_for(pages, *, name='운영기록.pdf', header=HEADER, tag=None, origin='policy_manual'):
    graph = EvidenceGraph('SYN', '합성기업')
    clauses = [ExtractedClause(section=section, text=text, page=page, rba_code_guess=tag)
               for page, (section, text) in enumerate(pages)]
    ext = OcrExtraction(name, DocChannel.UNSTRUCTURED, origin, clauses=clauses,
                        raw_text=header + '\n' + '\n'.join(s + '\n' + t for s, t in pages))
    merge_ocr_extraction(graph, ext, report_year=2099)
    return graph


def live_split_delivery_graph():
    fixture = json.loads((Path(__file__).parent / 'fixtures' /
                          'rehearsal_e7_live_20261012.json').read_text())
    graph = EvidenceGraph('LIVE_SPLIT', '가상기업')
    ext = OcrExtraction(fixture['source_file'], DocChannel.UNSTRUCTURED, 'policy_manual',
                        clauses=[ExtractedClause(**c) for c in fixture['clauses']],
                        raw_text=fixture['raw_text'])
    merge_ocr_extraction(graph, ext, report_year=2099)
    return graph


def test_live_e7_delivery_records_split_into_different_sections_remain_verified():
    graph = live_split_delivery_graph()
    a = answer(graph, 'E-7')
    assert a.value is True and a.status == 'verified', a.rationale
    assert {e.page for e in a.evidence_links} == {0, 1, 2}
    for text in ('6곳', 'SUP-01', 'CUS-01'):
        assert any(text in e.quote for e in a.evidence_links)


@pytest.mark.parametrize('case', ['missing_supplier', 'missing_customer', 'negative_customer',
                                 'future_customer', 'other_period', 'other_site', 'other_file',
                                 'other_page', 'unknown_page', 'title_only'])
def test_live_split_delivery_does_not_borrow_missing_or_foreign_receipts(case):
    graph = live_split_delivery_graph()
    supplier = next(n for n in graph.text_nodes.values() if n.section == '공급사 안내문 전달·수령 및 확인')
    customer = next(n for n in graph.text_nodes.values() if n.section == '고객 안내문 전달·수령')
    if case.startswith('missing_'):
        del graph.text_nodes[(supplier if case == 'missing_supplier' else customer).id]
    elif case == 'negative_customer':
        customer.text = 'CUS-01 고객에게 안내문을 발송했으나 수령 확인을 하지 않았다.'
    elif case == 'future_customer':
        customer.text = 'CUS-01 고객의 안내문 수령 확인은 향후 해야 한다.'
    elif case == 'other_period':
        customer.text = '적용 기간: 2025년 7월 | 사업장: 김해 제1공장, 양산 제2공장 | ' + customer.text
    elif case == 'other_site':
        customer.text = '적용 기간: 2026-04-01 | 사업장: 울산 제9공장 | ' + customer.text
    elif case == 'other_file':
        customer.source_file = '다른_전달기록.pdf'
        graph.source_texts[customer.source_file] = graph.source_texts[supplier.source_file]
    elif case == 'other_page':
        customer.page = 4
    elif case == 'unknown_page':
        customer.page = None
    else:
        customer.section = '고객 CUS-01 수령 확인 완료'
        customer.text = '고객 연락처와 전달 예정일을 보관한다.'
    a = answer(graph, 'E-7')
    assert a.value is None and a.status == 'insufficient', (case, a.rationale)
    assert '공급사·고객 전달 운영' in a.evidence_needed


@pytest.mark.parametrize('receipt', ['수령 확인', '수령 확인을 향후 해야 한다', '안내 전달 완료'])
def test_split_page_does_not_combine_recipient_names_with_an_unowned_receipt(receipt):
    graph = live_split_delivery_graph()
    for node in graph.text_nodes.values():
        if node.section == '공급사 안내문 전달·수령 및 확인':
            node.text = 'SUP-01 거래 공급사의 안내문 발송 주소를 기록했다.'
        elif node.section == '고객 안내문 전달·수령':
            node.text = 'CUS-01 고객의 안내문 발송 주소를 기록했다.'
    from esgenie.ssot.evidence_graph import TextNode
    graph.add_text_node(TextNode('unowned', '별도 확인', receipt, None,
                                next(iter(graph.source_texts)), page=1))
    assert answer(graph, 'E-7').value is None


def test_split_page_receipts_work_with_independent_section_names_and_filename():
    graph = live_split_delivery_graph()
    original = next(iter(graph.source_texts))
    graph.source_texts = {'다른_가상기업_전달대장.pdf': graph.source_texts[original]}
    for index, node in enumerate(graph.text_nodes.values()):
        node.source_file = '다른_가상기업_전달대장.pdf'
        node.section = f'OCR 단락 {index}'
    a = answer(graph, 'E-7')
    assert a.value is True and a.status == 'verified'
    assert {e.file_name for e in a.evidence_links} == {'다른_가상기업_전달대장.pdf'}


def test_delivered_notice_about_future_plans_is_still_an_actual_receipt():
    graph = live_split_delivery_graph()
    customer = next(n for n in graph.text_nodes.values() if n.section == '고객 안내문 전달·수령')
    customer.text = 'CUS-01 고객에게 향후 사업 계획 안내문을 04-30 11:20 발송한 후 14:00 수령 확인했다.'
    assert answer(graph, 'E-7').value is True


@pytest.mark.parametrize('recipient,section', [
    ('CUS-01 고객', '고객 안내문 전달·수령'),
    ('SUP-01 공급사', '공급사 안내문 전달·수령 및 확인'),
])
@pytest.mark.parametrize('description', [
    '에게 안내문을 발송하고 수령 확인 요청을 보냈다.',
    '에게 안내문을 발송했으며 수령 확인 대기 중이다.',
    '에게 안내문을 전달하고 수령 확인을 내일 진행할 계획이다.',
    '에게 안내문과 수령 확인 양식을 발송했다.',
    '에게 안내문을 발송하고 수령 확인 요청을 보냈다. 아직 회신은 없다.',
])
def test_split_delivery_requires_completed_receipts_not_requests_or_pending_actions(recipient, section, description):
    graph = live_split_delivery_graph()
    node = next(n for n in graph.text_nodes.values() if n.section == section)
    node.text = recipient + description
    # No old completed receipt remains in raw text; only the original scope header.
    graph.source_texts = {name: '한울정밀공업(주) / 절차 시행 2026-04-01 / 김해 제1공장·양산 제2공장\n'
                          for name in graph.source_texts}
    a = answer(graph, 'E-7')
    assert a.value is None and a.status == 'insufficient', (node.text, a.rationale)
    assert '공급사·고객 전달 운영' in a.evidence_needed


@pytest.mark.parametrize('recipient,section', [
    ('CUS-01 고객', '고객 안내문 전달·수령'),
    ('SUP-01 공급사', '공급사 안내문 전달·수령 및 확인'),
])
@pytest.mark.parametrize('description', [
    '에게 안내문을 발송하고 수령 확인을 마쳤다.',
    '에게 향후 사업 계획 안내문을 발송한 후 수령 확인했다.',
])
def test_split_delivery_keeps_actual_completed_receipts(recipient, section, description):
    graph = live_split_delivery_graph()
    next(n for n in graph.text_nodes.values() if n.section == section).text = recipient + description
    a = answer(graph, 'E-7')
    assert a.value is True and a.status == 'verified', a.rationale


@pytest.mark.parametrize('description', [
    'CUS-01 고객에게 안내문을 발송하고 수령 확인 완료를 요청했다.',
    'CUS-01 고객에게 안내문을 발송했으며 수령 확인 완료 예정이다.',
])
def test_completion_word_in_requested_or_future_receipt_is_not_a_completed_record(description):
    graph = live_split_delivery_graph()
    next(n for n in graph.text_nodes.values() if n.section == '고객 안내문 전달·수령').text = description
    assert answer(graph, 'E-7').value is None


@pytest.mark.parametrize('description,expected', [
    ('CUS-01 고객 | 안내문 발송 | 수령 확인 | 대기 중', None),
    ('CUS-01 고객에게 안내문 발송 후 수령 확인\n대기 중이다.', None),
    ('CUS-01 고객에게 안내문 발송 후 수령 확인\n을 내일 진행할 계획이다.', None),
    ('CUS-01 고객에게 안내문 발송 후 수령 확인\n했다.', True),
])
def test_receipt_completion_keeps_wrapped_predicates_and_table_status(description, expected):
    graph = live_split_delivery_graph()
    next(n for n in graph.text_nodes.values() if n.section == '고객 안내문 전달·수령').text = description
    a = answer(graph, 'E-7')
    assert a.value is expected and a.status == ('verified' if expected else 'insufficient')


@pytest.mark.parametrize('recipient,section', [
    ('CUS-01 고객', '고객 안내문 전달·수령'),
    ('SUP-01 공급사', '공급사 안내문 전달·수령 및 확인'),
])
@pytest.mark.parametrize('description,expected', [
    ('에게 안내문을 발송하고 수령 확인했다는 회신을 기다리고 있다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 회신해 주기를 요청했다.', None),
    ('에게 안내문을 발송하고 “수령 확인했습니다”라고 회신해 주기를 요청했다.', None),
    (' | 안내문 발송 | 수령 확인 완료 | 확인자: 담당자', True),
    (' | 안내문 전달 완료 | 확인자: 담당자', True),
    ('에게 안내문을 발송하고 수령 확인했다고 회신받았다.', True),
    (' | 안내문 발송 | 수령 확인 완료', True),
    ('에게 안내문 발송 후 수령 확인했다 | 확인자: 담당자', True),
])
def test_split_receipt_distinguishes_requested_replies_and_completed_table_cells(recipient, section, description, expected):
    graph = live_split_delivery_graph()
    node = next(n for n in graph.text_nodes.values() if n.section == section)
    node.text = recipient + description
    graph.source_texts = {name: '한울정밀공업(주) / 절차 시행 2026-04-01 / 김해 제1공장·양산 제2공장\n'
                          for name in graph.source_texts}
    a = answer(graph, 'E-7')
    assert a.value is expected and a.status == ('verified' if expected else 'insufficient'), (node.text, a.rationale)


@pytest.mark.parametrize('description,expected', [
    ('CUS-01 고객 | 안내문 발송 | 수령 확인 완료 | 대기 중', None),
    ('CUS-01 고객 | 안내문 발송 | 수령 확인 완료 | 상태: 대기', None),
    ('CUS-01 고객에게 안내문을 발송하고 수령 확인했다고 회신받았다는 답변을 기다린다.', None),
    ('CUS-01 고객에게 안내문을 발송하고 “수령 확인했습니다”라고 회신받았다.', True),
    ('CUS-01 고객에게 안내문을 발송하고 수령 확인했다면 대장을 갱신한다.', None),
    ('CUS-01 고객에게 안내문을 발송하고 수령 확인 요청을 확인했다.', None),
    ('CUS-01 고객에게 안내문 전달 요청 검토 완료.', None),
    ('CUS-01 고객에게 안내문을 발송하고 “수령 확인했습니다”\n라고 회신해 주기를 요청했다.', None),
    ('CUS-01 고객에게 안내문을 발송하고 수령 확인했다고\n회신받았다.', True),
    ('CUS-01 고객에게 안내문을 발송하고 수령 확인\n했음이라고 회신해 주기를 요청했다.', None),
])
def test_completed_reply_needs_an_actual_assertion_and_unambiguous_table_status(description, expected):
    graph = live_split_delivery_graph()
    next(n for n in graph.text_nodes.values() if n.section == '고객 안내문 전달·수령').text = description
    a = answer(graph, 'E-7')
    assert a.value is expected and a.status == ('verified' if expected else 'insufficient'), a.rationale


@pytest.mark.parametrize('recipient,section', [
    ('CUS-01 고객', '고객 안내문 전달·수령'),
    ('SUP-01 공급사', '공급사 안내문 전달·수령 및 확인'),
])
@pytest.mark.parametrize('description,expected', [
    ('에게 안내문을 발송하고 “수령 확인했습니다.”라고 회신해 주기를 요청했다.', None),
    ('에게 안내문을 발송하고 “수령 확인했습니다.”라고 회신받았다.', True),
    ('에게 안내문을 발송하고 “수령 확인했습니다?”라고 회신해 주기를 요청했다.', None),
    ('에게 안내문을 발송하고 “수령 확인했습니다”\n라고 회신해 주기를 요청했다.', None),
    ('에게 안내문을 발송하고 “수령 확인했습니다”\n라고회신해 주기를 요청했다.', None),
    ('에게 안내문을 발송하고 수령 확인했다는\n회신을 접수했다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 회신해 주기를\n요청했다.', None),
    (' | 안내문 발송 | 수령 확인 완료 | 확인자: 담당자', True),
    (' | 안내문 발송 | 수령 확인 완료 | 상태: 대기(회신 요청)', None),
    (' | 안내문 전달 완료 | 확인자: 담당자', True),
    (' | 안내문 전달 완료 | 상태: 대기 중', None),
    ('에게 안내문을 발송하고 수령 확인했다고 이메일로 회신받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 대장에 기록했다.', True),
    ('에게 안내문을 발송하고 수령 확인했다는 사실을 담당자가 대조했다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 대장에 기록해 주기를 요청했다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 대장에 기록했다는 답변을 기다린다.', None),
    ('에게 안내문을 발송하고 수령 확인했다는 사실을 담당자가 대조했다면 대장을 갱신한다.', None),
    ('에게 안내문을 발송하고 수령 확인했다는 사실을 담당자가 대조해 주기를 요청했다.', None),
    ('에게 안내문을 발송하고 “수령 확인했습니다.”\n라고회신해 주기를 요청했다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고\n대장에 기록했다.', True),
    ('에게 안내문을 발송하고 “수령 확인했습니다.”라는 사실을 담당자가 대조했다.', True),
    (' | 안내문 발송 | 수령 확인 완료 | 상태: 대기/회신 요청', None),
])
def test_receipt_quotes_ocr_status_and_completed_audit_context(recipient, section, description, expected):
    graph = live_split_delivery_graph()
    next(n for n in graph.text_nodes.values() if n.section == section).text = recipient + description
    graph.source_texts = {name: '한울정밀공업(주) / 절차 시행 2026-04-01 / 김해 제1공장·양산 제2공장\n'
                          for name in graph.source_texts}
    a = answer(graph, 'E-7')
    assert a.value is expected and a.status == ('verified' if expected else 'insufficient'), (description, a.rationale)


@pytest.mark.parametrize('recipient,section', [
    ('CUS-01 고객', '고객 안내문 전달·수령'),
    ('SUP-01 공급사', '공급사 안내문 전달·수령 및 확인'),
])
@pytest.mark.parametrize('description,expected', [
    ('에게 안내문을 발송하고 “수령 확인했습니다.”라는 회신을 요청했다.', None),
    ('에게 안내문을 발송하고 “수령 확인했습니다.”라는 회신을 기다리고 있다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 회신해 주기를 요청한 사실을 담당자가 대조했다.', None),
    ('에게 안내문을 발송하고 “수령 확인했습니다.”라고 회신해 주기를 요청한 사실을 담당자가 대조했다.', None),
    ('에게 안내문을 발송하고 수령 확인했다는 사실을\n담당자가 대조했다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 대장에\n기록했다.', True),
    ('에게 안내문을 발송하고 “수령 확인했습니다.”라고 회신해 주기를 요청했다.', None),
    ('에게 안내문을 발송하고 “수령 확인했습니다.”라는 회신을 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다는 사실을 담당자가 대조했다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 대장에 기록했다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고\n회신받았다.', True),
    (' | 안내문 발송 | 수령 확인 완료 | 상태: 대기(회신 요청)', None),
    ('에게 안내문을 발송하고 “수령 확인했습니다.”라는 사실을\n담당자가 대조했다.', True),
    ('에게 안내문을 발송하고 “수령 확인했습니다.”\n라는회신을 요청했다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 회신해 주기를 요청한 사실을\n담당자가 대조했다.', None),
    ('에게 안내문을 발송하고 수령 확인했다는 사실을 담당자가 대조했다는 답변을 기다린다.', None),
    ('에게 안내문을 발송하고 수령 확인했다는 사실을 담당자가 대조했다면 대장을 갱신한다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 담당자가 대장에 기록했다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 회신해 주기를 요청한 사실을 대장에 기록했다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 대장에\n기록해 주기를 요청했다.', None),
    ('에게 안내문을 발송하고 수령 확인했다는 사실을\n담당자가\n대조했다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 담당자가 대장에\n기록했다.', True),
])
def test_receipt_assertion_binds_completed_fact_and_preserves_ocr_complements(recipient, section, description, expected):
    graph = live_split_delivery_graph()
    next(n for n in graph.text_nodes.values() if n.section == section).text = recipient + description
    graph.source_texts = {name: '한울정밀공업(주) / 절차 시행 2026-04-01 / 김해 제1공장·양산 제2공장\n'
                          for name in graph.source_texts}
    a = answer(graph, 'E-7')
    assert a.value is expected and a.status == ('verified' if expected else 'insufficient'), (description, a.rationale)


@pytest.mark.parametrize('recipient,section', [
    ('CUS-01 고객', '고객 안내문 전달·수령'),
    ('SUP-01 공급사', '공급사 안내문 전달·수령 및 확인'),
])
@pytest.mark.parametrize('description,expected', [
    ('에게 안내문을 발송하고 “수령 확인했습니다.”라는 회신을 요청했고 요청을 접수했다는 회신을 받았다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 회신해 주기를 요청한 사실에 대해 담당자로부터 회신을 받았다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 답변해 주기를 요청했고 별도 문의에 대한 답변을 받았다.', None),
    ('에게 안내문을 발송하고 “수령 확인했습니다.”라는 회신을 요청했고 별도 문의에 대한 답변을 받았다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 담당자가\n대장에 기록했다.', True),
    ('에게 안내문을 발송하고 “수령 확인했습니다.”라고 담당자가\n대장에 기록했다.', True),
    ('에게 안내문을 발송하고 “수령 확인했습니다.”라는 회신을\n받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 회신을\n받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 담당자가 대장에 기록했다.', True),
    ('에게 안내문을 발송하고 “수령 확인했습니다.”라고 담당자가 대장에 기록했다.', True),
    ('에게 안내문을 발송하고 “수령 확인했습니다.”라는 회신을 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 회신을 받았다.', True),
    ('에게 안내문을 발송하고 “수령 확인했습니다.”라는 회신을 요청했고 요청을 접수했다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 회신해 주기를 요청한 사실을 담당자가 대조했다.', None),
    ('에게 안내문을 발송하고 “수령 확인했습니다.”라는 회신을 요청했다.', None),
    ('에게 안내문을 발송하고 수령 확인했다는 사실을\n담당자가 대조했다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 담당자로부터 회신을 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 고객에게서 회신을 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 담당자가 회신을 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 담당자가\n대장에\n기록했다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 담당자로부터 회신을\n받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 담당자가\n회신을 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 회신을\n받았다면 대장에 기록한다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 회신을\n받았다고 답변해 주기를 요청했다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 담당자가\n대장에 기록했다는 답변을 기다린다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 담당자가\n대장에 기록했다면 회신한다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 담당자가\n다른 문의에 대한 회신을 받았다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 회신을 요청했고\n별도 문의에 대한 답변을 받았다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 담당자가\nCUS-02 고객의 별도 대장에 기록했다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 회신을\n2026-05-01 별도 문의의 답변을 받았다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 회신을\n사업장: 부산\n받았다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 담당자가\n대장에 기록해 주기를 요청했다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 이메일로\n회신받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 담당자로부터 이메일로 회신을\n받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 이메일로 담당자가\n회신을 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 서면으로 답변을\n받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 이메일로 회신해 주기를 요청했고 별도 답변을 받았다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 이메일로\n회신을 요청했고 요청 접수에 대한 답변을 받았다.', None),
])
def test_receipt_reply_binds_claim_and_keeps_complete_ocr_complement(recipient, section, description, expected):
    graph = live_split_delivery_graph()
    next(n for n in graph.text_nodes.values() if n.section == section).text = recipient + description
    graph.source_texts = {name: '한울정밀공업(주) / 절차 시행 2026-04-01 / 김해 제1공장·양산 제2공장\n'
                          for name in graph.source_texts}
    a = answer(graph, 'E-7')
    assert a.value is expected and a.status == ('verified' if expected else 'insufficient'), (description, a.rationale)


RECEIPT_METADATA_CASES = [
    ('에게 안내문을 발송하고 수령 확인했다고 2026-04-30 회신을 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 4월 30일 회신을 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 14:00 회신을 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 상대에게서 2026-04-30 회신받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 생산계획담당자가 회신을 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 총무팀 담당자가 회신을 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 상대에게서 이메일로 담당자가 회신받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 담당자로부터 이메일로 회신을 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 회신받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 이메일로 담당자가\n회신을 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 회신해 주기를 요청했고 2026-04-30 별도 문의에 대한 답변을 받았다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 생산계획담당자가 회신해 주기를 요청했다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 상대에게서 이메일로 담당자가 회신을 받았다고 답변해 주기를 요청했다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 2026-04-30 회신받았다면 대장에 기록한다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 담당자가\nCUS-02 고객의 별도 문의에 대한 회신을 받았다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 담당자로부터 회신을\n받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 2026/04/30 회신을 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 2026.04.30 14:00에 회신을 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 4월 30일 오후 2시 30분에 회신을 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 (2026-04-30) 회신을 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 총무팀 생산계획담당자에게서 문자로 회신을 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 문서관리 담당자가 우편으로 회신을 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 회신을 2026-04-30 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 회신을 총무팀 담당자가 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 2026-04-30\n회신을 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 회신을\n2026-04-30 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 4월\n30일 회신을 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 총무팀\n담당자가 2026-04-30 회신을 받았다.', True),
    ('에게 안내문을 발송하고 “수령 확인했습니다.”라는 2026-04-30 회신을 받았다.', True),
    ('에게 안내문을 발송하고 “수령 확인했습니다.”라는 회신을 14:00에 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 14:00 회신을 요청했고 요청 접수 답변을 받았다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 총무팀 담당자가 회신을 기다린다는 답변을 받았다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 생산계획담당자가 회신받았다고 답변해 주기를 요청했다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 요청으로 회신을 받았다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 요청했다는 총무팀 담당자가 별도 회신을 받았다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 회신을\nCUS-02 고객이 받았다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 회신을\n사업장: 부산\n2026-04-30 받았다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 회신을\n2026-05-01 별도 문의의 답변을 받았다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 2026-04-30 회신을 받았다면 기록한다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 (회신 요청) 담당자가 답변을 받았다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 (2026-04-\n30) 회신을 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 (총무팀\n담당자가) 회신을 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 (\n2026-04-30 14:00) 회신을 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 （2026-04-30 14:00） 회신을 받았다.', True),
    ('에게 안내문을 발송하고 수령 확인했다고 (회신\n요청) 담당자가 답변을 받았다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 （요청했다는 총무팀 담당자가） 별도 회신을 받았다.', None),
]
for _stamp in ('2026-04-30', '2026.04.30', '4월 30일 14:00'):
    for _parts in ((_stamp, '상대에게서', '문자로', '생산계획담당자가'),
                   ('총무팀 담당자가', _stamp, '상대에게서', '이메일로'),
                   ('상대에게서', '이메일로', _stamp, '총무팀 담당자가'),
                   ('문자로', '상대에게서', '생산계획담당자가', _stamp)):
        _metadata = ' '.join(_parts)
        for _before, _after in ((' ', ' '), ('\n', ' '), (' ', '\n')):
            for _reply, _expected in (('회신을'+_after+'받았다.', True),
                                      ('회신해 주기를 요청했고 별도 문의의 답변을 받았다.', None),
                                      ('회신을'+_after+'받았다면 대장에 기록한다.', None)):
                RECEIPT_METADATA_CASES.append(('에게 안내문을 발송하고 수령 확인했다고 '+_metadata+_before+_reply, _expected))


RECEIPT_METADATA_CASES.extend(
    ('에게 안내문을 발송하고 수령 확인했다고 '+stamp+predicate+actor_suffix+' 회신을 받았다.', None)
    for predicate in ('기다린다는', '예정이라는', '계획이라는', '기다리는',
                      '대기한다는', '요청한다는', '회신하기로 계획한다는', '요청했다는')
    for stamp in ('', '2026-04-30 ')
    for actor_suffix in ('', '담당자가')
)
RECEIPT_METADATA_CASES.extend(
    ('에게 안내문을 발송하고 수령 확인했다고 '+phrase+' 회신을 받았다.', None)
    for words in ('회신해 주기를 요청한 담당자가', '회신해 주기를 요청한 사실을 담당자가',
                  '회신을 기다린다는 담당자가', '회신을 받을 예정이라는 담당자가')
    for phrase in (words, words.replace(' ', ''))
)


@pytest.mark.parametrize('recipient,section', [
    ('CUS-01 고객', '고객 안내문 전달·수령'),
    ('SUP-01 공급사', '공급사 안내문 전달·수령 및 확인'),
])
@pytest.mark.parametrize('description,expected', RECEIPT_METADATA_CASES)
def test_receipt_metadata_preserves_bound_claim_and_ocr(recipient, section, description, expected):
    graph = live_split_delivery_graph()
    next(n for n in graph.text_nodes.values() if n.section == section).text = recipient + description
    graph.source_texts = {name: '한울정밀공업(주) / 절차 시행 2026-04-01 / 김해 제1공장·양산 제2공장\n'
                          for name in graph.source_texts}
    a = answer(graph, 'E-7')
    assert a.value is expected and a.status == ('verified' if expected else 'insufficient'), (description, a.rationale)


RECEIPT_NOMINAL_REQUEST_CASES = [
    ('에게 안내문을 발송하고 수령 확인했다고 적어 달라고 요청받은 담당자가 회신을 받았다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 적어 달라고 요청받은 담당자로부터 회신을 받았다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 기재하도록 지시받은 담당자가 회신을 받았다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 쓰도록 부탁받은 담당자가 회신을 받았다.', None),
    ('에게 안내문을 발송하고 “수령 확인했다”고 적어 달라고 요청받은 담당자가 회신을 받았다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 적어 달라고 요청받은\n담당자가 회신을 받았다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 적어달라고요청받은담당자가 회신을 받았다.', None),
    ('에게 안내문을 발송하고 수령 확인했다고 2026-04-30 적어 달라고 요청받은 담당자가 회신을 받았다.', None),
]
RECEIPT_NOMINAL_REQUEST_CASES.extend(
    ('에게 안내문을 발송하고 수령 확인했다고 '+stamp+phrase+separator+actor+' 회신을 받았다.', None)
    for words in ('적어 달라고 요청받은', '기재하도록 지시받은',
                  '쓰도록 부탁받은', '기록하라고 요구받은')
    for stamp in ('', '2026-04-30 ')
    for actor in ('담당자가', '담당자로부터')
    for phrase, separator in ((words, '\n'), (words.replace(' ', ''), ''))
)
RECEIPT_NOMINAL_REQUEST_CASES.extend(
    ('에게 안내문을 발송하고 수령 확인했다고 '+stamp+phrase+' '+actor+' 회신을 받았다.', None)
    for request in ('요청', '요구', '지시', '부탁')
    for phrase in (request+'받은', request+'을 받은')
    for stamp in ('', '2026-04-30 ')
    for actor in ('담당자가', '담당자로부터')
)
RECEIPT_NOMINAL_REQUEST_CASES.extend(
    ('에게 안내문을 발송하고 수령 확인했다고 '+stamp+role+' 회신을 받았다.', True)
    for role in ('요청접수 담당자가', '지시관리 담당자가', '계획지원 담당자가',
                 '생산계획담당자가', '문서관리 담당자가', '총무팀 담당자가')
    for stamp in ('', '2026-04-30 ')
)
RECEIPT_NOMINAL_REQUEST_CASES.extend(
    ('에게 안내문을 발송하고 수령 확인했다고 '+stamp+qualification+' '+actor+' 회신을 받았다.', True)
    for qualification in ('자료를 받은', '교육을 받은', '고객교육을 받은')
    for actor in ('담당자가', '담당자로부터')
    for stamp in ('', '2026-04-30 ')
)


@pytest.mark.parametrize('recipient,section', [
    ('CUS-01 고객', '고객 안내문 전달·수령'),
    ('SUP-01 공급사', '공급사 안내문 전달·수령 및 확인'),
])
@pytest.mark.parametrize('description,expected', RECEIPT_NOMINAL_REQUEST_CASES)
def test_receipt_metadata_does_not_consume_requested_actor_clause(recipient, section, description, expected):
    graph = live_split_delivery_graph()
    next(n for n in graph.text_nodes.values() if n.section == section).text = recipient + description
    graph.source_texts = {name: '한울정밀공업(주) / 절차 시행 2026-04-01 / 김해 제1공장·양산 제2공장\n'
                          for name in graph.source_texts}
    a = answer(graph, 'E-7')
    assert a.value is expected and a.status == ('verified' if expected else 'insufficient'), (description, a.rationale)


def answer(graph, code, **kw):
    sheet = build_response_sheet('rba42', evidence_graph=graph, **kw)
    return next(a for a in sheet.answers if a.qid == f'RBA-{code}')


@pytest.mark.parametrize('code', NORMAL)
def test_complete_independent_requirements_include_every_page(code):
    a = answer(graph_for(NORMAL[code]), code)
    assert a.value is True and a.status == 'verified'
    assert {e.page for e in a.evidence_links} == set(range(len(NORMAL[code])))
    assert '2024' in a.boundary_label and '제3공장' in a.boundary_label
    assert '2099' not in a.boundary_label and '전사' not in a.boundary_label


@pytest.mark.parametrize('code,page', [(c, p) for c, pages in NORMAL.items() for p in range(len(pages))])
def test_remove_a_required_page_holds_answer(code, page):
    g = graph_for(NORMAL[code])
    for nid in list(g.text_nodes):
        if g.text_nodes[nid].page == page:
            del g.text_nodes[nid]
    a = answer(g, code)
    assert a.value is None and a.status == 'insufficient', (code, page, a.rationale)
    assert '미확인 요건' in a.rationale


@pytest.mark.parametrize('code', NORMAL)
def test_filename_company_and_wording_are_not_an_answer_key(code):
    pages = [(s.replace('전달', '배포'), t.replace('전달', '배포').replace('방호장치', '인터록'))
             for s, t in NORMAL[code]]
    a = answer(graph_for(pages, name='renamed-anything.pdf', header='2021년 6월 기록 / 울산 제9공장'), code)
    assert a.value is True
    assert '2021' in a.boundary_label and '제9공장' in a.boundary_label
    assert '2024' not in a.boundary_label


@pytest.mark.parametrize('code,text', [
    ('C-8', '연간 전사 온실가스 자료가 없다. Scope 1/2/3 추적·공개와 감축목표는 4월 고지서로 대체할 수 없다.'),
    ('E-3', '등록표는 모든 문서 목록이나 모든 법규 준수 판정표가 아니다.'),
    ('A-3', '근로시간 기록의 인원은 정규직·기간제·파견으로 구분한다. 다른 주와 다른 사업장의 준수는 확정하지 않는다.'),
    ('E-4', '회사는 자동차 부품 제조업이다. 고객이 실사 자료를 요청하면 경영지원 담당자가 취합한다.'),
    ('C-7', '상수도 고지서를 보관하고 공급자의 사용량 안내문을 수령했다.'),
    ('E-6', '안내 수령 73명은 안전교육 출석이나 이수 기록이 아니다.'),
])
def test_limitations_and_adjacent_topics_never_prove_the_question(code, text):
    a = answer(graph_for([('관련 기록', text)], tag=code), code)
    assert a.value is None and a.status == 'insufficient'
    assert a.reference_links


@pytest.mark.parametrize('code', ['E-7', 'E-11'])
def test_a_limit_on_another_conclusion_does_not_discard_valid_evidence(code):
    pages = list(NORMAL[code])
    section, text = pages[-1]
    pages[-1] = (section, text + ' 이 기록은 교육 출석이 아니다. 모든 법규 준수 판정표는 아니다.')
    assert answer(graph_for(pages), code).value is True


def test_prohibition_is_positive_protection_evidence():
    text = '근로자와 이해관계자 양방향 간담회를 실시한다. 고충을 접수하고 구제 접근을 보장한다. 보복을 금지한다.'
    a = answer(graph_for([('참여·구제', text)]), 'E-8')
    assert a.value is True


def test_prohibition_no_retaliation_is_not_absence_of_protection():
    text = '근로자와 대표가 양방향 간담회에 참여한다. 고충 접수·구제 접근을 보장한다. 보복 없는 의견제기를 보장한다.'
    assert answer(graph_for([('참여·구제', text)]), 'E-8').value is True


def test_machine_answer_does_not_inherit_unrelated_umbrella_failure():
    audit = [{'kesg_code': 'S-4-1', 'passed': False,
              'findings': [{'clause_id': 'ppe', 'status': 'missing', 'description': '보호구 지급 미확인'}]}]
    a = answer(graph_for(NORMAL['B-6']), 'B-6', policy_audit=audit)
    assert a.value is True and a.status == 'verified'
    assert audit[0]['passed'] is False


def test_exact_question_failure_is_still_applied():
    audit = [{'kesg_code': 'B-6', 'passed': False,
              'findings': [{'status': 'missing', 'description': '방호 기능 실패'}]}]
    a = answer(graph_for(NORMAL['B-6']), 'B-6', policy_audit=audit)
    assert a.value is True and a.status == 'flagged'
    assert any('방호 기능 실패' in f for f in a.flags)


def test_unknown_source_scope_does_not_use_runtime_year_or_company():
    a = answer(graph_for(NORMAL['E-7'], header=''), 'E-7')
    assert a.value is True and a.boundary_label == ''
    assert a.boundary == {} and a.completeness == 'unknown'


def test_company_response_cannot_be_independent_evidence():
    a = answer(graph_for(NORMAL['E-7'], origin='survey'), 'E-7')
    assert a.value is None and not a.evidence_links


def test_explicit_no_is_preserved_when_independent_documents_exist():
    extraction = SimpleNamespace(mapped={'E-7': {'value': False, 'survey_answer': {'yn': '아니오'}}}, missing=[])
    a = answer(graph_for(NORMAL['E-7']), 'E-7', extraction=extraction)
    assert a.value is False and a.status == 'flagged' and a.self_reports


def test_split_requirements_from_different_scopes_do_not_make_a_whole_yes():
    g = graph_for(NORMAL['E-2'][:1], name='assignment.pdf')
    other = graph_for(NORMAL['E-2'][1:], name='review.pdf', header='2025년 7월 기록 / 부산 제7공장')
    for nid, node in other.text_nodes.items():
        g.add_text_node(replace(node, id='other_' + nid))
    g.source_texts.update(other.source_texts)
    a = answer(g, 'E-2')
    assert a.value is None and '동일 기간·사업장' in a.rationale
    assert '2024' in a.boundary_label and '2025' in a.boundary_label


def test_graph_source_context_is_serialized_and_old_graphs_are_safe():
    g = graph_for(NORMAL['E-7'])
    assert g.to_dict()['source_texts']['운영기록.pdf'].startswith(HEADER)
    del g.source_texts
    a = answer(g, 'E-7')
    assert a.value is True and '2099' not in a.boundary_label


def test_document_register_is_not_training_attendance():
    a = answer(graph_for([('문서 등록표', '직원 교육 실시 기록 원본과 안전 규정은 담당자가 승인하고 보관한다.')], tag='E-6'), 'E-6')
    assert a.value is None


def test_training_disclaimer_and_delivery_in_same_sentence_have_different_roles():
    pages = list(NORMAL['E-7'])
    pages[-1] = ('수신자별 전달 확인', '전자 열람 기록은 수신자별 안내 수령·이해 확인 기록이며 교육 출석 기록은 아니다.')
    a = answer(graph_for(pages), 'E-7')
    assert a.value is True and {e.page for e in a.evidence_links} == {0, 1, 2}


def test_a_complete_working_hours_policy_can_be_confirmed():
    text = ('법정 근로시간 한도를 준수하며 주 60시간 근로시간 상한 초과를 금지한다. '
            '연장근로는 자발적 동의로 진행한다. 매주 1일 휴무를 보장한다.')
    assert answer(graph_for([('근로시간 절차', text)]), 'A-3').value is True


def test_a_complete_water_management_process_can_be_confirmed():
    text = ('수원과 용수 사용·폐수 배출을 측정하고 모니터링한다. '
            '절수 기회를 발굴하고 오염경로를 통제한다. 폐수 처리 설비를 점검한다.')
    assert answer(graph_for([('용수 관리', text)]), 'C-7').value is True


def test_a_complete_ghg_process_can_be_confirmed():
    text = ('온실가스 감축목표를 수립한다. Scope 1, Scope 2, Scope 3 배출을 산정·추적한다. '
            '온실가스 산정 결과를 보고서에 공개한다.')
    assert answer(graph_for([('온실가스 추적', text)]), 'C-8').value is True


def test_register_of_legal_and_customer_requirements_is_not_discarded():
    text = ('법규 목록을 식별하고 등록한다. 고객 요구사항도 식별해 등록한다. '
            '법규·요구사항의 개정 모니터링과 이해 확인을 시행한다.')
    assert answer(graph_for([('준수 요구사항', text)]), 'E-3').value is True


def test_complete_risk_assessment_can_be_confirmed():
    text = ('법규·환경·노동·윤리 리스크를 식별·평가한다. 중대 인권·환경 영향의 중요도를 평가한다. '
            '위험 통제와 완화 조치를 실행한다.')
    assert answer(graph_for([('위험 관리', text)]), 'E-4').value is True


def test_legacy_graph_can_still_prove_an_explicit_training_program():
    text = '관리자와 근로자를 대상으로 안전 규정·절차 이행 교육 프로그램을 실시하였다.'
    g = graph_for([('교육 실시', text)])
    del g.source_texts
    a = answer(g, 'E-6')
    assert a.value is True


def test_relevant_umbrella_clause_failure_is_still_applied():
    audit = [{'kesg_code': 'S-4-1', 'passed': False,
              'findings': [{'status': 'missing', 'description': '기계 방호장치 유지보수 미확인'},
                           {'status': 'missing', 'description': '안전보건위원회 미확인'}]}]
    a = answer(graph_for(NORMAL['B-6']), 'B-6', policy_audit=audit)
    assert a.status == 'flagged'
    assert any('기계 방호장치' in f for f in a.flags)
    assert all('안전보건위원회' not in f for f in a.flags)
    assert len(audit[0]['findings']) == 2


@pytest.mark.parametrize('code,pages', [
    ('C-8', [('온실가스 관리 현황', '온실가스 감축목표를 수립하지 않는다. '
             'Scope 1, Scope 2, Scope 3 배출을 산정·추적하지 않는다. '
             '온실가스 산정 결과를 보고서에 공개하지 않는다.')]),
    ('E-2', [NORMAL['E-2'][0], ('경영진 검토 실시 기록',
             '08-30 회의를 실시하지 않았다. 대표이사가 검토 완료를 승인하지 않았다.')]),
    ('E-6', [('교육 실시 기록', '관리자와 근로자를 대상으로 안전 규정·절차 이행 '
             '교육 프로그램을 실시하지 않았다.')]),
])
def test_negated_actions_do_not_prove_requirements(code, pages):
    a = answer(graph_for(pages, tag=code), code)
    assert a.value is None and a.status == 'insufficient'
    assert a.reference_links


@pytest.mark.parametrize('negative', ['실시하지 않는다', '실시하지 않았다',
                                     '실시하지 않습니다', '실시하지 못했다', '실시되지 않았다'])
def test_negative_action_inflections_keep_review_pending(negative):
    pages = [NORMAL['E-2'][0], ('경영진 검토 실시 기록', f'08-30 회의를 {negative}.')]
    assert answer(graph_for(pages), 'E-2').value is None


def test_no_retaliation_action_remains_protective_evidence():
    text = ('근로자와 이해관계자 양방향 간담회를 실시한다. 고충 접수·구제 접근을 보장한다. '
            '의견제기 근로자에게 보복을 하지 않는다.')
    assert answer(graph_for([('참여·구제', text)]), 'E-8').value is True


@pytest.mark.parametrize('period,site', [('2025년 7월', '대전 제3공장'),
                                      ('2024년 8월', '부산 제7공장'),
                                      ('2025년 7월', '부산 제7공장')])
@pytest.mark.parametrize('one_clause', [False, True])
def test_different_applicability_scopes_in_one_document_stay_pending(period, site, one_clause):
    pages = [('경영 책임', '2024년 8월 대전 제3공장: ' + NORMAL['E-2'][0][1]),
             ('경영진 검토 실시 기록', f'{period} {site}: ' + NORMAL['E-2'][1][1])]
    if one_clause:
        pages = [('경영 책임 및 검토 기록', '\n'.join(text for _, text in pages))]
    a = answer(graph_for(pages, header='경영책임 및 검토 기록 모음'), 'E-2')
    assert a.value is None and '동일 기간·사업장' in a.rationale
    assert all(e.node_id in graph_for(pages).text_nodes for e in a.evidence_links)


def test_equal_declared_scope_keeps_all_pages_connected():
    pages = [(s, '2024년 8월 대전 제3공장: ' + t) for s, t in NORMAL['E-2']]
    a = answer(graph_for(pages, header='2024년 8월 기록 / 대전 제3공장'), 'E-2')
    assert a.value is True and a.status == 'verified'
    assert {e.page for e in a.evidence_links} == {0, 1}


def test_equal_scope_can_combine_partial_requirements_from_separate_documents():
    g = graph_for(NORMAL['E-2'][:1], name='assignment.pdf')
    other = graph_for(NORMAL['E-2'][1:], name='review.pdf')
    for node in other.text_nodes.values():
        g.add_text_node(replace(node, id='other_' + node.id))
    g.source_texts.update(other.source_texts)
    a = answer(g, 'E-2')
    assert a.value is True and len({e.file_name for e in a.evidence_links}) == 2


@pytest.mark.parametrize('when', ['2024년 8월 30일', '8월 30일', '2024/08/30',
                                '2024.08.30', '2024-08-30', '08/30', '8.30', '16시 30분', '16:30'])
def test_review_record_accepts_equivalent_date_and_time_formats(when):
    pages = [NORMAL['E-2'][0], ('경영진 검토 실시 기록',
             f'{when} 회의를 실시하고 대표이사와 업무 담당자가 결정 사항을 확인했다.')]
    a = answer(graph_for(pages), 'E-2')
    assert a.value is True and {e.page for e in a.evidence_links} == {0, 1}


@pytest.mark.parametrize('when', ['2024년', '관리번호 MGT-0130', '99-99'])
def test_year_document_number_or_invalid_date_is_not_an_execution_timestamp(when):
    pages = [NORMAL['E-2'][0], ('경영진 검토 실시 기록', f'{when} 회의를 실시하고 완료를 확인했다.')]
    assert answer(graph_for(pages), 'E-2').value is None


@pytest.mark.parametrize('header,site,scope', [
    ('2024년 8월 기록 / 부산사업장', '부산사업장', 'site'),
    ('2024년 8월 기록 / 부산공장', '부산공장', 'site'),
    ('2024년 8월 기록 / 전사 모든 사업장', '전사', 'entity'),
    ('전사 모든 사업장', '전사', 'entity'),
    ('사업장: 부산사업장\n대상 기간: 2024년 8월', '부산사업장', 'site'),
])
def test_named_site_and_explicit_entity_scope_survive_in_shared_answer(header, site, scope):
    a = answer(graph_for(NORMAL['E-7'], header=header), 'E-7')
    assert a.value is True and a.boundary['site'] == site
    assert a.boundary['site_scope'] == scope and site in a.boundary_label


def test_labor_and_movement_words_are_not_sites():
    a = answer(graph_for(NORMAL['E-7'], header='2024년 8월 기록\n노동·인권 및 자료 이동 관리'), 'E-7')
    assert a.value is True and a.boundary['site'] == ''
    assert a.boundary['site_scope'] == 'unknown'


@pytest.mark.parametrize('tracked', [False, True])
def test_scope_three_topic_in_heading_cannot_replace_negated_tracking(tracked):
    text = ('온실가스 감축목표를 수립한다. Scope 1, Scope 2 배출을 산정·추적한다. '
            + ('Scope 3 배출을 추적한다. ' if tracked else 'Scope 3 배출은 추적하지 않는다. ')
            + '온실가스 산정 결과를 보고서에 공개한다.')
    a = answer(graph_for([('온실가스 관리 및 Scope 3 배출 추적', text)]), 'C-8')
    assert a.value is (True if tracked else None), a.to_dict()


@pytest.mark.parametrize('same_scope', [False, True])
def test_labeled_scope_fields_in_one_clause_are_separate_applicability_scopes(same_scope):
    period, site = ('2024년 8월', '대전 제3공장') if same_scope else ('2025년 7월', '부산 제7공장')
    text = ('적용 기간: 2024년 8월\n사업장: 대전 제3공장\n' + NORMAL['E-2'][0][1]
            + f'\n적용 기간: {period}\n사업장: {site}\n' + NORMAL['E-2'][1][1])
    a = answer(graph_for([('경영 책임 및 검토 기록', text)], header='기록 모음'), 'E-2')
    assert a.value is (True if same_scope else None), a.to_dict()


@pytest.mark.parametrize('same_scope', [False, True])
def test_site_before_period_still_declares_an_applicability_scope(same_scope):
    period, site = ('2024년 8월', '대전 제3공장') if same_scope else ('2025년 7월', '부산 제7공장')
    pages = [('경영 책임', '대전 제3공장 / 2024년 8월: ' + NORMAL['E-2'][0][1]),
             ('경영진 검토 실시 기록', f'{site} / {period}: ' + NORMAL['E-2'][1][1])]
    a = answer(graph_for(pages, header='기록 모음'), 'E-2')
    assert a.value is (True if same_scope else None), a.to_dict()


@pytest.mark.parametrize('separator', ['-', '/'])
def test_date_in_source_header_is_preserved_independently_of_separator(separator):
    date = separator.join(['2024', '08', '30'])
    a = answer(graph_for(NORMAL['E-7'], header=date + ' 기준 / 부산사업장'), 'E-7')
    assert a.value is True and a.boundary['site'] == '부산사업장'
    assert date in a.boundary['period_text'], a.to_dict()


@pytest.mark.parametrize('scope_three', ['', 'Scope 3 배출은 추적하지 않는다. ',
                                       'Scope 3 배출은 산정하지 않았다. '])
def test_heading_cannot_fill_absent_or_partially_negated_scope_three(scope_three):
    text = ('온실가스 감축목표를 수립한다. Scope 1, Scope 2 배출을 산정·추적한다. '
            + scope_three + '온실가스 산정 결과를 보고서에 공개한다.')
    a = answer(graph_for([('온실가스 관리 및 Scope 3 배출 추적', text)], tag='C-8'), 'C-8')
    assert a.value is None and 'Scope 3 추적' in a.evidence_needed
    assert all('Scope 3' not in e.quote for e in a.evidence_links)


@pytest.mark.parametrize('site_first', [False, True])
@pytest.mark.parametrize('period,site', [('2025년 7월', '대전 제3공장'),
                                      ('2024년 8월', '부산 제7공장'),
                                      ('2025년 7월', '부산 제7공장'),
                                      ('2024년 8월', '대전 제3공장')])
def test_scope_field_order_does_not_change_requirement_coverage(site_first, period, site):
    def fields(period, site):
        rows = [f'적용 기간: {period}', f'사업장: {site}']
        return '\n'.join(reversed(rows) if site_first else rows)
    text = (fields('2024년 8월', '대전 제3공장') + '\n' + NORMAL['E-2'][0][1]
            + '\n' + fields(period, site) + '\n' + NORMAL['E-2'][1][1])
    g = graph_for([('경영 책임 및 검토 기록', text)], header='기록 모음')
    a = answer(g, 'E-2')
    same = period == '2024년 8월' and site == '대전 제3공장'
    assert a.value is (True if same else None)
    assert a.status == ('verified' if same else 'insufficient')
    assert all(e.node_id in g.text_nodes and e.page == 0 for e in a.evidence_links)
    if not same:
        assert '동일 기간·사업장' in a.rationale and not a.boundary


@pytest.mark.parametrize('one_clause', [False, True])
@pytest.mark.parametrize('same_scope', [False, True])
def test_site_first_scope_prefix_preserves_real_links_across_inline_blocks(one_clause, same_scope):
    prefix = '대전 제3공장 / 2024년 8월' if same_scope else '부산 제7공장 / 2025년 7월'
    pages = [(NORMAL['E-2'][0][0], '대전 제3공장 / 2024년 8월: ' + NORMAL['E-2'][0][1]),
             (NORMAL['E-2'][1][0], prefix + ': ' + NORMAL['E-2'][1][1])]
    if one_clause:
        pages = [('경영 책임 및 검토 기록', ' '.join(t for _, t in pages))]
    g = graph_for(pages, header='기록 모음')
    a = answer(g, 'E-2')
    assert a.value is (True if same_scope else None)
    assert all(e.node_id in g.text_nodes for e in a.evidence_links)
    assert {e.page for e in a.evidence_links} == set(range(len(pages)))


def test_execution_date_and_site_mentions_do_not_replace_document_scope():
    pages = [NORMAL['E-2'][0], ('경영진 검토 실시 기록',
             '2024년 8월 30일 대전 제3공장에서 회의를 실시하고 완료를 확인했다.')]
    a = answer(graph_for(pages), 'E-2')
    assert a.value is True
    assert '아산 제4공장' in a.boundary['site']
    assert '2024년 8월 기록' in a.boundary['period_text']


@pytest.mark.parametrize('header', ['2024/08/30 기준 / 부산사업장',
                                    '부산사업장 / 2024/08/30 기준',
                                    '2024/08/01~2024/08/30 기준 | 부산사업장'])
def test_date_slashes_survive_field_separators_and_ranges(header):
    a = answer(graph_for(NORMAL['E-7'], header=header), 'E-7')
    assert a.value is True and a.boundary['site'] == '부산사업장'
    assert '2024/08/30' in a.boundary['period_text']
    if '~' in header:
        assert '2024/08/01~2024/08/30' in a.boundary['period_text']


@pytest.mark.parametrize('completed', [False, True])
def test_table_target_summary_requires_actual_delivery_records(completed):
    pages = [NORMAL['E-7'][0], NORMAL['E-7'][2],
             ('전달 기록', '공급사 6곳·고객 1곳이 전달 대상이다.')]
    if completed:
        pages.append(('전달 기록', '공급사와 고객의 안내 수령을 확인했다. 문의에 회신했다.'))
    a = answer(graph_for(pages), 'E-7')
    assert a.value is (True if completed else None)
    if not completed:
        assert '공급사·고객 전달 운영' in a.evidence_needed


def test_unrelated_understanding_check_cannot_prove_supplier_customer_delivery():
    pages = [NORMAL['E-7'][0], NORMAL['E-7'][2],
             ('전달 절차', '4월 수령 확인에서는 한국어 본문의 이해 가능 여부를 확인했다.')]
    a = answer(graph_for(pages), 'E-7')
    assert a.value is None and '공급사·고객 전달 운영' in a.evidence_needed


@pytest.mark.parametrize('same_scope', [False, True])
def test_inline_labeled_scope_rows_do_not_merge_different_scopes(same_scope):
    period, site = ('2024년 8월', '대전 제3공장') if same_scope else ('2025년 7월', '부산 제7공장')
    text = ('적용 기간: 2024년 8월 | 사업장: 대전 제3공장 | '
            '대표이사의 경영 책임과 권한을 지정하고 대표이사는 월 1회 경영진 검토 회의를 주재한다\n'
            + f'적용 기간: {period} | 사업장: {site} | '
            '08-30 회의를 실시하고 대표이사와 업무 담당자가 결정 사항을 확인했다')
    a = answer(graph_for([('경영 책임 및 검토 기록', text)], header='기록 모음'), 'E-2')
    assert a.value is (True if same_scope else None), a.to_dict()


@pytest.mark.parametrize('tracked', [False, True])
def test_same_section_topic_only_node_cannot_replace_negated_tracking(tracked):
    text = ('온실가스 감축목표를 수립한다. Scope 1, Scope 2 배출을 산정·추적한다. '
            + ('Scope 3 배출을 추적한다. ' if tracked else 'Scope 3 배출은 추적하지 않는다. ')
            + '온실가스 산정 결과를 보고서에 공개한다.')
    pages = [('온실가스 배출 현황', text),
             ('온실가스 배출 현황', 'Scope 3 적용 범위는 가치사슬 전체다.')]
    a = answer(graph_for(pages), 'C-8')
    assert a.value is (True if tracked else None), a.to_dict()


@pytest.mark.parametrize('spaces', [False, True])
def test_date_slashes_with_ocr_spacing_preserve_source_period(spaces):
    date = '2024 / 08 / 30' if spaces else '2024/08/30'
    a = answer(graph_for(NORMAL['E-7'], header=date + ' 기준 / 부산사업장'), 'E-7')
    assert a.value is True and a.boundary['site'] == '부산사업장'
    assert date in a.boundary['period_text'], a.to_dict()


@pytest.mark.parametrize('separator', ['\n', ' ', ' | '])
@pytest.mark.parametrize('site_first', [False, True])
@pytest.mark.parametrize('period,site', [('2025년 7월', '대전 제3공장'),
                                      ('2024년 8월', '부산 제7공장'),
                                      ('2025년 7월', '부산 제7공장'),
                                      ('2024년 8월', '대전 제3공장')])
def test_scope_metadata_with_body_preserves_each_range(separator, site_first, period, site):
    def row(period, site, text):
        fields = [f'적용 기간: {period}', f'사업장: {site}']
        if site_first:
            fields.reverse()
        return ' | '.join(fields + [text])
    policy = '대표이사의 경영 책임과 권한을 지정하고 대표이사는 월 1회 경영진 검토 회의를 주재한다'
    record = '08-30 회의를 실시하고 대표이사와 업무 담당자가 결정 사항을 확인했다'
    text = row('2024년 8월', '대전 제3공장', policy) + separator + row(period, site, record)
    g = graph_for([('경영 책임 및 검토 기록', text)], header='기록 모음')
    a = answer(g, 'E-2')
    same = period == '2024년 8월' and site == '대전 제3공장'
    assert a.value is (True if same else None)
    assert all(e.node_id in g.text_nodes and e.page == 0 for e in a.evidence_links)
    if not same:
        assert not a.boundary and '동일 기간·사업장' in a.rationale
        assert '대전 제3공장' in a.boundary_label and site in a.boundary_label


@pytest.mark.parametrize('one_clause', [False, True])
@pytest.mark.parametrize('scope_three', ['', 'Scope 3 배출은 추적하지 않는다. ',
                                       'Scope 3 배출을 추적한다. '])
def test_topic_and_action_require_same_positive_scope_tracking_unit(one_clause, scope_three):
    text = ('온실가스 감축목표를 수립한다. Scope 1, Scope 2 배출을 산정·추적한다. '
            + scope_three + '온실가스 산정 결과를 보고서에 공개한다.')
    pages = [('온실가스 배출 현황', text), ('온실가스 배출 현황', 'Scope 3 적용 범위는 가치사슬 전체다.')]
    if one_clause:
        pages = [('온실가스 배출 현황', '\n'.join(t for _, t in pages))]
    a = answer(graph_for(pages), 'C-8')
    tracked = scope_three == 'Scope 3 배출을 추적한다. '
    assert a.value is (True if tracked else None)
    if not tracked:
        assert 'Scope 3 추적' in a.evidence_needed


@pytest.mark.parametrize('text', ['Scope 1, Scope 2, Scope 3 배출을 산정·추적한다.',
                                '직접배출을 산정한다. 간접배출을 집계한다. 가치사슬 배출을 측정한다.',
                                'Scope 1 | 산정 완료\nScope 2 | 집계 완료\nScope 3 | 추적 완료'])
def test_each_scope_positive_action_in_its_own_statement_remains_supported(text):
    text = '온실가스 감축목표를 수립한다. ' + text + '\n온실가스 결과를 공개한다.'
    assert answer(graph_for([('온실가스 배출 현황', text)]), 'C-8').value is True


@pytest.mark.parametrize('date', ['2024 /08/30', '2024/ 08 /30', '2024 / 08 / 30',
                                '2024 . 08 . 30', '2024 - 08 - 30',
                                '2024 / 08 / 01~2024 / 08 / 30'])
@pytest.mark.parametrize('site_first', [False, True])
def test_numeric_date_spans_preserve_ocr_spacing_and_field_order(date, site_first):
    header = f'부산사업장 / {date} 기준' if site_first else f'{date} 기준/부산사업장'
    a = answer(graph_for(NORMAL['E-7'], header=header), 'E-7')
    assert a.value is True and a.boundary['site'] == '부산사업장'
    assert date in a.boundary['period_text'] and date in a.boundary_label


def test_equivalent_numeric_scope_dates_can_link_independent_partial_documents():
    g = graph_for(NORMAL['E-2'][:1], name='assignment.pdf', header='2024 / 8 / 30 기준 / 부산사업장')
    other = graph_for(NORMAL['E-2'][1:], name='review.pdf', header='2024-08-30 기준 / 부산사업장')
    for node in other.text_nodes.values():
        g.add_text_node(replace(node, id='other_' + node.id))
    g.source_texts.update(other.source_texts)
    a = answer(g, 'E-2')
    assert a.value is True and {e.file_name for e in a.evidence_links} == {'assignment.pdf', 'review.pdf'}


@pytest.mark.parametrize('supplied,customer', [(False, False), (True, False), (False, True), (True, True)])
def test_delivery_table_requires_actual_receipt_for_each_recipient_group(supplied, customer):
    pages = [NORMAL['E-7'][0], NORMAL['E-7'][2],
             ('전달 기록', '공급사 6곳·고객 1곳이 전달 대상이다.'),
             ('문의 회신', '근로자와 공급사·고객 문의를 접수하고 담당자가 회신했다.')]
    if supplied:
        pages.append(('전달 기록', 'SUP-01 | 08-30 11:00 안내 발송 / 13:00 수령 확인.'))
    if customer:
        pages.append(('전달 기록', 'CUS-01 | 08-30 11:00 안내 발송 / 14:00 수령 확인.'))
    a = answer(graph_for(pages), 'E-7')
    assert a.value is (True if supplied and customer else None)
    if not (supplied and customer):
        assert '공급사·고객 전달 운영' in a.evidence_needed


@pytest.mark.parametrize('same_scope', [False, True])
def test_delivery_table_context_never_joins_customer_record_of_another_scope(same_scope):
    period = '2024년 8월' if same_scope else '2025년 7월'
    pages = [NORMAL['E-7'][0], NORMAL['E-7'][2],
             ('전달 기록', '공급사 6곳·고객 1곳이 전달 대상이다.'),
             ('전달 기록', 'SUP-01 | 08-30 11:00 발송 / 13:00 수령 확인.'),
             ('전달 기록', f'적용 기간: {period} | 사업장: 대전 제3공장 | CUS-01 | 08-30 11:00 발송 / 14:00 수령 확인.'),
             ('문의 회신', '문의를 접수하고 담당자가 회신했다.')]
    a = answer(graph_for(pages, header='2024년 8월 기록 / 대전 제3공장'), 'E-7')
    assert a.value is (True if same_scope else None)


@pytest.mark.parametrize('field_separator', [':', '：', '|'])
@pytest.mark.parametrize('site_first', [False, True])
def test_scope_field_values_do_not_borrow_sites_and_dates_from_record_body(field_separator, site_first):
    fields = [f'적용 기간{field_separator} 2024년 8월', f'사업장{field_separator} 대전 제3공장']
    if site_first:
        fields.reverse()
    pages = [(s, ' | '.join(fields) + ' | ' + t) for s, t in NORMAL['E-2']]
    pages[1] = (pages[1][0], pages[1][1] + ' 부산 제7공장과 두 공장 담당자에게 회의 내용을 전달했다.')
    a = answer(graph_for(pages, header='기록 모음'), 'E-2')
    assert a.value is True
    assert a.boundary['site'] == '대전 제3공장'
    assert a.boundary['period_text'] == '2024년 8월'
    assert a.boundary['completeness'] == a.completeness == 'partial'


@pytest.mark.parametrize('punctuation', ['.', ';'])
@pytest.mark.parametrize('same_scope', [False, True])
def test_metadata_punctuation_is_not_a_record_body(punctuation, same_scope):
    period, site = ('2024년 8월', '대전 제3공장') if same_scope else ('2025년 7월', '부산 제7공장')
    text = ('적용 기간: 2024년 8월' + punctuation + '\n사업장: 대전 제3공장' + punctuation
            + '\n' + NORMAL['E-2'][0][1] + f'\n적용 기간: {period}' + punctuation
            + f'\n사업장: {site}' + punctuation + '\n' + NORMAL['E-2'][1][1])
    a = answer(graph_for([('경영 책임 및 검토 기록', text)], header='기록 모음'), 'E-2')
    assert a.value is (True if same_scope else None)


def carbon_record_text(separator='. ', tracked=False, date='2024.08.30'):
    parts = ['온실가스 감축목표를 수립한다',
             'Scope 1, Scope 2 배출을 산정·추적한다',
             f'Scope 3 배출을 {date}에 산정했다' if tracked else 'Scope 3 적용 범위는 가치사슬 전체다',
             '온실가스 산정 결과를 보고서에 공개한다']
    return separator.join(parts)


@pytest.mark.parametrize('separator', ['. ', '.', ';', '。'])
@pytest.mark.parametrize('tracked', [False, True])
def test_punctuation_without_whitespace_still_separates_scope_topic_and_action(separator, tracked):
    a = answer(graph_for([('온실가스 배출 현황', carbon_record_text(separator, tracked))]), 'C-8')
    assert a.value is (True if tracked else None), a.to_dict()


@pytest.mark.parametrize('date', ['2024/08/30', '2024 / 08 / 30', '2024.08.30', '2024 . 08 . 30'])
def test_numeric_date_inside_positive_scope_record_is_not_sentence_boundary(date):
    a = answer(graph_for([('온실가스 배출 현황', carbon_record_text(tracked=True, date=date))]), 'C-8')
    assert a.value is True, a.to_dict()


@pytest.mark.parametrize('separator', [' | ', ' / ', ': ', ' '])
def test_explicit_site_field_does_not_include_body_recipient_site(separator):
    pages = [(NORMAL['E-2'][0][0], '적용 기간: 2024년 8월 | 사업장: 대전 제3공장 | ' + NORMAL['E-2'][0][1]),
             (NORMAL['E-2'][1][0], '적용 기간: 2024년 8월 | 사업장: 대전 제3공장' + separator
              + '08-30 회의를 실시하고 부산 제7공장 담당자에게 결정 사항을 전달했다.')]
    a = answer(graph_for(pages, header='기록 모음'), 'E-2')
    assert a.value is True and a.boundary['site'] == '대전 제3공장', a.to_dict()


@pytest.mark.parametrize('separator', ['. ', '.', ';', '。', '!', '?'])
def test_compact_negative_statement_preserves_other_positive_scope_records(separator):
    text = separator.join(['온실가스 감축목표를 수립한다',
                           'Scope 1, Scope 2 배출을 산정·추적한다',
                           'Scope 3 배출은 추적하지 않는다',
                           '온실가스 결과를 보고서에 공개한다'])
    a = answer(graph_for([('온실가스 배출 현황', text)]), 'C-8')
    assert a.value is None and a.evidence_needed == ['Scope 3 추적']
    assert all('추적하지 않는다' not in link.quote for link in a.evidence_links)


@pytest.mark.parametrize('date', ['2024.08.30', '2024 . 08 . 30', '08.30', '08 . 30'])
@pytest.mark.parametrize('completed', [False, True])
def test_execution_date_dots_preserve_record_polarity(date, completed):
    text = f'{date} 경영진 검토 회의를 ' + ('실시했다.' if completed else '실시하지 않았다.')
    a = answer(graph_for([NORMAL['E-2'][0], ('경영진 검토 실시 기록', text)]), 'E-2')
    assert a.value is (True if completed else None)
    assert '2024년 8월 기록' in a.boundary_label


@pytest.mark.parametrize('separator', [' | ', ' / ', ': ', ' '])
@pytest.mark.parametrize('site_first', [False, True])
@pytest.mark.parametrize('same_scope', [False, True])
def test_scope_field_axes_ignore_body_event_date_and_recipient_sites(separator, site_first, same_scope):
    def fields(period, site):
        values = [f'적용 기간: {period}', f'사업장: {site}']
        return ' | '.join(reversed(values) if site_first else values)
    period, site = ('2024년 8월', '대전 제3공장 / 아산 제4공장')
    other_period = period if same_scope else '2025년 7월'
    pages = [(NORMAL['E-2'][0][0], fields(period, site) + separator + NORMAL['E-2'][0][1]),
             (NORMAL['E-2'][1][0], fields(other_period, site) + separator
              + '2024 . 08 . 30 경영진 검토 회의를 실시하고 부산 제7공장 담당자에게 결정 사항을 전달했다.')]
    a = answer(graph_for(pages, header='기록 모음'), 'E-2')
    assert a.value is (True if same_scope else None), a.to_dict()
    assert '부산' not in a.boundary_label and '2024 . 08 . 30' not in a.boundary_label
    assert '대전 제3공장' in a.boundary_label and '아산 제4공장' in a.boundary_label
    if same_scope:
        assert a.boundary['period_text'] == period
    else:
        assert '2025년 7월' in a.boundary_label and not a.boundary


@pytest.mark.parametrize('supplied,customer', [(False, False), (True, False), (False, True), (True, True)])
def test_spaced_dot_dates_in_delivery_rows_preserve_each_recipient_record(supplied, customer):
    pages = [NORMAL['E-7'][0], NORMAL['E-7'][2],
             ('전달 기록', '공급사 6곳·고객 1곳이 전달 대상이다.'),
             ('문의 회신', '문의를 접수하고 담당자가 회신했다.')]
    if supplied:
        pages.append(('전달 기록', 'SUP-01 | 2024 . 08 . 30 11:00 발송 / 13:00 수령 확인.'))
    if customer:
        pages.append(('전달 기록', 'CUS-01 | 2024 . 08 . 30 11:00 발송 / 14:00 수령 확인.'))
    a = answer(graph_for(pages), 'E-7')
    assert a.value is (True if supplied and customer else None)


def scope_rows_with_entity_role(same_scope):
    site = '대전 제3공장' if same_scope else '부산 제7공장'
    return [('경영 책임', '적용 기간: 2024년 8월 | 사업장: 대전 제3공장 | '
             '전사 경영지원 담당자가 대표이사의 경영 책임과 권한을 지정한다. '
             '대표이사는 월 1회 경영진 검토 회의를 주재한다.'),
            ('경영진 검토 기록', f'적용 기간: 2024년 8월 | 사업장: {site} | '
             '전사 경영지원 담당자가 08-30 경영진 검토 회의를 실시하고 완료를 확인했다.')]


@pytest.mark.parametrize('same_scope', [False, True])
def test_pipe_cell_with_entity_role_cannot_override_explicit_site(same_scope):
    a = answer(graph_for(scope_rows_with_entity_role(same_scope), header='기록 모음'), 'E-2')
    assert a.value is (True if same_scope else None), a.to_dict()
    assert '전사' not in a.boundary_label
    if same_scope:
        assert a.boundary['site'] == '대전 제3공장'


@pytest.mark.parametrize('separator', [' | ', ' / '])
def test_recipient_first_body_cell_cannot_expand_explicit_site(separator):
    pages = [('경영 책임', '적용 기간: 2024년 8월 | 사업장: 대전 제3공장 | ' + NORMAL['E-2'][0][1]),
             ('경영진 검토 기록', '적용 기간: 2024년 8월 | 사업장: 대전 제3공장' + separator
              + '부산 제7공장 담당자에게 08-30 경영진 검토 회의 실시 결과를 전달했다.')]
    a = answer(graph_for(pages, header='기록 모음'), 'E-2')
    assert a.value is True and a.boundary['site'] == '대전 제3공장', a.to_dict()


@pytest.mark.parametrize('wrap', [False, True])
@pytest.mark.parametrize('ending', ['하지 않는다.', '하지 않았다.', '했다.'])
def test_line_wrapped_negative_remains_connected_to_its_action(wrap, ending):
    sep = '\n' if wrap else ''
    body = ('온실가스 감축목표를 수립한다. Scope 1, Scope 2 배출을 산정·추적한다. '
            + 'Scope 3 배출은 산정' + sep + ending + ' 온실가스 산정 결과를 보고서에 공개한다.')
    a = answer(graph_for([('온실가스 배출 현황', body)]), 'C-8')
    assert a.value is (True if ending == '했다.' else None), a.to_dict()


@pytest.mark.parametrize('separator', ['. ', '; ', '\n'])
@pytest.mark.parametrize('positive', [False, True])
def test_scope_number_and_next_sentence_year_are_not_one_numeric_token(separator, positive):
    body = ('온실가스 감축목표를 수립한다. 배출 분류는 Scope 3' + separator
            + '2024년 Scope 1, Scope 2 배출을 산정·추적했다. '
            + ('Scope 3 배출을 산정했다. ' if positive else '')
            + '온실가스 산정 결과를 보고서에 공개한다.')
    a = answer(graph_for([('온실가스 배출 현황', body)]), 'C-8')
    assert a.value is (True if positive else None), a.to_dict()


@pytest.mark.parametrize('separator', [' | ', ' / ', ': ', '：', ' '])
@pytest.mark.parametrize('site_first', [False, True])
@pytest.mark.parametrize('same_scope', [False, True])
def test_local_scope_remains_local_when_body_starts_with_entity_role(separator, site_first, same_scope):
    def row(site, body):
        fields = ['적용 기간: 2024년 8월', f'사업장: {site}']
        return ' | '.join(reversed(fields) if site_first else fields) + separator + body
    pages = [(NORMAL['E-2'][0][0], row('대전 제3공장', '전사 경영지원 담당자가 ' + NORMAL['E-2'][0][1])),
             (NORMAL['E-2'][1][0], row('대전 제3공장' if same_scope else '부산 제7공장',
                                     '전사 경영지원 담당자가 ' + NORMAL['E-2'][1][1]))]
    a = answer(graph_for(pages, header='기록 모음'), 'E-2')
    assert a.value is (True if same_scope else None), a.to_dict()
    assert '전사' not in a.boundary_label and '대전 제3공장' in a.boundary_label
    if same_scope:
        assert a.boundary['site'] == '대전 제3공장'
    else:
        assert '부산 제7공장' in a.boundary_label and not a.boundary


@pytest.mark.parametrize('record,positive', [
    ('Scope 3 배출은 산정\n하지 않는다.', False),
    ('Scope 3 배출은 산정하\n지 않는다.', False),
    ('Scope 3 배출은 산정하지\n않았다.', False),
    ('Scope 3 배출은 산정하지\n못했다.', False),
    ('Scope 3 배출을\n산정\n하지 않았다.', False),
    ('Scope 3 배출은\n산정했다.', True),
    ('Scope 3 배출을\n산정\n했다.', True),
])
def test_ocr_line_breaks_preserve_scope_action_polarity(record, positive):
    body = ('온실가스 감축목표를 수립한다. Scope 1, Scope 2 배출을 산정·추적한다. '
            + record + ' 온실가스 결과를 공개한다.')
    a = answer(graph_for([('온실가스 배출 현황', body)]), 'C-8')
    assert a.value is (True if positive else None), a.to_dict()


@pytest.mark.parametrize('record,positive', [
    ('회의를 실시\n하지 않았다.', False),
    ('회의를 실시하\n지 않았다.', False),
    ('회의를\n실시\n하지 않았다.', False),
    ('회의를\n실시했다.', True),
])
def test_ocr_line_breaks_preserve_review_execution_polarity(record, positive):
    pages = [NORMAL['E-2'][0], ('경영진 검토 기록', '08-30 경영진 검토 ' + record)]
    a = answer(graph_for(pages), 'E-2')
    assert a.value is (True if positive else None), a.to_dict()


@pytest.mark.parametrize('prefix', ['2024년', '2025년', '2024.08.30에',
                                  '2024 . 08 . 30에', '08.30에', '3.14톤을'])
@pytest.mark.parametrize('positive', [False, True])
def test_scope_ordinal_before_next_sentence_numeric_record_is_not_a_measure(prefix, positive):
    body = ('온실가스 감축목표를 수립한다. 배출 분류는 Scope 3. ' + prefix
            + ' Scope 1, Scope 2 배출을 산정·추적했다. '
            + ('Scope 3 배출을 산정했다. ' if positive else '')
            + '온실가스 결과를 공개한다.')
    a = answer(graph_for([('온실가스 배출 현황', body)]), 'C-8')
    assert a.value is (True if positive else None), a.to_dict()


@pytest.mark.parametrize('positive', [False, True])
def test_wrapped_table_action_keeps_its_polarity_before_another_cell(positive):
    body = ('온실가스 감축목표를 수립한다. Scope 1, Scope 2 배출을 산정·추적한다. '
            'Scope 3 | 산정\n' + ('했다' if positive else '하지 않는다')
            + ' | 2024.08.30. 온실가스 결과를 공개한다.')
    a = answer(graph_for([('온실가스 배출 현황', body)]), 'C-8')
    assert a.value is (True if positive else None), a.to_dict()


@pytest.mark.parametrize('protection', ['보복을 하지\n않는다.', '보복을 하지\n않았다.',
                                      '보복 없는\n참여를 보장한다.'])
def test_line_wrapped_no_retaliation_remains_positive_protection(protection):
    body = '근로자와 대표가 양방향 간담회에 참여한다. 고충 접수·구제 접근을 보장한다. ' + protection
    assert answer(graph_for([('참여·구제', body)]), 'E-8').value is True


@pytest.mark.parametrize('label', ['Scope', '범위Scope'])
@pytest.mark.parametrize('positive', [False, True])
def test_scope_ordinal_after_korean_text_is_not_part_of_next_numeric_date(label, positive):
    body = (f'온실가스 감축목표를 수립한다. 배출 분류는 {label} 3. 2024.08.30에 '
            'Scope 1, Scope 2 배출을 산정·추적했다. '
            + ('Scope 3 배출을 산정했다. ' if positive else '')
            + '온실가스 결과를 공개한다.')
    a = answer(graph_for([('온실가스 배출 현황', body)]), 'C-8')
    assert a.value is (True if positive else None), a.to_dict()


# Sixth review: failed controls and their positive/structural counterparts.
def expanded_carbon(body):
    return ('온실가스 감축목표를 수립한다. Scope 1, Scope 2 배출을 산정·추적한다. '
            + body + ' 온실가스 결과를 공개한다.')


def expanded_negative_record(code, tail, wrap):
    prefix = 'Scope 3 배출 산정은' if code == 'C-8' else '08-30 경영진 검토 회의 실시는'
    if tail == '하고 있지 않다.':
        prefix = 'Scope 3 배출은 산정' if code == 'C-8' else '08-30 경영진 검토 회의를 실시'
    return prefix + ('\n' if wrap else ' ') + tail


def expanded_recipient_rows(role, sep):
    fields = '적용 기간: 2024년 8월 | 사업장: 대전 제3공장'
    return [('경영 책임', fields + ' | ' + NORMAL['E-2'][0][1]),
            ('경영진 검토 기록', fields + sep + '부산 제7공장 ' + role
             + ' 08-30 경영진 검토 회의 실시 결과를 전달했다.')]


def expanded_cross_scope_rows(same_scope, sep):
    site = '대전 제3공장' if same_scope else '부산 제7공장'
    recipient = '부산 제7공장' if same_scope else '대전 제3공장'
    return [('경영 책임', '적용 기간: 2024년 8월 | 사업장: 대전 제3공장' + sep
             + '부산 제7공장 품질 담당자에게 규정을 전달한다. ' + NORMAL['E-2'][0][1]),
            ('경영진 검토 기록', f'적용 기간: 2024년 8월 | 사업장: {site}' + sep
             + f'{recipient} 품질 담당자에게 08-30 경영진 검토 회의 실시 결과를 전달했다.')]


@pytest.mark.parametrize('code', ['C-8', 'E-2'])
@pytest.mark.parametrize('tail', ['전혀 하지 않는다.', '아직 하지 않았다.', '하고 있지 않다.'])
@pytest.mark.parametrize('wrap', [False, True])
def test_negative_continuation_with_adverb_or_auxiliary_does_not_become_positive(code, tail, wrap):
    text = expanded_negative_record(code, tail, wrap)
    pages = [('온실가스 배출 현황', expanded_carbon(text))] if code == 'C-8' else [NORMAL['E-2'][0], ('경영진 검토 기록', text)]
    a = answer(graph_for(pages), code)
    assert a.value is None, a.to_dict()


@pytest.mark.parametrize('role', ['품질 담당자에게', '환경팀에', '담당자에게'])
@pytest.mark.parametrize('sep', [' | ', ': ', ' / ', ' '])
def test_recipient_job_description_does_not_expand_explicit_site(role, sep):
    a = answer(graph_for(expanded_recipient_rows(role, sep), header='기록 모음'), 'E-2')
    assert a.value is True and a.boundary.get('site') == '대전 제3공장', a.to_dict()


@pytest.mark.parametrize('same_scope', [False, True])
@pytest.mark.parametrize('sep', [' | ', ' / '])
def test_reciprocal_recipients_cannot_merge_different_declared_sites(same_scope, sep):
    a = answer(graph_for(expanded_cross_scope_rows(same_scope, sep), header='기록 모음'), 'E-2')
    assert a.value is (True if same_scope else None), a.to_dict()
    if same_scope:
        assert a.boundary.get('site') == '대전 제3공장', a.to_dict()


@pytest.mark.parametrize('number', ['123.45', '14.25', '3.50'])
@pytest.mark.parametrize('prefix', ['총 ', '총'])
@pytest.mark.parametrize('positive', [False, True])
def test_decimal_quantity_next_to_korean_word_is_preserved(number, prefix, positive):
    text = f'Scope 3 배출량을 {prefix}{number}톤으로 산정' + ('했다.' if positive else '하지 않았다.')
    a = answer(graph_for([('온실가스 배출 현황', expanded_carbon(text))]), 'C-8')
    assert a.value is (True if positive else None), a.to_dict()


@pytest.mark.parametrize('code', ['C-8', 'E-2'])
@pytest.mark.parametrize('continuation', ['아직\n하지 않았다.', '전혀\n하지 않는다.',
                                        '아직\n전혀\n하지 않았다.', '아무것도\n하지 않았다.'])
def test_multiple_ocr_lines_keep_negative_predicate_with_action(code, continuation):
    prefix = 'Scope 3 배출 산정은' if code == 'C-8' else '08-30 경영진 검토 회의 실시는'
    text = prefix + '\n' + continuation
    pages = [('온실가스 배출 현황', expanded_carbon(text))] if code == 'C-8' else [NORMAL['E-2'][0], ('경영진 검토 기록', text)]
    assert answer(graph_for(pages), code).value is None


@pytest.mark.parametrize('tail', ['아직 하지 않았다', '하고 있지 않다', '아직\n전혀\n하지 않았다'])
def test_adverb_or_auxiliary_negative_table_tail_keeps_polarity(tail):
    text = expanded_carbon('Scope 3 | 산정\n' + tail + ' | 2024.08.30.')
    assert answer(graph_for([('온실가스 배출 현황', text)]), 'C-8').value is None


@pytest.mark.parametrize('next_subject', ['Scope 1 배출은', '근로자는', '품질 담당자가'])
def test_new_subject_on_next_line_does_not_negate_previous_scope_record(next_subject):
    text = expanded_carbon('Scope 3 배출을 산정\n' + next_subject + ' 아직 확인하지 않았다.')
    assert answer(graph_for([('온실가스 배출 현황', text)]), 'C-8').value is True


@pytest.mark.parametrize('role', ['품질담당자에게', '현장관리자가', '환경팀에', '시설관리실에',
                                 '협력회사 담당자 08-30'])
@pytest.mark.parametrize('gap', ['', ' '])
def test_ocr_compact_recipient_and_actor_phrases_remain_body(role, gap):
    pages = expanded_recipient_rows(role, ' / ')
    pages[1] = (pages[1][0], pages[1][1].replace('제7공장 ', '제7공장' + gap))
    a = answer(graph_for(pages, header='기록 모음'), 'E-2')
    assert a.value is True and a.boundary['site'] == '대전 제3공장'


@pytest.mark.parametrize('separator', [' | ', ' '])
@pytest.mark.parametrize('sites', ['대전 제3공장 / 아산 제4공장', '대전 제3공장 아산 제4공장 이천공장'])
def test_real_multi_site_field_is_preserved_before_record_body(separator, sites):
    pages = [(s, '적용 기간: 2024년 8월 | 사업장: ' + sites + separator + t) for s, t in NORMAL['E-2']]
    a = answer(graph_for(pages, header='기록 모음'), 'E-2')
    assert a.value is True
    assert '대전 제3공장' in a.boundary['site'] and '아산 제4공장' in a.boundary['site']
    if '이천공장' in sites:
        assert '이천공장' in a.boundary['site']


# Seventh review: embedded negative subjects and department identifiers.
def actor_negative_rows(code, actor, wrap):
    prefix = 'Scope 3 배출 산정은' if code == 'C-8' else '08-30 경영진 검토 회의 실시는'
    text = prefix + ('\n' if wrap else ' ') + actor + ' 아직 하지 않았다.'
    if code == 'C-8':
        return [('온실가스 배출 현황', expanded_carbon(text))]
    return [NORMAL['E-2'][0], ('경영진 검토 기록', text)]


def named_department_rows(name, separator, same_scope=False):
    return [(s, t.replace('품질 담당자에게', name))
            for s, t in expanded_cross_scope_rows(same_scope, separator)]


@pytest.mark.parametrize('code', ['C-8', 'E-2'])
@pytest.mark.parametrize('actor', ['담당자가', '외부 검증기관이', '담당 부서에서는'])
@pytest.mark.parametrize('wrap', [False, True])
def test_subject_in_same_negative_sentence_does_not_make_action_positive(code, actor, wrap):
    a = answer(graph_for(actor_negative_rows(code, actor, wrap)), code)
    assert a.value is None and a.status == 'insufficient', a.to_dict()


@pytest.mark.parametrize('name', ['품질 담당자에게', '품질1팀에게', 'QA-1팀에게'])
@pytest.mark.parametrize('separator', [' | ', ' / '])
@pytest.mark.parametrize('same_scope', [False, True])
def test_numeric_and_hyphenated_department_names_are_body(name, separator, same_scope):
    a = answer(graph_for(named_department_rows(name, separator, same_scope), header='기록 모음'), 'E-2')
    assert a.value is (True if same_scope else None), a.to_dict()
    if same_scope:
        assert a.boundary.get('site') == '대전 제3공장', a.to_dict()


@pytest.mark.parametrize('code', ['C-8', 'E-2'])
@pytest.mark.parametrize('actor', ['품질1팀이', 'QA-1팀이'])
@pytest.mark.parametrize('completed', [False, True])
def test_numbered_actor_is_part_of_the_same_execution_predicate(code, actor, completed):
    pages = actor_negative_rows(code, actor, True)
    if completed:
        pages = [(s, t.replace('아직 하지 않았다', '실제로 했다')) for s, t in pages]
    a = answer(graph_for(pages), code)
    assert a.value is (True if completed else None), a.to_dict()


@pytest.mark.parametrize('code', ['C-8', 'E-2'])
@pytest.mark.parametrize('actor', ['담당자가\n아직', '외부\n검증기관이 아직'])
def test_negative_actor_across_multiple_ocr_lines_remains_negative(code, actor):
    a = answer(graph_for(actor_negative_rows(code, actor, True)), code)
    assert a.value is None, a.to_dict()


@pytest.mark.parametrize('prefix', ['Scope 1 배출은', 'SUP-01', 'CUS-01', '2024년',
                                  '2024.08.30', '08-30', '08.30', '11:00'])
def test_other_dated_or_identified_record_does_not_negate_scope3(prefix):
    text = expanded_carbon('Scope 3 배출을 산정\n' + prefix + ' 교육을 실시하지 않았다.')
    assert answer(graph_for([('온실가스 배출 현황', text)]), 'C-8').value is True


@pytest.mark.parametrize('department', ['품질1팀에게', 'QA-1팀에게', 'HR_2팀에게', 'QA2파트에게'])
@pytest.mark.parametrize('separator', [' / ', ' '])
def test_multi_site_list_stops_at_numbered_recipient_after_last_metadata_site(department, separator):
    fields = '적용 기간: 2024년 8월 | 사업장: 대전 제3공장' + separator + '아산 제4공장'
    recipient = separator + '부산 제7공장 ' + department
    pages = [('경영 책임', fields + recipient + ' 규정을 전달한다. ' + NORMAL['E-2'][0][1]),
             ('경영진 검토 기록', fields + recipient + ' 08-30 경영진 검토 회의 실시 결과를 전달했다.')]
    a = answer(graph_for(pages, header='기록 모음'), 'E-2')
    assert a.value is True, a.to_dict()
    assert a.boundary['site'] == '대전 제3공장, 아산 제4공장', a.to_dict()


# Eighth review: combine numbered actors with a second OCR line break.
def multiline_actor_rows(code, actor, multiline, negative):
    prefix = 'Scope 3 배출 산정은' if code == 'C-8' else '08-30 경영진 검토 회의 실시는'
    text = prefix + '\n' + actor + ('\n' if multiline else ' ')
    text += '아직 하지 않았다.' if negative else '실제로 했다.'
    if code == 'C-8':
        return [('온실가스 배출 현황', expanded_carbon(text))]
    return [NORMAL['E-2'][0], ('경영진 검토 기록', text)]


@pytest.mark.parametrize('code', ['C-8', 'E-2'])
@pytest.mark.parametrize('actor', ['담당자가', '품질1팀이', 'QA-1팀이', 'HR_2팀이'])
@pytest.mark.parametrize('multiline', [False, True])
@pytest.mark.parametrize('negative', [False, True])
def test_numbered_actor_and_second_wrap_preserve_execution_polarity(code, actor, multiline, negative):
    a = answer(graph_for(multiline_actor_rows(code, actor, multiline, negative)), code)
    assert a.value is (None if negative else True), a.to_dict()
    assert a.status == ('insufficient' if negative else 'verified'), a.to_dict()


@pytest.mark.parametrize('stage', ['initial', 'followup'])
@pytest.mark.parametrize('code', ['C-8', 'E-2'])
@pytest.mark.parametrize('actor', ['품질1팀이', 'QA-1팀이'])
def test_serialized_extraction_context_keeps_multiline_actor_negative(stage, code, actor):
    """Portable counterparts to the eight private saved-copy review failures."""
    import json
    from esgenie.ssot.evidence_graph import build_unified_graph

    pages = multiline_actor_rows(code, actor, True, True)
    clauses = [ExtractedClause(section=s, text=t, page=i) for i, (s, t) in enumerate(pages)]
    header = '2024년 8월 기록 / 대전 제3공장'
    ext = OcrExtraction('negative_execution.pdf', DocChannel.UNSTRUCTURED, 'policy_manual',
                        clauses=clauses, raw_text=header + '\n' + '\n'.join(t for _, t in pages))
    # The followup contains an additional unrelated education record; it cannot
    # prove Scope 3 calculation or a management review that explicitly did not occur.
    extractions = [ext]
    if stage == 'followup':
        extractions.append(OcrExtraction('education_followup.pdf', DocChannel.UNSTRUCTURED,
                                        'policy_manual', raw_text=header,
                                        clauses=[ExtractedClause(section='추가 교육 실시', page=0,
                                                  text='관리자와 근로자에게 방침·절차 교육을 실시했다.')]))
    saved = json.loads(json.dumps([e.to_dict() for e in extractions]))
    restored = [OcrExtraction.from_dict(e) for e in saved]
    graph = build_unified_graph(None, restored, corp_code='SYN', corp_name='합성기업', report_year=2026)
    a = answer(graph, code)
    assert a.value is None and a.status == 'insufficient', a.to_dict()
