"""Fresh diagnostics for the existing behavior, not a patch to the implementation.

Synthetic examples establish reproducible correctness problems, not prevalence.
The D3 abstention cases are explicitly proposed product requirements. The unit
cases cover the typed supplier-claim boundary; the current PDF parser extracts
recycling percentages only. All forms below are production SAQ questions.
"""
from types import SimpleNamespace

import pytest

from esgenie.dart_client import CompanyReport, SOURCE_DART_REGEX
from esgenie.layer1_extract import ExtractionResult
from esgenie.pipeline import _apply_survey_answers, _collect_ocr_extractions, _build_risk_rows
from esgenie.ssot.evidence_graph import (
    EvidenceGraph, EvidenceNode, TextNode, build_unified_graph, build_from_dart,
)
from esgenie.ssot.ssot_pipeline import extract_with_ssot, _merge_ssot_evidence
from esgenie.ssot.audit_trace import DataPoint, EvidenceLink, build_data_points
from esgenie.supplychain.claims import SupplierClaim, parse_saq_claims, _extract_text
from esgenie.supplychain.frameworks.saq5 import SAQ5_ENV
from esgenie.supplychain.responder import build_response_sheet, respond_from_pipeline


def answer(sheet, qid):
    return next(a for a in sheet.answers if a.qid == qid)


def report(kesg_data=None):
    return CompanyReport(corp_code='AUDIT', corp_name='Synthetic audit company',
        industry='', report_year=2025, financials={}, kesg_data=kesg_data or {},
        raw_text_snippets=[], source='synthetic')


def numeric_answer(actual, claimed, *, unit='%', claim_unit='%', code='E-6-2'):
    point = DataPoint(code, code, actual, unit, 2025, .94, 'verified', 0,
        [EvidenceLink('audit.pdf', 'evidence_pack/audit.pdf', 'ocr_structured',
            node_id='value', page=0)])
    graph = EvidenceGraph('AUDIT', 'Audit')
    graph.add_node(EvidenceNode('value', code, actual, unit, 2025, 'ocr',
        source_file='audit.pdf', origin='ocr_structured', raw_text=f'{code} {actual}{unit}', page=0))
    sheet = build_response_sheet(SAQ5_ENV, data_points=[point], evidence_graph=graph,
        supplier_claims={code: SupplierClaim(code, claimed, claim_unit)})
    qid = next(q.qid for q in SAQ5_ENV.questions if q.qtype == 'numeric' and q.primary_code == code)
    return answer(sheet, qid)


def record_answer(observe, ans, **extra):
    observe(value=ans.value, status=ans.status, flags=ans.flags,
        rationale=ans.rationale, evidence=[x.to_dict() for x in ans.evidence_links], **extra)


