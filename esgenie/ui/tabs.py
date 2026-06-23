"""Streamlit tab renderers for ESGenie."""
from __future__ import annotations

import html
import json
import os
import re
from collections import Counter
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from esgenie.benchmark import format_report as bench_format
from esgenie.benchmark import load_benchmark, run_benchmark
from esgenie.config import SETTINGS
from esgenie.issb_gap import (
    EVIDENCE_LABELS,
    SCOPE_LABELS,
    STATUS_LABELS,
    remediation_text_for,
    rows_for_anchor,
)
from esgenie.knowledge.issb_mapping import PILLAR_LABELS, mappings_for
from esgenie.ssot.ssot_pipeline import ssot_summary
from esgenie.supplychain import (
    all_framework_keys,
    copy_evidence_pack,
    get_framework,
    respond_from_pipeline,
)
from esgenie.supplychain.exporters import (
    export_response_sheet,
    export_response_sheet_pdf,
)
from esgenie.ui.components import (
    callout_html,
    download_tile_html,
    panel_html,
    render_empty_state,
    render_section_header,
    render_stat_row,
)
from esgenie.ui.theme import PLOTLY_TEMPLATE

_DET_LABELS = {"rule": "룰 단독", "hybrid": "하이브리드 (룰+LLM)", "llm_only": "LLM 단독"}


def _issb_badge_text(code: str) -> str:
    badges: list[str] = []
    seen: set[tuple[str, str]] = set()
    for mapping in mappings_for(code):
        pair = (mapping.standard, mapping.pillar)
        if pair in seen:
            continue
        seen.add(pair)
        badges.append(f"ISSB {mapping.standard} · {PILLAR_LABELS[mapping.pillar]}")
    return " / ".join(badges)


def _item_name_with_issb(name: str, code: str) -> str:
    badge = _issb_badge_text(code)
    return name if not badge else f"{name} [{badge}]"


def _issb_gap_table_rows(gap_report, anchor: str | None = None) -> list[dict[str, str | int]]:
    rows = gap_report.rows if anchor is None else rows_for_anchor(gap_report, anchor)
    return [
        {
            "K-ESG": row.kesg_code,
            "항목명": row.name,
            "ISSB": " / ".join(f"ISSB {standard}" for standard in row.standards),
            "기둥": " / ".join(row.pillar_labels),
            "상태": STATUS_LABELS[row.status],
            "증빙": EVIDENCE_LABELS[row.evidence_status],
            "범위": SCOPE_LABELS[row.scope],
            "근거": " / ".join(row.requirements),
            "증빙수": row.evidence_count,
        }
        for row in rows
    ]


def _supplychain_issb_alert_rows(gap_report) -> list[dict[str, str]]:
    if gap_report is None:
        return []
    rows = []
    for row in gap_report.rows:
        if row.scope != "in_profile" or row.status != "missing":
            continue
        if not any(anchor in ("climate", "greenwash_defense") for anchor in row.anchors):
            continue
        rows.append({
            "K-ESG": row.kesg_code,
            "항목명": row.name,
            "ISSB": " / ".join(f"ISSB {standard}" for standard in row.standards),
            "기둥": " / ".join(row.pillar_labels),
            "보완 필요": " / ".join(row.requirements),
            "권장 증빙": remediation_text_for(row.kesg_code) or "관련 산정표·원천 증빙",
        })
    return rows


def _report_card(text: str, kind: str = "draft", tag_label: str | None = None) -> str:
    cls = "esg-report-card final" if kind == "final" else "esg-report-card"
    tag = (
        f'<span class="esg-report-tag {kind}">{html.escape(tag_label)}</span>'
        if tag_label else ""
    )
    return f'<div class="{cls}">{tag}\n\n{text}\n\n</div>'


def _apply_plotly_theme(fig: go.Figure, **layout_updates) -> go.Figure:
    fig.update_layout(**PLOTLY_TEMPLATE, **layout_updates)
    return fig


def _final_preview(text: str, limit: int = 420) -> str:
    preview = re.sub(r"^#{1,6}\s*", "", text[:limit], flags=re.MULTILINE).strip()
    if len(text) > limit:
        preview += "..."
    return preview


def _result_status_meta(result, active_area: str) -> list[dict[str, str]]:
    if result is None:
        return []
    verify = result.sections.get(active_area)
    extraction = getattr(result, "extraction", None)
    v15_trace = getattr(result, "v15_trace", None)
    summary = ssot_summary(result.evidence_graph) if getattr(result, "evidence_graph", None) else {}
    rows: list[dict[str, str]] = []
    if extraction is not None:
        rows.append({
            "label": "Coverage",
            "value": f"{extraction.coverage_pct:.1f}%",
            "note": extraction.profile_label,
        })
    if summary:
        rows.append({
            "label": "SSOT Nodes",
            "value": str(summary["total_nodes"]),
            "note": f"DART {summary['by_origin'].get('dart', 0)} · OCR {summary['by_origin'].get('ocr_structured', 0) + summary['by_origin'].get('ocr_unstructured', 0)}",
        })
    if v15_trace is not None:
        rows.append({
            "label": "Verified Ratio",
            "value": f"{v15_trace.summary['verified_ratio']*100:.0f}%",
            "note": f"정량 {v15_trace.summary['data_point_count']}건",
        })
        rows.append({
            "label": "Policy Pass",
            "value": f"{v15_trace.summary['policy_pass']}/{v15_trace.summary['policy_total']}",
            "note": "사내 규정 점검",
        })
    if verify is not None:
        rows.append({
            "label": "Risk Score",
            "value": f"{verify.final_score:.1f}",
            "note": f"{verify.final_band} · 검증 {verify.iterations_used}회",
        })
        rows.append({
            "label": "HITL",
            "value": "필요" if verify.hitl_required else "완료",
            "note": "문장 단위 수동 검토",
        })
    return rows


def _draft_vs_final_panel(result, active_area: str) -> None:
    verify = getattr(result, "sections", {}).get(active_area)
    if verify is None:
        render_empty_state("보고서 초안이 아직 없습니다.", "분석을 시작하면 초안과 최종본 비교가 생성됩니다.")
        return

    first_step = verify.steps[0]
    left, right = st.columns(2)
    with left:
        st.markdown(panel_html("초안", "L0 SSOT + L2 RAG 기반 초기 생성본입니다."), unsafe_allow_html=True)
        st.markdown(_report_card(first_step.generation.text, "draft", "DRAFT"), unsafe_allow_html=True)
    with right:
        delta = first_step.detection.risk_score - verify.final_score
        delta_text = f"위험도 {delta:.1f} 감소" if delta > 0 else "초안이 이미 기준치 이하"
        st.markdown(panel_html("최종본", "검증과 재생성을 거친 제출 직전 버전입니다.", compact_note=delta_text), unsafe_allow_html=True)
        st.markdown(_report_card(verify.final_text, "final", "FINAL"), unsafe_allow_html=True)

    with st.expander("📚 RAG 검색 근거", expanded=False):
        context = first_step.generation.context
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("**K-ESG 가이드라인**")
            for doc, score in context.kesg_hits:
                st.caption(f"[{score:.3f}] {doc.text[:80]}...")
        with c2:
            st.markdown("**업종 벤치마크**")
            for doc, score in context.industry_hits:
                st.caption(f"[{score:.3f}] {doc.text[:80]}...")
        with c3:
            st.markdown("**자사 DART 원문 + OCR 증빙**")
            for doc, score in context.corp_hits:
                st.caption(f"[{score:.3f}] {doc.text[:80]}...")


_AREA_META = {
    "E": ("🌿", "환경", "#63d674"),
    "S": ("🤝", "사회", "#5aa9e6"),
    "G": ("🏛", "지배구조", "#c79bff"),
}


def _esg_coverage_rows(result, profile: str) -> list[dict[str, object]]:
    """프로파일 기준 E/S/G 영역별 추적 항목 수와 커버리지를 계산한다.

    분석 전(result 또는 extraction 없음)에는 covered=0, analyzed=False로
    추적 대상 항목 수(breadth)만 채워 반환한다. 분석 후에는 extraction.mapped
    기준으로 영역별 공시 항목 수(covered)를 계산하고, 실제 심층분석된 영역은
    analyzed=True로 표시한다.
    """
    from esgenie.knowledge.kesg_items import items_for_profile

    try:
        profile_items = items_for_profile(profile)
    except ValueError:
        return []

    area_codes: dict[str, list[str]] = {"E": [], "S": [], "G": []}
    for item in profile_items:
        if item.area in area_codes:
            area_codes[item.area].append(item.code)

    extraction = getattr(result, "extraction", None)
    mapped_codes: set[str] = set()
    if extraction is not None:
        mapped_codes = {
            code
            for code, entry in extraction.mapped.items()
            if not entry.get("beyond_profile")
        }
    analyzed_areas = set(getattr(result, "sections", {}).keys()) if result is not None else set()
    has_extraction = extraction is not None

    rows: list[dict[str, object]] = []
    for area in ("E", "S", "G"):
        codes = area_codes.get(area, [])
        total = len(codes)
        covered = len(set(codes) & mapped_codes)
        rows.append({
            "area": area,
            "total": total,
            "covered": covered,
            "analyzed": area in analyzed_areas,
            "has_extraction": has_extraction,
        })
    return rows


