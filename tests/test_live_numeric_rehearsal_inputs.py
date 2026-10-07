"""scripts/live_numeric_rehearsal.py의 입력 검증 — 구성 목록 두 형식.

증빙 세트가 바뀌어도(BM 개편 12건 → 정상 5건 보강 17건) 코드를 고치지 않고
`--initial-dir`·`--followup-dir`·`--manifest`로 받는다. 해시 검증과 '회사 답변 1건'
검사는 두 형식에서 똑같이 걸려야 한다.

tmp_path에 수제 파일만 쓴다 — 실제 증빙 세트나 개인 절대 경로에 의존하지 않는다.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_script():
    path = ROOT / "scripts" / "live_numeric_rehearsal.py"
    spec = importlib.util.spec_from_file_location("live_numeric_rehearsal", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rehearsal = _load_script()

OLD_INITIAL, OLD_FOLLOWUP = "01_처음업로드_12건", "02_보완할때추가_1건"
OLD_MANIFEST = "00_촬영안내_업로드하지않음/구성_검산_목록.json"
NEW_INITIAL, NEW_FOLLOWUP = "01_처음업로드_17건", "02_교육보완때추가_1건"
NEW_MANIFEST = "00_안내와정답_업로드금지/구성_원본대조_목록.json"


def _write_pdf(path: Path, body: str) -> str:
    """최소한의 PDF 바이트. 내용을 읽지 않는 검증만 하므로 모양만 맞춘다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = f"%PDF-1.4\n% {body}\n".encode("utf-8")
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _old_pack(tmp_path: Path, *, n_initial: int = 3, company_index: int = 2,
              break_hash: str | None = None) -> Path:
    """옛 형식 팩: files + counts.initial_upload/followup_evidence + version."""
    pack = tmp_path / "old_pack"
    files = []
    for i in range(1, n_initial + 1):
        rel = f"{OLD_INITIAL}/{i:02d}_문서.pdf"
        digest = _write_pdf(pack / rel, f"old-{i}")
        files.append({"file": rel, "sha256": digest,
                      "role": "company_answer" if i == company_index else "evidence"})
    rel = f"{OLD_FOLLOWUP}/99_보완.pdf"
    files.append({"file": rel, "sha256": _write_pdf(pack / rel, "old-f"),
                  "role": "evidence"})
    # 업로드하지 않는 요청서 — 목록에는 있고 폴더에는 두지 않는다.
    files.append({"file": f"00_촬영안내_업로드하지않음/00_요청서.pdf",
                  "sha256": "0" * 64, "role": "context_only"})
    if break_hash:
        for f in files:
            if f["file"].endswith(break_hash):
                f["sha256"] = "f" * 64
    manifest = {"version": "2026-09-28", "files": files,
                "counts": {"initial_upload": n_initial, "followup_evidence": 1}}
    out = pack / OLD_MANIFEST
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return pack


