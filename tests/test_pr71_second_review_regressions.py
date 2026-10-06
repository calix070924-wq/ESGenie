"""PR71 재검토(29defa0) A~D 회귀 — 수량과 그 대상·날짜·사업장·출처의 관계를 유지한다(2026-10-05).

A 원문 청크 인용·인용 없음 경로가 날짜 불일치를 같은 숫자로 덮었다.
B 인용 없는 표 행(`cited_texts=None`)·인용 없는 제목·요약이 수량 대조를 건너뛰었다.
C 해당 사업장에 적용되는 FY 정의가 없으면 다른 사업장의 정의로 되돌아갔다.
D 고용형태별 인원을 뒤바꿔도(`정규직 6명과 기간제 40명`) 숫자 집합이 같아 통과했다.

검토 원문의 숫자(40·6·46·50·27)를 정답으로 쓰지 않는다 — 작은 가상 사례(6/3 대상 30·참석 27 = 정규직 20 + 기간제 7,
6/9 추가 참석 3)로 같은 관계를 대조한다. 기대값은 원문 관계에서 정했고 제품 함수의 반환값으로 만들지 않았다.
네트워크·OCR·LLM 호출 없음.
"""
from __future__ import annotations

import socket
from types import SimpleNamespace as NS

import pytest

from esgenie.embeddings import IndexedDoc
from esgenie.layer6_report import annotate_generated_text, annotate_summary_text
from esgenie.ssot import ocr_router as router


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("회귀 검사 중 네트워크 호출 금지")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


FACTS = [
    {"label": "교육 대상 인원", "value": 30.0, "unit": "명", "period_text": "2026-06-03", "source_file": "a.pdf"},
    {"label": "교육 참석 인원", "value": 27.0, "unit": "명", "period_text": "2026-06-03", "source_file": "a.pdf"},
    {"label": "교육 참석 인원 · 정규직", "value": 20.0, "unit": "명", "period_text": "2026-06-03", "source_file": "a.pdf"},
    {"label": "교육 참석 인원 · 기간제", "value": 7.0, "unit": "명", "period_text": "2026-06-03", "source_file": "a.pdf"},
    {"label": "추가 교육 참석 인원", "value": 3.0, "unit": "명", "period_text": "2026-06-09", "source_file": "b.pdf"},
]
# 한 청크에 두 날짜·두 고용형태의 수량이 함께 있다 — 수량마다 자기 날짜·집단을 가져야 한다.
CHUNK = ("2026년 6월 3일 교육에 정규직 20명과 기간제 7명이 참석해 참석 인원은 27명이다. "
         "2026년 6월 9일 추가 교육에는 3명이 참석했다.")


def docs_for(chunks: dict[str, str] | None, facts: list | None):
    docs = [(IndexedDoc(text=t, meta={"source_file": "a.pdf"}, chunk_id=c), 1.0) for c, t in (chunks or {}).items()]
    if facts is not None:
        docs.append((IndexedDoc(text="facts", meta={"source": "source_facts", "facts": facts},
                                chunk_id="source_facts_S"), 1.0))
    return docs


def verify_of(text, docs):
    gen = NS(text=text, context=NS(all_hits=lambda: docs))
    return NS(final=NS(generation=gen, detection=NS(risk_vector=None)), final_text=text, final_score=0.0,
              final_band="LOW", iterations_used=0, hitl_required=False)


def review(text, chunks=None, facts=FACTS, evidence=None):
    docs = docs_for({"c1": CHUNK} if chunks is None else chunks, facts)
    args = (NS(extraction=NS(mapped={})), "S", verify_of(text, docs))
    # 채택 근거 기록을 볼 때만 넘긴다 — 같은 검사를 수정 전 코드(인자 없음)에도 그대로 돌려 수정 전 결과를 남긴다.
    return annotate_generated_text(*args) if evidence is None else annotate_generated_text(*args, evidence)


def replaced(marks):
    return [m for m in marks if m.get("action") == "replaced"]


# ── §8.1 본문 처리 공통 대조: 형식 3 × 인용 3 × 관계 4 = 36 ─────────────────────────────────

