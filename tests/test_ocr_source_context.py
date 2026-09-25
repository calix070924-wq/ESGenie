"""OCR 표 문맥·출처·실패 상태 회귀. 회사별 숫자에 의존하지 않는 로컬 테스트."""
import json

import pytest

from esgenie.config import SETTINGS
from esgenie.llm import LLMResponse, LLMUnavailableError
from esgenie.ssot import ocr_router as o


def _row(*texts):
    return [(float(i * 100), text) for i, text in enumerate(texts)]


def _line(rows, index=-1):
    return " | ".join(t for _x, t in rows[index])


def test_adjacent_merged_labels_use_nearest_name_without_changing_values():
    rows = [
        [(100, "TJ"), (200, "11")], [(0, "직접 생산량")],
        [(100, "MWh"), (200, "12")],
        [(100, "TJ"), (200, "0")], [(0, "비재생 생산량")],
        [(100, "MWh"), (200, "0")],
        [(100, "TJ"), (200, "11")], [(0, "재생 생산량"), (80, "1)")],
        [(100, "MWh"), (200, "12")],
    ]
    result = o._inherit_label_rows(rows)
    for index, name in [(0, "직접 생산량"), (3, "비재생 생산량"), (6, "재생 생산량")]:
        assert result[index][0][1] == f"[{name}]"
        assert result[index][1:] == rows[index]


def test_equally_close_different_labels_are_not_guessed():
    rows = [[(0, "사용량")], [(100, "TJ"), (200, "7")], [(0, "생산량")]]
    assert o._inherit_label_rows(rows) == rows


def test_label_right_of_unit_is_not_inherited():
    rows = [[(100, "TJ"), (200, "7")], [(300, "표 설명")]]
    assert o._inherit_label_rows(rows) == rows


def test_mixed_actual_and_target_years_keep_target_qualifier():
    rows = [_row("2023", "2024", "2035 목표"), _row("연결", "연결", "연결"),
            _row("4.5", "8.2", "90")]
    line = _line(o._attach_column_headers(rows))
    assert line == "4.5(연결|2023) | 8.2(연결|2024) | 90(연결|2035 목표)"
    metrics, _ = o._map_vlm_json({"metrics": [{"metric_hint": "전환율(연결|2035 목표)",
                                               "value": 90, "unit": "%", "period": ""}]})
    assert metrics[0].period == "2035"
    assert "목표" in metrics[0].metric_hint


def test_target_only_year_header_is_not_lost():
    rows = [_row("2030 목표", "2040 목표"), _row("합계", "합계"), _row("30", "100")]
    assert "30(합계|2030 목표)" in _line(o._attach_column_headers(rows))


def test_pages_never_mix_even_when_small_and_page_zero_is_preserved():
    chunks = o._unstructured_chunks("first\nsecond", [(0, "first"), (3, "second")])
    assert [(c["page"], c["body"]) for c in chunks] == [(0, "first"), (3, "second")]


def test_long_page_repeats_only_header_and_preserves_all_data_rows(monkeypatch):
    monkeypatch.setattr(o, "_UNSTRUCTURED_CHUNK_CHARS", 240)
    headers = ["2023 | 2024", "구분 | 단위", "연결 | 연결"]
    rows = [f"지표 {i} | TJ | {10000+i}(연결|2023) | {20000+i}(연결|2024)" for i in range(10)]
    text = "\n".join(headers + rows)
    chunks = o._unstructured_chunks(text, [(4, text)])
    assert len(chunks) > 1
    assert all(len(c["text"]) <= 240 for c in chunks)
    assert "\n".join(c["body"] for c in chunks) == text
    assert all(c["page"] == 4 for c in chunks)
    assert all(c["context_repeated"] for c in chunks[1:])
    assert all("2023 | 2024" in c["text"] for c in chunks)
    for row in rows:
        assert sum(c["text"].count(row) for c in chunks) == 1


def test_long_paragraph_is_bounded_without_splitting_numeric_token():
    text = "표 설명 " * 30 + "123,456.78 단위 사용."
    parts = o._split_text_chunks(text, 70)
    assert all(len(p) <= 70 for p in parts)
    assert "".join(parts) == text
    assert sum("123,456.78" in p for p in parts) == 1


def test_oversized_unbroken_number_fails_explicitly():
    with pytest.raises(ValueError, match="numeric token"):
        o._split_text_chunks("1" * 71, 70)


def test_long_line_preserves_negative_sign_at_split_boundary():
    parts = o._split_text_chunks("text-12345", 7)
    assert "".join(parts) == "text-12345"
    assert "-12345" in parts