class TestSurveyAndEvidence:
    @pytest.mark.parametrize('reply,note', [
        ('예', ''), ('아니오', ''),
        ('예', '환경경영 목표를 수립함'), ('아니오', '환경경영 목표를 수립하지 않음'),
    ], ids=['yes', 'no', 'yes-with-note', 'no-with-note'])
    def test_survey_only_stays_self_reported(self, reply, note, observe):
        survey = {'E-1-1': {'yn': reply, 'text': note}}
        extractions = _collect_ocr_extractions(None, survey_answers=survey)
        graph = build_unified_graph(None, extractions, corp_code='AUDIT',
            corp_name='Synthetic audit company', report_year=2025)
        ledger = extract_with_ssot(report(), graph, profile='sme')
        _apply_survey_answers(ledger, survey)
        ans = answer(build_response_sheet(SAQ5_ENV, extraction=ledger,
            evidence_graph=graph), 'SAQ-E-10')
        record_answer(observe, ans, survey=survey, ledger=ledger.mapped.get('E-1-1'),
            graph_text_nodes=[n.to_dict() for n in graph.text_nodes.values()])
        assert ans.status != 'verified', 'A self-report has no independent evidence'
        if reply == '아니오':
            assert ans.value is not True, 'A negative survey response must not turn positive'

    def test_unanswered_survey_stays_unanswered(self, observe):
        ledger = extract_with_ssot(report(), EvidenceGraph('AUDIT', 'Audit'), profile='sme')
        _apply_survey_answers(ledger, {'E-1-1': {'yn': '미입력', 'text': ''}})
        ans = answer(build_response_sheet(SAQ5_ENV, extraction=ledger), 'SAQ-E-10')
        record_answer(observe, ans)
        assert ans.value is None and ans.status != 'verified'

    @pytest.mark.parametrize('kind,expected', [
        ('valid', 'verified'), ('unlinked', 'self_reported'),
        ('dangling', 'not_verified'), ('wrong-topic', 'not_verified'),
    ])
    def test_policy_requires_resolvable_relevant_evidence(self, kind, expected, observe):
        graph = EvidenceGraph('AUDIT', 'Audit')
        ids = [] if kind == 'unlinked' else ['policy']
        if kind in ('valid', 'wrong-topic'):
            graph.add_text_node(TextNode('policy', 'Policy',
                '환경방침을 수립하고 환경성과를 점검한다.' if kind == 'valid' else '근로자 고충을 접수한다.',
                'E-1-1' if kind == 'valid' else 'S-7-1', 'policy.pdf', 0))
        ledger = SimpleNamespace(mapped={'E-1-1': {'value': True, 'evidence_node_ids': ids}}, missing=[])
        ans = answer(build_response_sheet(SAQ5_ENV, extraction=ledger,
            evidence_graph=graph), 'SAQ-E-10')
        record_answer(observe, ans, evidence_kind=kind)
        if expected == 'not_verified':
            assert ans.status != 'verified'
        else:
            assert ans.status == expected

    def test_one_valid_link_cannot_verify_other_unsupported_options(self, observe):
        graph = EvidenceGraph('AUDIT', 'Audit')
        graph.add_text_node(TextNode('energy', 'Policy', '에너지 효율을 관리한다.',
            'E-4-1', 'policy.pdf', 0))
        ledger = SimpleNamespace(mapped={
            'E-4-1': {'value': True, 'evidence_node_ids': ['energy']},
            'E-6-2': {'value': True, 'evidence_node_ids': []},
        }, missing=[])
        ans = answer(build_response_sheet(SAQ5_ENV, extraction=ledger,
            evidence_graph=graph), 'SAQ-E-10a')
        record_answer(observe, ans)
        assert ans.status != 'verified', 'A single link must not verify all selected options'


class TestNumericalClaims:
    @pytest.mark.parametrize('actual,claimed,expected', [
        (43.8, 43.8, 'verified'), (43.8, 90.2, 'flagged'),
        (0, 0, 'verified'), (100, 100, 'verified'),
        (40, 49.9, 'verified'), (40, 50, 'flagged'),
    ])
    def test_valid_values_and_documented_threshold(self, actual, claimed, expected, observe):
        ans = numeric_answer(actual, claimed)
        record_answer(observe, ans, actual=actual, claimed=claimed)
        assert ans.status == expected

    def test_rounding_does_not_cross_ten_percentage_point_threshold(self, observe):
        ans = numeric_answer(40, 49.99)
        record_answer(observe, ans, absolute_difference=9.99)
        assert ans.status != 'flagged', 'The documented threshold is at least 10 percentage points'

    @pytest.mark.parametrize('side,bad', [
        ('evidence', -4), ('evidence', 101.2), ('evidence', float('nan')),
        ('evidence', float('inf')), ('claim', -4), ('claim', 101.2),
        ('claim', float('nan')), ('claim', float('inf')),
    ], ids=['evidence-negative', 'evidence-over-100', 'evidence-nan', 'evidence-infinite',
        'claim-negative', 'claim-over-100', 'claim-nan', 'claim-infinite'])
    def test_invalid_rates_are_never_reported_as_a_match(self, side, bad, observe):
        # The good side is a boundary for finite invalid values, and a normal rate otherwise.
        good = 0 if bad == -4 else 100 if bad == 101.2 else 43.8
        actual, claimed = (bad, good) if side == 'evidence' else (good, bad)
        ans = numeric_answer(actual, claimed)
        record_answer(observe, ans, bad_side=side, invalid_value=str(bad))
        assert ans.status != 'verified' and not any('자가신고 일치' in f for f in ans.flags)

    @pytest.mark.parametrize('actual,unit,claimed,claim_unit,matching', [
        (2000, 'kWh', 2, 'MWh', True),
        (2, 'MWh', 2000, 'kWh', True),
        (2, 'kWh', 2, 'MWh', False),
        (2, 'kWh', 2, 'm3', False),
        (2000, 'kWh', 2000, 'kWh', True),
    ], ids=['convert-mwh-to-kwh', 'convert-kwh-to-mwh', 'same-number-wrong-scale',
        'incompatible-dimensions', 'same-unit-control'])
    def test_energy_claims_respect_units(self, actual, unit, claimed, claim_unit, matching, observe):
        ans = numeric_answer(actual, claimed, unit=unit, claim_unit=claim_unit, code='E-4-1')
        record_answer(observe, ans, actual=actual, actual_unit=unit, claimed=claimed, claim_unit=claim_unit)
        has_match = any('자가신고 일치' in f for f in ans.flags)
        assert (ans.status == 'verified' and has_match) if matching else not has_match