RELATIONS = {
    # 이름: (날짜, 정규직 값, 기간제 값, 확정 문장으로 남으면 안 되는 (집단, 값) 쌍)
    "correct": ("2026년 6월 3일", 20, 7, set()),
    "wrong_date": ("2026년 6월 9일", 20, 7, {("정규직", 20), ("기간제", 7)}),
    "swapped_groups": ("2026년 6월 3일", 7, 20, {("정규직", 7), ("기간제", 20)}),
    "orphan_quantity": ("2026년 6월 3일", 20, 15, {("기간제", 15)}),
}
CITATIONS = {"structured": " [source_facts_S]", "chunk": " [c1]", "none": ""}


def render(fmt, date, regular, fixed, cite):
    if fmt == "sentence":
        return f"{date} 교육에 정규직 {regular}명과 기간제 {fixed}명이 참석했다{cite}."
    if fmt == "heading":
        return f"### {date} 교육 참석: 정규직 {regular}명·기간제 {fixed}명{cite}"
    return f"| 구분 | 정규직 | 기간제 |\n|---|---|---|\n| {date} 교육 참석 | {regular}명 | {fixed}명 |{cite}"


def asserted_pairs(fmt, body):
    """본문이 확정 서술로 남긴 (집단, 값) — 보류 문구가 인용한 모델 수치·원문 확인 값 목록은 단정이 아니다."""
    import re
    pairs = set()
    for line in body.splitlines():
        if line.startswith("[확인 보류]"):
            continue
        if fmt == "table" and line.startswith("|") and "교육 참석" in line:
            cells = [c.strip() for c in line.strip("|").split("|")]
            for group, cell in zip(("정규직", "기간제"), cells[1:3]):
                m = re.fullmatch(r"(\d+)명", cell)
                if m:
                    pairs.add((group, int(m.group(1))))
            continue
        for m in re.finditer(r"(정규직|기간제)\s*(\d+)명", line):
            pairs.add((m.group(1), int(m.group(2))))
    return pairs


@pytest.mark.parametrize("relation", list(RELATIONS))
@pytest.mark.parametrize("citation", list(CITATIONS))
@pytest.mark.parametrize("fmt", ["sentence", "heading", "table"])
def test_body_title_table_with_every_citation_form(fmt, citation, relation):
    date, regular, fixed, wrong = RELATIONS[relation]
    body, marks = review(render(fmt, date, regular, fixed, CITATIONS[citation]))
    pairs = asserted_pairs(fmt, body)
    if relation == "correct":
        assert not replaced(marks) and {("정규직", 20), ("기간제", 7)} <= pairs, (body, marks)
        return
    assert replaced(marks), (body, marks)
    assert not (wrong & pairs), (body, pairs)
    if fmt == "sentence":
        assert body.startswith("[확인 보류]") and render(fmt, date, regular, fixed, "").rstrip(".") not in body
    if relation == "orphan_quantity" and fmt != "sentence":
        assert ("정규직", 20) in pairs, body          # 바꾼 칸·수량 옆의 맞는 값은 그대로 둔다
    if relation == "wrong_date" and fmt != "sentence":
        assert date in body                            # 날짜 문구는 남고 그 날짜의 수량만 보류한다


@pytest.mark.parametrize("relation", list(RELATIONS))
@pytest.mark.parametrize("citation", ["chunk", "none"])
@pytest.mark.parametrize("fmt", ["sentence", "heading", "table"])
def test_the_chunk_path_alone_keeps_dates_and_groups(fmt, citation, relation):
    # 구조화 사실 없이 원문 청크만 근거일 때도 같은 판정(원문 청크가 날짜·집단 관계를 갖는다).
    date, regular, fixed, wrong = RELATIONS[relation]
    body, marks = review(render(fmt, date, regular, fixed, CITATIONS[citation]), facts=None)
    pairs = asserted_pairs(fmt, body)
    if relation == "correct":
        assert not replaced(marks) and {("정규직", 20), ("기간제", 7)} <= pairs, (body, marks)
    else:
        assert replaced(marks) and not (wrong & pairs), (body, marks)


def summary_output(facts=FACTS):
    return NS(extraction=NS(mapped={}), sections={"S": verify_of("", docs_for({"c1": CHUNK}, facts))})


@pytest.mark.parametrize("relation", list(RELATIONS))
def test_the_summary_checks_the_same_four_relations(relation):
    date, regular, fixed, wrong = RELATIONS[relation]
    text = f"사회 영역에서는 {date} 교육에 정규직 {regular}명과 기간제 {fixed}명이 참석했다. 공시 보완이 필요하다."
    body, marks = annotate_summary_text(summary_output(), text)
    if relation == "correct":
        assert not replaced(marks) and body.startswith("사회 영역에서는"), (body, marks)
    else:
        assert replaced(marks) and body.startswith("[확인 보류]") and "공시 보완이 필요하다." in body
        assert not (wrong & asserted_pairs("sentence", body)), body


