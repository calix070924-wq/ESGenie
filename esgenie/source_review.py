"""원문·추출·작성문에서 확인할 사항. 기존 위험 점수나 대표값을 변경하지 않는다.

새 LLM 판정 없이 확인된 값/검증 결과만 설명한다. 검색 성공은 문제의 증명이
아니며, 출처가 요약뿐이면 원문 인용으로 표시하지 않는다.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from .knowledge.kesg_items import PROFILES, by_code, items_for_profile
from .ssot.node_select import is_derived_hint


@dataclass
class ReviewEvidence:
    node_id: str = ""
    source_file: str = ""
    page: int | None = None  # 0-based, 표시할 때만 +1
    quote: str = ""
    extracted_text: str = ""
    #: "direct" = 이 확인 사항이 실제로 근거로 삼은 원장·그래프 노드.
    #: "context" = 항목별 추가 검색이 덧붙인 주변 설명. 관련 정책 문구가 사건의 경위나
    #: 직접 증거로 읽히지 않도록 표시를 구분한다.
    role: str = "direct"


@dataclass
class ReviewFinding:
    id: str
    category: str
    title: str
    fact: str
    reason: str
    action: str
    area: str = ""
    code: str = ""
    evidence: list[ReviewEvidence] = field(default_factory=list)
    check_reason: str = ""  # 기존 검사 사유 코드. 설명 문구와 분리해 감사 기록에 보존한다.
    check_result: dict[str, Any] = field(default_factory=dict)  # 원래 축 점수·사유·기권·근거 ID

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _reference(node: Any, *, role: str = "direct") -> ReviewEvidence:
    return ReviewEvidence(
        node_id=node.id, source_file=getattr(node, "source_file", "") or getattr(node, "source", ""),
        page=getattr(node, "page", None), quote=getattr(node, "quote", ""),
        extracted_text=getattr(node, "raw_text", "") or getattr(node, "text", ""),
        role=role,
    )


_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    """수치 토큰만 정규화해 모은다. 1,234와 1234, 1.0과 1을 같게 본다."""
    return {str(float(match.group())).rstrip("0").rstrip(".") or "0"
            for match in _NUMBER.finditer(str(text).replace(",", ""))}


def _evidence_role(finding: ReviewFinding, node: Any) -> str:
    """추가로 붙인 원문이 확인된 내용을 직접 진술하는지 판정한다.

    새 의미 판단을 하지 않는다. 확인 사항이 제시한 수치·연도가 그 원문에 모두
    그대로 적혀 있을 때만 직접 근거로 표시하고, 나머지는 주변 설명으로 남긴다.
    수치가 없는 확인 사항(작성 문장·검색 누락 등)은 보수적으로 주변 설명으로 둔다.
    """
    wanted = _numbers(finding.fact)
    if not wanted:
        return "context"
    text = f"{getattr(node, 'quote', '') or ''} {getattr(node, 'raw_text', '') or getattr(node, 'text', '') or ''}"
    return "direct" if wanted <= _numbers(text) else "context"


def _finding(category: str, title: str, fact: str, reason: str, action: str,
             *, code: str = "", area: str = "", check_reason: str = "", evidence=(),
             check_result: dict[str, Any] | None = None) -> ReviewFinding:
    refs = list(evidence)
    identity = "|".join([category, code, area, title, fact, reason, check_reason,
                         *[f"{r.node_id}:{r.source_file}:{r.page}" for r in refs]])
    return ReviewFinding(
        id="review_" + hashlib.sha256(identity.encode()).hexdigest()[:16],
        category=category, title=title, fact=fact, reason=reason, action=action,
        code=code, area=area or (code[:1] if by_code(code) else ""),
        check_reason=check_reason, check_result=dict(check_result or {}), evidence=refs,
    )


def _record_reference(source_file: str, entry: dict[str, Any], record: dict[str, Any]) -> ReviewEvidence:
    """라우터 기록의 출처. 기록에 페이지가 없으면 그 기록이 나온 청크의 실제 페이지를 쓴다.

    페이지는 0부터 센 내부 값 그대로 둔다(표시할 때만 +1). 0쪽을 빈 값으로 보지 않도록
    `None`인지로만 가른다. 둘 다 없으면 미상으로 남긴다.
    """
    page = record.get("page") if record.get("page") is not None else entry.get("page")
    return ReviewEvidence(source_file=source_file, page=page if isinstance(page, int) else None,
                          quote=str(record.get("quote") or ""))


def _plain_number(value: Any) -> str:
    """값을 **반올림하지 않고** 적는다. `:g`는 840,000,000을 8.4e+08로 바꿔 버린다."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return f"{int(value):,}"
    return f"{value:,}" if isinstance(value, int) else repr(value)