def _new_pack(tmp_path: Path, *, n_original: int = 3, n_new: int = 2,
              company_index: int = 2, break_hash: str | None = None,
              drop_company: bool = False) -> Path:
    """새 형식 팩: original_uploads + new_documents + initial/followup.total + dataset_id."""
    pack = tmp_path / "new_pack"
    original, new_docs = [], []
    for i in range(1, n_original + 1):
        rel = f"{NEW_INITIAL}/{i:02d}_문서.pdf"
        digest = _write_pdf(pack / rel, f"new-{i}")
        role = "evidence"
        if i == company_index and not drop_company:
            role = "company_answer"
        original.append({"source": f"{OLD_INITIAL}/{i:02d}_문서.pdf", "file": rel,
                         "sha256": digest, "copied_sha256": digest, "identical": True,
                         "role": role})
    rel = f"{NEW_FOLLOWUP}/13_보완.pdf"
    original.append({"source": f"{OLD_FOLLOWUP}/13_보완.pdf", "file": rel,
                     "sha256": _write_pdf(pack / rel, "new-f"), "copied_sha256": "",
                     "identical": True, "role": "evidence"})
    for j in range(1, n_new + 1):
        rel = f"{NEW_INITIAL}/{13 + j:02d}_신규.pdf"
        new_docs.append({"file": rel, "title": f"신규 {j}", "document_id": f"D{j}",
                         "role": "evidence", "pages": 2,
                         "sha256": _write_pdf(pack / rel, f"fresh-{j}")})
    # 업로드 금지 안내 문서 — 목록에는 있고 입력 폴더에는 없다.
    new_docs.append({"file": "00_안내와정답_업로드금지/00_안내.pdf", "title": "안내",
                     "document_id": "G", "role": "context_only", "pages": 1,
                     "sha256": "0" * 64})
    if break_hash:
        for f in original + new_docs:
            if f["file"].endswith(break_hash):
                f["sha256"] = "f" * 64
    initial_total = n_original + n_new
    manifest = {
        "dataset_id": "test_normal5_v1",
        "original_uploads": original, "new_documents": new_docs,
        "initial": {"total": initial_total, "evidence": initial_total - 1,
                    "company_answer": 1},
        "followup": {"total": initial_total + 1, "evidence": initial_total,
                     "company_answer": 1},
    }
    out = pack / NEW_MANIFEST
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return pack


# ── 옛 형식 (기본값으로 동작해야 한다) ──────────────────────────────────────
def test_old_format_initial(tmp_path):
    pack = _old_pack(tmp_path)
    files, meta = rehearsal.input_files(pack, "initial")
    assert [f["name"] for f in files] == ["01_문서.pdf", "02_문서.pdf", "03_문서.pdf"]
    assert sum(f["role"] == "company_answer" for f in files) == 1
    assert all(f["manifest_sha256_match"] for f in files)
    assert meta["manifest_version"] == "2026-09-28"
    assert meta["input_dirs"]["initial"] == OLD_INITIAL


def test_old_format_followup_adds_one(tmp_path):
    pack = _old_pack(tmp_path)
    files, _ = rehearsal.input_files(pack, "followup")
    assert len(files) == 4
    assert files[-1]["name"] == "99_보완.pdf"


def test_old_format_defaults_match_module_constants():
    """기본값이 예전 세트 값 그대로여야 기존 호출이 깨지지 않는다."""
    import inspect

    sig = inspect.signature(rehearsal.input_files)
    assert sig.parameters["initial_dir"].default == rehearsal.INITIAL_DIR
    assert sig.parameters["followup_dir"].default == rehearsal.FOLLOWUP_DIR
    assert sig.parameters["manifest_rel"].default == rehearsal.MANIFEST


# ── 새 형식 ──────────────────────────────────────────────────────────────────
def _new_kwargs():
    return {"initial_dir": NEW_INITIAL, "followup_dir": NEW_FOLLOWUP,
            "manifest_rel": NEW_MANIFEST}


def test_new_format_initial(tmp_path):
    pack = _new_pack(tmp_path)          # 원본 3 + 신규 2 = initial 5
    files, meta = rehearsal.input_files(pack, "initial", **_new_kwargs())
    assert len(files) == 5
    assert sum(f["role"] == "company_answer" for f in files) == 1
    assert all(f["manifest_sha256_match"] for f in files)
    assert meta["manifest_version"] == "test_normal5_v1"
    assert meta["input_dirs"]["followup"] == NEW_FOLLOWUP


def test_new_format_followup_total_is_cumulative(tmp_path):
    """새 형식의 followup.total은 누계다(initial + 보완)."""
    pack = _new_pack(tmp_path)
    files, _ = rehearsal.input_files(pack, "followup", **_new_kwargs())
    assert len(files) == 6
    assert files[-1]["name"] == "13_보완.pdf"


def test_new_format_context_only_not_included(tmp_path):
    """업로드 금지 문서는 입력에 들어가지 않는다."""
    pack = _new_pack(tmp_path)
    files, _ = rehearsal.input_files(pack, "initial", **_new_kwargs())
    assert not any("안내" in f["file"] for f in files)