def test_the_summary_keeps_values_it_was_given():
    inputs = [("K-ESG 커버리지", 17.9, ("%",)), ("ISSB 누락 항목", 4, ("건", "개"))]
    text = "K-ESG 공시 커버리지는 17.9%이며 ISSB 연계 항목 중 4건이 누락되었다. 교육 참석 인원은 27명이다."
    body, marks = annotate_summary_text(summary_output(), text, inputs)
    assert body == text and not marks
    body, marks = annotate_summary_text(summary_output(), "K-ESG 공시 커버리지는 23.0%이다.", inputs)
    assert replaced(marks) and body.startswith("[확인 보류]")


# ── 인접 대조 ─────────────────────────────────────────────────────────────────

def test_a_chunk_with_two_dates_does_not_lend_one_dates_count_to_the_other():
    body, marks = review("2026년 6월 9일 교육에 27명이 참석했다 [c1].", facts=None)
    assert replaced(marks) and "27명이 참석했다" not in body
    assert "date" in replaced(marks)[0]["problems"]
    body, marks = review("2026년 6월 3일 교육에 27명이 참석했고, 6월 9일 추가 교육에 3명이 참석했다 [c1].", facts=None)
    assert not replaced(marks), (body, marks)


def test_a_comma_does_not_switch_off_the_date():
    body, marks = review("2026년 6월 9일 교육에서는, 27명이 참석했다.")
    assert replaced(marks), body


def test_a_matching_candidate_is_chosen_over_a_same_value_candidate_of_another_date():
    facts = FACTS + [{"label": "교육 참석 인원", "value": 27.0, "unit": "명", "period_text": "2026-06-10",
                      "source_file": "c.pdf"}]
    for date, adopted, rejected in (("2026년 6월 10일", "2026-06-10", "2026-06-03"),
                                    ("2026년 6월 3일", "2026-06-03", "2026-06-10")):
        evidence: list = []
        body, marks = review(f"{date} 교육에 27명이 참석했다 [source_facts_S].", chunks={}, facts=facts,
                             evidence=evidence)
        assert not marks and body.startswith(date)
        (record,) = [e for e in evidence if e["quantity"] == "27명"]
        assert adopted in record["adopted"] and any(rejected in r["evidence"] for r in record["rejected"])


def test_units_in_cells_or_column_headers_and_swapped_column_order():
    cases = [
        ("| 구분 | 정규직(명) | 기간제(명) |\n|---|---|---|\n| 2026년 6월 3일 참석 | 20 | 7 |", False),
        ("| 구분 | 정규직(명) | 기간제(명) |\n|---|---|---|\n| 2026년 6월 3일 참석 | 7 | 20 |", True),
        ("| 구분 | 기간제 | 정규직 |\n|---|---|---|\n| 2026년 6월 3일 참석 | 7명 | 20명 |", False),
        ("| 구분 | 기간제 | 정규직 |\n|---|---|---|\n| 2026년 6월 3일 참석 | 20명 | 7명 |", True),
        ("| 항목 | 실적(명) |\n|---|---|\n| 교육 참석 인원 | 999 |", True),
    ]
    for table, wrong in cases:
        body, marks = review(table)
        row = body.splitlines()[-1]
        assert bool(replaced(marks)) is wrong and ("[확인 보류]" in row) is wrong, (table, body)


def test_a_table_keeps_empty_cells_neighbouring_values_and_notes_in_place():
    table = ("| 항목 | 6월 3일 | 6월 9일 | 비고 |\n|---|---|---|---|\n"
             "| 추가 교육 참석 |  | 3명 | 원문 확인 |\n"
             "| 교육 참석 | 27명 | 15명 | 원문 대조 |")
    body, marks = review(table)
    rows = body.splitlines()
    assert rows[2] == "| 추가 교육 참석 |  | 3명 | 원문 확인 |", rows      # 빈 칸이 앞 열로 밀리지 않는다
    assert rows[3] == "| 교육 참석 | 27명 | [확인 보류] | 원문 대조 |", rows  # 틀린 칸만 바꾸고 옆 값·비고는 둔다
    assert len(replaced(marks)) == 1 and replaced(marks)[0]["numbers"] == ["15"]


