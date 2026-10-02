"""PR #68 5차 검토 — R8-3 부재 표시·금액 항목 판정 회귀 (2026-09-30).

`3adb047`의 부재 표시 판정이 문구 **일부**에 '예정·추후·미정' 등이 있으면 칸 전체를 값 없음으로 보아,
2칸 가스 표 아래 '납부예정금액 | 247,500'의 항목명이 행 머리에서 사라지고 247,500이 열량 열 위치를 따라
247,500 MJ(0.2475 TJ·13.885 tCO2eq)가 되던 회귀. 같은 입력의 `06a1433` 코어에서는 생기지 않았다.
정상 혼합 표에서 항목명 '납부예정금액'이 물리량 힌트에 붙어 계획값으로 읽혀 답변이 비던 문제도 함께 본다.

기대값은 픽스처(tests/fixtures/ocr_numeric_review_r5/)에 원문 의미로 직접 적었다 — 제품 단어 목록을
읽지 않는다. 외부 API를 호출하지 않는다.
"""
from __future__ import annotations

import pytest

from esgenie.ssot import ocr_router as R
from tests.r8_3_support import FIXTURE, load_cases, observe, run_path, violations
from tests.test_pr68_review_r1_r5 import _pipeline, _review, _run

PAIRS = load_cases(FIXTURE)
COMBOS = load_cases(FIXTURE.with_name("combo_cases.json"))
HEAD = ["사용량(m3)", "사용열량(MJ)"]
MIXED = ["항목", "사용량(m3)", "사용열량(MJ)", "요금(원)"]
PERIOD = ["사용 기간: 2026-04-01 ~ 2026-04-30"]
FEES = ["납부예정금액", "청구예정금액", "추후청구요금", "납부금액"]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(R, "_get_openai_key", lambda: None)
    monkeypatch.setattr(R, "_get_anthropic_key", lambda: None)
    monkeypatch.setattr(R, "_get_upstage_key", lambda: None)


def _params(cases):
    return [pytest.param(name, path, id=f"{name}@{path}") for name, c in cases.items() for path in c["paths"]]


def _check(case, path, tmp_path):
    if path == "local_pdf":
        pytest.importorskip("fitz")
    obs = observe(run_path(case, path, tmp_path), case["forbidden_values"])
    assert violations(case, obs) == [], obs
    return obs


# ---- 판정 충돌 검사 · 구현 후 대표 조합 (추출 → 그래프 → 선택 → 답변) ----------------------

@pytest.mark.parametrize("name,path", _params(PAIRS))
def test_pair_cases(name, path, tmp_path):
    _check(PAIRS[name], path, tmp_path)


@pytest.mark.parametrize("name,path", _params(COMBOS))
def test_combo_cases(name, path, tmp_path):
    _check(COMBOS[name], path, tmp_path)


# ---- 검토자 16관측(4개 라벨 × 정상·빈 열량 × 표 재생·로컬 PDF) ------------------------------

@pytest.mark.parametrize("path", ["table_cells", "local_pdf"])
@pytest.mark.parametrize("label", FEES)
def test_reviewer_fee_row_with_valid_heat_uses_heat_cell(label, path, tmp_path):
    obs = _check(PAIRS[f"known_{_key(label)}_valid"], path, tmp_path)
    for code, value in (("E-4-1", 0.360772), ("E-3-1", 20.239)):
        a = obs["answers"][code]
        assert a["value"] == pytest.approx(value, abs=1e-6)
        assert any("360772" in q or "360,772" in q for q in a["evidence"])
        assert not any("247500" in q or "247,500" in q for q in (*a["evidence"], *a["reference"]))


@pytest.mark.parametrize("path", ["table_cells", "local_pdf"])
@pytest.mark.parametrize("label", FEES)
def test_reviewer_fee_row_with_empty_heat_makes_no_energy(label, path, tmp_path):
    obs = _check(PAIRS[f"known_{_key(label)}_empty"], path, tmp_path)
    assert obs["metrics"] == [[8420.0, "m³"]]
    assert obs["answers"]["E-4-1"] is None and obs["answers"]["E-3-1"] is None


def _key(label):
    return {"납부예정금액": "pay_scheduled", "청구예정금액": "bill_scheduled", "추후청구요금": "later_billing",
            "납부금액": "paid_amount"}[label]


# ---- 금액 행은 원문 셀 단위로 기록되고, LLM 정규화에도 가지 않는다 ------------------------------

