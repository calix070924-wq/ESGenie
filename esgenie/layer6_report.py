"""Layer 6 — 통합 보고서 조립 계층.

PipelineOutput에 흩어져 있는 분석 결과(커버리지·D6 선택적 공시·ISSB 갭·4축 리스크·
개선 로드맵·증빙)를 하나의 서술형 보고서로 엮는다.

설계 원칙
---------
- **하이브리드**: 대부분의 블록은 결정적(deterministic)으로 PipelineOutput 값을 그대로
  박아 환각을 0으로 만든다. 서술이 필요한 2개 블록(Executive Summary, 업종 벤치마크
  해설)만 LLM이 작성한다.
- **mock 누수 차단**: LLM 블록은 ``CLIENT.complete``를 호출하되 ``resp.used_mock``이
  True이면 결과를 버리고 모듈 자체 결정적 fallback 텍스트로 대체한다. (llm.py의 mock
  라우터가 엉뚱한 ESG 템플릿을 뱉어 보고서에 섞이는 것을 막는다.)
- **단일 소스**: 각 블록은 마크다운 문자열(body_md)만 보유한다. Streamlit 미리보기와
  PDF(exporters/report_pdf.py)가 동일한 마크다운에서 파생된다.

진입점: ``assemble_report(output) -> ReportDoc``
"""
from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from typing import Any

from .config import INDUSTRY_DIR
from .llm import CLIENT

AREA_LABELS = {"E": "환경", "S": "사회", "G": "지배구조"}


# ====================================================================
# 자료구조
# ====================================================================

@dataclass
class ReportBlock:
    id: str            # "cover" | "exec_summary" | "esg_E" | "benchmark" ...
    title: str         # 섹션 제목 ("" 이면 to_markdown에서 제목 줄 생략)
    body_md: str       # 마크다운 본문 (표 포함)
    kind: str          # "deterministic" | "llm" | "reused" — 출처 추적/디버깅용


@dataclass
class ReportDoc:
    corp_name: str
    industry: str
    report_year: int
    generated_at: str
    blocks: list[ReportBlock]
    meta: dict[str, Any] = field(default_factory=dict)

    def to_markdown(self) -> str:
        parts: list[str] = []
        for b in self.blocks:
            if b.title:
                parts.append(f"## {b.title}")
            parts.append(b.body_md.rstrip())
        return "\n\n".join(p for p in parts if p.strip()) + "\n"


# ====================================================================
# 마크다운 헬퍼
# ====================================================================

def _fmt(v: Any) -> str:
    if v is None or v == "":
        return "—"
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    """마크다운 pipe 표 생성. rows가 비면 빈 문자열."""
    if not rows:
        return ""
    head = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    body = "\n".join("| " + " | ".join(_fmt(c) for c in r) + " |" for r in rows)
    return f"{head}\n{sep}\n{body}"


# ====================================================================
# 업종 벤치마크 로더 (pipeline._load_industry_stats와 동일 로직, 순환 회피용 사본)
# ====================================================================

def _load_industry_stats(industry: str | None) -> dict[str, Any] | None:
    if not industry:
        return None
    try:
        for path in INDUSTRY_DIR.glob("*.json"):
            with open(path, encoding="utf-8") as fp:
                obj = json.load(fp)
            for b in obj.get("benchmarks", []):
                if b["industry"] == industry:
                    return b
    except Exception:
        return None
    return None


# ====================================================================
# 공통 추출 헬퍼
# ====================================================================

def _overall_risk(output: Any) -> tuple[float, str]:
    """영역별 위험도 중 최댓값과 그 밴드."""
    scores = [(v.final_score, v.final_band) for v in output.sections.values()]
    if not scores:
        return 0.0, "—"
    return max(scores, key=lambda x: x[0])


def _corp_meta(output: Any) -> tuple[str, str, int]:
    rep = output.report
    if rep is not None:
        return rep.corp_name, rep.industry, rep.report_year
    ext = output.extraction
    name = ext.corp_name if ext is not None else "—"
    return name, "", 0


# ====================================================================
# 블록 빌더 — 결정적
# ====================================================================

