"""Independent synthetic controls for requirement coverage, polarity and source scope.

No OCR/LLM calls or rehearsal labels. Saved real extraction checks live in the replay
script; these fixtures deliberately use different names, periods and expressions.
"""
from dataclasses import replace
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
