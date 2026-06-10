"""ESGenie — K-ESG 공시 보고서 생성·그린워싱 검증·증빙 자동화 AI.

실행:  streamlit run app.py
"""
from __future__ import annotations

import html
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ── 코어 파이프라인 ──────────────────────────────────────────────────────────
from esgenie.config import SETTINGS
from esgenie.dart_client import load_report, search_companies
from esgenie.knowledge.kesg_items import BASIC_28_CODES
from esgenie.layer0_evidence_graph import build_evidence_graph as _v10_build_graph
from esgenie.layer1_extract import extract
from esgenie.layer2_rag import HybridRAG
from esgenie.layer3_detect import detect, risk_band
from esgenie.layer4_verify import verify_and_refine
from esgenie.layer5_audit_trace import build_audit_trace, save_audit_trace
from esgenie.pipeline import _load_industry_stats
from esgenie.llm import CLIENT as LLM_CLIENT

# ── SSOT / OCR 확장 (esgenie.ssot — 구 v15, 메인 패키지로 통합됨) ────────────
from esgenie.ssot import ocr_router, evidence_graph as eg_v15, detector_5axis, audit_trace as at_v15, excel_exporter
from esgenie.ssot.ssot_pipeline import extract_with_ssot, build_rag_with_ssot, ssot_summary

OUT_ROOT     = Path("outputs")
# K-ESG 기본형 28개 기준으로 통일
TARGET_CODES = BASIC_28_CODES  # 정량 + 정성 전체 28개 추적
POLICY_CODES = [                # P축 규정 검증 대상 (정성 항목 위주)
    "P-1-1",
    "E-1-1", "E-1-2", "E-3-3",
    "S-1-1", "S-2-6", "S-4-1", "S-5-1", "S-6-1", "S-7-1", "S-8-1",
    "G-1-1", "G-3-1", "G-4-1", "G-5-1",
]


# ====================================================================
# 스타일
# ====================================================================