def _block_cover(output: Any) -> ReportBlock:
    name, industry, year = _corp_meta(output)
    risk, band = _overall_risk(output)
    ext = output.extraction
    cov = f"{ext.coverage_pct:.1f}%" if ext is not None else "—"
    profile = ext.profile_label if ext is not None else "—"
    d6 = output.disclosure
    issb = output.issb_gap

    rows = [
        ["대상 기업", name],
        ["업종", industry or "—"],
        ["보고 연도", f"{year}년" if year else "—"],
        ["적용 프로파일", profile],
        ["K-ESG 커버리지", cov],
        ["종합 그린워싱 위험도", f"{risk:.1f} ({band})"],
    ]
    if d6 is not None:
        rows.append(["선택적 공시 의심도", f"{d6.score:.2f} ({d6.level})"])
    if issb is not None:
        rows.append(["ISSB 프로파일 내 공시", f"{issb.in_profile_disclosed}/{issb.in_profile_total} (누락 {issb.in_profile_missing})"])
    if output.industry_module_key:
        rows.append(["적용 업종 모듈", output.industry_module_key])

    body = (
        f"# {name} ESG 공시 신뢰성 보고서\n\n"
        f"_생성일: {output_generated_at(output)}_\n\n"
        + _md_table(["항목", "내용"], rows)
    )
    return ReportBlock(id="cover", title="", body_md=body, kind="deterministic")


def output_generated_at(output: Any) -> str:
    return getattr(output, "_generated_at", datetime.date.today().isoformat())


def _block_esg(output: Any, area: str) -> ReportBlock | None:
    verify = output.sections.get(area)
    if verify is None:
        return None
    label = AREA_LABELS.get(area, area)
    rv = verify.final.detection.risk_vector
    axis_note = ""
    if rv is not None:
        axis_note = (
            f" · 4축(D1/D2/D3/D5): "
            f"{rv.D1_numeric.score*100:.0f}/{rv.D2_modifier.score*100:.0f}/"
            f"{rv.D3_semantic.score*100:.0f}/{rv.D5_timeseries.score*100:.0f}"
        )
    lead = (
        f"> **{label} ({area})** · 위험도 {verify.final_score:.1f} ({verify.final_band}) "
        f"· 검증 {verify.iterations_used}회"
        + (" · ⚠ 사람 검토 필요(HITL)" if verify.hitl_required else "")
        + axis_note
    )
    body = f"{lead}\n\n{verify.final_text.strip()}"
    return ReportBlock(id=f"esg_{area}", title="", body_md=body, kind="reused")


def _block_issb(output: Any) -> ReportBlock | None:
    issb = output.issb_gap
    if issb is None or not issb.rows:
        return None
    in_rows = [r for r in issb.rows if r.scope == "in_profile"]
    table_rows = [
        [
            r.kesg_code,
            r.name,
            ", ".join(r.standards) or "—",
            {"disclosed": "공시됨", "missing": "누락", "out_of_scope": "범위 외"}.get(r.status, r.status),
            {"verified": "증빙연결", "self_reported": "자기기재", "missing": "누락", "out_of_scope": "범위 외"}.get(r.evidence_status, r.evidence_status),
        ]
        for r in in_rows
    ]
    missing_names = [r.name for r in in_rows if r.status == "missing"]
    lead = (
        f"프로파일 대상 ISSB/KSSB 연계 항목 {issb.in_profile_total}건 중 "
        f"{issb.in_profile_disclosed}건이 공시되었고 {issb.in_profile_missing}건이 누락되었다."
    )
    if missing_names:
        lead += " 누락 항목: " + ", ".join(missing_names) + "."
    body = lead + "\n\n" + _md_table(
        ["K-ESG", "항목", "ISSB 기준", "공시 상태", "증빙 상태"], table_rows)
    return ReportBlock(id="issb", title="ISSB/KSSB 갭 분석", body_md=body, kind="deterministic")


def _block_disclosure(output: Any) -> ReportBlock | None:
    d6 = output.disclosure
    if d6 is None:
        return None
    lead = f"선택적 공시(cherry-picking) 의심도는 **{d6.score:.2f} ({d6.level})**이다."
    if d6.rationale:
        lead += f" {d6.rationale}"
    parts = [lead]

    if d6.orphan_ratios:
        rows = [[o.ratio_code, o.ratio_name, ", ".join(o.missing_context), o.detail] for o in d6.orphan_ratios]
        parts.append("**고아 비율(분모 없는 유리 지표):**")
        parts.append(_md_table(["비율 코드", "지표", "누락된 맥락", "상세"], rows))

    if d6.omitted_sensitive:
        rows = [[o.code, o.name, o.area, f"{o.sensitivity:.1f}", o.reason] for o in d6.omitted_sensitive]
        parts.append("**민감 항목 누락:**")
        parts.append(_md_table(["코드", "항목", "영역", "민감도", "사유"], rows))

    if not d6.orphan_ratios and not d6.omitted_sensitive:
        parts.append("탐지된 고아 비율·민감 항목 누락 신호는 없다.")

    body = "\n\n".join(p for p in parts if p.strip())
    return ReportBlock(id="disclosure", title="선택적 공시(D6) 점검", body_md=body, kind="deterministic")