def _render_esg_coverage_strip(result, active_area: str, profile: str) -> None:
    from esgenie.knowledge.kesg_items import PROFILE_LABELS

    rows = _esg_coverage_rows(result, profile)
    if not rows:
        return

    has_extraction = bool(rows[0]["has_extraction"])
    label = PROFILE_LABELS.get(profile, profile)
    note = (
        "한 영역을 깊게 분석하고, 나머지 영역도 동일 엔진으로 동일하게 확장됩니다."
        if not has_extraction
        else "심층분석한 영역은 강조 표시됩니다. 나머지 영역은 동일 파이프라인으로 확장 가능합니다."
    )
    st.markdown(
        panel_html("E·S·G 커버리지", note, compact_note=f"프로파일: {label}"),
        unsafe_allow_html=True,
    )

    cols = st.columns(3)
    for col, row in zip(cols, rows):
        area = str(row["area"])
        total = int(row["total"])
        covered = int(row["covered"])
        emoji, name, color = _AREA_META[area]
        is_active = bool(row["analyzed"]) and area == active_area
        pct = (covered / total * 100) if total else 0.0
        value_line = f"{covered}/{total} 항목" if has_extraction else f"{total} 항목"
        sub_line = "공시 확인" if has_extraction else "추적 대상"
        active_tag = (
            "<span style='font-size:11px;font-weight:800;color:#0c0c0c;background:"
            f"{color};border-radius:8px;padding:2px 8px;margin-left:6px'>심층분석</span>"
            if is_active else ""
        )
        border = f"1px solid {color}" if is_active else "1px solid rgba(255,255,255,0.10)"
        glow = f"box-shadow:0 0 0 1px {color}55;" if is_active else ""
        bar = (
            "<div style='height:8px;border-radius:6px;background:rgba(255,255,255,0.08);"
            "overflow:hidden;margin-top:10px'>"
            f"<div style='height:100%;width:{pct:.0f}%;background:{color}'></div></div>"
            if has_extraction else ""
        )
        with col:
            st.markdown(
                f"<div style='border-radius:16px;padding:14px 16px;border:{border};{glow}"
                "background:rgba(255,255,255,0.03)'>"
                f"<div style='font-size:13px;font-weight:800;opacity:.9'>{emoji} {name} ({area}){active_tag}</div>"
                f"<div style='font-size:24px;font-weight:900;margin-top:6px'>{value_line}</div>"
                f"<div style='font-size:12px;opacity:.7'>{sub_line}</div>"
                f"{bar}</div>",
                unsafe_allow_html=True,
            )


def render_overview_workspace(
    result,
    active_area: str,
    *,
    uploaded_names: list[str] | None = None,
    profile: str = "sme",
) -> None:
    render_section_header(
        "Overview",
        "핵심 결과, 품질 상태, 다음 액션을 한 화면에서 요약합니다.",
        kicker="Mission Control",
    )

    _render_esg_coverage_strip(result, active_area, profile)

    if result is None:
        render_empty_state("분석 결과가 아직 없습니다.", "회사와 증빙을 설정한 뒤 상단에서 분석을 시작하세요.")
        return

    verify = result.sections.get(active_area)
    extraction = getattr(result, "extraction", None)
    render_stat_row(_result_status_meta(result, active_area), columns=3)

    actions: list[str] = []
    if verify is not None and verify.hitl_required:
        actions.append("문장 단위 HITL 판정이 남아 있습니다. Audit Trail에서 검토를 마무리하세요.")
    if extraction is not None and extraction.missing:
        actions.append(f"K-ESG 누락 항목 {len(extraction.missing)}건이 있습니다. 진단 화면에서 우선순위를 확인하세요.")
    if getattr(result, "policy_drafts", None):
        actions.append(f"사내 규정 보완 초안 {len(result.policy_drafts)}건이 준비되었습니다.")
    if uploaded_names:
        actions.append(f"업로드 증빙 {len(uploaded_names)}건이 결과에 반영되었습니다.")
    else:
        actions.append("전기요금, 폐기물, 규정집 증빙을 업로드하면 검증 신뢰도가 크게 좋아집니다.")

    left, right = st.columns([1.7, 1.0])
    with left:
        preview_text = verify.final_text if verify is not None else "분석 결과가 없습니다."
        st.markdown(
            panel_html(
                "Executive Summary",
                "대회 시연에서는 이 카드가 가장 먼저 보이므로, 분석 결과와 제품 메시지가 한 번에 읽히도록 구성합니다.",
                compact_note=f"활성 영역: {active_area}",
            ),
            unsafe_allow_html=True,
        )
        st.markdown(
            _report_card(_final_preview(preview_text, 720), "final", "FINAL SNAPSHOT"),
            unsafe_allow_html=True,
        )

    with right:
        st.markdown(callout_html("Next Actions", actions, tone="info"), unsafe_allow_html=True)
        export_paths = getattr(result, "export_paths", {}) or {}
        deliverables = []
        if export_paths.get("xlsx"):
            deliverables.append(f"데이터시트 준비됨: {os.path.basename(export_paths['xlsx'])}")
        if export_paths.get("audit_json"):
            deliverables.append(f"감사 추적 준비됨: {os.path.basename(export_paths['audit_json'])}")
        if verify is not None:
            deliverables.append(f"최종 위험도 {verify.final_score:.1f} / {verify.final_band}")
        st.markdown(
            callout_html("Delivery Pack", deliverables or ["산출물은 분석 완료 후 이곳에 표시됩니다."], tone="success"),
            unsafe_allow_html=True,
        )


def render_analysis_workspace(result, active_area: str, gradient: str) -> None:
    render_section_header(
        "Analysis",
        "공시 진단, 초안 생성, 검증 결과를 하나의 분석 흐름으로 묶었습니다.",
        kicker="Core Workflow",
    )

    if result is None or getattr(result, "sections", {}).get(active_area) is None:
        render_empty_state("분석 데이터가 아직 없습니다.", "분석을 실행하면 진단, 초안, 검증 흐름이 이 탭에 채워집니다.")
        return

    verify = result.sections[active_area]
    delta = verify.steps[0].detection.risk_score - verify.final_score
    cards = [
        {"label": "초안 위험도", "value": f"{verify.steps[0].detection.risk_score:.1f}", "note": "첫 생성본 기준"},
        {"label": "최종 위험도", "value": f"{verify.final_score:.1f}", "note": verify.final_band},
        {"label": "위험도 개선", "value": f"{max(delta, 0):.1f}", "note": "재생성 감소폭"},
        {"label": "검증 반복", "value": f"{verify.iterations_used}회", "note": "임계치 수렴 루프"},
    ]
    render_stat_row(cards, columns=4)

    tab_diag, tab_compare, tab_verify = st.tabs(["📊 공시 진단", "🪄 초안 vs 최종", "✅ 리스크 & 검증"])
    with tab_diag:
        render_diag_tab(result, gradient, show_header=False)
    with tab_compare:
        _draft_vs_final_panel(result, active_area)
    with tab_verify:
        render_verify_tab(result, active_area, gradient, show_header=False)


def render_evidence_workspace(
    result,
    active_area: str,
    gradient: str,
    *,
    uploaded_names: list[str] | None = None,
) -> None:
    render_section_header(
        "Evidence",
        "증빙 업로드 상태부터 SSOT, 규정 검증, 감사 추적까지 근거 축으로 묶었습니다.",
        kicker="Source of Truth",
    )

    if result is None:
        render_empty_state("근거 데이터가 아직 없습니다.", "증빙을 업로드하고 분석을 시작하면 이 공간이 채워집니다.")
        return

    paths = getattr(result, "export_paths", {}) or {}
    summary_cards = [
        {"label": "업로드 파일", "value": str(len(uploaded_names or [])), "note": "현재 세션 기준"},
        {"label": "증빙 서류철", "value": "준비됨" if paths.get("evidence_dir") else "대기", "note": os.path.basename(paths.get("evidence_dir", "")) or "분석 후 생성"},
    ]
    render_stat_row(summary_cards, columns=2)

    tab_ssot, tab_policy, tab_audit = st.tabs(["🗂 업로드 & SSOT", "📋 규정 검증", "🔍 Audit Trail"])
    with tab_ssot:
        if uploaded_names:
            st.markdown(
                callout_html("Uploaded Evidence", [f"세션에 업로드된 파일 {len(uploaded_names)}건", *uploaded_names[:6]], tone="success"),
                unsafe_allow_html=True,
            )
        render_ssot_tab(result, gradient, show_header=False)
    with tab_policy:
        render_policy_tab(result, gradient, show_header=False)
    with tab_audit:
        render_audit_tab(result, active_area, gradient, show_header=False, show_downloads=False)


