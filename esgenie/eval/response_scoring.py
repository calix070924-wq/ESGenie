"""실사 응답서 채점기 — 정답 라벨 대비 응답 판정 분류(확정 규칙만).

입력은 **공통 답안 형식 v1**(`esgenie/eval/answer_format.py`)이다. 그래서 ESGenie와
일반 AI 대조군을 같은 채점 로직으로 본다. 이 모듈은 ESGenie 전용 필드
(`status`·`comparison`·0-기준 페이지)를 모른다 — 변환은 `esgenie_adapter.py`가 한다.

작업지시서 A-1에서 **확정된 규칙만** 구현한다.

- 응답 판정 매핑(§6.1)과 미검증 전달 행의 집계(§6.2)는 확정 규칙이다.
- 그 밖의 (응답 판정 × 정답 라벨) 조합, 지표의 분자·분모, 종합 점수 산식은 아직
  확정되지 않았다(§6.3). 확정되지 않은 조합은 `unresolved`로 남기고 행별 이유를 기록한다.
- 분자·분모가 미정이므로 이 모듈은 **비율·정확도·종합 점수를 산출하지 않는다.**
  확정된 분류 결과와 건수까지만 낸다.

페이지 번호: 공통 형식과 라벨 `expected_sources`는 **둘 다 1-기준**이다. 이 모듈에는
변환이 없다(0→1 변환은 어댑터에서 한 번만 한다). 페이지가 없는 근거는 첫 페이지로
간주하지 않고 `None`으로 남긴다.
"""
from __future__ import annotations

import csv
import unicodedata
from collections import Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from . import answer_format as af

# ── 계약상의 값 집합 ──────────────────────────────────────────────────────
STAGES = af.STAGES
DECISIONS = ("answer", "hold", "na")
HOLD_REASONS = (
    "no_evidence", "scope_unconfirmed", "not_comparable", "mismatch", "needs_human_text",
)

LABEL_COLUMNS = (
    "stage", "qid", "expected_decision", "hold_reason", "expected_value", "expected_unit",
    "tolerance", "expected_sources", "boundary_note", "labeler", "note",
)

# 응답 판정(§6.1) — 공통 형식의 판정 값을 그대로 쓴다.
V_CONFIRMED = af.D_CONFIRMED              # 확정
V_SELF_REPORTED = af.D_UNVERIFIED         # 미검증 전달
V_HOLD = af.D_HOLD                        # 보류
V_NOT_APPLICABLE = af.D_NOT_APPLICABLE    # 해당 없음
V_UNPARSED = af.D_UNPARSED                # 원출력을 읽을 수 없음
V_UNDETERMINED = af.D_UNDETERMINED        # 공통 판정으로 옮기는 규칙이 계약에 없음

# 집계 위치(§6.2). 지표 산식이 미정이므로 건수까지만 쓴다.
B_CORRECT_HOLD = "correct_hold"              # 올바른 보류
B_WRONG_CONFIRMATION = "wrong_confirmation"  # 잘못된 확정
B_UNNECESSARY_HOLD = "unnecessary_hold"      # 불필요한 보류
B_UNRESOLVED = "unresolved"                  # 미정 — 임의 배정하지 않는다

# 세부 표시(§6.2)
D_MISSED_MISMATCH = "missed_mismatch"            # 놓친 불일치
D_EVIDENCE_LINK_MISSING = "evidence_link_missing"  # 근거 연결 누락

# 예/아니오 토큰. `0`·`1`은 넣지 않는다 — 수치 0을 예/아니오로 읽으면 안 된다(§6.1).
_YES = frozenset({"예", "y", "yes", "true"})
_NO = frozenset({"아니오", "아니요", "n", "no", "false"})


class LabelError(ValueError):
    """라벨 파일의 값이 계약을 벗어났을 때."""


# ── 정규화 ────────────────────────────────────────────────────────────────
def _norm_text(raw: Any) -> str:
    """표기 차이만 지우는 정규화 — 전각/반각, 앞뒤 공백, 연속 공백, 대소문자."""
    s = unicodedata.normalize("NFKC", "" if raw is None else str(raw)).strip()
    return " ".join(s.split()).casefold()


def _norm_unit(raw: Any) -> str:
    """단위 정규화 — 표기만 고른다. 단위 환산·별칭(t↔톤 등)은 하지 않는다.

    단위가 다르면 의미가 다르다고 보고 불일치로 남긴다(정규화로 숨기지 않는다).
    """
    return _norm_text(raw)


