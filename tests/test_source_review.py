"""확인 목록은 관측된 근거만 설명하며 기존 점수·대표 선택을 바꾸지 않는다."""
from copy import deepcopy
from dataclasses import asdict, replace
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from esgenie.dart_client import CompanyReport
from esgenie.layer3_detect import _build_risk_vector, score_d1_numeric, score_d2_modifier, score_d5_timeseries
from esgenie.layer6_report import _block_source_review
from esgenie.schemas import AxisScore, GroundingResult, RiskVector
from esgenie.source_review import build_source_review, review_markdown
from esgenie.ssot.evidence_graph import EvidenceEdge, EvidenceGraph, EvidenceNode, TextNode, merge_ocr_extraction
from esgenie.ssot.ocr_router import DocChannel, ExtractedClause, ExtractedMetric, OcrExtraction
from esgenie.ssot.ssot_pipeline import extract_with_ssot


def node(nid, value, *, hint="환경교육 이수율", code="S-2-3", unit="%", year=2024,
         source="report.pdf", inferred=False, role="total", page=None, quote=""):
    return EvidenceNode(
        id=nid, metric=code, value=value, unit=unit, period=year,
        source="ocr/report", raw_text=f"{hint}={value}{unit} ({source})",
        origin="ocr_unstructured", source_file=source, period_inferred=inferred,
        value_role=role, page=page, quote=quote, page_source="chunk" if page is not None else "",
    )


def output(*nodes, extracts=(), sections=None):
    graph = EvidenceGraph("TEST", "테스트")
    graph.report_year = 2024
    for item in nodes:
        graph.add_node(item)
    return SimpleNamespace(
        evidence_graph=graph, extraction=SimpleNamespace(mapped={}, confidence_flags={}),
        ocr_extractions=list(extracts), sections=sections or {}, item_retrievals=[],
    )


def source_facts(result):
    return [finding for finding in build_source_review(result) if finding.category == "source_fact"]


def with_ledger(*nodes):
    result = output(*nodes)
    report = CompanyReport("TEST", "테스트", "", 2024, {}, {}, [], "test")
    result.extraction = extract_with_ssot(report, result.evidence_graph, profile="full")
    return result


@pytest.mark.parametrize("value,expected", [(0, False), (1, True)])
def test_violation_finding_requires_a_positive_disclosed_count(value, expected):
    result = with_ledger(node("violation", value, hint="환경 법규 위반 건수", code="E-8-1", unit="건"))
    findings = source_facts(result)
    assert bool(findings) is expected
    if expected:
        assert findings[0].code == "E-8-1"
        assert "시정" in findings[0].action


def test_explicit_unresolved_fact_does_not_crash_or_reappear():
    result = output(node("candidate", 1, hint="환경 법규 위반 건수", code="E-8-1", unit="건"))
    result.evidence_graph.resolved_facts["E-8-1"] = None
    assert source_facts(result) == []


def test_unknown_violation_year_is_not_presented_as_a_dated_event():
    result = with_ledger(node("violation", 1, hint="환경 법규 위반 건수", code="E-8-1", unit="건", inferred=True))
    assert source_facts(result) == []
    assert any("연도" in finding.title for finding in build_source_review(result))


def test_missing_resolved_period_is_not_displayed_as_a_dated_violation():
    result = with_ledger(node("violation", 1, hint="환경 법규 위반 건수", code="E-8-1", unit="건"))
    facts = result.evidence_graph.resolved_facts
    facts["E-8-1"] = replace(facts["E-8-1"], period=None)
    assert source_facts(result) == []


@pytest.mark.parametrize("before,after,expected", [(80, 90, False), (90, 90, False), (90, 80, True)])
def test_training_completion_only_declines_are_reviewed(before, after, expected):
    result = output(node("old", before, year=2023), node("new", after))
    findings = source_facts(result)
    assert bool(findings) is expected
    if expected:
        assert [ref.node_id for ref in findings[0].evidence] == ["old", "new"]


def test_decreasing_noncompletion_rate_is_not_an_adverse_completion_trend():
    result = output(node("old", 20, year=2023, hint="환경교육 미이수율"),
                    node("new", 10, hint="환경교육 미이수율"))
    assert source_facts(result) == []