def _get_assembled_report(result):
    """통합 보고서(ReportDoc)와 PDF 경로를 세션 캐시로 1회만 생성한다.

    섹션 구성/기업명이 같으면 재실행 시 재생성하지 않는다(LLM 호출 절약).
    PDF 생성 실패 시 pdf_path=None으로 두고 MD는 그대로 제공한다.
    """
    from esgenie.layer6_report import assemble_report

    sig = (
        tuple(sorted(result.sections.keys())),
        getattr(getattr(result, "report", None), "corp_name", ""),
        len(getattr(result, "risk_rows", []) or []),
    )
    cached = st.session_state.get("_assembled_report")
    if cached and cached[0] == sig:
        return cached[1], cached[2]

    doc = assemble_report(result)
    pdf_path = None
    try:
        from esgenie.exporters.report_pdf import export_report_pdf
        pdf_path = export_report_pdf(doc, os.path.join("outputs", "report_preview"))
    except Exception:
        pdf_path = None
    st.session_state["_assembled_report"] = (sig, doc, pdf_path)
    return doc, pdf_path


def render_deliverables_workspace(result, active_area: str, gradient: str) -> None:
    render_section_header(
        "Deliverables",
        "최종 제출본, 데이터시트, 감사 추적, 공급망 응답서를 한 곳에 모았습니다.",
        kicker="Delivery Pack",
    )

    if result is None or getattr(result, "sections", {}).get(active_area) is None:
        render_empty_state("산출물이 아직 없습니다.", "분석이 끝나면 다운로드 가능한 제출 패키지가 이 탭에 나타납니다.")
        return

    verify = result.sections[active_area]
    export_paths = getattr(result, "export_paths", {}) or {}

    doc, pdf_path = _get_assembled_report(result)

    d1, d2, d3 = st.columns(3)
    with d1:
        st.markdown(
            download_tile_html(
                "통합 보고서",
                "E·S·G 본문에 커버리지·선택적 공시·ISSB 갭·리스크·개선 로드맵을 엮은 제출본입니다.",
                note=f"섹션 {len(doc.blocks)}개 · PDF/MD",
            ),
            unsafe_allow_html=True,
        )
        if pdf_path and os.path.exists(pdf_path):
            with open(pdf_path, "rb") as fh:
                st.download_button(
                    "📥 통합 보고서 (.pdf)",
                    fh.read(),
                    file_name=os.path.basename(pdf_path),
                    mime="application/pdf",
                    use_container_width=True,
                )
        st.download_button(
            "📥 통합 보고서 (.md)",
            doc.to_markdown().encode(),
            file_name=f"esgenie_report_{doc.corp_name}.md",
            mime="text/markdown",
            use_container_width=True,
        )
    with d2:
        st.markdown(
            download_tile_html(
                "K-ESG 데이터시트",
                "대기업 제출용 엑셀 데이터시트와 증빙 연결 결과입니다.",
                note=os.path.basename(export_paths.get("xlsx", "")) or "분석 후 생성",
            ),
            unsafe_allow_html=True,
        )
        if export_paths.get("xlsx"):
            with open(export_paths["xlsx"], "rb") as fh:
                st.download_button(
                    "📥 데이터시트 (.xlsx)",
                    fh.read(),
                    file_name=os.path.basename(export_paths["xlsx"]),
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
    with d3:
        st.markdown(
            download_tile_html(
                "감사 추적",
                "수치와 증빙 연결을 검토할 수 있는 JSON 아티팩트입니다.",
                note=os.path.basename(export_paths.get("audit_json", "")) or "분석 후 생성",
            ),
            unsafe_allow_html=True,
        )
        if export_paths.get("audit_json"):
            with open(export_paths["audit_json"], "rb") as fh:
                st.download_button(
                    "📥 감사 추적 (.json)",
                    fh.read(),
                    file_name=os.path.basename(export_paths["audit_json"]),
                    mime="application/json",
                    use_container_width=True,
                )

    report_tab, supply_tab = st.tabs(["📝 통합 보고서", "📤 공급망 실사 응답서"])
    with report_tab:
        st.markdown(
            panel_html(
                "Integrated Report",
                "결정적 분석 블록(커버리지·D6·ISSB·리스크·로드맵)과 LLM 서술(요약·벤치마크)을 "
                "하나로 엮은 통합 보고서입니다.",
                compact_note=f"종합 위험도 {doc.meta.get('overall_risk', 0):.1f} · 섹션 {len(doc.blocks)}개",
            ),
            unsafe_allow_html=True,
        )
        st.markdown(_report_card(doc.to_markdown(), "final", "FINAL"), unsafe_allow_html=True)
    with supply_tab:
        render_supplychain_tab(result, gradient, show_header=False)


def render_lab_workspace(gradient: str) -> None:
    render_section_header(
        "Lab",
        "벤치마크와 내부 실험 기능은 메인 시연 흐름과 분리해 정리했습니다.",
        kicker="Experiment",
    )
    render_benchmark_tab(gradient, show_header=False)


def render_home_tab(result, active_area: str, gradient: str, *, show_header: bool = True) -> None:
    if show_header:
        render_section_header("ESGenie Overview", "핵심 지표와 최종 보고서 하이라이트를 먼저 확인합니다.", kicker="Overview")
        if gradient:
            st.markdown(gradient, unsafe_allow_html=True)

    if result is None:
        st.info("👈 사이드바에서 회사를 선택하고 **분석 시작**을 눌러주세요.")
        return

    verify = result.sections.get(active_area)
    summary = ssot_summary(result.evidence_graph)
    v15_trace = result.v15_trace

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("SSOT 노드", summary["total_nodes"])
    c2.metric("정량 항목", v15_trace.summary["data_point_count"])
    c3.metric("증빙 확인률", f"{v15_trace.summary['verified_ratio']*100:.0f}%")
    c4.metric("규정 통과", f"{v15_trace.summary['policy_pass']}/{v15_trace.summary['policy_total']}")
    if verify:
        band_emoji = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🟠", "CRITICAL": "🔴"}.get(verify.final_band, "")
        c5.metric("그린워싱 위험도", f"{verify.final_score:.1f}", f"{band_emoji} {verify.final_band}", delta_color="off")

    if verify:
        st.markdown("#### 보고서 미리보기")
        preview = re.sub(r"^#{1,6}\s*", "", verify.final_text[:300], flags=re.MULTILINE).strip()
        if len(verify.final_text) > 300:
            preview += "..."
        with st.container(border=True):
            st.write(preview)


def render_ssot_tab(result, gradient: str, *, show_header: bool = True) -> None:
    if show_header:
        render_section_header("Evidence & SSOT", "업로드된 증빙과 단일 진실 원천을 추적합니다.", kicker="Evidence")
        if gradient:
            st.markdown(gradient, unsafe_allow_html=True)

    if result is None:
        st.info("분석을 시작하세요.")
        return

    ssot = result.evidence_graph
    summary = ssot_summary(ssot)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("DART 노드", summary["by_origin"].get("dart", 0))
    c2.metric("OCR 정형", summary["by_origin"].get("ocr_structured", 0))
    c3.metric("OCR 비정형", summary["by_origin"].get("ocr_unstructured", 0))
    c4.metric("정성 조항", summary["text_nodes"])
    st.caption(f"시계열 엣지 {summary['edges']}개 · 교차검증 엣지 {summary['cross_check_edges']}개")

    node_rows = [
        {
            "노드 ID": node.id,
            "K-ESG": node.metric,
            "값": f"{node.value} {node.unit}",
            "연도": node.period,
            "출처": node.origin,
            "증빙 파일": node.source_file or "—",
            "신뢰도": round(node.confidence, 2),
        }
        for node in ssot.nodes.values()
    ]
    if node_rows:
        st.dataframe(node_rows, use_container_width=True, hide_index=True)

    if ssot.text_nodes:
        st.markdown("#### 정성 조항 노드 (규정집·회의록)")
        text_rows = [
            {
                "K-ESG": node.kesg_code or "—",
                "섹션": node.section,
                "내용": node.text[:60] + ("…" if len(node.text) > 60 else ""),
                "파일": node.source_file,
                "페이지": node.page,
            }
            for node in ssot.text_nodes.values()
        ]
        st.dataframe(text_rows, use_container_width=True, hide_index=True)


def render_diag_tab(result, gradient: str, *, show_header: bool = True) -> None:
    if show_header:
        render_section_header("Disclosure Diagnosis", "K-ESG 커버리지, ISSB 갭, 선택적 공시 리스크를 봅니다.", kicker="Diagnosis")
        if gradient:
            st.markdown(gradient, unsafe_allow_html=True)

    if result is None or result.extraction is None:
        st.info("DART 연동 후 분석을 시작하면 K-ESG 커버리지를 확인할 수 있습니다.")
        return

    extraction = result.extraction
    ssot = result.evidence_graph

    badge = "🏢" if extraction.profile == "full" else "🏭"
    extra = f" · 프로파일 외 추가 공시 {len(extraction.beyond_profile)}개" if extraction.beyond_profile else ""
    st.markdown(f"{badge} **프로파일: {extraction.profile_label}** · 커버리지 **{extraction.coverage_pct:.1f}%**{extra}")
    st.caption("커버리지 분모는 프로파일 기준 — 해당 기업 규모에 적용 가능한 항목만 평가")

    _render_disclosure_panel(result.disclosure)
    _render_issb_gap_panel(result.issb_gap)
    _render_coverage_panel(extraction)

    st.markdown("#### Evidence 노드")
    node_df = pd.DataFrame([
        {
            "K-ESG": node.metric,
            "값": node.value,
            "단위": node.unit,
            "연도": node.period,
            "출처": node.origin,
            "신뢰도": round(node.confidence, 2),
        }
        for node in sorted(ssot.nodes.values(), key=lambda item: item.metric)
    ])
    st.dataframe(node_df, hide_index=True, use_container_width=True)

    from esgenie.knowledge.kesg_items import items_for_profile

    profile_items = items_for_profile(extraction.profile)
    profile_codes = {item.code for item in profile_items}
    missing_codes = [code for code in extraction.missing if code in profile_codes]
    present_codes = [
        code for code in extraction.mapped
        if code in profile_codes and not extraction.mapped[code].get("beyond_profile")
    ]

    present_tab, missing_tab = st.tabs([
        f"✅ 공시 항목 ({len(present_codes)}개 / {len(profile_items)}개)",
        f"⚠️ 누락 항목 ({len(missing_codes)}개 / {len(profile_items)}개)",
    ])

    with present_tab:
        st.dataframe(pd.DataFrame([
            {
                "코드": entry["code"],
                "영역": entry["area"],
                "항목명": _item_name_with_issb(entry["name"], entry["code"]),
                "값": entry["value"],
                "단위": entry.get("unit") or "-",
                "출처": _source_tag(entry),
            }
            for entry in extraction.mapped.values()
            if entry["code"] in profile_codes and not entry.get("beyond_profile")
        ]), hide_index=True, use_container_width=True)

    with missing_tab:
        st.dataframe(pd.DataFrame([
            {
                "코드": item.code,
                "영역": item.area,
                "항목명": _item_name_with_issb(item.name, item.code),
                "유형": item.data_type,
            }
            for item in profile_items if item.code in extraction.missing
        ]), hide_index=True, use_container_width=True)

    if extraction.beyond_profile:
        with st.expander(f"➕ 프로파일 외 추가 공시 ({len(extraction.beyond_profile)}개) — 커버리지 미반영"):
            st.dataframe(pd.DataFrame([
                {
                    "코드": entry["code"],
                    "영역": entry["area"],
                    "항목명": _item_name_with_issb(entry["name"], entry["code"]),
                    "값": entry["value"],
                    "단위": entry.get("unit") or "-",
                }
                for entry in extraction.mapped.values() if entry.get("beyond_profile")
            ]), hide_index=True, use_container_width=True)


def render_draft_tab(result, active_area: str, gradient: str, *, show_header: bool = True) -> None:
    if show_header:
        render_section_header("Draft Generation", "RAG 기반 초안 생성 결과와 검색 근거를 확인합니다.", kicker="Generation")
        if gradient:
            st.markdown(gradient, unsafe_allow_html=True)

    if result is None or result.sections.get(active_area) is None:
        st.info("DART 연동 후 분석을 시작하면 RAG 기반 보고서 초안이 생성됩니다.")
        return

    verify = result.sections[active_area]
    first_step = verify.steps[0]
    area_label = {"E": "🌿 환경", "S": "🤝 사회", "G": "🏛 지배구조"}[active_area]

    st.info("L0 SSOT + L2 RAG 기반 초안. 그린워싱 시연 모드에서는 의도적 과장이 포함됩니다.")
    st.markdown(f"#### {area_label} 영역 초안")
    st.markdown(_report_card(first_step.generation.text, "draft", "DRAFT"), unsafe_allow_html=True)
    if first_step.generation.used_mock_llm:
        st.caption("⚠️ Mock LLM으로 생성 (OPENAI_API_KEY 설정 시 실 API 사용)")

    with st.expander("📚 RAG 검색 근거"):
        context = first_step.generation.context
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("**K-ESG 가이드라인**")
            for doc, score in context.kesg_hits:
                st.caption(f"[{score:.3f}] {doc.text[:80]}...")
        with c2:
            st.markdown("**업종 벤치마크**")
            for doc, score in context.industry_hits:
                st.caption(f"[{score:.3f}] {doc.text[:80]}...")
        with c3:
            st.markdown("**자사 DART 원문 + OCR 증빙**")
            for doc, score in context.corp_hits:
                st.caption(f"[{score:.3f}] {doc.text[:80]}...")


def render_verify_tab(result, active_area: str, gradient: str, *, show_header: bool = True) -> None:
    if show_header:
        render_section_header("Verification & Final", "리스크 감소 추이와 최종 제출본 품질을 확인합니다.", kicker="Verification")
        if gradient:
            st.markdown(gradient, unsafe_allow_html=True)

    if result is None or result.sections.get(active_area) is None:
        st.info("DART 연동 후 분석을 시작하면 검증 결과를 확인할 수 있습니다.")
        return

    verify = result.sections[active_area]
    band_emoji = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🟠", "CRITICAL": "🔴"}

    if len(verify.steps) > 1:
        before = verify.steps[0].detection.risk_score
        after = verify.final.detection.risk_score
        if after < before:
            st.success(f"✅ L4 재생성으로 위험도 {before:.1f} → {after:.1f} 감소")
        else:
            st.info("ℹ️ 초안이 이미 기준치 이하")
    else:
        st.info("ℹ️ 초안이 이미 기준치 이하")

    st.markdown("### 최종 보고서")
    st.markdown(_report_card(verify.final_text, "final", "FINAL"), unsafe_allow_html=True)
    st.caption(
        f"{band_emoji.get(verify.final_band, '')} 위험도 **{verify.final_score:.1f}** ({verify.final_band}) · "
        f"검증 {verify.iterations_used}회 · "
        + ("수렴 완료 ✅" if verify.converged else "HITL 필요 ⚠️" if verify.hitl_required else "미수렴 ⚠️")
    )

    final_risk = verify.final.detection.risk_vector
    if final_risk is not None:
        axes = ["D1 수치오차", "D2 모호어", "D3 의미괴리", "D5 시계열모순"]
        scores = [
            final_risk.D1_numeric.score * 100,
            final_risk.D2_modifier.score * 100,
            final_risk.D3_semantic.score * 100,
            final_risk.D5_timeseries.score * 100,
        ]
        fig = go.Figure()
        fig.add_trace(go.Scatterpolar(
            r=scores + scores[:1],
            theta=axes + axes[:1],
            fill="toself",
            line_color="#FF6B6B",
            fillcolor="rgba(255,107,107,0.2)",
        ))
        _apply_plotly_theme(fig,
            polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
            showlegend=False,
            height=300,
            margin=dict(t=30, b=20),
            title=f"4축 위험 분해 (종합 {final_risk.risk_score*100:.1f})",
        )
        st.plotly_chart(fig, use_container_width=True, key="radar_final")

    if len(verify.steps) > 1:
        progress_df = pd.DataFrame([
            {
                "반복": "초안" if step.iteration == 0 else f"{step.iteration}차 재생성",
                "위험도": step.detection.risk_score,
            }
            for step in verify.steps
        ])
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=progress_df["반복"],
            y=progress_df["위험도"],
            mode="lines+markers",
            line=dict(color="#FF6B6B", width=2),
        ))
        fig.add_hline(
            y=verify.metadata["threshold"],
            line_dash="dash",
            line_color="#4CAF50",
            annotation_text=f"목표 임계치 ({verify.metadata['threshold']})",
        )
        _apply_plotly_theme(fig, height=220, yaxis=dict(title="위험도", range=[0, 105]), margin=dict(t=10, b=10))
        st.plotly_chart(fig, use_container_width=True, key="progress")

    if result.risk_rows:
        st.markdown("#### K-ESG 항목별 4축 리스크 (증빙 기반)")
        df = pd.DataFrame(result.risk_rows)

        def _color(val):
            if isinstance(val, float):
                if val >= 0.7:
                    return "background-color:#FFC7CE"
                if val >= 0.4:
                    return "background-color:#FFEB9C"
            return ""

        st.dataframe(df.style.map(_color, subset=["종합 위험도"]), use_container_width=True, hide_index=True)