def test_an_unknown_citation_is_not_made_valid_by_another_sources_same_number():
    # 찾지 못한 인용 + 다른 날짜의 같은 숫자뿐 → 보류.
    body, marks = review("2026년 6월 9일 교육에 27명이 참석했다 [c9].")
    assert replaced(marks) and replaced(marks)[0]["missing_citations"] == ["c9"]
    # 찾지 못한 인용 + 관계가 모두 맞는 원문 확인 수치 → 보존하되 실제 채택 출처를 감사 기록에 남긴다.
    evidence: list = []
    body, marks = review("2026년 6월 3일 교육에 27명이 참석했다 [c9].", chunks={}, evidence=evidence)
    assert not marks and body.startswith("2026년 6월 3일 교육에 27명이 참석했다")
    (record,) = evidence
    assert record["reason"] == "citation_not_supporting" and record["missing_citations"] == ["c9"]
    assert "a.pdf" in record["adopted"]


def test_a_citation_to_another_document_records_the_document_actually_used():
    evidence: list = []
    chunks = {"c1": CHUNK, "c2": "2026년 6월 9일 추가 교육에는 3명이 참석했다."}
    docs = docs_for(chunks, None)
    docs[1] = (IndexedDoc(text=chunks["c2"], meta={"source_file": "b.pdf"}, chunk_id="c2"), 1.0)
    text = "2026년 6월 3일 교육에 27명이 참석했다 [c2]."
    body, marks = annotate_generated_text(NS(extraction=NS(mapped={})), "S", verify_of(text, docs), evidence)
    assert not marks and body.startswith("2026년 6월 3일")
    (record,) = evidence
    assert record["reason"] == "citation_not_supporting" and record["adopted_chunk"] == "c1"


def test_the_system_table_is_identified_by_its_generation_path_not_by_its_title():
    system = "| 항목 | 값 | 기간 | 출처 |\n|---|---|---|---|\n| 교육 참석 인원 | 999명 | 2026-06-03 | a.pdf 1쪽 |"
    docs = docs_for({"c1": CHUNK}, FACTS)
    gen = NS(text=system, context=NS(all_hits=lambda: docs), system_tables=(system,))
    verify = NS(final=NS(generation=gen), final_text=system)
    body, marks = annotate_generated_text(NS(extraction=NS(mapped={})), "S", verify)
    assert body == system and not marks                       # 생성 경로가 넣은 표(가상의 999는 대조 대상이 아님)
    body, marks = review(system)                              # 같은 제목·열 이름의 LLM 표는 대조한다
    assert replaced(marks) and "999명" not in body.splitlines()[-1]


# ── D 고용형태 ────────────────────────────────────────────────────────────────

GROUP_RAW = "2026년 5월 12일 교육에 정규직 9명과 기간제 3명이 출석했다. 총 참석 12명."


@pytest.mark.parametrize("sentence,valid", [
    ("2026년 5월 12일 교육에 정규직 9명과 기간제 3명이 출석했다 [c1].", True),
    ("2026년 5월 12일 교육에 기간제 3명과 정규직 9명이 출석했다 [c1].", True),     # 순서만 바꾼 정상
    ("2026년 5월 12일 교육에 정규직 3명과 기간제 9명이 출석했다 [c1].", False),    # 수량 연결만 바꾼 오답
    ("2026년 5월 12일 교육에 정규직 12명이 출석했다 [c1].", False),                # 합계를 한 집단의 값으로
    ("2026년 5월 12일 교육에 9명이 출석했다 [c1].", False),                        # 한 집단의 값을 합계처럼
    ("2026년 5월 12일 교육에 총 12명이 출석했다 [c1].", True),
])
def test_employment_group_counts_keep_their_group(sentence, valid):
    body, marks = review(sentence, chunks={"c1": GROUP_RAW}, facts=None)
    assert (not replaced(marks)) is valid, (body, marks)


def test_the_same_count_in_two_groups_is_a_valid_control():
    raw = "2026년 5월 12일 교육에 정규직 5명과 파견 5명이 출석했다."
    body, marks = review("2026년 5월 12일 교육에 정규직 5명과 파견 5명이 출석했다.", chunks={"c1": raw}, facts=None)
    assert not marks, (body, marks)
    body, marks = review("2026년 5월 12일 교육에 기간제 5명과 파견 5명이 출석했다.", chunks={"c1": raw}, facts=None)
    assert replaced(marks)