@pytest.mark.parametrize("change", ["scope", "role_scope", "source", "inferred", "target", "projection", "duplicate_year", "unit", "unknown_source"])
def test_incomparable_training_series_does_not_create_a_trend(change):
    old = node("old", 90, year=2023)
    new = node("new", 80)
    nodes = [old, new]
    if change == "scope":
        new.raw_text = "국내 사업장 환경교육 이수율=80% (report.pdf)"
    elif change == "role_scope":
        new.value_role = "component"
    elif change == "source":
        new.source_file = "different_report.pdf"
    elif change == "inferred":
        new.period_inferred = True
    elif change == "target":
        new.value_role = "target"
    elif change == "projection":
        new.metric += "__projection"
    elif change == "duplicate_year":
        nodes.append(node("conflicting", 99))
    elif change == "unit":
        new.unit = "%p"
    else:
        old.source_file = new.source_file = None
    assert source_facts(output(*nodes)) == []


def test_identical_duplicate_extraction_does_not_invent_a_conflict():
    result = output(node("old", 90, year=2023), node("new", 80), node("duplicate", 80))
    assert len(source_facts(result)) == 1


def test_explicit_year_suffixes_in_unmapped_education_labels_do_not_split_one_series():
    label = "환경교육 이수 비율 국내(별도)"
    result = output(*[
        node(str(year), value, year=year, hint=f"{label} {year}년", code=f"{label} {year}년", role="component")
        for year, value in [(2022, 70), (2023, 55), (2024, 45.1)]
    ])
    before = deepcopy(result.evidence_graph.to_dict())

    findings = source_facts(result)

    assert len(findings) == 1
    assert findings[0].fact == f"{label}: 2023년 55% → 2024년 45.1%"
    assert result.evidence_graph.to_dict() == before


def test_year_suffix_is_not_removed_when_it_disagrees_with_the_recorded_period():
    result = output(node("old", 90, year=2023, hint="교육 이수율 2023년"),
                    node("new", 80, year=2024, hint="교육 이수율 2023년"))
    assert source_facts(result) == []


def test_year_suffix_normalization_keeps_geographic_scope_separate():
    result = output(node("old", 90, year=2023, hint="교육 이수율 국내 2023년"),
                    node("new", 80, year=2024, hint="교육 이수율 해외 2024년"))
    assert source_facts(result) == []


@pytest.mark.parametrize("before,after,expected", [(2, 1, False), (1, 1, False), (1, 2, True)])
def test_injury_count_only_increases_are_reviewed(before, after, expected):
    result = output(node("old", before, year=2023, hint="산업재해 건수", code="S-4-2", unit="건"),
                    node("new", after, hint="산업재해 건수", code="S-4-2", unit="건"))
    assert bool(source_facts(result)) is expected


@pytest.mark.parametrize("hint", ["무재해 달성 건수", "산업재해 예방 활동 건수", "산업재해 교육 실시 건수"])
def test_prevention_activity_is_not_reported_as_an_injury_increase(hint):
    result = output(node("old", 1, year=2023, hint=hint, code="S-4-2", unit="건"),
                    node("new", 2, hint=hint, code="S-4-2", unit="건"))
    assert source_facts(result) == []


def test_ocr_quote_page_and_page_provenance_survive_graph_merge():
    graph = EvidenceGraph("TEST", "테스트")
    quote = "2024 환경 법규 위반 1건"
    ext = OcrExtraction(source_file="source.pdf", channel=DocChannel.UNSTRUCTURED, doc_type="esg_report",
        metrics=[ExtractedMetric("환경 법규 위반 건수", 1, "건", "2024", "E-8-1",
                                 page=52, confidence=1, quote=quote, page_source="chunk")],
        clauses=[ExtractedClause("환경 교육", "교육 체계를 운영합니다.", "E-1-2", page=7,
                                 quote="교육 체계를 운영합니다.", page_source="quote")])
    merge_ocr_extraction(graph, ext, report_year=2024)
    metric = next(iter(graph.nodes.values()))
    clause = next(iter(graph.text_nodes.values()))
    assert (metric.quote, metric.page, metric.page_source) == (quote, 52, "chunk")
    assert (clause.quote, clause.page, clause.page_source) == ("교육 체계를 운영합니다.", 7, "quote")
    result = with_ledger(metric)
    finding = source_facts(result)[0]
    assert finding.evidence[0].quote == quote and finding.evidence[0].page == 52
    md = review_markdown([finding])
    assert "53쪽" in md and f"> {quote}" in md


