"""Check both saved stages and controlled changes without external requests."""
import argparse
import json
from pathlib import Path

from replay_rehearsal_answer_evidence import record
from esgenie.web.presenter import present_sheet

NORMAL = {'B-6': ('14', {0, 1, 2}), 'E-2': ('15', {0, 1}),
          'E-7': ('16', {0, 1, 2}), 'E-10': ('17', {0, 1}), 'E-11': ('18', {0, 1})}
STAGES = {'initial': '20261007T062629Z_rev18', 'followup': '20261007T070758Z_rev19'}
FIELDS = ('value', 'status', 'evidence_links', 'reference_links', 'boundary_label', 'boundary',
          'scope_notes', 'comparison', 'comparison_reason', 'self_reports', 'flags', 'rationale')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    checks, diffs = [], {}

    def check(name, passed, detail=''):
        checks.append({'check': name, 'passed': bool(passed), 'detail': detail})

    for stage, dirname in STAGES.items():
        path = args.source / 'runs' / dirname / 'pipeline.json'
        sheet, graph, inputs = record(path)
        answers = {a.qid: a for a in sheet.answers}
        old = {a['qid']: a for a in json.loads((args.baseline / f'{stage}.json').read_text())['sheet']['answers']}
        historical = json.loads(path.with_name('result.json').read_text())['sheet']['answers']
        check(f'{stage}:baseline_presence_and_numeric_values',
              all(old[a['qid']]['value'] == a['value'] and old[a['qid']]['status'] == a['status'] for a in historical))
        check(f'{stage}:48_unique_rows', len(answers) == 48 and set(answers) == set(old))
        for code, (prefix, pages) in NORMAL.items():
            a = answers[f'RBA-{code}']
            actual = {e.page for e in a.evidence_links if Path(e.file_name).name.startswith(prefix + '_')}
            check(f'{stage}:{code}:full_pages', a.value is True and a.status == 'verified' and actual == pages,
                  f'expected={sorted(pages)}, actual={sorted(actual)}')
            check(f'{stage}:{code}:source_scope', bool(a.boundary_label) and '2026' in a.boundary_label)
        for code in ['A-3', 'C-7', 'C-8', 'E-3', 'E-4']:
            a = answers[f'RBA-{code}']
            check(f'{stage}:{code}:false_yes_removed', old[a.qid]['value'] is True and a.value is None and a.status == 'insufficient')
        for code in ['B-8', 'E-8']:
            a = answers[f'RBA-{code}']
            check(f'{stage}:{code}:partial_evidence_stays_pending', a.value is None and bool(a.evidence_links))
        a = answers['RBA-E-6']
        prefixes = {Path(e.file_name).name[:2] for e in a.evidence_links}
        check(f'{stage}:education_not_delivery', '16' not in prefixes and '18' not in prefixes and '09' in prefixes)
        check(f'{stage}:followup_education_connected', ('13' in prefixes) == (stage == 'followup'))
        if stage == 'followup':
            extra = next(ext for ext in inputs if ext.source_file.startswith('13_'))
            counts = {m.metric_hint: m.value for m in extra.metrics}
            check('followup:education_46_plus_4_distinct_50',
                  counts.get('4월 22일 참석 · 고유 인원') == 46 and
                  counts.get('4월 27일 추가 참석 · 고유 인원') == 4 and
                  counts.get('중복 제외 합계 · 고유 인원') == 50)

        check(f'{stage}:company_answer_is_separate', all(e.independent and not e.file_name.startswith('05_')
              for a in sheet.answers for e in a.evidence_links))
        numeric = [qid for qid in old if len(qid.split('-')) > 3 and old[qid]['unit']]
        # All numerical outputs retain the same value, boundary, evidence and comparison.
        numeric_fields = ('value', 'unit', 'period', 'boundary_label', 'boundary', 'completeness',
                          'comparison', 'comparison_reason', 'evidence_links', 'reference_links', 'self_reports')
        check(f'{stage}:numerical_meaning_unchanged', all(
            all(answers[qid].to_dict()[k] == old[qid][k] for k in numeric_fields) for qid in numeric))
        waste = answers['RBA-C-4-E-6-2']
        check(f'{stage}:different_denominators_preserved', waste.value == 29.3 and waste.comparison == 'not_comparable'
              and any(x.get('value') == 92 for x in waste.self_reports))
        shown = {a['id']: a for a in present_sheet(sheet.to_dict(), [])}
        check(f'{stage}:presenter_uses_shared_scope_and_sources', all(shown[qid]['scope_label'] == a.boundary_label and
              len(shown[qid]['sources']) == len(a.evidence_links) for qid, a in answers.items()))
        newly_positive = [qid for qid, a in answers.items() if a.value is True and old[qid]['value'] is not True]
        check(f'{stage}:new_yes_only_communication_and_records', set(newly_positive) == {'RBA-E-7', 'RBA-E-11'}, newly_positive)
        diffs[stage] = [{'qid': qid, 'changed_fields': [k for k in FIELDS if old[qid].get(k) != a.to_dict().get(k)],
                         'before': {k: old[qid].get(k) for k in FIELDS},
                         'after': {k: a.to_dict().get(k) for k in FIELDS}} for qid, a in answers.items()]

    path = args.source / 'runs' / STAGES['initial'] / 'pipeline.json'
    # A missing page removes its extracted clauses AND metrics, while the original saved
    # source is untouched. Only these synthetic copies are sent into the replay builder.
    for code, (prefix, pages) in NORMAL.items():
        for page in pages:
            def remove(inputs, prefix=prefix, page=page):
                for ext in inputs:
                    if ext.source_file.startswith(prefix + '_'):
                        ext.clauses = [c for c in ext.clauses if c.page != page]
                        ext.metrics = [m for m in ext.metrics if m.page != page]
            sheet, _, _ = record(path, remove)
            a = next(a for a in sheet.answers if a.qid == f'RBA-{code}')
            check(f'variant:{code}:remove_page_{page + 1}', a.value is None and a.status == 'insufficient', a.rationale)
    def rename(inputs):
        for i, ext in enumerate(inputs):
            ext.source_file = f'renamed_evidence_{i}.pdf'
            ext.raw_text = ext.raw_text.replace('2026', '2021').replace('김해', '울산').replace('양산', '대전')
            for clause in ext.clauses:
                clause.text = clause.text.replace('전달', '배포').replace('2026', '2021').replace('김해', '울산').replace('양산', '대전')
                clause.section = clause.section.replace('전달', '배포')
    sheet, _, _ = record(path, rename)
    for code in NORMAL:
        a = next(a for a in sheet.answers if a.qid == f'RBA-{code}')
        check(f'variant:{code}:rename_expression_period_site', a.value is True and a.status == 'verified'
              and '2021' in a.boundary_label and '2026' not in a.boundary_label, a.boundary_label)
    payload = {'mode': 'saved_extraction_replay_and_synthetic_variants', 'external_calls': 0,
               'checks': checks, 'passed': all(c['passed'] for c in checks),
               'note': 'Not a new AI/OCR run or independent A/B quality score.'}
    (args.output / 'checks.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    (args.output / 'diff_48_rows.json').write_text(json.dumps(diffs, ensure_ascii=False, indent=2))
    print(f"{sum(c['passed'] for c in checks)}/{len(checks)} checks passed")
    for c in checks:
        if not c['passed']:
            print('FAIL', c)
    if not payload['passed']:
        raise SystemExit(1)

if __name__ == '__main__':
    main()
