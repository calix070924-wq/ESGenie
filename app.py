"""ESGenie v10 Streamlit 데모.

실행:
    streamlit run app.py

OPENAI_API_KEY 없어도 Mock LLM으로 전체 6-Layer 흐름이 동작합니다.
Demo 모드 토글로 샘플 3사를 즉시 시연할 수 있습니다.
"""
from __future__ import annotations

import html
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

# ---- 글로벌 스타일 ----------------------------------------------------------

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;900&display=swap');
    html, body,
    [data-testid="stAppViewContainer"],
    [data-testid="stSidebar"] {
        font-family: 'Noto Sans KR', 'Helvetica Neue', sans-serif;
    }
    [data-testid="stAppViewContainer"] [class*="material-"],
    [data-testid="stSidebar"] [class*="material-"],
    [data-testid="stAppViewContainer"] [class*="Material"],
    [data-testid="stSidebar"] [class*="Material"] {
        font-family: 'Material Symbols Rounded', 'Material Icons', sans-serif !important;
    }
    section[data-testid="stSidebar"] {
        background-color: #f0f7f0;
    }
    section[data-testid="stSidebar"],
    section[data-testid="stSidebar"] p,
    section[data-testid="stSidebar"] span,
    section[data-testid="stSidebar"] label,
    section[data-testid="stSidebar"] h1,
    section[data-testid="stSidebar"] h2,
    section[data-testid="stSidebar"] h3,
    section[data-testid="stSidebar"] h4,
    section[data-testid="stSidebar"] li,
    section[data-testid="stSidebar"] small,
    section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"],
    section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] *,
    section[data-testid="stSidebar"] [data-testid="stCaptionContainer"],
    section[data-testid="stSidebar"] [data-testid="stWidgetLabel"],
    section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] *,
    section[data-testid="stSidebar"] details summary,
    section[data-testid="stSidebar"] details summary * {
        color: #1b3a1f !important;
    }
    section[data-testid="stSidebar"] [data-baseweb="select"] > div,
    section[data-testid="stSidebar"] [data-baseweb="input"] > div,
    section[data-testid="stSidebar"] input,
    section[data-testid="stSidebar"] textarea {
        background-color: #ffffff !important;
        border: 1px solid rgba(46, 125, 50, 0.30) !important;
        border-radius: 8px !important;
    }
    section[data-testid="stSidebar"] [data-baseweb="select"] > div:focus-within,
    section[data-testid="stSidebar"] [data-baseweb="input"] > div:focus-within {
        border-color: #4caf50 !important;
        box-shadow: 0 0 0 2px rgba(76, 175, 80, 0.18) !important;
    }
    section[data-testid="stSidebar"] [data-baseweb="select"] *,
    section[data-testid="stSidebar"] input,
    section[data-testid="stSidebar"] textarea,
    section[data-testid="stSidebar"] [role="combobox"] {
        color: #1b3a1f !important;
    }
    [data-baseweb="popover"] [data-baseweb="menu"] {
        background-color: #ffffff !important;
        border: 1px solid rgba(46, 125, 50, 0.20) !important;
    }
    [data-baseweb="popover"] [data-baseweb="menu"] li,
    [data-baseweb="popover"] [data-baseweb="menu"] li * {
        color: #1b3a1f !important;
    }
    [data-baseweb="popover"] [data-baseweb="menu"] li[aria-selected="true"],
    [data-baseweb="popover"] [data-baseweb="menu"] li:hover {
        background-color: #f0f7f0 !important;
    }
    section[data-testid="stSidebar"] hr {
        border-color: rgba(46, 125, 50, 0.25) !important;
    }
    section[data-testid="stSidebar"] button[kind="primary"],
    section[data-testid="stSidebar"] button[kind="primary"] * {
        color: #ffffff !important;
    }
    section[data-testid="stSidebar"] button[kind="primary"] {
        padding: 14px 24px !important;
        font-size: 18px !important;
        font-weight: 700 !important;
        height: auto !important;
        letter-spacing: 0.5px;
        background-color: #4caf50 !important;
        border-color: #2e7d32 !important;
        box-shadow: 0 2px 6px rgba(46, 125, 50, 0.35);
    }
    section[data-testid="stSidebar"] button[kind="primary"]:hover {
        background-color: #2e7d32 !important;
        border-color: #1b5e20 !important;
    }
    [data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 12px !important;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
        transition: box-shadow 0.2s ease;
    }
    [data-testid="stVerticalBlockBorderWrapper"]:hover {
        box-shadow: 0 4px 14px rgba(0, 0, 0, 0.12);
    }
    .esg-info-card {
        background: #f9f9f9;
        border-radius: 12px;
        padding: 24px 20px;
        border: 1px solid #e8e8e8;
        box-shadow: 0 2px 6px rgba(0, 0, 0, 0.05);
        transition: all 0.25s ease;
        min-height: 170px;
        margin-bottom: 12px;
    }
    .esg-info-card:hover {
        background: #f0f7f0;
        border-color: #4caf50;
        transform: translateY(-3px);
        box-shadow: 0 6px 18px rgba(76, 175, 80, 0.18);
    }
    .esg-info-icon {
        font-size: 44px;
        line-height: 1;
        margin-bottom: 10px;
    }
    .esg-info-title {
        font-size: 17px;
        font-weight: 700;
        color: #1b3a1f;
        margin-bottom: 8px;
    }
    .esg-info-desc {
        font-size: 14px;
        color: #555;
        line-height: 1.55;
    }
    .esg-metric-card {
        background: #ffffff;
        border: 2px solid #4caf50;
        border-radius: 12px;
        padding: 18px 16px;
        height: 100%;
        box-shadow: 0 2px 6px rgba(0, 0, 0, 0.05);
        transition: box-shadow 0.2s ease;
    }
    .esg-metric-card:hover {
        box-shadow: 0 4px 14px rgba(76, 175, 80, 0.18);
    }
    .esg-metric-label {
        font-size: 13px;
        color: #888;
        font-weight: 500;
        margin-bottom: 6px;
        letter-spacing: 0.2px;
    }
    .esg-metric-value {
        font-size: 30px;
        font-weight: 700;
        color: #1b3a1f;
        line-height: 1.1;
    }
    .esg-metric-delta {
        font-size: 13px;
        margin-top: 8px;
        font-weight: 600;
    }
    .esg-final-quote {
        background: #f7fbf7;
        border-left: 4px solid #4caf50;
        padding: 18px 22px;
        border-radius: 4px;
        margin-top: 8px;
        color: #1b3a1f;
        line-height: 1.7;
        font-style: italic;
        white-space: pre-wrap;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
    }
    .esg-final-quote::before {
        content: "\\201C";
        display: block;
        font-size: 32px;
        color: #4caf50;
        line-height: 0.6;
        font-style: normal;
        margin-bottom: 6px;
    }
    .esg-report-card {
        background: #ffffff;
        border: 1px solid #e5e7eb;
        border-radius: 12px;
        padding: 26px 28px;
        font-size: 15px;
        line-height: 1.8;
        color: #1f2937;
        word-break: keep-all;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
    }
    .esg-report-card.final {
        border-left: 3px solid #4caf50;
    }
    .esg-report-card p,
    .esg-report-card li,
    .esg-report-card td,
    .esg-report-card span:not(.esg-report-tag),
    .esg-report-card div:not(.esg-report-tag),
    .esg-report-card strong,
    .esg-report-card em,
    .esg-report-card blockquote {
        color: #1f2937 !important;
        font-style: normal !important;
    }
    .esg-report-card h1,
    .esg-report-card h2,
    .esg-report-card h3,
    .esg-report-card h4,
    .esg-report-card h5,
    .esg-report-card h6 {
        color: #1b3a1f !important;
        font-style: normal !important;
        font-weight: 700 !important;
        margin-top: 18px !important;
        margin-bottom: 10px !important;
        padding-bottom: 6px !important;
        border-bottom: 1px solid #e8f5e9 !important;
    }
    .esg-report-card h1 { font-size: 22px !important; }
    .esg-report-card h2 { font-size: 19px !important; }
    .esg-report-card h3 { font-size: 17px !important; }
    .esg-report-card h4 { font-size: 15px !important; }
    .esg-report-card h1:first-child,
    .esg-report-card h2:first-child,
    .esg-report-card h3:first-child,
    .esg-report-card h4:first-child,
    .esg-report-card > *:first-child + h1,
    .esg-report-card > *:first-child + h2,
    .esg-report-card > *:first-child + h3,
    .esg-report-card > *:first-child + h4 {
        margin-top: 4px !important;
    }
    .esg-report-card p {
        line-height: 1.8 !important;
        margin: 10px 0 !important;
    }
    .esg-report-card ul,
    .esg-report-card ol {
        margin: 10px 0 !important;
        padding-left: 24px !important;
    }
    .esg-report-card li {
        line-height: 1.7 !important;
        margin: 4px 0 !important;
    }
    .esg-report-card strong {
        color: #1b3a1f !important;
        font-weight: 700 !important;
    }
    .esg-report-card code {
        background: #f3f4f6 !important;
        color: #1b3a1f !important;
        padding: 2px 6px;
        border-radius: 4px;
        font-size: 13px;
    }
    .esg-report-tag {
        display: inline-block;
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.6px;
        padding: 3px 10px;
        border-radius: 999px;
        margin-bottom: 14px;
        text-transform: uppercase;
    }
    .esg-report-tag.draft {
        color: #6b7280 !important;
        background: #f3f4f6 !important;
    }
    .esg-report-tag.final {
        color: #2e7d32 !important;
        background: #e8f5e9 !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

GRADIENT_BANNER = (
    '<div style="height: 6px; '
    'background: linear-gradient(90deg, #2e7d32, #4caf50, #a5d6a7); '
    'border-radius: 3px; margin: 6px 0 20px 0;"></div>'
)


def _info_card(icon: str, title: str, desc: str) -> str:
    return (
        '<div class="esg-info-card">'
        f'<div class="esg-info-icon">{icon}</div>'
        f'<div class="esg-info-title">{html.escape(title)}</div>'
        f'<div class="esg-info-desc">{html.escape(desc)}</div>'
        '</div>'
    )


def _metric_card(label: str, value: str, delta: str, delta_color: str = "#666") -> str:
    return (
        '<div class="esg-metric-card">'
        f'<div class="esg-metric-label">{html.escape(label)}</div>'
        f'<div class="esg-metric-value">{html.escape(value)}</div>'
        f'<div class="esg-metric-delta" style="color: {delta_color};">{html.escape(delta)}</div>'
        '</div>'
    )


def _report_card(text: str, kind: str = "draft", tag_label: str | None = None) -> str:
    cls = "esg-report-card final" if kind == "final" else "esg-report-card"
    tag_html = ""
    if tag_label:
        tag_cls = "esg-report-tag final" if kind == "final" else "esg-report-tag draft"
        tag_html = f'<span class="{tag_cls}">{html.escape(tag_label)}</span>'
    # 본문은 escape 하지 않음 → Streamlit이 마크다운(### 제목, **굵게**, - 리스트)을 정상 렌더.
    # HTML 블록과 마크다운 본문이 분리되도록 빈 줄로 감싼다.
    return f'<div class="{cls}">{tag_html}\n\n{text}\n\n</div>'


# ---- 캐시 리소스 ------------------------------------------------------------

@st.cache_resource
def get_rag() -> HybridRAG:
    return HybridRAG()


# ---- 사이드바 ----------------------------------------------------------------

with st.sidebar:
    st.markdown(
        """
        <div style="background: linear-gradient(135deg, #2e7d32, #4caf50);
                    padding: 22px 16px; border-radius: 12px;
                    text-align: center; margin-bottom: 18px;
                    box-shadow: 0 2px 10px rgba(46, 125, 50, 0.25);">
            <div style="font-size: 34px; line-height: 1; color: #ffffff !important;">🌱</div>
            <div style="color: #ffffff !important; font-size: 22px; font-weight: 700; margin-top: 6px;">
                ESGenie v10
            </div>
            <div style="color: rgba(255, 255, 255, 0.95) !important; font-size: 12px; margin-top: 4px;">
                6-Layer K-ESG 공시 보고서 AI
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

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
            value=True,
            help="초안에서 의도적 과장을 생성 → L3/L4가 탐지·수정하는 과정을 시연합니다",
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
    st.markdown(GRADIENT_BANNER, unsafe_allow_html=True)

    cols = st.columns(3)
    cols[0].markdown(
        _info_card("🔗", "L0 · Evidence Graph",
                   "DART 수치를 사실 노드로 구조화하고 시계열 엣지로 연결합니다."),
        unsafe_allow_html=True,
    )
    cols[1].markdown(
        _info_card("🔍", "L1–L2 · 추출 & RAG",
                   "K-ESG 61항목 추출 + Hybrid RAG 보고서 초안 생성."),
        unsafe_allow_html=True,
    )
    cols[2].markdown(
        _info_card("🛡️", "L3–L4 · 5축 탐지 & 검증",
                   "D1~D5 위험 분해 + 5축 제약 주입 반복 정제."),
        unsafe_allow_html=True,
    )

    cols2 = st.columns(3)
    cols2[0].markdown(
        _info_card("📋", "L5 · Audit Trace",
                   "문장별 증거·위험·재생성 이력을 audit_trace.json으로 출력."),
        unsafe_allow_html=True,
    )
    cols2[1].markdown(
        _info_card("👤", "HITL 패널",
                   "위험 문장에 [승인/수정/무시] 인터랙션 지원."),
        unsafe_allow_html=True,
    )
    cols2[2].markdown(
        _info_card("🎭", "Demo 모드",
                   "API 키 없이 샘플 3사로 즉시 시연 가능."),
        unsafe_allow_html=True,
    )

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

        band_color_map = {
            "LOW": "#4caf50", "MEDIUM": "#fbc02d",
            "HIGH": "#f57c00", "CRITICAL": "#d32f2f",
        }
        risk_color = band_color_map.get(v.final_band, "#666")
        if v.converged:
            iter_text, iter_color = "수렴 ✅", "#4caf50"
        elif v.hitl_required:
            iter_text, iter_color = "HITL ⚠️", "#f57c00"
        else:
            iter_text, iter_color = "미수렴", "#d32f2f"

        m1, m2, m3, m4 = st.columns(4)
        with m1:
            st.metric("K-ESG 커버리지", f"{ex.coverage_pct:.1f}%",
                      f"{len(ex.mapped)}/{len(ALL_ITEMS)} 항목", delta_color="off")
        with m2:
            st.metric("Evidence 노드", f"{len(eg.nodes)}개",
                      f"엣지 {len(eg.edges)}개", delta_color="off")
        with m3:
            band_emoji = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🟠", "CRITICAL": "🔴"}.get(v.final_band, "")
            st.metric("그린워싱 위험도", f"{v.final_score:.1f}",
                      f"{band_emoji} {v.final_band}", delta_color="off")
        with m4:
            st.metric("검증 반복", f"{v.iterations_used}회",
                      iter_text, delta_color="off")

        import re as _re
        _preview_plain = _re.sub(r"^#{1,6}\s*", "", v.final_text[:300], flags=_re.MULTILINE).strip()
        if len(v.final_text) > 300:
            _preview_plain += "..."
        with st.container(border=True):
            st.caption("📄 보고서 미리보기")
            st.write(_preview_plain)


# ========== Step 1 · 공시 진단 ===============================================

with tab_step1:
    st.markdown("## 📊 Step 1 · 공시 진단")
    st.markdown(GRADIENT_BANNER, unsafe_allow_html=True)

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

        st.caption("→ Step 2에서 이 데이터로 보고서 초안을 생성합니다")


# ========== Step 2 · 보고서 초안 ============================================

with tab_step2:
    st.markdown("## 📝 Step 2 · 보고서 초안 생성")
    st.markdown(GRADIENT_BANNER, unsafe_allow_html=True)

    if result is None:
        st.info("사이드바에서 분석을 시작하세요.")
    else:
        v = result["verify"]
        first = v.steps[0]
        area_label = {"E": "🌿 환경", "S": "🤝 사회", "G": "🏛 지배구조"}[result["area"]]

        st.info(
            "💡 L0 Evidence Graph에서 추출한 수치 팩트를 기반으로 L2 RAG가 초안을 생성합니다. "
            "그린워싱 시연 모드에서는 의도적으로 과장된 초안이 생성되어 L3/L4 탐지 효과를 확인할 수 있습니다."
        )
        st.markdown(f"#### {area_label} 영역 초안")
        st.markdown(
            _report_card(first.generation.text, kind="draft", tag_label="DRAFT"),
            unsafe_allow_html=True,
        )
        if first.generation.used_mock_llm:
            st.caption("⚠️ 현재 Mock LLM으로 생성되었습니다.")

        with st.expander("📌 이 초안 생성에 사용된 레이어 입력값"):
            eg = result["evidence_graph"]
            ex = result["extraction"]
            ctx = first.generation.context
            area = result["area"]

            lc1, lc2, lc3 = st.columns(3)

            with lc1:
                st.markdown("**L0 Evidence 노드 (상위 3개)**")
                area_nodes = [
                    n for n in eg.nodes.values()
                    if n.metric.startswith(area + "-")
                ][:3]
                if area_nodes:
                    for n in area_nodes:
                        st.caption(f"`{n.metric}` {n.value} {n.unit or ''}")
                else:
                    st.caption("없음")

            with lc2:
                st.markdown("**L1 K-ESG 매핑 항목 수**")
                area_stats = ex.by_area.get(area, {})
                present = area_stats.get("present", 0)
                total   = area_stats.get("total", 0)
                st.caption(f"{present} / {total} 항목 공시")

            with lc3:
                st.markdown("**L2 RAG 검색 채널**")
                st.caption(f"K-ESG 가이드라인: {len(ctx.kesg_hits)}건")
                st.caption(f"업종 벤치마크: {len(ctx.industry_hits)}건")
                st.caption(f"자사 DART 원문: {len(ctx.corp_hits)}건")

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
    """4축 D1·D2·D3·D5 레이더 차트."""
    axes   = ["D1 수치오차", "D2 모호어", "D3 의미괴리", "D5 시계열모순"]
    scores = [
        rv.D1_numeric.score * 100,
        rv.D2_modifier.score * 100,
        rv.D3_semantic.score * 100,
        rv.D5_timeseries.score * 100,
    ]
    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=scores + scores[:1],
        theta=axes + axes[:1],
        fill="toself",
        name="4축 위험도",
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
    st.markdown(GRADIENT_BANNER, unsafe_allow_html=True)

    if result is None:
        st.info("사이드바에서 분석을 시작하세요.")
    else:
        v = result["verify"]
        band_color = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🟠", "CRITICAL": "🔴"}

        # 초안 → 최종본 위험도 delta 박스
        if len(v.steps) > 1:
            before = v.steps[0].detection.risk_score
            after  = v.final.detection.risk_score
            if after < before:
                st.success(f"✅ L4 재생성으로 위험도 {before:.1f} → {after:.1f} 감소")
            else:
                st.info("ℹ️ 초안이 이미 기준치 이하 (그린워싱 시연 모드를 켜면 개선 과정을 확인할 수 있습니다)")
        else:
            st.info("ℹ️ 초안이 이미 기준치 이하 (그린워싱 시연 모드를 켜면 개선 과정을 확인할 수 있습니다)")

        st.markdown("### 최종 보고서")
        st.markdown(
            _report_card(v.final_text, kind="final", tag_label="FINAL"),
            unsafe_allow_html=True,
        )
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
    st.markdown(GRADIENT_BANNER, unsafe_allow_html=True)

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
                        st.markdown("**4축 위험 분해**")
                        axis_df = pd.DataFrame([
                            {"축": ax, "점수": f"{sc:.3f}", "설명": detail}
                            for ax, sc, detail in [
                                ("D1 수치오차",    rv.D1_numeric.score,    rv.D1_numeric.detail),
                                ("D2 모호어",      rv.D2_modifier.score,   rv.D2_modifier.detail),
                                ("D3 의미괴리",    rv.D3_semantic.score,   rv.D3_semantic.detail),
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