def test_extracted_summary_is_never_presented_as_an_original_quote():
    result = with_ledger(node("violation", 1, hint="환경 법규 위반 건수", code="E-8-1", unit="건"))
    md = review_markdown(source_facts(result))
    assert "원문 인용 미확인" in md and "페이지 미확인" in md
    assert not any(line.startswith("> ") for line in md.splitlines())


def test_quote_markup_is_escaped_without_modifying_the_stored_quote():
    quote = "<script>alert(1)</script>\n## [링크](https://example.com)"
    result = with_ledger(node("violation", 1, hint="환경 법규 위반 건수", code="E-8-1", unit="건", quote=quote))
    finding = source_facts(result)[0]
    md = review_markdown([finding])
    assert finding.evidence[0].quote == quote
    assert "<script>" not in md and "\n## [링크]" not in md


def failed_extract(filename, **metadata):
    return OcrExtraction(filename, DocChannel.UNSTRUCTURED, "esg_report", router_meta=metadata)


def test_same_failure_in_two_files_keeps_both_sources():
    result = output(extracts=[failed_extract(name, extraction_status="failed") for name in ("a.pdf", "b.pdf")])
    findings = build_source_review(result)
    assert {ref.source_file for finding in findings for ref in finding.evidence} == {"a.pdf", "b.pdf"}


def test_chunk_failure_retains_specific_reason_and_unknown_page():
    result = output(extracts=[failed_extract("failed.pdf", extraction_status="partial",
        chunk_failures=[{"page": None, "reason": "invalid_json", "detail": "응답 JSON 해석 실패"}])])
    md = review_markdown(build_source_review(result))
    assert "응답 JSON 해석 실패" in md and "페이지 미확인" in md


def test_prechunk_failure_retains_its_specific_detail():
    result = output(extracts=[failed_extract("failed.pdf", extraction_status="failed",
        failure_reason="chunking_failed", failure_detail="한 행이 허용 길이를 초과했습니다")])
    assert "한 행이 허용 길이를 초과했습니다" in review_markdown(build_source_review(result))


@pytest.mark.parametrize("metadata", [{"mock": True}, {"extraction_status": "mock"},
                                      {"mock": True, "extraction_status": "partial"}])
def test_mock_values_do_not_create_violation_or_trend_facts(metadata):
    result = with_ledger(node("violation", 2, hint="환경 법규 위반 건수", code="E-8-1", unit="건"),
                         node("old", 90, year=2023), node("new", 80))
    result.ocr_extractions = [failed_extract("report.pdf", **metadata)]
    findings = build_source_review(result)
    assert not [finding for finding in findings if finding.category == "source_fact"]
    assert any("시연" in finding.fact for finding in findings)


def test_partial_extraction_keeps_valid_observations_and_completion_warning():
    result = with_ledger(node("violation", 1, hint="환경 법규 위반 건수", code="E-8-1", unit="건"))
    result.ocr_extractions = [failed_extract("report.pdf", extraction_status="partial")]
    findings = build_source_review(result)
    assert any(finding.category == "source_fact" for finding in findings)
    assert any(finding.category == "extraction" for finding in findings)


@pytest.mark.parametrize("flag", ["unit_suspect", "unit_mismatch", "partial_value", "period_inferred", "no_representative_node"])
def test_actual_ledger_quality_flags_are_explained(flag):
    result = output()
    result.extraction.confidence_flags = {"E-4-1": [flag]}
    findings = build_source_review(result)
    assert len(findings) == 1 and findings[0].category == "data_quality"


def test_free_target_exclusion_is_not_a_ledger_quality_problem_and_original_flags_are_kept():
    result = with_ledger(node("target", 25, hint="TSR 목표", code="TSR 목표", role="target"))
    assert result.evidence_graph.resolved_facts["TSR 목표"] is None
    assert "no_representative_node" in result.extraction.confidence_flags["TSR 목표"]
    result.extraction.confidence_flags["Non-captive 매출 목표"] = ["no_representative_node"]
    before = deepcopy((result.evidence_graph.to_dict(), asdict(result.extraction)))

    findings = build_source_review(result)

    assert not [finding for finding in findings if finding.category == "data_quality"]
    assert (result.evidence_graph.to_dict(), asdict(result.extraction)) == before