def test_mapping_uses_actual_chunk_page_and_validates_quotes():
    text = "사용량 | TJ | 123\n정기 교육을 실시한다."
    data = {"metrics": [{"metric_hint": "사용량", "value": 123, "unit": "TJ",
                          "page": 99, "quote": "사용량 | TJ | 123"}],
            "clauses": [{"section": "교육", "text": "교육을 운영함", "page": 99,
                         "quote": "정기 교육을 실시한다."}]}
    metrics, clauses = o._map_vlm_json(data, page_no=0, source_text=text)
    assert metrics[0].page == clauses[0].page == 0
    assert metrics[0].page_source == clauses[0].page_source == "chunk"
    assert metrics[0].quote == "사용량 | TJ | 123"
    assert clauses[0].quote == "정기 교육을 실시한다."
    data["metrics"][0]["quote"] = "원문에 없는 수치"
    assert o._map_vlm_json(data, page_no=0, source_text=text)[0][0].quote == ""


def test_missing_or_nonfinite_metric_value_never_becomes_zero():
    issues = []
    metrics, _ = o._map_vlm_json({"metrics": [
        {"metric_hint": "사용량"}, {"metric_hint": "사용량", "value": "NaN"},
        {"metric_hint": "위반 건수", "value": 0},
    ]}, issues=issues)
    assert [(m.metric_hint, m.value) for m in metrics] == [("위반 건수", 0.0)]
    assert len(issues) == 2


def test_unknown_page_is_not_replaced_by_llm_claim_or_printed_page():
    _, clauses = o._map_vlm_json({"clauses": [{"text": "page 17", "page": 17}]},
                                source_text="page 17")
    assert clauses[0].page is None
    assert clauses[0].page_source == ""


def test_resolver_preserves_real_chunk_source_for_paraphrase(tmp_path):
    fitz = pytest.importorskip("fitz")
    path = tmp_path / "policy.pdf"
    with fitz.open() as doc:
        doc.new_page().insert_text((50, 50), "First page")
        doc.new_page().insert_text((50, 50), "Safety review every year")
        doc.save(path)
    ext = o.OcrExtraction(path.name, o.DocChannel.UNSTRUCTURED, "policy", clauses=[
        o.ExtractedClause("안전", "연례 안전 검토 제도를 운영함", page=1, page_source="chunk"),
        o.ExtractedClause("잘못된 주장", "Absent quote", page=1),
        o.ExtractedClause("인용", "요약", quote="Safety review every year"),
    ])
    o._resolve_clause_pages(ext, str(path))
    assert [c.page for c in ext.clauses] == [1, None, 1]
    assert ext.clauses[2].page_source == "quote"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(SETTINGS, "strict_llm", False)
    monkeypatch.setattr(SETTINGS, "force_mock", False)
    monkeypatch.setenv("ESGENIE_OCR_CACHE", "0")
    class Client:
        calls = []
        replies = []
        def complete(self, **kwargs):
            self.calls.append(kwargs)
            reply = self.replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
    monkeypatch.setattr("esgenie.llm.LLMClient", Client)
    return Client


def _reply(data=None, mock=False):
    return LLMResponse(content=json.dumps(data or {"metrics": [], "clauses": []}),
                       used_mock=mock, meta={"provider": "test-double", "model": "test-double"})


def test_text_prompt_and_metrics_provenance_reach_extraction(client):
    body = "Energy 123 TJ"
    client.replies = [_reply({"metrics": [{"metric_hint": "Energy", "value": 123,
                                           "unit": "TJ", "quote": body}], "clauses": []})]
    ext = o._extract_unstructured_text("x.pdf", doc_type="report", raw_text=body,
                                       page_texts=[(8, body)])
    assert ext.metrics[0].page == 8 and ext.metrics[0].quote == body
    assert ext.router_meta["extraction_status"] == "complete"
    assert "페이지 이미지" not in client.calls[0]["system"] + client.calls[0]["user"]
    assert "원본 페이지 인덱스(0부터): 8" in client.calls[0]["user"]


def test_partial_parse_failure_keeps_page_reason_and_successful_chunk(client):
    client.replies = [LLMResponse(content="not json", used_mock=False, meta={}),
                      _reply({"metrics": [], "clauses": [{"section": "x", "text": "good"}]})]
    ext = o._extract_unstructured_text("x.pdf", doc_type="report", raw_text="bad\ngood",
                                       page_texts=[(2, "bad"), (3, "good")])
    assert ext.router_meta["extraction_status"] == "partial"
    assert ext.router_meta["chunk_failures"][0]["page"] == 2
    assert ext.clauses[0].page == 3


def test_invalid_metric_records_are_reported_without_losing_valid_zero(client):
    client.replies = [_reply({"metrics": [{"metric_hint": "broken", "value": "N/A"},
                                         {"metric_hint": "위반 건수", "value": 0}], "clauses": []})]
    ext = o._extract_unstructured_text("x.pdf", doc_type="report", raw_text="위반 0건")
    assert [m.value for m in ext.metrics] == [0]
    assert ext.router_meta["extraction_status"] == "partial"
    assert ext.router_meta["chunk_failures"][0]["reason"] == "invalid_records"


