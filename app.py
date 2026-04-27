"""ESGenie v10 Streamlit 데모.

실행:
    streamlit run app.py

OPENAI_API_KEY 없어도 Mock LLM으로 전체 6-Layer 흐름이 동작합니다.
Demo 모드 토글로 샘플 3사를 즉시 시연할 수 있습니다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from esgenie.config import SETTINGS
from esgenie.dart_client import list_sample_companies, load_report
from esgenie.knowledge.kesg_items import ALL_ITEMS
from esgenie.layer0_evidence_graph import build_evidence_graph
from esgenie.layer1_extract import extract
from esgenie.layer2_rag import HybridRAG
from esgenie.layer3_detect import detect, risk_band
from esgenie.layer4_verify import verify_and_refine
from esgenie.layer5_audit_trace import build_audit_trace, save_audit_trace
from esgenie.pipeline import _load_industry_stats

st.set_page_config(page_title="ESGenie v10 — K-ESG 공시 AI", layout="wide", page_icon="🌱")


# ---- 캐시 리소스 ------------------------------------------------------------

@st.cache_resource
def get_rag() -> HybridRAG:
    return HybridRAG()


# ---- 사이드바 ----------------------------------------------------------------

with st.sidebar:
    st.title("🌱 ESGenie v10")
    st.caption("6-Layer K-ESG 공시 보고서 AI")

    st.divider()

    # Demo 모드 토글 (가장 상단)
    demo_mode = st.toggle(
        "🎭 Demo 모드 (Mock LLM + 샘플 3사)",
        value=True,
        help="Mock LLM + 샘플 데이터로 API 키 없이 시연합니다.",
    )

    companies = list_sample_companies()
    labels = [f"{c['corp_name']} · {c['industry']}" for c in companies]
    choice = st.selectbox("기업 선택", options=range(len(companies)), format_func=lambda i: labels[i])
    corp = companies[choice]

    area = st.selectbox(
        "분석 영역",
        options=["E", "S", "G"],
        format_func=lambda a: {"E": "🌿 환경 (E)", "S": "🤝 사회 (S)", "G": "🏛 지배구조 (G)"}[a],
    )

    with st.expander("⚙️ 고급 설정", expanded=False):
        threshold = st.slider("자가 검증 임계치 (위험도 ≤)", 10, 80, 30, 5)
        max_iter  = st.slider("최대 재생성 반복", 1, 5, 3)
        demo_greenwash = st.checkbox(
            "그린워싱 시연 모드",
            value=False,
            help="초안을 의도적으로 과장 생성해 탐지 효과를 보여줍니다.",
        )

    st.divider()
    api_status  = "✅ 실 API" if not SETTINGS.use_mock_llm else "🟡 Mock LLM"
    dart_status = "✅ 실 API" if not SETTINGS.use_mock_dart else "🟡 샘플 데이터"
    st.caption(f"LLM {api_status} · DART {dart_status}")

    run_btn = st.button("▶ 분석 시작", type="primary", use_container_width=True)


# ---- 파이프라인 실행 ---------------------------------------------------------

if "result" not in st.session_state:
    st.session_state.result = None

if run_btn:
    with st.spinner("6-Layer 분석 중입니다..."):
        report          = load_report(corp["corp_code"])
        evidence_graph  = build_evidence_graph(report)
        extraction      = extract(report, evidence_graph=evidence_graph)
        rag             = get_rag()
        rag.build_corp_index(report)
        industry_stats  = _load_industry_stats(report.industry)

        verify = verify_and_refine(
            report, area, rag,
            threshold=float(threshold),
            max_iter=int(max_iter),
            demo_greenwash=bool(demo_greenwash),
            evidence_graph=evidence_graph,
            industry_stats=industry_stats,
        )
        trace = build_audit_trace(
            report=report, area=area, verification=verify,
            extraction=extraction,
            evidence_graph=evidence_graph,
            industry_stats=industry_stats,
        )
        trace_path = save_audit_trace(trace)

        st.session_state.result = {
            "report":          report,
            "evidence_graph":  evidence_graph,
            "extraction":      extraction,
            "verify":          verify,
            "trace":           trace,
            "trace_path":      str(trace_path),
            "area":            area,
        }

result = st.session_state.result

# ---- 탭 구성 ----------------------------------------------------------------

tab_home, tab_step1, tab_step2, tab_step3, tab_audit = st.tabs([
    "🏠 홈",
    "📊 Step 1 · 공시 진단",
    "📝 Step 2 · 보고서 초안",
    "✅ Step 3 · 검증 & 최종본",
    "🔍 Step 4 · Audit Trace",
])


# ========== 홈 ===============================================================

with tab_home:
    st.markdown("## 🌱 ESGenie v10")
    st.markdown("##### 6-Layer K-ESG 보고서 자동 작성 · 그린워싱 탐지 · 감사 추적")
    st.divider()

    cols = st.columns(3)
    with cols[0]:
        st.markdown("### L0 · Evidence Graph")
        st.markdown("DART 수치를 사실 노드로 구조화하고 시계열 엣지로 연결합니다.")
    with cols[1]:
        st.markdown("### L1–L2 · 추출 & RAG")
        st.markdown("K-ESG 61항목 추출 + Hybrid RAG 보고서 초안 생성.")
    with cols[2]:
        st.markdown("### L3–L4 · 5축 탐지 & 검증")
        st.markdown("D1~D5 위험 분해 + 5축 제약 주입 반복 정제.")

    cols2 = st.columns(3)
    with cols2[0]:
        st.markdown("### L5 · Audit Trace")
        st.markdown("문장별 증거·위험·재생성 이력을 audit_trace.json으로 출력.")
    with cols2[1]:
        st.markdown("### HITL 패널")
        st.markdown("위험 문장에 [승인/수정/무시] 인터랙션 지원.")
    with cols2[2]:
        st.markdown("### Demo 모드")
        st.markdown("API 키 없이 샘플 3사로 즉시 시연 가능.")

    st.divider()

    if result is None:
        st.info("👈 사이드바에서 기업·영역을 선택하고 **분석 시작**을 눌러주세요.")
    else:
        rp  = result["report"]
        ex  = result["extraction"]
        v   = result["verify"]
        eg  = result["evidence_graph"]
        tr  = result["trace"]
        area_label = {"E": "🌿 환경", "S": "🤝 사회", "G": "🏛 지배구조"}[result["area"]]

        st.markdown(f"### {rp.corp_name} · {area_label} 분석 결과")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("K-ESG 커버리지", f"{ex.coverage_pct:.1f}%", f"{len(ex.mapped)}/{len(ALL_ITEMS)} 항목")
        m2.metric("Evidence 노드", f"{len(eg.nodes)}개", f"엣지 {len(eg.edges)}개")
        m3.metric(
            "그린워싱 위험도", f"{v.final_score:.1f}",
            v.final_band, delta_color="inverse",
        )
        m4.metric(
            "검증 반복", f"{v.iterations_used}회",
            "수렴 ✅" if v.converged else ("HITL ⚠️" if v.hitl_required else "미수렴"),
        )
        st.success(v.final_text[:300] + ("..." if len(v.final_text) > 300 else ""))


# ========== Step 1 · 공시 진단 ===============================================

with tab_step1:
    st.markdown("## 📊 Step 1 · 공시 진단")
    st.divider()

    if result is None:
        st.info("사이드바에서 분석을 시작하세요.")
    else:
        ex = result["extraction"]
        eg = result["evidence_graph"]

        area_labels = {"P": "정보공시", "E": "환경", "S": "사회", "G": "지배구조"}
        cov_data = [
            {
                "영역": f"{k} · {area_labels[k]}",
                "공시 항목": v["present"],
                "전체 항목": v["total"],
                "커버리지": round(100 * v["present"] / v["total"], 1),
            }
            for k, v in ex.by_area.items()
        ]
        cov_df = pd.DataFrame(cov_data)

        col_chart, col_table = st.columns([3, 2])
        with col_chart:
            fig = go.Figure(go.Bar(
                x=cov_df["영역"],
                y=cov_df["커버리지"],
                marker_color=["#4CAF50" if v >= 70 else "#FFC107" if v >= 50 else "#F44336"
                               for v in cov_df["커버리지"]],
                text=[f"{v}%" for v in cov_df["커버리지"]],
                textposition="outside",
            ))
            fig.update_layout(title="영역별 K-ESG 커버리지", yaxis=dict(range=[0, 110], title="커버리지 (%)"),
                              height=300, margin=dict(t=40, b=20))
            st.plotly_chart(fig, use_container_width=True)
        with col_table:
            st.dataframe(cov_df, hide_index=True, use_container_width=True)

        st.divider()
        st.markdown("#### Evidence Graph 노드 목록")
        node_data = [
            {"ID": n.id, "코드": n.metric, "값": n.value, "단위": n.unit,
             "연도": n.period, "출처": n.source}
            for n in sorted(eg.nodes.values(), key=lambda x: x.metric)
        ]
        st.dataframe(pd.DataFrame(node_data), hide_index=True, use_container_width=True)

        tab_present, tab_missing = st.tabs([
            f"✅ 공시 항목 ({len(ex.mapped)}개)",
            f"⚠️ 누락 항목 ({len(ex.missing)}개)",
        ])
        with tab_present:
            df = pd.DataFrame([
                {
                    "코드": v["code"], "영역": v["area"], "항목명": v["name"],
                    "값": v["value"], "단위": v.get("unit", "-"),
                    "Evidence 노드": len(v.get("evidence_node_ids", [])),
                }
                for v in ex.mapped.values()
            ])
            st.dataframe(df, hide_index=True, use_container_width=True)
        with tab_missing:
            miss_df = pd.DataFrame([
                {"코드": it.code, "영역": it.area, "항목명": it.name, "유형": it.data_type}
                for it in ALL_ITEMS if it.code in ex.missing
            ])
            st.dataframe(miss_df, hide_index=True, use_container_width=True)


# ========== Step 2 · 보고서 초안 ============================================

with tab_step2:
    st.markdown("## 📝 Step 2 · 보고서 초안 생성")
    st.divider()

    if result is None:
        st.info("사이드바에서 분석을 시작하세요.")
    else:
        v = result["verify"]
        first = v.steps[0]
        area_label = {"E": "🌿 환경", "S": "🤝 사회", "G": "🏛 지배구조"}[result["area"]]

        st.markdown(f"#### {area_label} 영역 초안")
        st.info(first.generation.text)
        if first.generation.used_mock_llm:
            st.warning("현재 Mock LLM으로 생성되었습니다.")

        with st.expander("📚 참조 근거 보기"):
            ctx = first.generation.context
            c1, c2, c3 = st.columns(3)
            with c1:
                st.markdown("**K-ESG 가이드라인**")
                for doc, score in ctx.kesg_hits:
                    st.caption(f"[{score:.3f}] {doc.text[:100]}...")
            with c2:
                st.markdown("**업종 벤치마크**")
                for doc, score in ctx.industry_hits:
                    st.caption(f"[{score:.3f}] {doc.text[:100]}...")
            with c3:
                st.markdown("**자사 DART 원문**")
                for doc, score in ctx.corp_hits:
                    st.caption(f"[{score:.3f}] {doc.text[:100]}...")


# ========== 공통 렌더러 ======================================================

def render_detection(det: Any, key_prefix: str = "det") -> None:
    band  = risk_band(det.risk_score)
    color = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🟠", "CRITICAL": "🔴"}[band]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("종합 위험도", f"{det.risk_score:.1f}", f"{color} {band}", delta_color="inverse")
    c2.metric("수치 주장", f"{len(det.numeric_claims)}건")
    c3.metric("과장 수식어", f"{sum(len(v['phrases']) for v in det.vague_phrases)}건")
    c4.metric("DART 유사도", f"{det.semantic_similarity:.3f}")

    # 5축 레이더 차트
    rv = det.risk_vector
    if rv is not None:
        _render_radar(rv, key=f"{key_prefix}_radar_v10")
    elif det.components:
        _render_legacy_radar(det.components, key=f"{key_prefix}_radar_legacy")

    for h in det.highlights:
        if h["type"] == "mismatch":
            st.error(
                f"**수치 불일치** | 주장: `{h['claim']}` vs DART: `{h['dart_value']}` "
                f"(편차 {h['delta_pct']:+.1f}%)\n\n> {h['sentence']}"
            )
        else:
            st.warning(f"**모호한 표현** | `{', '.join(h['phrases'])}`\n\n> {h['sentence']}")


def _render_radar(rv: Any, key: str) -> None:
    """5축 D1~D5 레이더 차트."""
    axes   = ["D1 수치오차", "D2 모호어", "D3 의미괴리", "D4 업종편차", "D5 시계열모순"]
    scores = [
        rv.D1_numeric.score * 100,
        rv.D2_modifier.score * 100,
        rv.D3_semantic.score * 100,
        rv.D4_industry.score * 100,
        rv.D5_timeseries.score * 100,
    ]
    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=scores + scores[:1],
        theta=axes + axes[:1],
        fill="toself",
        name="5축 위험도",
        line_color="#FF6B6B",
        fillcolor="rgba(255,107,107,0.2)",
    ))
    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
        showlegend=False, height=300, margin=dict(t=30, b=20),
        title=f"5축 위험 분해 (종합 {rv.risk_score*100:.1f})",
    )
    st.plotly_chart(fig, use_container_width=True, key=key)


def _render_legacy_radar(components: dict, key: str) -> None:
    """기존 4축 레이더 (risk_vector 없을 때 폴백)."""
    label_kr = {"numeric_mismatch": "수치 과장", "unverifiable": "검증 불가",
                "vague_language": "모호한 표현", "semantic_gap": "의미 괴리"}
    labels = list(components.keys())
    labels_kr = [label_kr.get(l, l) for l in labels]
    values = [components[k] for k in labels]
    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=values + values[:1], theta=labels_kr + labels_kr[:1],
        fill="toself", name="위험도", line_color="#FF6B6B",
    ))
    fig.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
                      showlegend=False, height=280, margin=dict(t=20, b=10))
    st.plotly_chart(fig, use_container_width=True, key=key)


# ========== Step 3 · 검증 & 최종본 ==========================================

with tab_step3:
    st.markdown("## ✅ Step 3 · 검증 & 최종본")
    st.divider()

    if result is None:
        st.info("사이드바에서 분석을 시작하세요.")
    else:
        v = result["verify"]
        band_color = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🟠", "CRITICAL": "🔴"}

        st.markdown("### 최종 보고서")
        st.success(v.final_text)
        st.caption(
            f"{band_color[v.final_band]} 최종 위험도 **{v.final_score:.1f}** ({v.final_band}) · "
            f"검증 {v.iterations_used}회 · "
            + ("수렴 완료 ✅" if v.converged else "HITL 필요 ⚠️" if v.hitl_required else "미수렴 ⚠️")
        )

        # 5축 레이더 (최종 단계)
        final_rv = v.final.detection.risk_vector
        if final_rv is not None:
            st.markdown("#### 최종 5축 위험 분해")
            _render_radar(final_rv, key="step3_final_radar")

        st.divider()

        # 위험도 변화 추이
        if len(v.steps) > 1:
            prog_df = pd.DataFrame([
                {"반복": "초안" if s.iteration == 0 else f"{s.iteration}차 재생성",
                 "위험도": s.detection.risk_score}
                for s in v.steps
            ])
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=prog_df["반복"], y=prog_df["위험도"],
                mode="lines+markers", line=dict(color="#FF6B6B", width=2), marker=dict(size=8),
            ))
            fig.add_hline(
                y=v.metadata["threshold"], line_dash="dash", line_color="#4CAF50",
                annotation_text=f"목표 임계치 ({v.metadata['threshold']})",
                annotation_position="top right",
            )
            fig.update_layout(height=240, yaxis=dict(title="위험도", range=[0, 105]),
                              margin=dict(t=10, b=10))
            st.plotly_chart(fig, use_container_width=True, key="step3_progress")

        with st.expander("📋 초안 탐지 상세"):
            render_detection(v.steps[0].detection, key_prefix="step3_draft")

        for step in v.steps[1:]:
            with st.expander(f"🔄 {step.iteration}차 재생성 · 위험도 {step.detection.risk_score:.1f}", expanded=False):
                if step.instruction:
                    st.caption("적용된 제약:")
                    st.code(step.instruction, language=None)
                st.write(step.generation.text)
                render_detection(step.detection, key_prefix=f"step3_iter{step.iteration}")


# ========== Step 4 · Audit Trace ============================================

with tab_audit:
    st.markdown("## 🔍 Step 4 · Audit Trace")
    st.markdown("문장별 증거 노드 · 5축 위험도 · 재생성 이력을 확인하고 HITL 판정을 내립니다.")
    st.divider()

    if result is None:
        st.info("사이드바에서 분석을 시작하세요.")
    else:
        trace = result["trace"]
        area_label = {"E": "🌿 환경", "S": "🤝 사회", "G": "🏛 지배구조"}[result["area"]]

        # 요약 메트릭
        s = trace.summary
        sm1, sm2, sm3, sm4 = st.columns(4)
        sm1.metric("총 문장", s["total_sentences"])
        sm2.metric("HITL 필요", s["hitl_count"])
        sm3.metric("평균 위험도", f"{s['avg_risk_score']:.3f}")
        sm4.metric("수렴", "✅" if s["converged"] else "❌")

        st.divider()

        # audit_trace.json 다운로드 버튼
        trace_json = json.dumps(trace.to_dict(), ensure_ascii=False, indent=2)
        st.download_button(
            label="📥 audit_trace.json 다운로드",
            data=trace_json.encode("utf-8"),
            file_name=f"audit_trace_{trace.ticker}_{trace.area}.json",
            mime="application/json",
        )

        st.divider()

        # HITL 패널 — 문장별 인라인 위험 하이라이트
        st.markdown("### 문장별 위험 분석 & HITL 판정")
        if "hitl_decisions" not in st.session_state:
            st.session_state.hitl_decisions = {}

        for sent in trace.sentences:
            rv = sent.risk_vector
            risk_score = rv.risk_score * 100 if rv else 0.0
            level = rv.level if rv else "low"
            color = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(level, "⚪")

            with st.expander(
                f"{color} [{sent.sentence_id}] {sent.sentence_text[:60]}... "
                f"(위험도 {risk_score:.1f})",
                expanded=(level == "high"),
            ):
                col_text, col_meta = st.columns([3, 2])

                with col_text:
                    st.markdown("**문장 원문**")
                    st.write(sent.sentence_text)

                    if rv is not None:
                        st.markdown("**5축 위험 분해**")
                        axis_df = pd.DataFrame([
                            {"축": ax, "점수": f"{sc:.3f}", "설명": detail}
                            for ax, sc, detail in [
                                ("D1 수치오차",    rv.D1_numeric.score,    rv.D1_numeric.detail),
                                ("D2 모호어",      rv.D2_modifier.score,   rv.D2_modifier.detail),
                                ("D3 의미괴리",    rv.D3_semantic.score,   rv.D3_semantic.detail),
                                ("D4 업종편차",    rv.D4_industry.score,   rv.D4_industry.detail),
                                ("D5 시계열모순",  rv.D5_timeseries.score, rv.D5_timeseries.detail),
                            ]
                        ])
                        st.dataframe(axis_df, hide_index=True, use_container_width=True)

                with col_meta:
                    st.markdown("**K-ESG 연결**")
                    st.code(sent.kesg_item_id or "미매핑")
                    st.markdown("**Evidence 노드**")
                    for nid in sent.evidence_node_ids[:3]:
                        st.code(nid)
                    st.markdown("**현재 HITL 상태**")
                    st.badge(
                        sent.hitl_status,
                        color="red" if sent.hitl_status == "HITL_REQUIRED" else "green",
                    )

                # HITL 판정 버튼
                if level in ("medium", "high"):
                    st.markdown("---")
                    st.markdown("**HITL 판정:**")
                    btn_col = st.columns(3)
                    current = st.session_state.hitl_decisions.get(sent.sentence_id, "")
                    with btn_col[0]:
                        if st.button("✅ 승인", key=f"approve_{sent.sentence_id}"):
                            st.session_state.hitl_decisions[sent.sentence_id] = "approved"
                            st.rerun()
                    with btn_col[1]:
                        if st.button("✏️ 수정 필요", key=f"edit_{sent.sentence_id}"):
                            st.session_state.hitl_decisions[sent.sentence_id] = "needs_edit"
                            st.rerun()
                    with btn_col[2]:
                        if st.button("🚫 무시", key=f"ignore_{sent.sentence_id}"):
                            st.session_state.hitl_decisions[sent.sentence_id] = "ignored"
                            st.rerun()
                    if current:
                        label_map = {"approved": "✅ 승인됨", "needs_edit": "✏️ 수정 필요", "ignored": "🚫 무시됨"}
                        st.success(f"판정: {label_map.get(current, current)}")

        # HITL 결과 요약
        decisions = st.session_state.hitl_decisions
        if decisions:
            st.divider()
            st.markdown("### HITL 판정 결과 요약")
            dec_df = pd.DataFrame([
                {"문장 ID": sid, "판정": d}
                for sid, d in decisions.items()
            ])
            st.dataframe(dec_df, hide_index=True, use_container_width=True)