def render_policy_tab(result, gradient: str, *, show_header: bool = True) -> None:
    if show_header:
        render_section_header("Policy Audit", "사내 규정 필수 조항 충족 여부를 확인합니다.", kicker="Policy")
        if gradient:
            st.markdown(gradient, unsafe_allow_html=True)
    st.caption("K-ESG 가이드라인 + 중대재해처벌법·개인정보보호법·공정거래법 기준")

    if result is None:
        st.info("분석을 시작하세요.")
        return

    for policy_audit in result.v15_trace.policy_audit:
        code = policy_audit["kesg_code"]
        badge = "✅ 통과" if policy_audit["passed"] else "⚠️ 보완 필요"
        passed_count = sum(1 for finding in policy_audit["findings"] if finding["status"] == "met")
        with st.expander(f"**{code}** — {badge}  ({passed_count}/{len(policy_audit['findings'])}개 충족)"):
            for finding in policy_audit["findings"]:
                icon = {"met": "✅", "insufficient": "⚠️", "missing": "❌"}.get(finding["status"], "—")
                st.markdown(f"{icon} **{finding['requirement']}**")
                if finding["status"] != "met":
                    st.markdown(f"  - 갭: {finding['gap_comment']}")
                    st.markdown(f"  - 보완: {finding['suggested_fix']}")
            if policy_audit["source_files"]:
                st.caption(f"검토 파일: {', '.join(policy_audit['source_files'])}")

    if result.policy_drafts:
        st.markdown("#### 누락 조항 표준 조문 초안 (LLM 자동 생성)")
        for code, draft in result.policy_drafts.items():
            with st.expander(f"{code} — 보완 초안"):
                st.code(draft, language="markdown")