def test_quality_warnings_follow_profile_and_actual_beyond_profile_ledger_entries():
    from esgenie.knowledge.kesg_items import ALL_ITEMS, items_for_profile

    profile_codes = {item.code for item in items_for_profile("sme")}
    outside = next(item.code for item in ALL_ITEMS if item.code not in profile_codes)
    result = output()
    result.extraction.profile = "sme"
    result.extraction.confidence_flags[outside] = ["period_inferred"]
    assert build_source_review(result) == []
    result.extraction.mapped[outside] = {}  # 실제 원장에 실린 추가 공시에는 경고를 보존한다.
    assert [finding.code for finding in build_source_review(result)] == [outside]


def test_known_missing_ledger_value_keeps_warning_when_a_legacy_profile_is_absent():
    result = output()
    result.extraction.mapped = {"E-4-1": {}}
    result.extraction.missing = ["E-8-1"]
    result.extraction.confidence_flags = {"E-8-1": ["no_representative_node"],
                                          "G-3-4": ["period_inferred"]}
    assert [finding.code for finding in build_source_review(result)] == ["E-8-1"]


@pytest.mark.parametrize("flag", ["partial_aggregate", "unit_suspect"])
def test_resolved_fact_flags_and_sources_do_not_depend_on_extraction_flags(flag):
    result = with_ledger(node("energy", 100, code="E-4-1", hint="에너지 사용량 합계", unit="TJ"))
    graph = result.evidence_graph
    graph.resolved_facts["E-4-1"] = replace(graph.resolved_facts["E-4-1"], flags=[flag])
    result.extraction = None
    findings = build_source_review(result)
    assert len(findings) == 1 and findings[0].category == "data_quality"
    assert [ref.node_id for ref in findings[0].evidence] == ["energy"]


def verification(claims, *, aggregate_claims=None, grounding=None):
    rv = RiskVector(AxisScore(.7, evaluation={"claims": claims}), AxisScore(.2), AxisScore(.3), AxisScore(0),
                    aggregate={"risk_score": .31})
    if aggregate_claims is not None:
        rv.aggregate["numeric_evaluation"] = {"claims": aggregate_claims}
    step = SimpleNamespace(grounding=grounding, detection=SimpleNamespace(risk_vector=rv, risk_score=31))
    return SimpleNamespace(final=step)


def claim(reason, raw="200 TJ", **kwargs):
    return {"reason": reason, "raw": raw, "code": "E-4-1",
            "status": "compared" if reason in {"match", "mismatch"} else "unverified",
            "claim_value": 200, "claim_unit": "TJ", "evidence_value": 100,
            "evidence_unit": "TJ", "evidence_ids": [], **kwargs}


def test_numeric_review_uses_all_sentence_records():
    first = claim("mismatch", "200 TJ")
    later = claim("no_evidence", "77 톤", code="E-5-1", evidence_value=None)
    result = output(sections={"E": verification([first], aggregate_claims=[first, later])})
    facts = [finding.fact for finding in build_source_review(result)]
    assert any("200" in fact for fact in facts) and any("77" in fact for fact in facts)


def test_generated_numeric_failure_reasons_are_distinct_and_specific():
    reasons = ["mismatch", "no_evidence", "unit_mismatch", "ambiguous_topic", "invalid_number"]
    result = output(sections={"E": verification([claim(reason, raw=f"{i} TJ") for i, reason in enumerate(reasons)])})
    findings = build_source_review(result)
    assert len(findings) == len(reasons)
    assert len({finding.reason for finding in findings}) == len(reasons)
    assert {finding.check_reason for finding in findings} == set(reasons)


def test_numeric_mismatch_explains_both_compared_values():
    result = output(sections={"E": verification([claim("mismatch")])})
    md = review_markdown(build_source_review(result))
    assert "200" in md and "100" in md and "TJ" in md


