"""공통 답안 형식 v1 — 평가 도구가 읽는 중립 형식.

ESGenie와 일반 AI 대조군의 응답을 **같은 의미로** 비교하기 위한 형식이다.
형식 문서: `docs/공통답안형식_v1초안_2026-10-06.md` (v1 초안 / 합의 대기).

이 모듈이 하는 일은 두 가지뿐이다.

1. 형식 상수·허용값 정의
2. 문서 검증(`validate_document`) — 필수 필드·자료형·허용값·중복·누락·알 수 없는 qid

**페이지는 1-기준이다.** ESGenie의 0-기준 페이지는 어댑터
(`esgenie/eval/esgenie_adapter.py`)에서 한 번만 +1 한다. 이 모듈은 변환하지 않는다.

파싱 실패와 누락은 보류로 바꾸지 않는다. 파싱 실패는 `decision="unparsed"`로 남기고,
누락된 qid는 행을 만들지 않고 검증에서 누락으로 드러낸다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: 형식 버전. 합의 전이라 `-draft` 접미사를 붙인다.
FORMAT_VERSION = "1.0-draft"
#: 이 코드가 읽을 수 있는 버전(주 버전이 같아야 한다).
SUPPORTED_MAJOR = "1"

STAGES = ("initial", "followup")

#: 공통 응답 판정 — "그 시스템이 무엇을 답변으로 제출했는가".
#: 정답 여부가 아니다. 정답은 라벨로만 판정한다.
D_CONFIRMED = "confirmed"                      # 확정: 근거로 검증했다고 제출
D_UNVERIFIED = "unverified_submitted"          # 미검증 전달: 값은 냈으나 보증하지 않음
D_HOLD = "hold"                                # 보류: 값을 제출하지 않음
D_NOT_APPLICABLE = "not_applicable"            # 해당 없음
D_UNPARSED = "unparsed"                        # 원출력을 읽을 수 없음(파싱 실패)
#: 원출력은 읽었으나 공통 판정으로 옮기는 규칙이 계약에 없는 경우.
#: `unparsed`(읽을 수 없음)와 구분한다. 어느 한쪽으로 임의 배정하지 않기 위해 있다.
D_UNDETERMINED = "undetermined"
DECISIONS = (D_CONFIRMED, D_UNVERIFIED, D_HOLD, D_NOT_APPLICABLE,
             D_UNPARSED, D_UNDETERMINED)

COMPLETENESS = ("total", "partial", "unknown")

META_REQUIRED = ("system", "run_id", "stage", "framework", "data_source")
META_OPTIONAL = ("model", "produced_at", "adapter", "source_ref", "notice")
ANSWER_REQUIRED = ("qid", "decision")
BOUNDARY_FIELDS = ("period", "site", "target", "denominator", "label", "completeness")


class FormatError(ValueError):
    """공통 형식 문서를 읽을 수 없을 때(파일 파싱 실패·최상위 구조 위반)."""


def is_filled(value: Any) -> bool:
    """값이 채워졌는가. `0`·`False`는 **채워진 값**이다(빈 값으로 세지 않는다)."""
    if value is None:
        return False
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return True


def document_key(meta: dict[str, Any]) -> tuple[str, str, str]:
    """여러 실행을 함께 다룰 때의 문서 식별 — (system, run_id, stage).

    파일 내부의 행 식별은 `(stage, qid)`다. 문서 키를 앞에 붙이면 실행 간 충돌이 없다.
    """
    return (str(meta.get("system") or ""), str(meta.get("run_id") or ""),
            str(meta.get("stage") or ""))


def row_key(meta: dict[str, Any], qid: str) -> tuple[str, str, str, str]:
    """여러 실행을 함께 다룰 때의 행 식별 — (system, run_id, stage, qid)."""
    system, run_id, stage = document_key(meta)
    return (system, run_id, stage, qid)


def load_document(path: str | Path) -> dict[str, Any]:
    """공통 형식 파일을 읽는다. 파싱 실패는 보류로 바꾸지 않고 FormatError로 올린다."""
    path = Path(path)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FormatError(f"{path}: JSON 파싱 실패 — {exc}") from exc
    if not isinstance(doc, dict):
        raise FormatError(f"{path}: 최상위가 객체가 아니다")
    return doc


def dump_document(doc: dict[str, Any], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _issue(code: str, where: str, detail: str) -> dict[str, str]:
    return {"code": code, "where": where, "detail": detail}


def _check_evidence(items: Any, where: str, field: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if items is None:
        return out
    if not isinstance(items, list):
        return [_issue("bad_type", where, f"{field}는 배열이어야 한다")]
    for i, item in enumerate(items):
        at = f"{where}.{field}[{i}]"
        if not isinstance(item, dict):
            out.append(_issue("bad_type", at, "근거 항목은 객체여야 한다"))
            continue
        name = item.get("file_name")
        if not isinstance(name, str) or not name.strip():
            out.append(_issue("missing_field", at, "file_name이 비어 있다"))
        page = item.get("page")
        if page is None:
            pass  # 미상 — 만들어 넣지 않는다
        elif isinstance(page, bool) or not isinstance(page, int):
            out.append(_issue("bad_type", at, "page는 1-기준 정수 또는 null이어야 한다"))
        elif page < 1:
            out.append(_issue("bad_value", at, f"page {page} — 1-기준이므로 1 이상이어야 한다"))
        quote = item.get("quote")
        if quote is not None and not isinstance(quote, str):
            out.append(_issue("bad_type", at, "quote는 문자열 또는 null이어야 한다"))
    return out


def validate_document(
    doc: dict[str, Any], expected_qids: tuple[str, ...] | None = None,
) -> list[dict[str, str]]:
    """문서를 검증해 문제 목록을 낸다. 빈 목록이면 통과.

    `expected_qids`를 주면 알 수 없는 qid·누락 qid·응답 수를 함께 본다.
    문항 목록은 호출자가 양식에서 읽어 넘긴다(여기에 문항 ID를 박지 않는다).
    """
    issues: list[dict[str, str]] = []

    version = doc.get("format_version")
    if not isinstance(version, str) or not version:
        issues.append(_issue("missing_field", "format_version", "형식 버전이 없다"))
    elif version.split(".")[0] != SUPPORTED_MAJOR:
        issues.append(_issue("unsupported_version", "format_version",
                             f"'{version}'의 주 버전이 {SUPPORTED_MAJOR}이 아니다"))

    meta = doc.get("meta")
    if not isinstance(meta, dict):
        issues.append(_issue("missing_field", "meta", "meta가 없다"))
        meta = {}
    for key in META_REQUIRED:
        value = meta.get(key)
        if not isinstance(value, str) or not value.strip():
            issues.append(_issue("missing_field", f"meta.{key}", "비어 있다"))
    if isinstance(meta.get("stage"), str) and meta["stage"] not in STAGES:
        issues.append(_issue("bad_value", "meta.stage", f"{STAGES} 중 하나여야 한다"))

    answers = doc.get("answers")
    if not isinstance(answers, list):
        issues.append(_issue("missing_field", "answers", "answers 배열이 없다"))
        return issues

    seen: dict[str, int] = {}
    for i, a in enumerate(answers):
        where = f"answers[{i}]"
        if not isinstance(a, dict):
            issues.append(_issue("bad_type", where, "응답 항목은 객체여야 한다"))
            continue
        qid = a.get("qid")
        if not isinstance(qid, str) or not qid.strip():
            issues.append(_issue("missing_field", f"{where}.qid", "qid가 비어 있다"))
        else:
            seen[qid] = seen.get(qid, 0) + 1
            where = f"answers[{i}]({qid})"

        decision = a.get("decision")
        if decision not in DECISIONS:
            issues.append(_issue("bad_value", f"{where}.decision",
                                 f"'{decision}'는 {DECISIONS} 중 하나가 아니다"))

        if not isinstance(a.get("value"), (int, float, bool, str, type(None))):
            issues.append(_issue("bad_type", f"{where}.value",
                                 "value는 수치·참거짓·문자열·null이어야 한다"))
        unit = a.get("unit", "")
        if not isinstance(unit, str):
            issues.append(_issue("bad_type", f"{where}.unit", "unit은 문자열이어야 한다"))

        boundary = a.get("boundary")
        if boundary is not None:
            if not isinstance(boundary, dict):
                issues.append(_issue("bad_type", f"{where}.boundary", "boundary는 객체여야 한다"))
            else:
                for key, value in boundary.items():
                    if key not in BOUNDARY_FIELDS:
                        issues.append(_issue("unknown_field", f"{where}.boundary.{key}",
                                             f"허용 필드는 {BOUNDARY_FIELDS}다"))
                    elif value is not None and not isinstance(value, str):
                        issues.append(_issue("bad_type", f"{where}.boundary.{key}",
                                             "문자열 또는 null(미상)이어야 한다"))
                comp = boundary.get("completeness")
                if comp is not None and comp not in COMPLETENESS:
                    issues.append(_issue("bad_value", f"{where}.boundary.completeness",
                                         f"{COMPLETENESS} 중 하나 또는 null이어야 한다"))

        issues += _check_evidence(a.get("evidence"), where, "evidence")
        issues += _check_evidence(a.get("references"), where, "references")

        source = a.get("source")
        if source is not None and not isinstance(source, dict):
            issues.append(_issue("bad_type", f"{where}.source", "source는 객체여야 한다"))

    for qid, n in sorted(seen.items()):
        if n > 1:
            issues.append(_issue("duplicate_qid", f"answers({qid})", f"{n}번 나온다"))

    if expected_qids is not None:
        expected = set(expected_qids)
        for qid in sorted(set(seen) - expected):
            issues.append(_issue("unknown_qid", f"answers({qid})", "양식에 없는 qid다"))
        for qid in sorted(expected - set(seen)):
            issues.append(_issue("missing_qid", f"answers({qid})", "응답이 없다"))
        if len(answers) != len(expected):
            issues.append(_issue("row_count", "answers",
                                 f"{len(answers)}행 — 정상 실행은 {len(expected)}행이다"))
    return issues


def new_document(
    *, system: str, run_id: str, stage: str, framework: str, data_source: str,
    model: str | None = None, produced_at: str | None = None,
    adapter: dict[str, Any] | None = None, source_ref: dict[str, Any] | None = None,
    notice: str | None = None, answers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """빈 공통 형식 문서 한 개(한 시스템·한 실행·한 단계)."""
    meta: dict[str, Any] = {
        "system": system, "run_id": run_id, "stage": stage,
        "framework": framework, "data_source": data_source,
    }
    for key, value in (("model", model), ("produced_at", produced_at),
                       ("adapter", adapter), ("source_ref", source_ref),
                       ("notice", notice)):
        if value is not None:
            meta[key] = value
    return {"format_version": FORMAT_VERSION, "meta": meta, "answers": answers or []}


def new_answer(
    *, qid: str, decision: str, decision_basis: str = "",
    value: Any = None, unit: str = "", value_text: str | None = None,
    boundary: dict[str, Any] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    references: list[dict[str, Any]] | None = None,
    source: dict[str, Any] | None = None, notes: str | None = None,
) -> dict[str, Any]:
    """공통 형식 응답 한 행."""
    row: dict[str, Any] = {
        "qid": qid, "decision": decision, "value": value, "unit": unit,
        "evidence": list(evidence or []),
    }
    if decision_basis:
        row["decision_basis"] = decision_basis
    if value_text is not None:
        row["value_text"] = value_text
    if boundary is not None:
        row["boundary"] = boundary
    if references:
        row["references"] = list(references)
    if source is not None:
        row["source"] = source
    if notes is not None:
        row["notes"] = notes
    return row