def test_strict_mode_rejects_malformed_metric_record(client, monkeypatch):
    monkeypatch.setattr(SETTINGS, "strict_llm", True)
    client.replies = [_reply({"metrics": [{"value": 12, "unit": "TJ"}], "clauses": []})]
    with pytest.raises(LLMUnavailableError, match="invalid records"):
        o._extract_unstructured_text("x.pdf", doc_type="report", raw_text="source")


@pytest.mark.parametrize("record", [{"metric_hint": "여성 비율", "unit": "%"},
                                    {"metric_hint": "여성 비율", "value": None, "unit": "%"}])
def test_unreported_value_is_recorded_without_failing_the_document(client, monkeypatch, record):
    """라벨만 읽힌 행이 문서 전체를 실패시키지 않는다(strict 포함).

    실측 회귀: 현대모비스 2025 p.16의 '젠더 다양성(여성 비율)' 등 3건이 `value: null`로
    보고돼 176청크 전체가 strict에서 중단됐다. 수치를 0으로 채우지 않고 미확인으로
    남기는 기존 계약은 유지한다.
    """
    monkeypatch.setattr(SETTINGS, "strict_llm", True)
    client.replies = [_reply({"metrics": [record, {"metric_hint": "위반 건수", "value": 0}],
                              "clauses": []})]
    ext = o._extract_unstructured_text("x.pdf", doc_type="report", raw_text="위반 0건")
    assert [m.value for m in ext.metrics] == [0]  # 실제 0은 보존, 미확인은 주입하지 않는다
    assert ext.router_meta["extraction_status"] == "complete"
    assert not ext.router_meta["chunk_failures"]
    assert ext.router_meta["unvalued_record_count"] == 1
    assert ext.router_meta["unvalued_records"][0]["records"][0] == {
        "record_type": "metric", "record_index": 0,
        "reason": "value_not_reported", "fatal": False, "metric_hint": "여성 비율"}


@pytest.mark.parametrize("reply", [LLMResponse(content="bad", used_mock=False, meta={}),
                                    _reply(mock=True), RuntimeError("connection failed")])
def test_strict_mode_does_not_turn_chunk_failure_into_success(client, monkeypatch, reply):
    monkeypatch.setattr(SETTINGS, "strict_llm", True)
    client.replies = [reply]
    with pytest.raises(LLMUnavailableError):
        o._extract_unstructured_text("x.pdf", doc_type="report", raw_text="source")


def test_default_model_mock_fallback_is_failed_and_not_parsed_as_evidence(client):
    client.replies = [_reply({"metrics": [{"metric_hint": "fake", "value": 999}], "clauses": []}, mock=True)]
    ext = o._extract_unstructured_text("x.pdf", doc_type="report", raw_text="source")
    assert ext.metrics == []
    assert ext.router_meta["extraction_status"] == "failed"
    assert ext.router_meta["mock"] is True


def test_missing_key_raises_in_strict_mode(monkeypatch):
    monkeypatch.setattr(SETTINGS, "strict_llm", True)
    monkeypatch.setattr(o, "_get_openai_key", lambda: None)
    with pytest.raises(LLMUnavailableError, match="missing_api_key"):
        o.extract_unstructured("x.pdf", doc_type="report")


def test_scanned_tokens_require_real_page_metadata(client, monkeypatch):
    monkeypatch.setattr(o, "_get_openai_key", lambda: "test")
    monkeypatch.setattr(o, "_get_upstage_key", lambda: "test")
    monkeypatch.setattr(o, "_extract_pages_pymupdf", lambda *a, **k: ["", ""])
    monkeypatch.setattr(o, "_call_upstage_dp", lambda *a, **k: [{"text": "page 99 source", "page": None}])
    client.replies = [_reply({"metrics": [], "clauses": [{"text": "source", "page": 99}]})]
    ext = o.extract_unstructured("scan.pdf", doc_type="report")
    assert ext.clauses[0].page is None
    assert ext.router_meta["chunk_sources"][0]["page"] is None


def test_digital_empty_pages_keep_original_indices(client, monkeypatch):
    monkeypatch.setattr(o, "_get_openai_key", lambda: "test")
    monkeypatch.setattr(o, "_extract_pages_pymupdf", lambda *a, **k: ["", "body"])
    client.replies = [_reply({"metrics": [], "clauses": [{"text": "body"}]})]
    ext = o.extract_unstructured("x.pdf", doc_type="report")
    assert ext.clauses[0].page == 1


def test_quote_fields_survive_serialization():
    ext = o.OcrExtraction("x.pdf", o.DocChannel.UNSTRUCTURED, "report", metrics=[
        o.ExtractedMetric("Energy", 123, "TJ", "2024", page=2, quote="Energy 123 TJ", page_source="chunk")])
    assert o.OcrExtraction.from_dict(ext.to_dict()).to_dict() == ext.to_dict()