def test_matching_and_target_claims_are_not_reported_as_issues():
    result = output(sections={"E": verification([
        claim("match"), claim("target", status="excluded"),
    ])})
    assert build_source_review(result) == []


def test_grounding_failure_preserves_the_actual_uncited_sentence():
    sentence = "당사의 모든 사업장은 완전한 탄소중립을 실현했다."
    grounding = GroundingResult("REJECT", [sentence], [], [], False, ["uncited"], [], .1)
    result = output(sections={"E": verification([], grounding=grounding)})
    assert any(finding.fact == sentence for finding in build_source_review(result))


def test_same_uncited_sentence_keeps_each_affected_area():
    grounding = GroundingResult("REJECT", ["당사는 모든 목표를 달성했다."], [], [], False, ["uncited"], [], .1)
    result = output(sections={area: verification([], grounding=grounding) for area in ("E", "S")})
    assert {finding.area for finding in build_source_review(result)} == {"E", "S"}


@pytest.mark.parametrize("axis_name", ["D2_modifier", "D5_timeseries"])
def test_existing_modifier_or_timeseries_failures_are_explained_even_with_grounding_accept(axis_name):
    result = output(node("old_E-4-1", 100, hint="에너지 사용량", code="E-4-1", unit="TJ", year=2023),
                    node("new_E-4-1", 90, hint="에너지 사용량", code="E-4-1", unit="TJ"))
    result.evidence_graph.add_edge(EvidenceEdge("old_E-4-1", "new_E-4-1", "timeseries", yoy=-10))
    axis = (score_d2_modifier("혁신적인 친환경 기업입니다.") if axis_name == "D2_modifier"
            else score_d5_timeseries("에너지 사용량은 90 TJ로 증가했다.", result.evidence_graph))
    assert axis.score == 1
    axes = {name: AxisScore(0) for name in ("D1_numeric", "D2_modifier", "D3_semantic", "D5_timeseries")}
    axes[axis_name] = axis
    rv = _build_risk_vector(*axes.values())
    result.sections = {"E": SimpleNamespace(final=SimpleNamespace(
        detection=SimpleNamespace(risk_vector=rv),
        grounding=GroundingResult("ACCEPT", [], [], [], False, [], [], 1.0)))}

    findings = build_source_review(result)

    assert len(findings) == 1 and findings[0].check_reason == axis_name
    assert findings[0].check_result == axis.to_dict()
    assert axis.detail in findings[0].reason
    assert "대표 벡터만" in findings[0].reason and "대상 문장 위치를 특정할 수 없습니다" in findings[0].reason
    if axis_name == "D5_timeseries":
        assert {ref.node_id for ref in findings[0].evidence} == {"old_E-4-1", "new_E-4-1"}


def test_sentence_axis_records_preserve_other_sentences_without_changing_representative_score(monkeypatch):
    from esgenie import layer4_verify
    from esgenie.embeddings import IndexedDoc
    from esgenie.layer2_rag import RAGContext

    first_sentence, second_sentence = "혁신적인 친환경 기업입니다.", "에너지 사용량은 90 TJ로 증가했다."
    text = first_sentence + "\n" + second_sentence
    first = _build_risk_vector(AxisScore(0), score_d2_modifier(first_sentence), AxisScore(.1), AxisScore(0))
    second = _build_risk_vector(AxisScore(0), AxisScore(0), AxisScore(0),
                                AxisScore(1, detail="E-4-1 문장방향=증가 vs YoY=-10.0%"))
    assert first.risk_score > second.risk_score
    baseline = deepcopy(first.to_dict())
    sequence = iter([first, second])
    passed_chunks = []
    def detect(sentence, **kwargs):
        passed_chunks.append(kwargs["retrieved_chunks"])
        return next(sequence)
    monkeypatch.setattr(layer4_verify, "detect_risk_vector", detect)
    context = RAGContext([], [], [(IndexedDoc("실적 근거", {}, "actual_source_id"), 1)])
    generation = SimpleNamespace(context=context)

    rv = layer4_verify._compute_text_risk_vector(text, None, generation, None)

    assert rv is first
    for name in ("D1_numeric", "D2_modifier", "D3_semantic", "D5_timeseries"):
        assert getattr(rv, name).to_dict() == baseline[name]
    assert {key: value for key, value in rv.aggregate.items() if key != "sentence_axis_reviews"} == baseline["aggregate"]
    records = rv.aggregate["sentence_axis_reviews"]
    assert [row["sentence"] for row in records] == [first_sentence, second_sentence]
    assert all(text[row["start"]:row["end"]] == row["sentence"] for row in records)
    assert all(chunks[0]["id"] == "actual_source_id" for chunks in passed_chunks)
    result = output(sections={"E": SimpleNamespace(final=SimpleNamespace(
        detection=SimpleNamespace(risk_vector=rv), generation=generation, grounding=None))})
    findings = build_source_review(result)
    assert [(finding.check_reason, finding.fact) for finding in findings] == [
        ("D2_modifier", first_sentence), ("D5_timeseries", second_sentence)]
    assert all("구버전" not in finding.reason for finding in findings)
    json.dumps([finding.to_dict() for finding in findings], ensure_ascii=False)