def _with_unit(value: Any, unit: Any) -> str:
    return _plain_number(value) + (f" {unit}" if unit else " (단위 미확인)")


_UNRESOLVED_CAUSES = {
    "multiple_candidates": "근거 문구에 같은 단위의 자릿수 표기가 여러 개라 어느 값인지 정할 수 없습니다.",
    "unit_unproven": "원문 표기나 추출값의 단위를 읽지 못해 같은 수량인지 확인할 수 없습니다.",
    "metric_unproven": "원문 표기의 라벨이 이 지표와 같다는 것을 확인하지 못했습니다(같은 문장의 다른 지표일 수 있습니다).",
    "ratio_out_of_range": "원문 표기와 추출값의 차이가 자릿수 오류로 설명되지 않습니다.",
}


# 0을 싣지 않은 하위 사유(`ocr_router._ZeroVerdict.cause`). 사유가 없는 과거 기록은 기본 문구를 쓴다.
_ZERO_DEFAULT_CAUSE = ("근거 문구에 0이나 이 지표의 명시적 미발생·미보유 서술이 없어 0으로 싣지 않았습니다. "
                       "미공시(-)·빈 칸·미확인·예방 목표 문구일 수 있습니다.")
_ZERO_CAUSES = {
    "no_zero_statement": _ZERO_DEFAULT_CAUSE,
    "other_subject": "근거 문구의 미발생·미보유 서술이 이 지표가 아닌 다른 대상에 관한 것이라 0으로 싣지 않았습니다.",
    "future_or_intent": "근거 문구가 미래 예상·목표·계획을 말하고 있어 실제로 없었다는 사실로 볼 수 없어 0으로 싣지 않았습니다.",
    "not_confirmed": "근거 문구가 미확인·미집계·미공시·해당 없음을 말하고 있어 0으로 싣지 않았습니다.",
    "evidence_insufficient": "근거 문구가 미발생·미보유를 확인하지 못했다는 뜻(증거 부족·단정 어려움)이라 0으로 싣지 않았습니다.",
    "negation_negated": "근거 문구가 미발생·미보유를 다시 부정하고 있어(예: '미보유 상태가 아니다') 0으로 싣지 않았습니다.",
    "interpretation_unknown": "근거 문구의 0·부정 서술이 실제 사실을 말하는지 판정하지 못해 0으로 싣지 않았습니다.",
    "conditional": "근거 문구가 조건(예: '발생하지 않으면', '0건일 경우')을 말하고 있어 실제로 없었다는 사실로 볼 수 없어 0으로 싣지 않았습니다.",
    "assumption": "근거 문구가 가정·전제(예: '발생하지 않는다고 가정한다')를 말하고 있어 실제 사실로 볼 수 없어 0으로 싣지 않았습니다.",
    "period_ambiguous": "근거 문구에 기간이 여럿이라 이 0이 어느 기간의 값인지 특정하지 못해 0으로 싣지 않았습니다.",
    "period_mismatch": "근거 문구의 기간이 지표 기간과 달라 다른 기간의 0으로 이 기간의 0을 추론하지 않았습니다.",
    "period_unproven": "근거 문구의 기간이 지표 기간과 같은 범위인지 확인하지 못해(기준일·연간 등) 0으로 싣지 않았습니다.",
    "site_mismatch": "근거 문구의 사업장이 지표 사업장과 달라 다른 사업장의 0으로 이 사업장의 0을 추론하지 않았습니다.",
}


def _zero_scope(record: dict[str, Any]) -> str:
    """0을 뺀 행의 지표 범위와 원문 범위. 기간·사업장 불일치를 한눈에 대조하게 한다."""
    parts = []
    if record.get("evidence_period"):
        parts.append(f"원문 기간 {record['evidence_period']}")
    if record.get("metric_site"):
        parts.append(f"지표 사업장 {record['metric_site']}")
    if record.get("evidence_site"):
        parts.append(f"원문 사업장 {record['evidence_site']}")
    return (" · " + " · ".join(parts)) if parts else ""