class TestActualSaqPdfParsing:
    @pytest.mark.parametrize('text,expected', [
        ('2025년 폐기물 재활용률 43.8% 달성', 43.8),
        ('2030년 재활용률 목표 90.2%\n2025년 재활용률 43.8% 달성', 43.8),
        ('2025년 재활용률 43.8% 달성\n2030년 재활용률 목표 90.2%', 43.8),
        ('2030년 재활용률 목표 90.2%', None),
        ('2030년 재활용률 90.2% 목표', None),
        ('2025년 재활용률 -4%', None),
        ('2025년 재활용률 101.2%', None),
        ('2025년 재활용률 0% 달성', 0),
        ('2025년 재활용률 100% 달성', 100),
        ('2025년 매립·소각 12.5% 수준', 87.5),
    ], ids=['actual', 'target-before-actual', 'actual-before-target', 'target-only',
        'postfix-target', 'negative', 'over-100', 'zero', 'hundred', 'complement'])
    def test_embedded_pdf_text_yields_only_valid_actual(self, text, expected, tmp_path, observe):
        import fitz
        path = tmp_path / 'supplier_saq.pdf'
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_text((35, 50), text, fontname='korea', fontsize=11)
            doc.save(path)
        extracted = _extract_text(str(path))
        assert ''.join(text.split()) in ''.join(extracted.split()), 'Generated PDF text did not round-trip'
        parsed = parse_saq_claims([str(path)])
        claim = parsed.get('E-6-2')
        observe(input=text, extracted=extracted, expected=expected,
            parsed_value=claim.value if claim else None, raw=claim.raw if claim else None)
        assert (claim is None) if expected is None else (claim is not None and claim.value == expected)


def energy_node(nid, value, *, role='total', confidence=.9, year=2025):
    labels = {'total': '에너지 사용량 전사 합계', 'component': '에너지 사용량 국내 사업장',
        'target': '에너지 사용량 목표'}
    return EvidenceNode(nid, 'E-4-1', value, 'TJ', year, 'ocr/energy',
        raw_text=f'{labels[role]} = {value} TJ', origin='ocr_structured',
        source_file='energy.pdf', confidence=confidence, value_role=role, page=0)


