"""확정 원장의 값과 출처를 출력·D1이 함께 소비하는 선택 계약.

resolved_facts에 코드가 없으면 구버전 입력, None이면 명시적 미확정이다.
후자는 후보 재선택으로 되살리지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict, replace
from math import isclose, isfinite
from typing import Any

from .boundary import (Boundary, DUPLICATE_MEASURE_REASON, SOURCE_CONFLICT_REASON, SumDecision,
                       covers_full_year, merge_boundaries, plan_sum)
from .node_select import classify_value_role, is_partial_aggregate, normalize_to_item_unit, select_representative_node
from ..rag_gates.units import normalize_unit, convert_to_common

# 실측 에너지원을 공용 단위로 합산해야 하는 코드(작업지시서 §2-2).
# 전기 고지서와 도시가스 고지서는 서로 다른 에너지원이라 각각 별개 문서로 올라온다.
# 대표 노드 하나만 고르면 '총 에너지 사용량' 자리에 전력만 앉는다. 이중계상 방어
# (단위 차원·계열 총량+구성요소·같은 측정 대상 중복)는 boundary.plan_sum이 담당한다.
ADDITIVE_ENERGY_CODES: frozenset[str] = frozenset({"E-4-1"})

# 전사·연간·전체 범위 총량을 묻는 코드 — 완결성이 입증돼야 verified가 된다.
# 부분값 하나가 총량 자리에 앉는 것을 막는다(작업지시서 §2-2 마지막 항, §3 마지막 항).
WHOLE_SCOPE_CODES: frozenset[str] = frozenset({"E-3-1", "E-3-2", "E-4-1", "E-4-2"})


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
    # ── 측정 경계(2026-09-20, 작업지시서 §2) ────────────────────────────
    # completeness: 이 값이 전사·연간·전체 에너지원 총량을 덮는가.
    #   합산으로는 절대 'total'이 되지 않는다(boundary.plan_sum 6단계).
    #   'unknown'은 구버전 입력이며 부분값과 구분한다 — 일괄 강등하지 않는다.
    completeness: str = "unknown"
    # boundary: 채택값의 경계 dict 표현. 출력이 기간·사업장·측정 대상을 표기한다.
    boundary: dict[str, Any] = field(default_factory=dict)
    # reference_node_ids: 합산에서 제외된 참고·보완 대상 근거. 산정 근거와 섞지 않는다
    #   — 쓰지 않은 가스 문서를 전력 단독값의 산정 근거로 붙이면 안 된다(§2-2 마지막 항).
    reference_node_ids: list[str] = field(default_factory=list)
    # scope_notes: 검토 사유(단위 차원 상이·목표값 제외·부분합 등) 사람 읽는 문장.
    scope_notes: list[str] = field(default_factory=list)

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


# 파생 배출량이 원측정값의 상충을 물려받았음을 알리는 검토 사유 표지.
# audit_trace가 이 표지로 파생 배출량의 비교 상태를 원측정값과 맞춘다.
SOURCE_CONFLICT_NOTE = "원측정값 상충"


def _derived_parent(graph, node):
    """파생 노드의 원측정값 노드. `source='derived_from:<node_id>'` 계약을 읽는다."""
    source = str(node.source)
    if not source.startswith("derived_from:"):
        return None
    return graph.nodes.get(source.split("derived_from:", 1)[1])


def _parent_comparisons(graph, ids):
    """원측정값 노드 사이에 **기록된** 비교 판정. {노드 id: {판정, …}}.

    합산 제외와 수치 모순은 다른 판단이다. plan_sum은 같은 측정 대상의 값이 정확히
    같지 않으면 합산을 막지만(오차 1%도, 범위가 달라 비교 불가한 값도), 그 사유
    문자열만으로 파생 배출량을 불일치로 올리면 교차검증이 `compared`·`not_comparable`인
    사례까지 mismatch가 된다(2026-09-21 3차 검토 R1-b). 그래서 그래프에 실제로
    기록된 판정만 본다 — 허용 오차와 범위 자격은 이미 그 판정이 담고 있다.

    비교 상대가 `ids` 밖이어도 그 판정은 `ids` 쪽 노드에 남긴다. 배출량 환산은 kWh·MJ
    에만 붙어 0.2 MWh 상충 문서에는 파생 노드가 없다 — 양 끝을 모두 요구하면 100 kWh
    ↔ 0.2 MWh의 실제 불일치가 조회에서 빠진다(5차 검토 1). 교차검증 엣지는 같은
    지표·기간끼리만 생기므로 다른 기간의 판정은 여기로 들어오지 않는다.
    """
    wanted = set(ids)
    states: dict[str, set[str]] = {}
    for edge in graph.edges:
        state = getattr(edge, "comparison", None)
        if not state or not {edge.source_id, edge.target_id} & wanted:
            continue
        for nid in {edge.source_id, edge.target_id} & wanted:
            states.setdefault(nid, set()).add(state)
    return states


def _same_quantity(node, others):
    """단위만 다른 같은 양인가 — 100 kWh와 0.1 MWh는 같은 측정값이다."""
    if node is None:
        return False
    for other in others:
        value = convert_to_common(node.value, normalize_unit(node.unit) or node.unit,
                                  normalize_unit(other.unit) or other.unit)
        if value is not None and isclose(value, other.value, rel_tol=1e-9, abs_tol=1e-12):
            return True
    return False


def _equivalent_basis(parent, target):
    """`parent`가 채택된 원측정값 `target`의 환산 근거로 쓸 수 있는 동등 문서인가.

    `DUPLICATE_MEASURE_REASON`으로 빠진 후보는 plan_sum이 같은 측정 대상·기간·사업장의
    정확히 같은 값임을 이미 판정한 것이다. 그래서 동등성 판정을 다시 만들지 않고, 어느
    채택값의 동등 문서인지 짝만 고른다 — 측정 대상 계열과 단위 환산 후 값으로 가른다.
    """
    if parent is None or target is None:
        return False
    if Boundary.from_dict(parent.boundary).measure_kind != Boundary.from_dict(target.boundary).measure_kind:
        return False
    return _same_quantity(parent, [target])


def plan_derived_emissions(graph, nodes, adopted=None):
    """파생 배출량 후보의 채택·제외 계획과 원측정값 상충 여부.

    파생 배출량은 환산 결과일 뿐이므로 **원측정값의 채택·중복 판정을 따라야** 한다.
    같은 사용전력량을 담은 별도 문서 두 건을 E-4-1에서는 중복으로 걸러 0.00036 TJ를
    유지하면서 E-3-1에서는 두 환산값을 더해 0.096 tCO2eq를 만들던 결함(재검토 R1)이
    여기서 갈린다.

    채택된 원측정값이 환산 대상 단위가 아닐 수 있다 — 배출량 환산은 kWh·MJ에만 붙는다.
    100 kWh·100 kWh·0.1 MWh 세 문서에서 대표가 MWh 문서로 뽑히면 환산 후보가 전부
    중복으로 빠진다. 그때 제외 목록을 통째로 되돌리면 0.096이 되살아난다(3차 검토
    R1-a). 동등한 중복 **하나**만 대표로 남기고, 그 노드는 제외 목록에서 뺀다 —
    산정에 쓴 근거와 제외 근거가 겹치면 설명 자체가 모순이다.

    복구는 **채택된 원측정값마다 따로** 판단한다. 전체 usable 목록이 비었을 때만
    복구하면 정상 도시가스 자료가 함께 있는 순간 전력 배출량 전체가 사라진다
    (4차 검토 A: 0.104 → 0.056). 다른 에너지원의 후보가 남아 있는지는 이 결정과
    무관하다.

    Args:
        adopted — 최종 채택된 파생 노드. 주면 conflict를 그 원측정값에 관련된 비교
            쌍으로 제한한다. 2025년 문서끼리의 불일치가 2026년 채택값의 상태를
            바꾸면 안 된다(4차 검토 B).

    Returns:
        usable   — 환산에 쓸 파생 노드
        excluded — [(노드, 사유)] 원측정값이 합산에서 빠진 후보. 파생 노드가 없는
            상충 상대는 그 원측정값 노드 자체가 들어간다.
        conflict — 채택된 원측정값에 관련된 실제 수치 불일치가 기록됐는가
    """
    parents = {n.id: _derived_parent(graph, n) for n in nodes}
    blocked: dict[str, str] = {}
    selected: list[Any] = []
    for metric in sorted({p.metric for p in parents.values() if p}):
        decision = plan_energy_selection(graph, metric)
        if not decision:
            continue
        blocked.update({node.id: why for node, why in decision.blocked})
        selected.extend(decision.summable)
    dropped = {n.id: blocked[parents[n.id].id] for n in nodes
               if parents.get(n.id) and parents[n.id].id in blocked}
    usable = [n for n in nodes if n.id not in dropped]
    covered = {parents[n.id].id for n in usable if parents.get(n.id)}
    # 동등 문서로 대신 환산한 경우 {환산 근거 파생 노드 id: 채택 원측정값}.
    # 상충 전파는 환산 근거뿐 아니라 채택 원측정값의 비교 판정도 따라야 한다.
    substituted: dict[str, Any] = {}
    for target in sorted(selected, key=lambda n: n.id):
        if target.id in covered:
            continue                      # 이 원측정값의 환산 근거는 이미 남아 있다.
        equivalent = next((n for n in sorted(nodes, key=lambda n: n.id)
                           if dropped.get(n.id) == DUPLICATE_MEASURE_REASON
                           and _equivalent_basis(parents.get(n.id), target)), None)
        if equivalent is not None:
            usable.append(equivalent)
            covered.add(target.id)
            substituted[equivalent.id] = target
    usable = sorted(usable, key=lambda n: n.id)
    kept = {n.id for n in usable}
    states = _parent_comparisons(graph, [p.id for p in parents.values() if p]
                                 + [t.id for t in substituted.values()])
    excluded = []
    for node in nodes:
        why = dropped.get(node.id)
        if why is None or node.id in kept:
            continue
        parent = parents[node.id]
        label = parent.source_file or parent.id
        recorded = states.get(parent.id, set())
        note = f"{label} 원측정값 {why}"
        if "mismatch" in recorded:
            note = f"{label} {SOURCE_CONFLICT_NOTE} — {why}"
        elif why == SOURCE_CONFLICT_REASON:
            # 합산은 막았지만 수치 모순은 아니다 — 기록된 판정을 함께 적는다.
            note += f" (교차검증 {'/'.join(sorted(recorded)) or '기록 없음'})"
        excluded.append((node, note))
    # 상충 전파 범위 — 채택값을 모르면 환산에 쓸 후보 전체가 기준이다.
    anchors = [p for n in (usable if adopted is None else adopted)
               for p in (parents.get(n.id), substituted.get(n.id)) if p]
    # 파생 노드가 없는 상충 상대(0.2 MWh 문서)는 위 순회에 들어오지 않는다. 상태만
    # mismatch로 올리고 상대 문서와 사유를 잃으면 응답서·체크리스트가 범위 사유만
    # 보여 준다(6차 검토 1). 그 원측정값을 상충 근거로 직접 남긴다.
    anchor_ids = {p.id for p in anchors}
    known = {p.id for p in parents.values() if p} | anchor_ids
    for edge in graph.edges:
        if getattr(edge, "comparison", None) != "mismatch":
            continue
        for mine, other_id in ((edge.source_id, edge.target_id), (edge.target_id, edge.source_id)):
            other = graph.nodes.get(other_id)
            if mine not in anchor_ids or other is None or other_id in known:
                continue
            known.add(other_id)
            why = blocked.get(other_id) or "교차검증 불일치"
            excluded.append((other, f"{other.source_file or other.id} {SOURCE_CONFLICT_NOTE} — {why}"))
    return usable, excluded, any("mismatch" in states.get(p.id, set()) for p in anchors)


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
            # 원측정값이 중복·상충으로 제외됐으면 그 환산값도 더하지 않는다. 남는
            # 후보가 없으면 그대로 비워 둔다 — 제외한 중복을 되돌리지 않는다(R1-a).
            derived = plan_derived_emissions(graph, derived)[0]
        if derived:
            period = min({n.period for n in derived}, key=lambda p: (abs(p-year), -p)) if year else max(n.period for n in derived)
            from .boundary import same_period, compatible_sites
            candidates = sorted((n for n in derived if n.period == period), key=lambda n: n.id)
            chosen, seen = [], set()
            for n in candidates:
                b = Boundary.from_dict(n.boundary)
                identity = (n.document_id or n.source, b.measure_kind, n.value, b.period_start, b.period_end)
                if identity in seen:
                    continue
                if chosen and (same_period(chosen[0].boundary, b)[0] != "compared"
                               or not compatible_sites(Boundary.from_dict(chosen[0].boundary), b, inclusion=True)):
                    continue
                seen.add(identity)
                chosen.append(n)
            return chosen
    preferred_id = getattr(graph, "representative_node_ids", {}).get(code)
    if preferred_id:
        preferred = [n for n in reported if n.id == preferred_id]
        if preferred and select_representative_node(code, preferred, report_year=year):
            return preferred
    dart = [n for n in reported if n.origin == "dart"]
    selected = select_representative_node(code, dart or reported, report_year=year)
    return [selected] if selected else []


def plan_energy_selection(graph, code, nodes=None) -> SumDecision | None:
    """실측 에너지원 제한적 합산 계획. 합산 대상이 2개 이상일 때만 결정을 돌려준다.

    ssot_pipeline(값 채움)과 finalize_ledger(원장 확정)가 **같은 함수를 같은 풀로**
    호출한다. 각자 따로 판단하면 합산 여부가 갈려 원장 불일치가 난다.
    이중계상 방어 3종은 boundary.plan_sum이 담당한다 — 같은 고지서 중복 업로드,
    계열 총량 + 그 구성요소, 도시가스 m³ + MJ 같은 단위만 다른 같은 물리량.
    """
    if code not in ADDITIVE_ENERGY_CODES:
        return None
    from ..survey import is_survey
    pool = [n for n in (nodes if nodes is not None else graph.nodes_by_metric(code))
            if finite_number(n.value) is not None and not _is_derived(n) and not is_survey(n)]
    if len(pool) < 2:
        return None
    decision = plan_sum(sorted(pool, key=lambda n: n.id), report_year=graph.report_year,
                        boundary_of=lambda n: n.boundary,
                        unit_of=lambda n: normalize_unit(n.unit) or n.unit)
    return decision


def plan_energy_sum(graph, code, nodes=None) -> SumDecision | None:
    decision = plan_energy_selection(graph, code, nodes)
    return decision if decision and decision.ok else None


def energy_sum_value(code, nodes):
    """합산값을 항목 정의 단위로 환산해 (값, 단위, 단위플래그)를 돌려준다.

    원장값은 표시 목적으로 반올림하지 않는다 — 0.513216 TJ가 0.5로 뭉개지면 안 된다.
    round(…, 9)는 부동소수 잡음만 지운다(0.8739879999… → 0.873988).
    """
    if not nodes:
        return None
    base = normalize_unit(nodes[0].unit) or nodes[0].unit
    values = [convert_to_common(n.value, normalize_unit(n.unit) or n.unit, base)
              for n in nodes]
    if any(v is None for v in values):
        return None
    value, unit, unit_flag = normalize_to_item_unit(code, sum(values), base)
    number = finite_number(value)
    if number is None:
        return None
    return round(number, 9), unit, unit_flag


def apply_energy_sum(result, graph) -> None:
    """총 에너지 항목의 값을 제한적 합산으로 채운다(작업지시서 §2-2).

    대표 노드 하나를 고르는 기존 경로 **뒤에** 붙는다. 합산이 성립하지 않으면
    (단일 문서, 목표·계획값, 단위 차원 상이, 계열 총량 + 구성요소) 기존 선택이
    그대로 남는다. 합산이 성립해도 완결성은 별도 판정이다 — 부분합은 부분값이다.
    """
    for code in sorted(ADDITIVE_ENERGY_CODES):
        entry = result.mapped.get(code)
        if not entry or entry.get("data_type") == "정성":
            continue
        if finite_number(entry.get("value")) is None:
            continue
        if entry.get("source_tier") != "ocr_node_gated":
            continue  # 구조화 공시의 확정 총량은 OCR 부분합으로 대체하지 않는다.
        decision = plan_energy_selection(graph, code)
        if decision is None:
            continue
        if not decision.ok:
            # 포함 관계 제거로 남은 확실한 총량만 단독 대표를 대체할 수 있다.
            # 미상 경계의 단독 후보가 기존 248.5 총량을 61.2 부분값으로 바꾸면 안 된다.
            if not decision.summable:
                continue
            candidate = decision.summable[0]
            cb = Boundary.from_dict(candidate.boundary)
            if not (cb.period_start and cb.period_end and cb.site_scope != "unknown"
                    and cb.measure_kind == "energy_total"
                    and classify_value_role(code, candidate, report_year=graph.report_year) == "total"):
                continue
        summed = energy_sum_value(code, list(decision.summable))
        if summed is None:
            continue
        value, unit, unit_flag = summed
        old_value, old_unit = entry.get("value"), entry.get("unit") or ""
        entry["value"] = value
        entry["unit"] = unit or old_unit
        entry["energy_selection_ids"] = [n.id for n in decision.summable]
        sources = [n.source_file or n.source for n in decision.summable]
        entry["note"] = (
            f"실측 에너지원 제한적 합산 — {', '.join(dict.fromkeys(sources))} "
            f"(단독 대표값 {old_value}{old_unit} → 합산 {value}{unit or old_unit})")
        flags = result.confidence_flags.get(code, [])
        for flag in (*(("partial_value",) if decision.ok else ()), *(("unit_suspect",) if unit_flag else ())):
            if flag not in flags:
                flags = flags + [flag]
        result.confidence_flags[code] = flags
        # 합산은 대표 노드가 여럿이다 — 단일 대표 슬롯은 비운다(E-3-1 파생 합산과 같은 규약).
        graph.representative_node_ids.pop(code, None)
        result.notes.append(
            f"[제한적 합산] {code}: {len(decision.summable)}개 에너지원 합산 = "
            f"{value}{unit or old_unit} — 전체 에너지원을 덮었다는 근거는 없으므로 부분값")
        for reason in decision.reasons:
            result.notes.append(f"[합산 판정] {code}: {reason}")
        for node, why in (*decision.blocked, *decision.reference):
            result.notes.append(
                f"[합산 제외] {code}: {node.source_file or node.id} — {why}")


def resolve_completeness(boundary, *, summed: bool = False, code: str = "") -> str:
    """원장 완결성 — 경계의 총량/부분 판정에 '대상 기간이 1년을 덮는가'를 더한다.

    K-ESG 정량 항목은 연간값을 묻는다. 연간 총량·연간 비율 자리에 상반기 값 하나가
    앉으면 숫자는 맞아도 전체성이 입증되지 않는다(작업지시서 §2-2 마지막 항, §3 마지막 항).
    합산 결과는 구성요소를 몇 개 더했는지와 무관하게 절대 total이 되지 않는다.
    근거가 없으면 'unknown'으로 남긴다 — 구버전 입력을 부분값으로 일괄 강등하지 않는다.
    """
    b = Boundary.from_dict(boundary)
    if summed or b.completeness == "partial":
        return "partial"
    full = covers_full_year(b)
    if full is False:
        return "partial"
    if code in {"E-4-1", "E-4-2"} and scope_gaps(code, b):
        return "unknown"
    if b.completeness == "total":
        return "total" if full is True else "unknown"
    return b.completeness


def scope_gaps(code, boundary):
    b = Boundary.from_dict(boundary)
    gaps = []
    if not b.period_start or not b.period_end or covers_full_year(b) is not True:
        gaps.append("연간 실적의 실제 사용기간·집계 방식 확인")
    if b.site_scope != "entity":
        gaps.append("전사/공장 조직·사업장 범위 확인")
    if b.basis != "actual":
        gaps.append("실적/계획 여부 확인")
    if code == "E-4-1" and b.measure_kind != "energy_total":
        gaps.append("전체 에너지원 포함 여부 확인")
    if code == "E-4-2":
        if b.measure_kind != "renewable_total" or b.completeness != "total":
            gaps.append("전체 재생에너지 분자·조달수단 포함 관계 확인")
        if b.denominator_kind != "total_energy":
            gaps.append("총에너지 분모 확인" + (" (증빙은 총전력 분모)" if b.denominator_kind == "total_electricity" else " (분모 미상)"))
    gaps.extend(b.review_notes)
    return gaps


def _scope_note(code, boundary, completeness) -> str:
    """부분값·미확정 경계의 검토 사유 — 배지만으로는 알 수 없는 기간·사업장을 적는다."""
    b = Boundary.from_dict(boundary)
    label = b.label()
    detail = "; ".join(n for n in scope_gaps(code, b) if n not in b.review_notes)
    if completeness == "partial":
        return (f"{code}: 부분값 — {label or '경계 미기록'}. "
                f"전사·연간·전체 범위 총량임이 입증되지 않아 검증 보류. {detail}")
    return f"{code}: 경계 확인 필요 — {label or '경계 미기록'}; {detail}"


def _scope_source_only(boundary) -> bool:
    """근거 0이 원문 사실로만 보존됐는가(`ocr_router._ZeroVerdict` SOURCE_ONLY).

    '범위를 검사하지 않음'과 '검사해서 일치함'을 가르는 기록이다 — 이 표지가 있으면 요청 범위의
    실적으로 확인되지 않았다는 뜻이므로 원장 플래그 `scope_source_only`로 남긴다.
    """
    return any(p.get("source") == "zero_evidence" and p.get("status") == "SOURCE_ONLY"
               for p in (boundary.provenance or ()) if isinstance(p, dict))


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
    boundary = (merge_boundaries([n.boundary for n in nodes]) if len(nodes) > 1
                else Boundary.from_dict(first.boundary))
    completeness = resolve_completeness(boundary, summed=len(nodes) > 1, code=code)
    # 원장에 싣는 경계는 판정된 완전성을 그대로 반영한다 — label()이 '총량'이라고
    # 찍으면서 fact.completeness가 'partial'인 자기모순 출력을 막는다.
    boundary = replace(boundary, completeness=completeness)
    notes = list(boundary.review_notes) if _scope_source_only(boundary) else []
    if notes:
        flags.append("scope_source_only")
    if code in WHOLE_SCOPE_CODES and completeness != "total":
        flags.append("incomplete_scope")
        notes.append(_scope_note(code, boundary, completeness))
    return ResolvedFact(code, round(sum(values), 3) if derived else first.value,
                        first.unit, first.period, "derived" if derived else first.origin,
                        [n.id for n in nodes], min(n.confidence for n in nodes),
                        classify_value_role(code, first, report_year=graph.report_year),
                        flags, completeness=completeness,
                        boundary=boundary.to_dict(), scope_notes=notes)


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
        reference: list[Any] = []
        sum_notes: list[str] = []
        # 제한적 합산이 이 값을 만들었는지 먼저 확인한다(§2-2). 합산값은 어떤 단일
        # 노드와도 일치하지 않으므로 아래 일치 검사로는 대표 노드를 찾지 못하고
        # 'no_representative_node'로 떨어진다.
        decision = plan_energy_selection(graph, code)
        summed = energy_sum_value(code, list(decision.summable)) if decision else None
        # 전력 Scope2 + 가스 Scope1의 정당한 파생값 합산도 원장에서 확정한다.
        derived = [n for n in pool if _is_derived(n)]
        if (summed and decision and entry.get("energy_selection_ids") == [n.id for n in decision.summable]
                and isclose(summed[0], value, rel_tol=1e-9, abs_tol=1e-9)
                and (summed[1] or unit) == unit):
            chosen = list(decision.summable)
            reference = [n for n, _ in (*decision.blocked, *decision.reference)]
            sum_notes = [f"{code}: {r}" for r in decision.reasons]
            sum_notes += [f"{code}: 합산 제외 — {n.source_file or n.id} — {why}"
                          for n, why in (*decision.blocked, *decision.reference)]
        elif code == "E-3-1" and derived and len(derived) == len(pool) and tier == "ocr_node_gated":
            # 원측정값 쪽 제외 사유와 실제 상충 판정을 파생 배출량에 남긴다. 환산할
            # 후보가 남지 않아도 사유는 적는다 — 근거 없이 값만 남기지 않는다.
            # 채택값을 먼저 확정한다 — 상충 전파는 그 원측정값에 관련된 비교 쌍에만
            # 적용해야 한다(4차 검토 B).
            fact = _from_nodes(graph, code, select_fact_nodes(graph, code))
            adopted = [graph.nodes[nid] for nid in (fact.representative_node_ids if fact else [])
                       if nid in graph.nodes]
            _, excluded, conflict = plan_derived_emissions(graph, derived, adopted=adopted)
            reference = [n for n, _ in excluded]
            sum_notes = [f"{code}: 파생 배출량 제외 — {why}" for _, why in excluded]
            if conflict:
                flags.append("source_conflict")
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
        if decision and not sum_notes:
            reference = [n for n, _ in (*decision.blocked, *decision.reference)]
            sum_notes = [f"{code}: {r}" for r in decision.reasons]
        ids = [n.id for n in chosen]
        if not ids:
            flags.append("no_representative_node")
        if any(n.period_inferred for n in chosen):
            flags.append("period_inferred")
        if len(chosen) > 1:
            boundary = merge_boundaries([n.boundary for n in chosen], completeness="partial")
        elif chosen:
            boundary = Boundary.from_dict(chosen[0].boundary)
        else:
            boundary = Boundary()
        completeness = resolve_completeness(boundary, summed=len(chosen) > 1, code=code)
        boundary = replace(boundary, completeness=completeness)
        scope_notes = list(sum_notes)
        scope_notes.extend(boundary.review_notes)
        if _scope_source_only(boundary):
            flags.append("scope_source_only")
        if code in WHOLE_SCOPE_CODES and completeness != "total":
            flags.append("incomplete_scope")
            scope_notes.append(_scope_note(code, boundary, completeness))
        fact = ResolvedFact(code, value, unit,
                            chosen[0].period if chosen else getattr(graph, "report_year", None),
                            entry.get("source_tier") or "dart", ids,
                            min((n.confidence for n in chosen), default=0.0),
                            entry.get("value_role") or (chosen[0].value_role if chosen else "unknown"),
                            sorted(set(flags)), completeness=completeness,
                            boundary=boundary.to_dict(),
                            reference_node_ids=[n.id for n in reference],
                            scope_notes=scope_notes)
        graph.resolved_facts[code] = fact
        if len(ids) == 1:
            graph.representative_node_ids[code] = ids[0]
        else:
            graph.representative_node_ids.pop(code, None)
        entry.update(period=fact.period, representative_node_ids=ids,
                     completeness=fact.completeness,
                     reference_node_ids=fact.reference_node_ids,
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
        period_inferred="period_inferred" in fact.flags,
        # D1이 '같은 것끼리' 비교하려면 원장 뷰도 경계를 들고 있어야 한다(§4).
        boundary=Boundary.from_dict(fact.boundary), completeness=fact.completeness)