@pytest.mark.parametrize("label", FEES[:3])
@pytest.mark.parametrize("heat", ["360,772", "-"])
def test_fee_row_is_money_record_with_label(label, heat):
    ext = _run([[HEAD, ["8,420", heat], [label, "247,500"]]], "gas_bill", extra=PERIOD)
    rows = [r for r in ext.router_meta["table_metrics"]["review"] if r["reason"] == "money_row_excluded"]
    assert rows and rows[0]["row_label"] == label and rows[0]["cells"] == [label, "247,500"]
    assert not any(m.value == 247500 for m in ext.metrics)


@pytest.mark.parametrize("label", FEES[:3])
@pytest.mark.parametrize("heat", ["360,772", "-"])
def test_fee_row_never_reaches_llm_normalization(monkeypatch, label, heat):
    seen = {}

    def fake_llm(kv_pairs, *, doc_type, api_key):
        seen.update(kv_pairs)
        return R._rule_normalize(kv_pairs, doc_type=doc_type)

    monkeypatch.setattr(R, "_get_openai_key", lambda: "offline-test")
    monkeypatch.setattr(R, "_llm_normalize", fake_llm)
    ext = _run([[HEAD, ["8,420", heat], [label, "247,500"]]], "gas_bill", extra=PERIOD)
    assert not any(float(v["value"]) == 247500 for v in seen.values())
    assert not any(m.value == 247500 for m in ext.metrics)


# ---- 요금 명세 행의 항목명은 금액 칸의 이름 — 물리량의 행 이름·기준(계획값)이 되지 않는다 ------------

@pytest.mark.parametrize("label", ["납부예정금액", "청구예정금액", "사용요금"])
def test_fee_statement_row_item_is_kept_as_record_not_as_row_label(label):
    ext = _run([[MIXED, [label, "8,420", "360,772", "247,500"]]], "gas_bill", extra=PERIOD)
    heat = next(m for m in ext.metrics if m.unit == "MJ")
    assert heat.source_detail["row_item"] == label and heat.source_detail["row_label"] == ""
    assert "예정" not in heat.metric_hint
    _, _, sheet, answers = _pipeline([ext])
    assert answers["E-4-1"].value == pytest.approx(0.360772)
    assert "insufficient" not in {a.status for a in sheet.answers if a.qid.endswith("-E-4-1")}


def test_scope_row_label_is_not_treated_as_fee_item():
    ext = _run([[["사업장", "사용량(kWh)"], ["1공장", "1,000"]]], "kepco_bill")
    assert [(m.source_detail["row_label"], m.source_detail.get("row_item")) for m in ext.metrics] == [("1공장", None)]


# ---- '값 없음 표시'와 '숫자가 아닌 글자'는 다른 판단이다 -------------------------------------------

@pytest.mark.parametrize("marker", ["", "-", "N/A", "검침 예정", "(검침 예정)", "미검침", "미정", "해당 없음"])
def test_absent_marker_in_heat_cell_is_value_absent(marker):
    ext = _run([[HEAD, ["8,420", marker]]], "gas_bill", extra=PERIOD)
    tm = ext.router_meta["table_metrics"]
    assert "value_absent" in _review(ext) and "value_not_numeric" not in _review(ext)
    assert {a["raw_text"] for a in tm["absent_cells"]} == {marker}       # 표·텍스트 격자에서 같은 칸(기존 동작)


@pytest.mark.parametrize("text", ["검침불가", "확인 요망", "검침예정일"])
def test_unknown_text_in_heat_cell_is_held_not_absent(text):
    ext = _run([[HEAD, ["8,420", text]]], "gas_bill", extra=PERIOD)
    tm = ext.router_meta["table_metrics"]
    assert "value_not_numeric" in _review(ext) and "value_absent" not in _review(ext)
    assert {a["raw_text"] for a in tm["absent_cells"]} == {text}          # 같은 표의 다른 후보 차단은 유지
    assert sorted((m.value, m.unit) for m in ext.metrics) == [(8420, "m³")]


def test_explicit_zero_is_a_value():
    ext = _run([[HEAD, ["8,420", "0"]]], "gas_bill", extra=PERIOD)
    assert sorted((m.value, m.unit) for m in ext.metrics) == [(0, "MJ"), (8420, "m³")]
    assert "value_absent" not in _review(ext) and "value_not_numeric" not in _review(ext)


@pytest.mark.parametrize("marker", ["검침 예정", "미검침", "미정"])
def test_absent_marker_first_column_keeps_index_calculation(marker):
    ext = _run([[["사용량(kWh)", "전월지침", "당월지침", "배율"], [marker, "1,000", "1,250", "1"]]], "kepco_bill")
    assert [(m.value, m.source_detail["value_source"]) for m in ext.metrics] == [(250, "computed")]
    assert "row_label_in_quantity_column" not in _review(ext)