def _reconciliation_reviews(ext: Any) -> list[ReviewFinding]:
    """추출값을 근거 문구와 대조한 기록(`router_meta.value_reconciliations`)을 확인 목록에 싣는다.

    - `scale_chain_unresolved`: 값을 확정하지 못했다 — 추출값과 원문 후보를 함께 보인다.
    - `value_not_written_in_evidence`: 인용에 그 수가 직접 없다 — 계산·해석 근거 확인. 오류로
      단정하지 않는다.
    - `scale_chain_recomposed`: 원문 표기로 이미 보정했다 — 미해결 사항이 아니므로 별도 분류로
      원값·수정값·근거만 남긴다.
    값이 실린 행이므로 '수치를 읽지 못한 행' 수(`unvalued_records`)와 합치지 않는다.
    """
    findings: list[ReviewFinding] = []
    for entry in ext.router_meta.get("value_reconciliations", []) or []:
        for record in entry.get("records", []) or []:
            reason = record.get("reason")
            hint = str(record.get("metric_hint") or "지표 미확인")
            period = f" · 기간 {record['period']}" if record.get("period") else ""
            ref = _record_reference(ext.source_file, entry, record)
            if reason == "scale_chain_unresolved":
                if record.get("candidates"):
                    shown = ", ".join(str(c.get("surface") or _plain_number(c.get("amount")))
                                      + (f" {c['unit']}" if c.get("unit") else " (단위 미확인)")
                                      for c in record["candidates"])
                else:   # 후보 단위를 남기지 않은 기록 — 배율을 곱한 값만 있다.
                    shown = ", ".join(f"{_plain_number(amount)} (배율 적용값)"
                                      for amount in record.get("source_amounts", []))
                cause = _UNRESOLVED_CAUSES.get(str(record.get("cause") or ""),
                                               "근거 문구의 자릿수 표기와 추출값이 일치하지 않습니다.")
                findings.append(_finding(
                    "data_quality", "추출값과 원문 표기 대조 미해결",
                    f"{hint}: 추출값 {_with_unit(record.get('value'), record.get('unit'))}{period}"
                    f" · 원문 후보 {shown or '없음'}",
                    f"{cause} 추출값을 바꾸지 않고 그대로 두었습니다.",
                    "원본의 해당 문장에서 이 지표의 값과 단위를 확인하고, 다르면 올바른 값으로 수정하세요.",
                    check_reason="scale_chain_unresolved", check_result=dict(record), evidence=[ref]))
            elif reason == "value_not_written_in_evidence":
                findings.append(_finding(
                    "data_quality", "인용에 직접 적히지 않은 값 확인",
                    f"{hint}: 추출값 {_with_unit(record.get('value'), record.get('unit'))}{period}",
                    "근거 인용에 이 수치가 직접 적혀 있지 않아 서술을 수치로 옮기거나 계산한 값일 수 "
                    "있습니다. 값이 틀렸다는 판정은 아닙니다.",
                    "인용 문장과 계산·환산 근거를 대조해 값이 원문 의미와 맞는지 확인하세요.",
                    check_reason="value_not_written_in_evidence", check_result=dict(record),
                    evidence=[ref]))
            elif reason == "scale_chain_recomposed":
                source = str(record.get("source_surface") or "")
                source_unit = record.get("source_unit")
                findings.append(_finding(
                    "correction", "원문 표기로 보정한 값",
                    f"{hint}: {_with_unit(record.get('value_before'), record.get('unit'))} → "
                    f"{_with_unit(record.get('value_after'), record.get('unit'))}{period}"
                    + (f" · 원문 {source}" + (f" {source_unit}" if source_unit else "") if source else ""),
                    "추출값이 원문의 연속 자릿수 표기와 달라 같은 지표·같은 단위군으로 확인한 원문 값으로 "
                    "바꿨습니다. 미해결 오류가 아니라 보정 기록입니다.",
                    "원문 표기와 보정값이 맞는지 한 번 대조하세요.",
                    check_reason="scale_chain_recomposed", check_result=dict(record), evidence=[ref]))
    return findings


