"""ESGenie `result.json` → 공통 답안 형식 v1 어댑터.

ESGenie 전용 의미(`status`·`comparison`·0-기준 페이지)를 공통 형식으로 옮기는 곳은
**이 파일 하나뿐**이다. 채점기(`response_scoring.py`)는 ESGenie 필드를 모른다.

지키는 것:

- **페이지 0→1 기준 변환은 `to_common_page()` 한 곳에서 한 번만 한다.**
  이미 1-기준인 대조군 응답은 이 어댑터를 거치지 않으므로 다시 변환되지 않는다.
  페이지가 없으면 `None`(미상)으로 남기고 첫 페이지로 간주하지 않는다.
- **원본 필드를 보존한다.** `source`에 `status`·`comparison`·사유를 그대로 둔다.
- **누락·파싱 실패를 보류로 바꾸지 않는다.** 응답 행이 없으면 행을 만들지 않고,
  읽을 수 없는 행은 `unparsed`로 남긴다.
- **미정 조합을 임의 배정하지 않는다.** `not_applicable` + 차단 비교는 계약에
  우선순위가 없어 `undetermined`로 남긴다.

제품 코드(`esgenie/supplychain/*`)는 수정하지 않는다 — 읽기만 한다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import answer_format as af

ADAPTER_NAME = "esgenie_result_json"
ADAPTER_VERSION = "1"

#: 값을 비교 불가로 만드는 비교 판정 — 이 중 하나면 확정으로 세지 않는다.
BLOCKING_COMPARISONS = frozenset({"scope_unconfirmed", "not_comparable", "mismatch"})


def to_common_page(system_page: Any) -> int | None:
    """ESGenie 0-기준 페이지 → 공통 형식 1-기준 페이지. **변환의 유일한 지점.**

    `None`·bool·비정수는 `None`(미상)으로 남긴다. 첫 페이지로 간주하지 않는다.
    """
    if system_page is None or isinstance(system_page, bool):
        return None
    try:
        return int(system_page) + 1
    except (TypeError, ValueError):
        return None


def classify_decision(status: Any, comparison: Any, value: Any) -> tuple[str, str]:
    """ESGenie `status`×`comparison` → 공통 응답 판정. (판정, 근거)를 낸다.

    작업지시서 A §6.1 매핑을 공통 판정 이름으로 옮긴 것이다. 의미를 바꾸지 않았다.
    `self_reported`는 어떤 경우에도 확정(`confirmed`)으로 올라가지 않는다.
    """
    status = str(status or "")
    comparison = str(comparison or "")
    blocking = comparison in BLOCKING_COMPARISONS

    if status == "not_applicable":
        if blocking:
            return (af.D_UNDETERMINED,
                    f"status=not_applicable과 차단 비교 '{comparison}'의 우선순위가 계약에 없다")
        return af.D_NOT_APPLICABLE, "status=not_applicable"
    if status == "verified" and af.is_filled(value) and not blocking:
        return af.D_CONFIRMED, "status=verified · 값 있음 · 차단 비교 없음"
    if status == "self_reported" and not blocking:
        return af.D_UNVERIFIED, "status=self_reported · 차단 비교 없음"
    if blocking:
        return af.D_HOLD, f"차단 비교 '{comparison}'"
    if status == "verified":
        return af.D_HOLD, "status=verified이나 값이 비어 있다"
    return af.D_HOLD, f"status={status or '(없음)'}"


def _links(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for link in raw or []:
        if not isinstance(link, dict):
            continue
        item: dict[str, Any] = {
            "file_name": str(link.get("file_name") or ""),
            "page": to_common_page(link.get("page")),
            "quote": link.get("quote") if isinstance(link.get("quote"), str) else None,
        }
        node_id = link.get("node_id")
        if isinstance(node_id, str) and node_id:
            item["node_id"] = node_id
        out.append(item)
    return out


def _or_none(raw: Any) -> str | None:
    """빈 문자열·`None`은 미상(`None`)으로 둔다. 값을 만들어 넣지 않는다."""
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _boundary(answer: dict[str, Any]) -> dict[str, Any]:
    """ESGenie 경계 필드 → 공통 `boundary`. 없는 값은 `null`(미상)로 남긴다."""
    raw = answer.get("boundary") if isinstance(answer.get("boundary"), dict) else {}
    period = _or_none(raw.get("period_text"))
    if period is None and answer.get("period") is not None:
        period = str(answer["period"])
    completeness = _or_none(answer.get("completeness")) or _or_none(raw.get("completeness"))
    if completeness not in af.COMPLETENESS:
        completeness = None if completeness is None else "unknown"
    return {
        "period": period,
        "site": _or_none(raw.get("site")),
        "target": _or_none(raw.get("measure")),
        "denominator": _or_none(raw.get("denominator")),
        "label": _or_none(answer.get("boundary_label")),
        "completeness": completeness,
    }


def convert_answer(answer: Any) -> dict[str, Any]:
    """`sheet.answers[]` 한 행 → 공통 형식 응답 한 행."""
    if not isinstance(answer, dict):
        return af.new_answer(qid="", decision=af.D_UNPARSED,
                            decision_basis="응답 행이 객체가 아니다")
    qid = str(answer.get("qid") or "")
    if not qid:
        return af.new_answer(qid="", decision=af.D_UNPARSED,
                            decision_basis="응답 행에 qid가 없다")

    decision, basis = classify_decision(
        answer.get("status"), answer.get("comparison"), answer.get("value"))
    source = {
        "status": str(answer.get("status") or ""),
        "comparison": str(answer.get("comparison") or ""),
    }
    for key in ("comparison_reason", "rationale"):
        text = _or_none(answer.get(key))
        if text:
            source[key] = text
    flags = answer.get("flags")
    if isinstance(flags, list) and flags:
        source["flags"] = [str(f) for f in flags]

    return af.new_answer(
        qid=qid, decision=decision, decision_basis=basis,
        value=answer.get("value") if isinstance(
            answer.get("value"), (int, float, bool, str, type(None))) else None,
        unit=str(answer.get("unit") or ""),
        value_text=_or_none(answer.get("display_value")),
        boundary=_boundary(answer),
        evidence=_links(answer.get("evidence_links")),
        references=_links(answer.get("reference_links")),
        source=source,
        notes=_or_none(answer.get("review_note")),
    )


def convert_result(
    result: dict[str, Any], *, stage: str, run_id: str, framework: str,
    data_source: str, system: str = "esgenie", model: str | None = None,
    source_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`result.json` → 공통 형식 문서 한 개(한 시스템·한 실행·한 단계)."""
    if stage not in af.STAGES:
        raise ValueError(f"stage '{stage}'는 {af.STAGES} 중 하나가 아니다")
    sheet = result.get("sheet")
    if not isinstance(sheet, dict):
        raise af.FormatError("result.json에 'sheet'가 없다")
    answers = sheet.get("answers")
    if not isinstance(answers, list):
        raise af.FormatError("result.json의 sheet에 'answers'가 없다")

    # 목록 값은 형태가 달라 value에 담지 않고 원문을 source에 남긴다
    # (rba42에는 multi_select 문항이 없다 — 다른 양식에서만 생긴다).
    rows = []
    for raw in answers:
        row = convert_answer(raw)
        if isinstance(raw, dict) and isinstance(raw.get("value"), (list, tuple)):
            row.setdefault("source", {})["value_list"] = [str(v) for v in raw["value"]]
        rows.append(row)

    return af.new_document(
        system=system, run_id=run_id, stage=stage, framework=framework,
        data_source=data_source, model=model,
        produced_at=datetime.now(timezone.utc).isoformat(),
        adapter={"name": ADAPTER_NAME, "version": ADAPTER_VERSION},
        source_ref=source_ref, answers=rows,
    )