class TestLedgerToResponse:
    @pytest.mark.parametrize('case', ['single', 'component', 'target', 'future-target', 'older-total'])
    def test_selected_ledger_value_survives_export(self, case, observe):
        graph = EvidenceGraph('AUDIT', 'Audit')
        graph.report_year = 2025
        graph.add_node(energy_node('selected-total', 248.5))
        if case != 'single':
            graph.add_node(energy_node('decoy', 61.2,
                role='component' if case == 'component' else 'target' if 'target' in case else 'total',
                confidence=.99, year=2030 if case == 'future-target' else 2024 if case == 'older-total' else 2025))
        ledger = extract_with_ssot(report(), graph, profile='sme')
        assert ledger.mapped['E-4-1']['value'] == 248.5, 'L1 control must select the actual total'
        scores, rows = _build_risk_rows(graph, target_codes=['E-4-1'])
        points = build_data_points(graph, scores, target_codes=['E-4-1'])
        output = SimpleNamespace(report=report(), extraction=ledger, evidence_graph=graph,
            v15_trace=SimpleNamespace(data_points=points))
        ans = answer(respond_from_pipeline(output, SAQ5_ENV), 'SAQ-E-NUM-ENERGY')
        record_answer(observe, ans, scenario=case, ledger_value=248.5,
            representative=graph.representative_node_ids.get('E-4-1'),
            points=[p.to_dict() for p in points], risk_rows=rows)
        assert ans.value == 248.5 and points[0].period == 2025

    def test_target_only_cannot_reappear_as_verified_actual(self, observe):
        graph = EvidenceGraph('AUDIT', 'Audit')
        graph.report_year = 2025
        graph.add_node(energy_node('target', 61.2, role='target'))
        ledger = extract_with_ssot(report(), graph, profile='sme')
        assert 'E-4-1' not in ledger.mapped, 'L1 must reject the target'
        points = build_data_points(graph, {}, target_codes=['E-4-1'])
        ans = answer(build_response_sheet(SAQ5_ENV, extraction=ledger, data_points=points),
            'SAQ-E-NUM-ENERGY')
        record_answer(observe, ans, confidence_flags=ledger.confidence_flags,
            points=[p.to_dict() for p in points])
        assert ans.value is None and ans.status != 'verified'

    def test_superseded_dart_regex_does_not_return_in_export(self, observe):
        source_report = report({'E-4-1': {'value': 999.0, 'unit': 'TJ',
            'source_tier': SOURCE_DART_REGEX, 'note': 'DART 원문 정규식 추출'}})
        graph = build_from_dart(source_report)
        graph.report_year = 2025
        graph.add_node(energy_node('gated-total', 248.5))
        ledger = extract_with_ssot(source_report, graph, profile='sme')
        assert ledger.mapped['E-4-1']['value'] == 248.5, 'L1 must supersede the raw regex value'
        scores, rows = _build_risk_rows(graph, target_codes=['E-4-1'])
        points = build_data_points(graph, scores, target_codes=['E-4-1'])
        ans = answer(build_response_sheet(SAQ5_ENV, extraction=ledger, data_points=points),
            'SAQ-E-NUM-ENERGY')
        record_answer(observe, ans, ledger=ledger.mapped['E-4-1'],
            points=[p.to_dict() for p in points], risk_rows=rows)
        assert ans.value == 248.5

    @pytest.mark.parametrize('order', ['total-first', 'total-last'])
    def test_risk_rows_describe_the_selected_total(self, order, observe):
        nodes = [energy_node('total', 248.5), energy_node('component', 61.2, role='component')]
        if order == 'total-last':
            nodes.reverse()
        graph = EvidenceGraph('AUDIT', 'Audit')
        graph.report_year = 2025
        for node in nodes:
            graph.add_node(node)
        ledger = extract_with_ssot(report(), graph, profile='sme')
        _, rows = _build_risk_rows(graph, target_codes=['E-4-1'])
        observe(order=order, ledger_value=ledger.mapped['E-4-1']['value'], risk_rows=rows)
        assert rows[0]['값'] == '248.5 TJ'