def build_source_review(output: Any) -> list[ReviewFinding]:
    """SSOT와 기존 검증 결과를 읽어 집계한다. 모든 문제의 발견을 보장하지 않는다."""
    findings: list[ReviewFinding] = []
    graph = output.evidence_graph
    nodes = graph.nodes
    extractions = getattr(output, "ocr_extractions", [])
    mock_sources = {ext.source_file for ext in extractions
                    if ext.router_meta.get("mock") or ext.router_meta.get("extraction_status") == "mock"}

    for ext in extractions:
        meta = ext.router_meta
        status = "mock" if meta.get("mock") else meta.get("extraction_status", "")
        ref = ReviewEvidence(source_file=ext.source_file)
        if status in {"failed", "partial", "mock"}:
            labels = {"failed": "문서를 읽지 못했습니다.", "partial": "일부 구간을 읽지 못했습니다.",
                      "mock": "실제 추출 대신 시연용 결과가 사용됐습니다."}
            detail = str(meta.get("failure_detail") or meta.get("failure_reason") or "")
            findings.append(_finding(
                "extraction", "입력 자료 처리 확인", labels[status] + (f" {detail}" if detail else ""),
                "해당 자료의 처리가 완료되지 않아 전체 내용이 반영됐다고 볼 수 없습니다.",
                "연결·문서 상태와 실패 구간을 확인한 뒤 다시 추출하세요.",
                check_reason=str(meta.get("failure_reason") or status), evidence=[ref]))
        for failure in meta.get("chunk_failures", []):
            findings.append(_finding(
                "extraction", "읽지 못한 구간", str(failure.get("detail") or failure.get("reason") or "추출 실패"),
                "이 구간의 근거가 결과에서 빠질 수 있습니다.", "해당 페이지를 원본과 대조하고 다시 추출하세요.",
                check_reason=str(failure.get("reason") or ""),
                evidence=[ReviewEvidence(source_file=ext.source_file, page=failure.get("page"))]))
        # 라벨은 읽혔지만 수치를 보고하지 못한 행. 추출 상태는 complete이지만 그 행의
        # 값은 결과에 없으므로 조용히 넘기지 않는다. 화면이 길어지지 않게 자료마다 한
        # 건으로 묶고 라벨은 앞 5개만 보여 준다. 0으로 채우거나 미공시로 단정하지 않는다.
        unvalued = [record for entry in meta.get("unvalued_records", [])
                    for record in entry.get("records", [])
                    if record.get("reason") != "zero_not_in_evidence"]
        if unvalued:
            labels = [str(record.get("metric_hint") or "").strip() for record in unvalued]
            shown = [label for label in labels[:5] if label]
            detail = ", ".join(shown) + (" 등" if len(labels) > len(shown) else "")
            findings.append(_finding(
                "extraction", "값을 읽지 못한 항목",
                f"라벨은 읽었으나 수치를 확인하지 못한 행 {len(unvalued)}건" + (f": {detail}" if detail else ""),
                "그림·빈 칸에 있는 값일 수 있어 해당 행의 수치가 결과에 반영되지 않았습니다. "
                "미공시라는 확정 판정은 아닙니다.",
                "원본의 해당 표·그래프에서 값을 확인하고 필요하면 직접 입력하세요.",
                check_reason="value_not_reported", evidence=[ref]))
        # 0으로 실린 값을 원문 근거가 없어 뺀 행은 '그림·빈 칸' 안내와 사유가 다르다.
        # 사유(`cause`)별로 설명한다 — 미공시·다른 기간·다른 사업장·미래 예상·증거 부족.
        for entry in meta.get("unvalued_records", []):
            for record in entry.get("records", []):
                if record.get("reason") != "zero_not_in_evidence":
                    continue
                findings.append(_finding(
                    "extraction", "0값의 원문 근거 확인",
                    f"{record.get('metric_hint') or '지표 미확인'}: 추출값 0"
                    + (f" {record['unit']}" if record.get("unit") else "")
                    + (f" · 기간 {record['period']}" if record.get("period") else "")
                    + _zero_scope(record),
                    _ZERO_CAUSES.get(record.get("cause"), _ZERO_DEFAULT_CAUSE)
                    + " 실제 0이라는 판정도, 미공시라는 판정도 아닙니다.",
                    "원본에서 이 지표가 0인지, 미공시·미집계인지 확인하고 0이면 근거와 함께 입력하세요.",
                    check_reason="zero_not_in_evidence", check_result=dict(record),
                    evidence=[_record_reference(ext.source_file, entry, record)]))
        if ext.source_file not in mock_sources:   # 시연값의 대조 기록은 입력 경고로만 알린다
            findings.extend(_reconciliation_reviews(ext))
        for row in meta.get("consistency_findings", []):
            if row.get("severity") != "fail":
                continue
            findings.append(_finding(
                "data_quality", "문서 내부 수치 정합성 확인", str(row.get("detail") or row.get("rule_id")),
                "합계·비율 등의 수치 관계가 기존 정합성 검사와 일치하지 않습니다.",
                "원본 구성값·단위·합계와 자동 보정 여부를 확인하세요.", evidence=[ref]))

    extraction = getattr(output, "extraction", None)
    facts = getattr(graph, "resolved_facts", {}) or {}
    # 원장 객체가 없는 구버전 결과도 확정값에 저장된 경고·출처를 잃지 않는다.
    # 원래 컨테이너를 수정하지 않도록 집합을 새로 만든다.
    flags_by_code = {code: set(flags) for code, flags in
                     (getattr(extraction, "confidence_flags", {}) or {}).items()}
    for code, fact in facts.items():
        if fact is not None:
            flags_by_code.setdefault(code, set()).update(fact.flags)
    flag_messages = {
        "period_inferred": ("실적 연도 확인", "원문에서 연도를 확인하지 못한 값을 보고 연도로 보충했습니다.", "원본 표의 연도 열을 확인하세요."),
        "partial_value": ("집계 범위 확인", "대표값이 전체 총량임을 확인하지 못했거나 일부 범위의 값입니다.", "전체·부분·국내·해외 범위를 구분하고 총량 근거를 확인하세요."),
        "no_representative_node": ("대표값 결정 불가", "후보 근거는 있으나 대표값 자격을 충족하지 못했습니다.", "연도·단위·항목·범위가 명확한 근거를 보완하세요."),
        "unit_mismatch": ("단위 확인", "항목 단위와 근거 단위를 일치시킬 수 없습니다.", "물리량과 단위를 확인하고 호환되는 근거를 제공하세요."),
        "scope_source_only": ("범위 미확정", "원문 사실은 확인했지만 요청 기간·사업장의 실적임은 확인하지 못했습니다(원문 범위로만 보존).", "원문 범위(월·기준일·사업장)를 확인하고 요청 범위의 근거를 보완하세요."),
    }
    flag_messages["unit_suspect"] = flag_messages["unit_mismatch"]
    flag_messages["partial_aggregate"] = flag_messages["partial_value"]
    mapped = getattr(extraction, "mapped", {}) or {}
    profile = getattr(extraction, "profile", None)
    ledger_codes = None
    if profile in PROFILES:
        # 프로필 밖이라도 실제 원장에 실린 추가 공시 항목은 확인 대상이다.
        ledger_codes = {item.code for item in items_for_profile(profile)} | set(mapped)
    elif mapped or getattr(extraction, "missing", None):
        ledger_codes = set(mapped) | set(getattr(extraction, "missing", []))
    for code, flags in flags_by_code.items():
        item = by_code(code)
        # 자유 목표·보조 항목은 실적 대표값에서 제외되는 것이 정상이다. 내부 flags는
        # 보존하되 원장 품질 경고로 오인하지 않으며, 추세 근거는 아래에서 따로 읽는다.
        if item is None or (ledger_codes is not None and code not in ledger_codes):
            continue
        entry = mapped.get(code, {})
        fact = facts.get(code)
        ids = fact.representative_node_ids if fact is not None else entry.get("representative_node_ids", [])
        refs = [_reference(nodes[n]) for n in ids if n in nodes]
        for flag in sorted(flags):
            if flag not in flag_messages:
                continue
            title, reason, action = flag_messages[flag]
            findings.append(_finding("data_quality", title, item.name,
                                     reason, action, code=code, check_reason=flag, evidence=refs))

    for code, fact in (getattr(graph, "resolved_facts", {}) or {}).items():
        item = by_code(code)
        if fact is None or item is None or fact.value is None or fact.unit != "건" or fact.value <= 0:
            continue
        if not any(word in item.name for word in ("위반", "침해")):
            continue
        if fact.period is None or "period_inferred" in fact.flags:
            continue
        if any(nodes[n].source_file in mock_sources for n in fact.representative_node_ids if n in nodes):
            continue  # 시연값은 입력 경고로만 알리고 실제 공시 사실로 재사용하지 않는다.
        refs = [_reference(nodes[n]) for n in fact.representative_node_ids if n in nodes]
        findings.append(_finding(
            "source_fact", "공시된 위반·침해 현황 확인",
            f"{fact.period}년 {item.name}: {fact.value:g} {fact.unit}",
            "위반·침해 건수가 공시되어 있습니다. 건수만으로 사건 경위나 시정 완료 여부는 알 수 없습니다.",
            "사건 내용·적용 범위·시정 조치와 재발 방지 근거를 확인하세요.", code=code, evidence=refs))

    findings.extend(_trend_reviews(node for node in nodes.values() if node.source_file not in mock_sources))

    for area, verification in getattr(output, "sections", {}).items():
        step = getattr(verification, "final", None)
        if step is None:
            continue
        grounding = getattr(step, "grounding", None)
        if grounding and grounding.decision != "ACCEPT":
            for sentence in grounding.g1_uncited_sentences:
                findings.append(_finding("generated_claim", "작성 문장의 출처 누락", sentence,
                    "작성 문장에 확인할 수 있는 근거 인용이 없습니다.", "근거를 연결하거나 확인할 수 없는 주장을 삭제하세요.", area=area))
            # G2와 G4는 사유가 다르므로 한 문구로 합치지 않는다(2026-09-21 실측 수정).
            # 종전에는 둘을 합쳐 전부 '일치하지 않습니다'로 단정했다. 그런데 G2는
            # '인용한 청크 안에서 그 숫자를 찾지 못했다'는 뜻이라 값이 맞는데도 불일치로
            # 보고됐다(실측: 산업 평균 11.0% 같은 비교 수치가 불일치로 표기됨).
            # 게이트 자신은 이미 두 사유를 구분해 표현한다(grounding_gate._rewrite_hint).
            for value in grounding.g2_orphan_numbers:
                findings.append(_finding("generated_claim", "인용 근거에 없는 숫자 확인", value,
                    "작성한 숫자를 인용한 근거 안에서 찾지 못했습니다. 숫자가 틀렸다는 판정은 아닙니다.",
                    "그 숫자의 출처를 인용에 연결하거나, 근거로 확인할 수 없으면 문장에서 빼세요.", area=area))
            for value in grounding.g4_unit_mismatches:
                findings.append(_finding("generated_claim", "작성 문장의 단위 확인", value,
                    "작성한 단위가 인용 근거의 단위와 다릅니다.",
                    "원본 단위와 대조해 환산하거나 문장을 수정하세요.", area=area))
            if not (grounding.g1_uncited_sentences or grounding.g2_orphan_numbers or grounding.g4_unit_mismatches):
                findings.append(_finding("generated_claim", "작성 문장의 근거 확인", "최종 문장이 근거 검증을 통과하지 못했습니다.",
                    "인용 근거에 비해 표현이 확대되었거나 의미를 충분히 뒷받침하지 못합니다.", "근거의 범위에 맞게 표현을 좁혀 확인하세요.", area=area))
        rv = getattr(getattr(step, "detection", None), "risk_vector", None)
        if rv:
            for claim in rv.numeric_evaluation.get("claims", []):
                if claim.get("reason") == "match" or claim.get("status") == "excluded":
                    continue
                refs = [_reference(nodes[n]) for n in claim.get("evidence_ids", []) if n in nodes]
                check_reason = claim.get("reason") or "unverified"
                claim_value = claim.get("claim_value")
                evidence_value = claim.get("evidence_value")
                claim_unit = claim.get("claim_unit") or "단위 미확인"
                evidence_unit = claim.get("evidence_unit") or "단위 미확인"
                reasons = {
                    "mismatch": (f"작성 수치 {claim_value} {claim_unit}와 대표 근거 {evidence_value} {evidence_unit}가 다릅니다.",
                                 "원본의 항목·연도·범위를 확인한 뒤 대표 근거와 일치하도록 문장을 수정하세요."),
                    "no_evidence": ("해당 지표에 비교 가능한 확정 근거가 없습니다.",
                                    "원본 수치와 출처를 보완하고 근거를 확보하기 전에는 값을 확정하지 마세요."),
                    "unit_mismatch": (f"작성 단위 {claim_unit}와 대표 근거 단위 {evidence_unit}를 비교할 수 없습니다.",
                                      "두 값의 물리량과 단위를 확인하고 호환되는 근거로 다시 검증하세요."),
                    "ambiguous_topic": ("작성한 숫자를 하나의 지표에 연결할 수 없습니다.",
                                        "수치와 해당 지표명을 같은 문장에 명확히 쓰고 여러 지표는 문장을 나누세요."),
                    "invalid_number": ("숫자 표기가 지원 범위를 벗어났거나 비교에 부적합합니다.",
                                       "원본 숫자의 부호·소수점·자릿수 구분을 확인하고 유효한 표기로 다시 검증하세요."),
                }
                reason, action = reasons.get(check_reason, (
                    "기존 수치 검사에서 비교를 완료하지 못했습니다.",
                    "검사 사유와 원본 항목·단위·연도를 확인하고 다시 검증하세요."))
                findings.append(_finding("generated_claim", "작성 수치 대조 확인", str(claim.get("raw", "")),
                    reason, action, check_reason=check_reason,
                    code=claim.get("code") or "", area=area, evidence=refs))
            findings.extend(_axis_reviews(rv, step, graph, area, mock_sources))

    for result in getattr(output, "item_retrievals", []):
        if result.get("accepted_chunk_ids"):
            continue
        code = result.get("item_code", "")
        item = by_code(code)
        findings.append(_finding("retrieval", "항목 설명 근거 확인", item.name if item else code,
            "항목별 추가 검색에서 검증 기준을 통과한 설명 근거를 확보하지 못했습니다. 미공시라는 확정 판정은 아닙니다.",
            "원본의 해당 항목·표·각주를 확인하거나 추가 증빙을 제공하세요.", code=code))

    # 검색 통과 자체를 위험으로 바꾸지 않는다. 이미 확인된 사항에 대해 추가로
    # 찾은 근거만 붙이며, 원문 인용과 추출 요약의 구분은 그대로 유지한다.
    # 붙인 자료가 확인된 내용을 직접 진술하는지(직접 근거), 항목 질의에 걸린 주변
    # 설명인지 구분해 표시한다. 정책·방침 문구가 실제 사건의 직접 증거로 읽히지
    # 않게 하려는 구분이며 근거 자체를 버리지 않는다. 새 판정을 만들지 않고 이미
    # 확정된 수치·연도가 그 원문에 그대로 적혀 있는지만 본다.
    for finding in findings:
        seen = {ref.node_id for ref in finding.evidence}
        for result in getattr(output, "item_retrievals", []):
            if not finding.code or result.get("item_code") != finding.code:
                continue
            accepted = set(result.get("accepted_chunk_ids", []))
            for hit in result.get("hits", []):
                node_id = hit.get("meta", {}).get("node_id")
                if hit.get("id") not in accepted or not node_id or node_id in seen:
                    continue
                node = nodes.get(node_id) or graph.text_nodes.get(node_id)
                if node is not None and node.source_file not in mock_sources:
                    finding.evidence.append(_reference(node, role=_evidence_role(finding, node)))
                    seen.add(node_id)
    # 같은 근거·사유를 여러 소비 경로가 보고해도 목록에서는 한 번만 표시한다.
    return list({finding.id: finding for finding in findings}.values())