def _parse_number(raw: Any) -> float | None:
    """수치 파싱 — 천 단위 구분기호만 지운다. 단위가 붙은 문자열은 수치로 보지 않는다."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = unicodedata.normalize("NFKC", "" if raw is None else str(raw)).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_bool_token(raw: Any) -> bool | None:
    """예/아니오 계열 토큰 — 그 밖의 값은 None."""
    if isinstance(raw, bool):
        return raw
    s = _norm_text(raw)
    if s in _YES:
        return True
    if s in _NO:
        return False
    return None


#: 값이 채워졌는가 — `0`과 `False`는 채워진 값이다(§6.1). 공통 형식과 같은 판정을 쓴다.
_is_filled = af.is_filled


# ── 라벨 ──────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SourceRef:
    """`파일명#페이지` 한 건. `page`는 1-기준(라벨 기준)."""
    file_name: str
    page: int


@dataclass
class Label:
    stage: str
    qid: str
    expected_decision: str
    hold_reason: str = ""
    expected_value: str = ""
    expected_unit: str = ""
    tolerance: float = 0.0
    expected_sources: tuple[SourceRef, ...] = ()
    boundary_note: str = ""
    labeler: str = ""
    note: str = ""


def _parse_sources(raw: str, where: str) -> tuple[SourceRef, ...]:
    """`파일명#페이지`를 `;`로 구분. 페이지는 1부터 시작하는 정수."""
    out: list[SourceRef] = []
    for chunk in (raw or "").split(";"):
        item = chunk.strip()
        if not item:
            continue
        if "#" not in item:
            raise LabelError(f"{where}: expected_sources '{item}'에 '#페이지'가 없다")
        name, _, page_text = item.rpartition("#")
        name = name.strip()
        if not name:
            raise LabelError(f"{where}: expected_sources '{item}'에 파일명이 없다")
        try:
            page = int(page_text.strip())
        except ValueError as exc:
            raise LabelError(f"{where}: expected_sources '{item}'의 페이지가 정수가 아니다") from exc
        if page < 1:
            raise LabelError(f"{where}: expected_sources '{item}'의 페이지는 1부터 시작한다")
        out.append(SourceRef(file_name=name, page=page))
    return tuple(out)


def parse_label_row(row: dict[str, str], where: str) -> Label:
    """라벨 1행 검증·변환. 계약을 벗어난 값은 LabelError로 드러낸다."""
    missing = [c for c in LABEL_COLUMNS if c not in row]
    if missing:
        raise LabelError(f"{where}: 라벨 열 누락 {missing}")

    stage = (row["stage"] or "").strip()
    if stage not in STAGES:
        raise LabelError(f"{where}: stage '{stage}'는 {STAGES} 중 하나가 아니다")
    qid = (row["qid"] or "").strip()
    if not qid:
        raise LabelError(f"{where}: qid가 비어 있다")

    decision = (row["expected_decision"] or "").strip()
    if decision not in DECISIONS:
        raise LabelError(f"{where}: expected_decision '{decision}'는 {DECISIONS} 중 하나가 아니다")

    hold_reason = (row["hold_reason"] or "").strip()
    if decision == "hold":
        if hold_reason not in HOLD_REASONS:
            raise LabelError(f"{where}: hold에는 hold_reason이 {HOLD_REASONS} 중 하나여야 한다 (받은 값 '{hold_reason}')")
    elif hold_reason:
        raise LabelError(f"{where}: hold가 아닌 행에 hold_reason '{hold_reason}'이 있다")

    expected_value = (row["expected_value"] or "").strip()
    if decision == "answer" and expected_value == "":
        raise LabelError(f"{where}: answer 행에 expected_value가 없다")
    if decision != "answer" and expected_value:
        raise LabelError(f"{where}: answer가 아닌 행에 expected_value '{expected_value}'가 있다")

    tol_text = (row["tolerance"] or "").strip()
    tolerance = 0.0 if tol_text == "" else _parse_number(tol_text)
    if tolerance is None or tolerance < 0:
        raise LabelError(f"{where}: tolerance '{tol_text}'가 0 이상의 수치가 아니다")

    return Label(
        stage=stage,
        qid=qid,
        expected_decision=decision,
        hold_reason=hold_reason,
        expected_value=expected_value,
        expected_unit=(row["expected_unit"] or "").strip(),
        tolerance=float(tolerance),
        expected_sources=_parse_sources(row["expected_sources"], where),
        boundary_note=(row["boundary_note"] or "").strip(),
        labeler=(row["labeler"] or "").strip(),
        note=(row["note"] or "").strip(),
    )


def load_labels(path: str | Path) -> list[Label]:
    """라벨 CSV를 읽는다. 정답은 이 파일에서만 읽는다(제품 코드에 두지 않는다)."""
    path = Path(path)
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    return [parse_label_row(r, f"{path.name}:{i + 2}") for i, r in enumerate(rows)]


# ── 채점 대상 응답 (공통 형식에서 읽는다) ───────────────────────────────
@dataclass(frozen=True)
class EvidenceRef:
    """근거 1건. `page_1based`는 공통 형식의 `page`를 그대로 담는다(이미 1-기준)."""
    file_name: str
    page_1based: int | None


@dataclass
class SystemAnswer:
    """채점기가 보는 응답 한 행 — 시스템 중립이다."""
    stage: str
    qid: str
    decision: str
    value: Any
    unit: str
    evidence: tuple[EvidenceRef, ...] = ()
    decision_basis: str = ""
    source_status: str = ""
    source_comparison: str = ""


def _evidence(raw: Any) -> tuple[EvidenceRef, ...]:
    """공통 형식 근거 목록 → `EvidenceRef`. 페이지는 변환하지 않는다(이미 1-기준)."""
    return tuple(
        EvidenceRef(
            file_name=str(link.get("file_name") or ""),
            page_1based=link.get("page") if isinstance(link.get("page"), int)
            and not isinstance(link.get("page"), bool) else None,
        )
        for link in (raw or [])
        if isinstance(link, dict)
    )


def answers_from_document(doc: dict[str, Any]) -> list[SystemAnswer]:
    """공통 형식 문서 → 채점 대상 응답 목록. 단계는 `meta.stage`에서 읽는다.

    형식 위반은 여기서 바로잡지 않는다. 호출자가 `af.validate_document()`로 먼저 본다.
    """
    meta = doc.get("meta")
    if not isinstance(meta, dict):
        raise af.FormatError("공통 형식 문서에 'meta'가 없다")
    stage = str(meta.get("stage") or "")
    if stage not in STAGES:
        raise af.FormatError(f"meta.stage '{stage}'는 {STAGES} 중 하나가 아니다")
    rows = doc.get("answers")
    if not isinstance(rows, list):
        raise af.FormatError("공통 형식 문서에 'answers' 배열이 없다")

    out: list[SystemAnswer] = []
    for a in rows:
        if not isinstance(a, dict):
            raise af.FormatError("응답 항목이 객체가 아니다")
        source = a.get("source") if isinstance(a.get("source"), dict) else {}
        out.append(SystemAnswer(
            stage=stage,
            qid=str(a.get("qid") or ""),
            decision=str(a.get("decision") or ""),
            value=a.get("value"),
            unit=str(a.get("unit") or ""),
            evidence=_evidence(a.get("evidence")),
            decision_basis=str(a.get("decision_basis") or ""),
            source_status=str(source.get("status") or ""),
            source_comparison=str(source.get("comparison") or ""),
        ))
    return out


# ── 값·근거 비교 ─────────────────────────────────────────────────────────
def compare_value(label: Label, answer: SystemAnswer) -> tuple[bool | None, str]:
    """값 일치 여부. 판단 규칙이 없는 형태는 (None, 이유)로 남긴다."""
    if label.expected_decision != "answer":
        return None, "answer 라벨이 아니어서 값 비교 대상이 아니다"
    if not _is_filled(answer.value):
        return False, "시스템 값이 비어 있다"

    expected_bool = _parse_bool_token(label.expected_value)

    # 예/아니오 — 값 형태는 시스템 값의 자료형으로 가른다. 단위는 보지 않는다.
    if isinstance(answer.value, bool):
        if expected_bool is None:
            return False, "시스템 값은 예/아니오인데 라벨은 수치·문자다"
        if answer.value == expected_bool:
            return True, "예/아니오 일치"
        return False, "예/아니오 불일치"
    if expected_bool is not None:
        token = _parse_bool_token(answer.value)
        if token is None:
            return False, "라벨은 예/아니오인데 시스템 값을 예/아니오로 읽을 수 없다"
        if token == expected_bool:
            return True, "예/아니오 일치"
        return False, "예/아니오 불일치"

    # 단위는 수치 비교 전에 본다 — 환산 없이 표기만 맞춰 비교한다.
    if _norm_unit(label.expected_unit) != _norm_unit(answer.unit):
        return False, f"단위 불일치: 라벨 '{label.expected_unit}' vs 시스템 '{answer.unit}'"

    expected_num = _parse_number(label.expected_value)
    system_num = _parse_number(answer.value)
    if expected_num is not None and system_num is not None:
        if abs(system_num - expected_num) <= label.tolerance:
            return True, f"수치 일치(허용 오차 {label.tolerance})"
        return False, f"수치 불일치: 라벨 {expected_num} vs 시스템 {system_num} (허용 오차 {label.tolerance})"
    if expected_num is None and system_num is None:
        if isinstance(answer.value, (list, tuple, set)):
            return None, "목록 값의 비교 규칙이 계약에 없다"
        if _norm_text(label.expected_value) == _norm_text(answer.value):
            return True, "문자 일치"
        return False, "문자 불일치"
    return False, "한쪽만 수치여서 값 형태가 다르다"


def compare_sources(label: Label, answer: SystemAnswer) -> tuple[bool | None, str]:
    """근거 일치 여부. 정답 근거 여러 건 중 하나라도 맞으면 일치(§5.1)."""
    if not label.expected_sources:
        return None, "라벨에 정답 근거가 없다"
    if not answer.evidence:
        return False, "시스템 근거가 없다"
    wanted = {(_norm_text(s.file_name), s.page) for s in label.expected_sources}
    got = {(_norm_text(e.file_name), e.page_1based) for e in answer.evidence}
    hit = wanted & got
    if hit:
        return True, f"정답 근거 일치: {sorted(f'{n}#{p}' for n, p in hit)}"
    if all(e.page_1based is None for e in answer.evidence):
        return False, "시스템 근거에 페이지가 없다"
    return False, "정답 근거와 일치하는 시스템 근거가 없다"


# ── 집계 위치(§6.2 확정 규칙) ────────────────────────────────────────────
#: `self_reported` × hold 라벨의 확정 매핑.
_SELF_REPORTED_HOLD = {
    "no_evidence": (B_CORRECT_HOLD, ""),
    "mismatch": (B_WRONG_CONFIRMATION, D_MISSED_MISMATCH),
    "not_comparable": (B_WRONG_CONFIRMATION, D_MISSED_MISMATCH),
    "scope_unconfirmed": (B_WRONG_CONFIRMATION, D_MISSED_MISMATCH),
}


def assign_bucket(
    verdict: str, label: Label, value_match: bool | None,
) -> tuple[str, str, str]:
    """(집계 위치, 세부 표시, 이유).

    확정 규칙은 §6.2의 미검증 전달 행뿐이다. 그 밖의 조합은 `unresolved`로 남긴다
    (§6.3). 시스템에 유리하게 바꾸거나 조용히 제외하지 않는다.
    """
    if verdict == V_UNPARSED:
        return B_UNRESOLVED, "", "응답을 읽을 수 없어 집계하지 않는다(보류로 세지 않는다)"
    if verdict == V_UNDETERMINED:
        return B_UNRESOLVED, "", "응답을 공통 판정으로 옮기는 규칙이 계약에 없다"
    if verdict != V_SELF_REPORTED:
        return B_UNRESOLVED, "", f"응답 판정 '{verdict}' × 라벨 '{label.expected_decision}'의 집계 규칙이 계약에 없다"

    if label.expected_decision == "hold":
        rule = _SELF_REPORTED_HOLD.get(label.hold_reason)
        if rule is None:
            return B_UNRESOLVED, "", f"self_reported × hold({label.hold_reason})의 집계 규칙이 계약에 없다"
        bucket, detail = rule
        return bucket, detail, f"§6.2: self_reported × hold({label.hold_reason})"

    if label.expected_decision == "answer":
        if value_match is None:
            return B_UNRESOLVED, "", "값 일치 여부를 판단할 규칙이 없어 집계하지 않는다"
        if value_match:
            return B_UNNECESSARY_HOLD, D_EVIDENCE_LINK_MISSING, "§6.2: self_reported × answer(값 일치)"
        return B_WRONG_CONFIRMATION, D_MISSED_MISMATCH, "§6.2: self_reported × answer(값 불일치)"

    return B_UNRESOLVED, "", f"self_reported × {label.expected_decision}의 집계 규칙이 계약에 없다"


# ── 행별 결과·구성 검증 ──────────────────────────────────────────────────
@dataclass
class RowScore:
    stage: str
    qid: str
    system_status: str          # 원본 보존 — 대조군은 비어 있을 수 있다
    system_comparison: str      # 원본 보존 — 대조군은 비어 있을 수 있다
    system_value_filled: bool
    system_verdict: str         # 공통 응답 판정
    verdict_reason: str
    label_decision: str
    label_hold_reason: str
    value_match: bool | None
    value_reason: str
    source_match: bool | None
    source_reason: str
    bucket: str
    bucket_detail: str
    bucket_reason: str


@dataclass
class StructureReport:
    rows_per_stage: dict[str, int] = field(default_factory=dict)
    total_rows: int = 0
    expected_rows_per_stage: int = 0
    expected_total_rows: int = 0
    duplicate_labels: list[str] = field(default_factory=list)
    duplicate_answers: list[str] = field(default_factory=list)
    missing_answers: list[str] = field(default_factory=list)
    unexpected_answers: list[str] = field(default_factory=list)
    missing_labels: list[str] = field(default_factory=list)
    unexpected_labels: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any((
            self.duplicate_labels, self.duplicate_answers, self.missing_answers,
            self.unexpected_answers, self.missing_labels, self.unexpected_labels,
        ))


def _dupes(keys: list[tuple[str, str]]) -> list[str]:
    return sorted(f"{s}/{q}" for (s, q), n in Counter(keys).items() if n > 1)


def check_structure(
    labels: list[Label],
    answers_by_stage: dict[str, list[SystemAnswer]],
    expected_qids: tuple[str, ...],
) -> StructureReport:
    """단계별·전체 구성과 중복·누락·예상 외 행을 검증한다.

    `expected_qids`는 호출자가 양식에서 읽어 넘긴다(채점기에 문항 ID를 박지 않는다).
    """
    expected = set(expected_qids)
    stages = tuple(s for s in STAGES if s in answers_by_stage)
    rep = StructureReport(
        rows_per_stage={s: len(answers_by_stage[s]) for s in stages},
        total_rows=sum(len(answers_by_stage[s]) for s in stages),
        expected_rows_per_stage=len(expected),
        expected_total_rows=len(expected) * len(stages),
    )
    rep.duplicate_labels = _dupes([(l.stage, l.qid) for l in labels])
    rep.duplicate_answers = _dupes([(a.stage, a.qid) for s in stages for a in answers_by_stage[s]])

    for s in stages:
        got = {a.qid for a in answers_by_stage[s]}
        rep.missing_answers += sorted(f"{s}/{q}" for q in expected - got)
        rep.unexpected_answers += sorted(f"{s}/{q}" for q in got - expected)
        labeled = {l.qid for l in labels if l.stage == s}
        rep.missing_labels += sorted(f"{s}/{q}" for q in expected - labeled)
        rep.unexpected_labels += sorted(f"{s}/{q}" for q in labeled - expected)
    return rep


@dataclass
class ScoreReport:
    """채점 결과 — 분류와 건수까지만. 비율·정확도·종합 점수는 내지 않는다."""
    rows: list[RowScore] = field(default_factory=list)
    verdict_counts: dict[str, int] = field(default_factory=dict)
    bucket_counts: dict[str, int] = field(default_factory=dict)
    detail_counts: dict[str, int] = field(default_factory=dict)
    value_match_counts: dict[str, int] = field(default_factory=dict)
    source_match_counts: dict[str, int] = field(default_factory=dict)
    unresolved_reasons: dict[str, int] = field(default_factory=dict)
    unscored_pairs: list[str] = field(default_factory=list)
    structure: StructureReport = field(default_factory=StructureReport)
    #: 채점에 쓴 입력 문서들 — 어느 시스템·실행·단계·자료 출처였는지 남긴다.
    documents: list[dict[str, Any]] = field(default_factory=list)
    metrics_blocked_reason: str = (
        "지표의 분자·분모와 종합 점수 산식이 확정되지 않았다(작업지시서 A §4·공통 계약 미확보). "
        "비율·정확도·종합 점수를 산출하지 않는다."
    )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["structure"]["ok"] = self.structure.ok
        return d


def _flag(v: bool | None) -> str:
    return {True: "match", False: "mismatch", None: "undetermined"}[v]


def score(
    labels: list[Label],
    answers_by_stage: dict[str, list[SystemAnswer]],
    expected_qids: tuple[str, ...],
) -> ScoreReport:
    """`(stage, qid)`로 라벨과 응답을 대응시켜 채점한다."""
    rep = ScoreReport(structure=check_structure(labels, answers_by_stage, expected_qids))
    by_key: dict[tuple[str, str], SystemAnswer] = {}
    for stage, answers in answers_by_stage.items():
        for a in answers:
            by_key.setdefault((stage, a.qid), a)

    seen: set[tuple[str, str]] = set()
    for label in labels:
        key = (label.stage, label.qid)
        if key in seen:
            continue  # 중복은 structure.duplicate_labels에 드러난다
        seen.add(key)
        answer = by_key.get(key)
        if answer is None:
            rep.unscored_pairs.append(f"{label.stage}/{label.qid}: 대응하는 응답 행이 없다")
            continue

        verdict, verdict_reason = answer.decision, answer.decision_basis
        value_match, value_reason = compare_value(label, answer)
        source_match, source_reason = compare_sources(label, answer)
        bucket, detail, bucket_reason = assign_bucket(verdict, label, value_match)

        rep.rows.append(RowScore(
            stage=label.stage, qid=label.qid,
            system_status=answer.source_status,
            system_comparison=answer.source_comparison,
            system_value_filled=_is_filled(answer.value),
            system_verdict=verdict, verdict_reason=verdict_reason,
            label_decision=label.expected_decision, label_hold_reason=label.hold_reason,
            value_match=value_match, value_reason=value_reason,
            source_match=source_match, source_reason=source_reason,
            bucket=bucket, bucket_detail=detail, bucket_reason=bucket_reason,
        ))

    for key, answer in by_key.items():
        if key not in seen:
            rep.unscored_pairs.append(f"{key[0]}/{key[1]}: 대응하는 라벨 행이 없다")

    rep.verdict_counts = dict(Counter(r.system_verdict for r in rep.rows))
    rep.bucket_counts = dict(Counter(r.bucket for r in rep.rows))
    rep.detail_counts = dict(Counter(r.bucket_detail for r in rep.rows if r.bucket_detail))
    rep.value_match_counts = dict(Counter(_flag(r.value_match) for r in rep.rows))
    rep.source_match_counts = dict(Counter(_flag(r.source_match) for r in rep.rows))
    rep.unresolved_reasons = dict(Counter(
        r.bucket_reason for r in rep.rows if r.bucket == B_UNRESOLVED
    ))
    rep.unscored_pairs.sort()
    return rep


# ── 양식에서 문항 구성 읽기 ──────────────────────────────────────────────
def framework_qids(framework_key: str) -> tuple[str, ...]:
    """양식의 qid 목록. 채점기에 문항 ID를 박지 않기 위해 여기서 읽는다."""
    from ..supplychain.frameworks import get_framework
    return tuple(q.qid for q in get_framework(framework_key).questions)


#: 예전 이름 — 내부 호출 호환용.
_framework_qids = framework_qids


def score_documents(
    labels: list[Label],
    documents: list[dict[str, Any]],
    expected_qids: tuple[str, ...],
) -> ScoreReport:
    """공통 형식 문서들을 단계별로 묶어 채점한다.

    **같은 단계를 두 문서가 주면 거부한다** — 실행이 다른 응답을 한 단계로 합치면
    어느 실행의 결과인지 알 수 없다. 실행마다 따로 채점한다.
    """
    answers_by_stage: dict[str, list[SystemAnswer]] = {}
    seen: dict[str, tuple[str, str, str]] = {}
    for doc in documents:
        answers = answers_from_document(doc)
        stage = answers[0].stage if answers else str(
            (doc.get("meta") or {}).get("stage") or "")
        key = af.document_key(doc.get("meta") or {})
        if stage in answers_by_stage:
            raise af.FormatError(
                f"단계 '{stage}'에 문서가 둘 이상이다: {seen[stage]} vs {key}. "
                "실행이 다른 응답을 한 단계로 합치지 않는다 — 실행별로 따로 채점하라")
        answers_by_stage[stage] = answers
        seen[stage] = key

    rep = score(labels, answers_by_stage, expected_qids)
    for doc in documents:
        meta = doc.get("meta") or {}
        rep.documents.append({
            "system": meta.get("system"), "model": meta.get("model"),
            "run_id": meta.get("run_id"), "stage": meta.get("stage"),
            "framework": meta.get("framework"), "data_source": meta.get("data_source"),
            "format_version": doc.get("format_version"),
            "adapter": meta.get("adapter"), "source_ref": meta.get("source_ref"),
            "answer_rows": len(doc.get("answers") or []),
        })
    return rep
