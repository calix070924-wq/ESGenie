"""L0 — 통합 Evidence Graph (DART + 내부 증빙 OCR).

기존 v10 EvidenceGraph(DART 정형 수치 전용)를 확장한다.
핵심 변경: **단일 진실 원천(SSOT)** 에 두 출처를 함께 묶는다.

  DART JSON ─┐
             ├─► EvidenceGraph (nodes + edges)  ──► L1/L2/L3 …
  OCR 증빙 ──┘
   (ocr_router.OcrExtraction)

설계 원칙
  - EvidenceNode.origin 으로 출처를 구분(dart | ocr_structured | ocr_unstructured).
  - 모든 노드는 source_file(원본 증빙 파일명)을 보존 → L5 증빙 서류철 하드링크 키.
  - DART와 OCR이 같은 metric/period를 가지면 cross-check 엣지로 연결(D1 교차검증 재료).
  - 정성 조항(ExtractedClause)은 TextNode로 별도 보관 → 사내규정 검증(detector)에서 사용.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from typing import Any, Literal

from .ocr_router import OcrExtraction, ExtractedMetric, ExtractedClause, DocChannel

Origin = Literal["dart", "ocr_structured", "ocr_unstructured"]
ValueRole = Literal["total", "component", "target", "unknown"]


# ====================================================================
# 노드 / 엣지 스키마 (v10 호환 + 확장 필드)
# ====================================================================

@dataclass
class EvidenceNode:
    id: str            # "{corp}_{metric}_{period}__{origin}"
    metric: str        # K-ESG 코드 (예: "E-4-1")
    value: float
    unit: str
    period: int        # 보고 연도
    source: str        # 데이터 출처 경로 (예: "kesg_data/E-4-1", "ocr/kepco_bill")
    raw_text: str = ""
    origin: Origin = "dart"          # ★ 신규: 출처 구분
    source_file: str | None = None   # ★ 신규: 원본 증빙 파일명 (감사 하드링크)
    bbox: list[float] | None = None  # ★ 신규: 원문 내 위치(0~1 정규화)
    page: int | None = None          # ★ 신규: 0-기준 페이지 인덱스
    confidence: float = 1.0          # ★ 신규: OCR/추출 신뢰도 (DART=1.0)
    # 원문에서 연도를 못 읽어 report_year로 채운 값인가(_normalize_period 폴백).
    # 기본값 False라 기존 노드·DART 경로는 동작이 바뀌지 않는다.
    #
    # 왜 필요한가(2026-07-29): period == report_year가 '진짜 report_year 실적'과
    # '연도 미상'을 구분하지 못했다. 실측 478노드 중 폴백은 15건(3.1%)뿐이지만,
    # 그 15건은 사용자에게 2025 실적으로 보인다(근거: docs/연도미상_원인조사_2026-07-29.md).
    # 소비 지점 3곳 — G4 projection 판정(아래) · node_select 연도 축 · confidence_flags.
    period_inferred: bool = False
    # 코드 배정(근거 보존)과 대표값 자격을 분리한다. unknown은 구버전 노드 호환 기본값.
    value_role: ValueRole = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TextNode:
    """정성 조항 노드 (회의록·규정집 등 서술형)."""
    id: str            # "{corp}_TXT_{idx:04d}"
    section: str
    text: str
    kesg_code: str | None
    source_file: str
    page: int | None = None
    origin: Origin = "ocr_unstructured"
    rba_code: str | None = None    # RBA 자가진단 substrate 매칭(고유 조항용)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceEdge:
    source_id: str
    target_id: str
    edge_type: str        # "timeseries" | "cross_check"
    yoy: float | None = None
    cagr: float | None = None
    years_gap: int = 1
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ====================================================================
# EvidenceGraph
# ====================================================================

class EvidenceGraph:
    """사실 노드 + 정성 노드 + 엣지 통합 그래프 (SSOT)."""

    def __init__(self, corp_code: str, corp_name: str) -> None:
        self.corp_code = corp_code
        self.corp_name = corp_name
        # 보고 대상 연도 — G5(D1 노드 선택)가 '보고연도 최근접'을 판정할 기준.
        # build_unified_graph/merge_ocr_extraction가 report_year로 세팅한다. None이면
        # 검출기가 후보 노드의 최신 연도로 폴백(기존 동작 보존).
        self.report_year: int | None = None
        # 코드 → 원장(L1)이 대표로 채택한 노드 id. _merge_ssot_evidence가 쓰고
        # D1(layer3_detect._score_d1_numeric)이 읽는다. report_year와 같은
        # '레이어 간 합의' 슬롯이다.
        #
        # 왜 필요한가(2026-07-26): 두 경로가 node_select.select_representative_node를
        # 공유해도 **넘기는 후보 풀이 다르다**. 원장은 origin이 ocr_*인 노드만(DART 값은
        # 이미 report.kesg_data → mapped 경로로 들어와 이중 처리를 피한다), D1은
        # search_nodes()로 DART 노드까지 포함한 풀을 넘긴다. 같은 규칙도 풀이 다르면
        # 다른 노드를 가리킨다(실측: 원장 623,648 vs D1 1,992,921). 규칙을 두 번
        # 돌리는 대신 원장의 결정을 여기 기록해 D1이 따르게 한다.
        # 대표 노드가 없는(미공시) 코드는 기록하지 않는다.
        self.representative_node_ids: dict[str, str] = {}
        self._nodes: dict[str, EvidenceNode] = {}
        self._text_nodes: dict[str, TextNode] = {}
        self._edges: list[EvidenceEdge] = []
        self._text_seq = 0

    # ---- 변경 API ----------------------------------------------------
    def add_node(self, node: EvidenceNode) -> None:
        self._nodes[node.id] = node

    def add_text_node(self, node: TextNode) -> None:
        self._text_nodes[node.id] = node

    def add_edge(self, edge: EvidenceEdge) -> None:
        self._edges.append(edge)

    # ---- 조회 API ----------------------------------------------------
    @property
    def nodes(self) -> dict[str, EvidenceNode]:
        return self._nodes

    @property
    def text_nodes(self) -> dict[str, TextNode]:
        return self._text_nodes

    @property
    def edges(self) -> list[EvidenceEdge]:
        return self._edges

    def nodes_by_metric(self, metric: str) -> list[EvidenceNode]:
        return sorted(
            (n for n in self._nodes.values() if n.metric == metric),
            key=lambda n: n.period,
        )

    def text_nodes_by_code(self, code: str) -> list[TextNode]:
        return [t for t in self._text_nodes.values() if t.kesg_code == code]

    def text_nodes_by_rba_code(self, code: str) -> list[TextNode]:
        return [t for t in self._text_nodes.values() if t.rba_code == code]

    def search_nodes(
        self,
        keywords: list[str],
        period: int | None = None,
    ) -> list[EvidenceNode]:
        """K-ESG 코드/키워드로 노드 검색 (v10 layer1 호환 API).

        매칭 우선순위:
          1) node.metric이 keywords 중 하나와 정확히 일치 (K-ESG 코드 직접 매칭)
          2) node.metric에 keyword가 부분 포함
        period가 주어지면 해당 연도 노드만 반환.

        G4 주의: '{code}__projection'(미래 전망 분리 노드)은 실적 코드 검색에서 제외한다.
        부분 포함 매칭이 'E-3-1'로 'E-3-1__projection'을 잡으면 D1이 전망치를 실적과
        비교하게 되므로, keyword가 명시적으로 projection을 요구하지 않는 한 걸러낸다.
        """
        result: list[EvidenceNode] = []
        for node in self._nodes.values():
            is_projection = node.metric.endswith("__projection")
            matched = False
            for kw in keywords:
                if kw == node.metric:
                    matched = True
                    break
                # 부분 포함 매칭: projection 노드는 keyword가 projection을 명시할 때만.
                if kw.lower() in node.metric.lower():
                    if is_projection and "projection" not in kw.lower():
                        continue
                    matched = True
                    break
            if not matched:
                continue
            if period is not None and node.period != period:
                continue
            result.append(node)
        return sorted(result, key=lambda n: n.period)

    def to_dict(self) -> dict[str, Any]:
        return {
            "corp_code": self.corp_code,
            "corp_name": self.corp_name,
            "nodes": [n.to_dict() for n in self._nodes.values()],
            "text_nodes": [t.to_dict() for t in self._text_nodes.values()],
            "edges": [e.to_dict() for e in self._edges],
        }

    # ---- 내부 시퀀스 -------------------------------------------------
    def _next_text_id(self) -> str:
        self._text_seq += 1
        return f"{self.corp_code}_TXT_{self._text_seq:04d}"


# ====================================================================
# 빌더 1 — DART (기존 v10 로직 위임; 여기선 인터페이스만)
# ====================================================================

def build_from_dart(report: Any) -> EvidenceGraph:
    """DART CompanyReport → EvidenceGraph (v15 SSOT).

    v10의 build_evidence_graph()를 호출한 뒤, 생성된 노드/엣지를
    v15 스키마(origin='dart', confidence=1.0)로 변환해 반환한다.
    """
    from esgenie.layer0_evidence_graph import (
        build_evidence_graph as _v10_build,
    )

    v10_graph = _v10_build(report)
    graph = EvidenceGraph(corp_code=v10_graph.corp_code, corp_name=v10_graph.corp_name)

    # v10 EvidenceNode → v15 EvidenceNode (origin/confidence 필드 추가)
    for v10_node in v10_graph.nodes.values():
        node = EvidenceNode(
            id=v10_node.id,
            metric=v10_node.metric,
            value=v10_node.value,
            unit=v10_node.unit,
            period=v10_node.period,
            source=v10_node.source,
            raw_text=v10_node.raw_text,
            origin="dart",
            source_file=None,   # DART는 파일 증빙 없음
            bbox=None,
            confidence=1.0,     # DART 공식 공시 = 신뢰도 최대
        )
        graph.add_node(node)

    # v10 EvidenceEdge → v15 EvidenceEdge (detail 필드 추가)
    for v10_edge in v10_graph.edges:
        edge = EvidenceEdge(
            source_id=v10_edge.source_id,
            target_id=v10_edge.target_id,
            edge_type=v10_edge.edge_type,
            yoy=v10_edge.yoy,
            cagr=v10_edge.cagr,
            years_gap=v10_edge.years_gap,
            detail=f"dart timeseries yoy={v10_edge.yoy}%",
        )
        graph.add_edge(edge)

    return graph


# ====================================================================
# 빌더 2 — OCR 증빙 → 노드 편입  ★ 신규 핵심
# ====================================================================

# 단위 환산 → 탄소/에너지 표준화 (예시 계수, 실제는 환경부/한전 배출계수 사용)
_EMISSION_FACTORS = {
    "kWh_to_tco2": 0.4781 / 1000,   # 전력 tCO2eq/kWh (2025 국가 전력배출계수 예시)
    "MJ_gas_to_tco2": 0.0000561,    # 도시가스 tCO2eq/MJ (예시)
}


def merge_ocr_extraction(
    graph: EvidenceGraph,
    extraction: OcrExtraction,
    *,
    report_year: int,
    industry_module=None,
) -> EvidenceGraph:
    """OCR 추출 결과를 기존 그래프에 편입(SSOT 통합).

    1) 정량 metric → EvidenceNode 추가 (origin=ocr_*).
    2) 동일 metric/period의 DART 노드가 있으면 cross_check 엣지 생성.
    3) 정성 clause → TextNode 추가.
    4) 탄소 배출량 파생 노드 자동 산출(전력·가스 → tCO2eq).
    """
    origin: Origin = (
        "ocr_structured" if extraction.channel is DocChannel.STRUCTURED else "ocr_unstructured"
    )
    # G5 참조 기준 — 그래프에 보고 연도 기록(검출기가 최근접 노드 선택에 사용).
    if getattr(graph, "report_year", None) is None:
        graph.report_year = report_year

    for idx, m in enumerate(extraction.metrics):
        code = _resolve_kesg_code(m)
        period, period_inferred = _normalize_period(
            m.period, fallback=report_year, hint=m.metric_hint)
        confidence = m.confidence
        # G4. 미래 기간 분리 — 보고 연도보다 '충분히' 미래(2030/2035/2040 목표·전망 등)인
        # 확정 코드 노드는 실적 코드에서 떼어내 '{code}__projection'으로 보존. search_nodes
        # (실적 코드)로는 안 잡혀 D1 비교 대상에서 제외되고, 값은 향후 목표 대비 실적 분석
        # 재료로 남는다.
        #
        # 임계값 _PROJECTION_YEAR_GAP(=2): report_year+1 은 실적으로 인정한다. 증빙(전기요금
        # 명세 등)은 DART 공시연도보다 1년 앞설 수 있어(예: 2024 보고서 + 2025-12 고지서),
        # +1까지 미래로 보면 정상 최신 증빙까지 projection으로 오분류된다. 목표·전망 곡선의
        # 축 연도(2030+)는 +2 이상이라 이 임계값으로 정확히 걸러진다.
        #
        # period_inferred면 판정하지 않는다(2026-07-29): 폴백 연도는 report_year와 같아
        # 임계값을 넘지 않으므로 지금도 projection이 되지 않는다. 조건을 명시해 두는 이유는
        # 폴백 기준값이 report_year가 아니게 바뀌어도(예: 문서 연도) 추론값으로 '미래 전망'을
        # 판정하지 않도록 못박기 위한 것이다 — 근거 없는 분리는 D1 비교 대상을 잘못 줄인다.
        metric = code or m.metric_hint
        if code and not period_inferred and period - report_year >= _PROJECTION_YEAR_GAP:
            metric = f"{code}__projection"
            confidence = round(confidence * 0.3, 4)
        node = EvidenceNode(
            id=_make_ocr_node_id(
                graph.corp_code,
                metric,
                period,
                origin,
                extraction.source_file,
                m.metric_hint,
                idx,
            ),
            metric=metric,
            value=m.value,
            unit=m.unit,
            period=period,
            source=f"ocr/{extraction.doc_type}",
            raw_text=f"{m.metric_hint}={m.value}{m.unit} ({extraction.source_file})",
            origin=origin,
            source_file=extraction.source_file,
            bbox=m.bbox,
            page=m.page,
            confidence=confidence,
            period_inferred=period_inferred,
        )
        # 역할은 노드에 영속화하되 선택 시에도 재계산한다. 구버전 덤프를 리플레이해도
        # 같은 규칙을 적용하고, 신규 산출물은 역할을 감사할 수 있게 하기 위함이다.
        from .node_select import classify_value_role

        node.value_role = classify_value_role(
            code or metric, m.metric_hint, report_year=report_year)
        graph.add_node(node)
        _link_cross_check(graph, node)
        _emit_derived_emission(
            graph,
            node,
            industry_module=industry_module,
            source_file=extraction.source_file,
            seq=idx,
        )

    for c in extraction.clauses:
        tnode = TextNode(
            id=graph._next_text_id(),
            section=c.section,
            text=c.text,
            kesg_code=c.kesg_code_guess,
            source_file=extraction.source_file,
            page=c.page,
            origin=origin,
            rba_code=getattr(c, "rba_code_guess", None),
        )
        graph.add_text_node(tnode)

    return graph


def build_unified_graph(
    dart_report: Any | None,
    extractions: list[OcrExtraction],
    *,
    corp_code: str,
    corp_name: str,
    report_year: int,
    industry_module=None,
) -> EvidenceGraph:
    """최상위 진입점 — DART + 모든 OCR 증빙을 하나의 SSOT로 통합.

    app.py 가 호출하는 핵심 함수.
    """
    if dart_report is not None:
        graph = build_from_dart(dart_report)
    else:
        graph = EvidenceGraph(corp_code, corp_name)
    graph.report_year = report_year   # G5 기준 연도(OCR 없는 DART-only 경로도 보장)

    for ext in extractions:
        merge_ocr_extraction(
            graph, ext, report_year=report_year, industry_module=industry_module)
    return graph


# ====================================================================
# 내부 헬퍼
# ====================================================================

# 총량/대표 코드로 잡으면 안 되는 하위·보조 수치(상위코드 부분문자열에 걸리는 것).
# 예: '지정폐기물'은 '폐기물'(E-6-1)에 걸리지만 총량이 아니라 하위 분류다.
_HINT_EXCLUDE: tuple[str, ...] = ("지정폐기물",)

# 배정 단계의 코드별 충돌 어휘. node_select에만 두면 잘못 배정된 노드가 후보 풀을 먼저
# 오염시킨다. 이번 결함의 실측 범위(E-3-1 ← Scope 3)만 최소 적용한다.
_ASSIGNMENT_NEGATIVE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "E-3-1": ("scope3", "1+2+3", "가치사슬"),
}

# G1 가드 어휘 — "실적 총량이 아닌 값"을 알리는 수식어. hint에 포함되면 코드 미부여.
#   · 미래·의도: 목표/전망/계획/예정/로드맵/선언
#   · 부분·파생(총량 자리에 오면 안 됨): 감축량/전환량/절감량/누적
#   · 정규화 지표(총량과 단위 다름): 원단위/집약도/intensity
# 주의: '재활용률'·'재생에너지'처럼 정당한 비율/항목은 넣지 않는다(과차단 방지).
#       가드는 "같은 지표의 다른 성격 값"만 겨냥한다.
_GUARD_TERMS: tuple[str, ...] = (
    "목표", "전망", "계획", "예정", "로드맵", "선언",
    "감축량", "전환량", "절감량", "누적",
    "원단위", "집약도", "intensity",
)

# G4. 미래 기간 분리 임계값 — period가 report_year보다 이 값 이상 앞서면 projection.
# 2로 둔다: report_year+1(최신 증빙)은 실적, +2 이상(2030/2035/2040 목표축)은 전망.
_PROJECTION_YEAR_GAP: int = 2


def _resolve_kesg_code(
    m: ExtractedMetric, *, allow_fuzzy: bool = False
) -> str | None:
    """단일 alias 사전 + LLM 보조 추정으로 K-ESG 코드 확정.

    게이트 순서(하나라도 걸리면 코드 None → 노드는 metric_hint로 보존, 폐기 아님):
      0. ``kesg_items.resolve_kesg_code``로 후보 탐색(exact 우선, fuzzy 기본 거부).
      G1. 가드 어휘 — hint에 목표/전망/감축량/집약도 등이 있으면 실적 총량이 아님.
      G2. 최장 일치 — alias 해소기의 가장 긴 고유 별칭을 채택한다.
      G3. 단위 정합성 — 확정 코드의 kesg_items.unit과 m.unit이 명백히 다르면 기각.
    exact alias가 없을 때만 LLM 추정 코드를 보조 후보로 쓰며, 이 역시 G1·G3를 거친다.
    ``allow_fuzzy``는 오프라인 비교 측정 전용이고 생산 기본값은 False다.
    """
    from ..knowledge.kesg_items import by_code, resolve_kesg_code
    from ..layer1_extract import _unit_suspect

    hint = m.metric_hint.lower().replace(" ", "")
    alias_code, _alias_score, alias_method = resolve_kesg_code(m.metric_hint)
    code = alias_code if alias_method == "exact" or allow_fuzzy else None

    if getattr(m, "_kesg_backfill_blocked", False):
        return None
    # 하위·보조 수치는 어떤 추정코드가 와도 총량 코드로 잡지 않는다(중복 노드 방지).
    if any(x in hint for x in _HINT_EXCLUDE):
        return None

    # G1. 가드 어휘 — 실적 총량이 아닌 값(목표·전환량·집약도 등)은 코드 미부여.
    if any(g in hint for g in _GUARD_TERMS):
        return None

    def _conflicts(code: str) -> bool:
        return any(term in hint for term in _ASSIGNMENT_NEGATIVE_KEYWORDS.get(code, ()))

    # exact alias가 LLM 추정보다 우선한다. 삼성전기 '총 Scope 3 배출량'에 붙어 있던
    # E-3-1 오추정도 여기서 E-3-2로 교정된다.
    if not code:
        code = m.kesg_code_guess
    # 모비스 Scope 3 캐시는 코드 대신 표 머리글 숫자("2")를 guess에 넣었다.
    if code and "scope3" in hint and str(code) == "2":
        code = None
    if code and _conflicts(code):
        code = None
    # GRI 번호·표 머리글 등 비 K-ESG 문자열은 후보 코드가 아니다.
    if code and by_code(str(code)) is None:
        code = None
    if not code:
        return None

    # G3. 단위 정합성 — 항목 정의 단위와 명백히 다르면 기각(표기용 _unit_suspect 승격).
    item = by_code(str(code))
    # 용수는 보고서에서 부피(m³), K-ESG 정의에서 질량(ton)으로 병용한다. 이 동치는
    # E-5-1/E-5-2 계열에만 한정해 폐기물 등 다른 ton 지표의 m³ 오결합은 계속 차단한다.
    raw_unit = str(m.unit or "").lower().replace(" ", "")
    water_volume = code in {"E-5-1", "E-5-2"} and raw_unit in {"m3", "m³", "㎥", "m^3"}
    if item and item.unit and not water_volume and _unit_suspect(m.unit, item.unit):
        return None

    return code


def _normalize_period(
    period_raw: str, *, fallback: int, hint: str = "",
) -> tuple[int, bool]:
    """'2025-12' / '2025년' / '' → (연도 정수, 추론여부).

    두 번째 값이 True면 원문에 연도가 없어 `fallback`(=report_year)으로 채운 것이다.
    호출부가 이 사실을 노드에 남겨야 한다(EvidenceNode.period_inferred) — 값만 돌려주면
    '진짜 report_year'와 '연도 미상'이 구분되지 않는다(실측: 모비스 '미상' 13건이
    2025 실적으로 보였다).
    """
    import re
    m = re.search(r"(20\d{2})", period_raw or "")
    if m:
        return int(m.group(1)), False
    # period가 비었거나 '미상'이면 hint의 연도를 폴백보다 먼저 쓴다. 특히
    # '2040년 RE100 달성률'을 보고연도 실적으로 둔갑시키지 않고 G4 projection으로 보낸다.
    m = re.search(r"(20\d{2})", hint or "")
    if m:
        return int(m.group(1)), False
    return fallback, True


def _link_cross_check(graph: EvidenceGraph, node: EvidenceNode) -> None:
    """같은 metric/period의 DART 노드와 cross_check 엣지 연결 (D1 교차검증 재료)."""
    for other in graph.nodes_by_metric(node.metric):
        if other.id == node.id or other.period != node.period:
            continue
        if other.origin == "dart" or other.origin != node.origin:
            diff_pct = _pct_diff(node.value, other.value)
            graph.add_edge(EvidenceEdge(
                source_id=other.id,
                target_id=node.id,
                edge_type="cross_check",
                detail=f"교차검증 오차 {diff_pct:.1f}% ({other.origin}↔{node.origin})",
            ))


def _emit_derived_emission(
    graph: EvidenceGraph,
    node: EvidenceNode,
    industry_module=None,
    *,
    source_file: str | None = None,
    seq: int = 0,
) -> None:
    """전력/가스 사용량 노드 → 탄소 배출량(E-3-1) 파생 노드 자동 생성.

    industry_module이 업종 배출계수를 제공하면 전역값 위에 덮어쓴다(부분 키만
    줘도 나머지는 전역 폴백). None이면 전역 _EMISSION_FACTORS 그대로.
    """
    from ..industry.base import resolve_map
    factors = resolve_map(industry_module, "emission_factors", _EMISSION_FACTORS)

    tco2: float | None = None
    if node.unit.lower() == "kwh" and node.metric == "E-4-1":
        tco2 = node.value * factors["kWh_to_tco2"]
    elif node.unit.lower() == "mj" and node.metric == "E-4-1":
        tco2 = node.value * factors["MJ_gas_to_tco2"]
    if tco2 is None:
        return
    derived = EvidenceNode(
        id=_make_derived_node_id(
            graph.corp_code,
            "E-3-1",
            node.period,
            node.origin,
            source_file or node.source_file,
            node.id,
            seq,
        ),
        metric="E-3-1",
        value=round(tco2, 3),
        unit="tCO2eq",
        period=node.period,
        source=f"derived_from:{node.id}",
        raw_text=f"{node.raw_text} → 배출계수 환산",
        origin=node.origin,
        source_file=node.source_file,
        confidence=node.confidence * 0.95,   # 환산 불확실성 반영
        # 파생 노드는 원 노드의 period를 그대로 쓰므로 추론여부도 함께 물려받는다.
        period_inferred=node.period_inferred,
    )
    graph.add_node(derived)


def _pct_diff(a: float, b: float) -> float:
    if b == 0:
        return 0.0 if a == 0 else 100.0
    return abs(a - b) / abs(b) * 100.0


def _make_ocr_node_id(
    corp_code: str,
    metric: str,
    period: int,
    origin: Origin,
    source_file: str | None,
    metric_hint: str,
    seq: int,
) -> str:
    suffix = _stable_suffix(source_file or "", metric_hint, seq)
    return f"{corp_code}_{metric}_{period}__{origin}__{suffix}"


def _make_derived_node_id(
    corp_code: str,
    metric: str,
    period: int,
    origin: Origin,
    source_file: str | None,
    parent_id: str,
    seq: int,
) -> str:
    suffix = _stable_suffix(source_file or "", parent_id, seq)
    return f"{corp_code}_{metric}_{period}__derived_{origin}__{suffix}"


def _stable_suffix(*parts: object) -> str:
    raw = "||".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