def _block_risk(output: Any) -> ReportBlock | None:
    rows = output.risk_rows
    if not rows:
        return None
    headers = ["K-ESG 코드", "값", "D1 수치", "D2 수식어", "D3 의미", "D5 시계열", "종합 위험도"]
    table_rows = [[r.get(h, "—") for h in headers] for r in rows]
    body = (
        "증빙(L0 노드)에 연결된 정량 항목별 4축 그린워싱 위험 분해다. "
        "D1=수치 정확성, D2=과장 수식어, D3=의미 괴리, D5=시계열 모순.\n\n"
        + _md_table(headers, table_rows)
    )
    return ReportBlock(id="risk", title="항목별 4축 리스크", body_md=body, kind="deterministic")


def _block_roadmap(output: Any) -> ReportBlock | None:
    drafts = output.policy_drafts or {}
    if not drafts:
        return None
    parts = ["검증에서 미흡·누락으로 판정된 정책 항목과 자동 생성된 보완 초안이다."]
    for code, draft in drafts.items():
        parts.append(f"### {code}\n\n{draft.strip()}")
    body = "\n\n".join(parts)
    return ReportBlock(id="roadmap", title="개선 로드맵 (정책 보완 초안)", body_md=body, kind="deterministic")


def _block_evidence(output: Any) -> ReportBlock:
    g = output.evidence_graph
    rows = [
        ["Evidence 노드", len(getattr(g, "nodes", []))],
        ["텍스트 노드", len(getattr(g, "text_nodes", []))],
        ["엣지", len(getattr(g, "edges", []))],
    ]
    parts = [
        "본 보고서의 모든 정량 주장은 아래 증빙 그래프에 연결되어 있으며, "
        "영역별 감사 추적(audit trace) JSON으로 수치-증빙 연결을 검증할 수 있다.",
        _md_table(["항목", "수"], rows),
    ]
    if output.trace_paths:
        parts.append("**감사 추적 파일:**")
        for area, path in output.trace_paths.items():
            import os
            parts.append(f"- [{area}] `{os.path.basename(path)}`")
    body = "\n\n".join(parts)
    return ReportBlock(id="evidence", title="증빙 및 감사 추적", body_md=body, kind="deterministic")


# ====================================================================
# 블록 빌더 — LLM (mock 시 결정적 fallback)
# ====================================================================

def _llm_or_fallback(system: str, user: str, fallback: str) -> str:
    """LLM 호출. used_mock이면 결과를 버리고 fallback 반환."""
    try:
        resp = CLIENT.complete(system, user, mock_hint="generate", temperature=0.3)
        if resp.used_mock or not resp.content.strip():
            return fallback
        return resp.content.strip()
    except Exception:
        return fallback


def _block_exec_summary(output: Any) -> ReportBlock:
    name, industry, year = _corp_meta(output)
    risk, band = _overall_risk(output)
    ext = output.extraction
    cov = ext.coverage_pct if ext is not None else 0.0
    d6 = output.disclosure
    issb = output.issb_gap

    facts = {
        "기업": name,
        "업종": industry,
        "연도": year,
        "K-ESG_커버리지_pct": round(cov, 1),
        "종합_위험도": round(risk, 1),
        "위험_밴드": band,
        "선택적공시_의심도": round(d6.score, 2) if d6 else None,
        "선택적공시_수준": d6.level if d6 else None,
        "ISSB_누락": issb.in_profile_missing if issb else None,
        "영역별_위험도": {a: round(v.final_score, 1) for a, v in output.sections.items()},
    }

    fallback = (
        f"{name}의 K-ESG 공시 커버리지는 {cov:.1f}%이며, 종합 그린워싱 위험도는 "
        f"{risk:.1f}({band}) 수준이다. "
        + (f"선택적 공시 의심도는 {d6.score:.2f}({d6.level})로 평가되었다. " if d6 else "")
        + (f"ISSB/KSSB 연계 항목 중 {issb.in_profile_missing}건이 누락되었다. " if issb else "")
        + "정량 근거가 확보된 영역은 신뢰도가 높으나, 누락·고아 비율 항목은 추가 증빙 보완이 필요하다."
    )

    system = (
        "당신은 ESG 공시 신뢰성 평가 보고서의 Executive Summary를 쓰는 전문가다. "
        "주어진 수치만 사용하고, 표·제목·불릿 없이 5~7문장의 서술형 한 단락으로만 작성하라. "
        "과장 수식어를 피하고 경영진이 한눈에 읽을 수 있게 요약하라."
    )
    user = (
        "다음 분석 결과를 바탕으로 Executive Summary를 작성하라.\n"
        + json.dumps(facts, ensure_ascii=False)
    )
    body = _llm_or_fallback(system, user, fallback)
    return ReportBlock(id="exec_summary", title="Executive Summary", body_md=body, kind="llm")