# ── 공통 실패 조건 ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("builder,kwargs", [(_old_pack, {}), (_new_pack, _new_kwargs())])
def test_hash_mismatch_stops(tmp_path, builder, kwargs):
    pack = builder(tmp_path, break_hash="01_문서.pdf")
    with pytest.raises(SystemExit, match="해시 불일치"):
        rehearsal.input_files(pack, "initial", **kwargs)


def test_missing_company_answer_stops(tmp_path):
    """회사 답변이 0건이면 멈춘다."""
    pack = _new_pack(tmp_path, drop_company=True)
    with pytest.raises(SystemExit, match="회사 답변 1건 필요"):
        rehearsal.input_files(pack, "initial", **_new_kwargs())


def test_old_format_missing_company_answer_stops(tmp_path):
    pack = _old_pack(tmp_path, company_index=99)   # 어느 행도 company_answer가 아니다
    with pytest.raises(SystemExit, match="회사 답변 1건 필요"):
        rehearsal.input_files(pack, "initial")


def test_count_mismatch_stops(tmp_path):
    """폴더에 있는 문서 수가 구성 목록의 기대 수와 다르면 멈춘다."""
    pack = _new_pack(tmp_path)
    extra = pack / NEW_INITIAL / "99_목록에없음.pdf"
    _write_pdf(extra, "stray")
    with pytest.raises(SystemExit, match="구성 목록에 없는 입력"):
        rehearsal.input_files(pack, "initial", **_new_kwargs())


def test_file_in_manifest_but_missing_from_folder_counts_as_mismatch(tmp_path):
    """목록에는 있는데 폴더에 없으면 수가 모자라 멈춘다."""
    pack = _new_pack(tmp_path)
    (pack / NEW_INITIAL / "01_문서.pdf").unlink()
    with pytest.raises(SystemExit, match="입력 구성 불일치"):
        rehearsal.input_files(pack, "initial", **_new_kwargs())


# ── 형식 판별 헬퍼 ──────────────────────────────────────────────────────────
def test_manifest_entries_reads_both_formats():
    old = {"files": [{"file": "a", "role": "evidence", "sha256": "x"}]}
    new = {"original_uploads": [{"file": "a", "role": "evidence", "sha256": "x"}],
           "new_documents": [{"file": "b", "role": "context_only", "sha256": "y"}]}
    assert rehearsal.manifest_entries(old) == {"a": {"role": "evidence", "sha256": "x"}}
    assert rehearsal.manifest_entries(new) == {
        "a": {"role": "evidence", "sha256": "x"},
        "b": {"role": "context_only", "sha256": "y"}}


def test_manifest_entries_unknown_format_stops():
    with pytest.raises(SystemExit, match="입력 목록을 찾지 못했다"):
        rehearsal.manifest_entries({"something": 1})


def test_manifest_expected_old_is_additive():
    old = {"counts": {"initial_upload": 12, "followup_evidence": 1}}
    assert rehearsal.manifest_expected(old, "initial") == 12
    assert rehearsal.manifest_expected(old, "followup") == 13


def test_manifest_expected_new_is_cumulative():
    new = {"initial": {"total": 17}, "followup": {"total": 18}}
    assert rehearsal.manifest_expected(new, "initial") == 17
    assert rehearsal.manifest_expected(new, "followup") == 18


def test_manifest_expected_unknown_stops():
    with pytest.raises(SystemExit, match="문서 수를 찾지 못했다"):
        rehearsal.manifest_expected({}, "initial")


def test_manifest_version_prefers_version_then_dataset_id():
    assert rehearsal.manifest_version({"version": "v1", "dataset_id": "d"}) == "v1"
    assert rehearsal.manifest_version({"dataset_id": "d"}) == "d"
    assert rehearsal.manifest_version({}) == "unknown"