def test_a_breakdown_that_the_source_does_not_state_is_held_and_the_totals_are_kept():
    facts = [{"label": "교육 대상 인원", "value": 12.0, "unit": "명", "period_text": "2026-05-12", "source_file": "a.pdf"},
             {"label": "교육 참석 인원", "value": 10.0, "unit": "명", "period_text": "2026-05-12", "source_file": "a.pdf"}]
    body, marks = review("2026년 5월 12일 교육 대상 12명 중 10명이 참석했다 [source_facts_S].", chunks={}, facts=facts)
    assert not marks
    body, marks = review("2026년 5월 12일 교육에 정규직 8명과 기간제 2명이 참석했다 [source_facts_S].", chunks={},
                         facts=facts)
    assert body.startswith("[확인 보류]") and "교육 참석 인원 10명(2026-05-12)" in body
    assert "교육 대상 인원 12명(2026-05-12)" in body


def test_group_words_are_read_from_the_word_that_modifies_the_count():
    from esgenie import report_claims as rc
    cases = [("정규직 40명과 기간제 6명이 출석", ["정규직", "기간제"]),
             ("기간제 근로자는 6명", ["기간제"]),
             ("정규직·기간제·파견 포함 50명", [""]),                 # 나열 + 다른 말 → 합계
             ("40명은 정규직이다", ["정규직"]),
             ("대상 50명(정규직 40명, 기간제 8명)", ["", "정규직", "기간제"]),   # 괄호 내역은 앞 수량의 집단이 아니다
             ("비정규직 4명", ["비정규직"]),
             ("계약직 3명", ["기간제"])]                              # 동의어
    for text, expected in cases:
        found = rc.quantities(text)
        assert [rc.count_group(w, a, b) for _q, w, a, b, _s, _e in rc.contexts(text, found)] == expected, text


# ── 사업장 ────────────────────────────────────────────────────────────────────

def test_a_count_of_one_site_is_not_another_sites_count():
    facts = [{"label": "교육 참석 인원", "value": 27.0, "unit": "명", "period_text": "2026-06-03", "site": "김해 제1공장",
              "source_file": "a.pdf"}]
    body, marks = review("2026년 6월 3일 김해 제1공장 교육에 27명이 참석했다 [source_facts_S].", chunks={}, facts=facts)
    assert not replaced(marks) and body.startswith("2026년 6월 3일 김해 제1공장 교육에 27명이 참석했다")
    body, marks = review("2026년 6월 3일 양산 제2공장 교육에 27명이 참석했다.", chunks={}, facts=facts)
    assert replaced(marks) and "site" in replaced(marks)[0]["problems"]


# ── 날짜 구간 위치(A의 전제) ────────────────────────────────────────────────────

def test_a_range_date_is_located_and_masked():
    from esgenie import report_claims as rc
    text = "2026년 4월 13일부터 19일까지 김해 제1공장 근로자 50명의 근로시간"
    assert [q.raw for q in rc.quantities(text)] == ["50명"]       # 날짜 숫자(2026·4·13·19)는 수량이 아니다
    found = rc.quantities(text)
    when = rc.date_near(text, found[0].start, found[0].end)
    assert (when.start.isoformat(), when.end.isoformat()) == ("2026-04-13", "2026-04-19")


def test_a_heading_line_lends_its_date_but_a_prose_sentence_does_not():
    from esgenie import report_claims as rc
    heading = "근로시간 기록: 김해 제1공장 / 2026-04-13 ~ 2026-04-19\n정규직·기간제·파견 포함 50명"
    (occ,) = rc.chunk_occurrences(heading)
    assert occ.when is not None and occ.when.start.isoformat() == "2026-04-13" and occ.group == ""
    prose = "4월 27일 추가 교육을 예정했습니다. 이번 회차 46명 출석만으로 완료를 확정하지 않습니다."
    (occ,) = rc.chunk_occurrences(prose)
    assert occ.when is None                                        # 앞 문장의 날짜를 빌리지 않는다


# ── 실제 소비 경로: 본문·제목·표·요약 → Markdown·PDF ─────────────────────────────

