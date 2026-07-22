"""표 구조 복원 회귀 테스트 — L0 오염 근본 수정(feature/l0-table-structure).

`_extract_text_pymupdf`를 좌표 기반 행 복원(dict)으로 교체한 효과를 실보고서로 고정한다.
채점 로직·정답 셀은 bake-off 실험 스크립트(scripts/table_extract_bakeoff.py)를 그대로
재사용한다 — 실험에서 검증한 기준(LINKED = 레이블-값 같은 줄)을 회귀에 이식.

느린 테스트(실보고서 PDF 오픈)라 @pytest.mark.slow. 실행: pytest -m slow
PDF가 없는 CI에서는 자동 skip.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# bake-off 스크립트를 모듈로 로드(scripts/는 패키지가 아니라 importlib로 직접 로드).
_BAKEOFF_PATH = ROOT / "scripts" / "table_extract_bakeoff.py"
_spec = importlib.util.spec_from_file_location("table_extract_bakeoff", _BAKEOFF_PATH)
bakeoff = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("table_extract_bakeoff", bakeoff)
_spec.loader.exec_module(bakeoff)

from esgenie.ssot.ocr_router import _reconstruct_rows_from_dict

pytestmark = pytest.mark.slow

fitz = pytest.importorskip("fitz", reason="pymupdf 미설치")


def _page_text(pdf_rel: str, page_no: int) -> str:
    """실보고서 PDF의 한 페이지를 신규 행 복원 방식으로 텍스트화."""
    pdf = ROOT / pdf_rel
    if not pdf.exists():
        pytest.skip(f"실보고서 PDF 없음: {pdf_rel}")
    doc = fitz.open(str(pdf))
    return _reconstruct_rows_from_dict(doc[page_no])


# CASES를 (case_id, label, value)로 펼쳐 셀 단위 파라미터화 — 실패 셀을 바로 식별.
_CELL_PARAMS = [
    pytest.param(case, lab, val, id=f"{case['id']}::{lab}::{val}")
    for case in bakeoff.CASES
    for (lab, val, _col) in case["cells"]
]


@pytest.mark.parametrize("case,label,value", _CELL_PARAMS)
def test_cases_are_linked(case, label, value):
    """bake-off 정답 셀 9건이 모두 LINKED(레이블-값 같은 줄로 복원)여야 한다.

    현행 get_text() 평탄화 방식에서는 전부 PRESENT/MISSING이었다(0/9).
    행 복원 + 헤더 상속(작업2) + 컬럼 헤더 매핑(작업3)으로 9/9 LINKED가 목표.
    """
    text = _page_text(case["pdf"], case["page"])
    verdict = bakeoff.score_cell(text, label, value)
    assert verdict == "LINKED", (
        f"[{case['id']}] {label}/{value} → {verdict} (기대 LINKED)\n"
        f"오염 사례: {case['contamination']}"
    )


def test_mobis_energy_label_inheritance():
    """작업2: 모비스 p.52 '에너지 사용량'은 레이블이 별도 행에 있어 행 복원만으론
    PRESENT였다(bake-off 한계 1건). 헤더 행 상속으로 9,075와 같은 줄이 돼야 한다."""
    text = _page_text("data/real_reports/012330_mobis_2025.pdf", 52)
    assert bakeoff.score_cell(text, "에너지 사용량", "9,075") == "LINKED"


def test_mobis_renewable_total_column_distinguishable():
    """작업3: 모비스 p.53 재생에너지 전환율은 법인별 12컬럼. 전사(합계) 값 12.9가
    다른 11개 값과 구분 가능해야 한다(35.0 오염의 원인 지점).

    같은 행에 12.9가 '합계' 컬럼 라벨과 함께 부착되고, 다른 값(0.2 등)에는 '합계'가
    붙지 않아야 한다(틀린 라벨 부착 금지)."""
    text = _page_text("data/real_reports/012330_mobis_2025.pdf", 53)
    row = next((ln for ln in text.splitlines() if "재생에너지 사용·전환율" in ln), None)
    assert row is not None, "재생에너지 사용·전환율 행을 찾지 못함"

    # 전사값 12.9에 합계 컬럼 라벨이 부착돼 전사임을 식별할 수 있어야 한다.
    assert "12.9(합계)" in row, f"전사 12.9에 합계 라벨 미부착:\n{row}"
    # 국내(별도) 첫 값 0.2는 합계가 아니므로 (합계)가 붙으면 안 된다.
    assert "0.2(합계)" not in row, f"비전사 값에 합계 라벨 오부착:\n{row}"


def test_footnote_marker_split_from_value():
    """부수 효과: 각주 마커(예: '7)')가 값과 다른 셀로 분리돼야 한다(각주→값 오파싱 방지).

    LG화학 p.41 용수 재이용률 행에서 '7)'와 '2.72'가 같은 셀로 붙지 않아야 한다
    (삼성전기 재해율 '4)'를 4.0으로 읽던 계열의 오염 예방)."""
    text = _page_text("data/real_reports/051910_lgchem_2025.pdf", 41)
    row = next((ln for ln in text.splitlines() if "용수 재이용률" in ln), None)
    assert row is not None
    assert "7)2.72" not in row.replace(" ", "")


def test_reconstruct_falls_back_gracefully_on_empty_page():
    """빈 페이지(span 없음)는 예외 없이 get_text() 결과(빈 문자열)로 폴백해야 한다."""

    class _EmptyPage:
        def get_text(self, kind: str | None = None):
            return {"blocks": []} if kind == "dict" else ""

    assert _reconstruct_rows_from_dict(_EmptyPage()) == ""
