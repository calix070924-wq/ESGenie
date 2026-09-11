"""확정 원장의 값과 출처를 출력·D1이 함께 소비하는 선택 계약.

resolved_facts에 코드가 없으면 구버전 입력, None이면 명시적 미확정이다.
후자는 후보 재선택으로 되살리지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from math import isclose, isfinite
from typing import Any

from .node_select import classify_value_role, is_partial_aggregate, normalize_to_item_unit, select_representative_node
from ..rag_gates.units import normalize_unit, convert_to_common


@dataclass(frozen=True)
class ResolvedFact:
    code: str
    value: float
    unit: str
    period: int | None
    source_tier: str
    representative_node_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    value_role: str = "unknown"
    flags: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def finite_number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _is_derived(node):
    return str(node.source).startswith("derived_from:")


def select_fact_nodes(graph, code):
    """원장 미실행/구버전의 공용 대체 선택. 총량 우선, 합산 허용 코드는 Scope1+2뿐."""
    from ..survey import is_survey
    nodes = [n for n in graph.nodes_by_metric(code)
             if finite_number(n.value) is not None and not is_survey(n)]
    year = getattr(graph, "report_year", None)
    reported = [n for n in nodes if not _is_derived(n)]
    if not reported and code == "E-3-1":
        derived = [n for n in nodes if _is_derived(n)
                   and classify_value_role(code, n, report_year=year) != "target"]
        if derived:
            period = min({n.period for n in derived}, key=lambda p: (abs(p-year), -p)) if year else max(n.period for n in derived)
            return sorted((n for n in derived if n.period == period), key=lambda n: n.id)
    preferred_id = getattr(graph, "representative_node_ids", {}).get(code)
    if preferred_id:
        preferred = [n for n in reported if n.id == preferred_id]
        if preferred and select_representative_node(code, preferred, report_year=year):
            return preferred
    dart = [n for n in reported if n.origin == "dart"]
    selected = select_representative_node(code, dart or reported, report_year=year)
    return [selected] if selected else []


def _from_nodes(graph, code, nodes):
    if not nodes:
        return None
    first = nodes[0]
    values = [convert_to_common(n.value, normalize_unit(n.unit) or n.unit,
                               normalize_unit(first.unit) or first.unit) for n in nodes]
    if any(v is None for v in values):
        return None
    derived = all(_is_derived(n) for n in nodes)
    flags = []
    if derived:
        flags.append("derived")
    if any(n.period_inferred for n in nodes):
        flags.append("period_inferred")
    if not derived and is_partial_aggregate(first, code, report_year=graph.report_year):
        flags.append("partial_aggregate")
    return ResolvedFact(code, round(sum(values), 3) if derived else first.value,
                        first.unit, first.period, "derived" if derived else first.origin,
                        [n.id for n in nodes], min(n.confidence for n in nodes),
                        classify_value_role(code, first, report_year=graph.report_year), flags)


def resolve_fact(graph, code):
    facts = getattr(graph, "resolved_facts", {})
    if code in facts:
        return facts[code]
    return _from_nodes(graph, code, select_fact_nodes(graph, code))


def finalize_ledger(result, graph):
    """선택값이 최종 원장과 일치함을 확인한 뒤 결정 자체를 저장한다."""
    if not hasattr(graph, "resolved_facts"):
        graph.resolved_facts = {}
    codes = set(result.mapped) | {n.metric for n in graph.nodes.values()}
    for code in sorted(codes):
        entry = result.mapped.get(code)
        if not entry or entry.get("data_type") == "정성" or finite_number(entry.get("value")) is None:
            graph.resolved_facts[code] = None
            graph.representative_node_ids.pop(code, None)
            continue
        value, unit = entry["value"], entry.get("unit") or ""
        pool = graph.nodes_by_metric(code)
        tier = entry.get("source_tier", "")
        flags = list(result.confidence_flags.get(code, []))
        # 전력 Scope2 + 가스 Scope1의 정당한 파생값 합산도 원장에서 확정한다.
        derived = [n for n in pool if _is_derived(n)]
        if code == "E-3-1" and derived and len(derived) == len(pool) and tier == "ocr_node_gated":
            fact = _from_nodes(graph, code, select_fact_nodes(graph, code))
            if fact:
                value, unit, unit_flag = normalize_to_item_unit(code, fact.value, fact.unit)
                entry.update(value=value, unit=unit, source_tier="derived")
                flags = sorted(set(flags + fact.flags + ([unit_flag] if unit_flag else [])))
                chosen = [graph.nodes[nid] for nid in fact.representative_node_ids]
            else:
                chosen = []
        else:
            source_pool = [n for n in pool if n.origin != "dart"] if tier == "ocr_node_gated" else [n for n in pool if n.origin == "dart"]
            matching = []
            for node in source_pool:
                if classify_value_role(code, node, report_year=graph.report_year) == "target":
                    continue
                nv = convert_to_common(node.value, normalize_unit(node.unit) or node.unit,
                                       normalize_unit(unit) or unit)
                # normalize_to_item_unit는 물 질량/체적의 기존 복구 규칙도 공유한다.
                if nv is None:
                    nv, nu, uf = normalize_to_item_unit(code, node.value, node.unit)
                    if uf or nu != unit:
                        continue
                if finite_number(nv) is not None and isclose(nv, value, rel_tol=1e-8, abs_tol=1e-6):
                    matching.append(node)
            preferred = graph.representative_node_ids.get(code)
            chosen = [n for n in matching if n.id == preferred]
            if not chosen and matching:
                node = select_representative_node(code, matching, report_year=graph.report_year)
                # 구조화 공시의 0 실적은 원장 확정값이므로 대표가 될 수 있다.
                if node is None and value == 0:
                    node = min(matching, key=lambda n: n.id)
                chosen = [node] if node else []
        ids = [n.id for n in chosen]
        if not ids:
            flags.append("no_representative_node")
        if any(n.period_inferred for n in chosen):
            flags.append("period_inferred")
        fact = ResolvedFact(code, value, unit,
                            chosen[0].period if chosen else getattr(graph, "report_year", None),
                            entry.get("source_tier") or "dart", ids,
                            min((n.confidence for n in chosen), default=0.0),
                            entry.get("value_role") or (chosen[0].value_role if chosen else "unknown"),
                            sorted(set(flags)))
        graph.resolved_facts[code] = fact
        if len(ids) == 1:
            graph.representative_node_ids[code] = ids[0]
        else:
            graph.representative_node_ids.pop(code, None)
        entry.update(period=fact.period, representative_node_ids=ids,
                     resolved_fact=fact.to_dict())
        result.confidence_flags[code] = fact.flags


def comparison_node(graph, code):
    """D1용 읽기 전용 확정값 뷰. 원 노드를 변경하거나 후보를 다시 선택하지 않는다."""
    from types import SimpleNamespace
    fact = resolve_fact(graph, code)
    if fact is None:
        return None
    return SimpleNamespace(
        id=" + ".join(fact.representative_node_ids) or f"ledger:{code}",
        metric=code, value=fact.value, unit=fact.unit, period=fact.period,
        source_file=" / ".join(dict.fromkeys(graph.nodes[nid].source_file for nid in fact.representative_node_ids
                               if nid in graph.nodes and graph.nodes[nid].source_file)) or None,
        source=fact.source_tier,
        raw_text="", confidence=fact.confidence, value_role=fact.value_role,
        period_inferred="period_inferred" in fact.flags)