def _block_benchmark(output: Any) -> ReportBlock | None:
    name, industry, year = _corp_meta(output)
    stats = _load_industry_stats(industry)
    rep = output.report
    if stats is None or rep is None:
        return None

    metrics = stats.get("metrics", {})
    # 산업 평균 지표명 → 자사 대응 코드 추정은 단순화: 산업 metrics 키를 그대로 비교 표기
    rows = []
    for k, v in metrics.items():
        rows.append([k, _fmt(v)])
    issues = "; ".join(stats.get("key_issues", []))

    table = _md_table(["산업 평균 지표", "값"], rows)

    fallback = (
        f"{industry} 업종의 산업 평균 지표와 핵심 이슈를 기준으로 볼 때, {name}의 공시는 "
        f"업계 공통 관심사({issues})를 중심으로 점검될 필요가 있다. 자사 수치는 본 보고서의 "
        f"각 영역 핵심 지표 표를 참조하라."
    )
    system = (
        "당신은 ESG 업종 벤치마크 해설을 쓰는 애널리스트다. 주어진 산업 평균 지표와 자사 "
        "데이터를 비교해 표·제목 없이 3~5문장의 서술형 한 단락으로만 작성하라. 데이터에 없는 "
        "수치를 지어내지 말 것."
    )
    user = (
        f"회사: {name} ({industry}, {year}년)\n"
        f"산업 평균 지표(JSON): {json.dumps(metrics, ensure_ascii=False)}\n"
        f"산업 핵심 이슈: {issues}\n"
        f"자사 K-ESG 데이터(JSON): {json.dumps(rep.kesg_data, ensure_ascii=False)}\n\n"
        "위 자사 수치와 산업 평균을 비교해 해설하라."
    )
    narrative = _llm_or_fallback(system, user, fallback)
    body = narrative + ("\n\n" + table if table else "")
    return ReportBlock(id="benchmark", title="업종 벤치마크 비교", body_md=body, kind="llm")


# ====================================================================
# 진입점
# ====================================================================

def assemble_report(output: Any) -> ReportDoc:
    """PipelineOutput → 통합 ReportDoc.

    블록 순서: 표지 → Exec Summary → E/S/G 본문 → 벤치마크 → ISSB 갭 →
    선택적 공시 → 4축 리스크 → 개선 로드맵 → 증빙 부록.
    데이터가 없는 블록(None 반환)은 자동 생략된다.
    """
    name, industry, year = _corp_meta(output)
    setattr(output, "_generated_at", datetime.date.today().isoformat())

    candidates: list[ReportBlock | None] = [_block_cover(output), _block_exec_summary(output)]
    for area in output.requested_areas or list(output.sections.keys()):
        candidates.append(_block_esg(output, area))
    candidates += [
        _block_benchmark(output),
        _block_issb(output),
        _block_disclosure(output),
        _block_risk(output),
        _block_roadmap(output),
        _block_evidence(output),
    ]
    blocks = [b for b in candidates if b is not None]

    risk, band = _overall_risk(output)
    meta = {
        "coverage_pct": output.extraction.coverage_pct if output.extraction else None,
        "overall_risk": risk,
        "overall_band": band,
        "d6_score": output.disclosure.score if output.disclosure else None,
        "issb_missing": output.issb_gap.in_profile_missing if output.issb_gap else None,
        "llm_blocks": [b.id for b in blocks if b.kind == "llm"],
    }
    return ReportDoc(
        corp_name=name,
        industry=industry,
        report_year=year,
        generated_at=output_generated_at(output),
        blocks=blocks,
        meta=meta,
    )
