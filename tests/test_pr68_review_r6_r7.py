"""PR #68 2차 검토 보완 R6·R7 회귀 (2026-09-29).

R6 — 사용량·열량 칸이 비어 있을 때 템플릿 인접 숫자(전월지침 등)가 사용량으로 되살아나던 회귀
     (50b8d72 → 89f34aa). 빈 칸은 역할을 차지하지 않되(R3), 그 표 안의 다른 칸 숫자를 템플릿·LLM
     후보로 채택하지 않는다.
R7 — 같은 쪽의 다른 기간·계량기 기록이 '같은 쪽'이라는 이유만으로 한 사실로 합쳐지던 결함.

재현 입력은 검토 파일(repro.py)의 네 최소 입력을 옮긴 가상 픽스처다
(tests/fixtures/ocr_numeric_review_r2/review_cases.json). 외부 API를 호출하지 않는다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from esgenie.ssot import ocr_router as R
from esgenie.ssot.ocr_router import ExtractedTable, TableCell
from tests.test_pr68_review_r1_r5 import _pipeline, _review, _run, _usage

FIXTURES = Path(__file__).parent / "fixtures" / "ocr_numeric_review_r2"
CASES = json.loads((FIXTURES / "review_cases.json").read_text(encoding="utf-8"))["cases"]
EMPTY = ["", "-", "—", "검침 예정"]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(R, "_get_openai_key", lambda: None)
    monkeypatch.setattr(R, "_get_anthropic_key", lambda: None)
    monkeypatch.setattr(R, "_get_upstage_key", lambda: None)


def _case(name, **kw):
    c = CASES[name]
    return _run(c["tables"], c["doc_type"], extra=c["extra"], **kw)


def _values(ext):
    return sorted((m.value, m.unit) for m in ext.metrics)


def _absent_states(ext):
    return [a["state"] for a in (ext.router_meta.get("table_metrics") or {}).get("absent_cells", [])]


def _permute(rows, order):
    return [[row[k] for k in order] for row in rows]


def _pdf(path, tables, extra=()):
    """표를 글자로만 찍은 디지털 PDF(빈 칸은 아무것도 찍지 않는다) — 로컬 텍스트 경로 확인용."""
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    y = 80
    for line in extra:
        page.insert_text((50, y), line, fontname="korea", fontsize=10)
        y += 20
    for rows in tables:
        y += 20
        for row in rows:
            for c, text in enumerate(row):
                if text:
                    page.insert_text((50 + c * 120, y), text, fontname="korea", fontsize=10)
            y += 22
    doc.save(str(path))
    return str(path)


# ---- R6. 빈 사용량·열량 칸 --------------------------------------------------------

@pytest.mark.parametrize("empty", EMPTY)
@pytest.mark.parametrize("order", [(0, 1, 2), (1, 2, 0), (1, 0, 2)])
def test_r6_empty_electric_usage_is_not_filled_from_readings(empty, order):
    rows = _permute([["사용량(kWh)", "전월지침", "당월지침"], [empty, "1,000", "1,250"]], order)
    ext = _run([rows], "kepco_bill")
    assert ext.metrics == []
    assert "multiplier_missing" in _review(ext)           # 배율을 가정해 250을 만들지 않는다
    assert _absent_states(ext) and set(_absent_states(ext)) == {"held:multiplier_missing"}


@pytest.mark.parametrize("empty", EMPTY)
@pytest.mark.parametrize("header", [["가스사용량(MJ)", "전월지침(m3)"], ["사용열량(MJ)", "전월지침(m3)"],
                                    ["전월지침(m3)", "가스사용량(MJ)"]])
def test_r6_empty_gas_heat_is_not_filled_from_previous_reading(empty, header):
    first = header[0].startswith("전월")
    rows = [header, ["31,580", empty] if first else [empty, "31,580"]]
    ext = _run([rows], "gas_bill")
    assert ext.metrics == []                               # 31,580 MJ도, 다른 사용량·열량도 없다
    assert "value_absent" in _review(ext)


@pytest.mark.parametrize("name", ["empty_electric_missing_multiplier", "empty_heat_with_only_previous"])
def test_r6_review_minimal_inputs(name):
    ext = _case(name)
    assert not any(m.value in CASES[name]["forbidden_values"] for m in ext.metrics)
    assert "template_candidate_from_other_cell" in _review(ext)
    _, dps, _, answers = _pipeline([ext])
    assert "E-4-1" not in dps and "E-3-1" not in dps       # 가짜 사용량의 파생 에너지·배출량 없음
    assert "E-4-1" not in answers and "E-3-1" not in answers


@pytest.mark.parametrize("name", ["empty_electric_missing_multiplier", "empty_heat_with_only_previous"])
def test_r6_llm_normalization_never_sees_the_reading(monkeypatch, name):
    seen = {}

    def fake_llm(kv_pairs, *, doc_type, api_key):
        seen.update(kv_pairs)
        return R._rule_normalize(kv_pairs, doc_type=doc_type)

    monkeypatch.setattr(R, "_get_openai_key", lambda: "offline-test")
    monkeypatch.setattr(R, "_llm_normalize", fake_llm)
    ext = _case(name)
    assert not any(float(v["value"]) in CASES[name]["forbidden_values"] for v in seen.values())
    assert not any(m.value in CASES[name]["forbidden_values"] for m in ext.metrics)


def test_r6_empty_heat_keeps_volume_but_never_makes_mj():
    ext = _run([[["사용량(m3)", "사용열량(MJ)"], ["8,420", "-"]]], "gas_bill")
    assert [(m.value, m.unit, m.kesg_code_guess) for m in ext.metrics] == [(8420, "m³", None)]


def test_r6_empty_volume_keeps_heat_without_fake_volume():
    ext = _run([[["사용량(m3)", "사용열량(MJ)"], ["-", "360,772"]]], "gas_bill")
    assert [(m.value, m.unit, m.kesg_code_guess) for m in ext.metrics] == [(360772, "MJ", "E-4-1")]


def test_r6_explicit_zero_is_a_valid_value():
    ext = _run([[["사용량(kWh)", "전월지침", "당월지침"], ["0", "1,000", "1,000"]]], "kepco_bill")
    assert [(m.value, m.unit) for m in _usage(ext)] == [(0, "kWh")]
    assert _absent_states(ext) == []


def test_r6_r1_computed_usage_from_mwh_readings_is_kept():
    ext = _run([[["전월지침(MWh)", "당월지침(MWh)", "배율", "사용량(kWh)"], ["1", "2", "1", "-"]]], "kepco_bill")
    [m] = _usage(ext)
    assert (m.value, m.unit, m.source_detail["value_source"]) == (1000, "kWh", "computed")
    assert m.source_detail["index_check"]["status"] == "computed_only"
    assert set(_absent_states(ext)) == {"computed"}


def test_r6_r1_mismatch_warning_is_kept():
    ext = _run([[["전월지침(MWh)", "당월지침(MWh)", "배율", "사용량(kWh)"], ["1", "2", "1", "1"]]], "kepco_bill")
    [m] = _usage(ext)
    assert m.source_detail["index_check"]["status"] == "mismatch"
    assert "explicit_vs_index_mismatch" in _review(ext)


def test_r6_gas_heat_and_volume_values_are_kept():
    heat = _run([[["가스사용량(MJ)"], ["360,772"]]], "gas_bill")
    assert [(m.value, m.unit, m.kesg_code_guess) for m in heat.metrics] == [(360772, "MJ", "E-4-1")]
    vol = _run([[["전월지침(m3)", "당월지침(m3)", "사용량(MJ)"], ["31,580", "40,000", "-"]]], "gas_bill")
    assert [(m.value, m.unit, m.kesg_code_guess) for m in vol.metrics] == [(8420, "m³", None)]


def test_r6_valid_usage_in_body_text_is_not_removed():
    # 같은 문서의 본문 문장에 적힌 사용량은 빈 칸 표 밖의 원문이다 — 지우지 않는다.
    ext = _run([[["사용량(kWh)", "전월지침", "당월지침"], ["-", "1,000", "1,250"]]], "kepco_bill",
               extra=["사용전력량(kWh): 128,400"])
    assert _values(ext) == [(128400, "kWh")]


def test_r6_valid_usage_in_other_table_is_not_removed():
    ext = _run([[["사용량(kWh)", "전월지침", "당월지침"], ["-", "1,000", "1,250"]],
                [["사용량(kWh)"], ["142,560"]]], "kepco_bill", pages=[0, 1])
    assert [(m.value, m.unit, m.page) for m in ext.metrics] == [(142560, "kWh", 1)]


def test_r6_r3_empty_column_still_does_not_claim_role():
    from esgenie.ssot.ocr_table_metrics import extract_table_metrics
    rows = [["가스사용량(MJ)", "비고"], ["-", "검침 예정"]]
    res = extract_table_metrics([], [ExtractedTable(
        table_id="t0", row_count=2, column_count=2, source="upstage_dp", page=0,
        cells=[TableCell(row_index=r, column_index=c, content=t, bbox=[0.1, 0.1, 0.2, 0.2], page=0)
               for r, row in enumerate(rows) for c, t in enumerate(row)])], doc_type="gas_bill")
    assert res.claimed_roles == set() and [a["state"] for a in res.absent_cells] == ["absent"]


@pytest.mark.parametrize("empty", EMPTY)
@pytest.mark.parametrize("doc_type,rows,forbidden", [
    ("kepco_bill", [["사용량(kWh)", "전월지침", "당월지침"], ["{e}", "1,000", "1,250"]], {1000, 1250, 250}),
    ("gas_bill", [["가스사용량(MJ)", "전월지침(m3)"], ["{e}", "31,580"]], {31580}),
])
def test_r6_local_pdf_text_path(tmp_path, empty, doc_type, rows, forbidden):
    rows = [[t.replace("{e}", empty) for t in row] for row in rows]
    ext = R._extract_structured_no_llm(_pdf(tmp_path / "variant.pdf", [rows]), doc_type=doc_type)
    assert not any(m.value in forbidden for m in ext.metrics)
    assert [m for m in ext.metrics if m.kesg_code_guess == "E-4-1"] == []


def test_r6_local_pdf_text_path_keeps_normal_usage(tmp_path):
    rows = [["전월지침(m3)", "당월지침(m3)", "사용량(m3)", "사용열량(MJ)"], ["31,580", "40,000", "8,420", "360,772"]]
    ext = R._extract_structured_no_llm(_pdf(tmp_path / "normal.pdf", [rows]), doc_type="gas_bill")
    assert _values(ext) == [(8420, "m³"), (360772, "MJ")]


def test_r6_charge_row_below_table_is_not_aligned_into_usage(tmp_path):
    # 칸 수가 머리글보다 모자란 행은 모든 칸이 수치일 때만 열 좌표로 맞춘다 — 표 아래
    # '기본요금 247,500'을 열을 추측해 사용열량으로 넣지 않는다(HMC 가스 고지서 배치).
    path = tmp_path / "charge.pdf"
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    for row, y in ((["전월지침(m3)", "당월지침(m3)", "사용량(m3)", "사용열량(MJ)"], 100),
                   (["31,580", "40,000", "8,420", "360,772"], 122)):
        for c, t in enumerate(row):
            page.insert_text((50 + c * 120, y), t, fontname="korea", fontsize=10)
    page.insert_text((50, 144), "기본요금", fontname="korea", fontsize=10)
    page.insert_text((410, 144), "247,500", fontname="korea", fontsize=10)
    doc.save(str(path))
    ext = R._extract_structured_no_llm(str(path), doc_type="gas_bill")
    assert _values(ext) == [(8420, "m³"), (360772, "MJ")]


# ---- R7. 같은 쪽의 다른 기간·계량기 ------------------------------------------------

def _scopes(ext, axis):
    return sorted(m.source_detail.get("scope", {}).get(axis, "") for m in _usage(ext))


def test_r7_two_months_on_one_page_are_two_facts():
    ext = _case("same_site_different_period")
    assert sorted(m.period for m in _usage(ext)) == ["2026년 4월", "2026년 5월"]
    assert _scopes(ext, "site") == ["김해 제1공장", "김해 제1공장"]
    assert "conflicting_values" not in _review(ext)
    assert not any("repeated_cells" in m.source_detail for m in ext.metrics)


def test_r7_two_meters_on_one_page_are_two_facts():
    ext = _case("different_meters_same_site")
    assert _scopes(ext, "meter") == ["전력계 A", "전력계 B"]
    assert "conflicting_values" not in _review(ext)


def test_r7_different_meter_values_are_not_a_conflict():
    ext = _run([[["사업장", "계량기", "사용량(kWh)"], ["김해 제1공장", "전력계 A", "1,000"]],
                [["사업장", "계량기", "사용량(kWh)"], ["김해 제1공장", "전력계 B", "1,200"]]], "kepco_bill")
    assert sorted(m.value for m in _usage(ext)) == [1000, 1200]
    assert "conflicting_values" not in _review(ext)
    assert ext.router_meta.get("hitl_required") is not True


@pytest.mark.parametrize("order", [(0, 1, 2), (1, 0, 2), (2, 1, 0), (2, 0, 1)])
def test_r7_scope_columns_read_regardless_of_order(order):
    tables = [_permute(t, order) for t in CASES["same_site_different_period"]["tables"]]
    ext = _run(tables, "kepco_bill")
    assert sorted(m.period for m in _usage(ext)) == ["2026년 4월", "2026년 5월"]
    assert _scopes(ext, "site") == ["김해 제1공장", "김해 제1공장"]


def test_r7_multi_row_table_keeps_period_and_meter():
    ext = _run([[["사업장", "기간", "계량기", "사용량(kWh)"],
                 ["김해 제1공장", "2026년 4월", "전력계 A", "1,000"],
                 ["김해 제1공장", "2026년 4월", "전력계 B", "1,000"],
                 ["김해 제1공장", "2026년 5월", "전력계 A", "1,000"]]], "kepco_bill")
    got = sorted((m.period, m.source_detail["scope"]["meter"]) for m in _usage(ext))
    assert got == [("2026년 4월", "전력계 A"), ("2026년 4월", "전력계 B"), ("2026년 5월", "전력계 A")]


def test_r7_different_pages_are_two_facts():
    ext = _run(CASES["same_site_different_period"]["tables"], "kepco_bill", pages=[0, 1])
    assert sorted((m.page, m.period) for m in _usage(ext)) == [(0, "2026년 4월"), (1, "2026년 5월")]


def test_r7_same_cell_from_table_and_text_is_one_fact():
    # _run은 표 객체 칸과 같은 칸의 텍스트 토큰을 함께 준다 — 두 경로가 같은 원문 칸이다.
    ext = _run([[["사업장", "기간", "사용량(kWh)"], ["김해 제1공장", "2026년 4월", "1,000"]]], "kepco_bill")
    [m] = _usage(ext)
    assert "duplicate_status" not in m.source_detail


def test_r7_shared_table_bbox_does_not_make_different_cells_one():
    # Upstage 표 칸은 표 외접 bbox 하나를 공유한다. 그 bbox가 여러 텍스트 칸을 감싸도 같은 칸이 아니다.
    rows = [["사업장", "기간", "사용량(kWh)"], ["김해 제1공장", "2026년 4월", "1,000"], ["김해 제1공장", "2026년 5월", "1,000"]]
    tokens, cells = [], []
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            box = [0.05 + c * 0.16, 0.2 + r * 0.03, 0.19 + c * 0.16, 0.21 + r * 0.03]
            tokens.append({"text": text, "bbox": box, "page": 0})
            cells.append(TableCell(row_index=r, column_index=c, content=text, bbox=[0.04, 0.19, 0.6, 0.3], page=0))
    table = ExtractedTable(table_id="t0", row_count=3, column_count=3, cells=cells, source="upstage_dp", page=0)
    ext = R._tokens_to_extraction(tokens, doc_type="kepco_bill", file_path="shared.pdf", engine="upstage_dp",
                                  tables=[table])
    assert sorted(m.period for m in _usage(ext)) == ["2026년 4월", "2026년 5월"]


def test_r7_summary_and_detail_repeat_is_kept_but_not_double_counted():
    ext = _run([[["사업장", "사용량(kWh)"], ["김해 제1공장", "1,000"]],
                [["사업장", "사용량(kWh)"], ["김해 제1공장", "1,000"]]], "kepco_bill",
               extra=["사용 기간: 2026-04-01 ~ 2026-04-30"])
    ms = _usage(ext)
    assert len(ms) == 2 and all(m.source_detail["duplicate_status"] == "undetermined" for m in ms)
    _, dps, _, _ = _pipeline([ext])
    assert dps["E-4-1"].value == pytest.approx(0.0036)     # 0.0072(이중 계산)가 아니다
    assert dps["E-3-1"].value == pytest.approx(0.478, abs=1e-3)


def test_r7_same_scope_conflict_stays_in_review():
    ext = _run([[["사업장", "기간", "사용량(kWh)"], ["김해 제1공장", "2026년 4월", "1,000"]],
                [["사업장", "기간", "사용량(kWh)"], ["김해 제1공장", "2026년 4월", "1,200"]]], "kepco_bill")
    assert "conflicting_values" in _review(ext)
    assert ext.router_meta.get("hitl_required") is True
    _, dps, _, _ = _pipeline([ext])
    assert any("상충" in n for n in dps["E-4-1"].scope_notes)


def _answer_scope(answer):
    [link] = answer.evidence_links
    return link.quote


def test_r7_months_answer_uses_one_period_for_energy_and_emission():
    g, dps, _, answers = _pipeline([_case("same_site_different_period")])
    nodes = [n for n in g.nodes.values() if n.metric == "E-4-1"]
    assert sorted(n.boundary.period_text for n in nodes) == ["2026년 4월", "2026년 5월"]
    e41, e31 = dps["E-4-1"], dps["E-3-1"]
    assert e41.value == pytest.approx(0.0036)
    assert e41.boundary["period_text"] == e31.boundary["period_text"]
    period = e41.boundary["period_text"]
    assert period in _answer_scope(answers["E-4-1"]) and period in _answer_scope(answers["E-3-1"])
    assert "합산 보류" in answers["E-4-1"].review_note      # 다른 달은 참고 근거로 보존
    assert answers["E-4-1"].reference_links


@pytest.mark.parametrize("values", [("1,000", "1,000"), ("1,000", "1,200")])
def test_r7_meters_answer_uses_one_meter_and_is_not_a_conflict(values):
    tables = [[["사업장", "계량기", "사용량(kWh)"], ["김해 제1공장", "전력계 A", values[0]]],
              [["사업장", "계량기", "사용량(kWh)"], ["김해 제1공장", "전력계 B", values[1]]]]
    ext = _run(tables, "kepco_bill", extra=["사용 기간: 2026-04-01 ~ 2026-04-30"])
    g, dps, _, answers = _pipeline([ext])
    meters = sorted(p["scope"]["meter"] for n in g.nodes.values() if n.metric == "E-4-1"
                    for p in n.boundary.provenance if p.get("scope"))
    assert meters == ["전력계 A", "전력계 B"]
    e41, e31 = dps["E-4-1"], dps["E-3-1"]
    for dp in (e41, e31):
        assert "source_conflict" not in dp.confidence_flags and dp.comparison != "mismatch"
        assert not any("상충" in n for n in dp.scope_notes)
    chosen = _answer_scope(answers["E-4-1"])
    meter = "전력계 A" if "전력계 A" in chosen else "전력계 B"
    assert meter in _answer_scope(answers["E-3-1"])         # 에너지와 배출량이 같은 계량기
    kwh = 1000 if values[0] == values[1] or meter == "전력계 A" else 1200
    assert e41.value == pytest.approx(kwh * 3.6e-6)          # 두 계량기를 합산하지 않는다
    assert "계량기 상이" in answers["E-4-1"].review_note
    assert any(("전력계 B" if meter == "전력계 A" else "전력계 A") in link.quote
               for link in answers["E-4-1"].reference_links)


def test_r7_meters_in_different_documents_are_not_cross_check_mismatch():
    a = _run([[["사업장", "계량기", "사용량(kWh)"], ["김해 제1공장", "전력계 A", "1,000"]]], "kepco_bill",
             name="a.pdf", extra=["사용 기간: 2026-04-01 ~ 2026-04-30", "문서 A"])
    b = _run([[["사업장", "계량기", "사용량(kWh)"], ["김해 제1공장", "전력계 B", "1,200"]]], "kepco_bill",
             name="b.pdf", extra=["사용 기간: 2026-04-01 ~ 2026-04-30", "문서 B"])
    g, dps, _, _ = _pipeline([a, b])
    assert not [e for e in g.edges if e.comparison == "mismatch"]
    assert "source_conflict" not in dps["E-4-1"].confidence_flags