def test_wrong_claims_in_body_heading_table_and_summary_do_not_reach_markdown_or_pdf(tmp_path, monkeypatch):
    import fitz
    from esgenie import layer6_report as l6
    from esgenie.exporters.report_pdf import export_report_pdf
    raw = ("## 사회 성과\n\n### 2026년 6월 9일 교육 참석: 정규직 20명\n\n"
           "2026년 6월 3일 교육에 정규직 20명과 기간제 7명이 참석했다 [c1]. "
           "2026년 6월 9일 교육에 27명이 참석했다.\n\n"
           "| 구분 | 정규직 | 기간제 |\n|---|---|---|\n| 2026년 6월 3일 교육 참석 | 7명 | 20명 |\n")
    docs = docs_for({"c1": CHUNK}, FACTS)
    verify = verify_of(raw, docs)
    output = NS(extraction=NS(mapped={}, coverage_pct=0.0, corp_name="가상", profile_label="-"), report=None,
                sections={"S": verify}, disclosure=None, issb_gap=None)
    monkeypatch.setattr(l6, "_llm_or_fallback", lambda *a: "2026년 6월 3일 교육에 정규직 7명과 기간제 20명이 참석했다.")
    blocks = [l6._block_exec_summary(output), l6._block_esg(output, "S")]
    doc = l6.ReportDoc("가상", "", 2026, "2026-10-05", blocks,
                       meta={"body_reviews": [{"block": b.id, **m} for b in blocks for m in b.reviews]})
    md = doc.to_markdown()
    with fitz.open(export_report_pdf(doc, tmp_path)) as pdf:
        pdf_text = " ".join(" ".join(page.get_text().split()) for page in pdf)
    for text in (md, pdf_text):
        flat = " ".join(text.split())
        assert "정규직 20명과 기간제 7명이 참석했다" in flat                       # 정상 문장 보존
        assert "6월 9일 교육에 27명이 참석했다" not in flat and "정규직 7명과 기간제 20명이 참석했다" not in flat
        assert "| 7명 | 20명 |" not in text and "6월 9일 교육 참석: 정규직 20명" not in flat
    reviews = doc.meta["body_reviews"]
    assert {r["block"] for r in reviews if r["action"] == "replaced"} == {"exec_summary", "esg_S"}
    assert all(r.get("model_text") for r in reviews if r["action"] == "replaced")


# ── §8.2 FY 정의 적용 대조(C) ─────────────────────────────────────────────────

CALENDAR = "FY2026 = 2026-01-01~2026-12-31"
APRIL = "FY2026 = 2025-04-01~2026-03-31"
GIMHAE, BUSAN = "김해 제1공장 ", "부산 제2공장 "


def fiscal(definitions, heading="FY2026 김해 제1공장 안전 현황", site="김해 제1공장"):
    return router._zero_verdict(f"{heading}\n산업재해 발생 건수 0건", "산업재해 발생 건수", "2026-01~2026-12",
                                {"site": site} if site else {}, definitions=definitions)


@pytest.mark.parametrize("definitions,expected", [
    ("", ("SOURCE_ONLY", "fiscal_period_undefined")),
    (BUSAN + CALENDAR, ("SOURCE_ONLY", "fiscal_definition_not_applicable")),        # 다른 사업장 정의만 — 달력 연도
    (BUSAN + APRIL, ("SOURCE_ONLY", "fiscal_definition_not_applicable")),           # 다른 사업장 정의만 — 4월 시작
    (CALENDAR, ("CONFIRMED", "stated_zero")),                                       # 공통 단일 정의
    (APRIL, ("REJECTED", "period_unproven")),                                       # 공통 정의 — 명시된 범위로 비교
    (GIMHAE + CALENDAR, ("CONFIRMED", "stated_zero")),                              # 해당 사업장 단일 정의
    (GIMHAE + CALENDAR + "\n" + BUSAN + APRIL, ("CONFIRMED", "stated_zero")),       # 해당 + 다른 사업장
    (BUSAN + CALENDAR + "\n" + GIMHAE + APRIL, ("REJECTED", "period_unproven")),    # 해당 사업장 정의로 비교
    (GIMHAE + CALENDAR + "\n" + GIMHAE + APRIL, ("SOURCE_ONLY", "fiscal_definition_conflict")),
    (GIMHAE + APRIL + "\n" + GIMHAE + CALENDAR, ("SOURCE_ONLY", "fiscal_definition_conflict")),   # 줄 순서 교환
    (GIMHAE + CALENDAR + "\n" + GIMHAE + CALENDAR, ("CONFIRMED", "stated_zero")),   # 동일 정의 반복
    (CALENDAR + "\n" + GIMHAE + APRIL, ("SOURCE_ONLY", "fiscal_definition_conflict")),   # 공통·사업장 우선순위 미기재
    (APRIL + "\n" + BUSAN + CALENDAR, ("REJECTED", "period_unproven")),             # 부산 정의는 김해에 적용되지 않는다
])
def test_fiscal_definitions_apply_only_where_they_apply(definitions, expected):
    v = fiscal(definitions)
    assert (v.status, v.cause) == expected, v


