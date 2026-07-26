"""Phase 3 — 설문 응답 주입 후 coverage_pct 재계산 (2026-07-17).

기존 버그: _apply_survey_answers가 mapped/missing만 바꾸고 coverage_pct·by_area를
안 건드려 표지/UI 수치가 extract() 시점 값으로 낡아 있었다.

설계 확정: coverage_pct = '값 존재' 기준(설문 포함), 증빙 기준은
evidence_coverage_pct(survey_* 제외)가 분리 담당 — Phase 2와 짝.
"""
from __future__ import annotations

from esgenie.layer1_extract import ExtractionResult, evidence_coverage_pct
from esgenie.pipeline import _apply_survey_answers


def _ext() -> ExtractionResult:
    """프로파일 4항목 가정: 공시 2(증빙 1) + 누락 2 → 커버리지 50%."""
    return ExtractionResult(
        corp_name="테스트",
        mapped={
            "E-1-1": {"code": "E-1-1", "area": "E", "evidence_node_ids": ["n1"]},
            "S-3-1": {"code": "S-3-1", "area": "S", "evidence_node_ids": []},
        },
        missing=["S-2-1", "G-1-1"],
        coverage_pct=50.0,
        by_area={
            "P": {"present": 0, "total": 0},
            "E": {"present": 1, "total": 1},
            "S": {"present": 1, "total": 2},
            "G": {"present": 0, "total": 1},
        },
    )


def test_survey_injection_recomputes_coverage():
    ext = _ext()
    _apply_survey_answers(ext, {"S-2-1": {"yn": "예", "text": "정책 있음"}})
    assert "S-2-1" in ext.mapped
    assert "S-2-1" not in ext.missing
    assert ext.coverage_pct == 75.0            # 3/4 — 낡은 50.0이 아님
    assert ext.by_area["S"]["present"] == 2
    # 증빙 커버리지는 설문을 분자에 안 넣음 (Phase 2 분리)
    assert evidence_coverage_pct(ext) == 25.0  # 증빙 연결은 E-1-1 하나


def test_survey_out_of_profile_goes_beyond_not_coverage():
    ext = _ext()
    # 프로파일 밖 코드(미missing) 설문 응답 → beyond로 분류, 분모·분자 왜곡 없음
    _apply_survey_answers(ext, {"E-9-1": {"yn": "예"}})
    assert ext.mapped["E-9-1"]["beyond_profile"] is True
    assert "E-9-1" in ext.beyond_profile
    assert ext.coverage_pct == 50.0  # 불변


def test_survey_skips_unanswered_and_existing():
    ext = _ext()
    _apply_survey_answers(ext, {
        "S-2-1": {"yn": "미입력"},
        "E-1-1": {"yn": "예"},  # 이미 mapped — 덮어쓰지 않음
    })
    assert ext.coverage_pct == 50.0
    assert ext.mapped["E-1-1"]["evidence_node_ids"] == ["n1"]
