"""Streamlit tab renderers for ESGenie."""
from __future__ import annotations

import html
import json
import os
import re
from collections import Counter

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from esgenie.benchmark import format_report as bench_format
from esgenie.benchmark import load_benchmark, run_benchmark
from esgenie.config import SETTINGS
from esgenie.knowledge.issb_mapping import PILLAR_LABELS, mappings_for
from esgenie.ssot.ssot_pipeline import ssot_summary

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


def _report_card(text: str, kind: str = "draft", tag_label: str | None = None) -> str:
    cls = "esg-report-card final" if kind == "final" else "esg-report-card"
    tag = (
        f'<span class="esg-report-tag {kind}">{html.escape(tag_label)}</span>'
        if tag_label else ""
    )
    return f'<div class="{cls}">{tag}\n\n{text}\n\n</div>'


def render_home_tab(result, active_area: str, gradient: str) -> None:
    st.markdown("## ESGenie · 6-Layer K-ESG 공시 자동화")
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


def render_ssot_tab(result, gradient: str) -> None:
    st.markdown("## 🗂 증빙 & 단일 진실 원천(SSOT)")
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


def render_diag_tab(result, gradient: str) -> None:
    st.markdown("## 📊 공시 진단")
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


def render_draft_tab(result, active_area: str, gradient: str) -> None:
    st.markdown("## 📝 보고서 초안 생성")
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


def render_verify_tab(result, active_area: str, gradient: str) -> None:
    st.markdown("## ✅ 검증 & 최종본")
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
        fig.update_layout(
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
        fig.update_layout(height=220, yaxis=dict(title="위험도", range=[0, 105]), margin=dict(t=10, b=10))
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


def render_policy_tab(result, gradient: str) -> None:
    st.markdown("## 📋 P축 — 사내 규정 필수 조항 검증")
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


def render_audit_tab(result, active_area: str, gradient: str) -> None:
    st.markdown("## 🔍 감사 추적 & HITL 패널")
    st.markdown(gradient, unsafe_allow_html=True)

    if result is None:
        st.info("분석을 시작하세요.")
        return

    sentence_trace = result.audit_traces.get(active_area)
    paths = result.export_paths

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


def render_benchmark_tab(gradient: str) -> None:
    st.markdown("## 🧪 그린워싱 검출 벤치마크")
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
    fig.update_layout(
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
        fig.update_layout(title="영역별 K-ESG 커버리지", yaxis=dict(range=[0, 110], title="커버리지 (%)"), height=300, margin=dict(t=40, b=20))
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
                            "좌표 미연결 — OCR 증빙(Azure Doc Intelligence) 경로 시 bbox 표시</div>",
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
