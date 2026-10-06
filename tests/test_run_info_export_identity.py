"""`run_info=None`이면 기존 출력과 같아야 한다 — 작업지시서 B §3 통과 조건 1.

기준값은 exporters에 `run_info`를 넣기 **전에** `tests/fixtures/make_run_info_baseline.py`로
떠 두었다(`tests/fixtures/run_info_baseline.json`). 수정 후 출력과 그것을 비교한다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from .fixtures.make_run_info_baseline import VOLATILE_NOTE, snapshot

BASELINE = Path(__file__).resolve().parent / "fixtures" / "run_info_baseline.json"


@pytest.fixture(scope="module")
def baseline() -> dict:
    if not BASELINE.exists():
        pytest.fail(f"기준값이 없다: {BASELINE} — make_run_info_baseline.py를 먼저 돌려야 한다")
    return json.loads(BASELINE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def current(tmp_path_factory) -> dict:
    out = tmp_path_factory.mktemp("identity") / "response_sheet"
    return snapshot(out)          # run_info를 넘기지 않는다 = run_info=None


def test_excel_sheet_names_unchanged(baseline, current):
    assert current["excel_sheets"] == baseline["excel_sheets"]
    assert "실행정보" not in current["excel_sheets"]


def test_excel_every_cell_unchanged(baseline, current):
    assert set(current["excel_cells"]) == set(baseline["excel_cells"])
    for name in baseline["excel_cells"]:
        want, got = baseline["excel_cells"][name], current["excel_cells"][name]
        assert set(got) == set(want), f"시트 '{name}'의 셀 좌표 집합이 달라졌다"
        for coord in want:
            assert got[coord] == want[coord], f"{name}!{coord} 셀 값이 달라졌다"


def test_pdf_extracted_text_unchanged(baseline, current):
    assert len(current["pdf_pages"]) == len(baseline["pdf_pages"]), "PDF 쪽수가 달라졌다"
    for i, (want, got) in enumerate(zip(baseline["pdf_pages"], current["pdf_pages"])):
        assert got == want, f"PDF {i+1}쪽 추출 텍스트가 달라졌다"


def test_volatile_exclusions_recorded(baseline):
    """비교에서 제외한 항목을 기준값 파일이 명시한다."""
    assert baseline["volatile_note"] == VOLATILE_NOTE


def test_baseline_actually_has_content(baseline):
    """기준값이 비어 있으면 위 테스트들이 조용히 통과한다 — 그걸 막는다."""
    assert baseline["excel_sheets"]
    assert sum(len(v) for v in baseline["excel_cells"].values()) >= 20
    assert baseline["pdf_pages"] and baseline["pdf_pages"][0].strip()


def test_stamped_output_differs(tmp_path):
    """run_info를 주면 출력이 실제로 달라진다 — 동일성 테스트가 무의미하지 않음을 보인다."""
    from esgenie import run_info as ri

    info = ri.build_run_info(llm_stats_end={"mode": "on", "hits": 0, "misses": 2,
                                            "live_calls": 2},
                             llm_stats_start={"mode": "on", "hits": 0, "misses": 0,
                                              "live_calls": 0},
                             ocr_stats={"hits": 0, "misses": 1, "mode": "miss"})
    stamped = snapshot(tmp_path / "stamped", run_info=info)
    assert "실행정보" in stamped["excel_sheets"]
    assert ri.STAMP_PREFIX.replace(" ", "") in stamped["pdf_pages"][0].replace(" ", "")
