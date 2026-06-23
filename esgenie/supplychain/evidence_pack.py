"""증빙 원본을 출력 폴더의 evidence_pack/ 로 복사 — PDF 증빙 부록의 전제.

exporters/pdf.py의 증빙 부록은 EvidenceLink.relative_path('evidence_pack/foo.pdf')를
out_dir 기준으로 resolve해 원본 페이지를 임베드한다. 그런데 supplychain 응답서는
SSOT 엑셀(excel_exporter)과 달리 원본을 자동 복사하지 않아 evidence_pack가 비어
있었다 → 부록이 항상 스킵됐다. 이 모듈이 응답서가 참조하는 원본만 골라 복사한다.

SSOT `excel_exporter._copy_evidence`와 같은 계약: uploaded_files = {파일명: 원본절대경로}.
세션의 upload_paths(st.session_state)가 정확히 이 형태다.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


def _referenced_names(sheet: Any) -> set[str]:
    """응답서가 실제로 참조하는 증빙 파일명 집합(relative_path basename + file_name)."""
    names: set[str] = set()
    for a in getattr(sheet, "answers", []):
        for e in getattr(a, "evidence_links", []):
            rel = getattr(e, "relative_path", "") or ""
            if rel:
                names.add(Path(rel).name)
            fn = getattr(e, "file_name", "") or ""
            if fn:
                names.add(fn)
    return {n for n in names if n}


def copy_evidence_pack(
    sheet: Any,
    out_dir: str | Path,
    uploaded_files: dict[str, str] | None,
) -> list[str]:
    """응답서가 참조하는 원본 증빙을 out_dir/evidence_pack/ 로 복사.

    Args:
        sheet: ResponseSheet (evidence_links로 참조 파일을 알아냄).
        out_dir: 응답서 출력 폴더(여기 아래에 evidence_pack/ 생성).
        uploaded_files: {파일명: 원본 절대경로}. None/빈값이면 아무것도 안 함.

    Returns:
        복사한 파일명 리스트(정렬·중복제거). 원본이 없거나 매칭 안 되면 빈 리스트.

    설계: 부수효과 최소화 — 복사할 게 없으면 디렉토리도 만들지 않는다. 개별 복사
    실패(권한·삭제 등)는 건너뛰고 계속한다(export 전체를 막지 않게).
    """
    if not uploaded_files:
        return []
    wanted = _referenced_names(sheet)
    if not wanted:
        return []

    pack_dir = Path(out_dir) / "evidence_pack"
    copied: list[str] = []
    for name in sorted(wanted):
        src = uploaded_files.get(name)
        if not src:
            continue
        src_path = Path(src)
        try:
            if not src_path.is_file():
                continue
            pack_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, pack_dir / name)
            copied.append(name)
        except OSError:
            continue
    return sorted(set(copied))