def _axis_reviews(rv, step, graph, area: str, mock_sources: set[str]) -> list[ReviewFinding]:
    """이미 수행한 문장별 D2/D3/D5 검사만 설명하며 점수를 새로 계산하지 않는다."""
    records = rv.aggregate.get("sentence_axis_reviews")
    legacy = records is None
    if legacy:
        records = [{"sentence": "기존 결과에 저장된 대표 검사 결과", "high_axes": rv.high_axes(),
                    "axes": {name: getattr(rv, name).to_dict()
                             for name in ("D2_modifier", "D3_semantic", "D5_timeseries")}}]
    context = getattr(getattr(step, "generation", None), "context", None)
    # 과거 D3는 이 순서로 kesg_i를 임의 부여했다. 구버전 결과에만 그 대응을 복원한다.
    docs = ([doc for doc, _ in context.kesg_hits + context.corp_hits] if context else [])
    docs_by_id = {doc.chunk_id or str(doc.meta.get("id") or f"kesg_{i}"): doc
                  for i, doc in enumerate(docs)}
    legacy_docs = {f"kesg_{i}": doc for i, doc in enumerate(docs)}
    labels = {
        "D2_modifier": ("작성 문장의 모호·과장 표현 확인", "모호어·최상급 검사",
                        "표현의 대상·범위와 확인 가능한 근거를 구체적으로 적으세요."),
        "D3_semantic": ("작성 문장과 근거의 의미 대조", "의미 유사도 검사",
                        "연결된 근거가 문장의 대상·범위를 뒷받침하는지 확인하세요. 낮은 유사도만으로 사실 오류를 확정하지 않습니다."),
        "D5_timeseries": ("작성 문장의 증감 방향 확인", "시계열 방향 검사",
                          "원본의 같은 지표·기간·집계 범위와 문장의 증가·감소 표현을 대조하세요."),
    }
    findings = []
    for record in records:
        for name, axis in record.get("axes", {}).items():
            if name not in labels or not (name in record.get("high_axes", []) or axis.get("abstain")):
                continue
            title, label, action = labels[name]
            refs: list[ReviewEvidence] = []
            seen = set()
            evidence_ids = list(axis.get("evidence", []))
            if name == "D5_timeseries":
                evidence_ids += [edge.source_id for edge in graph.edges
                                 if edge.edge_type == "timeseries" and edge.target_id in evidence_ids]
            for evidence_id in evidence_ids:
                node = graph.nodes.get(evidence_id) or graph.text_nodes.get(evidence_id)
                doc = (legacy_docs if legacy and name == "D3_semantic" else docs_by_id).get(evidence_id)
                if node is None and doc is not None:
                    node_id = doc.meta.get("node_id")
                    node = graph.nodes.get(node_id) or graph.text_nodes.get(node_id)
                if node is not None:
                    ref = _reference(node)
                elif doc is not None:
                    ref = ReviewEvidence(
                        node_id=doc.chunk_id or evidence_id, source_file=doc.meta.get("source_file") or "",
                        page=doc.meta.get("page"), quote=doc.meta.get("quote") or "", extracted_text=doc.text)
                else:
                    continue  # 연결 불가 ID는 check_result에 남기고 원문 출처는 만들어내지 않는다.
                if ref.node_id not in seen and ref.source_file not in mock_sources:
                    refs.append(ref)
                    seen.add(ref.node_id)
            detail = axis.get("detail") or axis.get("abstain_reason") or "세부 사유가 저장되지 않았습니다."
            if axis.get("abstain"):
                reason = f"기존 {label}에서 판단을 보류했습니다. {detail}"
            else:
                reason = f"기존 {label}에서 확인 기준에 해당했습니다(점수 {axis.get('score', 0):g}). {detail}"
            if legacy:
                reason += " 문장별 기록이 없는 구버전 결과이므로 저장된 대표 벡터만 설명하며 대상 문장 위치를 특정할 수 없습니다."
            findings.append(_finding(
                "generated_claim", title, record.get("sentence", ""), reason, action,
                area=area, check_reason=name, check_result=axis, evidence=refs))
    return findings


