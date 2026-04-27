"""Layer 1 — DART 사업보고서에서 K-ESG 61개 항목 자동 추출.

v10 변경:
- ExtractionResult에 evidence_node_ids 필드 추가
- extract()에 evidence_graph 선택 인자 추가 (default=None, 하위 호환)
- 수치 항목에 한해 L0 노드 매칭 → evidence_node_ids 부착
- 매칭 실패 시 evidence_node_ids=[], confidence_flags에 "no_evidence" 기록
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .dart_client import CompanyReport
from .knowledge.kesg_items import ALL_ITEMS, KESGItem


@dataclass
class ExtractionResult:
    corp_name: str
    mapped: dict[str, dict[str, Any]]      # code → entry (evidence_node_ids 포함)
    missing: list[str]                     # 누락 항목 코드
    coverage_pct: float
    by_area: dict[str, dict[str, int]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    # v10 신설: 항목별 신뢰도 플래그 (코드 → 플래그 목록)
    confidence_flags: dict[str, list[str]] = field(default_factory=dict)


def extract(
    report: CompanyReport,
    evidence_graph: Any | None = None,  # EvidenceGraph | None (순환 임포트 회피)
) -> ExtractionResult:
    """K-ESG 61개 항목 추출.

    evidence_graph가 주어지면 각 항목에 L0 노드 ID를 부착한다.
    없으면 기존(v9) 동작과 동일하게 evidence_node_ids=[]로 설정된다.
    """
    mapped: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    confidence_flags: dict[str, list[str]] = {}
    by_area: dict[str, dict[str, int]] = {a: {"present": 0, "total": 0} for a in ("P", "E", "S", "G")}

    for item in ALL_ITEMS:
        by_area[item.area]["total"] += 1
        entry = report.kesg_data.get(item.code)
        if entry:
            # evidence_node_ids 결정
            node_ids = _match_evidence_nodes(item.code, report, evidence_graph)
            flags: list[str] = []
            if not node_ids and item.data_type == "정량":
                flags.append("no_evidence")

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
            }
            if flags:
                confidence_flags[item.code] = flags
            by_area[item.area]["present"] += 1
        else:
            missing.append(item.code)

    coverage_pct = 100 * len(mapped) / len(ALL_ITEMS)
    notes = [
        f"DART + 지속가능경영보고서 기반 {len(mapped)}/{len(ALL_ITEMS)} 항목 추출 완료",
        f"누락 {len(missing)}개 항목은 Layer 2 생성 단계에서 RAG로 보완",
    ]
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
    )


def _match_evidence_nodes(
    code: str,
    report: CompanyReport,
    evidence_graph: Any | None,
) -> list[str]:
    """K-ESG 코드에 대응하는 L0 EvidenceNode ID 목록 반환.

    매칭 전략:
    1. K-ESG 코드를 키워드로 직접 검색 (가장 정확)
    2. 매칭 결과를 현재 보고 연도로 필터
    """
    if evidence_graph is None:
        return []
    # EvidenceGraph.search_nodes는 코드 직접 매칭을 최우선으로 처리
    nodes = evidence_graph.search_nodes(
        keywords=[code],
        period=report.report_year,
    )
    return [n.id for n in nodes]


def missing_items_detail(missing: list[str]) -> list[KESGItem]:
    return [it for it in ALL_ITEMS if it.code in missing]
