"""보류 판단 적절성 평가 — 규칙 v2 (2026-10-15 정민 결정).

v1 채점기(`response_scoring`)의 결과 파일을 **바꾸지 않고** 그 위에 한 층을 더한다.

v1은 '답변 가능' 라벨인데 시스템이 보류한 행을 모두 `unnecessary_hold`로 센다.
v2는 두 축을 나눠 본다.

1. 값·근거 정확성 — 시스템 출력의 값·근거가 라벨과 맞는가(확정 여부와 무관).
2. 확정·보류 판단의 적절성 — v1 `unnecessary_hold` 행마다 사람이 남긴 판정 기록으로
   보류 사유가 성립하는지 본다.
   - ``justified``  → `justified_review_hold`(정당한 검토 보류, 오류로 세지 않는다)
   - ``unjustified`` → `unnecessary_hold`(보류 사유 불성립)
   - ``pending``·판정 기록 없음·승인 전 제안 → `hold_review_pending`(판단 대기)

판정은 행별 기록 파일(CSV)에서만 읽는다. 문항 ID·회사·값으로 분기하지 않는다.
승인되지 않은 판정은 집계에서 판단 대기로 센다 — 제안을 결과로 쓰지 않는다.
v1의 다른 집계 위치(정답 확정·잘못된 확정·올바른 보류 등)는 그대로 둔다.
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

RULE_VERSION = "hold_review_v2"
RULE_DECIDED = "2026-10-15 정민 결정"

B_UNNECESSARY = "unnecessary_hold"
B_JUSTIFIED = "justified_review_hold"
B_PENDING = "hold_review_pending"

VERDICTS = ("justified", "unjustified", "pending")
CRITERIA = {
    "J1": "회사 답변과 증빙의 지표·분모 차이를 시스템이 탐지해 검토로 넘겼다",
    "J2": "제품에 실제 제공된 정보만으로 요청 범위를 확정할 수 없어 보류했다",
    "U": "보류 사유가 성립하지 않는다(제공된 정보로 확정할 수 있었다)",
    "P": "보류 사유의 성립 여부를 판단할 근거가 부족하다",
}
VERDICT_CRITERIA = {"justified": {"J1", "J2"}, "unjustified": {"U"}, "pending": {"P"}}
APPROVALS = ("approved", "proposed")

REVIEW_COLUMNS = (
    "system", "run_id", "stage", "qid", "verdict", "criterion", "system_hold_basis",
    "assessment", "reviewer", "approval", "approved_by", "decided_at",
)
#: 선택 열 — 보류 판정과 별개로 시스템 설명의 오류를 기록한다(판정에 영향 없음).
OPTIONAL_COLUMNS = ("explanation_error",)


class ReviewError(ValueError):
    """판정 기록 파일이 규칙에 맞지 않는다."""


def load_reviews(path: str | Path) -> dict[tuple[str, str, str, str], dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in REVIEW_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ReviewError(f"판정 기록 열 누락: {missing}")
        out: dict[tuple[str, str, str, str], dict[str, str]] = {}
        for i, row in enumerate(reader, start=2):
            key = (row["system"].strip(), row["run_id"].strip(), row["stage"].strip(), row["qid"].strip())
            if not all(key):
                raise ReviewError(f"{i}행: system·run_id·stage·qid는 비울 수 없다")
            if key in out:
                raise ReviewError(f"{i}행: 중복 판정 {key}")
            verdict, crit, appr = row["verdict"].strip(), row["criterion"].strip(), row["approval"].strip()
            if verdict not in VERDICTS:
                raise ReviewError(f"{i}행: verdict {verdict!r}는 {VERDICTS} 중 하나여야 한다")
            if crit not in VERDICT_CRITERIA[verdict]:
                raise ReviewError(f"{i}행: verdict {verdict}에는 기준 {sorted(VERDICT_CRITERIA[verdict])}만 쓴다 ({crit!r})")
            if appr not in APPROVALS:
                raise ReviewError(f"{i}행: approval {appr!r}는 {APPROVALS} 중 하나여야 한다")
            if appr == "approved" and not row["approved_by"].strip():
                raise ReviewError(f"{i}행: 승인된 판정에는 approved_by가 필요하다")
            if not row["assessment"].strip():
                raise ReviewError(f"{i}행: 판단 근거(assessment)를 비울 수 없다")
            out[key] = {k: (v or "").strip() for k, v in row.items() if k}
        return out


def _doc_ids(score: dict[str, Any]) -> dict[str, tuple[str, str]]:
    ids: dict[str, tuple[str, str]] = {}
    for doc in score.get("documents", []):
        ids[doc["stage"]] = (doc["system"], doc["run_id"])
    return ids


def apply_v2(score: dict[str, Any], reviews: dict[tuple[str, str, str, str], dict[str, str]]) -> dict[str, Any]:
    """v1 결과에 v2 판정을 붙인 새 보고서를 만든다. 입력 dict는 바꾸지 않는다."""
    ids = _doc_ids(score)
    rows_out: list[dict[str, Any]] = []
    used: set[tuple[str, str, str, str]] = set()
    for row in score["rows"]:
        system, run_id = ids.get(row["stage"], ("", ""))
        key = (system, run_id, row["stage"], row["qid"])
        out = {
            "system": system, "run_id": run_id, "stage": row["stage"], "qid": row["qid"],
            "label_decision": row["label_decision"], "system_decision": row.get("system_decision"),
            "system_status": row.get("system_status"), "system_comparison": row.get("system_comparison"),
            "value_match": row.get("value_match"), "source_match": row.get("source_match"),
            "bucket_v1": row["bucket"], "bucket_v2": row["bucket"],
            "review": None, "bucket_v2_reason": "v1 집계 위치를 그대로 쓴다",
        }
        if row["bucket"] == B_UNNECESSARY:
            rev = reviews.get(key)
            if rev is None:
                out["bucket_v2"], out["bucket_v2_reason"] = B_PENDING, "판정 기록 없음 — 판단 대기"
            else:
                used.add(key)
                out["review"] = rev
                if rev["approval"] != "approved":
                    out["bucket_v2"], out["bucket_v2_reason"] = B_PENDING, f"승인 전 제안({rev['verdict']}/{rev['criterion']}) — 판단 대기"
                elif rev["verdict"] == "justified":
                    out["bucket_v2"], out["bucket_v2_reason"] = B_JUSTIFIED, f"정당한 검토 보류({rev['criterion']})"
                elif rev["verdict"] == "unjustified":
                    out["bucket_v2"], out["bucket_v2_reason"] = B_UNNECESSARY, "보류 사유 불성립(U)"
                else:
                    out["bucket_v2"], out["bucket_v2_reason"] = B_PENDING, "판단 근거 부족(P)"
        rows_out.append(out)
    unmatched = sorted(k for k in reviews if k not in used and k[:2] in {v for v in ids.values()})
    return {
        "rule_version": RULE_VERSION, "rule_decided": RULE_DECIDED, "criteria": CRITERIA,
        "v1_label_file": score.get("label_file"), "documents": score.get("documents", []),
        "v1_unresolved": score.get("bucket_counts", {}).get("unresolved", 0),
        "interpretation": [
            "v2는 제품 성능 향상이 아니라 기존 출력의 평가 기준을 보완한 재분류다.",
            "규칙은 평가 결과를 본 뒤 정했다(2026-10-15). v1 결과는 그대로 보존한다.",
            "판단 대기가 남은 보고 단위에서 '불필요한 보류 N건'은 판단 대기 건수와 함께만 읽는다.",
            "시스템 간 판단 대기 차이가 해소되지 않으면 정당한 보류 건수만으로 시스템 우열을 판단하지 않는다.",
        ],
        "rows": rows_out, "unused_reviews": [list(k) for k in unmatched],
        "scopes": _scopes(rows_out),
    }


def _ratio(n: int, d: int) -> dict[str, Any]:
    return {"numerator": n, "denominator": d, "value": (n / d) if d else None,
            "status": "산출" if d else "산출 불가(분모 0)"}


def _scope(rows: list[dict[str, Any]]) -> dict[str, Any]:
    answer_rows = [r for r in rows if r["label_decision"] == "answer"]
    accurate = [r for r in answer_rows if r["value_match"] is True and r["source_match"] is True]
    c2 = Counter(r["bucket_v2"] for r in rows)
    reviewed = [r for r in rows if r["bucket_v1"] == B_UNNECESSARY]
    pending = c2.get(B_PENDING, 0)
    expl = [f"{r['stage']}/{r['qid']}: {r['review']['explanation_error']}" for r in rows
            if r.get("review") and r["review"].get("explanation_error")]
    return {
        "A1_값근거_정확률": _ratio(len(accurate), len(answer_rows)) | {
            "note": "답변 가능 라벨 중 시스템 출력의 값·근거가 모두 정답인 비율. 확정 여부와 무관"},
        "M1_확정정답률": _ratio(c2.get("correct_answer", 0), len(answer_rows)) | {
            "note": "v1 M1과 같다 — 값·근거 정답이면서 확정한 행"},
        "판단_적절성": {
            "정답_확정": c2.get("correct_answer", 0),
            "정당한_검토_보류": c2.get(B_JUSTIFIED, 0),
            "불필요한_보류": c2.get(B_UNNECESSARY, 0),
            "판단_대기": c2.get(B_PENDING, 0),
            "잘못된_확정": c2.get("wrong_confirmation", 0),
            "올바른_보류(보류 라벨)": c2.get("correct_hold", 0),
        },
        "보류_검토대상": len(reviewed),
        "판단_대기_비율": _ratio(pending, len(reviewed)) | {
            "note": "v1에서 불필요한 보류로 집계돼 v2 판정 대상이 된 행 중 판단 대기 비율"},
        "설명_오류": expl,
        "bucket_v2_counts": dict(sorted(c2.items())),
        "rows": len(rows),
    }


def _scopes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    keys = sorted({(r["system"], r["run_id"], r["stage"]) for r in rows})
    for system, run_id, stage in keys:
        sub = [r for r in rows if (r["system"], r["run_id"], r["stage"]) == (system, run_id, stage)]
        out.append({"system": system, "run_id": run_id, "stage": stage, "aggregated": False, **_scope(sub)})
    stages = {k[2] for k in keys}
    if len(stages) > 1:
        for system, run_id in sorted({k[:2] for k in keys}):
            sub = [r for r in rows if (r["system"], r["run_id"]) == (system, run_id)]
            out.append({"system": system, "run_id": run_id, "stage": "+".join(sorted(stages)),
                        "aggregated": True, "note": "합산은 참고", **_scope(sub)})
    return out


def sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(score_path: str | Path, reviews_path: str | Path) -> dict[str, Any]:
    score = json.loads(Path(score_path).read_text(encoding="utf-8"))
    report = apply_v2(score, load_reviews(reviews_path))
    report["inputs"] = {"v1_score": {"path": str(score_path), "sha256": sha256(score_path)},
                        "reviews": {"path": str(reviews_path), "sha256": sha256(reviews_path)}}
    return report