def test_semantic_axis_preserves_real_evidence_and_abstention_without_relabeling_it_as_a_failure():
    from esgenie.embeddings import IndexedDoc
    from esgenie.layer2_rag import RAGContext

    result = output(node("source", 100, hint="에너지 사용량", code="E-4-1", unit="TJ", page=2, quote="에너지 사용량 100 TJ"))
    axis = AxisScore(.8, evidence=["actual_chunk"], detail="최고 cos-sim=0.100")
    rv = _build_risk_vector(AxisScore(0), AxisScore(0), axis, AxisScore(0))
    context = RAGContext([], [], [(IndexedDoc("에너지 사용량 100 TJ", {"node_id": "source"}, "actual_chunk"), 1)])
    rv.aggregate["sentence_axis_reviews"] = [
        {"sentence": "문장 A", "high_axes": ["D3_semantic"], "axes": {"D3_semantic": axis.to_dict()}},
        {"sentence": "문장 B", "high_axes": [], "axes": {"D3_semantic": AxisScore(
            0, detail="검색 근거 없음", abstain=True, abstain_reason="no_evidence").to_dict()}},
    ]
    result.sections = {"E": SimpleNamespace(final=SimpleNamespace(
        detection=SimpleNamespace(risk_vector=rv), generation=SimpleNamespace(context=context), grounding=None))}

    findings = build_source_review(result)

    assert len(findings) == 2
    assert findings[0].check_result["evidence"] == ["actual_chunk"]
    assert findings[0].evidence[0].node_id == "source" and findings[0].evidence[0].page == 2
    assert "판단을 보류" in findings[1].reason
    assert findings[1].check_result["abstain"] is True


def test_low_axis_scores_and_explicit_empty_sentence_records_do_not_create_new_issues():
    result = output(sections={"E": verification([])})
    assert build_source_review(result) == []
    rv = result.sections["E"].final.detection.risk_vector
    rv.D2_modifier = AxisScore(1, detail="사용하지 않을 대표 기록")
    rv.aggregate["sentence_axis_reviews"] = []
    assert build_source_review(result) == []


def test_legacy_section_without_final_verification_is_not_a_new_problem():
    result = output(sections={"E": SimpleNamespace(final_text="구버전 결과")})
    assert build_source_review(result) == []


def test_retrieval_success_alone_is_not_an_issue():
    result = output()
    result.item_retrievals = [{"item_code": "E-4-1", "accepted_chunk_ids": ["chunk1"], "hits": []}]
    assert build_source_review(result) == []


def test_retrieval_failure_is_not_proof_of_nondisclosure():
    result = output()
    result.item_retrievals = [{"item_code": "E-4-1", "accepted_chunk_ids": [], "hits": []}]
    finding = build_source_review(result)[0]
    assert finding.category == "retrieval"
    assert "미공시라는 확정 판정은 아닙니다" in finding.reason