def convert_result_file(
    path: str | Path, *, stage: str, run_id: str, framework: str, data_source: str,
    system: str = "esgenie", model: str | None = None,
) -> dict[str, Any]:
    """`result.json` 파일을 읽어 변환한다. `source_ref`에 원본 경로·해시를 남긴다."""
    path = Path(path)
    raw = path.read_bytes()
    try:
        result = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise af.FormatError(f"{path}: JSON 파싱 실패 — {exc}") from exc
    return convert_result(
        result, stage=stage, run_id=run_id, framework=framework,
        data_source=data_source, system=system, model=model,
        source_ref={"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()},
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="ESGenie result.json을 공통 답안 형식 v1로 변환한다")
    ap.add_argument("--result", required=True, help="ESGenie result.json 경로")
    ap.add_argument("--stage", required=True, choices=list(af.STAGES))
    ap.add_argument("--run-id", required=True, help="실행 식별자 (실행별로 달라야 한다)")
    ap.add_argument("--framework", default="rba42")
    ap.add_argument("--data-source", required=True,
                    help="응답이 무엇을 보고 나왔는가 (예: 촬영증빙_12건)")
    ap.add_argument("--model", default=None)
    ap.add_argument("--out", required=True, help="공통 형식 JSON 출력 경로")
    args = ap.parse_args(argv)

    doc = convert_result_file(
        args.result, stage=args.stage, run_id=args.run_id, framework=args.framework,
        data_source=args.data_source, model=args.model)
    af.dump_document(doc, args.out)
    print(f"wrote {args.out} ({len(doc['answers'])} answers)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
