"""저장된 덤프로 search_terms 변경의 D1 귀속·alias 영향을 비교한다.

라이브 API를 호출하지 않는다. 변경 전에는 ``--snapshot``으로 기준선을 저장하고,
변경 후에는 ``--compare``로 현재 결과와 비교한다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ARTIFACT_ROOT = ROOT

DUMP_PATHS = (
    "outputs/lp_009150_E.json",
    "outputs/lp_051910_E.json",
    "outputs/lp_055550_E.json",
    "outputs/lp_035420_E.json",
    "outputs/ledger_provenance_012330_v3.json",
)
PROBES = (
    "폐기물 발생량은 72,463톤이다.",
    "여성 직원 비율은 24.2%이다.",
    "이사 출석률은 97.5%이다.",
    "이사 평균 참석률은 97.5%이다.",
    "사내이사 출석률은 95%이다.",
)
PROBE_LABELS = (
    "폐기물 발생량",
    "여성 직원 비율",
    "이사 출석률",
    "이사 평균 참석률",
    "사내이사 출석률",
)


def _walk(value: Any) -> Iterator[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _collect_dump_labels() -> tuple[set[str], list[str]]:
    labels: set[str] = set(PROBE_LABELS)
    missing: list[str] = []
    for rel in DUMP_PATHS:
        path = ARTIFACT_ROOT / rel
        if not path.exists():
            missing.append(rel)
            continue
        for key, value in _walk(_read_json(path)):
            if key in {"hint", "metric_hint"} and isinstance(value, str):
                if value.strip():
                    labels.add(value.strip())
    return labels, missing


def _collect_audit_sentences(
    *, audit_cutoff: str | None = None, modified_before: float | None = None
) -> tuple[list[str], list[str]]:
    sentences: list[str] = []
    paths = sorted((ARTIFACT_ROOT / "outputs").glob("audit_trace*.json"))
    if audit_cutoff is not None:
        paths = [
            path
            for path in paths
            if (match := re.search(r"_(20\d{6})_(\d{6})\.json$", path.name))
            and "".join(match.groups()) <= audit_cutoff
        ]
    if modified_before is not None:
        paths = [path for path in paths if path.stat().st_mtime <= modified_before]
    for path in paths:
        for key, value in _walk(_read_json(path)):
            if key == "sentence_text" and isinstance(value, str):
                sentences.append(value)
    relative_paths = [str(path.relative_to(ARTIFACT_ROOT)) for path in paths]
    return sentences, relative_paths


def _assignments(sentence: str) -> list[str | None]:
    from esgenie.layer3_detect import _NUMBER_PATTERN, _match_topic_near

    return [
        _match_topic_near(sentence, match.start(), match.end())[1]
        for match in _NUMBER_PATTERN.finditer(sentence)
    ]


def snapshot(
    *,
    audit_sentences: list[str] | None = None,
    audit_cutoff: str | None = None,
    modified_before: float | None = None,
) -> dict[str, Any]:
    from esgenie.knowledge.kesg_items import resolve_kesg_code
    from esgenie.layer3_detect import _build_topic_terms

    labels, missing = _collect_dump_labels()
    if audit_sentences is None:
        sentences, audit_paths = _collect_audit_sentences(
            audit_cutoff=audit_cutoff, modified_before=modified_before
        )
    else:
        sentences, audit_paths = audit_sentences, []
    assigned = unassigned = 0
    audit_assignments: list[list[str | None]] = []
    for sentence in sentences:
        sentence_assignments = _assignments(sentence)
        audit_assignments.append(sentence_assignments)
        for code in sentence_assignments:
            assigned += code is not None
            unassigned += code is None

    return {
        "topic_term_count": len(_build_topic_terms()),
        "audit_file_count": len(audit_paths),
        "audit_cutoff": audit_cutoff,
        "audit_paths": audit_paths,
        # 비교 시 테스트가 새 trace를 만들어도 동일 입력을 재평가한다.
        "audit_sentences": sentences,
        "audit_assignments": audit_assignments,
        "audit_sentence_count": len(sentences),
        "assigned_number_count": assigned,
        "unassigned_number_count": unassigned,
        "missing_dumps": missing,
        "aliases": {label: list(resolve_kesg_code(label)) for label in sorted(labels)},
        "probes": {sentence: _assignments(sentence) for sentence in PROBES},
    }


def _print_comparison(before: dict[str, Any], after: dict[str, Any]) -> None:
    print("| 지표 | 변경 전 | 변경 후 | 차이 |")
    print("|---|---:|---:|---:|")
    for key, label in (
        ("assigned_number_count", "audit 숫자 귀속 성공"),
        ("unassigned_number_count", "audit 숫자 귀속 실패"),
        ("topic_term_count", "토픽 용어 수"),
    ):
        old, new = before[key], after[key]
        print(f"| {label} | {old} | {new} | {new - old:+d} |")

    changed = {
        label: (old, after["aliases"].get(label))
        for label, old in before["aliases"].items()
        if after["aliases"].get(label) != old
    }
    print("\nalias 변화:")
    if not changed:
        print("- 없음")
    for label, (old, new) in changed.items():
        print(f"- `{label}`: `{old}` → `{new}`")

    before_assignments = before.get("audit_assignments", [])
    after_assignments = after.get("audit_assignments", [])
    if before_assignments and len(before_assignments) == len(after_assignments):
        changed_sentences = [
            (sentence, old, new)
            for sentence, old, new in zip(
                before["audit_sentences"], before_assignments, after_assignments
            )
            if old != new
        ]
        print(f"\naudit 귀속 변화 문장: {len(changed_sentences)}건")
        for sentence, old, new in changed_sentences:
            print(f"- `{sentence}`: `{old}` → `{new}`")

    print("\n고정 probe 변화:")
    for sentence, old in before["probes"].items():
        new = after["probes"].get(sentence)
        if new != old:
            print(f"- `{sentence}`: `{old}` → `{new}`")

    missing = after["missing_dumps"]
    if missing:
        print("\n누락 덤프:")
        for path in missing:
            print(f"- `{path}`")


def main() -> None:
    global ARTIFACT_ROOT

    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--snapshot", type=Path, help="현재 결과를 JSON 기준선으로 저장")
    group.add_argument("--compare", type=Path, help="저장한 기준선과 현재 결과를 비교")
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=ROOT,
        help="outputs가 들어 있는 artifact 루트(기본값: 저장소 루트)",
    )
    parser.add_argument(
        "--code-root",
        type=Path,
        default=ROOT,
        help="비교할 esgenie 코드 루트(기본값: 현재 저장소 루트)",
    )
    parser.add_argument(
        "--audit-cutoff",
        help="파일명 시각 YYYYMMDDHHMMSS 이하의 root audit_trace만 사용",
    )
    args = parser.parse_args()
    ARTIFACT_ROOT = args.artifact_root.resolve()
    code_root = args.code_root.resolve()
    sys.path[:] = [
        path for path in sys.path if path not in {str(ROOT), str(code_root)}
    ]
    sys.path.insert(0, str(code_root))
    if args.audit_cutoff and not re.fullmatch(r"20\d{12}", args.audit_cutoff):
        parser.error("--audit-cutoff은 YYYYMMDDHHMMSS 형식이어야 합니다")

    if args.snapshot:
        current = snapshot(audit_cutoff=args.audit_cutoff)
        args.snapshot.write_text(
            json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(current, ensure_ascii=False, indent=2))
        return

    before = _read_json(args.compare)
    # 최신 snapshot은 문장 자체를 보존한다. 구버전 snapshot은 파일 생성시각을
    # cutoff로 사용해 이후 테스트가 만든 trace를 비교 입력에서 제외한다.
    current = snapshot(
        audit_sentences=before.get("audit_sentences"),
        audit_cutoff=before.get("audit_cutoff"),
        modified_before=None if before.get("audit_sentences") else args.compare.stat().st_mtime,
    )
    _print_comparison(before, current)


if __name__ == "__main__":
    main()
