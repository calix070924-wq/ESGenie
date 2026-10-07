"""PR #68 3차 검토 보완 R8 회귀 (2026-09-30).

R8 — 표 아래 금액 행('기본요금 | 247,500')이 머리글과 칸 수가 같다는 이유로 표 데이터 행이 되어,
     2칸 가스 표(사용량(m3)|사용열량(MJ))의 열량 칸 247,500 MJ로 읽히던 결함.
     A(정상 열량 + 요금)는 main 대비 회귀, B(빈 열량 + 요금)는 main부터 있던 잔여 결함이다.
     행 머리가 금액 라벨이면 사용량·열량 행이 아니다 — 금액 원문은 검토 기록으로만 남긴다.

재현 입력은 검토 파일(repro_fee.py)의 가상 고지서를 옮긴 픽스처다
(tests/fixtures/ocr_numeric_review_r3/review_cases.json). 외부 API를 호출하지 않는다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from esgenie.ssot import ocr_router as R
from tests.test_pr68_review_r1_r5 import _pipeline, _review, _run

FIXTURES = Path(__file__).parent / "fixtures" / "ocr_numeric_review_r3"
CASES = json.loads((FIXTURES / "review_cases.json").read_text(encoding="utf-8"))["cases"]
EMPTY = ["", "-", "—", "검침 예정"]
HEAD = ["사용량(m3)", "사용열량(MJ)"]
PERIOD = ["사용 기간: 2026-04-01 ~ 2026-04-30"]
FEE_TEXTS = ("247,500", "247500")


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(R, "_get_openai_key", lambda: None)
    monkeypatch.setattr(R, "_get_anthropic_key", lambda: None)
    monkeypatch.setattr(R, "_get_upstage_key", lambda: None)


def _values(ext):
    return sorted((m.value, m.unit) for m in ext.metrics)


def _lines_pdf(path, lines, *, x0=50, dx=180):
    """검토 파일과 같은 배치 — 줄마다 칸을 x로 벌려 글자로만 찍은 디지털 PDF(빈 칸은 찍지 않는다)."""
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    for r, row in enumerate(lines):
        for c, text in enumerate(row):
            if text:
                page.insert_text((x0 + c * dx, 60 + r * 22), text, fontname="korea", fontsize=10)
    doc.save(str(path))
    return str(path)


def _case_pdf(tmp_path, name, heat=None, fee=None):
    lines = [list(r) for r in CASES[name]["lines"]]
    if heat is not None:
        lines[4][1] = heat
    if fee is not None:
        lines[5][1] = fee
    ext = R._extract_structured_no_llm(_lines_pdf(tmp_path / f"{name}.pdf", lines), doc_type="gas_bill")
    return ext


def _no_fee_anywhere(ext):
    g, dps, sheet, answers = _pipeline([ext])
    assert not any(m.value == 247500 for m in ext.metrics)
    assert not any(n.value == 247500 or n.value == pytest.approx(0.2475) for n in g.nodes.values())
    for a in sheet.answers:
        text = repr(a)
        assert not any(t in text for t in FEE_TEXTS) and "0.2475 " not in text
    return g, dps, sheet, answers


def _money_rows(ext):
    return [r for r in (ext.router_meta.get("table_metrics") or {}).get("review", [])
            if r["reason"] == "money_row_excluded"]


# ---- 로컬 디지털 PDF (검토 재현 그대로) ---------------------------------------------

def test_r8a_local_pdf_keeps_heat_and_drops_fee(tmp_path):
    ext = _case_pdf(tmp_path, "valid_heat_with_fee")
    assert _values(ext) == [(8420, "m³"), (360772, "MJ")]
    _, dps, _, answers = _no_fee_anywhere(ext)
    assert (dps["E-4-1"].value, dps["E-4-1"].unit) == (pytest.approx(0.360772), "TJ")
    assert (dps["E-3-1"].value, dps["E-3-1"].unit) == (pytest.approx(20.239, abs=1e-3), "tCO2eq")
    assert answers["E-4-1"].value == pytest.approx(0.360772)
    assert answers["E-3-1"].value == pytest.approx(20.239, abs=1e-3)
    # 에너지와 배출량이 같은 근거(360,772 칸)를 쓰고, 합산 보류 참고로 밀려나지 않는다.
    for code in ("E-4-1", "E-3-1"):
        assert any("360772" in link.quote for link in answers[code].evidence_links)
        assert "합산 보류" not in (answers[code].review_note or "")


@pytest.mark.parametrize("empty", EMPTY)
def test_r8b_local_pdf_keeps_volume_only(tmp_path, empty):
    ext = _case_pdf(tmp_path, "empty_heat_with_fee", heat=empty)
    assert [(m.value, m.unit, m.kesg_code_guess) for m in ext.metrics] == [(8420, "m³", None)]
    _, dps, sheet, answers = _no_fee_anywhere(ext)
    assert "E-4-1" not in dps and "E-3-1" not in dps           # m³→MJ 환산·기본 열량 없음
    assert "E-4-1" not in answers and "E-3-1" not in answers
    assert {a.status for a in sheet.answers if a.qid.endswith(("-E-4-1", "-E-3-1"))} == {"insufficient"}


@pytest.mark.parametrize("fee", ["247,500", "247,500원", "₩247,500"])
def test_r8b_local_pdf_fee_with_or_without_won(tmp_path, fee):
    ext = _case_pdf(tmp_path, "empty_heat_with_fee", fee=fee)
    assert _values(ext) == [(8420, "m³")]
    _no_fee_anywhere(ext)


def test_r8a_local_pdf_explicit_zero_heat_is_kept(tmp_path):
    ext = _case_pdf(tmp_path, "valid_heat_with_fee", heat="0")
    assert _values(ext) == [(0, "MJ"), (8420, "m³")]


def test_r8_local_pdf_swapped_columns_fee_under_volume(tmp_path):
    # 칸 순서가 바뀌면 요금 숫자가 부피 칸 x에 놓인다 — 247,500 m³로도 읽지 않는다.
    lines = [PERIOD, ["사용열량(MJ)", "사용량(m3)"], ["360,772", "8,420"], ["기본요금", "247,500"]]
    ext = R._extract_structured_no_llm(_lines_pdf(tmp_path / "swap.pdf", lines), doc_type="gas_bill")
    assert _values(ext) == [(8420, "m³"), (360772, "MJ")]


def test_r8_local_pdf_fee_value_exactly_on_heat_column(tmp_path):
    # 요금 라벨·숫자를 머리글 두 칸의 x에 정확히 맞춰 찍어도 표 행으로 쓰지 않는다.
    lines = [PERIOD, HEAD, ["8,420", "-"], ["기본요금", "247,500"]]
    ext = R._extract_structured_no_llm(_lines_pdf(tmp_path / "aligned.pdf", lines, dx=120), doc_type="gas_bill")
    assert _values(ext) == [(8420, "m³")]


# ---- 표 객체 · 텍스트 줄 · 마크다운 재생 -------------------------------------------

@pytest.mark.parametrize("empty", EMPTY)
def test_r8_table_cells_empty_heat_with_fee(empty):
    ext = _run([[HEAD, ["8,420", empty], ["기본요금", "247,500"]]], "gas_bill", extra=PERIOD)
    assert [(m.value, m.unit, m.kesg_code_guess) for m in ext.metrics] == [(8420, "m³", None)]
    _no_fee_anywhere(ext)


def test_r8_table_cells_valid_heat_with_fee():
    ext = _run([[HEAD, ["8,420", "360,772"], ["기본요금", "247,500"]]], "gas_bill", extra=PERIOD)
    assert _values(ext) == [(8420, "m³"), (360772, "MJ")]
    _, dps, _, _ = _no_fee_anywhere(ext)
    assert dps["E-4-1"].value == pytest.approx(0.360772)


@pytest.mark.parametrize("label", ["기본요금", "사용요금", "공급가액", "부가가치세", "세액", "납부금액",
                                   "청구금액", "당월 청구액"])
@pytest.mark.parametrize("fee", ["247,500", "247,500원"])
def test_r8_money_labels_are_not_usage(label, fee):
    ext = _run([[HEAD, ["8,420", "-"], [label, fee]]], "gas_bill", extra=PERIOD)
    assert _values(ext) == [(8420, "m³")]
    assert [r["row_label"] for r in _money_rows(ext)][:1] == [label]


def _text_line_tokens(rows):
    return [{"text": t, "bbox": [0.08 + c * 0.3, 0.2 + r * 0.03, 0.2 + c * 0.3, 0.21 + r * 0.03], "page": 0}
            for r, row in enumerate(rows) for c, t in enumerate(row) if t]


@pytest.mark.parametrize("heat,expected", [("360,772", [(8420, "m³"), (360772, "MJ")]), ("-", [(8420, "m³")])])
def test_r8_text_line_replay(heat, expected):
    tokens = _text_line_tokens([HEAD, ["8,420", heat], ["기본요금", "247,500"]])
    ext = R._tokens_to_extraction(tokens, doc_type="gas_bill", file_path="lines.pdf", engine="pymupdf")
    assert _values(ext) == expected


@pytest.mark.parametrize("heat,expected", [("360,772", [(8420, "m³"), (360772, "MJ")]), ("-", [(8420, "m³")])])
def test_r8_markdown_replay(heat, expected):
    md = f"| 사용량(m3) | 사용열량(MJ) |\n|---|---|\n| 8,420 | {heat} |\n| 기본요금 | 247,500 |"
    tokens = [{"text": PERIOD[0], "bbox": None, "page": 0}, {"text": md, "bbox": [0.05, 0.2, 0.5, 0.3], "page": 0}]
    ext = R._tokens_to_extraction(tokens, doc_type="gas_bill", file_path="md.pdf", engine="upstage_dp", tables=[])
    assert _values(ext) == expected


@pytest.mark.parametrize("heat", ["360,772", "-"])
def test_r8_llm_normalization_never_sees_the_fee(monkeypatch, heat):
    seen = {}

    def fake_llm(kv_pairs, *, doc_type, api_key):
        seen.update(kv_pairs)
        return R._rule_normalize(kv_pairs, doc_type=doc_type)

    monkeypatch.setattr(R, "_get_openai_key", lambda: "offline-test")
    monkeypatch.setattr(R, "_llm_normalize", fake_llm)
    ext = _run([[HEAD, ["8,420", heat], ["기본요금", "247,500"]]], "gas_bill", extra=PERIOD)
    assert not any(float(v["value"]) == 247500 for v in seen.values())
    assert not any(m.value == 247500 for m in ext.metrics)


def test_r8_fee_text_is_kept_as_review_record_only():
    ext = _run([[HEAD, ["8,420", "-"], ["기본요금", "247,500"]]], "gas_bill", extra=PERIOD)
    rows = _money_rows(ext)
    assert rows and rows[0]["cells"] == ["기본요금", "247,500"] and rows[0]["table_id"]
    _, _, sheet, _ = _no_fee_anywhere(ext)
    for a in sheet.answers:                               # 채택·참고 근거 어느 쪽에도 없다
        assert not any(t in link.quote for link in (*a.evidence_links, *a.reference_links) for t in FEE_TEXTS)


# ---- 정상값 보존 ---------------------------------------------------------------------

def test_r8_usage_and_fee_in_separate_columns_keep_usage():
    ext = _run([[HEAD + ["요금(원)"], ["8,420", "360,772", "247,500"]]], "gas_bill", extra=PERIOD)
    assert _values(ext) == [(8420, "m³"), (360772, "MJ")]


def test_r8_same_number_as_heat_is_kept_only_the_fee_cell_is_excluded():
    ext = _run([[HEAD, ["8,420", "247,500"], ["기본요금", "247,500"]]], "gas_bill", extra=PERIOD)
    assert _values(ext) == [(8420, "m³"), (247500, "MJ")]
    _, dps, _, answers = _pipeline([ext])
    assert dps["E-4-1"].value == pytest.approx(0.2475)
    [link] = answers["E-4-1"].evidence_links
    assert "사용열량" in link.quote and "기본요금" not in link.quote


def test_r8_valid_heat_in_other_table_is_kept_and_linked():
    ext = _run([[HEAD, ["8,420", "-"], ["기본요금", "247,500"]], [["사용열량(MJ)"], ["360,772"]]],
               "gas_bill", extra=PERIOD, pages=[0, 1])
    assert _values(ext) == [(8420, "m³"), (360772, "MJ")]
    _, dps, _, answers = _no_fee_anywhere(ext)
    assert dps["E-4-1"].value == pytest.approx(0.360772)
    assert any("360772" in link.quote for link in answers["E-4-1"].evidence_links)


def test_r8_valid_heat_in_body_text_is_kept():
    ext = _run([[HEAD, ["8,420", "-"], ["기본요금", "247,500"]]], "gas_bill",
               extra=PERIOD + ["도시가스 사용열량 360,772 MJ"])
    assert _values(ext) == [(8420, "m³"), (360772, "MJ")]
    _no_fee_anywhere(ext)


def test_r8_site_period_meter_rows_are_not_money_rows():
    ext = _run([[["사업장", "기간", "계량기", "사용량(kWh)"],
                 ["김해 제1공장", "2026년 4월", "전력계 A", "1,000"],
                 ["김해 제1공장", "2026년 5월 요금 청구기간", "전력계 B", "1,200"],
                 ["기본요금", "", "", "247,500"]]], "kepco_bill")
    got = sorted((m.value, m.source_detail["scope"].get("meter")) for m in ext.metrics)
    assert got == [(1000, "전력계 A"), (1200, "전력계 B")]


def test_r8_label_table_total_and_detail_rows_are_kept():
    ext = _run([[["구분", "사용량(kWh)"], ["1공장", "1,000"], ["2공장", "2,000"], ["합계", "3,000"],
                 ["합계금액", "247,500"]]], "kepco_bill", extra=PERIOD)
    assert _values(ext) == [(3000, "kWh")]
    ext = _run([[["구분", "사용량(kWh)"], ["1공장", "1,000"], ["기본요금", "247,500"]]], "kepco_bill", extra=PERIOD)
    assert [(m.value, m.source_detail["row_label"]) for m in ext.metrics] == [(1000, "1공장")]


def test_r8_electric_table_with_fee_row_keeps_r1_index_check():
    ext = _run([[["전월지침(MWh)", "당월지침(MWh)", "배율", "사용량(kWh)"], ["1", "2", "1", "1"],
                 ["전력량요금", "", "", "247,500"]]], "kepco_bill")
    [m] = ext.metrics
    assert (m.value, m.unit) == (1, "kWh")
    assert m.source_detail["index_check"]["status"] == "mismatch"
    assert "explicit_vs_index_mismatch" in _review(ext)