def _trend_reviews(nodes) -> list[ReviewFinding]:
    groups: dict[tuple, dict[int, list[Any]]] = defaultdict(lambda: defaultdict(list))
    for node in nodes:
        hint = node.raw_text.split("=", 1)[0].strip()
        compact_hint = re.sub(r"\s+", "", hint)
        training = ("교육" in hint and any(word in hint for word in ("이수", "참여"))
                    and not any(word in compact_hint for word in ("미이수", "미참여", "불참"))
                    and node.unit == "%")
        injury = ("재해" in hint and node.unit == "건"
                  and not any(word in compact_hint for word in ("무재해", "예방", "교육", "조치", "점검", "훈련")))
        if not (training or injury) or node.period_inferred or is_derived_hint(hint) or "__projection" in node.metric:
            continue
        if node.value_role in {"target", "projection", "derived"}:
            continue
        if not node.source_file or not isinstance(node.period, int):
            continue  # 같은 자료의 확정 연도끼리인지 확인할 수 없다.
        # 과거 추출은 확정 연도를 hint/미매핑 metric 끝에도 붙였다. 노드의 실제
        # period와 같은 단독 꼬리만 비교 키에서 제거하고 지역·범위 및 원본은 보존한다.
        year_tail = rf"\s+{node.period}(?:년)?\s*$"
        hint = re.sub(year_tail, "", hint).strip()
        code = node.metric if by_code(node.metric) else re.sub(year_tail, "", node.metric).strip()
        # 범위가 다른 항목을 코드만으로 합치지 않는다. 같은 항목명도 같은 연도에
        # 다른 값이 있으면 비교를 보류한다. 산정 기준의 동일성은 별도 확인 사항이다.
        key = (node.source_file, code, hint, node.unit, node.value_role)
        groups[key][node.period].append(node)
    findings = []
    for (_, code, hint, unit, _), periods in groups.items():
        if len(periods) < 2:
            continue
        years = sorted(periods)[-2:]
        if any(len({n.value for n in periods[y]}) != 1 for y in years):
            continue
        before, after = (periods[y][0] for y in years)
        if not (after.value < before.value if unit == "%" else after.value > before.value):
            continue
        findings.append(_finding("source_fact", "연도별 수치 변화 확인",
            f"{hint}: {years[0]}년 {before.value:g}{unit} → {years[1]}년 {after.value:g}{unit}",
            "교육 이수·참여 비율이 낮아졌습니다." if unit == "%" else "보고된 재해 건수가 늘었습니다.",
            "집계 대상·산정 기준 변경 각주와 변화 원인을 확인하세요. 두 값만으로 성과 악화나 위반을 확정하지 않습니다.",
            code=code if by_code(code) else "", evidence=[_reference(before), _reference(after)]))
    return findings


