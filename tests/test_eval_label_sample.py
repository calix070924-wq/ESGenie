"""독립 라벨링 표본 추출 테스트.

여기서 검증하는 것은 (1) 재현성, (2) **정답이 새지 않는가**, (3) 빈 서식이 정답으로
오해되어 쓰이지 않는가다. 표본 문항 ID는 양식에서 읽으며 테스트에 박지 않는다.
"""
from __future__ import annotations

import csv
import importlib
import json
from pathlib import Path

import pytest

from esgenie.eval import answer_format as af
from esgenie.eval import response_scoring as rs
from esgenie.supplychain.frameworks import get_framework

sampler = importlib.import_module("scripts.eval_label_sample")

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "data" / "eval" / "sample" / "bm_rba42_v1"


def test_same_seed_gives_the_same_sample():
    a = sampler.draw("rba42", 10, 20261006)
    b = sampler.draw("rba42", 10, 20261006)
    assert a == b


def test_different_seed_gives_a_different_sample():
    a = sampler.draw("rba42", 10, 1)["qids"]
    b = sampler.draw("rba42", 10, 2)["qids"]
    assert a != b, "씨값이 달라도 같은 표본이면 추출이 씨값을 쓰지 않는다는 뜻이다"


def test_sample_covers_both_stages_with_the_same_questions():
    sample = sampler.draw("rba42", 10, 20261006)
    assert sample["stages"] == list(af.STAGES)
    assert sample["total_rows"] == 20
    assert len(sample["rows"]) == 20
    for stage in af.STAGES:
        assert sorted(r["qid"] for r in sample["rows"] if r["stage"] == stage) == sample["qids"]


def test_sampled_qids_exist_in_the_framework_and_are_unique():
    qids = sampler.draw("rba42", 10, 20261006)["qids"]
    known = {q.qid for q in get_framework("rba42").questions}
    assert len(set(qids)) == len(qids) == 10
    assert set(qids) <= known


def test_every_question_type_present_in_the_framework_is_representable():
    """수치형이 6문항뿐이라 비율 배분으로도 한 건은 들어온다 — 유형이 통째로 빠지지 않는다."""
    sample = sampler.draw("rba42", 10, 20261006)
    assert set(sample["strata"]) == {q.qtype for q in get_framework("rba42").questions}
    assert sample["strata"]["numeric"]["sampled"] >= 1
    assert sum(s["sampled"] for s in sample["strata"].values()) == 10


def test_strata_sizes_distribute_without_exceeding_population():
    quota = sampler.strata_sizes({"a": 6, "b": 42}, 48, 10)
    assert sum(quota.values()) == 10
    assert quota["a"] <= 6 and quota["b"] <= 42
    # 모집단보다 큰 표본은 거부한다.
    with pytest.raises(ValueError, match="문항 수"):
        sampler.strata_sizes({"a": 3}, 3, 4)


def test_full_framework_sample_takes_every_question():
    sample = sampler.draw("rba42", 48, 7)
    assert sorted(sample["qids"]) == sorted(q.qid for q in get_framework("rba42").questions)


def test_package_contains_no_answers(tmp_path):
    """표본 패키지에 정답으로 쓰일 수 있는 값이 없는지 본다."""
    assert sampler.main(["--seed", "20261006", "--out", str(tmp_path / "pkg")]) == 0
    pkg = tmp_path / "pkg"

    manifest = json.loads((pkg / sampler.MANIFEST).read_text(encoding="utf-8"))
    assert "정답이 들어 있지 않다" in manifest["notice"]
    # 정답에 해당하는 열 이름이 표본 기록에 아예 없다.
    blob = json.dumps(manifest, ensure_ascii=False)
    for leaked in ("expected_value", "expected_sources", "expected_decision", "decision"):
        assert leaked not in blob

    question_cols = set(sampler.QUESTION_COLUMNS)
    assert question_cols.isdisjoint({"expected_value", "expected_decision", "value", "answer"})

    with (pkg / sampler.BLANK_LABELS).open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 20
    for row in rows:
        assert row["stage"] in af.STAGES and row["qid"]
        filled = {k: v for k, v in row.items() if k not in ("stage", "qid") and v != ""}
        assert filled == {}, f"빈 서식에 값이 들어 있다: {filled}"


def test_blank_form_is_refused_by_the_scorer(tmp_path):
    """빈 서식이 정답 라벨로 쓰이지 않게 막는다 — 기본값으로 채우지 않았다."""
    assert sampler.main(["--seed", "1", "--out", str(tmp_path / "pkg")]) == 0
    with pytest.raises(rs.LabelError, match="expected_decision"):
        rs.load_labels(tmp_path / "pkg" / sampler.BLANK_LABELS)


def test_question_export_matches_the_framework_text(tmp_path):
    assert sampler.main(["--seed", "20261006", "--out", str(tmp_path / "pkg")]) == 0
    questions = {q.qid: q for q in get_framework("rba42").questions}
    with (tmp_path / "pkg" / sampler.QUESTIONS).open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 10
    for row in rows:
        q = questions[row["qid"]]
        assert row["text"] == q.text and row["qtype"] == q.qtype
        assert row["evidence_required"] == ("Y" if q.evidence_required else "N")


def test_existing_package_is_not_overwritten_silently(tmp_path, capsys):
    out = str(tmp_path / "pkg")
    assert sampler.main(["--seed", "1", "--out", out]) == 0
    assert sampler.main(["--seed", "2", "--out", out]) == 2
    assert "이미 표본이 있다" in capsys.readouterr().err
    # 씨값 1의 표본이 그대로 남아 있다.
    manifest = json.loads((tmp_path / "pkg" / sampler.MANIFEST).read_text(encoding="utf-8"))
    assert manifest["seed"] == 1
    assert sampler.main(["--seed", "2", "--out", out, "--overwrite"]) == 0


def test_committed_sample_package_is_reproducible(tmp_path):
    """커밋한 표본이 기록된 씨값으로 다시 나오는지 고정한다."""
    committed = json.loads((SAMPLE_DIR / sampler.MANIFEST).read_text(encoding="utf-8"))
    assert sampler.draw(committed["framework"], committed["per_stage"],
                        committed["seed"]) == committed