def test_only_accepted_retrieval_evidence_is_attached_to_existing_findings():
    result = with_ledger(node("violation", 1, hint="환경 법규 위반 건수", code="E-8-1", unit="건"))
    for nid in ("accepted", "rejected"):
        result.evidence_graph.add_text_node(TextNode(
            nid, "시정 조치", "요약된 시정 절차", "E-8-1", f"{nid}.pdf",
            page=2, quote="담당 부서가 시정 조치를 확인한다.", page_source="quote"))
    result.item_retrievals = [{
        "item_code": "E-8-1", "accepted_chunk_ids": ["accepted-hit"],
        "hits": [{"id": f"{nid}-hit", "meta": {"node_id": nid}} for nid in ("accepted", "rejected")],
    }]
    findings = source_facts(result)
    assert len(findings) == 1
    assert {ref.node_id for ref in findings[0].evidence} == {"violation", "accepted"}
    # 추가 검색이 붙인 자료는 사건의 직접 증거가 아니라 주변 설명으로만 표시한다.
    roles = {ref.node_id: ref.role for ref in findings[0].evidence}
    assert roles == {"violation": "direct", "accepted": "context"}
    md = review_markdown(findings)
    assert "**출처:** report\\.pdf" in md
    assert "**참고(주변 설명):** accepted\\.pdf" in md


def test_context_role_survives_json_and_report_outputs():
    """화면·Markdown·JSON·보고서가 같은 역할 구분을 쓴다."""
    result = with_ledger(node("violation", 1, hint="환경 법규 위반 건수", code="E-8-1", unit="건"))
    result.evidence_graph.add_text_node(TextNode(
        "policy", "환경 법규 준수", "환경 법규 준수 모니터링 체계를 운영한다.", "E-8-1",
        "report.pdf", page=140, quote="환경 법규 준수 모니터링 체계를 운영한다.", page_source="quote"))
    result.item_retrievals = [{"item_code": "E-8-1", "accepted_chunk_ids": ["policy-hit"],
                              "hits": [{"id": "policy-hit", "meta": {"node_id": "policy"}}]}]
    result.review_findings = build_source_review(result)
    finding = next(f for f in result.review_findings if f.category == "source_fact")
    assert [ref["role"] for ref in finding.to_dict()["evidence"]] == ["direct", "context"]
    assert _block_source_review(result).body_md == review_markdown(result.review_findings)


def test_policy_text_and_incident_narrative_get_different_roles():
    """정책 문구는 사건의 직접 증거로 표시하지 않고, 실제 경위 문장만 직접 근거로 둔다."""
    result = with_ledger(node("violation", 1, hint="환경 법규 위반 건수", code="E-8-1", unit="건"))
    texts = {
        "policy": "환경 법규를 정기적으로 검토하며 모니터링 체계를 운영합니다.",
        "narrative": "2024년 기준 국내(별도)에서 1건의 규제 위반이 있었으며 자회사는 0건입니다.",
    }
    for nid, text in texts.items():
        result.evidence_graph.add_text_node(TextNode(nid, "환경 법규", text, "E-8-1", "report.pdf"))
    result.item_retrievals = [{"item_code": "E-8-1", "accepted_chunk_ids": list(texts),
        "hits": [{"id": nid, "meta": {"node_id": nid}} for nid in texts]}]
    finding = source_facts(result)[0]
    assert {ref.node_id: ref.role for ref in finding.evidence} == {
        "violation": "direct", "policy": "context", "narrative": "direct"}
    md = review_markdown([finding])
    assert "**참고(주변 설명):**" in md and "**출처:**" in md


def test_unreported_values_are_listed_without_claiming_nondisclosure():
    """라벨만 읽힌 행을 조용히 넘기지 않되, 0으로 채우거나 미공시로 단정하지 않는다."""
    result = output(extracts=[failed_extract(
        "report.pdf", extraction_status="complete", unvalued_record_count=7,
        unvalued_records=[{"chunk_index": 16, "page": 15, "records": [
            {"record_type": "metric", "record_index": i, "reason": "value_not_reported",
             "fatal": False, "metric_hint": hint}
            for i, hint in enumerate(["젠더 다양성(여성 비율)", "국적 다양성", "연령 분포",
                                      "네번째", "다섯번째", "여섯번째", "일곱번째"])]}])])
    findings = build_source_review(result)
    assert [f.category for f in findings] == ["extraction"]
    finding = findings[0]
    assert finding.check_reason == "value_not_reported"
    assert "7건" in finding.fact
    assert "젠더 다양성(여성 비율)" in finding.fact and "등" in finding.fact
    assert "일곱번째" not in finding.fact  # 화면이 길어지지 않게 앞 5개만 보여 준다
    assert "미공시라는 확정 판정은 아닙니다" in finding.reason
    assert [ref.source_file for ref in finding.evidence] == ["report.pdf"]


