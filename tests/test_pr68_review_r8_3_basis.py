"""PR #68 6차 검토 — R8-3 후속: 요금 명세 행 항목명의 예상·계획 성격 보존 (2026-10-02).

`3c0b953`은 요금 명세 행('납부예정금액 | 8,420 | 360,772 | 247,500')의 항목명을 물리량의 행 이름에서 뗐다.
'예상 사용량 및 요금'·'계획 사용량 및 요금'도 같은 분류라 예상·계획 표시가 사라져 실적 답변
(0.360772 TJ·20.239 tCO2eq)이 생기던 회귀. 같은 숫자·같은 표 구조에서 항목명만 바꾼 짝으로 본다.

기대값은 픽스처(tests/fixtures/ocr_numeric_review_r6/)에 원문 의미로 직접 적었다 — 제품 단어 목록을
읽지 않는다. 외부 API를 호출하지 않는다.
"""
from __future__ import annotations

import pytest

from esgenie.ssot import ocr_router as R
from tests.r8_3_basis_support import FIXTURE, load_cases, observe, run_path, violations

PAIRS = load_cases(FIXTURE)
COMBOS = load_cases(FIXTURE.with_name("combo_cases.json"))


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(R, "_get_openai_key", lambda: None)
    monkeypatch.setattr(R, "_get_anthropic_key", lambda: None)
    monkeypatch.setattr(R, "_get_upstage_key", lambda: None)


def _params(cases):
    return [pytest.param(n, p, id=f"{n}@{p}") for n, c in cases.items() for p in c["paths"]]


def _check(case, path, tmp_path):
    if path == "local_pdf":
        pytest.importorskip("fitz")
    obs = observe(run_path(case, path, tmp_path), case["forbidden_values"])
    assert violations(case, obs) == [], obs


# ---- 같은 숫자·표 구조에서 항목명만 바꾼 짝 · 구현 후 대표 조합 (추출 → 그래프 → 선택 → 답변) ---------------

@pytest.mark.parametrize("name,path", _params(PAIRS))
def test_fee_item_basis_pairs(name, path, tmp_path):
    _check(PAIRS[name], path, tmp_path)


@pytest.mark.parametrize("name,path", _params(COMBOS))
def test_fee_item_basis_combos(name, path, tmp_path):
    _check(COMBOS[name], path, tmp_path)


# ---- 검토자 최소 입력: 행 이름·원문 항목명 기록 ------------------------------------------------------


@pytest.mark.parametrize("label", ["예상 사용량 및 요금", "계획 사용량 및 요금"])
def test_reviewer_quantity_plan_row_keeps_plan_label(label, tmp_path):
    ext = run_path(PAIRS["qty_expected_usage" if label.startswith("예상") else "qty_planned_usage"], "table_cells", tmp_path)
    assert {m.source_detail["row_item"] for m in ext.metrics} == {label}
    assert all(m.source_detail["row_label"] == label for m in ext.metrics)


def test_reviewer_pay_scheduled_row_is_actual(tmp_path):
    ext = run_path(PAIRS["timing_pay_scheduled"], "table_cells", tmp_path)
    assert [(m.source_detail["row_label"], m.source_detail["row_item"]) for m in ext.metrics] == [("", "납부예정금액")] * 2