def review_markdown(findings: list[ReviewFinding]) -> str:
    """UI와 내보내기가 같은 내용과 출처를 표시한다."""
    def plain(value):
        return re.sub(r"([\\`*_{}\[\]()#+.!|<>-])", r"\\\1", str(value)).replace("\n", " ")
    if not findings:
        return "현재 확보한 근거와 수행한 검사에서 추가 확인 사항이 기록되지 않았습니다."
    parts = ["원문 사실·자료 품질·작성 문장에서 확인할 사항입니다. 기존 그린워싱 점수와 구분하여 검토하세요."]
    for finding in findings:
        parts.extend([f"### {plain(finding.title)}", f"**확인된 내용:** {plain(finding.fact)}",
                      f"**판단 이유:** {plain(finding.reason)}", f"**확인·보완:** {plain(finding.action)}"])
        for ref in finding.evidence:
            location = f"{ref.page + 1}쪽" if isinstance(ref.page, int) and ref.page >= 0 else "페이지 미확인"
            # 직접 근거와 추가 검색이 붙인 주변 설명을 구분해 표시한다.
            label = "참고(주변 설명)" if ref.role == "context" else "출처"
            parts.append(f"**{label}:** {plain(ref.source_file or '파일 미확인')} · {location}")
            if ref.quote:
                parts.append(f"> {plain(ref.quote)}")
            elif ref.extracted_text:
                parts.append(f"추출 내용(원문 인용 미확인): {plain(ref.extracted_text)}")
    return "\n\n".join(parts)
