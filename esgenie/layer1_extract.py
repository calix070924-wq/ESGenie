"""Layer 1 — DART 사업보고서에서 K-ESG 항목 자동 추출 (프로파일 기반).

K-ESG 61항목 체계 위에서 기업 규모에 맞는 프로파일을 적용한다:
  sme  — 중소기업 기본형 28항목 (커버리지 분모 = 28)
  full — 61항목 전체 (커버리지 분모 = 61)
  None — corp_code로 자동 판별 (상장 6자리 숫자 → full, 그 외 → sme)

프로파일 밖 항목이 데이터에 존재하면 beyond_profile=True로 함께 추출하되
커버리지 계산에는 포함하지 않는다 (추가 공시는 보너스, 분모 왜곡 방지).

v10 변경:
- ExtractionResult에 evidence_node_ids 필드 추가
- extract()에 evidence_graph 선택 인자 추가 (default=None, 하위 호환)
- 수치 항목에 한해 L0 노드 매칭 → evidence_node_ids 부착
- 매칭 실패 시 evidence_node_ids=[], confidence_flags에 "no_evidence" 기록
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .dart_client import CompanyReport
from .knowledge.kesg_items import (
    ALL_ITEMS,
    KESGItem,
    PROFILE_LABELS,
    Profile,
    by_code,
    detect_profile,
    items_for_profile,
)


@dataclass
class ExtractionResult:
    corp_name: str
    mapped: dict[str, dict[str, Any]]      # code → entry (evidence_node_ids 포함)
    missing: list[str]                     # 누락 항목 코드 (프로파일 내)
    coverage_pct: float                    # 프로파일 기준 커버리지
    by_area: dict[str, dict[str, int]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    # v10 신설: 항목별 신뢰도 플래그 (코드 → 플래그 목록)
    confidence_flags: dict[str, list[str]] = field(default_factory=dict)
    # 프로파일 정보
    profile: str = "full"
    profile_label: str = ""
    beyond_profile: list[str] = field(default_factory=list)  # 프로파일 밖 추가 공시 코드


def extract(
    report: CompanyReport,
    evidence_graph: Any | None = None,  # EvidenceGraph | None (순환 임포트 회피)
    profile: Profile | None = None,     # None → corp_code로 자동 판별
) -> ExtractionResult:
    """K-ESG 항목 추출 (프로파일 기준).

    evidence_graph가 주어지면 각 항목에 L0 노드 ID를 부착한다.
    없으면 기존(v9) 동작과 동일하게 evidence_node_ids=[]로 설정된다.
    """
    if profile is None:
        profile = detect_profile(report.corp_code)
    profile_items = items_for_profile(profile)
    profile_codes = {it.code for it in profile_items}

    mapped: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    beyond: list[str] = []
    confidence_flags: dict[str, list[str]] = {}
    by_area: dict[str, dict[str, int]] = {a: {"present": 0, "total": 0} for a in ("P", "E", "S", "G")}

    for item in ALL_ITEMS:
        in_profile = item.code in profile_codes
        if in_profile:
            by_area[item.area]["total"] += 1

        entry = report.kesg_data.get(item.code)
        if not entry:
            if in_profile:
                missing.append(item.code)
            continue

        # evidence_node_ids 결정
        node_ids = _match_evidence_nodes(item.code, report, evidence_graph)
        flags: list[str] = []
        if not node_ids and item.data_type == "정량":
            flags.append("no_evidence")
        if item.data_type == "정량" and _unit_suspect(entry.get("unit"), item.unit):
            flags.append("unit_suspect")

        mapped[item.code] = {
            "code":              item.code,
            "name":              item.name,
            "area":              item.area,
            "category":          item.category,
            "data_type":         item.data_type,
            "value":             entry.get("value"),
            "unit":              entry.get("unit"),
            "note":              entry.get("note"),
            "evidence_node_ids": node_ids,
            "beyond_profile":    not in_profile,
        }
        if flags:
            confidence_flags[item.code] = flags
        if in_profile:
            by_area[item.area]["present"] += 1
        else:
            beyond.append(item.code)

    in_profile_mapped = len(mapped) - len(beyond)
    coverage_pct = 100 * in_profile_mapped / len(profile_items)
    notes = [
        f"프로파일: {PROFILE_LABELS[profile]}",
        f"DART + 지속가능경영보고서 기반 {in_profile_mapped}/{len(profile_items)} 항목 추출 완료",
        f"누락 {len(missing)}개 항목은 Layer 2 생성 단계에서 RAG로 보완",
    ]
    if beyond:
        notes.append(f"프로파일 외 추가 공시 {len(beyond)}개 항목 (커버리지 분모 미포함)")
    if evidence_graph is not None:
        attached = sum(1 for v in mapped.values() if v.get("evidence_node_ids"))
        notes.append(f"L0 Evidence 노드 부착: {attached}개 항목")

    return ExtractionResult(
        corp_name=report.corp_name,
        mapped=mapped,
        missing=missing,
        coverage_pct=coverage_pct,
        by_area=by_area,
        notes=notes,
        confidence_flags=confidence_flags,
        profile=profile,
        profile_label=PROFILE_LABELS[profile],
        beyond_profile=beyond,
    )


def _match_evidence_nodes(
    code: str,
    report: CompanyReport,
    evidence_graph: Any | None,
) -> list[str]:
    """K-ESG 코드에 대응하는 L0 EvidenceNode ID 목록 반환.

    매칭 전략:
    1. K-ESG 코드를 키워드로 직접 검색 (가장 정확)
    2. 코드만으로 놓치는 동의어 표기를 SearchTerm으로 보강 (ESGReveal <SearchTerm>)
    3. 매칭 결과를 현재 보고 연도로 필터
    """
    if evidence_graph is None:
        return []
    # 코드 직접 매칭(최우선) + 항목별 동의어로 재현율 보강.
    keywords = [code]
    item = by_code(code)
    if item is not None and item.search_terms:
        keywords.extend(item.search_terms)
    nodes = evidence_graph.search_nodes(
        keywords=keywords,
        period=report.report_year,
    )
    return [n.id for n in nodes]


def missing_items_detail(missing: list[str]) -> list[KESGItem]:
    return [it for it in ALL_ITEMS if it.code in missing]


# ====================================================================
# 증빙 연결 커버리지 + 단위 타당성 (Phase 2, 2026-07-17)
# ====================================================================

def evidence_coverage_pct(extraction: "ExtractionResult") -> float:
    """프로파일 내 항목 중 실제 증빙 노드(L0)가 연결된 비율.

    coverage_pct(값 존재)와 분리된 지표 — 합성값·설문(survey_*) 응답처럼 값만 있는
    항목은 분자에 들어가지 않는다. 상태를 저장하지 않고 호출 시점에 계산하므로
    survey 주입(_apply_survey_answers) 이후에도 낡은 값이 되지 않는다.
    """
    beyond = set(extraction.beyond_profile or [])
    in_profile = [c for c in extraction.mapped if c not in beyond]
    denom = len(in_profile) + len(extraction.missing or [])
    if denom == 0:
        return 0.0
    linked = 0
    for code in in_profile:
        ev = extraction.mapped[code].get("evidence_node_ids") or []
        if any(not str(e).startswith("survey_") for e in ev):
            linked += 1
    return 100.0 * linked / denom


def _relaxed_unit(u: str) -> str:
    """공백 제거·소문자화 + 흔한 동의 표기 축약 ('ton CO2eq'→'tco2eq')."""
    s = re.sub(r"\s+", "", str(u)).lower()
    s = s.replace("co₂", "co2").replace("톤", "t")
    s = re.sub(r"^tons?(?=co2|$)", "t", s)
    return s


def _unit_suspect(extracted: Any, expected: str) -> bool:
    """추출 단위가 항목 정의 단위(kesg_items.unit)와 명백히 다르면 True.

    보수적 판정 — 둘 다 비어있지 않고, 관대 정규화 후에도 다르고, 환산 그룹
    (kWh↔MWh 등)으로도 호환되지 않을 때만 플래그. 오탐(정상 단위에 검증필요
    표기)보다 미탐이 낫다는 게 아니라, 표기 요동('ton CO2eq' vs 'tCO2eq')을
    오결합('명' vs 'TJ')과 구분하기 위한 것."""
    if not extracted or not expected:
        return False
    a, b = _relaxed_unit(extracted), _relaxed_unit(expected)
    if a == b:
        return False
    from .rag_gates.units import normalize_unit, units_compatible
    na, nb = normalize_unit(a), normalize_unit(b)
    if na is not None and nb is not None and units_compatible(na, nb):
        return False
    return True
