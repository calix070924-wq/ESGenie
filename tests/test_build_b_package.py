"""B 전달 패키지 빌더 테스트 — 정답·키가 섞이면 실패하는지 본다.

실제 정답 값을 테스트에 적지 않는다. 검사 대상은 "값이 채워졌는가"다.
"""
from __future__ import annotations

import csv
import importlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from esgenie.eval import answer_format as af
from esgenie.eval import response_scoring as rs

pkg = importlib.import_module("scripts.build_b_package")

REPO = Path(__file__).resolve().parents[1]


def test_pre_review_list_excludes_answers_and_expected_values():
    for rel in pkg.PRE_REVIEW_FILES:
        assert not rel.startswith(pkg.FINAL_LABEL_DIR), rel
        for part in pkg.DENIED_PARTS:
            assert part not in rel, (rel, part)


def test_denied_parts_cover_the_known_leak_paths():
    """기대값이 적힌 문서와 실제 실행 결과가 금지 목록에 들어 있는지 고정한다."""
    for needed in ("docs/validation/", "UI연결용_수치범위_출력계약", "독립성_노출기록",
                   "result.json", ".env"):
        assert needed in pkg.DENIED_PARTS


def test_pre_review_package_builds_and_matches_its_own_listing(tmp_path):
    out = tmp_path / "b.zip"
    result = pkg.build("pre_review", out)
    assert out.exists()
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    assert names == {f"{result['root']}/README.md"} | {
        f"{result['root']}/{rel}" for rel in pkg.PRE_REVIEW_FILES}
    assert result["file_count"] == len(pkg.PRE_REVIEW_FILES) + 1


def test_packaged_blank_forms_are_still_blank(tmp_path):
    out = tmp_path / "b.zip"
    pkg.build("pre_review", out)
    with zipfile.ZipFile(out) as zf:
        forms = [n for n in zf.namelist() if n.endswith("labels_blank.csv")]
        assert len(forms) == 2, "공식 표본과 수치형 전수, 두 서식이 들어 있어야 한다"
        for name in forms:
            rows = list(csv.DictReader(io.StringIO(zf.read(name).decode("utf-8-sig"))))
            assert rows
            for row in rows:
                assert not any((v or "").strip() for k, v in row.items()
                               if k not in ("stage", "qid"))


def test_packaged_examples_are_marked_synthetic(tmp_path):
    out = tmp_path / "b.zip"
    result = pkg.build("pre_review", out)
    with zipfile.ZipFile(out) as zf:
        for name in zf.namelist():
            if name.endswith("_excerpt.json"):
                text = zf.read(name).decode("utf-8")
                assert "합성 예시" in text
        readme = zf.read(f"{result['root']}/README.md").decode("utf-8")
    assert "정답이 없다" in readme
    assert result["commit"].split()[0]  # 커밋 SHA가 README 생성에 쓰였다
    assert af.FORMAT_VERSION in readme


def test_readme_records_hashes_of_every_file(tmp_path):
    out = tmp_path / "b.zip"
    result = pkg.build("pre_review", out)
    with zipfile.ZipFile(out) as zf:
        readme = zf.read(f"{result['root']}/README.md").decode("utf-8")
    for item in result["files"]:
        assert item["path"] in readme
        assert item["sha256"] in readme


def test_readme_separates_done_from_undone(tmp_path):
    out = tmp_path / "b.zip"
    result = pkg.build("pre_review", out)
    with zipfile.ZipFile(out) as zf:
        readme = zf.read(f"{result['root']}/README.md").decode("utf-8")
    assert "구현 검증 완료" in readme
    for undone in ("합의 대기", "원본 증빙 미확보", "미실행", "미정"):
        assert undone in readme
    assert "채점기 납품 전체 완료" in readme  # 그렇게 쓰지 않는다는 경고로 들어 있다


def test_final_package_is_refused_while_anything_is_missing(tmp_path, capsys):
    """미완료 상태에서 최종본을 만들지 않는다 — 빈 파일로 채우지도 않는다."""
    assert pkg.main(["--stage", "final", "--out", str(tmp_path / "f.zip")]) == 2
    err = capsys.readouterr().err
    assert "최종본을 만들 수 없다" in err
    assert "확정 정답 라벨이 없다" in err or "초안이다" in err
    assert not (tmp_path / "f.zip").exists()


