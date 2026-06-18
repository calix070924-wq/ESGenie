"""ISSB/KSSB 얇은 갭 리포트.

설계 원칙
---------
- K-ESG 추출 결과(extraction) 위에 ISSB/KSSB 매핑을 얇게 덧씌운다.
- 판단 로직은 넣지 않고, 상태 분류는 공시됨/누락/프로파일 외 참고만 계산한다.
- SME 프로파일에서는 범위 밖 ISSB 항목을 "누락"으로 보지 않고 out_of_scope로 둔다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .knowledge.issb_mapping import MAPPINGS, PILLAR_LABELS, mappings_for
from .knowledge.kesg_items import by_code, items_for_profile

GapStatus = Literal["disclosed", "missing", "out_of_scope"]
EvidenceStatus = Literal["verified", "self_reported", "missing", "out_of_scope"]
ScopeStatus = Literal["in_profile", "beyond_profile"]

STATUS_LABELS: dict[str, str] = {
    "disclosed": "공시됨",
    "missing": "누락",
    "out_of_scope": "프로파일 외 참고",
}

EVIDENCE_LABELS: dict[str, str] = {
    "verified": "증빙 연결",
    "self_reported": "자기기재",
    "missing": "누락",
    "out_of_scope": "프로파일 외",
}

SCOPE_LABELS: dict[str, str] = {
    "in_profile": "프로파일 포함",
    "beyond_profile": "프로파일 외 참고",
}

SUGGESTED_EVIDENCE: dict[str, tuple[str, ...]] = {
    "E-1-1": (
        "이사회/경영진 승인 환경·기후 목표 문서",
        "연도별 감축 목표 및 KPI 표",
    ),
    "E-3-1": (
        "Scope1·2 배출량 산정표",
        "전력·연료 사용 원천 증빙(고지서·명세서)",
    ),
    "E-3-2": (
        "Scope3 산정 경계·방법론 문서",
        "카테고리별 공급망·물류 활동데이터",
    ),
    "E-3-3": (
        "온실가스 제3자 검증의견서",
        "검증기관명·검증범위 명시 페이지",
    ),
    "E-4-1": (
        "전기·가스 사용량 집계표",
        "에너지 사용 원천 증빙(고지서·계량기록)",
    ),
    "E-4-2": (
        "재생에너지 사용량 산정표",
        "REC·PPA·녹색프리미엄 계약/정산 증빙",
    ),
    "G-1-1": (
        "이사회 ESG 안건 상정 내역",
        "이사회 회의록 또는 보고자료",
    ),
    "S-5-2": (
        "인권 리스크 평가 보고서",
        "고위험 공급망·사업장 실사 기록",
    ),
}


@dataclass(frozen=True)
class ISSBGapRow:
    kesg_code: str
    name: str
    standards: tuple[str, ...]
    kssb: tuple[str, ...]
    pillars: tuple[str, ...]
    anchors: tuple[str, ...]
    requirements: tuple[str, ...]
    status: GapStatus
    evidence_status: EvidenceStatus
    scope: ScopeStatus
    evidence_count: int = 0

    @property
    def pillar_labels(self) -> tuple[str, ...]:
        return tuple(PILLAR_LABELS[pillar] for pillar in self.pillars)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kesg_code": self.kesg_code,
            "name": self.name,
            "standards": list(self.standards),
            "kssb": list(self.kssb),
            "pillars": list(self.pillars),
            "pillar_labels": list(self.pillar_labels),
            "anchors": list(self.anchors),
            "requirements": list(self.requirements),
            "status": self.status,
            "evidence_status": self.evidence_status,
            "scope": self.scope,
            "evidence_count": self.evidence_count,
        }


@dataclass(frozen=True)
class ISSBAnchorSummary:
    anchor: str
    total: int
    disclosed: int
    missing: int
    out_of_scope: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchor": self.anchor,
            "total": self.total,
            "disclosed": self.disclosed,
            "missing": self.missing,
            "out_of_scope": self.out_of_scope,
        }


@dataclass(frozen=True)
class ISSBGapReport:
    rows: tuple[ISSBGapRow, ...]
    profile: str
    profile_label: str
    in_profile_total: int
    in_profile_disclosed: int
    in_profile_missing: int
    beyond_profile_disclosed: int
    out_of_scope_total: int
    verified_count: int
    self_reported_count: int
    anchor_summary: tuple[ISSBAnchorSummary, ...]
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": [row.to_dict() for row in self.rows],
            "profile": self.profile,
            "profile_label": self.profile_label,
            "in_profile_total": self.in_profile_total,
            "in_profile_disclosed": self.in_profile_disclosed,
            "in_profile_missing": self.in_profile_missing,
            "beyond_profile_disclosed": self.beyond_profile_disclosed,
            "out_of_scope_total": self.out_of_scope_total,
            "verified_count": self.verified_count,
            "self_reported_count": self.self_reported_count,
            "anchor_summary": [summary.to_dict() for summary in self.anchor_summary],
            "rationale": self.rationale,
        }


def build_issb_gap_report(extraction: Any) -> ISSBGapReport:
    """ExtractionResult 위에 ISSB/KSSB 갭 요약을 계산한다."""
    profile = getattr(extraction, "profile", "full")
    profile_label = getattr(extraction, "profile_label", profile)
    profile_codes = {item.code for item in items_for_profile(profile)}
    mapped: dict[str, dict[str, Any]] = getattr(extraction, "mapped", {}) or {}
    missing: set[str] = set(getattr(extraction, "missing", []) or [])

    rows = tuple(_build_row(code, profile_codes, mapped, missing) for code in _mapped_codes())
    in_profile_rows = tuple(row for row in rows if row.scope == "in_profile")

    anchor_summary = tuple(
        ISSBAnchorSummary(
            anchor=anchor,
            total=len(group),
            disclosed=sum(1 for row in group if row.status == "disclosed"),
            missing=sum(1 for row in group if row.status == "missing"),
            out_of_scope=sum(1 for row in group if row.status == "out_of_scope"),
        )
        for anchor, group in _group_rows_by_anchor(rows).items()
    )

    in_profile_total = len(in_profile_rows)
    in_profile_disclosed = sum(1 for row in in_profile_rows if row.status == "disclosed")
    in_profile_missing = sum(1 for row in in_profile_rows if row.status == "missing")
    beyond_profile_disclosed = sum(
        1 for row in rows if row.scope == "beyond_profile" and row.status == "disclosed"
    )
    out_of_scope_total = sum(1 for row in rows if row.status == "out_of_scope")
    verified_count = sum(1 for row in rows if row.evidence_status == "verified")
    self_reported_count = sum(1 for row in rows if row.evidence_status == "self_reported")

    parts = [f"프로파일 내 ISSB 연계 {in_profile_disclosed}/{in_profile_total}개 공시"]
    if in_profile_missing:
        parts.append(f"누락 {in_profile_missing}개")
    if beyond_profile_disclosed:
        parts.append(f"프로파일 외 ISSB 참고 공시 {beyond_profile_disclosed}개")
    rationale = " | ".join(parts)

    return ISSBGapReport(
        rows=rows,
        profile=profile,
        profile_label=profile_label,
        in_profile_total=in_profile_total,
        in_profile_disclosed=in_profile_disclosed,
        in_profile_missing=in_profile_missing,
        beyond_profile_disclosed=beyond_profile_disclosed,
        out_of_scope_total=out_of_scope_total,
        verified_count=verified_count,
        self_reported_count=self_reported_count,
        anchor_summary=anchor_summary,
        rationale=rationale,
    )


def rows_for_anchor(report: ISSBGapReport, anchor: str) -> list[ISSBGapRow]:
    return [row for row in report.rows if anchor in row.anchors]


def suggested_evidence_for(code: str) -> tuple[str, ...]:
    return SUGGESTED_EVIDENCE.get(code, ())


def remediation_text_for(code: str) -> str:
    suggestions = suggested_evidence_for(code)
    return " / ".join(suggestions)


def _mapped_codes() -> list[str]:
    seen: set[str] = set()
    codes: list[str] = []
    for mapping in MAPPINGS:
        if mapping.kesg_code in seen:
            continue
        seen.add(mapping.kesg_code)
        codes.append(mapping.kesg_code)
    return codes


def _build_row(
    code: str,
    profile_codes: set[str],
    mapped: dict[str, dict[str, Any]],
    missing: set[str],
) -> ISSBGapRow:
    item = by_code(code)
    code_mappings = mappings_for(code)
    entry = mapped.get(code, {})
    scope: ScopeStatus = "in_profile" if code in profile_codes else "beyond_profile"

    if code in mapped:
        status: GapStatus = "disclosed"
        evidence_ids = list(entry.get("evidence_node_ids", []) or [])
        evidence_status: EvidenceStatus = "verified" if evidence_ids else "self_reported"
        evidence_count = len(evidence_ids)
    elif code in missing:
        status = "missing"
        evidence_status = "missing"
        evidence_count = 0
    else:
        status = "out_of_scope"
        evidence_status = "out_of_scope"
        evidence_count = 0

    return ISSBGapRow(
        kesg_code=code,
        name=item.name if item is not None else code,
        standards=tuple(_uniq(mapping.standard for mapping in code_mappings)),
        kssb=tuple(_uniq(mapping.kssb for mapping in code_mappings)),
        pillars=tuple(_uniq(mapping.pillar for mapping in code_mappings)),
        anchors=tuple(_uniq(mapping.anchor for mapping in code_mappings)),
        requirements=tuple(_uniq(mapping.requirement for mapping in code_mappings)),
        status=status,
        evidence_status=evidence_status,
        scope=scope,
        evidence_count=evidence_count,
    )


def _group_rows_by_anchor(rows: tuple[ISSBGapRow, ...]) -> dict[str, list[ISSBGapRow]]:
    grouped: dict[str, list[ISSBGapRow]] = {}
    for row in rows:
        for anchor in row.anchors:
            grouped.setdefault(anchor, []).append(row)
    return grouped


def _uniq(values) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out
