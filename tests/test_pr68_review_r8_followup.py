"""PR #68 4차 검토 — R8 재보완 회귀 (2026-09-30).

R8-1 — `9101b92`의 금액 행 제외가 첫 칸 항목명('사용요금')만 보고 4열 혼합 표
       (항목|사용량(m3)|사용열량(MJ)|요금(원))의 정상 사용량·열량 행을 통째로 지우던 회귀.
       머리글 '사용량(m3)'의 3이 템플릿 값(3 m3)으로 남던 부수 오인식도 함께 본다.
R8-2 — 목록 밖 금액 이름(기본료·사용료)과 '계량기' 단어로 면제되던 '계량기 교체요금'이 2칸 표
       아래에서 열량(247,500 MJ)이 되던 기존 잔존 결함.

같은 표현의 정상 사례와 제외 사례를 짝지어, 잘못된 값의 생성과 정상값의 소실을 함께 검사한다.
기대값은 픽스처(tests/fixtures/ocr_numeric_review_r4/)에 원문 의미로 직접 적었다 — 제품 단어 목록을
읽지 않는다. 외부 API를 호출하지 않는다.
"""
from __future__ import annotations

import pytest

from esgenie.ssot import ocr_router as R
from esgenie.ssot import ocr_table_metrics as T
from tests.r8_followup_support import FIXTURE, load_cases, observe, run_path, violations
from tests.test_pr68_review_r1_r5 import _pipeline, _review, _run

PAIRS = load_cases()
COMBOS = load_cases(FIXTURE.with_name("combo_cases.json"))
HEAD = ["사용량(m3)", "사용열량(MJ)"]
MIXED = ["항목", "사용량(m3)", "사용열량(MJ)", "요금(원)"]
PERIOD = ["사용 기간: 2026-04-01 ~ 2026-04-30"]


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


# ---- 짝 검사 · 구현 후 대표 조합 (추출 → 그래프 → 선택 → 답변) ------------------------

@pytest.mark.parametrize("name,path", _params(PAIRS))
def test_pair_cases(name, path, tmp_path):
    _check(PAIRS[name], path, tmp_path)


@pytest.mark.parametrize("name,path", _params(COMBOS))
def test_combo_cases(name, path, tmp_path):
    _check(COMBOS[name], path, tmp_path)


# ---- R8-1 혼합 표 -----------------------------------------------------------------

@pytest.mark.parametrize("path", ["table_cells", "markdown", "text_lines", "local_pdf"])
def test_r8_1_mixed_row_keeps_physical_cells_and_links_heat_cell(path, tmp_path):
    obs = _check(PAIRS["r8_1_mixed_valid"], path, tmp_path)
    for code in ("E-4-1", "E-3-1"):
        a = obs["answers"][code]
        assert any("360772" in q or "360,772" in q for q in a["evidence"])
        assert not any("247500" in q or "247,500" in q for q in (*a["evidence"], *a["reference"]))
    assert obs["answers"]["E-3-1"]["value"] == pytest.approx(20.239, abs=1e-3)


def test_r8_1_money_column_cell_is_recorded_not_used():
    ext = _run([[MIXED, ["사용요금", "8,420", "360,772", "247,500"]]], "gas_bill", extra=PERIOD)
    heat = next(m for m in ext.metrics if m.unit == "MJ")
    assert heat.source_detail["raw_text"] == "360,772" and heat.source_detail["header"] == "사용열량(MJ)"
    assert "money_row_excluded" not in _review(ext)


def test_r8_1_header_unit_digit_is_not_a_template_value():
    # 표가 사용량을 차지하지 못한 경우에도 머리글 '사용량(m3)'의 3을 값으로 쓰지 않는다.
    template = R._load_template("gas_bill")
    kv = R._apply_template([{"text": "사용량(m3)", "bbox": [0.1, 0.1, 0.2, 0.12], "page": 0}], template)
    assert all(v["value"] != 3 for v in kv.values())
    kv = R._apply_template([{"text": "가스사용량(m3): 8,420", "bbox": None, "page": 0}], template)
    assert kv["가스사용량"]["value"] == 8420


# ---- R8-2 금액 이름 · 범위 예외 --------------------------------------------------------

@pytest.mark.parametrize("label", ["기본요금", "기본료", "사용료", "계량기 교체요금"])
@pytest.mark.parametrize("heat", ["360,772", "-"])
def test_r8_2_fee_row_is_review_record_only(label, heat):
    ext = _run([[HEAD, ["8,420", heat], [label, "247,500"]]], "gas_bill", extra=PERIOD)
    rows = [r for r in ext.router_meta["table_metrics"]["review"] if r["reason"] == "money_row_excluded"]
    assert rows and rows[0]["row_label"] == label and rows[0]["cells"] == [label, "247,500"]
    assert not any(m.value == 247500 for m in ext.metrics)


