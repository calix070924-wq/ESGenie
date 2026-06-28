"""공급망 실사 응답 출력 모듈 — 데이터 모델.

대기업(OEM)이 협력사에 보내는 ESG 자가진단 양식을, ESGenie의 기존 산출물
(L1 추출 / D6 선택적 공시 / v15 data_points 증빙)으로 자동 응답한다.

설계 원칙
---------
* 양식(Framework)은 **선언적 설정**이다. 코드가 아니라 Question 목록.
  → OEM/산업 추가 = 설정 추가 (industry/ 모듈과 동일 패턴).
* 응답(Answer)은 항상 **증빙 링크 + 신뢰 상태**를 동반한다.
  → 검출 결과(D1/D6)가 "점수"가 아니라 "출력물을 신뢰하게 만드는 근거"로 합쳐짐.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Literal

# 기존 audit_trace의 증빙 링크를 그대로 재사용 (별도 타입 신설 금지).
from ..ssot.audit_trace import EvidenceLink

QType = Literal["yes_no", "yes_no_evidence", "multi_select", "numeric", "text"]

# 답변 신뢰 상태 → 출력 배지
#   insufficient  : 증빙만 올리면 자동으로 풀림(데이터 타입은 정량/공시존재형).
#   hitl_required : 증빙이 있어도 사람이 직접 서술해야 함(정성 내부정책 서술형).
#                   → insufficient와 의미가 다름. 증빙 추가로는 자동화되지 않는다.
#   not_applicable: 해당 기업/문항에 적용되지 않음 → 커버리지 분모에서 제외.
AnswerStatus = Literal[
    "verified", "self_reported", "insufficient", "flagged",
    "hitl_required", "not_applicable",
]

_BADGE: dict[str, tuple[str, str]] = {
    "verified":       ("✅", "증빙검증"),    # 원본 증빙 + D1 통과
    "self_reported":  ("⚠️", "자가신고"),    # 응답은 있으나 증빙 미연결
    "insufficient":   ("❗", "데이터부족"),   # 양식이 요구하나 증빙 없음 → 증빙 올리면 풀림
    "flagged":        ("🚩", "검토필요"),    # D1 불일치 / D6 누락 / 과장 의심
    "hitl_required":  ("✍️", "작성필요"),    # 사람이 직접 서술해야 함(증빙 추가로 자동화 불가)
    "not_applicable": ("➖", "해당없음"),     # 적용 불가 → 분모 제외
}

# ── status 그룹 (3분할 집계용) ────────────────────────────────────────────────
#   자동응답 = 기계가 답을 채운 문항(검토필요 flagged 포함 — 답 자체는 채워짐)
#   사람필요 = 사람이 서술해야 풀리는 문항(hitl_required)
#   증빙대기 = 증빙만 올리면 풀리는 문항(insufficient)
#   not_applicable은 어느 그룹에도 들지 않고 분모에서 빠진다.
_AUTO_STATUSES: tuple[str, ...] = ("verified", "self_reported", "flagged")
_HITL_STATUSES: tuple[str, ...] = ("hitl_required",)
_PENDING_STATUSES: tuple[str, ...] = ("insufficient",)


@dataclass(frozen=True)
class Question:
    """양식의 한 문항 + 무엇으로 답하는가에 대한 매핑 메타데이터."""
    qid: str
    section: str
    text: str
    qtype: QType
    evidence_required: bool = False
    # 존재형/수치형 — 첫 코드를 대표 코드로 사용
    kesg_codes: tuple[str, ...] = ()
    # 체크형(multi_select) — (보기 라벨, 충족 판정 K-ESG 코드들)
    option_map: tuple[tuple[str, tuple[str, ...]], ...] = ()
    unit_hint: str = ""

    @property
    def primary_code(self) -> str:
        return self.kesg_codes[0] if self.kesg_codes else ""


@dataclass(frozen=True)
class Framework:
    """공급망 실사 양식 (선언적)."""
    key: str
    label: str
    questions: tuple[Question, ...]
    # 양식이 속한 기둥(pillar): "disclosure"(공시) | "due_diligence"(실사).
    # 기본값 disclosure → 기존 양식(K-ESG/SAQ)은 무회귀, 실사 양식만 명시 지정.
    pillar: str = "disclosure"

    def __post_init__(self) -> None:
        if not self.questions:
            raise ValueError(f"Framework '{self.key}'에 문항이 없습니다.")


@dataclass
class Answer:
    """문항 1개에 대한 자동 응답 + 신뢰/증빙."""
    qid: str
    section: str
    question_text: str
    value: Any                       # bool / list[str] / float / str / None
    status: AnswerStatus
    evidence_links: list[EvidenceLink] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    rationale: str = ""
    # 미해소(insufficient/hitl_required) 시 '무엇을 올리면/작성하면 풀리는가'.
    # derive 시 증빙요구 룩업에서 채워, 응답이 자기기술적이 되게 한다(체크리스트/exporter/UI 재사용).
    evidence_needed: list[str] = field(default_factory=list)

    @property
    def badge(self) -> str:
        emoji, label = _BADGE[self.status]
        return f"{emoji} {label}"

    @property
    def answered(self) -> bool:
        """집계용 — 실제로 응답이 채워졌는가(부족/미채움 제외)."""
        return self.status in ("verified", "self_reported", "flagged")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["evidence_links"] = [e.to_dict() for e in self.evidence_links]
        d["badge"] = self.badge
        return d


@dataclass
class ResponseSheet:
    """완성된 자동 응답서 — Excel/PDF 출력의 원천."""
    framework_key: str
    framework_label: str
    corp_name: str
    answers: list[Answer] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)

    @property
    def denominator(self) -> int:
        """커버리지 분모 — not_applicable(해당없음)은 제외한다."""
        return sum(1 for a in self.answers if a.status != "not_applicable")

    def _pct(self, statuses: tuple[str, ...]) -> float:
        denom = self.denominator
        if denom == 0:
            return 0.0
        n = sum(1 for a in self.answers if a.status in statuses)
        return round(100.0 * n / denom, 1)

    @property
    def auto_pct(self) -> float:
        """자동응답% — 기계가 답을 채운 문항(검토필요 포함)."""
        return self._pct(_AUTO_STATUSES)

    @property
    def hitl_pct(self) -> float:
        """사람필요% — 사람이 직접 서술해야 하는 문항."""
        return self._pct(_HITL_STATUSES)

    @property
    def pending_pct(self) -> float:
        """증빙대기% — 증빙만 올리면 풀리는 문항."""
        return self._pct(_PENDING_STATUSES)

    @property
    def coverage_pct(self) -> float:
        """하위호환 별칭 — '자동응답 커버리지'를 의미한다."""
        return self.auto_pct

    @property
    def flagged_count(self) -> int:
        return sum(1 for a in self.answers if a.status == "flagged")

    def to_dict(self) -> dict[str, Any]:
        return {
            "framework_key": self.framework_key,
            "framework_label": self.framework_label,
            "corp_name": self.corp_name,
            "coverage_pct": self.coverage_pct,
            "auto_pct": self.auto_pct,
            "hitl_pct": self.hitl_pct,
            "pending_pct": self.pending_pct,
            "flagged_count": self.flagged_count,
            "answers": [a.to_dict() for a in self.answers],
            "gaps": self.gaps,
        }