def render_audit_tab(
    result,
    active_area: str,
    gradient: str,
    *,
    show_header: bool = True,
    show_downloads: bool = True,
) -> None:
    if show_header:
        render_section_header("Audit Trail & HITL", "주장부터 증빙까지의 추적과 수동 검토 포인트를 확인합니다.", kicker="Trace")
        if gradient:
            st.markdown(gradient, unsafe_allow_html=True)

    if result is None:
        st.info("분석을 시작하세요.")
        return

    sentence_trace = result.audit_traces.get(active_area)
    paths = result.export_paths

    if show_downloads:
        dl1, dl2, dl3 = st.columns(3)
        with dl1:
            with open(paths["xlsx"], "rb") as fh:
                st.download_button(
                    "📥 K-ESG 데이터시트 (.xlsx)",
                    fh.read(),
                    file_name="ESG_DataSheet_대기업제출용.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
        with dl2:
            with open(paths["audit_json"], "rb") as fh:
                st.download_button(
                    "📥 감사 추적 (.json)",
                    fh.read(),
                    file_name="audit_trace.json",
                    mime="application/json",
                )
        if sentence_trace:
            with dl3:
                trace_json = json.dumps(sentence_trace.to_dict(), ensure_ascii=False, indent=2)
                st.download_button(
                    "📥 문장 단위 추적 (.json)",
                    trace_json.encode(),
                    file_name=f"audit_trace_{sentence_trace.ticker}_{sentence_trace.area}.json",
                    mime="application/json",
                )

    st.info(f"📁 증빙 서류철: `{paths['evidence_dir']}`")

    _render_provenance_panel(result)

    if sentence_trace:
        _render_hitl_panel(sentence_trace)


# ====================================================================
# 공급망 실사 응답서
# ====================================================================

def _fmt_answer_value(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) if value else "—"
    return str(value)


def _fmt_answer_evidence(answer) -> str:
    parts: list[str] = []
    for e in answer.evidence_links:
        loc = f" p.{e.page + 1}" if e.page is not None else ""
        if e.bbox:
            loc += " 📍"
        parts.append(f"{e.file_name}{loc}".strip())
    return " / ".join(parts) or "—"


def _extract_upload_recommendations(answer) -> list[str]:
    recs: list[str] = []
    for flag in getattr(answer, "flags", []) or []:
        if flag.startswith("보완 증빙: "):
            payload = flag.removeprefix("보완 증빙: ").strip()
            recs.extend(part.strip() for part in payload.split(" / ") if part.strip())
    seen: set[str] = set()
    out: list[str] = []
    for rec in recs:
        if rec not in seen:
            seen.add(rec)
            out.append(rec)
    return out


def _supplychain_upload_cta_rows(sheet, uploaded_names: list[str] | None = None) -> list[dict[str, str | int]]:
    """제출 전 체크리스트 행 — 증빙대기/작성필요/검토필요를 한데 모은다(STEP 4).

    derive가 채운 evidence_needed/rationale/flags를 checklist로 환원한 뒤,
    flagged 항목엔 기존 ISSB 보완 권장(권장 증빙)도 덧붙인다.
    """
    from esgenie.supplychain.checklist import build_checklist

    rows: list[dict[str, str | int]] = []
    for item in build_checklist(sheet):
        upload_doc = " / ".join(item.evidence_needed) if item.evidence_needed else "—"
        if item.status == "flagged":
            answer = next((a for a in sheet.answers if a.qid == item.qid), None)
            recs = _extract_upload_recommendations(answer) if answer is not None else []
            if recs:
                upload_doc = " / ".join(recs)
        rows.append({
            "문항": item.question_text,
            "할 일": item.action,
            "올릴 문서 / 작성 사항": upload_doc,
            "안내": item.request,
        })
    return rows


def _answer_option_label(answer) -> str:
    return f"{answer.badge} · {answer.question_text}"


def _answer_primary_code(question_map: dict[str, Any], answer) -> str:
    question = question_map.get(answer.qid)
    return question.primary_code if question is not None else ""


def _data_point_by_code(result) -> dict[str, Any]:
    v15 = getattr(result, "v15_trace", None)
    if v15 is None:
        return {}
    return {dp.kesg_code: dp for dp in getattr(v15, "data_points", []) or []}


def _text_evidence_rows(result, answer) -> list[dict[str, Any]]:
    text_nodes = getattr(getattr(result, "evidence_graph", None), "text_nodes", {}) or {}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for evidence in getattr(answer, "evidence_links", []) or []:
        node_id = getattr(evidence, "node_id", "") or ""
        if node_id in seen:
            continue
        seen.add(node_id)
        node = text_nodes.get(node_id)
        if node is None:
            continue
        rows.append({
            "섹션": node.section or "—",
            "파일": node.source_file or getattr(evidence, "file_name", "—"),
            "페이지": (node.page + 1) if node.page is not None else "—",
            "내용": node.text,
        })
    return rows


def _render_supplychain_evidence_preview(evidence, *, evidence_dir: str = "") -> None:
    from esgenie.provenance import bbox_to_pct

    if evidence is None:
        st.info("연결된 원본 증빙이 없습니다.")
        return

    file_name = getattr(evidence, "file_name", "") or "—"
    page = (getattr(evidence, "page", 0) or 0)
    bbox = getattr(evidence, "bbox", None)
    st.caption(f"원본 증빙: {file_name}" + (f" · p.{page + 1}" if getattr(evidence, "page", None) is not None else ""))

    pdf_path = os.path.join(evidence_dir, file_name) if evidence_dir and file_name else ""
    rendered = False
    if bbox and file_name.lower().endswith(".pdf") and pdf_path and os.path.exists(pdf_path):
        try:
            from esgenie.pdf_render import render_page_with_box

            png = render_page_with_box(pdf_path, bbox, page=page, dpi=120)
            st.image(png, caption=f"{file_name} · p.{page + 1}", use_container_width=True)
            rendered = True
        except Exception as exc:  # noqa: BLE001
            st.caption(f"원본 렌더 실패: {exc}")

    if rendered:
        return

    box = bbox_to_pct(bbox)
    if box:
        st.markdown(
            f"<div style='position:relative;width:100%;height:130px;"
            f"background:#f4f4f2;border:1px solid #ccc;border-radius:6px'>"
            f"<div style='position:absolute;left:{box['left']}%;top:{box['top']}%;"
            f"width:{box['width']}%;height:{box['height']}%;"
            f"background:rgba(255,193,7,0.3);border:2px solid #BA7517;border-radius:3px'></div></div>"
            f"<div style='font-size:12px;color:#999'>원본 PDF 미첨부 — bbox 위치 비율만 표시</div>",
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        "<div style='width:100%;height:130px;background:#f4f4f2;"
        "border:1px dashed #bbb;border-radius:6px;display:flex;"
        "align-items:center;justify-content:center;color:#999;font-size:13px'>"
        "좌표 미연결 — 정성 조항 또는 bbox 없는 증빙입니다.</div>",
        unsafe_allow_html=True,
    )


def _render_supplychain_answer_detail(result, answer, *, question_map: dict[str, Any]) -> None:
    from esgenie.provenance import primary_evidence, verification_view

    code = _answer_primary_code(question_map, answer)
    data_point = _data_point_by_code(result).get(code)
    text_rows = _text_evidence_rows(result, answer)
    evidence = primary_evidence(getattr(answer, "evidence_links", []) or [])
    evidence_dir = (getattr(result, "export_paths", {}) or {}).get("evidence_dir", "")

    st.markdown("#### 선택 문항 상세 근거")
    left, right = st.columns([2, 3])
    with left:
        st.markdown(f"**문항**: {answer.question_text}")
        st.markdown(f"**답변**: {_fmt_answer_value(answer.value)}")
        st.markdown(f"**신뢰**: {answer.badge}")
        if data_point is not None:
            view = verification_view(getattr(data_point, "verification", ""))
            st.caption(f"{view['label']} · D1 위험 {float(getattr(data_point, 'd1_risk', 0.0) or 0.0):.2f}")
        if answer.flags:
            st.markdown("**검토 포인트**")
            for flag in answer.flags:
                st.markdown(f"- {flag}")
        if answer.rationale:
            st.markdown("**판정 근거**")
            st.write(answer.rationale)
        if answer.evidence_links:
            st.markdown("**연결된 증빙**")
            for evidence_link in answer.evidence_links:
                label = evidence_link.file_name
                if evidence_link.page is not None:
                    label += f" · p.{evidence_link.page + 1}"
                if getattr(evidence_link, "bbox", None):
                    label += " · bbox"
                st.markdown(f"- {label}")

    with right:
        st.markdown("**원본 위치 미리보기**")
        _render_supplychain_evidence_preview(evidence, evidence_dir=evidence_dir)

        if text_rows:
            st.markdown("**정성 조항 근거**")
            for row in text_rows:
                st.markdown(f"- `{row['섹션']}` · {row['파일']} · p.{row['페이지']}")
                st.caption(row["내용"])


def render_supplychain_tab(result, gradient: str, *, show_header: bool = True) -> None:
    if show_header:
        render_section_header("Supply Chain Response", "OEM 제출용 실사 응답서를 증빙 근거와 함께 자동 생성합니다.", kicker="Deliverable")
        if gradient:
            st.markdown(gradient, unsafe_allow_html=True)
    st.caption("증빙·공시·그린워싱 검증 결과를 대기업(OEM) ESG 자가진단 양식으로 자동 응답합니다. "
               "각 답변에 원본 증빙과 신뢰 배지가 함께 실립니다.")

    if result is None or getattr(result, "v15_trace", None) is None:
        st.info("분석을 시작하면 협력사 실사 응답서가 자동 생성됩니다.")
        return

    keys = all_framework_keys()
    sel = st.selectbox(
        "제출 양식 선택",
        keys,
        format_func=lambda k: get_framework(k).label,
        help="OEM/산업별 양식. 같은 증빙으로 여러 양식에 동시 대응됩니다.",
    )
    framework = get_framework(sel)

    supplier_claims = getattr(result, "supplier_claims", None) or {}
    supplier_claim_files = getattr(result, "supplier_claim_files", None) or []
    sheet = respond_from_pipeline(result, framework, supplier_claims=supplier_claims)
    question_map = {question.qid: question for question in framework.questions}

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("자동응답", f"{sheet.auto_pct:.0f}%")
    c2.metric("작성필요 (✍️)", f"{sheet.hitl_pct:.0f}%")
    c3.metric("증빙대기 (❗)", f"{sheet.pending_pct:.0f}%")
    c4.metric("검토 필요 (🚩)", f"{sheet.flagged_count}건")
    st.caption(
        f"문항 {len(sheet.answers)}개 (분모 {sheet.denominator}개, 해당없음 제외) · "
        "자동응답=기계가 답 채움 / 작성필요=사람 서술 / 증빙대기=증빙 업로드 시 자동화"
    )

    if supplier_claims:
        joined = ", ".join(sorted(supplier_claim_files)) if supplier_claim_files else "업로드 SAQ"
        st.caption(f"협력사 자가주장 {len(supplier_claims)}건 연동됨: {joined}")

    issb_alert_rows = _supplychain_issb_alert_rows(getattr(result, "issb_gap", None))
    if issb_alert_rows:
        with st.expander(f"🛡 ISSB 방어 관점 보완 항목 ({len(issb_alert_rows)}건)", expanded=bool(sheet.flagged_count)):
            st.caption("실사 응답서 제출 전 보완이 필요한 ISSB 기후/그린워싱 방어 항목입니다.")
            st.dataframe(pd.DataFrame(issb_alert_rows), hide_index=True, use_container_width=True)

    upload_cta_rows = _supplychain_upload_cta_rows(
        sheet,
        uploaded_names=sorted(st.session_state.get("upload_paths", {}).keys()),
    )
    if upload_cta_rows:
        with st.expander(f"📋 제출 전 증빙 체크리스트 ({len(upload_cta_rows)}건)", expanded=bool(sheet.flagged_count)):
            st.caption("증빙 업로드=문서 올리면 자동 해소 / 담당자 작성=사람이 서술 / 검토·보완=경고 소명")
            st.dataframe(pd.DataFrame(upload_cta_rows), hide_index=True, use_container_width=True)

    st.markdown("#### 자동 응답")
    rows = [
        {
            "신뢰": a.badge,
            "섹션": a.section,
            "문항": a.question_text,
            "답변": _fmt_answer_value(a.value),
            "근거": _fmt_answer_evidence(a),
        }
        for a in sheet.answers
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    detail_answers = [a for a in sheet.answers if a.evidence_links or a.flags or a.rationale]
    if detail_answers:
        labels = [_answer_option_label(answer) for answer in detail_answers]
        selected_label = st.selectbox(
            "문항별 상세 근거",
            labels,
            index=0,
            help="공급망 실사 탭 안에서 바로 플래그 이유, 정성 조항, bbox 위치를 확인합니다.",
        )
        selected_answer = detail_answers[labels.index(selected_label)]
        _render_supplychain_answer_detail(result, selected_answer, question_map=question_map)

    flagged = [a for a in sheet.answers if a.status == "flagged"]
    if flagged:
        st.markdown("#### 🚩 제출 전 검토 필요")
        for a in flagged:
            st.error(f"**{a.question_text}** — " + "; ".join(a.flags))

    if sheet.gaps:
        with st.expander(f"📋 보완·검토 항목 ({len(sheet.gaps)}건)", expanded=bool(flagged)):
            for g in sheet.gaps:
                st.markdown(f"- {g}")

    out_dir = os.path.join("outputs", "_supplychain", sheet.framework_key)
    xlsx_path = export_response_sheet(sheet, out_dir)
    dl_xlsx, dl_pdf = st.columns(2)
    with open(xlsx_path, "rb") as fh:
        dl_xlsx.download_button(
            "📥 실사 응답서 (.xlsx)",
            fh.read(),
            file_name=os.path.basename(xlsx_path),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    try:
        # 응답서가 참조하는 증빙 원본을 out_dir/evidence_pack 로 복사 → 부록 임베드 가능.
        try:
            copy_evidence_pack(sheet, out_dir, st.session_state.get("upload_paths", {}))
        except Exception:  # noqa: BLE001 — 복사 실패해도 부록만 스킵, 본문은 정상
            pass
        # evidence_base_dir=out_dir → out_dir/evidence_pack 의 원본이 있으면 증빙 부록에
        # 원본 페이지+bbox를 임베드. (원본 미복사 시 부록은 자동 스킵 — 본문은 정상)
        pdf_path = export_response_sheet_pdf(sheet, out_dir, evidence_base_dir=out_dir)
        with open(pdf_path, "rb") as fh:
            dl_pdf.download_button(
                "📄 실사 응답서 (.pdf)",
                fh.read(),
                file_name=os.path.basename(pdf_path),
                mime="application/pdf",
            )
    except Exception as exc:  # noqa: BLE001  — PDF 실패해도 xlsx 경로는 유지
        dl_pdf.caption(f"PDF 생성 불가: {exc}")


def render_benchmark_tab(gradient: str, *, show_header: bool = True) -> None:
    if show_header:
        render_section_header("Benchmark Lab", "룰, 하이브리드, LLM 단독 검출기의 성능을 비교합니다.", kicker="Lab")
        if gradient:
            st.markdown(gradient, unsafe_allow_html=True)

    try:
        benchmark_data = load_benchmark()
        cases = benchmark_data["cases"]
    except Exception as exc:  # noqa: BLE001
        st.error(f"벤치마크 데이터셋 로드 실패: {exc}")
        cases = []

    if not cases:
        return

    categories = Counter(case["category"] for case in cases)
    greenwash_count = sum(1 for case in cases if case["label"] == "greenwash")
    b1, b2, b3 = st.columns(3)
    b1.metric("라벨링 문장", len(cases))
    b2.metric("그린워싱 / 정상", f"{greenwash_count} / {len(cases) - greenwash_count}")
    b3.metric("카테고리", len(categories))
    st.caption(" · ".join(f"{k} {v}" for k, v in sorted(categories.items())))

    if SETTINGS.use_mock_llm:
        st.warning("⚠ LLM 키 미설정 — mock 판정으로 실행됩니다. 결과는 아키텍처 데모용이며 성능 주장에는 실키 결과를 사용하세요.")

    if st.button("▶ 벤치마크 실행 (룰 vs 하이브리드 vs LLM 단독)", type="primary"):
        with st.spinner("50문장 × 3검출기 평가 중…"):
            st.session_state.bench_reports = run_benchmark(["rule", "hybrid", "llm_only"])

    reports = st.session_state.get("bench_reports")
    if not reports:
        return

    st.markdown("#### 종합 지표")
    metric_rows = []
    for name, report in reports.items():
        metrics = report.metrics()
        metric_rows.append({
            "검출기": _DET_LABELS.get(name, name),
            "Precision": metrics["precision"],
            "Recall": metrics["recall"],
            "F1": metrics["f1"],
            "Accuracy": metrics["accuracy"],
            "LLM 호출": metrics["llm_calls"],
        })
    st.dataframe(pd.DataFrame(metric_rows), hide_index=True, use_container_width=True)

    fig = go.Figure()
    for metric_name in ("precision", "recall", "f1"):
        fig.add_bar(
            name=metric_name.capitalize(),
            x=[_DET_LABELS.get(name, name) for name in reports],
            y=[report.metrics()[metric_name] for report in reports.values()],
        )
    _apply_plotly_theme(fig,
        barmode="group",
        height=320,
        yaxis=dict(range=[0, 1.05], title="점수"),
        margin=dict(t=30, b=20),
        title="검출기별 Precision / Recall / F1",
    )
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("#### 카테고리별 정답률")
    category_names = sorted({case.category for report in reports.values() for case in report.cases})
    category_rows = []
    for category in category_names:
        row = {"카테고리": category}
        for name, report in reports.items():
            breakdown = report.by_category().get(category, {})
            row[_DET_LABELS.get(name, name)] = f"{breakdown.get('correct', 0)}/{breakdown.get('total', 0)}"
        category_rows.append(row)
    st.dataframe(pd.DataFrame(category_rows), hide_index=True, use_container_width=True)
    st.caption("backed_modifier(근거 수반 수식어)·future_plan(미래 계획)이 룰 단독의 구조적 오탐 영역 — 하이브리드가 LLM 맥락 판정으로 해소")

    st.markdown("#### 오답 상세")
    for name, report in reports.items():
        wrong = [case for case in report.cases if not case.correct]
        with st.expander(f"{_DET_LABELS.get(name, name)} — 오답 {len(wrong)}건"):
            if wrong:
                st.dataframe(pd.DataFrame([
                    {
                        "ID": case.case_id,
                        "카테고리": case.category,
                        "유형": "오탐(FP)" if case.label == "clean" else "미탐(FN)",
                        "점수": round(case.risk_score, 3),
                        "비고": case.detail[:60],
                    }
                    for case in wrong
                ]), hide_index=True, use_container_width=True)
            else:
                st.success("오답 없음")

    report_md = bench_format(reports, n_cases=len(cases))
    st.download_button("📥 벤치마크 리포트 (.md)", report_md.encode(), file_name="benchmark_report.md", mime="text/markdown")


def _render_disclosure_panel(disclosure) -> None:
    if disclosure is None:
        return

    style = {
        "low": ("✅", "#4CAF50", "낮음"),
        "medium": ("⚠️", "#FFC107", "중간"),
        "high": ("🚨", "#F44336", "높음"),
    }.get(disclosure.level, ("•", "#9E9E9E", disclosure.level))
    emoji, color, level_ko = style

    st.markdown("#### 🎯 선택적 공시 (Cherry-picking) 탐지")
    st.caption("문서 단위 D6 — 불리한 민감 항목 누락(hidden trade-off)과 분모 없는 유리 비율(고아 비율)을 룰 기반으로 결정적 탐지")
    col1, col2 = st.columns([1, 3])
    with col1:
        st.markdown(
            f"<div style='border-radius:10px;padding:14px;text-align:center;"
            f"background:{color}22;border:1px solid {color}'>"
            f"<div style='font-size:26px'>{emoji}</div>"
            f"<div style='font-size:13px;color:#555'>선택적 공시 의심도</div>"
            f"<div style='font-size:22px;font-weight:700;color:{color}'>{disclosure.score:.2f} · {level_ko}</div></div>",
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(f"**판정 근거:** {html.escape(disclosure.rationale)}")
        asymmetry = disclosure.asymmetry or {}
        st.caption(
            f"공시한 유리 비율 {asymmetry.get('favorable_ratios_disclosed', 0)}개 · "
            f"누락 민감항목 {asymmetry.get('sensitive_items_omitted', 0)}개 · "
            f"고아 비율 {asymmetry.get('orphan_ratios', 0)}건"
        )

    if disclosure.orphan_ratios:
        with st.expander(f"🍒 고아 비율 {len(disclosure.orphan_ratios)}건 — 유리 비율만 공시, 분모/맥락 누락"):
            for orphan in disclosure.orphan_ratios:
                st.markdown(f"- {html.escape(orphan.detail)}")

    if disclosure.omitted_sensitive:
        with st.expander(f"🙈 누락 민감 항목 {len(disclosure.omitted_sensitive)}건 — 불리 노출 회피 의심"):
            st.dataframe(
                pd.DataFrame([
                    {
                        "K-ESG": item.code,
                        "항목": item.name,
                        "영역": item.area,
                        "민감도": round(item.sensitivity, 2),
                    }
                    for item in sorted(disclosure.omitted_sensitive, key=lambda item: item.sensitivity, reverse=True)
                ]),
                hide_index=True,
                use_container_width=True,
            )
    st.divider()


def _render_issb_gap_panel(gap_report) -> None:
    if gap_report is None:
        return

    st.markdown("#### 📎 ISSB/KSSB 얇은 갭 리포트")
    st.caption("K-ESG 뼈대는 유지하고, ISSB/KSSB 권위는 기후·그린워싱 방어 지점에만 얇게 덧댄 참고 레이어")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("프로파일 내 ISSB 공시", f"{gap_report.in_profile_disclosed}/{gap_report.in_profile_total}")
    c2.metric("프로파일 내 누락", gap_report.in_profile_missing)
    c3.metric("증빙 연결", gap_report.verified_count)
    c4.metric("프로파일 외 참고", gap_report.beyond_profile_disclosed)
    st.markdown(f"**요약:** {html.escape(gap_report.rationale)}")

    climate_rows = _issb_gap_table_rows(gap_report, "climate")
    defense_rows = _issb_gap_table_rows(gap_report, "greenwash_defense")
    general_rows = _issb_gap_table_rows(gap_report, "general")

    climate_tab, defense_tab, general_tab = st.tabs([
        f"🌿 기후/탄소 ({len(climate_rows)}개)",
        f"🛡 그린워싱 방어 ({len(defense_rows)}개)",
        f"📚 일반 ISSB ({len(general_rows)}개)",
    ])

    with climate_tab:
        st.dataframe(pd.DataFrame(climate_rows), hide_index=True, use_container_width=True)
    with defense_tab:
        st.dataframe(pd.DataFrame(defense_rows), hide_index=True, use_container_width=True)
    with general_tab:
        st.dataframe(pd.DataFrame(general_rows), hide_index=True, use_container_width=True)

    st.divider()


def _render_coverage_panel(extraction) -> None:
    area_labels = {"P": "정보공시", "E": "환경", "S": "사회", "G": "지배구조"}
    coverage_data = [
        {
            "영역": f"{code} · {area_labels[code]}",
            "공시 항목": stats["present"],
            "전체 항목": stats["total"],
            "커버리지": round(100 * stats["present"] / stats["total"], 1),
        }
        for code, stats in extraction.by_area.items()
    ]
    coverage_df = pd.DataFrame(coverage_data)

    chart_col, table_col = st.columns([3, 2])
    with chart_col:
        fig = go.Figure(go.Bar(
            x=coverage_df["영역"],
            y=coverage_df["커버리지"],
            marker_color=[
                "#4CAF50" if value >= 70 else "#FFC107" if value >= 50 else "#F44336"
                for value in coverage_df["커버리지"]
            ],
            text=[f"{value}%" for value in coverage_df["커버리지"]],
            textposition="outside",
        ))
        _apply_plotly_theme(
            fig,
            title="영역별 K-ESG 커버리지",
            yaxis=dict(range=[0, 110], title="커버리지 (%)"),
            height=300,
            margin=dict(t=40, b=20),
        )
        st.plotly_chart(fig, use_container_width=True)
    with table_col:
        st.dataframe(coverage_df, hide_index=True, use_container_width=True)


def _render_provenance_panel(result) -> None:
    from esgenie.provenance import bbox_to_pct, primary_evidence, provenance_chain, trust_summary, verification_view

    v15_trace = result.v15_trace
    if not v15_trace.data_points:
        return

    st.markdown("#### 🔍 주장 → 증빙 추적 (Provenance)")
    st.caption("생성된 모든 정량값은 SSOT 노드·원본 파일·문서 내 위치까지 역추적됩니다.")

    trust = trust_summary(v15_trace.data_points)
    m1, m2, m3 = st.columns(3)
    m1.metric("정량 항목", trust["total"])
    m2.metric("증빙 검증", f"{trust['verified']} / {trust['total']}")
    m3.metric("D1 평균 위험", f"{trust['avg_d1_risk']:.2f}")
    if trust["unverified"]:
        st.warning(f"⚠ 미검증 {trust['unverified']}건 — 원본 증빙 대조가 필요합니다 (시스템이 검증 갭을 스스로 표시).")

    tone_color = {"green": "green", "amber": "orange", "red": "red"}
    for data_point in v15_trace.data_points:
        view = verification_view(data_point.verification)
        icon = {"green": "✅", "amber": "⚠️", "red": "🚫"}[view["tone"]]
        with st.expander(f"{icon} [{data_point.kesg_code}] {data_point.kesg_name} — {data_point.value} {data_point.unit} ({data_point.period})"):
            chain = provenance_chain(data_point)
            cols = st.columns(len(chain))
            for col, step in zip(cols, chain):
                with col:
                    st.caption(step["label"])
                    if step["key"] == "location" and not step.get("linked"):
                        st.markdown(f"🔗❌ {step['value']}")
                    elif step["key"] == "location":
                        st.markdown(f"📍 {step['value']}")
                    else:
                        st.markdown(f"`{step['value']}`")

            cc1, cc2 = st.columns([2, 3])
            with cc1:
                st.badge(view["label"], color=tone_color[view["tone"]])
                st.caption("D1 수치 위험도")
                st.progress(min(1.0, float(data_point.d1_risk or 0.0)), text=f"{float(data_point.d1_risk or 0.0):.2f}")
            with cc2:
                evidence = primary_evidence(data_point.evidence_files or [])
                bbox = getattr(evidence, "bbox", None) if evidence else None
                file_name = getattr(evidence, "file_name", "") if evidence else ""
                page = (getattr(evidence, "page", 0) or 0) if evidence else 0
                st.caption("원본 문서 내 위치")
                pdf_path = os.path.join(result.export_paths["evidence_dir"], file_name) if file_name else ""
                rendered = False
                if bbox and file_name.lower().endswith(".pdf") and os.path.exists(pdf_path):
                    try:
                        from esgenie.pdf_render import render_page_with_box

                        png = render_page_with_box(pdf_path, bbox, page=page, dpi=110)
                        st.image(png, caption=f"{file_name} · p.{page + 1}", use_container_width=True)
                        rendered = True
                    except Exception as exc:  # noqa: BLE001
                        st.caption(f"원본 렌더 실패: {exc}")
                if not rendered:
                    box = bbox_to_pct(bbox)
                    if box:
                        st.markdown(
                            f"<div style='position:relative;width:100%;height:110px;"
                            f"background:#f4f4f2;border:1px solid #ccc;border-radius:6px'>"
                            f"<div style='position:absolute;left:{box['left']}%;top:{box['top']}%;"
                            f"width:{box['width']}%;height:{box['height']}%;"
                            f"background:rgba(255,193,7,0.3);border:2px solid #BA7517;border-radius:3px'></div></div>"
                            f"<div style='font-size:12px;color:#999'>원본 PDF 미첨부 — 위치 비율만 표시</div>",
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(
                            "<div style='width:100%;height:110px;background:#f4f4f2;"
                            "border:1px dashed #bbb;border-radius:6px;display:flex;"
                            "align-items:center;justify-content:center;color:#999;font-size:13px'>"
                            "좌표 미연결 — OCR 증빙(Upstage Document Parse) 경로 시 bbox 표시</div>",
                            unsafe_allow_html=True,
                        )

    with st.expander("정량 데이터시트 원본(표) 보기"):
        st.dataframe([data_point.to_dict() for data_point in v15_trace.data_points], use_container_width=True, hide_index=True)


def _render_hitl_panel(sentence_trace) -> None:
    st.divider()
    st.markdown("#### 문장별 위험 분석 & HITL 판정")
    summary = sentence_trace.summary
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("총 문장", summary["total_sentences"])
    m2.metric("HITL 필요", summary["hitl_count"])
    m3.metric("평균 위험도", f"{summary['avg_risk_score']:.3f}")
    m4.metric("수렴", "✅" if summary["converged"] else "❌")

    if "hitl_decisions" not in st.session_state:
        st.session_state.hitl_decisions = {}

    for sentence in sentence_trace.sentences:
        risk_vector = sentence.risk_vector
        risk_score = risk_vector.risk_score * 100 if risk_vector else 0.0
        level = risk_vector.level if risk_vector else "low"
        color = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(level, "⚪")

        with st.expander(f"{color} [{sentence.sentence_id}] {sentence.sentence_text[:60]}... (위험도 {risk_score:.1f})", expanded=(level == "high")):
            text_col, meta_col = st.columns([3, 2])
            with text_col:
                st.markdown("**문장 원문**")
                st.write(sentence.sentence_text)
                if risk_vector is not None:
                    st.markdown("**4축 위험 분해**")
                    st.dataframe(pd.DataFrame([
                        {"축": axis, "점수": f"{score:.3f}", "설명": detail}
                        for axis, score, detail in [
                            ("D1 수치오차", risk_vector.D1_numeric.score, risk_vector.D1_numeric.detail),
                            ("D2 모호어", risk_vector.D2_modifier.score, risk_vector.D2_modifier.detail),
                            ("D3 의미괴리", risk_vector.D3_semantic.score, risk_vector.D3_semantic.detail),
                            ("D5 시계열모순", risk_vector.D5_timeseries.score, risk_vector.D5_timeseries.detail),
                        ]
                    ]), hide_index=True, use_container_width=True)
            with meta_col:
                st.markdown("**K-ESG 연결**")
                st.code(sentence.kesg_item_id or "미매핑")
                st.markdown("**HITL 상태**")
                st.badge(sentence.hitl_status, color="red" if sentence.hitl_status == "HITL_REQUIRED" else "green")

            if level in ("medium", "high"):
                st.markdown("---")
                st.markdown("**HITL 판정:**")
                buttons = st.columns(3)
                current = st.session_state.hitl_decisions.get(sentence.sentence_id, "")
                with buttons[0]:
                    if st.button("✅ 승인", key=f"approve_{sentence.sentence_id}"):
                        st.session_state.hitl_decisions[sentence.sentence_id] = "approved"
                        st.rerun()
                with buttons[1]:
                    if st.button("✏️ 수정 필요", key=f"edit_{sentence.sentence_id}"):
                        st.session_state.hitl_decisions[sentence.sentence_id] = "needs_edit"
                        st.rerun()
                with buttons[2]:
                    if st.button("🚫 무시", key=f"ignore_{sentence.sentence_id}"):
                        st.session_state.hitl_decisions[sentence.sentence_id] = "ignored"
                        st.rerun()
                if current:
                    labels = {"approved": "✅ 승인됨", "needs_edit": "✏️ 수정 필요", "ignored": "🚫 무시됨"}
                    st.success(f"판정: {labels.get(current, current)}")

    if st.session_state.hitl_decisions:
        st.divider()
        st.markdown("### HITL 판정 결과 요약")
        st.dataframe(
            pd.DataFrame([{"문장 ID": sentence_id, "판정": decision} for sentence_id, decision in st.session_state.hitl_decisions.items()]),
            hide_index=True,
            use_container_width=True,
        )


def _source_tag(entry: dict) -> str:
    evidence_ids = entry.get("evidence_node_ids", [])
    if any(node_id.startswith("survey_") for node_id in evidence_ids):
        return "📝 설문"
    if any("ocr" in node_id for node_id in evidence_ids):
        return "📄 OCR"
    return "🏛 DART"