def test_existing_zip_is_not_overwritten_silently(tmp_path, capsys):
    out = tmp_path / "b.zip"
    assert pkg.main(["--out", str(out)]) == 0
    assert pkg.main(["--out", str(out)]) == 2
    assert "이미 있다" in capsys.readouterr().err
    assert pkg.main(["--out", str(out), "--overwrite"]) == 0


def test_audit_rejects_a_filled_label_form(tmp_path, monkeypatch):
    """허용 목록이 잘못 바뀌어 채워진 라벨이 들어가도 검사에서 걸린다."""
    bad = tmp_path / "labels_blank.csv"
    with bad.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(rs.LABEL_COLUMNS)
        writer.writerow(["initial", "SYN-1", "hold", "no_evidence"]
                        + [""] * (len(rs.LABEL_COLUMNS) - 4))
    monkeypatch.setattr(pkg, "REPO", tmp_path)
    problems = pkg.audit(["labels_blank.csv"], "pre_review")
    assert any("빈 서식에 값이 들어 있다" in p for p in problems)


def test_audit_rejects_key_markers_and_denied_paths(tmp_path, monkeypatch):
    (tmp_path / "docs").mkdir()
    leak = tmp_path / "docs" / "note.md"
    leak.write_text("UPSTAGE_API_KEY=<여기에 키가 있다>\n", encoding="utf-8")
    monkeypatch.setattr(pkg, "REPO", tmp_path)
    problems = pkg.audit(["docs/note.md"], "pre_review")
    assert any("키로 보이는 표식" in p for p in problems)

    (tmp_path / "docs" / "validation").mkdir()
    (tmp_path / "docs" / "validation" / "README.md").write_text("x", encoding="utf-8")
    problems = pkg.audit(["docs/validation/README.md"], "pre_review")
    assert any("금지 경로" in p for p in problems)


def test_audit_rejects_an_unmarked_evidence_quote(tmp_path, monkeypatch):
    """합성 예시 자리에 실제 증빙 문구가 들어가면 걸린다.

    실제 수치를 테스트에 적지 않는다. 검사 대상은 "합성 표시가 있는가"다.
    """
    bad = tmp_path / "x_excerpt.json"
    bad.write_text(json.dumps(
        {"answers": [{"evidence": [{"quote": "어떤 지표 00.0%"}]}]}, ensure_ascii=False),
        encoding="utf-8")
    monkeypatch.setattr(pkg, "REPO", tmp_path)
    assert any("합성 표시가 없는 근거 인용문" in p
               for p in pkg.audit(["x_excerpt.json"], "pre_review"))

    ok = tmp_path / "y_excerpt.json"
    ok.write_text(json.dumps(
        {"answers": [{"evidence": [{"quote": "(예시) 어떤 문구"}, {"quote": None}]}]},
        ensure_ascii=False), encoding="utf-8")
    assert not [p for p in pkg.audit(["y_excerpt.json"], "pre_review")
                if "근거 인용문" in p]


def test_packaged_examples_have_only_synthetic_quotes():
    """전달본에 실제로 들어가는 예시 파일이 이 검사를 통과하는지 본다."""
    for rel in pkg.PRE_REVIEW_FILES:
        if rel.endswith("_excerpt.json"):
            assert pkg._unmarked_quotes(pkg.REPO / rel) == []


def test_audit_rejects_missing_and_empty_files(tmp_path, monkeypatch):
    (tmp_path / "empty.md").write_text("", encoding="utf-8")
    monkeypatch.setattr(pkg, "REPO", tmp_path)
    problems = pkg.audit(["empty.md", "gone.md"], "pre_review")
    assert any("빈 파일" in p for p in problems)
    assert any("없는 파일" in p for p in problems)


def test_pre_review_stage_refuses_confirmed_labels_in_the_list(tmp_path, monkeypatch):
    label_dir = tmp_path / pkg.FINAL_LABEL_DIR
    label_dir.mkdir(parents=True)
    (label_dir / "real.csv").write_text("stage,qid\n", encoding="utf-8")
    monkeypatch.setattr(pkg, "REPO", tmp_path)
    problems = pkg.audit([f"{pkg.FINAL_LABEL_DIR}/real.csv"], "pre_review")
    assert any("독립 검토 전 전달본에 정답 라벨" in p for p in problems)
