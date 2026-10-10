"""PR #76 review controls on copies of the saved initial/followup extraction.

No OCR/LLM calls. Original PDFs, extraction records and labels are never written.
"""
import argparse
import json
import re
from dataclasses import replace
from pathlib import Path

from replay_rehearsal_answer_evidence import record

STAGES = {'initial': '20261007T062629Z_rev18', 'followup': '20261007T070758Z_rev19'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    checks = []

    def check(path, name, code, transform, expected, scope='', period=''):
        sheet, _, _ = record(path, transform)
        a = next(a for a in sheet.answers if a.qid == f'RBA-{code}')
        passed = a.value is expected and a.status == ('verified' if expected else 'insufficient')
        if scope:
            passed = passed and a.boundary.get('site') == scope
            if scope == '전사':
                passed = passed and a.boundary.get('site_scope') == 'entity'
        if period:
            passed = passed and period in a.boundary.get('period_text', '')
        checks.append({'check': name, 'passed': bool(passed), 'answer': a.to_dict()})

    for stage, dirname in STAGES.items():
        path = args.source / 'runs' / dirname / 'pipeline.json'
        for style in ('korean', 'slash', 'dot'):
            def dates(inputs, style=style):
                def rewrite(text):
                    if style == 'korean':
                        text = re.sub(r'(20\d{2})-(\d{2})-(\d{2})',
                                      lambda m: f'{m[1]}년 {int(m[2])}월 {int(m[3])}일', text)
                        text = re.sub(r'(?<!\d)(\d{2})-(\d{2})(?!\d)',
                                      lambda m: f'{int(m[1])}월 {int(m[2])}일', text)
                        return re.sub(r'(\d{1,2}):(\d{2})',
                                      lambda m: f'{int(m[1])}시 {int(m[2])}분', text)
                    separator = '/' if style == 'slash' else '.'
                    text = re.sub(r'(20\d{2})-(\d{2})-(\d{2})',
                                  lambda m: separator.join(m.groups()), text)
                    text = re.sub(r'(?<!\d)(\d{2})-(\d{2})(?!\d)',
                                  lambda m: separator.join(m.groups()), text)
                    return re.sub(r'(\d{1,2}):(\d{2})',
                                  lambda m: f'{int(m[1])}시 {int(m[2])}분', text)
                for ext in inputs:
                    if ext.source_file.startswith('15_'):
                        ext.raw_text = rewrite(ext.raw_text)
                        for clause in ext.clauses:
                            clause.text = rewrite(clause.text)
            check(path, f'{stage}:E-2:date_{style}', 'E-2', dates, True)

        for code in ('E-2', 'E-6'):
            def negative(inputs, code=code):
                for ext in inputs:
                    if code == 'E-2' and ext.source_file.startswith('15_'):
                        for clause in ext.clauses:
                            if clause.page == 1:
                                clause.text = '2026년 4월 30일 경영진 검토 회의를 실시하지 않았다. 검토 완료를 승인하지 않았다.'
                    if code == 'E-6' and ext.source_file.startswith(('09_', '13_')):
                        for clause in ext.clauses:
                            clause.text = '관리자와 근로자를 대상으로 안전 규정·절차 이행 교육 프로그램을 실시하지 않았다.'
            check(path, f'{stage}:{code}:negative_execution', code, negative, None)

        for case, period, site in [('period', '2025년 7월', '대전 제3공장'),
                                   ('site', '2024년 8월', '부산 제7공장'),
                                   ('both', '2025년 7월', '부산 제7공장'),
                                   ('equal', '2024년 8월', '대전 제3공장')]:
            def scopes(inputs, period=period, site=site):
                for ext in inputs:
                    if ext.source_file.startswith('15_'):
                        ext.raw_text = '경영 책임 및 검토 기록 모음'
                        for clause in ext.clauses:
                            prefix = '2024년 8월 대전 제3공장' if clause.page == 0 else f'{period} {site}'
                            clause.text = prefix + ': ' + clause.text
            check(path, f'{stage}:E-2:one_file_scope_{case}', 'E-2', scopes,
                  True if case == 'equal' else None)

        for site in ('부산사업장', '부산공장', '전사 모든 사업장'):
            def named_site(inputs, site=site):
                for ext in inputs:
                    if ext.source_file.startswith('16_'):
                        ext.raw_text = '2024년 8월 기록 / ' + site
            check(path, f'{stage}:E-7:site_{site}', 'E-7', named_site, True,
                  '전사' if site.startswith('전사') else site)

        for form in ('labeled_block', 'site_first'):
            for case, period, site in [('period', '2025년 7월', '대전 제3공장'),
                                       ('site', '2024년 8월', '부산 제7공장'),
                                       ('both', '2025년 7월', '부산 제7공장'),
                                       ('equal', '2024년 8월', '대전 제3공장')]:
                def declared_scopes(inputs, form=form, period=period, site=site):
                    for ext in inputs:
                        if not ext.source_file.startswith('15_'):
                            continue
                        ext.raw_text = '경영 책임 및 검토 기록 모음'
                        if form == 'site_first':
                            for clause in ext.clauses:
                                p, s = ('2024년 8월', '대전 제3공장') if clause.page == 0 else (period, site)
                                clause.text = f'{s} / {p}: ' + clause.text
                        else:
                            blocks = []
                            for page, p, s in [(0, '2024년 8월', '대전 제3공장'), (1, period, site)]:
                                # 책임·절차 본문과 실제 검토 행을 한 OCR 조항으로 묶는다.
                                # 별도 의사결정/요약은 합치지 않아 서로 다른 범위의
                                # 부분 요건을 연결하는 실패만 독립적으로 검사한다.
                                text = '\n'.join(c.text for c in ext.clauses if c.page == page
                                                 and (page == 0 or c.section == '경영진 정기 검토 실시 기록'))
                                blocks.append(f'적용 기간: {p}\n사업장: {s}\n' + text)
                            ext.clauses = [replace(ext.clauses[0], section='경영 책임 및 검토 기록',
                                                   text='\n'.join(blocks))]
                check(path, f'{stage}:E-2:{form}_{case}', 'E-2', declared_scopes,
                      True if case == 'equal' else None)

        for case, scope_three in [('absent', ''), ('negated', 'Scope 3 배출은 추적하지 않는다. '),
                                  ('tracked', 'Scope 3 배출을 추적한다. ')]:
            def heading_topic(inputs, scope_three=scope_three):
                for ext in inputs:
                    if ext.source_file.startswith('06_'):
                        ext.clauses[0].section = '온실가스 관리 및 Scope 3 배출 추적'
                        ext.clauses[0].text = ('온실가스 감축목표를 수립한다. '
                                               'Scope 1, Scope 2 배출을 산정·추적한다. '
                                               + scope_three + '온실가스 산정 결과를 보고서에 공개한다.')
            check(path, f'{stage}:C-8:heading_topic_{case}', 'C-8', heading_topic,
                  True if case == 'tracked' else None)

        for case, header, period in [
            ('hyphen', '2024-08-30 기준 / 부산사업장', '2024-08-30'),
            ('slash', '2024/08/30 기준 / 부산사업장', '2024/08/30'),
            ('dot', '2024.08.30 기준 / 부산사업장', '2024.08.30'),
            ('site_first', '부산사업장 / 2024/08/30 기준', '2024/08/30'),
            ('range', '2024/08/01~2024/08/30 기준 | 부산사업장', '2024/08/01~2024/08/30'),
        ]:
            def source_date(inputs, header=header):
                for ext in inputs:
                    if ext.source_file.startswith('16_'):
                        ext.raw_text = header
            check(path, f'{stage}:E-7:source_date_{case}', 'E-7', source_date, True,
                  scope='부산사업장', period=period)

    payload = {'mode': 'saved_extraction_copies_review_controls', 'external_calls': 0,
               'passed': all(c['passed'] for c in checks), 'checks': checks,
               'note': 'No new AI/OCR run or independent A/B labels/scores.'}
    (args.output / 'checks.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"{sum(c['passed'] for c in checks)}/{len(checks)} review checks passed")
    for c in checks:
        if not c['passed']:
            a = c['answer']
            print('FAIL', c['check'], a['value'], a['status'], a['rationale'], a['boundary_label'])
    if not payload['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