def test_complete_extraction_without_unreported_values_adds_no_notice():
    result = output(extracts=[failed_extract("report.pdf", extraction_status="complete",
                                            unvalued_records=[], unvalued_record_count=0)])
    assert build_source_review(result) == []


def test_mock_retrieval_hit_is_not_attached_as_real_support():
    result = with_ledger(node("violation", 1, hint="환경 법규 위반 건수", code="E-8-1", unit="건"))
    result.evidence_graph.add_text_node(TextNode("mock-note", "조치", "시연용 조치", "E-8-1", "mock.pdf"))
    result.ocr_extractions = [failed_extract("mock.pdf", extraction_status="mock")]
    result.item_retrievals = [{"item_code": "E-8-1", "accepted_chunk_ids": ["mock-hit"],
        "hits": [{"id": "mock-hit", "meta": {"node_id": "mock-note"}}]}]
    assert all(ref.node_id != "mock-note" for finding in source_facts(result) for ref in finding.evidence)


def test_review_build_is_deterministic_and_does_not_change_scores_or_selection():
    result = with_ledger(node("energy", 100, code="E-4-1", hint="에너지 사용량 합계", unit="TJ"))
    axis = score_d1_numeric("에너지 사용량은 200 TJ입니다.", result.evidence_graph)
    result.sections = {"E": verification(axis.evaluation["claims"])}
    rv = result.sections["E"].final.detection.risk_vector
    before = deepcopy((result.evidence_graph.to_dict(), asdict(result.extraction), rv.to_dict()))
    one = build_source_review(result)
    two = build_source_review(result)
    assert [finding.to_dict() for finding in one] == [finding.to_dict() for finding in two]
    assert (result.evidence_graph.to_dict(), asdict(result.extraction), rv.to_dict()) == before


def test_ui_and_report_use_the_same_review_renderer(monkeypatch):
    import esgenie.ui.tabs as tabs

    result = with_ledger(node("violation", 1, hint="환경 법규 위반 건수", code="E-8-1", unit="건"))
    result.review_findings = build_source_review(result)
    result.policy_drafts = []
    expected = review_markdown(result.review_findings)
    fake_st = MagicMock()
    fake_st.columns.side_effect = lambda spec: [MagicMock() for _ in spec]
    monkeypatch.setattr(tabs, "st", fake_st)
    for name in ("render_section_header", "_render_esg_coverage_strip", "render_stat_row",
                 "render_report_card", "render_diag_tab", "render_policy_tab"):
        monkeypatch.setattr(tabs, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(tabs, "_result_status_meta", lambda *args: [])
    tabs.render_diagnosis_workspace(result, "E")
    assert expected in [call.args[0] for call in fake_st.markdown.call_args_list if call.args]
    assert _block_source_review(result).body_md == expected


def test_orphan_number_and_unit_mismatch_are_reported_as_different_reasons():
    """G2(근거에서 못 찾은 숫자)와 G4(단위 불일치)는 사유가 다르다.

    합쳐서 '일치하지 않습니다'로 단정하면 값이 맞는 숫자까지 불일치로 보고된다
    (실측: 본문의 산업 평균 비교 수치가 불일치로 표기됨).
    """
    grounding = GroundingResult("REJECT", [], ["11.0"], ["9,075 TJ vs 9,075 GJ"],
                                False, ["orphan_number"], [], .4)
    result = output(sections={"E": verification([], grounding=grounding)})
    findings = build_source_review(result)
    by_fact = {finding.fact: finding for finding in findings}

    assert "11.0" in by_fact and "9,075 TJ vs 9,075 GJ" in by_fact
    orphan, unit = by_fact["11.0"], by_fact["9,075 TJ vs 9,075 GJ"]
    assert orphan.reason != unit.reason
    # 숫자를 못 찾은 것을 틀렸다고 단정하지 않는다.
    assert "찾지 못했습니다" in orphan.reason
    assert "판정은 아닙니다" in orphan.reason
    assert "단위" in unit.reason