st.set_page_config(page_title="ESGenie — K-ESG 공시 AI", layout="wide", page_icon="🌱")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;900&display=swap');
html, body, [data-testid="stAppViewContainer"], [data-testid="stSidebar"] {
    font-family: 'Noto Sans KR', 'Helvetica Neue', sans-serif;
}
section[data-testid="stSidebar"] { background-color: #f0f7f0; }
section[data-testid="stSidebar"], section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] span, section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] h1, section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3, section[data-testid="stSidebar"] h4,
section[data-testid="stSidebar"] li, section[data-testid="stSidebar"] small,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"],
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] *,
section[data-testid="stSidebar"] [data-testid="stWidgetLabel"],
section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] * { color: #1b3a1f !important; }
section[data-testid="stSidebar"] [data-baseweb="select"] > div,
section[data-testid="stSidebar"] [data-baseweb="input"] > div,
section[data-testid="stSidebar"] input, section[data-testid="stSidebar"] textarea {
    background-color: #ffffff !important;
    color: #000000 !important;
    border: 1px solid rgba(46,125,50,0.30) !important; border-radius: 8px !important;
}
section[data-testid="stSidebar"] button[kind="primary"] {
    padding: 14px 24px !important; font-size: 18px !important;
    font-weight: 700 !important; height: auto !important;
    background-color: #4caf50 !important; border-color: #2e7d32 !important;
    box-shadow: 0 2px 6px rgba(46,125,50,0.35); color: #ffffff !important;
}
section[data-testid="stSidebar"] button[kind="primary"]:hover {
    background-color: #2e7d32 !important; border-color: #1b5e20 !important;
}
.esg-report-card {
    background: #ffffff; border: 1px solid #e5e7eb; border-radius: 12px;
    padding: 26px 28px; font-size: 15px; line-height: 1.8; color: #1f2937;
    word-break: keep-all; box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}
.esg-report-card.final { border-left: 3px solid #4caf50; }
.esg-report-tag {
    display: inline-block; font-size: 11px; font-weight: 700;
    letter-spacing: 0.6px; padding: 3px 10px; border-radius: 999px;
    margin-bottom: 14px; text-transform: uppercase;
}
.esg-report-tag.draft { color: #6b7280 !important; background: #f3f4f6 !important; }
.esg-report-tag.final { color: #2e7d32 !important; background: #e8f5e9 !important; }
</style>
""", unsafe_allow_html=True)

GRADIENT = ('<div style="height:6px;background:linear-gradient(90deg,#2e7d32,#4caf50,#a5d6a7);'
            'border-radius:3px;margin:6px 0 20px 0;"></div>')

def _report_card(text: str, kind: str = "draft", tag_label: str | None = None) -> str:
    cls = "esg-report-card final" if kind == "final" else "esg-report-card"
    tag = (f'<span class="esg-report-tag {kind}">{html.escape(tag_label)}</span>'
           if tag_label else "")
    return f'<div class="{cls}">{tag}\n\n{text}\n\n</div>'


# ====================================================================
# 캐시
# ====================================================================

@st.cache_resource
def get_base_rag() -> HybridRAG:
    return HybridRAG()


# ====================================================================
# 사이드바
# ====================================================================

with st.sidebar:
    st.markdown("""
    <div style="background:linear-gradient(135deg,#2e7d32,#4caf50);
                padding:22px 16px;border-radius:12px;text-align:center;
                margin-bottom:18px;box-shadow:0 2px 10px rgba(46,125,50,0.25);">
        <div style="font-size:34px;color:#fff;">🌱</div>
        <div style="color:#fff;font-size:22px;font-weight:700;margin-top:6px;">ESGenie</div>
        <div style="color:rgba(255,255,255,0.9);font-size:12px;margin-top:4px;">
            K-ESG 공시 보고서 생성 · 그린워싱 검증 · 증빙 자동화
        </div>
    </div>""", unsafe_allow_html=True)

    # API 키 상태
    dart_ok      = bool(os.getenv("DART_API_KEY"))
    openai_ok    = bool(os.getenv("OPENAI_API_KEY"))
    anthropic_ok = bool(os.getenv("ANTHROPIC_API_KEY"))
    clova_ok     = bool(os.getenv("CLOVA_OCR_SECRET"))
    st.markdown(
        f"{'🟢' if dart_ok else '🔴'} DART  "
        f"{'🟢' if openai_ok else '🔴'} OpenAI  "
        f"{'🟢' if anthropic_ok else '🔴'} Anthropic  "
        f"{'🟢' if clova_ok else '🔴'} CLOVA"
    )
    st.divider()

    # 회사 검색
    search_q = st.text_input("🔍 회사 검색", placeholder="예: 현대, 포스코, (주)예시")
    corp_code, corp_name, industry = "", "", ""

    if search_q:
        hits = search_companies(search_q)
        if hits:
            opts = [f"{h['corp_name']} ({h['corp_code']})" for h in hits]
            sel  = st.selectbox("검색 결과", opts)
            idx  = opts.index(sel)
            corp_code = hits[idx]["corp_code"]
            corp_name = hits[idx]["corp_name"]
            industry  = hits[idx].get("industry", "")
        else:
            st.caption("DART 미매칭 — 직접 입력")
            corp_name = search_q

    if not corp_name:
        corp_name = st.text_input("회사명 직접 입력")
    if not corp_code:
        corp_code = st.text_input("DART 코드 (없으면 공란)", value="")

    use_dart = st.checkbox("DART 연동", value=bool(corp_code))
    industry = st.selectbox(
        "업종", ["자동차부품", "전자부품", "화학", "금속가공", "식품", "기타"],
        index=["자동차부품","전자부품","화학","금속가공","식품","기타"].index(industry)
              if industry in ["자동차부품","전자부품","화학","금속가공","식품","기타"] else 0,
    )
    report_year = st.number_input("보고 연도", 2020, 2030, 2025)

    area = st.selectbox(
        "분석 영역",
        options=["E", "S", "G"],
        format_func=lambda a: {"E": "🌿 환경 (E)", "S": "🤝 사회 (S)", "G": "🏛 지배구조 (G)"}[a],
    )

    with st.expander("⚙️ 고급 설정", expanded=False):
        threshold      = st.slider("자가 검증 임계치 (위험도 ≤)", 10, 80, 30, 5)
        max_iter       = st.slider("최대 재생성 반복", 1, 5, 3)
        demo_greenwash = st.checkbox(
            "그린워싱 시연 모드",
            value=True,
            help="의도적 과장 생성 → L3/L4 탐지·수정 과정 시연",
        )
        _prof_sel = st.selectbox(
            "K-ESG 프로파일",
            ["자동 판별", "중소기업 기본형 (28)", "전체 (61)"],
            help="자동: 상장코드(6자리 숫자) → 61항목, 그 외 → 기본형 28항목",
        )
        profile_choice = {"자동 판별": None, "중소기업 기본형 (28)": "sme", "전체 (61)": "full"}[_prof_sel]
        llm_judge_opt = st.checkbox(
            "LLM 2차 판정 (하이브리드)",
            value=False,
            help="룰 1차 스크리닝 + LLM 맥락 판정. 키 없으면 mock 판정으로 시연",
        )

    st.divider()
    run_btn = st.button("▶ 분석 시작", type="primary", use_container_width=True,
                        disabled=not corp_name)


# ====================================================================
# ② 증빙 파일 업로드 (메인 영역)
# ====================================================================

st.title("🌱 ESGenie · K-ESG 공시 자동화 AI")
st.caption("DART 공시 + 내부 증빙(전기요금·폐기물 대장·규정집)을 SSOT로 통합 → "
           "K-ESG 보고서 생성 · 4축 그린워싱 검증 · 사내규정 누락 조항 검출 · 대기업 제출용 엑셀 자동화")

st.markdown("#### 📎 내부 증빙 파일 업로드 (선택)")
st.caption("한전 전기요금 고지서 · 도시가스 영수증 · 폐기물 대장 · 안전보건 회의록 · 사내 규정집 (PDF/이미지)")
uploads = st.file_uploader("증빙 파일", type=["pdf","png","jpg","jpeg"], accept_multiple_files=True, label_visibility="collapsed")

if "upload_paths" not in st.session_state:
    st.session_state.upload_paths = {}

if uploads:
    tmp = OUT_ROOT / "_uploads"
    tmp.mkdir(parents=True, exist_ok=True)
    rows = []
    st.session_state.upload_paths = {}   # 새 업로드로 교체
    for uf in uploads:
        p = tmp / uf.name
        p.write_bytes(uf.getbuffer())
        st.session_state.upload_paths[uf.name] = str(p)
        dec = ocr_router.route_document(str(p))
        rows.append({"파일명": uf.name, "채널": dec.channel.value,
                     "문서 유형": dec.doc_type, "신뢰도": f"{dec.confidence:.0%}"})
    st.dataframe(rows, use_container_width=True, hide_index=True)
elif not uploads and st.session_state.upload_paths:
    # 파일 제거 시 초기화
    st.session_state.upload_paths = {}

upload_paths = st.session_state.upload_paths

# ====================================================================
# ③ 설문 입력 — 정성 항목 (OCR/DART로 못 채우는 항목)
# ====================================================================

_SURVEY_ITEMS = [
    ("P-1-1", "ESG 정보를 공시하는 방식이 있습니까?",          "예: 홈페이지, DART, 자체 보고서 등"),
    ("E-1-1", "중장기 환경경영 목표를 수립하였습니까?",          "예: 2030년 탄소 20% 감축 목표 등"),
    ("E-1-2", "환경경영 전담 조직·인력이 있습니까?",            "예: 환경안전팀, ESG 담당자 등"),
    ("E-3-3", "온실가스 배출량에 대한 제3자 검증을 받았습니까?", "예: 검증기관명"),
    ("S-1-1", "사회적 책임 목표를 수립·공시하고 있습니까?",      "예: 산업재해율 목표 등"),
    ("S-2-6", "노동조합 또는 결사의 자유를 보장하고 있습니까?",  "예: 노조 가입률, 노사협의회 등"),
    ("S-4-1", "안전보건 전담 조직·정책이 있습니까?",            "예: 안전보건위원회 운영 등"),
    ("S-5-1", "인권정책을 수립·시행하고 있습니까?",             "예: 인권경영 선언, 고충처리 절차 등"),
    ("S-6-1", "협력사 ESG 관리 기준·프로그램이 있습니까?",      "예: 협력사 행동강령, 평가 절차 등"),
    ("S-7-1", "전략적 사회공헌(CSR) 활동을 하고 있습니까?",     "예: 지역사회 프로그램, 기부 등"),
    ("S-8-1", "정보보호 체계(ISMS 등)를 구축하였습니까?",       "예: ISMS 인증, 정보보호 정책 등"),
    ("G-1-1", "이사회에서 ESG 안건을 정기적으로 상정합니까?",    "예: 연 2회 이상 ESG 보고 등"),
    ("G-3-1", "주주총회 소집 공고를 법정 기간 내에 하고 있습니까?", "예: 2주 전 공고 등"),
    ("G-4-1", "윤리규범 위반사항 공시 체계가 있습니까?",         "예: 윤리헌장, 내부신고 채널 등"),
    ("G-5-1", "내부감사 부서 또는 기구가 설치되어 있습니까?",    "예: 감사위원회, 내부감사팀 등"),
]

st.markdown("#### 📝 정성 항목 설문")
st.caption("DART·OCR로 확인 어려운 정책·체계 항목입니다. 해당 항목만 입력하면 됩니다.")

if "survey_answers" not in st.session_state:
    st.session_state.survey_answers = {}

with st.expander("설문 입력 펼치기", expanded=False):
    for code, question, hint in _SURVEY_ITEMS:
        prev = st.session_state.survey_answers.get(code, {"yn": "미입력", "text": ""})
        col1, col2 = st.columns([1, 2])
        with col1:
            yn = st.radio(
                f"`{code}` {question}",
                ["미입력", "예", "아니오"],
                index=["미입력", "예", "아니오"].index(prev["yn"]),
                key=f"survey_yn_{code}",
                horizontal=True,
            )
        with col2:
            txt = st.text_input(
                f"상세 내용 ({hint})",
                value=prev["text"],
                key=f"survey_txt_{code}",
                label_visibility="collapsed",
                placeholder=hint,
            )
        if yn != "미입력" or txt:
            st.session_state.survey_answers[code] = {"yn": yn, "text": txt}
        elif code in st.session_state.survey_answers:
            del st.session_state.survey_answers[code]

survey_answers = st.session_state.survey_answers
_answered = sum(1 for v in survey_answers.values() if v["yn"] != "미입력")
if _answered:
    st.caption(f"✅ {_answered}개 항목 입력됨")


# ====================================================================
# 파이프라인
# ====================================================================

def _run_pipeline() -> dict:
    # L0-A: OCR 추출
    extractions = []
    _up = st.session_state.get("upload_paths", {})
    for fname, path in _up.items():
        try:
            dec = ocr_router.route_document(path)
            ext = ocr_router.extract_document(path, dec)
            ext.source_file = fname
            extractions.append(ext)
        except Exception as e:
            st.warning(f"OCR 처리 실패 [{fname}]: {e}")

    # L0-B: 설문 응답 → OcrExtraction (정성 조항으로 변환)
    _survey = st.session_state.get("survey_answers", {})
    if _survey:
        from esgenie.ssot.ocr_router import OcrExtraction, ExtractedClause, DocChannel
        clauses = []
        for code, ans in _survey.items():
            if ans["yn"] == "미입력":
                continue
            text = f"[설문] {ans['yn']}" + (f": {ans['text']}" if ans['text'] else "")
            clauses.append(ExtractedClause(
                section=code,
                text=text,
                kesg_code_guess=code,
                page=1,
            ))
        if clauses:
            survey_ext = OcrExtraction(
                source_file="survey_form",
                channel=DocChannel.UNSTRUCTURED,
                doc_type="survey",
                clauses=clauses,
                router_meta={"source": "survey"},
            )
            extractions.append(survey_ext)

    # DART 로드
    dart_report = None
    if use_dart and corp_code:
        try:
            dart_report = load_report(corp_code, report_year=int(report_year))
        except Exception as e:
            st.warning(f"DART 로드 실패: {e}")

    # L0: SSOT 통합 그래프
    ssot_graph = eg_v15.build_unified_graph(
        dart_report, extractions,
        corp_code=corp_code or "LOCAL",
        corp_name=(dart_report.corp_name if dart_report else corp_name) or corp_name,
        report_year=int(report_year),
    )

    # L1 + L2 (SSOT 연결)
    l1_result = None
    rag = get_base_rag()
    if dart_report is not None:
        l1_result = extract_with_ssot(dart_report, ssot_graph, profile=profile_choice)
        build_rag_with_ssot(rag, dart_report, ssot_graph)

    # L0-C: 설문 응답 → l1_result.mapped 직접 추가
    _survey = st.session_state.get("survey_answers", {})
    if _survey and l1_result is not None:
        from esgenie.knowledge.kesg_items import by_code as _by_code
        for code, ans in _survey.items():
            if ans["yn"] == "미입력" or code in l1_result.mapped:
                continue
            item = _by_code(code)
            if not item:
                continue
            l1_result.mapped[code] = {
                "code": code, "name": item.name, "area": item.area,
                "category": item.category, "data_type": item.data_type,
                "value": ans["yn"], "unit": "",
                "note": ans["text"] or None,
                "evidence_node_ids": [f"survey_{code}"],
            }
            if code in l1_result.missing:
                l1_result.missing.remove(code)

    # L4: 보고서 생성 + 그린워싱 검증 (DART 있을 때)
    verify    = None
    v10_trace = None
    if dart_report is not None:
        industry_stats = _load_industry_stats(dart_report.industry)
        verify = verify_and_refine(
            dart_report, area, rag,
            threshold=float(threshold),
            max_iter=int(max_iter),
            demo_greenwash=bool(demo_greenwash),
            evidence_graph=ssot_graph,
            industry_stats=industry_stats,
            llm_judge=bool(llm_judge_opt),
        )
        extraction_for_trace = l1_result or extract(
            dart_report, evidence_graph=ssot_graph, profile=profile_choice)
        v10_trace = build_audit_trace(
            report=dart_report, area=area, verification=verify,
            extraction=extraction_for_trace,
            evidence_graph=ssot_graph,
            industry_stats=industry_stats,
            llm_judge=bool(llm_judge_opt),
        )
        save_audit_trace(v10_trace)

    # L3 (v15): 4축 리스크 항목별 계산
    d1_scores: dict[str, float] = {}
    risk_rows: list[dict] = []
    for code in TARGET_CODES:
        nodes = ssot_graph.nodes_by_metric(code)
        if not nodes:
            continue
        n = nodes[-1]
        axes = detector_5axis.detect_risk_axes(
            f"{code} 값은 {n.value}{n.unit}이다.", code, ssot_graph)
        d1_scores[code] = axes["D1"].score
        risk_rows.append({
            "K-ESG 코드":  code,
            "값":          f"{n.value} {n.unit}",
            "D1 수치":     round(axes["D1"].score, 3),
            "D2 수식어":   round(axes["D2"].score, 3),
            "D3 의미":     round(axes["D3"].score, 3),
            "D5 시계열":   round(axes["D5"].score, 3),
            "종합 위험도": round(axes["aggregate"].score, 3),
        })

    # P축: 규정 검증
    active_codes = list({
        *[c for c in POLICY_CODES if ssot_graph.text_nodes_by_code(c) or ssot_graph.nodes_by_metric(c)],
        "S-3-1", "E-1-1",
    })
    policy_results, drafts = [], {}
    for code in active_codes:
        res = detector_5axis.audit_policy_documents(code, ssot_graph, LLM_CLIENT)
        policy_results.append(res)
        if not res.passed:
            drafts[code] = detector_5axis.draft_missing_policy(
                code, res, corp_name, industry, LLM_CLIENT)

    # L5: DataPoint + Excel
    dps      = at_v15.build_data_points(ssot_graph, d1_scores, target_codes=TARGET_CODES)
    v15_trace = at_v15.build_audit_trace_v15(
        corp_code or "LOCAL", corp_name, dps, policy_results)
    out_dir  = OUT_ROOT / f"{corp_code or corp_name}_{int(report_year)}"
    paths    = excel_exporter.export_datasheet(v15_trace, out_dir, uploaded_files=upload_paths)

    return {
        "ssot_graph":   ssot_graph,
        "l1_result":    l1_result,
        "verify":       verify,
        "v10_trace":    v10_trace,
        "v15_trace":    v15_trace,
        "risk_rows":    risk_rows,
        "drafts":       drafts,
        "policy":       policy_results,
        "paths":        paths,
        "dart_report":  dart_report,
    }


# ====================================================================
# 실행 & 결과
# ====================================================================

if "result" not in st.session_state:
    st.session_state.result = None

if run_btn:
    with st.spinner("증빙 파싱 → SSOT 통합 → 보고서 생성 → 그린워싱 검증 → 규정 심사 → 서류철 생성…"):
        st.session_state.result = _run_pipeline()

result = st.session_state.result

# 탭
tabs = st.tabs([
    "🏠 홈",
    "🗂 증빙 & SSOT",
    "📊 공시 진단",
    "📝 보고서 생성",
    "✅ 검증 & 최종본",
    "📋 규정 검증",
    "🔍 감사 추적 & HITL",
    "🧪 벤치마크",
])
tab_home, tab_ssot, tab_diag, tab_draft, tab_verify, tab_policy, tab_audit, tab_bench = tabs


# ── 홈 ──────────────────────────────────────────────────────────────────────
with tab_home:
    st.markdown("## ESGenie · 6-Layer K-ESG 공시 자동화")
    st.markdown(GRADIENT, unsafe_allow_html=True)

    if result is None:
        st.info("👈 사이드바에서 회사를 선택하고 **분석 시작**을 눌러주세요.")
    else:
        v       = result["verify"]
        ssot    = result["ssot_graph"]
        summ    = ssot_summary(ssot)
        v15t    = result["v15_trace"]

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("SSOT 노드",  summ["total_nodes"])
        c2.metric("정량 항목",  v15t.summary["data_point_count"])
        c3.metric("증빙 확인률", f"{v15t.summary['verified_ratio']*100:.0f}%")
        c4.metric("규정 통과",  f"{v15t.summary['policy_pass']}/{v15t.summary['policy_total']}")
        if v:
            band_emoji = {"LOW":"🟢","MEDIUM":"🟡","HIGH":"🟠","CRITICAL":"🔴"}.get(v.final_band,"")
            c5.metric("그린워싱 위험도", f"{v.final_score:.1f}", f"{band_emoji} {v.final_band}", delta_color="off")

        if v:
            st.markdown("#### 보고서 미리보기")
            import re as _re
            preview = _re.sub(r"^#{1,6}\s*","", v.final_text[:300], flags=_re.MULTILINE).strip()
            if len(v.final_text) > 300:
                preview += "..."
            with st.container(border=True):
                st.write(preview)


# ── 증빙 & SSOT ─────────────────────────────────────────────────────────────
with tab_ssot:
    st.markdown("## 🗂 증빙 & 단일 진실 원천(SSOT)")
    st.markdown(GRADIENT, unsafe_allow_html=True)

    if result is None:
        st.info("분석을 시작하세요.")
    else:
        ssot  = result["ssot_graph"]
        summ  = ssot_summary(ssot)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("DART 노드",       summ["by_origin"].get("dart", 0))
        c2.metric("OCR 정형",        summ["by_origin"].get("ocr_structured", 0))
        c3.metric("OCR 비정형",      summ["by_origin"].get("ocr_unstructured", 0))
        c4.metric("정성 조항",        summ["text_nodes"])
        st.caption(f"시계열 엣지 {summ['edges']}개 · 교차검증 엣지 {summ['cross_check_edges']}개")

        node_rows = [
            {"노드 ID": n.id, "K-ESG": n.metric, "값": f"{n.value} {n.unit}",
             "연도": n.period, "출처": n.origin,
             "증빙 파일": n.source_file or "—", "신뢰도": round(n.confidence, 2)}
            for n in ssot.nodes.values()
        ]
        if node_rows:
            st.dataframe(node_rows, use_container_width=True, hide_index=True)

        if ssot.text_nodes:
            st.markdown("#### 정성 조항 노드 (규정집·회의록)")
            text_rows = [
                {"K-ESG": t.kesg_code or "—", "섹션": t.section,
                 "내용": t.text[:60] + ("…" if len(t.text)>60 else ""),
                 "파일": t.source_file, "페이지": t.page}
                for t in ssot.text_nodes.values()
            ]
            st.dataframe(text_rows, use_container_width=True, hide_index=True)


# ── 공시 진단 ────────────────────────────────────────────────────────────────
with tab_diag:
    st.markdown("## 📊 공시 진단")
    st.markdown(GRADIENT, unsafe_allow_html=True)

    if result is None or result["l1_result"] is None:
        st.info("DART 연동 후 분석을 시작하면 K-ESG 커버리지를 확인할 수 있습니다.")
    else:
        ex = result["l1_result"]
        ssot = result["ssot_graph"]

        # 프로파일 배지
        _badge = "🏢" if ex.profile == "full" else "🏭"
        _extra = f" · 프로파일 외 추가 공시 {len(ex.beyond_profile)}개" if ex.beyond_profile else ""
        st.markdown(
            f"{_badge} **프로파일: {ex.profile_label}** · "
            f"커버리지 **{ex.coverage_pct:.1f}%**{_extra}"
        )
        st.caption("커버리지 분모는 프로파일 기준 — 해당 기업 규모에 적용 가능한 항목만 평가")

        area_labels = {"P": "정보공시", "E": "환경", "S": "사회", "G": "지배구조"}
        cov_data = [
            {"영역": f"{k} · {area_labels[k]}",
             "공시 항목": v["present"], "전체 항목": v["total"],
             "커버리지": round(100*v["present"]/v["total"], 1)}
            for k, v in ex.by_area.items()
        ]
        cov_df = pd.DataFrame(cov_data)

        col_chart, col_table = st.columns([3, 2])
        with col_chart:
            fig = go.Figure(go.Bar(
                x=cov_df["영역"], y=cov_df["커버리지"],
                marker_color=["#4CAF50" if v>=70 else "#FFC107" if v>=50 else "#F44336"
                              for v in cov_df["커버리지"]],
                text=[f"{v}%" for v in cov_df["커버리지"]], textposition="outside",
            ))
            fig.update_layout(title="영역별 K-ESG 커버리지",
                              yaxis=dict(range=[0,110], title="커버리지 (%)"),
                              height=300, margin=dict(t=40,b=20))
            st.plotly_chart(fig, use_container_width=True)
        with col_table:
            st.dataframe(cov_df, hide_index=True, use_container_width=True)

        st.markdown("#### Evidence 노드")
        node_df = pd.DataFrame([
            {"K-ESG": n.metric, "값": n.value, "단위": n.unit,
             "연도": n.period, "출처": n.origin, "신뢰도": round(n.confidence,2)}
            for n in sorted(ssot.nodes.values(), key=lambda x: x.metric)
        ])
        st.dataframe(node_df, hide_index=True, use_container_width=True)

        # 프로파일 기준 공시/누락 (분모 = 프로파일 항목 수)
        from esgenie.knowledge.kesg_items import items_for_profile
        _prof_items = items_for_profile(ex.profile)
        _prof_codes = {it.code for it in _prof_items}
        _n_prof     = len(_prof_items)
        _missing_p  = [c for c in ex.missing if c in _prof_codes]
        _present_p  = [c for c in ex.mapped
                       if c in _prof_codes and not ex.mapped[c].get("beyond_profile")]
        t_present, t_missing = st.tabs([
            f"✅ 공시 항목 ({len(_present_p)}개 / {_n_prof}개)",
            f"⚠️ 누락 항목 ({len(_missing_p)}개 / {_n_prof}개)",
        ])
        with t_present:
            def _source_tag(v):
                ids = v.get("evidence_node_ids", [])
                if any(i.startswith("survey_") for i in ids): return "📝 설문"
                if any("ocr" in i for i in ids): return "📄 OCR"
                return "🏛 DART"
            st.dataframe(pd.DataFrame([
                {"코드": v["code"], "영역": v["area"], "항목명": v["name"],
                 "값": v["value"], "단위": v.get("unit") or "-", "출처": _source_tag(v)}
                for v in ex.mapped.values()
                if v["code"] in _prof_codes and not v.get("beyond_profile")
            ]), hide_index=True, use_container_width=True)
        with t_missing:
            st.dataframe(pd.DataFrame([
                {"코드": it.code, "영역": it.area, "항목명": it.name, "유형": it.data_type}
                for it in _prof_items if it.code in ex.missing
            ]), hide_index=True, use_container_width=True)
        if ex.beyond_profile:
            with st.expander(f"➕ 프로파일 외 추가 공시 ({len(ex.beyond_profile)}개) — 커버리지 미반영"):
                st.dataframe(pd.DataFrame([
                    {"코드": v["code"], "영역": v["area"], "항목명": v["name"],
                     "값": v["value"], "단위": v.get("unit") or "-"}
                    for c, v in ex.mapped.items() if v.get("beyond_profile")
                ]), hide_index=True, use_container_width=True)


# ── 보고서 생성 ──────────────────────────────────────────────────────────────
with tab_draft:
    st.markdown("## 📝 보고서 초안 생성")
    st.markdown(GRADIENT, unsafe_allow_html=True)

    if result is None or result["verify"] is None:
        st.info("DART 연동 후 분석을 시작하면 RAG 기반 보고서 초안이 생성됩니다.")
    else:
        v = result["verify"]
        first = v.steps[0]
        area_label = {"E":"🌿 환경","S":"🤝 사회","G":"🏛 지배구조"}[area]

        st.info("L0 SSOT + L2 RAG 기반 초안. 그린워싱 시연 모드에서는 의도적 과장이 포함됩니다.")
        st.markdown(f"#### {area_label} 영역 초안")
        st.markdown(_report_card(first.generation.text, "draft", "DRAFT"), unsafe_allow_html=True)
        if first.generation.used_mock_llm:
            st.caption("⚠️ Mock LLM으로 생성 (OPENAI_API_KEY 설정 시 실 API 사용)")

        with st.expander("📚 RAG 검색 근거"):
            ctx = first.generation.context
            c1, c2, c3 = st.columns(3)
            with c1:
                st.markdown("**K-ESG 가이드라인**")
                for doc, score in ctx.kesg_hits:
                    st.caption(f"[{score:.3f}] {doc.text[:80]}...")
            with c2:
                st.markdown("**업종 벤치마크**")
                for doc, score in ctx.industry_hits:
                    st.caption(f"[{score:.3f}] {doc.text[:80]}...")
            with c3:
                st.markdown("**자사 DART 원문 + OCR 증빙**")
                for doc, score in ctx.corp_hits:
                    st.caption(f"[{score:.3f}] {doc.text[:80]}...")


# ── 검증 & 최종본 ─────────────────────────────────────────────────────────────
with tab_verify:
    st.markdown("## ✅ 검증 & 최종본")
    st.markdown(GRADIENT, unsafe_allow_html=True)

    if result is None or result["verify"] is None:
        st.info("DART 연동 후 분석을 시작하면 검증 결과를 확인할 수 있습니다.")
    else:
        v = result["verify"]
        band_emoji = {"LOW":"🟢","MEDIUM":"🟡","HIGH":"🟠","CRITICAL":"🔴"}

        if len(v.steps) > 1:
            before, after = v.steps[0].detection.risk_score, v.final.detection.risk_score
            if after < before:
                st.success(f"✅ L4 재생성으로 위험도 {before:.1f} → {after:.1f} 감소")
            else:
                st.info("ℹ️ 초안이 이미 기준치 이하")
        else:
            st.info("ℹ️ 초안이 이미 기준치 이하")

        st.markdown("### 최종 보고서")
        st.markdown(_report_card(v.final_text, "final", "FINAL"), unsafe_allow_html=True)
        st.caption(
            f"{band_emoji.get(v.final_band,'')} 위험도 **{v.final_score:.1f}** ({v.final_band}) · "
            f"검증 {v.iterations_used}회 · "
            + ("수렴 완료 ✅" if v.converged else "HITL 필요 ⚠️" if v.hitl_required else "미수렴 ⚠️")
        )

        # 4축 레이더
        final_rv = v.final.detection.risk_vector
        if final_rv is not None:
            axes   = ["D1 수치오차","D2 모호어","D3 의미괴리","D5 시계열모순"]
            scores = [final_rv.D1_numeric.score*100, final_rv.D2_modifier.score*100,
                      final_rv.D3_semantic.score*100, final_rv.D5_timeseries.score*100]
            fig = go.Figure()
            fig.add_trace(go.Scatterpolar(
                r=scores+scores[:1], theta=axes+axes[:1],
                fill="toself", line_color="#FF6B6B", fillcolor="rgba(255,107,107,0.2)",
            ))
            fig.update_layout(polar=dict(radialaxis=dict(visible=True,range=[0,100])),
                              showlegend=False, height=300, margin=dict(t=30,b=20),
                              title=f"4축 위험 분해 (종합 {final_rv.risk_score*100:.1f})")
            st.plotly_chart(fig, use_container_width=True, key="radar_final")

        # 위험도 추이 차트
        if len(v.steps) > 1:
            prog_df = pd.DataFrame([
                {"반복": "초안" if s.iteration==0 else f"{s.iteration}차 재생성",
                 "위험도": s.detection.risk_score}
                for s in v.steps
            ])
            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(x=prog_df["반복"], y=prog_df["위험도"],
                           mode="lines+markers", line=dict(color="#FF6B6B",width=2)))
            fig2.add_hline(y=v.metadata["threshold"], line_dash="dash", line_color="#4CAF50",
                           annotation_text=f"목표 임계치 ({v.metadata['threshold']})")
            fig2.update_layout(height=220, yaxis=dict(title="위험도",range=[0,105]),
                               margin=dict(t=10,b=10))
            st.plotly_chart(fig2, use_container_width=True, key="progress")

        # OCR 기반 4축 리스크 테이블
        if result["risk_rows"]:
            st.markdown("#### K-ESG 항목별 4축 리스크 (증빙 기반)")
            df = pd.DataFrame(result["risk_rows"])
            def _color(val):
                if isinstance(val, float):
                    if val >= 0.7: return "background-color:#FFC7CE"
                    if val >= 0.4: return "background-color:#FFEB9C"
                return ""
            st.dataframe(df.style.map(_color, subset=["종합 위험도"]),
                         use_container_width=True, hide_index=True)


# ── 규정 검증 ─────────────────────────────────────────────────────────────────
with tab_policy:
    st.markdown("## 📋 P축 — 사내 규정 필수 조항 검증")
    st.markdown(GRADIENT, unsafe_allow_html=True)
    st.caption("K-ESG 가이드라인 + 중대재해처벌법·개인정보보호법·공정거래법 기준")

    if result is None:
        st.info("분석을 시작하세요.")
    else:
        for pa in result["v15_trace"].policy_audit:
            code  = pa["kesg_code"]
            badge = "✅ 통과" if pa["passed"] else "⚠️ 보완 필요"
            passed_n = sum(1 for f in pa["findings"] if f["status"] == "met")
            with st.expander(f"**{code}** — {badge}  ({passed_n}/{len(pa['findings'])}개 충족)"):
                for f in pa["findings"]:
                    icon = {"met":"✅","insufficient":"⚠️","missing":"❌"}.get(f["status"],"—")
                    st.markdown(f"{icon} **{f['requirement']}**")
                    if f["status"] != "met":
                        st.markdown(f"  - 갭: {f['gap_comment']}")
                        st.markdown(f"  - 보완: {f['suggested_fix']}")
                if pa["source_files"]:
                    st.caption(f"검토 파일: {', '.join(pa['source_files'])}")

        if result["drafts"]:
            st.markdown("#### 누락 조항 표준 조문 초안 (LLM 자동 생성)")
            for code, draft in result["drafts"].items():
                with st.expander(f"{code} — 보완 초안"):
                    st.code(draft, language="markdown")


# ── 감사 추적 & HITL ──────────────────────────────────────────────────────────
with tab_audit:
    st.markdown("## 🔍 감사 추적 & HITL 패널")
    st.markdown(GRADIENT, unsafe_allow_html=True)

    if result is None:
        st.info("분석을 시작하세요.")
    else:
        v10t  = result["v10_trace"]
        paths = result["paths"]

        # 다운로드
        dl1, dl2, dl3 = st.columns(3)
        with dl1:
            with open(paths["xlsx"], "rb") as fh:
                st.download_button("📥 K-ESG 데이터시트 (.xlsx)", fh.read(),
                    file_name="ESG_DataSheet_대기업제출용.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        with dl2:
            with open(paths["audit_json"], "rb") as fh:
                st.download_button("📥 감사 추적 (.json)", fh.read(),
                    file_name="audit_trace.json", mime="application/json")
        if v10t:
            with dl3:
                trace_json = json.dumps(v10t.to_dict(), ensure_ascii=False, indent=2)
                st.download_button("📥 문장 단위 추적 (.json)", trace_json.encode(),
                    file_name=f"audit_trace_{v10t.ticker}_{v10t.area}.json",
                    mime="application/json")

        st.info(f"📁 증빙 서류철: `{paths['evidence_dir']}`")

        # DataSheet 미리보기
        v15t = result["v15_trace"]
        if v15t.data_points:
            st.markdown("#### 정량 데이터시트 미리보기")
            st.dataframe([dp.to_dict() for dp in v15t.data_points],
                         use_container_width=True, hide_index=True)

        # 문장별 HITL (DART 보고서 있을 때만)
        if v10t:
            st.divider()
            st.markdown("#### 문장별 위험 분석 & HITL 판정")
            s = v10t.summary
            sm1, sm2, sm3, sm4 = st.columns(4)
            sm1.metric("총 문장", s["total_sentences"])
            sm2.metric("HITL 필요", s["hitl_count"])
            sm3.metric("평균 위험도", f"{s['avg_risk_score']:.3f}")
            sm4.metric("수렴", "✅" if s["converged"] else "❌")

            if "hitl_decisions" not in st.session_state:
                st.session_state.hitl_decisions = {}

            for sent in v10t.sentences:
                rv = sent.risk_vector
                risk_score = rv.risk_score * 100 if rv else 0.0
                level = rv.level if rv else "low"
                color = {"low":"🟢","medium":"🟡","high":"🔴"}.get(level,"⚪")

                with st.expander(
                    f"{color} [{sent.sentence_id}] {sent.sentence_text[:60]}... (위험도 {risk_score:.1f})",
                    expanded=(level == "high"),
                ):
                    col_text, col_meta = st.columns([3, 2])
                    with col_text:
                        st.markdown("**문장 원문**")
                        st.write(sent.sentence_text)
                        if rv is not None:
                            st.markdown("**4축 위험 분해**")
                            st.dataframe(pd.DataFrame([
                                {"축": ax, "점수": f"{sc:.3f}", "설명": detail}
                                for ax, sc, detail in [
                                    ("D1 수치오차",   rv.D1_numeric.score,   rv.D1_numeric.detail),
                                    ("D2 모호어",     rv.D2_modifier.score,  rv.D2_modifier.detail),
                                    ("D3 의미괴리",   rv.D3_semantic.score,  rv.D3_semantic.detail),
                                    ("D5 시계열모순", rv.D5_timeseries.score,rv.D5_timeseries.detail),
                                ]
                            ]), hide_index=True, use_container_width=True)
                    with col_meta:
                        st.markdown("**K-ESG 연결**")
                        st.code(sent.kesg_item_id or "미매핑")
                        st.markdown("**HITL 상태**")
                        st.badge(sent.hitl_status,
                                 color="red" if sent.hitl_status=="HITL_REQUIRED" else "green")

                    if level in ("medium", "high"):
                        st.markdown("---")
                        st.markdown("**HITL 판정:**")
                        bc = st.columns(3)
                        current = st.session_state.hitl_decisions.get(sent.sentence_id, "")
                        with bc[0]:
                            if st.button("✅ 승인", key=f"approve_{sent.sentence_id}"):
                                st.session_state.hitl_decisions[sent.sentence_id] = "approved"
                                st.rerun()
                        with bc[1]:
                            if st.button("✏️ 수정 필요", key=f"edit_{sent.sentence_id}"):
                                st.session_state.hitl_decisions[sent.sentence_id] = "needs_edit"
                                st.rerun()
                        with bc[2]:
                            if st.button("🚫 무시", key=f"ignore_{sent.sentence_id}"):
                                st.session_state.hitl_decisions[sent.sentence_id] = "ignored"
                                st.rerun()
                        if current:
                            label_map = {"approved":"✅ 승인됨","needs_edit":"✏️ 수정 필요","ignored":"🚫 무시됨"}
                            st.success(f"판정: {label_map.get(current, current)}")

            decisions = st.session_state.hitl_decisions
            if decisions:
                st.divider()
                st.markdown("### HITL 판정 결과 요약")
                st.dataframe(pd.DataFrame([{"문장 ID": sid, "판정": d}
                             for sid, d in decisions.items()]),
                             hide_index=True, use_container_width=True)


# ── 벤치마크 ─────────────────────────────────────────────────────────────────
with tab_bench:
    st.markdown("## 🧪 그린워싱 검출 벤치마크")
    st.markdown(GRADIENT, unsafe_allow_html=True)

    from esgenie.benchmark import format_report as _bench_format
    from esgenie.benchmark import load_benchmark as _bench_load
    from esgenie.benchmark import run_benchmark as _bench_run

    _DET_LABELS = {"rule": "룰 단독", "hybrid": "하이브리드 (룰+LLM)", "llm_only": "LLM 단독"}

    try:
        _bench_data = _bench_load()
        _cases = _bench_data["cases"]
    except Exception as e:
        st.error(f"벤치마크 데이터셋 로드 실패: {e}")
        _cases = []

    if _cases:
        from collections import Counter as _Counter
        _cats = _Counter(c["category"] for c in _cases)
        _gw   = sum(1 for c in _cases if c["label"] == "greenwash")
        b1, b2, b3 = st.columns(3)
        b1.metric("라벨링 문장", len(_cases))
        b2.metric("그린워싱 / 정상", f"{_gw} / {len(_cases) - _gw}")
        b3.metric("카테고리", len(_cats))
        st.caption(" · ".join(f"{k} {v}" for k, v in sorted(_cats.items())))

        if SETTINGS.use_mock_llm:
            st.warning("⚠ LLM 키 미설정 — mock 판정으로 실행됩니다. "
                       "결과는 아키텍처 데모용이며 성능 주장에는 실키 결과를 사용하세요.")

        if st.button("▶ 벤치마크 실행 (룰 vs 하이브리드 vs LLM 단독)", type="primary"):
            with st.spinner("50문장 × 3검출기 평가 중…"):
                st.session_state.bench_reports = _bench_run(["rule", "hybrid", "llm_only"])

        _reports = st.session_state.get("bench_reports")
        if _reports:
            # 종합 지표
            st.markdown("#### 종합 지표")
            _mrows = []
            for name, rep in _reports.items():
                m = rep.metrics()
                _mrows.append({
                    "검출기": _DET_LABELS.get(name, name),
                    "Precision": m["precision"], "Recall": m["recall"],
                    "F1": m["f1"], "Accuracy": m["accuracy"],
                    "LLM 호출": m["llm_calls"],
                })
            st.dataframe(pd.DataFrame(_mrows), hide_index=True, use_container_width=True)

            # F1 비교 차트
            fig = go.Figure()
            for metric_name in ("precision", "recall", "f1"):
                fig.add_bar(
                    name=metric_name.capitalize(),
                    x=[_DET_LABELS.get(n, n) for n in _reports],
                    y=[rep.metrics()[metric_name] for rep in _reports.values()],
                )
            fig.update_layout(barmode="group", height=320,
                              yaxis=dict(range=[0, 1.05], title="점수"),
                              margin=dict(t=30, b=20),
                              title="검출기별 Precision / Recall / F1")
            st.plotly_chart(fig, use_container_width=True)

            # 카테고리별 정답률
            st.markdown("#### 카테고리별 정답률")
            _cat_names = sorted({c.category for rep in _reports.values() for c in rep.cases})
            _crows = []
            for cat in _cat_names:
                row = {"카테고리": cat}
                for name, rep in _reports.items():
                    bc = rep.by_category().get(cat, {})
                    row[_DET_LABELS.get(name, name)] = f"{bc.get('correct', 0)}/{bc.get('total', 0)}"
                _crows.append(row)
            st.dataframe(pd.DataFrame(_crows), hide_index=True, use_container_width=True)
            st.caption("backed_modifier(근거 수반 수식어)·future_plan(미래 계획)이 "
                       "룰 단독의 구조적 오탐 영역 — 하이브리드가 LLM 맥락 판정으로 해소")

            # 오답 상세
            st.markdown("#### 오답 상세")
            for name, rep in _reports.items():
                wrong = [c for c in rep.cases if not c.correct]
                with st.expander(f"{_DET_LABELS.get(name, name)} — 오답 {len(wrong)}건"):
                    if wrong:
                        st.dataframe(pd.DataFrame([
                            {"ID": c.case_id, "카테고리": c.category,
                             "유형": "오탐(FP)" if c.label == "clean" else "미탐(FN)",
                             "점수": round(c.risk_score, 3), "비고": c.detail[:60]}
                            for c in wrong
                        ]), hide_index=True, use_container_width=True)
                    else:
                        st.success("오답 없음")

            # 리포트 다운로드
            _md = _bench_format(_reports, n_cases=len(_cases))
            st.download_button("📥 벤치마크 리포트 (.md)", _md.encode(),
                               file_name="benchmark_report.md", mime="text/markdown")
