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

# 집계 위치. §6.2 네 줄 + 2026-10-06 지민 확정 판정표.
B_CORRECT_ANSWER = "correct_answer"          # 올바른 답변 (2026-10-06 신설)
B_CORRECT_HOLD = "correct_hold"              # 올바른 보류
B_CORRECT_NA = "correct_na"                  # 올바른 해당 없음 (2026-10-06 신설)
B_WRONG_CONFIRMATION = "wrong_confirmation"  # 잘못된 확정
B_UNNECESSARY_HOLD = "unnecessary_hold"      # 불필요한 보류
B_DECISION_MISMATCH = "decision_mismatch"    # 판정 불일치 (2026-10-06 신설)
B_UNRESOLVED = "unresolved"                  # 미정 — 임의 배정하지 않는다

#: `decision_mismatch`는 **정상 보류도 정답도 아니다.** 별도 건수로만 센다.

# 세부 표시(§6.2 + 2026-10-06 확정표)
D_MISSED_MISMATCH = "missed_mismatch"            # 놓친 불일치
D_EVIDENCE_LINK_MISSING = "evidence_link_missing"  # 근거 연결 누락
# 확정 응답의 오류를 값·근거·둘 다로 **분리**한다(근거만 틀린 것을 값 오류로 뭉개지 않는다).
D_VALUE_ERROR = "value_error"                      # 값만 틀렸다
D_EVIDENCE_ERROR = "evidence_error"                # 근거만 틀렸다
D_VALUE_AND_EVIDENCE_ERROR = "value_and_evidence_error"  # 둘 다 틀렸다
D_WRONGLY_NOT_APPLICABLE = "wrongly_not_applicable"      # 답할 수 있는데 해당 없음으로 냈다
# 보류 결정이 맞은 행의 **사유** 비교. 미확인을 일치로 간주하지 않는다.
D_HOLD_REASON_MATCH = "hold_reason_match"
D_HOLD_REASON_MISMATCH = "hold_reason_mismatch"
D_HOLD_REASON_UNKNOWN = "hold_reason_unknown"

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
    """단위 정규화 — 표기 차이와 **같은 단위의 별칭**까지만 고른다.

    작업지시서 §2.4: "단위 동치는 기존 `esgenie` 유틸을 재사용한다(`tCO2e`≡`tCO2eq` 등).
    새 환산표를 만들지 않는다." 그래서 `rag_gates.units.normalize_unit`을 **읽어 쓴다**
    (제품 유틸을 고치지 않는다). 사전에 없는 단위는 표기 정규화로만 비교한다 — 모르는
    단위 두 개를 같다고 보지 않는다.

    **배율 환산은 하지 않는다.** `kg`과 `t`는 여전히 불일치다. 환산하려면 라벨의
    `tolerance`도 함께 환산해야 하는데 그 규정이 계약에 없다 — 임의로 만들지 않는다.
    """
    text = _norm_text(raw)
    if not text:
        return ""
    try:
        from ..rag_gates.units import normalize_unit
    except ImportError:          # 제품 패키지 없이 채점할 때 — 표기 비교로 내려간다
        return text
    canonical = normalize_unit(str(raw))
    return canonical.casefold() if canonical else text


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

    sources = _parse_sources(row["expected_sources"], where)
    # `answer` 라벨에는 근거 출처가 **필수**다(2026-10-06 확정). 비면 라벨 검증 오류로
    # 드러낸다. 비어 있는 행을 지표 분모에서 조용히 빼면 그만큼 수치가 높아진다.
    if decision == "answer" and not sources:
        raise LabelError(
            f"{where}: answer 행에 expected_sources가 없다. "
            "'파일명#페이지'(1-기준, 여러 개는 ';')를 적는다. "
            "비워 두고 지표 분모에서 빼지 않는다"
        )

    return Label(
        stage=stage,
        qid=qid,
        expected_decision=decision,
        hold_reason=hold_reason,
        expected_value=expected_value,
        expected_unit=(row["expected_unit"] or "").strip(),
        tolerance=float(tolerance),
        expected_sources=sources,
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
    #: 외부 형식 3값(`answer`/`hold`/`na`) 또는 `None`(정상 3값을 줄 수 없는 오류 행).
    decision: str | None
    #: 내부 상세 상태 6값. **채점은 이것만 본다** — `decision=answer`라는 이유로 확정으로
    #: 세지 않기 위해서다. 구조화 필드가 없으면 빈 문자열이고, 자유 문구에서 추정하지 않는다.
    decision_detail: str
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
        detail = af.detail_of(a)
        basis = str(a.get("decision_basis") or "")
        if detail is None:
            # 상세 상태가 없으면 **추정하지 않는다.** 빈 상태로 두면 판정표에 없는
            # 값이 되어 `unresolved`로 남는다 — 정상 보류로 섞이지 않는다.
            basis = (basis + " · decision_detail 누락 — 상세 상태를 자유 문구에서 "
                     "복원하지 않는다").strip(" ·")
        out.append(SystemAnswer(
            stage=stage,
            qid=str(a.get("qid") or ""),
            decision=a.get("decision") if a.get("decision") in af.DECISION_VALUES else None,
            decision_detail=detail or "",
            value=a.get("value"),
            unit=str(a.get("unit") or ""),
            evidence=_evidence(a.get("evidence")),
            decision_basis=basis,
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


def compare_sources_file_only(label: Label, answer: SystemAnswer) -> tuple[bool | None, str]:
    """**보조** — 파일만 맞으면 일치로 본다(쪽은 보지 않는다).

    공식 수치는 `compare_sources()`의 **파일+쪽** 일치다. 이 함수의 결과가 공식 수치를
    대체하지 않는다. B 의견대로 근거 일치를 두 단계로 나누어 보기 위한 것이다.
    """
    if not label.expected_sources:
        return None, "라벨에 정답 근거가 없다"
    if not answer.evidence:
        return False, "시스템 근거가 없다"
    wanted = {_norm_text(s.file_name) for s in label.expected_sources}
    got = {_norm_text(e.file_name) for e in answer.evidence}
    hit = wanted & got
    if hit:
        return True, f"정답 근거 파일 일치(쪽 미확인): {sorted(hit)}"
    return False, "정답 근거와 일치하는 파일이 없다"


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


#: 시스템 보류 사유를 **구조화해 받는 필드가 공통 형식에 없다.** `source.comparison`이
#: 라벨 사유와 같은 어휘인 경우만 비교하고, 그 밖은 "미확인"으로 남긴다.
#: `no_evidence`·`needs_human_text`에 대응하는 `comparison` 값은 없다.
_COMPARISON_AS_HOLD_REASON = frozenset({"mismatch", "not_comparable", "scope_unconfirmed"})


def _hold_reason_detail(label: Label, system_comparison: str) -> tuple[str, str]:
    """보류 결정이 맞은 행의 **사유** 비교. (세부 표시, 설명).

    사유가 확인되지 않은 것을 **사유 일치로 간주하지 않는다.**
    """
    got = _norm_text(system_comparison)
    if got not in _COMPARISON_AS_HOLD_REASON:
        return D_HOLD_REASON_UNKNOWN, (
            "보류 결정은 맞았다. 시스템 보류 사유는 공통 형식에 구조화 필드가 없어 "
            f"확인되지 않는다(comparison='{system_comparison}'). 사유 일치로 세지 않는다"
        )
    if got == label.hold_reason:
        return D_HOLD_REASON_MATCH, f"보류 결정·사유 모두 일치({got})"
    return D_HOLD_REASON_MISMATCH, (
        f"보류 결정은 맞았으나 사유가 다르다(라벨 '{label.hold_reason}' vs 시스템 '{got}')"
    )


def _confirmed_answer_detail(value_match: bool, source_match: bool) -> str:
    """확정 응답의 오류를 값·근거·둘 다로 분리한다."""
    if not value_match and not source_match:
        return D_VALUE_AND_EVIDENCE_ERROR
    if not value_match:
        return D_VALUE_ERROR
    return D_EVIDENCE_ERROR


def assign_bucket(
    verdict: str, label: Label, value_match: bool | None,
    source_match: bool | None = None, system_comparison: str = "",
    value_reason: str = "", source_reason: str = "",
) -> tuple[str, str, str]:
    """(집계 위치, 세부 표시, 이유).

    §6.1 시스템 판정 매핑과 §6.2 미검증 전달 규칙은 **그대로 유지한다.** 아래는 그
    판정 이후의 집계 규칙이며 2026-10-06 지민 확정표다. 판정 자체가 미정인 사례
    (`unparsed`·`undetermined`)는 여기서 임의로 해결하지 않고 `unresolved`로 남긴다.
    """
    if verdict == V_UNPARSED:
        return B_UNRESOLVED, "", "응답을 읽을 수 없어 집계하지 않는다(보류로 세지 않는다)"
    if verdict == V_UNDETERMINED:
        return B_UNRESOLVED, "", "응답을 공통 판정으로 옮기는 규칙이 계약에 없다"

    decision = label.expected_decision

    # ── 확정 응답 ─────────────────────────────────────────────────────────
    if verdict == V_CONFIRMED:
        if decision == "answer":
            if value_match is None or source_match is None:
                why = value_reason if value_match is None else source_reason
                return B_UNRESOLVED, "", (
                    "값·근거 비교 규칙이 없어 집계하지 않는다(판정표 이전 단계의 미정이다)"
                    + (f": {why}" if why else ""))
            if value_match and source_match:
                return B_CORRECT_ANSWER, "", "확정표: confirmed × answer(값·근거 모두 일치)"
            detail = _confirmed_answer_detail(value_match, source_match)
            return B_WRONG_CONFIRMATION, detail, (
                f"확정표: confirmed × answer({detail}). "
                "근거만 틀린 확정도 잘못된 확정에 넣되 세부 사유로 분리한다")
        if decision == "hold":
            return B_WRONG_CONFIRMATION, D_MISSED_MISMATCH, (
                f"확정표: confirmed × hold({label.hold_reason}) — 모든 사유에서 "
                "잘못된 확정이다")
        return B_WRONG_CONFIRMATION, "", "확정표: confirmed × na"

    # ── 미검증 전달 — §6.2 기존 규칙을 유지한다 ──────────────────────────
    if verdict == V_SELF_REPORTED:
        if decision == "hold":
            rule = _SELF_REPORTED_HOLD.get(label.hold_reason)
            if rule is not None:
                bucket, detail = rule
                return bucket, detail, f"§6.2: self_reported × hold({label.hold_reason})"
            if label.hold_reason == "needs_human_text":
                return B_WRONG_CONFIRMATION, D_MISSED_MISMATCH, (
                    "확정표: self_reported × hold(needs_human_text) — 보수적 평가 정책. "
                    "§6.2 네 줄은 바뀌지 않았다")
            return B_UNRESOLVED, "", (
                f"self_reported × hold({label.hold_reason})의 집계 규칙이 계약에 없다")
        if decision == "answer":
            if value_match is None:
                return B_UNRESOLVED, "", (
                    "값 일치 여부를 판단할 규칙이 없어 집계하지 않는다"
                    + (f": {value_reason}" if value_reason else ""))
            if value_match:
                return B_UNNECESSARY_HOLD, D_EVIDENCE_LINK_MISSING, "§6.2: self_reported × answer(값 일치)"
            return B_WRONG_CONFIRMATION, D_MISSED_MISMATCH, "§6.2: self_reported × answer(값 불일치)"
        return B_WRONG_CONFIRMATION, "", "확정표: self_reported × na"

    # ── 보류 ──────────────────────────────────────────────────────────────
    if verdict == V_HOLD:
        if decision == "answer":
            return B_UNNECESSARY_HOLD, "", "확정표: hold × answer"
        if decision == "hold":
            detail, why = _hold_reason_detail(label, system_comparison)
            return B_CORRECT_HOLD, detail, f"확정표: hold × hold — {why}"
        return B_DECISION_MISMATCH, "", (
            "확정표: hold × na — 정상 보류도 정답도 아니다. 별도로 센다")

    # ── 해당 없음 ─────────────────────────────────────────────────────────
    if verdict == V_NOT_APPLICABLE:
        if decision == "answer":
            return B_UNNECESSARY_HOLD, D_WRONGLY_NOT_APPLICABLE, (
                "확정표: not_applicable × answer — 답할 수 있는 문항을 해당 없음으로 냈다")
        if decision == "hold":
            return B_DECISION_MISMATCH, "", (
                "확정표: not_applicable × hold — 정상 보류도 정답도 아니다. 별도로 센다")
        return B_CORRECT_NA, "", "확정표: not_applicable × na"

    return B_UNRESOLVED, "", (
        f"응답 판정 '{verdict}'는 확정표에 없다. 임의로 배정하지 않는다")


# ── 행별 결과·구성 검증 ──────────────────────────────────────────────────
@dataclass
class RowScore:
    stage: str
    qid: str
    system_status: str          # 원본 보존 — 대조군은 비어 있을 수 있다
    system_comparison: str      # 원본 보존 — 대조군은 비어 있을 수 있다
    system_value_filled: bool
    system_evidence_count: int  # 연결한 근거 건수. 정답 여부와 별개다(M5 vs A5)
    system_verdict: str         # 내부 상세 상태(`decision_detail`) — 채점 기준
    verdict_reason: str
    label_decision: str
    label_hold_reason: str
    value_match: bool | None
    value_reason: str
    source_match: bool | None       # 공식 — 파일+쪽 일치
    source_reason: str
    source_file_match: bool | None  # 보조 — 파일만 일치. 공식을 대체하지 않는다
    source_file_reason: str
    bucket: str
    bucket_detail: str
    bucket_reason: str
    #: 외부 형식 3값(`answer`/`hold`/`na`) 또는 `None`(오류 행). 보고에 함께 적는다.
    #: 상세 상태와 **1:1이 아니다** — `answer`는 confirmed와 미검증 전달을 함께 덮는다.
    system_decision: str | None = None


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


# ── 지표(2026-10-06 확정 정책) ───────────────────────────────────────────
#: 핵심 지표 키. **종합 점수는 만들지 않는다** — 확정 요구사항이고 향후 구현 대상도 아니다.
M1 = "M1_답변근거_동시정답률"
M2 = "M2_잘못된_확정_수"
M3 = "M3_불필요한_보류_수"
#: **M4 이름 변경 (2026-10-07 지민 승인)** — '자동응답률'에서 '확정 제출률'로 구분한다.
#: 정의(`confirmed / 예상 문항 전체`)와 결과는 그대로다. 이름만 갈랐다.
#: 제품 고유 `ResponseSheet.auto_pct`는 **다른 지표**이고 제품 코드에 그대로 남아 있다.
M4 = "M4_확정_제출률"
#: 이전 이름. 보존해서 적는다 — 지난 보고의 수치와 이어 읽을 수 있어야 한다.
M4_PREVIOUS_NAME = "M4_자동응답률"
M5 = "M5_자료_연결률"
#: **값 제출률 (2026-10-07 지민 승인)** — 공통 비교 지표로 승격. 이전에는 보조 A3였다.
M6 = "M6_값_제출률"
M6_PREVIOUS_NAME = "A3_값_제출률"

#: 지표 산출 상태.
MS_AVAILABLE = "산출"            # 분자·분모가 모두 정해져 계산했다
MS_WITHHELD = "보류"             # 미정 조합이 남아 공식 비율을 내지 않는다
MS_NOT_COMPUTABLE = "산출_불가"   # 분모가 0이다. **0%가 아니다**


@dataclass
class Metric:
    """지표 1건. 비율은 분자·분모를 **항상 함께** 남긴다.

    `value`가 `None`인 경우를 `0`으로 바꾸지 않는다 — 보류와 0%는 다른 뜻이다.
    """
    key: str
    kind: str                     # "ratio" | "count"
    status: str
    numerator: int | None = None
    denominator: int | None = None
    value: float | None = None    # 비율. 0~1
    scope_note: str = ""          # 분모가 무엇인지 말로 적는다
    undetermined_rows: int = 0
    reasons: dict[str, int] = field(default_factory=dict)
    note: str = ""


def _ratio(key: str, numer: int, denom: int, scope_note: str, *,
           undetermined: int = 0, reasons: dict[str, int] | None = None,
           note: str = "") -> Metric:
    """비율 지표 1건. 미정 행이 남으면 **공식 비율을 내지 않는다.**

    `undetermined`는 **그 보고 단위(실행·단계)에 남은 `unresolved` 행 수**다. 그 지표의
    분모에 영향을 주는 행만 세는 것이 아니다 — 2026-10-06 지민 승인 범위다.
    """
    reasons = reasons or {}
    if undetermined:
        return Metric(key=key, kind="ratio", status=MS_WITHHELD,
                      numerator=numer, denominator=denom, value=None,
                      scope_note=scope_note, undetermined_rows=undetermined,
                      reasons=reasons,
                      note=(note + " 미정 행이 남아 공식 비율을 보류한다. "
                            "확정 행 기준 분자·분모만 참고로 남긴다.").strip())
    if denom == 0:
        return Metric(key=key, kind="ratio", status=MS_NOT_COMPUTABLE,
                      numerator=numer, denominator=0, value=None,
                      scope_note=scope_note, reasons=reasons,
                      note=(note + " 분모가 0이다. 산출 불가이며 0%가 아니다.").strip())
    return Metric(key=key, kind="ratio", status=MS_AVAILABLE,
                  numerator=numer, denominator=denom, value=numer / denom,
                  scope_note=scope_note, reasons=reasons, note=note)


@dataclass
class MetricScope:
    """보고 단위 1건 — **시스템·실행·단계별이 기본이다.**

    `aggregated`가 True면 단계를 합친 참고용 수치다. 한 단계만 있으면 합산을 만들지
    않는다(한 단계를 '전체'라고 부르지 않는다).
    """
    system: str = ""
    run_id: str = ""
    stage: str = ""
    aggregated: bool = False
    label: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    auxiliary: dict[str, Any] = field(default_factory=dict)
    input_completeness: dict[str, Any] = field(default_factory=dict)


def _metric_scope(rows: list[RowScore], labels: list[Label],
                  expected_qid_count: int, stages: tuple[str, ...],
                  *, aggregated: bool) -> MetricScope:
    """한 보고 단위의 지표를 계산한다. 확정된 조건만 계산하고, 미정은 보류로 남긴다."""
    answer_labels = [l for l in labels if l.expected_decision == "answer"]
    answer_rows = [r for r in rows if r.label_decision == "answer"]
    na_rows = [r for r in rows if r.label_decision == "na"]

    # ── Q7(2026-10-06 승인) — **이 보고 단위에 `unresolved` 행이 1건이라도 남으면
    #    그 실행·단계의 공식 비율 전체를 보류한다.** 지표별 분모에 영향을 주는 행만
    #    보는 것으로 좁히지 않는다. 확정표로 해결되는 조합을 먼저 구현한 뒤에도 남는
    #    미정이 여기 걸린다.
    unresolved_rows = [r for r in rows if r.bucket == B_UNRESOLVED]
    hold_count = len(unresolved_rows)
    hold_reasons = dict(Counter(r.bucket_reason for r in unresolved_rows))

    # ── M1 — 분모는 해당 단계 `answer` 라벨 **전체**(응답 누락·파싱 실패 포함).
    m1_denom = len(answer_labels)
    m1_numer = sum(1 for r in answer_rows if r.bucket == B_CORRECT_ANSWER)
    m1_missing = m1_denom - len(answer_rows)
    m1_unparsed = sum(1 for r in answer_rows if r.system_verdict == V_UNPARSED)
    m1 = _ratio(
        M1, m1_numer, m1_denom,
        "분모: `answer` 라벨 전체(응답 누락·파싱 실패 포함) / "
        "분자: 집계 위치가 `correct_answer`인 행 — `confirmed`로 제출하고 값·근거가 "
        "모두 정답인 행이다(확정표와 같은 조건)",
        undetermined=hold_count, reasons=hold_reasons,
        note=("제출한 답변 중 정답 비율이 아니다. "
              "답할 수 있어야 하는 문항 중 근거까지 갖춰 확정한 비율이다. "
              "미검증 전달(`unverified_submitted`)은 분자에 넣지 않는다. "
              f"응답 누락 {m1_missing}건·파싱 실패 {m1_unparsed}건은 분모에 들어가고 "
              "분자에서 빠진다. `hold`·`na` 라벨은 섞지 않는다."),
    )

    # ── M2·M3 — 건수. **확정된 행에서 확인된 건수**다.
    unresolved = hold_count
    resolved = len(rows) - unresolved
    count_note = (f"확정된 행 {resolved}건에서 확인된 건수다. "
                  f"미정 행 {unresolved}건은 포함되지 않는다 — 실제 건수는 이보다 많을 수 있다.")
    m2 = Metric(key=M2, kind="count", status=MS_AVAILABLE,
                numerator=sum(1 for r in rows if r.bucket == B_WRONG_CONFIRMATION),
                scope_note="보류해야 할 문항에 확정 답변을 낸 건수", note=count_note)
    m3 = Metric(key=M3, kind="count", status=MS_AVAILABLE,
                numerator=sum(1 for r in rows if r.bucket == B_UNNECESSARY_HOLD),
                scope_note="답할 수 있는 문항을 보류한 건수", note=count_note)

    # ── M4 — 분모는 실행·단계별 **예상 문항 전체**.
    m4_denom = expected_qid_count * len(stages)
    confirmed = sum(1 for r in rows if r.system_verdict == V_CONFIRMED)
    submitted = confirmed + sum(1 for r in rows if r.system_verdict == V_SELF_REPORTED)
    m4 = _ratio(
        M4, confirmed, m4_denom,
        "분모: 실행·단계별 예상 문항 전체 / 분자: `decision_detail=confirmed`인 행",
        undetermined=hold_count, reasons=hold_reasons,
        note=("**제품 자동응답률(`auto_pct`)이 아니다.** 제품 지표는 분자·분모 조건이 "
              "다르고 제품 코드에 그대로 있다(`meta.product_metrics`에 구획해 둔다). "
              "**'검증 확정률'도 아니다** — 근거 검증 완료율을 뜻하지 않는다. "
              f"이전 이름은 '{M4_PREVIOUS_NAME}'이고 정의·결과는 바뀌지 않았다. "
              "`decision=answer`인 행 전체가 아니다 — 미검증 전달은 분자에서 빠진다. "
              "양쪽 비교(ESGenie·대조군)에 **같은 정의로** 쓴다."),
    )

    # ── M6 값 제출률 — 실제 값이 있는 `confirmed`·`unverified_submitted`.
    #    분모는 M4와 같다. 두 지표를 같은 정의로 양쪽에 쓴다.
    value_submitted = sum(1 for r in rows
                          if r.system_verdict in (V_CONFIRMED, V_SELF_REPORTED)
                          and r.system_value_filled)
    m6 = _ratio(
        M6, value_submitted, m4_denom,
        "분모: 실행·단계별 예상 문항 전체 / 분자: 실제 값이 있는 "
        "`confirmed`·`unverified_submitted` 행",
        undetermined=hold_count, reasons=hold_reasons,
        note=("값을 제출했는지만 본다 — **정답 여부도, 검증 여부도 아니다.** "
              f"이전에는 보조 '{M6_PREVIOUS_NAME}'였고(분자에 값 없는 행도 포함) "
              "이제 값이 채워진 행만 센다. 이전 수치는 A3로 함께 남긴다. "
              "값 `0`·`False`는 빈 값으로 세지 않는다. "
              "양쪽 비교(ESGenie·대조군)에 **같은 정의로** 쓴다."),
    )

    # ── M5 — 분모는 **실제 값이 채워진** `confirmed`·`unverified_submitted` 행.
    #    값 `0`·`False`는 빈 값으로 취급하지 않는다.
    m5_rows = [r for r in rows
               if r.system_verdict in (V_CONFIRMED, V_SELF_REPORTED)
               and r.system_value_filled]
    m5_numer = sum(1 for r in m5_rows if r.system_evidence_count > 0)
    m5 = _ratio(
        M5, m5_numer, len(m5_rows),
        "분모: 실제 값이 채워진 `confirmed`·`unverified_submitted` 행 / "
        "분자: 그중 근거를 연결한 행",
        undetermined=hold_count, reasons=hold_reasons,
        note=("**연결했는지만 본다.** 연결한 근거가 정답인지는 보조 A5에서 따로 센다. "
              "값 `0`·`False`를 빈 값으로 취급하지 않는다. "
              "응답 누락·파싱 실패 행은 억지로 분모에 넣지 않는다 — 입력 완전성에 적는다. "
              "`na` 라벨 행을 추가로 제외하지 않는다."),
    )

    scope = MetricScope(aggregated=aggregated)
    scope.metrics = {m.key: asdict(m) for m in (m1, m2, m3, m4, m5, m6)}
    scope.auxiliary = {
        "A1_값_일치_건수": dict(Counter(_flag(r.value_match) for r in answer_rows)),
        "A2_근거_일치_건수": {
            "파일+쪽_일치(공식)": sum(1 for r in answer_rows if r.source_match is True),
            "파일_일치(보조)": sum(1 for r in answer_rows if r.source_file_match is True),
            "주의": "파일 일치는 공식 수치를 대체하지 않는다. 공식은 파일+쪽이다.",
        },
        "A3_값_제출률_이전정의": asdict(_ratio(
            M6_PREVIOUS_NAME, submitted, m4_denom,
            "분모: 예상 문항 전체 / 분자: `confirmed`+`unverified_submitted`"
            "(값이 비어 있는 행도 포함)",
            undetermined=hold_count, reasons=hold_reasons,
            note=f"이전 정의를 보존한 수치다. 공통 비교 지표는 {M6}다 — 그쪽은 "
                 "값이 채워진 행만 센다. 두 값을 같은 줄에 놓지 않는다.")),
        "A4_올바른_보류_수": sum(1 for r in rows if r.bucket == B_CORRECT_HOLD),
        "A5_연결한_근거가_정답인_비율": asdict(_ratio(
            "A5_연결근거_정답률",
            sum(1 for r in m5_rows if r.source_match is True),
            sum(1 for r in m5_rows if r.system_evidence_count > 0),
            "분모: 근거를 연결한 행 / 분자: 그 근거가 정답 위치와 맞은 행",
            undetermined=hold_count, reasons=hold_reasons,
            note="M5(자료 연결률)와 **다른 지표다.** 같은 것으로 취급하지 않는다.")),
        "A6_미해결_행": unresolved,
        "올바른_답변_수": m1_numer,
        "올바른_해당없음_수": sum(1 for r in rows if r.bucket == B_CORRECT_NA),
        "판정_불일치": {
            "건수": sum(1 for r in rows if r.bucket == B_DECISION_MISMATCH),
            "이유": dict(Counter(r.bucket_reason for r in rows
                                 if r.bucket == B_DECISION_MISMATCH)),
            "주의": ("`decision_mismatch`는 정상 보류도 정답도 아니다. "
                     "`correct_hold`·`correct_answer`에 섞지 않는다."),
        },
        "보류_사유_일치": {
            "사유_일치": sum(1 for r in rows if r.bucket_detail == D_HOLD_REASON_MATCH),
            "사유_불일치": sum(1 for r in rows if r.bucket_detail == D_HOLD_REASON_MISMATCH),
            "사유_미확인": sum(1 for r in rows if r.bucket_detail == D_HOLD_REASON_UNKNOWN),
            "주의": ("보류 **결정**이 맞은 행만 센다. 공통 형식에 시스템 보류 사유를 "
                     "담는 구조화 필드가 없어 대부분 '사유 미확인'이 된다. "
                     "미확인을 사유 일치로 간주하지 않는다."),
        },
        "확정_응답_오류_구분": {
            "값만_오류": sum(1 for r in rows if r.bucket_detail == D_VALUE_ERROR),
            "근거만_오류": sum(1 for r in rows if r.bucket_detail == D_EVIDENCE_ERROR),
            "값·근거_모두_오류": sum(1 for r in rows
                                    if r.bucket_detail == D_VALUE_AND_EVIDENCE_ERROR),
            "주의": "세 가지 모두 `wrong_confirmation`에 들어간다. 원인만 분리한다.",
        },
        "na_라벨_판정": {
            "행": len(na_rows),
            "일치": sum(1 for r in na_rows if r.system_verdict == V_NOT_APPLICABLE),
            "불일치": sum(1 for r in na_rows if r.system_verdict != V_NOT_APPLICABLE),
            "주의": ("`na`는 M1 분모에서 빼고 M4 분모에는 넣는다. M5에서 추가 제외하지 "
                     "않는다. `na` 문항의 오답이 사라지지 않게 일치·불일치를 따로 적는다."),
        },
    }
    scope.input_completeness = {
        "라벨_행": len(labels),
        "채점된_행": len(rows),
        "응답_누락_행": len(labels) - len(rows),
        "파싱_실패_행": sum(1 for r in rows if r.system_verdict == V_UNPARSED),
        "판정_규칙_미정_행": sum(1 for r in rows if r.system_verdict == V_UNDETERMINED),
        "주의": "누락·파싱 실패를 정상 보류로 바꾸지 않는다. M1·M4 분모에 포함하고 분자에서 뺀다.",
    }
    return scope


def compute_metric_scopes(rep: "ScoreReport", labels: list[Label],
                          expected_qid_count: int) -> list[MetricScope]:
    """보고 단위별 지표. **단계별이 기본이고, 합산은 두 단계가 모두 있을 때만 참고로 낸다.**"""
    stages = tuple(s for s in STAGES if any(r.stage == s for r in rep.rows))
    out: list[MetricScope] = []
    for s in stages:
        sc = _metric_scope([r for r in rep.rows if r.stage == s],
                           [l for l in labels if l.stage == s],
                           expected_qid_count, (s,), aggregated=False)
        sc.stage = s
        sc.label = s
        out.append(sc)
    if len(stages) > 1:
        sc = _metric_scope(list(rep.rows), list(labels), expected_qid_count,
                           stages, aggregated=True)
        sc.stage = "+".join(stages)
        sc.label = f"{'+'.join(stages)} 합산(참고)"
        out.append(sc)
    return out


@dataclass
class ScoreReport:
    """채점 결과 — 행별 분류·건수와 **확정된 산식의 지표**. 종합 점수는 내지 않는다."""
    rows: list[RowScore] = field(default_factory=list)
    verdict_counts: dict[str, int] = field(default_factory=dict)
    bucket_counts: dict[str, int] = field(default_factory=dict)
    detail_counts: dict[str, int] = field(default_factory=dict)
    value_match_counts: dict[str, int] = field(default_factory=dict)
    source_match_counts: dict[str, int] = field(default_factory=dict)
    source_file_match_counts: dict[str, int] = field(default_factory=dict)
    #: 보고 단위별 지표. **시스템·실행·단계별이 기본**이고 합산은 참고다.
    metric_scopes: list[dict[str, Any]] = field(default_factory=list)
    unresolved_reasons: dict[str, int] = field(default_factory=dict)
    unscored_pairs: list[str] = field(default_factory=list)
    structure: StructureReport = field(default_factory=StructureReport)
    #: 채점에 쓴 입력 문서들 — 어느 시스템·실행·단계·자료 출처였는지 남긴다.
    documents: list[dict[str, Any]] = field(default_factory=list)
    #: 지표별 상태 — 하나의 문장으로 전부 차단하지 않는다. 보류는 지표·보고 단위별이다.
    metrics_policy: dict[str, Any] = field(default_factory=lambda: {
        "확정일": "2026-10-06",
        "종합_점수": "만들지 않는다. 확정 요구사항이고 향후 구현 대상도 아니다.",
        "보고_단위": ("시스템·실행·단계별이 기본이다. initial+followup 합산은 참고이며, "
                      "한 단계만 있으면 합산을 만들지 않고 '전체'라고 부르지 않는다."),
        "표본_분리": "공식 표본과 수치형 전수는 분리해 보고한다. 합치지 않는다.",
        "반복_실행": "실행별로 보존한다. 표본 수가 늘어난 것처럼 보고하지 않는다.",
        "산출_불가": "분모 0은 산출 불가다. 0%로 적지 않는다.",
        "미정_조합": ("`unresolved` 행이 **1건이라도 남은 실행·단계**는 그 보고 단위의 "
                      "공식 비율 전체를 보류하고, 확정 행별 결과·부분 건수·미정 건수·"
                      "이유를 낸다. 분모에서 빼서 비율을 만들지 않고, '그 지표의 분모에 "
                      "영향을 주는 미정만' 보는 것으로 좁히지도 않는다."),
        "집계_판정표": ("2026-10-06 확정. §6.1 시스템 판정 매핑과 §6.2 미검증 전달 규칙은 "
                        "바뀌지 않았다. 그 뒤의 집계만 정했다."),
        "판정_불일치": ("`decision_mismatch`는 정상 보류도 정답도 아니다. 별도 건수와 "
                        "이유로만 남긴다."),
        "보류_사유": ("공통 형식에 시스템 보류 사유를 담는 구조화 필드가 없다. 사유가 "
                      "확인되지 않은 행을 사유 일치로 간주하지 않는다."),
    })

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

        # **상세 상태로만 채점한다.** 외부 3값(`answer`)은 확정 여부를 말해 주지 않는다.
        verdict, verdict_reason = answer.decision_detail, answer.decision_basis
        value_match, value_reason = compare_value(label, answer)
        source_match, source_reason = compare_sources(label, answer)
        file_match, file_reason = compare_sources_file_only(label, answer)
        bucket, detail, bucket_reason = assign_bucket(
            verdict, label, value_match, source_match, answer.source_comparison,
            value_reason, source_reason)

        rep.rows.append(RowScore(
            stage=label.stage, qid=label.qid,
            system_status=answer.source_status,
            system_comparison=answer.source_comparison,
            system_value_filled=_is_filled(answer.value),
            system_evidence_count=len(answer.evidence),
            system_verdict=verdict, verdict_reason=verdict_reason,
            system_decision=answer.decision,
            label_decision=label.expected_decision, label_hold_reason=label.hold_reason,
            value_match=value_match, value_reason=value_reason,
            source_match=source_match, source_reason=source_reason,
            source_file_match=file_match, source_file_reason=file_reason,
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
    rep.source_file_match_counts = dict(Counter(
        _flag(r.source_file_match) for r in rep.rows))
    rep.unresolved_reasons = dict(Counter(
        r.bucket_reason for r in rep.rows if r.bucket == B_UNRESOLVED
    ))
    rep.unscored_pairs.sort()
    rep.metric_scopes = [asdict(s) for s in
                         compute_metric_scopes(rep, labels, len(set(expected_qids)))]
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

    # 보고 단위에 시스템·실행을 붙인다. **단계마다 문서가 하나**라는 제약(위) 덕에
    # 단계 → 문서가 1:1이다. 실행이 섞이면 합산 단위는 비워 둔다.
    by_stage_meta = {str((d.get("meta") or {}).get("stage") or ""): (d.get("meta") or {})
                     for d in documents}
    systems = {str(m.get("system") or "") for m in by_stage_meta.values()}
    runs = {str(m.get("run_id") or "") for m in by_stage_meta.values()}
    for scope in rep.metric_scopes:
        meta = by_stage_meta.get(scope["stage"])
        if meta is not None:
            scope["system"] = str(meta.get("system") or "")
            scope["run_id"] = str(meta.get("run_id") or "")
            scope["label"] = f"{scope['system']} / {scope['run_id']} / {scope['stage']}"
        elif len(systems) == 1 and len(runs) == 1:
            scope["system"], scope["run_id"] = next(iter(systems)), next(iter(runs))
            scope["label"] = (f"{scope['system']} / {scope['run_id']} / "
                              f"{scope['stage']} 합산(참고)")
        else:
            scope["label"] = (f"{scope['stage']} 합산 — 시스템·실행이 섞여 있어 "
                              "합산 수치를 쓰지 않는다")

    for doc in documents:
        meta = doc.get("meta") or {}
        rep.documents.append({
            "system": meta.get("system"), "model": meta.get("model"),
            "run_id": meta.get("run_id"), "stage": meta.get("stage"),
            "framework": meta.get("framework"),
            # §4 원문 자료 집합 구분과 입력 자료 구성은 **다른 축**이다. 섞지 않는다.
            "dataset_tag": meta.get("dataset_tag"),
            "material_kind": meta.get("material_kind"),
            "data_source": meta.get("data_source"),
            "format_version": doc.get("format_version"),
            "adapter": meta.get("adapter"), "source_ref": meta.get("source_ref"),
            "answer_rows": len(doc.get("answers") or []),
            # 제품 고유 지표는 **구획해서** 그대로 전달한다. 공통 지표로 바꾸지 않는다.
            # 없는 시스템(대조군)에는 같은 이름의 값을 만들어 넣지 않는다 — `null`이다.
            "product_metrics": meta.get("product_metrics"),
            "product_metrics_note":
                ("제품 고유 지표다. 공통 비교 지표(M4 확정 제출률·M6 값 제출률)와 "
                 "같은 줄에 놓지 않는다. 대조군에는 이 지표가 없으므로 null이며, "
                 "없는 것을 0으로 적지 않는다."),
            "decision_mapping":
                {k: v for k, v in af.DETAIL_TO_DECISION.items()},
        })
    return rep