@pytest.fixture
def fake_provider(monkeypatch):
    from esgenie import llm, llm_cache
    calls = []
    monkeypatch.setattr(llm.SETTINGS, 'force_mock', False)
    monkeypatch.setattr(llm.SETTINGS, 'openai_api_key', 'synthetic-test-key')
    monkeypatch.setattr(llm.SETTINGS, 'openai_model', 'synthetic-deployment')
    monkeypatch.setattr(llm.SETTINGS, 'azure_openai_endpoint', 'https://audit-a.invalid/openai/v1')
    monkeypatch.setattr(llm.SETTINGS, 'pii_mask', False)
    monkeypatch.setattr(llm.SETTINGS, 'strict_llm', True)
    monkeypatch.setenv('ESGENIE_FORCE_MOCK', '0')
    llm_cache.reset_stats()

    def factory(settings):
        endpoint = settings.azure_openai_endpoint
        def create(**kwargs):
            calls.append({'endpoint': endpoint, 'request': kwargs})
            content = f'synthetic-response-{len(calls)}-{endpoint}'
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    monkeypatch.setattr(llm, 'create_openai_client', factory)
    return llm, calls


class TestProviderCache:
    def test_identical_request_reuses_success(self, fake_provider, observe):
        llm, calls = fake_provider
        client = llm.LLMClient()
        first = client.complete('audit system', 'audit question')
        second = client.complete('audit system', 'audit question')
        observe(provider_calls=len(calls), first=first.meta, second=second.meta)
        assert len(calls) == 1 and first.content == second.content and second.meta['cache'] == 'hit'

    @pytest.mark.parametrize('change', ['prompt', 'model', 'json-mode', 'temperature', 'endpoint'])
    def test_changed_request_identity_requires_provider(self, change, fake_provider, monkeypatch, observe):
        llm, calls = fake_provider
        client = llm.LLMClient()
        first = client.complete('audit system', 'audit question')
        kwargs = {}
        prompt = 'audit question'
        if change == 'prompt':
            prompt = 'different audit question'
        elif change == 'model':
            monkeypatch.setattr(llm.SETTINGS, 'openai_model', 'other-deployment')
        elif change == 'json-mode':
            kwargs['json_mode'] = True
        elif change == 'temperature':
            kwargs['temperature'] = 0.7
        elif change == 'endpoint':
            monkeypatch.setattr(llm.SETTINGS, 'azure_openai_endpoint', 'https://audit-b.invalid/openai/v1')
            client = llm.LLMClient()
        second = client.complete('audit system', prompt, **kwargs)
        observe(changed=change, provider_calls=len(calls), first=first.meta,
            second=second.meta, first_content=first.content, second_content=second.content)
        assert len(calls) == 2 and second.meta['cache'] == 'miss'

    def test_cache_disabled_always_calls_provider(self, fake_provider, monkeypatch, observe):
        llm, calls = fake_provider
        monkeypatch.setenv('ESGENIE_LLM_CACHE', '0')
        client = llm.LLMClient()
        client.complete('audit system', 'audit question')
        client.complete('audit system', 'audit question')
        observe(provider_calls=len(calls))
        assert len(calls) == 2

    def test_strict_failure_does_not_become_mock_success(self, fake_provider, observe):
        llm, calls = fake_provider
        client = llm.LLMClient()
        def fail(**kwargs):
            raise RuntimeError('Synthetic provider failure')
        client._openai_client.chat.completions.create = fail
        with pytest.raises(llm.LLMUnavailableError):
            client.complete('audit system', 'audit question')
        observe(strict_failure='LLMUnavailableError')


class TestProposedD3Requirement:
    @pytest.mark.parametrize('kind', ['no-chunks', 'empty-index'])
    def test_missing_evidence_is_explicit_abstention(self, kind, observe):
        from esgenie.layer3_detect import score_d3_semantic, _build_risk_vector
        from esgenie.schemas import AxisScore
        index = None if kind == 'no-chunks' else SimpleNamespace(_docs=[], search=lambda *a, **k: [])
        d3 = score_d3_semantic('폐기물 재활용률은 43.8%다.', [], prebuilt_index=index)
        risk = _build_risk_vector(AxisScore(0), AxisScore(0), d3, AxisScore(0))
        observe(requirement='Proposed abstention behavior', d3=d3.to_dict(), aggregate=risk.aggregate)
        assert d3.abstain, 'Proposed requirement: distinguish unavailable evidence from measured risk'
