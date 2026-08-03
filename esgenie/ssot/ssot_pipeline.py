"""L1/L2 SSOT 연결 브리지 (v15).

v10의 Layer1(K-ESG 추출)과 Layer2(Hybrid RAG)를 v15 EvidenceGraph(SSOT)와 연결한다.

연결 포인트
-----------
  L1 — extract_with_ssot()
    · v10 extract()에 v15 EvidenceGraph를 직접 전달
      → v10이 search_nodes()를 호출해 증거 노드 ID를 자동 부착
    · OCR 출처 노드가 있는 항목은 evidence_node_ids에 ocr_* 노드도 병합
    · 'no_evidence' 플래그가 있는 항목을 OCR 노드로 해소(resolve) 처리

  L2 — build_rag_with_ssot()
    · v10 HybridRAG.build_corp_index()로 DART 원문 인덱스 먼저 빌드
    · SSOT TextNode(규정집·회의록)를 CorpIndex에 추가 편입
      → D3 의미일관성 검증과 규정 검증(P축)이 같은 인덱스를 공유
    · SSOT EvidenceNode(OCR 수치)를 CorpIndex에 추가 편입
      → "E-4-1 128400 kWh (kepco_bill.pdf)" 같은 증빙 문자열로 검색 가능
    · rag 인스턴스에는 얹지 않고 CorpIndex를 반환한다(회사별 격리).

사용 예시 (app.py)
------------------
    from esgenie.ssot.ssot_pipeline import extract_with_ssot, build_rag_with_ssot

    graph  = build_unified_graph(dart_report, ocr_extractions, ...)
    l1     = extract_with_ssot(dart_report, graph)
    rag    = HybridRAG()
    corp   = build_rag_with_ssot(rag, dart_report, graph)
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..layer2_rag import CorpIndex
from .evidence_graph import EvidenceGraph, EvidenceNode

# 값 공급 경로 티어 — 원장(L1.mapped["source_tier"])에 남는다.
# dart_client.SOURCE_DART_REGEX(무게이트) < SOURCE_OCR_GATED(L0 G1~G6 통과) 순.
SOURCE_OCR_GATED = "ocr_node_gated"


# ====================================================================
# L1 — extract_with_ssot
# ====================================================================

def extract_with_ssot(
    report: Any,           # esgenie.dart_client.CompanyReport
    graph: EvidenceGraph,
    profile: str | None = None,   # "sme" | "full" | None(자동 판별)
):
    """K-ESG 항목 추출(프로파일 기반) + v15 SSOT 증거 부착.

    Parameters
    ----------
    report : CompanyReport   (v10 DART 보고서 객체)
    graph  : EvidenceGraph   (v15 SSOT — DART + OCR 통합)
    profile: K-ESG 프로파일 — None이면 corp_code로 자동 판별
             (중소기업 → 기본형 28항목, 상장사 → 61항목 전체)

    Returns
    -------
    ExtractionResult  (v10 호환, evidence_node_ids에 OCR 노드 포함)
    """
    from esgenie.layer1_extract import extract as _v10_extract

    # v10 extract()는 evidence_graph.search_nodes(keywords, period) 인터페이스만 사용.
    # v15 EvidenceGraph에 search_nodes()가 추가됐으므로 직접 전달 가능.
    result = _v10_extract(report, evidence_graph=graph, profile=profile)

    _merge_ssot_evidence(result, graph)
    return result


def _merge_ssot_evidence(result: Any, graph: EvidenceGraph) -> None:
    """SSOT graph의 OCR/TextNode를 L1 결과에 병합."""
    from esgenie.dart_client import SOURCE_DART_REGEX
    from esgenie.knowledge.kesg_items import by_code as _by_code
    from esgenie.ssot.node_select import (
        classify_value_role,
        is_partial_aggregate,
        normalize_to_item_unit,
        select_representative_node,
    )

    # v10 extract()는 DART 노드만 탐색하므로, OCR 출처(ocr_structured / ocr_unstructured)
    # 노드도 evidence_node_ids에 추가하고 'no_evidence' 플래그를 해소한다.
    ocr_by_metric: dict[str, list[str]] = {}
    # 코드별 대표 OCR 노드 — DART 미공시 코드를 승격할 때 표시값 출처.
    #
    # 선택 규칙(2026-07-26): hint 기반 공용 규칙(node_select.select_representative_node).
    # 연도는 이 규칙의 **최후 tie-breaker(7순위)로 강등**됐다. 2026-07-25에 넣은
    # '보고 연도 최근접'만으로는 같은 연도 안에서 사실상 임의 선택이라, 정답 노드가 같은
    # 풀에 있어도 파생값('온실가스 감축 효과')·부분값('국내(별도)')을 골랐다.
    # 원문 표의 연도 열이 한 period로 뭉개져 period 신뢰도 자체가 낮은 것도 이유다.
    #
    # D1의 G5(layer3_detect._score_d1_numeric)가 **같은 함수를 호출한다**. 이 대칭이
    # 깨지면 데이터가 옳아도 claim ≠ node가 되어 구조적 D1 오탐이 난다.
    # 규칙이 전 후보를 배제하면 None → 해당 코드는 미공시(잘못된 값보다 미공시가 낫다).
    ref_year = getattr(graph, "report_year", None)

    ocr_pool: dict[str, list[EvidenceNode]] = {}
    for node in graph.nodes.values():
        if node.origin in ("ocr_structured", "ocr_unstructured"):
            ocr_by_metric.setdefault(node.metric, []).append(node.id)
            ocr_pool.setdefault(node.metric, []).append(node)

    def _is_qualitative(code: str, entry: dict[str, Any]) -> bool:
        """정성(존재형) 항목인가 — OCR 정량 노드 승격을 막는 판정(2026-07-28 결함 (c)).

        entry에 이미 담긴 data_type을 우선 쓰고(mapped entry가 kesg_items에서 복사한다),
        없으면 항목 정의로 폴백한다. '혼합'은 정량값이 정상이므로 대상이 아니다.
        """
        dt = entry.get("data_type")
        if not dt:
            item = _by_code(code)
            dt = item.data_type if item else ""
        return dt == "정성"

    def _add_flag(code: str, flag: str) -> None:
        flags = result.confidence_flags.get(code, [])
        if flag not in flags:
            result.confidence_flags[code] = flags + [flag]

    def _flag_partial(code: str, node: EvidenceNode) -> None:
        """대표 노드가 부분값이면 'partial_value'를 남긴다 — 2026-07-28 결함 (a).

        총량 후보가 아예 없는 풀에서는 부분값 하나가 이긴다(LG화학 E-4-1 '비재생 전력
        소비량 해외', NAVER E-3-2 'Scope 3 - Upstream 구매 제품 및 서비스'). 값을 버리면
        커버리지가 더 떨어지므로 싣되, 전사 총량이 아니라는 사실을 산출물에 드러낸다.
        D1은 이 오류를 못 잡는다(원장·노드가 같은 값이라 Δ=0) — 표기가 유일한 방어선이다.
        layer2_rag._area_item_rows가 이 플래그를 원장 표 상태에 '·부분값'으로 붙인다.
        """
        role = classify_value_role(code, node, report_year=ref_year)
        node.value_role = role
        if not is_partial_aggregate(node, code, report_year=ref_year):
            return
        _add_flag(code, "partial_value")
        raw = str(getattr(node, "raw_text", "") or "")
        hint = raw.split("=", 1)[0].strip() if "=" in raw else raw.strip()
        result.notes.append(
            f"[부분값] {code}: 대표 노드가 전사 총량이 아니다 — "
            f"{hint or node.id} (총량 후보 없음)"
        )

    def _flag_period_inferred(code: str, node: EvidenceNode) -> None:
        """대표 노드의 연도가 폴백값이면 'period_inferred'를 남긴다 — 2026-07-29.

        `_normalize_period`가 원문에서 `20\\d{2}`를 못 찾으면 report_year로 조용히 채운다.
        그러면 원장 표에서 '2025 실적'과 '연도 미상'이 똑같이 보인다(실측 15건 —
        모비스 폐기물 12건이 실제로는 2022 값이었다). 값은 하위 호환으로 그대로 싣되
        추론값이라는 사실은 드러낸다 — D1은 이 오류를 못 잡는다(원장·노드가 같은 값).
        """
        if not getattr(node, "period_inferred", False):
            return
        _add_flag(code, "period_inferred")
        raw = str(getattr(node, "raw_text", "") or "")
        hint = raw.split("=", 1)[0].strip() if "=" in raw else raw.strip()
        result.notes.append(
            f"[연도 미상] {code}: 원문에 연도가 없어 {node.period}(보고연도)로 채운 값 — "
            f"{hint or node.id}"
        )

    ocr_repr: dict[str, EvidenceNode] = {}
    for metric, pool in ocr_pool.items():
        picked = select_representative_node(metric, pool, report_year=ref_year)
        if picked is not None:
            ocr_repr[metric] = picked
            # D1이 따라 쓸 수 있도록 결정을 그래프에 남긴다(2026-07-26). 공용 함수를
            # 공유해도 원장(OCR-only)과 D1(DART 포함)의 후보 풀이 달라 규칙을 각자
            # 다시 돌리면 갈린다 — 결정 자체를 공유해야 대칭이 성립한다.
            graph.representative_node_ids[metric] = picked.id
        else:
            # 전 후보가 파생·비실적/지표 충돌로 배제 — 값을 채우지 않고 플래그만 남긴다.
            # 노드는 evidence_node_ids에 그대로 붙어 감사추적용으로 보존된다(폐기 아님).
            flags = result.confidence_flags.get(metric, [])
            if "no_representative_node" not in flags:
                result.confidence_flags[metric] = flags + ["no_representative_node"]
            result.notes.append(
                f"[대표노드 없음] {metric}: 후보 {len(pool)}개 전부 파생·비실적 또는 "
                f"지표 불일치로 배제 → 미공시 유지"
            )

    # 정성 조항(TextNode)도 존재형 문항의 증빙 근거다. 규정집/회의록에서 매핑된
    # K-ESG 코드가 있으면 해당 항목의 evidence_node_ids에 편입한다.
    text_by_code: dict[str, list[str]] = {}
    for tnode in graph.text_nodes.values():
        if tnode.kesg_code:
            text_by_code.setdefault(tnode.kesg_code, []).append(tnode.id)

    for code, entry in result.mapped.items():
        ocr_ids = ocr_by_metric.get(code, [])
        text_ids = text_by_code.get(code, [])
        if ocr_ids or text_ids:
            existing = entry.get("evidence_node_ids") or []
            entry["evidence_node_ids"] = list(dict.fromkeys(existing + ocr_ids + text_ids))
        if ocr_ids:
            # OCR 증거가 생겼으면 'no_evidence' 플래그 제거
            flags = result.confidence_flags.get(code, [])
            if "no_evidence" in flags:
                result.confidence_flags[code] = [f for f in flags if f != "no_evidence"]
                result.notes.append(f"[OCR resolved] {code}: OCR 증빙 노드로 근거 확보")

        # 우선순위 역전 해소(2026-07-25) — 무게이트 DART 정규식 값이 G1~G6를 통과한 OCR
        # 노드를 이기던 문제. 정규식 경로는 문서 전체 첫 매치 승리라 연도·표구조·가드어휘
        # 검증이 없다(현대모비스 E-5-1 용수 114,884 = 실제로는 '누적 온실가스 목표 감축량').
        # 태그가 붙은 값(SOURCE_DART_REGEX)만 강등하며, 구조화 API·사외이사 재계산 등
        # 태그 없는 값은 종전대로 유지된다.
        repr_node = ocr_repr.get(code)
        # 정성 항목에는 OCR 정량값을 싣지 않는다(2026-07-28 결함 (c)). E-1-2 '환경경영
        # 추진체계'는 존재형 항목인데 'ESG위원회 정기회의 횟수' 2회(삼성전기)·'1차 개최
        # 출석률' 5명(LG화학)이 실렸다. 숫자가 들어갈 자리가 아니다.
        if repr_node is not None and _is_qualitative(code, entry):
            repr_node = None
        if repr_node is not None and entry.get("source_tier") == SOURCE_DART_REGEX:
            item = _by_code(code)
            old_value, old_unit = entry.get("value"), entry.get("unit")
            # 원장에는 항목 정의 단위로 환산해 저장(2026-07-26). ton→kg 1,000배·MWh→TJ 등.
            new_value, new_unit, unit_flag = normalize_to_item_unit(
                code, repr_node.value, repr_node.unit)
            entry["value"] = new_value
            entry["unit"] = new_unit or (item.unit if item else old_unit)
            entry["value_role"] = classify_value_role(
                code, repr_node, report_year=ref_year)
            if unit_flag:
                _add_flag(code, unit_flag)
            _flag_partial(code, repr_node)
            _flag_period_inferred(code, repr_node)
            entry["note"] = (
                f"OCR 증빙 노드로 대체 ({repr_node.source_file or repr_node.source}) — "
                f"DART 정규식 값 {old_value}{old_unit or ''}은 무게이트 추출이라 강등"
            )
            entry["source_tier"] = SOURCE_OCR_GATED
            entry["superseded_value"] = old_value
            flags = result.confidence_flags.get(code, [])
            if "regex_superseded" not in flags:
                result.confidence_flags[code] = flags + ["regex_superseded"]
            result.notes.append(
                f"[경로 우선순위] {code}: DART 정규식 {old_value} → OCR 노드 {repr_node.value}"
            )

    # DART에 없더라도 OCR 정량노드(전기·가스·수도·폐기물 등)나 규정집/회의록 TextNode가
    # 있으면 공시 항목으로 승격한다. 기존엔 TextNode(정성)만 승격하고 OCR 정량노드는
    # 'DART가 먼저 만든 mapped 항목'에만 보조증거로 붙어, DART 미공시 코드의 OCR 정량값이
    # 통째로 버려졌다(예: 재활용률 E-6-2, 용수 E-5-1 — 공시 항목 표에서 누락).
    # by_code()로 유효 K-ESG 코드만 승격해 깨진 VLM hint('CSPD count' 등)는 자동 제외하며,
    # 이 게이트가 그래프 빌드 단계의 중복가드(지정폐기물 차단·재활용량/률 구분)도 그대로 보존한다.
    candidate_codes = sorted(set(ocr_by_metric) | set(text_by_code))
    if candidate_codes:
        from esgenie.knowledge.kesg_items import by_code, items_for_profile

        profile_codes = {item.code for item in items_for_profile(result.profile)}
        quant_added = 0
        text_added = 0
        for code in candidate_codes:
            if code in result.mapped:
                continue
            item = by_code(code)
            if item is None:
                continue
            ocr_ids = ocr_by_metric.get(code, [])
            text_ids = text_by_code.get(code, [])
            repr_node = ocr_repr.get(code)
            # 정성 항목은 정량 승격 대상이 아니다(2026-07-28 결함 (c)). TextNode가 있으면
            # 아래 '문서 조항 확인' 경로로 정상 승격되고, 없으면 미공시로 남는다.
            if repr_node is not None and item.data_type == "정성":
                repr_node = None
            # 대표 노드가 배제됐고 정성 근거도 없으면 승격 자체를 하지 않는다(2026-07-26).
            # repr_node=None은 종전엔 'TextNode만 있는 존재형 문항'만 뜻했지만, 이제
            # '전 후보가 파생·비실적으로 배제됨'도 뜻한다. 정량 항목에 '문서 조항 확인'을
            # 채우면 미공시가 공시로 위장된다 — 잘못된 값보다 미공시가 낫다(라벨링 §3-1).
            if repr_node is None and not text_ids:
                continue
            value: Any
            if repr_node is not None:
                # 정량 OCR 노드 → 실제 수치로 채움. 항목 정의 단위로 환산해 저장(2026-07-26).
                value, unit, unit_flag = normalize_to_item_unit(
                    code, repr_node.value, repr_node.unit)
                unit = unit or (item.unit or "")
                if unit_flag:
                    _add_flag(code, unit_flag)
                _flag_partial(code, repr_node)
                _flag_period_inferred(code, repr_node)
                note = f"OCR 정량 증빙으로 자동 인식 ({repr_node.source_file or repr_node.source})"
                tier = SOURCE_OCR_GATED
                value_role = classify_value_role(code, repr_node, report_year=ref_year)
                quant_added += 1
            else:
                # 정성 TextNode만 있는 존재형 문항 → 문서 조항 확인
                value = "문서 조항 확인"
                unit = ""
                note = "OCR 정성 증빙으로 자동 인식"
                tier = SOURCE_OCR_GATED
                value_role = "total"
                text_added += 1
            in_profile = code in profile_codes
            result.mapped[code] = {
                "code": item.code,
                "name": item.name,
                "area": item.area,
                "category": item.category,
                "data_type": item.data_type,
                "value": value,
                "unit": unit,
                "note": note,
                "source_tier": tier,
                "value_role": value_role,
                "evidence_node_ids": list(dict.fromkeys(ocr_ids + text_ids)),
                "beyond_profile": not in_profile,
            }
            if in_profile:
                if code in result.missing:
                    result.missing.remove(code)
                result.by_area[item.area]["present"] += 1
            elif code not in result.beyond_profile:
                result.beyond_profile.append(code)

        if quant_added or text_added:
            profile_items = items_for_profile(result.profile)
            in_profile_mapped = sum(
                1 for entry in result.mapped.values() if not entry.get("beyond_profile")
            )
            result.coverage_pct = 100 * in_profile_mapped / len(profile_items)
            if quant_added:
                result.notes.append(
                    f"OCR 정량 증빙 승격: {quant_added}개 항목을 내부 증빙 수치로 자동 채움"
                )
            if text_added:
                result.notes.append(
                    f"TextNode 증빙 병합: {text_added}개 항목을 규정/회의록 근거로 자동 채움"
                )

    # ── 커버리지 메모 추가 ─────────────────────────────────────────────
    ocr_resolved = sum(
        1 for code, entry in result.mapped.items()
        if any(
            nid for nid in (entry.get("evidence_node_ids") or [])
            if "__ocr_" in nid
        )
    )
    if ocr_resolved:
        result.notes.append(f"OCR 증빙 병합: {ocr_resolved}개 항목에 내부 증빙 노드 부착")


# ====================================================================
# L2 — build_rag_with_ssot
# ====================================================================

def build_rag_with_ssot(
    rag: Any,              # esgenie.layer2_rag.HybridRAG
    report: Any,           # CompanyReport
    graph: EvidenceGraph,
) -> CorpIndex:
    """DART + SSOT(TextNode + OCR 수치)를 모두 편입한 CorpIndex를 만들어 반환.

    Parameters
    ----------
    rag    : HybridRAG  (v10 인스턴스 — kesg/industry 인덱스는 이미 로드된 상태)
    report : CompanyReport
    graph  : EvidenceGraph  (v15 SSOT)

    회사별로 달라지는 corp 인덱스는 rag 인스턴스에 얹지 않고 CorpIndex로
    반환한다. 기존 build_corp_index()를 먼저 호출한 뒤 TextNode/수치 노드를
    추가 편입한다.
    """
    from esgenie.embeddings import IndexedDoc

    # ── DART 원문 인덱스 먼저 빌드 (v10 원본 로직) ────────────────────
    corp = rag.build_corp_index(report)

    # ── SSOT TextNode 추가 편입 (규정집·회의록 조항) ───────────────────
    text_docs: list[IndexedDoc] = []
    for tnode in graph.text_nodes.values():
        code_tag = f"[{tnode.kesg_code}] " if tnode.kesg_code else ""
        text = (
            f"{code_tag}{tnode.section}: {tnode.text}"
            f" (출처: {tnode.source_file}, p.{tnode.page})"
        )
        text_docs.append(IndexedDoc(
            text=text,
            meta={
                "source": "ssot_text",
                "kesg_code": tnode.kesg_code,
                "source_file": tnode.source_file,
                "node_id": tnode.id,
            },
        ))

    # ── SSOT OCR 수치 노드 추가 편입 ──────────────────────────────────
    ocr_docs: list[IndexedDoc] = []
    for node in graph.nodes.values():
        if node.origin in ("ocr_structured", "ocr_unstructured"):
            text = (
                f"[{node.metric}] {node.value}{node.unit} "
                f"({node.period}년, 출처: {node.source_file or node.source}, "
                f"신뢰도: {node.confidence:.2f})"
            )
            ocr_docs.append(IndexedDoc(
                text=text,
                meta={
                    "source": "ssot_ocr",
                    "kesg_code": node.metric,
                    "source_file": node.source_file,
                    "origin": node.origin,
                    "node_id": node.id,
                },
            ))

    # ── corp 인덱스에 추가 문서 편입 ────────────────────────────────────
    # VectorIndex.add()가 없을 경우 기존 문서 위에 rebuild 방식으로 처리
    extra = text_docs + ocr_docs
    if extra:
        corp = _extend_corp_index(corp, extra)

    return corp


def _extend_corp_index(corp: CorpIndex, extra_docs: list[Any]) -> CorpIndex:
    """기존 CorpIndex에 문서를 추가 편입한 새(혹은 갱신된) CorpIndex를 반환.

    VectorIndex가 add() API를 제공하면 그것을 사용하고,
    없으면 기존 문서를 꺼내 합쳐서 rebuild한다.
    """
    index = corp.vector

    # ── add() API 있으면 직접 추가 ────────────────────────────────────
    if hasattr(index, "add"):
        index.add(extra_docs)
        return corp

    # ── rebuild 방식 폴백 ─────────────────────────────────────────────
    # VectorIndex 내부 문서 목록을 꺼내 합쳐 rebuild
    existing: list[Any] = []
    if hasattr(index, "_docs"):
        existing = list(index._docs)

    from esgenie.embeddings import BM25Index, VectorIndex
    new_index = VectorIndex()
    new_index.build(existing + extra_docs)
    new_bm25 = BM25Index()
    new_bm25.build(existing + extra_docs)
    return CorpIndex(vector=new_index, bm25=new_bm25)


# ====================================================================
# 유틸 — SSOT 요약 출력 (디버그/로깅용)
# ====================================================================

def ssot_summary(graph: EvidenceGraph) -> dict[str, Any]:
    """EvidenceGraph의 출처별 통계를 반환 (로그·UI 표시용)."""
    from collections import Counter
    origins = Counter(n.origin for n in graph.nodes.values())
    return {
        "corp": graph.corp_name,
        "total_nodes": len(graph.nodes),
        "by_origin": dict(origins),
        "text_nodes": len(graph.text_nodes),
        "edges": len(graph.edges),
        "cross_check_edges": sum(1 for e in graph.edges if e.edge_type == "cross_check"),
    }