@pytest.mark.parametrize("label", ["기본료", "사용료", "계량기 교체요금"])
@pytest.mark.parametrize("heat", ["360,772", "-"])
def test_r8_2_llm_normalization_never_sees_the_fee(monkeypatch, label, heat):
    seen = {}

    def fake_llm(kv_pairs, *, doc_type, api_key):
        seen.update(kv_pairs)
        return R._rule_normalize(kv_pairs, doc_type=doc_type)

    monkeypatch.setattr(R, "_get_openai_key", lambda: "offline-test")
    monkeypatch.setattr(R, "_llm_normalize", fake_llm)
    ext = _run([[HEAD, ["8,420", heat], [label, "247,500"]]], "gas_bill", extra=PERIOD)
    assert not any(float(v["value"]) == 247500 for v in seen.values())
    assert not any(m.value == 247500 for m in ext.metrics)


def test_r8_2_unlisted_label_is_held_by_structure_not_by_word():
    # 금액 이름으로 알려지지 않은 행 머리도 수량 열에 글자가 있으면 표 행이 아니다 — 보류 사유를 남긴다.
    ext = _run([[HEAD, ["8,420", "360,772"], ["비고", "247,500"]]], "gas_bill", extra=PERIOD)
    held = [r for r in ext.router_meta["table_metrics"]["review"] if r["reason"] == "row_label_in_quantity_column"]
    assert {r["row_label"] for r in held} == {"비고"}                 # 표·텍스트 격자에서 같은 사유(기존 동작)
    assert sorted((m.value, m.unit) for m in ext.metrics) == [(8420, "m³"), (360772, "MJ")]


def test_r8_2_money_label_row_with_empty_money_cell_is_held():
    ext = _run([[MIXED, ["사용요금", "8,420", "360,772", "247,500"], ["기본료", "7,300", "", ""]]],
               "gas_bill", extra=PERIOD)
    assert "money_label_row_unconfirmed" in _review(ext)
    assert sorted((m.value, m.unit) for m in ext.metrics) == [(8420, "m³"), (360772, "MJ")]


def test_r8_2_meter_column_values_kept_meter_fee_excluded():
    ext = _run([[["계량기", "사용량(kWh)"], ["전력계 A", "1,000"], ["전력계 B", "1,200"],
                 ["계량기 교체요금", "33,000"]]], "kepco_bill")
    got = sorted((m.value, m.source_detail["scope"].get("meter")) for m in ext.metrics)
    assert got == [(1000, "전력계 A"), (1200, "전력계 B")]
    _, _, sheet, _ = _pipeline([ext])
    assert "합산 보류" in " ".join(a.review_note or "" for a in sheet.answers)   # R7 계량기 구분 유지


def test_r8_2_period_value_with_charge_word_stays_a_period():
    ext = _run([[["기간", "사용량(kWh)"], ["2026년 4월", "1,000"], ["2026년 5월 요금 청구기간", "1,200"]]], "kepco_bill")
    assert sorted(m.source_detail["scope"].get("period") for m in ext.metrics) == ["2026년 4월", "2026년 5월 요금 청구기간"]


# ---- 템플릿 후보와 제외 칸의 동일성 ------------------------------------------------------

def _money(value, bbox=(0.1, 0.1, 0.2, 0.2), page=0):
    return {"value": value, "raw_text": f"{value:,.0f}", "bbox": list(bbox), "page": page, "table_id": "t0",
            "reason": "money_row_excluded"}


@pytest.mark.parametrize("info,expected", [
    ({"value": 247500, "bbox": [0.12, 0.12, 0.18, 0.18], "page": 0, "raw_label": "열량"}, True),
    ({"value": 247500, "bbox": [0.12, 0.12, 0.18, 0.18], "page": 1, "raw_label": "열량"}, False),   # 다른 쪽
    ({"value": 247500, "bbox": [0.5, 0.5, 0.6, 0.6], "page": 0, "raw_label": "열량"}, False),       # 칸 밖
    ({"value": 360772, "bbox": [0.12, 0.12, 0.18, 0.18], "page": 0, "raw_label": "열량"}, False),   # 다른 숫자
    ({"value": 247500, "bbox": None, "page": 0, "raw_label": "사용열량 247,500 MJ"}, False),       # 자기 토큰이 출처
    ({"value": 247500, "bbox": None, "page": 0, "raw_label": "열량"}, None),                        # 확인 불가
])
def test_template_candidate_identity(info, expected):
    assert T._candidate_is_money_cell(info, _money(247500)) is expected


def test_template_candidate_identity_unknown_is_held_with_reason():
    res = T.TableMetricResult(money_cells=[_money(247500)])
    kv = {"열량": {"value": 247500, "unit": "MJ", "bbox": None, "page": 0, "raw_label": "열량"}}
    T.drop_template_candidates_from_money_cells(res, kv, "gas_bill")
    assert kv == {} and [r["reason"] for r in res.review] == ["template_candidate_identity_unknown"]


# ---- 기존 R8 최소 사례 · R6 빈 칸 첫 열 ---------------------------------------------------

def test_absent_marker_in_first_column_is_not_a_row_label():
    ext = _run([[["사용량(kWh)", "전월지침", "당월지침"], ["검침 예정", "31,580", "32,580"]]], "kepco_bill")
    assert "row_label_in_quantity_column" not in _review(ext) and "money_row_excluded" not in _review(ext)
    assert not any(m.value == 31580 for m in ext.metrics)