@pytest.mark.parametrize("definitions,expected", [
    ("FY26 = FY2026\n" + CALENDAR, ("CONFIRMED", "stated_zero")),
    ("FY26 = FY2026\n" + BUSAN + CALENDAR, ("SOURCE_ONLY", "fiscal_definition_not_applicable")),
    ("FY26 = FY2026\n" + GIMHAE + CALENDAR + "\n" + GIMHAE + APRIL, ("SOURCE_ONLY", "fiscal_definition_conflict")),
])
def test_an_alias_keeps_the_applicability_and_conflicts_of_its_target(definitions, expected):
    v = fiscal(definitions, heading="FY26 김해 제1공장 안전 현황")
    assert (v.status, v.cause) == expected, v


def test_the_not_applicable_reason_names_the_site_and_keeps_the_definitions():
    v = fiscal(BUSAN + CALENDAR)
    traced = [text for where, _role, text, _s, _e in v.scope_spans if where == "fiscal_definition"]
    assert traced == [BUSAN.strip() + " " + CALENDAR] or traced == [CALENDAR], traced
    merged = router._zero_scope_boundary({"site": "김해 제1공장"}, v, "2026-01~2026-12")
    assert "김해 제1공장에 적용되는 FY 구간을 원문에서 확인할 수 없어" in merged["review_notes"][-1]


def test_a_request_without_a_site_uses_the_evidence_site_for_its_definition():
    assert (fiscal(GIMHAE + CALENDAR, site="").status, fiscal(GIMHAE + CALENDAR, site="").cause) \
        == ("SOURCE_ONLY", "source_site_only")                        # 김해 정의는 김해 원문에 적용된다
    assert fiscal(GIMHAE + APRIL, site="").cause == "period_unproven"   # 실제로 다른 기간은 거부를 유지
    v = fiscal(BUSAN + CALENDAR, heading="FY2026 안전 현황", site="")
    assert (v.status, v.cause) == ("SOURCE_ONLY", "fiscal_definition_not_applicable")


def test_held_fiscal_years_do_not_hold_plain_dates_and_a_clear_other_year_is_still_rejected():
    v = fiscal(BUSAN + CALENDAR, heading="2026년 김해 제1공장 연간 안전 현황")
    assert (v.status, v.cause) == ("CONFIRMED", "stated_zero")        # FY 표기가 없는 실제 연도는 그대로 비교
    v = fiscal(BUSAN + CALENDAR, heading="2025년 김해 제1공장 연간 안전 현황")
    assert (v.status, v.cause) == ("REJECTED", "period_mismatch")


# ── §10 점검에서 찾은 누락: 확인 필요 사항이 검토 전 모델 문장을 `확인된 내용`으로 다시 실었다 ──────────────

def test_the_review_list_does_not_republish_a_sentence_the_body_held():
    from esgenie import layer6_report as l6
    from esgenie.source_review import _finding
    wrong = "2026년 6월 9일 교육에는 27명이 참석하였다."
    right = "2026년 6월 3일 교육에는 27명이 참석하였다."
    findings = [_finding("generated_claim", "작성 문장의 출처 누락", sentence, "근거 인용이 없습니다.", "근거를 연결하세요.",
                         area="S") for sentence in (wrong, right)]
    # 문장이 아닌 값 항목(`작성 수치 대조 확인`의 `27명`)은 본문 판정과 따로 다시 판정하지 않는다.
    findings.append(_finding("generated_claim", "작성 수치 대조 확인", "27명", "지표에 연결할 수 없습니다.", "확인하세요.",
                             area="S"))
    output = NS(extraction=NS(mapped={}), sections={"S": verify_of(f"{wrong} {right}", docs_for({"c1": CHUNK}, FACTS))})
    output.review_findings = l6.review_generated_findings(output, findings)
    block = l6._block_source_review(output)
    from esgenie.source_review import review_markdown
    assert review_markdown(output.review_findings) == block.body_md      # 화면·내보내기와 보고서가 같은 목록
    flat = block.body_md.replace("\\", "")
    assert wrong.rstrip(".") not in flat and "[확인 보류]" in flat       # 본문이 보류한 문장은 보류 문구로
    assert right.rstrip(".") in flat                                     # 확인되는 문장은 그대로
    (record,) = [r for r in block.reviews if r["action"] == "replaced"]
    assert record["model_text"] == wrong and record["finding_id"] == findings[0].id
    assert output.review_findings[0].fact == wrong                       # 확인 목록 자료에는 모델 원문을 그대로 둔다
    assert output.review_findings[0].check_result["display_fact"].startswith("[확인 보류]")
    assert "display_fact" not in output.review_findings[2].check_result   # 값 항목은 그대로


