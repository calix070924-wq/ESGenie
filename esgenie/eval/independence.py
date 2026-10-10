"""독립 검토 인정 — 노출된 행을 독립 일치율에서 제외한다.

확정된 규칙(2026-10-06 정민 지시):

- **시스템 답변 또는 기대 정답에 노출된 것으로 확인된 행은, 일치·불일치와 무관하게
  독립 일치율의 분자·분모에서 모두 제외한다.**
- **표본 자체는 다시 뽑지 않는다.** 제외는 집계에서만 한다.
- **제외한 라벨을 폐기하지 않는다.** 비독립 검토·협의 기록으로 보존한다.
- **전체 PR에 접근했다는 이유만으로 모든 문항을 일괄 제외하지 않는다.** 노출은
  사람·단계·문항 단위로 확인된 것만 센다.

이 모듈이 **하지 않는** 것:

- 일치율을 계산하지 않는다. 라벨이 없고, 일치 판정 산식도 이 모듈의 일이 아니다.
  여기서는 **어느 행이 분모에 들어가는가**만 가른다.
- 노출을 추측하지 않는다. 노출 기록에 적힌 것만 노출로 본다. 기록이 없는 행은
  `pending`(확인 대기)이고 `independent`가 아니다 — **확인 전에는 독립으로 세지 않는다.**
- 제외를 0으로 표시하지 않는다. 제외 건수와 이유를 따로 돌려준다.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: 노출 기록 CSV의 열. `labeler`·`stage`·`qid` 단위로 적는다.
EXPOSURE_COLUMNS = (
    "labeler",      # 누가 보았는가 (라벨 작성자 ID)
    "stage",        # initial | followup | all
    "qid",          # 문항 ID (일괄 제외를 막기 위해 문항 단위로 적는다)
    "exposure",     # 무엇에 노출됐는가 — 아래 EXPOSURE_KINDS
    "source",       # 어디서 보았는가 (문서·PR 등)
    "confirmed_by",  # 누가 확인했는가 (본인 신고 / 제3자 확인)
    "note",
)

#: 노출 종류. `none`은 "보지 않았다고 확인됨"이다 — 기록이 없는 것과 다르다.
EXPOSURE_KINDS = (
    "expected_value",    # 기대 정답(수치·판정)을 보았다
    "system_answer",     # 시스템 답변을 보았다
    "both",
    "none",
)

#: `stage`에 이 값을 쓰면 그 문항의 모든 단계에 적용한다. 문항 단위는 유지된다.
ALL_STAGES = "all"

#: 행 상태.
INDEPENDENT = "independent"   # 보지 않았다고 확인됨 → 분모에 넣는다
EXCLUDED = "excluded"         # 노출 확인됨 → 분자·분모에서 뺀다
PENDING = "pending"           # 확인 대기 → 아직 분모에 넣지 않는다


class ExposureError(ValueError):
    """노출 기록을 읽을 수 없을 때. 조용히 넘기지 않는다."""


@dataclass(frozen=True)
class Exposure:
    labeler: str
    stage: str
    qid: str
    exposure: str
    source: str = ""
    confirmed_by: str = ""
    note: str = ""

    def covers(self, stage: str, qid: str) -> bool:
        return self.qid == qid and self.stage in (stage, ALL_STAGES)


@dataclass
class RowStatus:
    stage: str
    qid: str
    status: str
    reason: str
    exposures: list[dict[str, str]] = field(default_factory=list)


@dataclass
class IndependenceReport:
    """집합 하나의 독립 검토 인정 상태. **일치율은 들어 있지 않다.**"""
    set_id: str
    labeler: str
    sample_rows: int = 0
    independent_rows: int = 0
    excluded_rows: int = 0
    pending_rows: int = 0
    rows: list[RowStatus] = field(default_factory=list)
    excluded_qids: list[str] = field(default_factory=list)
    pending_reasons: dict[str, int] = field(default_factory=dict)
    agreement_denominator: int = 0
    agreement_blocked_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "set_id": self.set_id,
            "labeler": self.labeler,
            "sample_rows": self.sample_rows,
            "independent_rows": self.independent_rows,
            "excluded_rows": self.excluded_rows,
            "pending_rows": self.pending_rows,
            "excluded_qids": self.excluded_qids,
            "pending_reasons": self.pending_reasons,
            "agreement_denominator": self.agreement_denominator,
            "agreement_blocked_reason": self.agreement_blocked_reason,
            "rows": [
                {"stage": r.stage, "qid": r.qid, "status": r.status,
                 "reason": r.reason, "exposures": r.exposures}
                for r in self.rows
            ],
        }


def parse_exposure_row(row: dict[str, str], line: int) -> Exposure:
    missing = [c for c in ("labeler", "stage", "qid", "exposure")
               if not (row.get(c) or "").strip()]
    if missing:
        raise ExposureError(f"{line}행: 필수 열이 비었다 {missing}")
    stage = row["stage"].strip()
    if stage != ALL_STAGES and stage not in ("initial", "followup"):
        raise ExposureError(f"{line}행: stage '{stage}'는 initial·followup·{ALL_STAGES} 중 하나가 아니다")
    kind = row["exposure"].strip()
    if kind not in EXPOSURE_KINDS:
        raise ExposureError(f"{line}행: exposure '{kind}'는 {EXPOSURE_KINDS} 중 하나가 아니다")
    return Exposure(
        labeler=row["labeler"].strip(), stage=stage, qid=row["qid"].strip(),
        exposure=kind, source=(row.get("source") or "").strip(),
        confirmed_by=(row.get("confirmed_by") or "").strip(),
        note=(row.get("note") or "").strip(),
    )


def load_exposures(path: str | Path) -> list[Exposure]:
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        header = tuple(reader.fieldnames or ())
        missing = [c for c in EXPOSURE_COLUMNS if c not in header]
        if missing:
            raise ExposureError(f"노출 기록에 열이 없다: {missing}")
        return [parse_exposure_row(row, i) for i, row in enumerate(reader, start=2)]


def classify(sample_rows: list[tuple[str, str]], exposures: list[Exposure],
             labeler: str, set_id: str = "") -> IndependenceReport:
    """표본 행을 독립/제외/대기로 가른다.

    `sample_rows`는 `(stage, qid)` 목록이다 — 표본 패키지의 `sample.json`에서 온다.
    표본을 다시 뽑지 않으므로 이 목록은 바뀌지 않는다.
    """
    mine = [e for e in exposures if e.labeler == labeler]
    rep = IndependenceReport(set_id=set_id, labeler=labeler,
                             sample_rows=len(sample_rows))
    excluded_qids: set[str] = set()

    for stage, qid in sample_rows:
        hits = [e for e in mine if e.covers(stage, qid)]
        seen = [e for e in hits if e.exposure != "none"]
        cleared = [e for e in hits if e.exposure == "none"]

        if seen:
            kinds = sorted({e.exposure for e in seen})
            status, reason = EXCLUDED, (
                f"노출 확인됨({','.join(kinds)}) — 일치·불일치와 무관하게 분자·분모에서 제외")
            excluded_qids.add(qid)
        elif cleared:
            status, reason = INDEPENDENT, "보지 않았다고 확인됨"
        else:
            status, reason = PENDING, "열람 확인 회신이 없다 — 확인 전에는 독립으로 세지 않는다"

        rep.rows.append(RowStatus(stage=stage, qid=qid, status=status, reason=reason,
                                  exposures=[{"exposure": e.exposure, "source": e.source,
                                              "confirmed_by": e.confirmed_by}
                                             for e in hits]))

    rep.independent_rows = sum(1 for r in rep.rows if r.status == INDEPENDENT)
    rep.excluded_rows = sum(1 for r in rep.rows if r.status == EXCLUDED)
    rep.pending_rows = sum(1 for r in rep.rows if r.status == PENDING)
    rep.excluded_qids = sorted(excluded_qids)
    rep.pending_reasons = {}
    for r in rep.rows:
        if r.status == PENDING:
            rep.pending_reasons[r.reason] = rep.pending_reasons.get(r.reason, 0) + 1

    # 분모는 독립으로 **확인된** 행뿐이다. 대기 행을 넣어 분모를 부풀리지 않는다.
    rep.agreement_denominator = rep.independent_rows
    if rep.pending_rows or not rep.independent_rows:
        rep.agreement_blocked_reason = (
            f"독립 일치율을 산출하지 않는다: 확인 대기 {rep.pending_rows}행, "
            f"독립 확인 {rep.independent_rows}행. 열람 확인 회신과 라벨이 모두 있어야 한다. "
            "산출 불가를 0%로 적지 않는다."
        )
    return rep


def label_usability_note() -> str:
    """제외한 라벨을 어떻게 다루는지 — 인정 여부와 구분한다."""
    return (
        "독립 일치율에서 제외한 행의 라벨은 폐기하지 않는다. 비독립 검토·협의 기록으로 "
        "보존한다. 그 라벨을 원본 근거와 사람 검토를 거쳐 **최종 정답 라벨로 쓸 수 있는지는 "
        "독립 일치율 인정 여부와 별개 판단**이다 — 노출된 라벨도 원본으로 검증되면 최종 "
        "정답 라벨이 될 수 있고, 독립으로 인정된 라벨도 원본 검증 전에는 확정이 아니다."
    )
