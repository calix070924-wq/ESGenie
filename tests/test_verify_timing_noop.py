"""동일성 검사(§3.3 ①) 강화 테스트 — 2026-10-11 정민 보완 지시서 T1.

업무 값이 조금이라도 바뀐 사본은 **실패**해야 하고, 허용한 계측·생성 메타데이터만
바뀐 사본은 **통과**해야 한다. 입력은 전부 합성이다.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "verify_timing_noop", Path(__file__).resolve().parents[1] / "scripts" / "verify_timing_noop.py")
noop = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(noop)

BASE = {
    "generated_at": "2026-10-11T10:00:00",
    "timings": {"seconds": 1.25, "stages": [{"name": "extract", "seconds": 0.5}]},
    "sheet": {
        "answers": [{
            "qid": "SYN-1", "value": 1.0000000001, "unit": "%", "status": "verified",
            "comparison": "", "period": "2026-04", "boundary_label": "합성 사업장 A",
            "rationale": "합성 사유",
            "evidence_links": [{"file_name": "SYN_01.pdf", "page": 0,
                                "quote": "합성 인용 2026-04-01T09:00:00 기록"}],
        }],
    },
}


def _cmp(b, allowed=None):
    allowed = noop.DEFAULT_ALLOWED if allowed is None else allowed
    diffs, _ = noop.diff(BASE, b, list(allowed))
    return diffs


def _mut(fn):
    b = copy.deepcopy(BASE)
    fn(b)
    return b


def _ans(b):
    return b["sheet"]["answers"][0]


@pytest.mark.parametrize("name, mutate", [
    ("값의 아주 작은 차이", lambda b: _ans(b).update(value=1.0000000002)),
    ("단위", lambda b: _ans(b).update(unit="퍼센트")),
    ("상태", lambda b: _ans(b).update(status="self_reported")),
    ("비교 판정", lambda b: _ans(b).update(comparison="scope_unconfirmed")),
    ("기간", lambda b: _ans(b).update(period="2026-05")),
    ("사업장", lambda b: _ans(b).update(boundary_label="합성 사업장 B")),
    ("사유", lambda b: _ans(b).update(rationale="다른 합성 사유")),
    ("근거 파일", lambda b: _ans(b)["evidence_links"][0].update(file_name="SYN_02.pdf")),
    ("근거 쪽", lambda b: _ans(b)["evidence_links"][0].update(page=1)),
    ("인용문 안의 시각", lambda b: _ans(b)["evidence_links"][0].update(
        quote="합성 인용 2026-04-02T09:00:00 기록")),
    ("인용문 안의 절대경로", lambda b: _ans(b)["evidence_links"][0].update(
        quote="합성 인용 /Users/someone/x.pdf")),
    ("근거 추가", lambda b: _ans(b)["evidence_links"].append({"file_name": "SYN_03.pdf"})),
    ("업무 필드 누락", lambda b: _ans(b).pop("unit")),
])
def test_business_changes_are_detected(name, mutate):
    assert _cmp(_mut(mutate)), name


def test_allowed_metadata_only_changes_pass():
    b = _mut(lambda b: (b.update(generated_at="2027-01-01T00:00:00"),
                        b["timings"].update(seconds=99.0)))
    assert _cmp(b) == []


def test_timings_missing_on_the_old_side_passes():
    """계측 전 코드에는 timings가 없다 — 허용 경로라 차이가 아니다."""
    old = copy.deepcopy(BASE)
    old.pop("timings")
    diffs, skipped = noop.diff(old, BASE, list(noop.DEFAULT_ALLOWED))
    assert diffs == [] and "timings" in skipped


def test_same_key_name_elsewhere_is_still_compared():
    """'seconds'나 'generated_at'이라는 이름이어도 허용 경로가 아니면 비교한다."""
    b = _mut(lambda b: _ans(b).update(seconds=2))
    assert _cmp(b), "허용 경로 밖에 생긴 필드"
    a2 = _mut(lambda b: _ans(b).update(generated_at="x"))
    b2 = _mut(lambda b: _ans(b).update(generated_at="y"))
    diffs, _ = noop.diff(a2, b2, list(noop.DEFAULT_ALLOWED))
    assert diffs, "중첩 위치의 generated_at은 업무 데이터일 수 있다"


def test_wildcard_path_matches_only_that_path():
    allowed = {"sheet.answers[*].evidence_links[*].page": "합성 테스트"}
    b = _mut(lambda b: _ans(b)["evidence_links"][0].update(page=5))
    assert _cmp(b, allowed) == []
    b = _mut(lambda b: _ans(b).update(page=5))          # 다른 위치의 page
    assert _cmp(b, allowed)


def test_no_default_allow_compares_everything():
    b = _mut(lambda b: b.update(generated_at="2027-01-01T00:00:00"))
    diffs, _ = noop.diff(BASE, b, [])
    assert [d["path"] for d in diffs] == ["generated_at"]


def test_compare_cli_reports_hashes_and_exit_code(tmp_path, capsys):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps(BASE, ensure_ascii=False), encoding="utf-8")
    b.write_text(json.dumps(_mut(lambda x: _ans(x).update(value=1.0000000002)),
                            ensure_ascii=False), encoding="utf-8")
    report = tmp_path / "r.json"
    assert noop.main(["compare", str(a), str(b), "--report", str(report)]) == 1
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["diff_count"] == 1
    assert data["diffs"][0]["path"] == "sheet.answers[0].value"
    assert len(data["a"]["sha256"]) == 64 and data["a"]["sha256"] != data["b"]["sha256"]
    assert data["normalization"].startswith("없음")
    assert noop.main(["compare", str(a), str(a)]) == 0
    assert "IDENTICAL" in capsys.readouterr().out


def test_allow_requires_a_reason(tmp_path):
    a = tmp_path / "a.json"
    a.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit):
        noop.main(["compare", str(a), str(a), "--allow", "sheet.x"])


def test_old_style_out_argument_is_treated_as_dump():
    """`--out`만 주던 옛 사용법을 dump로 받는다(인자 해석만 본다)."""
    with pytest.raises(SystemExit):
        noop.main(["--help"])