def test_a_person_id_range_is_not_a_head_count():
    from esgenie import report_claims as rc
    assert rc.quantities("HN-G01~40: 정규직 / HN-G41~48: 기간제") == []
    assert [q.raw for q in rc.quantities("HN-G01~HN-G46 출석 46명")] == ["46명"]


def test_merging_keeps_counts_of_different_groups_apart():
    from esgenie.report_claims import facts_from_rows, related_facts, table_rows
    rows = [{"label": "교육 참석 인원 · 정규직", "value": 6.0, "unit": "명", "role": "참석", "period_text": "2026-05-12"},
            {"label": "교육 참석 인원 · 기간제", "value": 6.0, "unit": "명", "role": "참석", "period_text": "2026-05-12"},
            {"label": "교육 참석 인원", "value": 6.0, "unit": "명", "role": "참석", "period_text": "2026-05-12"},
            {"label": "참석 인원", "value": 6.0, "unit": "명", "role": "참석", "period_text": "2026-05-12"}]   # 같은 합계의 반복
    assert sorted(r["label"] for r in table_rows(rows)) == ["교육 참석 인원", "교육 참석 인원 · 기간제", "교육 참석 인원 · 정규직"]
    listed = related_facts("2026년 5월 12일 교육 참석", None, {"명"}, facts_from_rows(rows))
    assert {f.label for f in listed} >= {"교육 참석 인원 · 정규직", "교육 참석 인원 · 기간제"}


def test_a_supported_site_is_not_blocked_by_a_same_value_conflict_on_another_relation():
    # 원문에서 날짜·사업장·역할이 확인된 사실은 같은 값의 다른 날짜 후보가 있어도 채택한다.
    facts = [{"label": "교육 대상 인원", "value": 30.0, "unit": "명", "period_text": "2026-06-03",
              "site": "김해 제1공장", "source_file": "a.pdf"},
             {"label": "사업장별 인원 - 김해 제1공장", "value": 30.0, "unit": "명", "period_text": "2026-06-30",
              "site": "제1공장", "source_file": "c.pdf"}]
    body, marks = review("2026년 6월 3일 김해 제1공장 교육 대상 30명이 정해졌다 [source_facts_S].", chunks={}, facts=facts)
    assert not replaced(marks), (body, marks)
    facts[0].pop("site")
    body, marks = review("2026년 6월 3일 김해 제1공장 교육 대상 30명이 정해졌다 [source_facts_S].", chunks={}, facts=facts)
    assert replaced(marks) and "site_unstated" in replaced(marks)[0]["problems"]
    # 같은 관계(날짜)가 걸리면 여전히 막는다 — 날짜 없는 원문으로 날짜 불일치를 덮지 않는다.
    body, marks = review("2026년 6월 9일 교육에 27명이 참석했다.", chunks={"c1": "교육 참석 27명"},
                         facts=[FACTS[1]])
    assert replaced(marks) and "date" in replaced(marks)[0]["problems"]


def test_a_day_column_header_is_not_a_head_count():
    from esgenie import report_claims as rc
    chunk = "근로시간 기록: 김해 제1공장 / 2026-04-13 ~ 2026-04-19\n인원번호 | 13일 | 14일 | 15일 | 합계\nHN-G26 | 9 | 9 | 9 | 27"
    occurrences = rc.chunk_occurrences(chunk)
    assert all(o.q.value != 15 for o in occurrences)                         # 머리글의 `15일`은 수량이 아니다
    assert [o.q.value for o in occurrences] == [9, 9, 9, 27]
    body, marks = review("2026년 6월 3일 교육에는 정규직 15명이 출석했다.", chunks={"c1": chunk}, facts=None)
    assert replaced(marks)[0]["reason"] == "orphan_number"                    # 근거에 없는 15 — 다른 날짜의 값이 아니다
