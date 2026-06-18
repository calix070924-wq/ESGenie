"""ESGenie — K-ESG 공시 보고서 생성·그린워싱 검증·증빙 자동화 AI.

실행:  streamlit run app.py
"""
from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

# ── 코어 파이프라인 ──────────────────────────────────────────────────────────
from esgenie.config import SETTINGS
from esgenie.dart_client import search_companies
from esgenie.pipeline import run as run_pipeline
from esgenie.ui.tabs import (
    render_audit_tab,
    render_benchmark_tab,
    render_diag_tab,
    render_draft_tab,
    render_home_tab,
    render_policy_tab,
    render_ssot_tab,
    render_supplychain_tab,
    render_verify_tab,
)

# ── SSOT / OCR 확장 (esgenie.ssot — 구 v15, 메인 패키지로 통합됨) ────────────
from esgenie.ssot import ocr_router

OUT_ROOT     = Path("outputs")


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
    azure_ocr_ok = bool(os.getenv("AZURE_DOC_INTEL_ENDPOINT")) and bool(os.getenv("AZURE_DOC_INTEL_KEY"))
    st.markdown(
        f"{'🟢' if dart_ok else '🔴'} DART  "
        f"{'🟢' if openai_ok else '🔴'} OpenAI  "
        f"{'🟢' if anthropic_ok else '🔴'} Anthropic  "
        f"{'🟢' if azure_ocr_ok else '🔴'} Azure OCR"
    )

    # 임베딩 백엔드 — 조용한 폴백을 보이게 (환경별 D3 품질 변동 방지)
    from esgenie.embeddings import embedding_backend as _emb_backend
    _eb = _emb_backend()
    if _eb == "sbert":
        st.caption("🟢 임베딩: SBERT (정상)")
    else:
        st.caption("🟡 임베딩: 해시 폴백 — D3 품질 저하. "
                   "`pip install sentence-transformers faiss-cpu` 권장")
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

def _run_pipeline():
    return run_pipeline(
        corp_code=corp_code,
        corp_name=corp_name,
        industry=industry,
        report_year=int(report_year),
        use_dart=bool(use_dart),
        evidence_files=upload_paths,
        survey_answers=st.session_state.get("survey_answers", {}),
        areas=[area],
        threshold=float(threshold),
        max_iter=int(max_iter),
        demo_greenwash=bool(demo_greenwash),
        llm_judge=bool(llm_judge_opt),
        export_outputs=True,
        profile=profile_choice,
    )


# ====================================================================
# 실행 & 결과
# ====================================================================

if "result" not in st.session_state:
    st.session_state.result = None

if run_btn:
    with st.spinner("증빙 파싱 → SSOT 통합 → 보고서 생성 → 그린워싱 검증 → 규정 심사 → 서류철 생성…"):
        st.session_state.result = _run_pipeline()

result = st.session_state.result
if result is not None and not hasattr(result, "sections"):
    st.session_state.result = None
    result = None
active_area = area
if result is not None and area not in result.sections and result.requested_areas:
    active_area = result.requested_areas[0]

# 탭
tabs = st.tabs([
    "🏠 홈",
    "🗂 증빙 & SSOT",
    "📊 공시 진단",
    "📝 보고서 생성",
    "✅ 검증 & 최종본",
    "📋 규정 검증",
    "🔍 감사 추적 & HITL",
    "📤 실사 응답서",
    "🧪 벤치마크",
])
(tab_home, tab_ssot, tab_diag, tab_draft, tab_verify, tab_policy,
 tab_audit, tab_supplychain, tab_bench) = tabs


# ── 홈 ──────────────────────────────────────────────────────────────────────
with tab_home:
    render_home_tab(result, active_area, GRADIENT)


# ── 증빙 & SSOT ─────────────────────────────────────────────────────────────
with tab_ssot:
    render_ssot_tab(result, GRADIENT)


# ── 공시 진단 ────────────────────────────────────────────────────────────────
with tab_diag:
    render_diag_tab(result, GRADIENT)


# ── 보고서 생성 ──────────────────────────────────────────────────────────────
with tab_draft:
    render_draft_tab(result, active_area, GRADIENT)


# ── 검증 & 최종본 ─────────────────────────────────────────────────────────────
with tab_verify:
    render_verify_tab(result, active_area, GRADIENT)


# ── 규정 검증 ─────────────────────────────────────────────────────────────────
with tab_policy:
    render_policy_tab(result, GRADIENT)


# ── 감사 추적 & HITL ──────────────────────────────────────────────────────────
with tab_audit:
    render_audit_tab(result, active_area, GRADIENT)


# ── 실사 응답서 ──────────────────────────────────────────────────────────────
with tab_supplychain:
    render_supplychain_tab(result, GRADIENT)


# ── 벤치마크 ─────────────────────────────────────────────────────────────────
with tab_bench:
    render_benchmark_tab(GRADIENT)
