"""ESGenie — K-ESG 공시 보고서 생성·그린워싱 검증·증빙 자동화 AI.

실행: streamlit run app.py
"""
from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from esgenie.dart_client import search_companies
from esgenie.embeddings import embedding_backend
from esgenie.pipeline import run as run_pipeline
from esgenie.supplychain import is_saq_upload, parse_saq_claims
from esgenie.ui.components import (
    badge_html,
    callout_html,
    hero_html,
    panel_html,
    render_metric_cards,
    render_pipeline_loading,
    render_section_badge,
    render_section_header,
)
from esgenie.ui.tabs import (
    render_diagnosis_workspace,
    render_evidence_workspace,
    render_greenwash_workspace,
    render_lab_workspace,
    render_submission_workspace,
)
from esgenie.ui.theme import apply_theme

# SSOT / OCR 확장
from esgenie.ssot import ocr_router


OUT_ROOT = Path("outputs")
AREA_LABELS = {"E": "환경 (E)", "S": "사회 (S)", "G": "지배구조 (G)"}
INDUSTRY_OPTIONS = ["자동차부품", "전자부품", "화학", "금속가공", "식품", "기타"]
PROFILE_OPTIONS = ["자동 판별", "중소기업 기본형 (28)", "전체 (61)"]
PURPOSE_OPTIONS = ["둘 다 (공시 + 실사)", "공시 보고서", "고객사 실사 대응"]
PURPOSE_FOCUS = {
    "둘 다 (공시 + 실사)": "both",
    "공시 보고서": "disclosure",
    "고객사 실사 대응": "due_diligence",
}
UPLOAD_GUIDES = {
    "둘 다 (공시 + 실사)": (
        "전기요금 고지서, 폐기물 처리 대장, 사내 규정집, 안전보건 문서, 고객사 SAQ 등 "
        "가지고 있는 서류를 그대로 올리면 됩니다. 서류가 없어도 진단은 가능합니다."
    ),
    "공시 보고서": (
        "공시 보고서에는 정량 증빙이 특히 중요합니다 — 전기·가스 요금 고지서, 폐기물 처리 대장, "
        "온실가스·에너지 집계표, 용수 사용량 자료를 우선 올려주세요. 서류가 없어도 진단은 가능합니다."
    ),
    "고객사 실사 대응": (
        "실사 응답에는 정책·체계 문서가 특히 중요합니다 — 취업규칙, 윤리규범·행동강령, 안전보건 관리 문서, "
        "인권정책, 협력사 행동강령, 고객사 SAQ 회신본을 우선 올려주세요. 서류가 없어도 진단은 가능합니다."
    ),
}
SURVEY_ITEMS = [
    ("P-1-1", "ESG 정보를 공시하는 방식이 있습니까?", "예: 홈페이지, DART, 자체 보고서 등"),
    ("E-1-1", "중장기 환경경영 목표를 수립하였습니까?", "예: 2030년 탄소 20% 감축 목표 등"),
    ("E-1-2", "환경경영 전담 조직·인력이 있습니까?", "예: 환경안전팀, ESG 담당자 등"),
    ("E-3-3", "온실가스 배출량에 대한 제3자 검증을 받았습니까?", "예: 검증기관명"),
    ("S-1-1", "사회적 책임 목표를 수립·공시하고 있습니까?", "예: 산업재해율 목표 등"),
    ("S-2-6", "노동조합 또는 결사의 자유를 보장하고 있습니까?", "예: 노조 가입률, 노사협의회 등"),
    ("S-4-1", "안전보건 전담 조직·정책이 있습니까?", "예: 안전보건위원회 운영 등"),
    ("S-5-1", "인권정책을 수립·시행하고 있습니까?", "예: 인권경영 선언, 고충처리 절차 등"),
    ("S-6-1", "협력사 ESG 관리 기준·프로그램이 있습니까?", "예: 협력사 행동강령, 평가 절차 등"),
    ("S-7-1", "전략적 사회공헌(CSR) 활동을 하고 있습니까?", "예: 지역사회 프로그램, 기부 등"),
    ("S-8-1", "정보보호 체계(ISMS 등)를 구축하였습니까?", "예: ISMS 인증, 정보보호 정책 등"),
    ("G-1-1", "이사회에서 ESG 안건을 정기적으로 상정합니까?", "예: 연 2회 이상 ESG 보고 등"),
    ("G-3-1", "주주총회 소집 공고를 법정 기간 내에 하고 있습니까?", "예: 2주 전 공고 등"),
    ("G-4-1", "윤리규범 위반사항 공시 체계가 있습니까?", "예: 윤리헌장, 내부신고 채널 등"),
    ("G-5-1", "내부감사 부서 또는 기구가 설치되어 있습니까?", "예: 감사위원회, 내부감사팀 등"),
]


st.set_page_config(page_title="ESGenie — 중소기업 ESG 자동 진단", layout="wide", page_icon="🌿")
apply_theme()


def _ensure_state_defaults() -> None:
    defaults = {
        "result": None,
        "last_run_inputs": None,
        "upload_paths": {},
        "upload_roles": {},
        "survey_answers": {},
        "company_search_q": "",
        "corp_name_manual": "",
        "corp_code_manual": "",
        "industry_select": INDUSTRY_OPTIONS[0],
        "use_dart": False,
        "report_year": 2025,
        "area_select": "E",
        "threshold": 30,
        "max_iter": 3,
        "demo_greenwash": True,
        "profile_select": PROFILE_OPTIONS[0],
        "llm_judge_opt": False,
        "expert_mode": False,
        "purpose_select": PURPOSE_OPTIONS[0],
        "_last_search_corp_code": "",
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _service_chip(label: str, ok: bool) -> str:
    bg = "rgba(99, 214, 116, 0.16)" if ok else "rgba(255, 128, 128, 0.14)"
    border = "rgba(99, 214, 116, 0.28)" if ok else "rgba(255, 128, 128, 0.24)"
    tone = "#f5fff4" if ok else "#ffe8e8"
    icon = "●" if ok else "●"
    return (
        "<div style='padding:10px 12px;border-radius:14px;"
        f"background:{bg};border:1px solid {border};color:{tone};"
        "font-size:13px;font-weight:700;margin-bottom:8px'>"
        f"{icon} {label}</div>"
    )


def _render_sidebar() -> None:
    with st.sidebar:
        st.markdown(
            """
            <div style="padding:18px 16px;border-radius:22px;
                        background:linear-gradient(135deg, rgba(255,255,255,0.10), rgba(255,255,255,0.04));
                        border:1px solid rgba(255,255,255,0.10);margin-bottom:18px;">
                <div style="font-size:12px;font-weight:800;letter-spacing:.08em;opacity:.82;">중소기업 ESG 자동 진단</div>
                <div style="font-size:28px;font-weight:900;margin-top:6px;">ESGenie</div>
                <div style="font-size:13px;line-height:1.6;opacity:.86;margin-top:8px;">
                    회사를 고르고 서류를 올리면 진단, 그린워싱 검증, 제출 서류까지 자동으로 만들어 드립니다.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        dart_ok = bool(os.getenv("DART_API_KEY"))
        openai_ok = bool(os.getenv("OPENAI_API_KEY"))
        anthropic_ok = bool(os.getenv("ANTHROPIC_API_KEY"))
        upstage_ocr_ok = bool(os.getenv("UPSTAGE_API_KEY"))

        st.markdown("#### 연결 상태")
        st.markdown(
            _service_chip("DART", dart_ok)
            + _service_chip("OpenAI", openai_ok)
            + _service_chip("Anthropic", anthropic_ok)
            + _service_chip("Upstage OCR", upstage_ocr_ok),
            unsafe_allow_html=True,
        )

        st.markdown("#### 보기 설정")
        st.toggle(
            "🔬 전문가 모드",
            key="expert_mode",
            help="증빙 추적(원본 데이터), 감사 추적, 벤치마크 등 상세 화면을 추가로 엽니다.",
        )

        emb = embedding_backend()
        emb_note = "SBERT (정상)" if emb == "sbert" else "해시 폴백 - 품질 저하 가능"
        st.caption(f"임베딩 백엔드: {emb_note}")


def _handle_search_prefill() -> None:
    search_q = st.text_input("회사 검색", key="company_search_q", placeholder="예: 현대, 포스코, (주)예시")
    if not search_q:
        st.session_state["_last_search_corp_code"] = ""
        return

    hits = search_companies(search_q)
    if not hits:
        st.caption("DART 미매칭 — 직접 입력으로 계속 진행")
        st.session_state["_last_search_corp_code"] = ""
        return

    from esgenie.demo_aliases import display_name as _alias

    labels = [
        f"{_alias(hit['corp_name'])}" + ("" if _alias(hit["corp_name"]) != hit["corp_name"] else f" ({hit['corp_code']})")
        for hit in hits
    ]
    sel = st.selectbox("검색 결과", labels)
    selected = hits[labels.index(sel)]

    if st.session_state.get("_last_search_corp_code") != selected["corp_code"]:
        st.session_state["corp_name_manual"] = selected["corp_name"]
        st.session_state["corp_code_manual"] = selected["corp_code"]
        if selected.get("industry") in INDUSTRY_OPTIONS:
            st.session_state["industry_select"] = selected["industry"]
        st.session_state["use_dart"] = True
        st.session_state["_last_search_corp_code"] = selected["corp_code"]


def _handle_uploads() -> tuple[list[dict[str, str]], dict[str, str]]:
    upload_rows: list[dict[str, str]] = []
    uploads = st.file_uploader(
        "증빙 파일",
        type=["pdf", "png", "jpg", "jpeg"],
        accept_multiple_files=True,
        label_visibility="collapsed",
    )

    if uploads:
        tmp = OUT_ROOT / "_uploads"
        tmp.mkdir(parents=True, exist_ok=True)
        st.session_state.upload_paths = {}
        st.session_state.upload_roles = {}
        for uf in uploads:
            path = tmp / uf.name
            path.write_bytes(uf.getbuffer())
            st.session_state.upload_paths[uf.name] = str(path)
            role = "supplier_claim" if is_saq_upload(str(path), file_name=uf.name) else "evidence"
            st.session_state.upload_roles[uf.name] = role
            dec = ocr_router.route_document(str(path))
            upload_rows.append(
                {
                    "파일명": uf.name,
                    "채널": dec.channel.value,
                    "문서 유형": dec.doc_type,
                    "연동": "SAQ 자가주장" if role == "supplier_claim" else "증빙",
                    "신뢰도": f"{dec.confidence:.0%}",
                }
            )
    elif st.session_state.upload_paths:
        st.session_state.upload_paths = {}
        st.session_state.upload_roles = {}

    return upload_rows, st.session_state.upload_paths


def _render_survey_editor() -> int:
    with st.expander("정성 항목 입력", expanded=False):
        for code, question, hint in SURVEY_ITEMS:
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

    return sum(1 for value in st.session_state.survey_answers.values() if value["yn"] != "미입력")


def _input_snapshot(
    *,
    corp_code: str,
    corp_name: str,
    industry: str,
    report_year: int,
    use_dart: bool,
    area: str,
    threshold: int,
    max_iter: int,
    demo_greenwash: bool,
    profile_choice: str | None,
    llm_judge_opt: bool,
    upload_paths: dict[str, str],
    survey_answers: dict[str, dict[str, str]],
) -> dict[str, object]:
    return {
        "corp_code": corp_code.strip(),
        "corp_name": corp_name.strip(),
        "industry": industry,
        "report_year": int(report_year),
        "use_dart": bool(use_dart),
        "area": area,
        "threshold": int(threshold),
        "max_iter": int(max_iter),
        "demo_greenwash": bool(demo_greenwash),
        "profile_choice": profile_choice,
        "llm_judge_opt": bool(llm_judge_opt),
        "uploaded_names": sorted(upload_paths.keys()),
        "survey_answers": {
            key: {"yn": value.get("yn", ""), "text": value.get("text", "")}
            for key, value in sorted(survey_answers.items())
        },
    }


def _hero_status(result, is_stale: bool, active_area: str) -> tuple[str, str, str]:
    if result is None:
        return "분석 대기", "warning", "회사와 증빙을 설정한 뒤 실행하세요."
    if is_stale:
        return "설정 변경됨", "warning", "현재 결과는 이전 설정 기준입니다. 다시 분석이 필요합니다."
    verify = result.sections.get(active_area)
    if verify is None:
        return "부분 결과", "info", "현재 선택한 영역과 저장된 결과 영역이 다릅니다."
    if verify.hitl_required:
        return "검토 필요", "danger", "최종본은 생성됐지만 일부 문장에 수동 검토가 필요합니다."
    return "분석 완료", "success", f"위험도 {verify.final_score:.1f} / {verify.final_band}"


def _run_pipeline_now(
    *,
    corp_code: str,
    corp_name: str,
    industry: str,
    report_year: int,
    use_dart: bool,
    area: str,
    threshold: int,
    max_iter: int,
    demo_greenwash: bool,
    llm_judge_opt: bool,
    upload_paths: dict[str, str],
    profile_choice: str | None,
) -> object:
    roles = st.session_state.get("upload_roles", {})
    evidence_files = {name: path for name, path in upload_paths.items() if roles.get(name) != "supplier_claim"}
    saq_paths = [path for name, path in upload_paths.items() if roles.get(name) == "supplier_claim"]

    result = run_pipeline(
        corp_code=corp_code,
        corp_name=corp_name,
        industry=industry,
        report_year=int(report_year),
        use_dart=bool(use_dart),
        evidence_files=evidence_files,
        survey_answers=st.session_state.get("survey_answers", {}),
        areas=[area],
        threshold=float(threshold),
        max_iter=int(max_iter),
        demo_greenwash=bool(demo_greenwash),
        llm_judge=bool(llm_judge_opt),
        export_outputs=True,
        profile=profile_choice,
    )
    result.supplier_claims = parse_saq_claims(saq_paths)
    result.supplier_claim_files = [Path(path).name for path in saq_paths]
    return result


def _render_onboarding_guide() -> None:
    """분석 전 빈 탭 대신 보여주는 3단계 안내."""
    st.markdown("### 이렇게 진행됩니다")
    guide_cols = st.columns(3)
    steps = [
        ("① 목적·회사 선택", "필요한 것(공시 보고서/실사 대응)을 고르고 회사 이름을 검색하면 DART 공시 정보를 자동으로 불러옵니다."),
        ("② 증빙 서류 업로드", "전기요금 고지서, 폐기물 대장, 규정집 등 가지고 있는 서류를 그대로 올리세요. 없어도 진단은 가능합니다."),
        ("③ 분석 시작", "버튼 하나로 진단 → 그린워싱 검증 → 제출 서류 생성까지 자동으로 진행됩니다."),
    ]
    for col, (step_title, step_body) in zip(guide_cols, steps):
        with col:
            st.markdown(panel_html(step_title, step_body), unsafe_allow_html=True)

    st.markdown(
        callout_html(
            "분석이 끝나면 받는 것",
            [
                "📊 진단 결과 — 우리 회사 공시 수준, 부족한 항목, 지금 준비할 일",
                "🔍 그린워싱 검증 — 과장·모호 표현을 찾아 고친 전후 비교",
                "📄 제출 서류 — 통합 보고서(PDF), 대기업 제출용 데이터시트, 실사 응답서",
            ],
            tone="info",
        ),
        unsafe_allow_html=True,
    )


def _render_ocr_health(result) -> None:
    """OCR 무음 실패(Upstage 폴백·mock·파싱 누락)를 화면 경고로 노출."""
    roles = st.session_state.get("upload_roles", {})
    evidence_names = [
        name for name in st.session_state.get("upload_paths", {})
        if roles.get(name) != "supplier_claim"
    ]
    msgs = ocr_router.ocr_health_report(
        getattr(result, "ocr_extractions", None) or [],
        evidence_names,
        upstage_key_present=bool(os.getenv("UPSTAGE_API_KEY")),
    )
    for lvl, msg in msgs:
        (st.error if lvl == "error" else st.warning)(f"🔍 OCR · {msg}")


_ensure_state_defaults()
_render_sidebar()

render_section_badge("ESG 자동 진단 콘솔")
render_section_header(
    "ESG 진단 준비",
    "① 회사 선택 → ② 증빙 서류 업로드 → ③ 분석 시작. 세 단계면 준비가 끝납니다.",
    kicker="시작하기",
)

with st.container(border=True):
    st.markdown("#### ① 목적·회사 선택")
    st.radio(
        "무엇이 필요하세요?",
        PURPOSE_OPTIONS,
        key="purpose_select",
        horizontal=True,
        help="분석은 한 번에 모두 수행됩니다. 선택에 따라 추천 증빙 안내와 결과 화면 순서가 바뀝니다.",
    )
    _handle_search_prefill()
    corp_col1, corp_col2, corp_col3 = st.columns([1.35, 1.0, 0.75])
    with corp_col1:
        st.text_input("회사명", key="corp_name_manual")
    with corp_col2:
        st.text_input("DART 코드 (모르면 비워두세요)", key="corp_code_manual")
    with corp_col3:
        st.checkbox("DART 연동", key="use_dart", help="위에서 회사를 검색해 선택하면 자동으로 켜집니다.")

    meta_col1, meta_col2, meta_col3 = st.columns(3)
    with meta_col1:
        st.selectbox("업종", INDUSTRY_OPTIONS, key="industry_select")
    with meta_col2:
        st.number_input("보고 연도", 2020, 2030, key="report_year")
    with meta_col3:
        st.selectbox(
            "집중 분석 영역",
            options=["E", "S", "G"],
            format_func=lambda area_code: {"E": "🌿 환경 (E)", "S": "🤝 사회 (S)", "G": "🏛 지배구조 (G)"}[area_code],
            key="area_select",
            help="한 영역을 깊게 분석합니다. 나머지 영역도 같은 방식으로 확장됩니다.",
        )

    st.markdown("#### ② 증빙 서류 업로드")
    st.caption(UPLOAD_GUIDES.get(st.session_state.purpose_select, UPLOAD_GUIDES[PURPOSE_OPTIONS[0]]))
    upload_rows, upload_paths = _handle_uploads()
    answered_count = _render_survey_editor()
    if upload_rows:
        st.dataframe(upload_rows, width='stretch', hide_index=True)

    with st.expander("⚙️ 고급 설정 — 기본값 그대로 두어도 됩니다", expanded=False):
        adv1, adv2 = st.columns(2)
        with adv1:
            st.slider("자가 검증 임계치 (위험도 ≤)", 10, 80, key="threshold", step=5)
            st.slider("최대 재생성 반복", 1, 5, key="max_iter")
            st.checkbox(
                "그린워싱 시연 모드",
                key="demo_greenwash",
                help="의도적 과장 생성 → 탐지·수정 과정을 시연합니다.",
            )
        with adv2:
            st.selectbox(
                "K-ESG 프로파일",
                PROFILE_OPTIONS,
                key="profile_select",
                help="자동: 상장코드(6자리 숫자) → 61항목, 그 외 → 기본형 28항목",
            )
            st.checkbox(
                "LLM 2차 판정 (하이브리드)",
                key="llm_judge_opt",
                help="룰 1차 스크리닝 + LLM 맥락 판정. 키가 없으면 mock 판정으로 시연합니다.",
            )

    st.markdown("#### ③ 분석 시작")
    run_btn = st.button(
        "▶ 분석 시작" if st.session_state.result is None else "▶ 다시 분석 (바뀐 설정 반영)",
        type="primary",
        width='stretch',
        disabled=not st.session_state.corp_name_manual.strip(),
    )
    if not st.session_state.corp_name_manual.strip():
        st.caption("①에서 회사명을 입력하면 버튼이 활성화됩니다.")

from esgenie.demo_aliases import display_name as _demo_display_name

corp_name_raw = st.session_state.corp_name_manual.strip()
corp_code = st.session_state.corp_code_manual.strip()
corp_name = _demo_display_name(corp_name_raw) if corp_name_raw else ""
industry = st.session_state.industry_select
use_dart = bool(st.session_state.use_dart)
report_year = int(st.session_state.report_year)
area = st.session_state.area_select
threshold = int(st.session_state.threshold)
max_iter = int(st.session_state.max_iter)
demo_greenwash = bool(st.session_state.demo_greenwash)
profile_choice = {"자동 판별": None, "중소기업 기본형 (28)": "sme", "전체 (61)": "full"}[st.session_state.profile_select]
from esgenie.knowledge.kesg_items import detect_profile as _detect_profile

resolved_profile = profile_choice or _detect_profile(corp_code)
llm_judge_opt = bool(st.session_state.llm_judge_opt)
upload_paths = st.session_state.upload_paths
survey_answers = st.session_state.survey_answers
answered_count = sum(1 for value in survey_answers.values() if value["yn"] != "미입력")

snapshot = _input_snapshot(
    corp_code=corp_code,
    corp_name=corp_name,
    industry=industry,
    report_year=report_year,
    use_dart=use_dart,
    area=area,
    threshold=threshold,
    max_iter=max_iter,
    demo_greenwash=demo_greenwash,
    profile_choice=profile_choice,
    llm_judge_opt=llm_judge_opt,
    upload_paths=upload_paths,
    survey_answers=survey_answers,
)

result = st.session_state.result
if result is not None and not hasattr(result, "sections"):
    st.session_state.result = None
    result = None

is_result_stale = result is not None and st.session_state.last_run_inputs != snapshot
active_area = area
if result is not None and area not in result.sections and result.requested_areas:
    active_area = result.requested_areas[0]

status_label, status_tone, status_detail = _hero_status(result, is_result_stale, active_area)
display_name = corp_name or "대상 기업을 선택하세요"
subtitle = (
    "공시 자료와 증빙 서류를 모아 K-ESG 진단, 그린워싱 검증, 제출 서류 생성까지 한 번에 처리합니다."
)

hero_badges = [
    badge_html(status_label, status_tone),
    badge_html(st.session_state.purpose_select, "neutral"),
    badge_html(industry or "업종 미선택", "neutral"),
    badge_html(AREA_LABELS[area], "neutral"),
]
hero_meta = [
    display_name,
    f"{report_year} 기준",
    st.session_state.profile_select,
    f"증빙 {len(upload_paths)}건",
    f"정성 설문 {answered_count}건",
]
st.markdown(
    hero_html(
        kicker="ESG 자동 진단",
        title=f"{display_name} ESG 진단",
        subtitle=subtitle,
        badges=hero_badges,
        meta=hero_meta,
    ),
    unsafe_allow_html=True,
)
if corp_name and corp_name != corp_name_raw:
    st.caption("🔒 시연 익명화 적용 — 실명 대신 익명으로 표시합니다. DART 및 내부 처리에는 실제 식별값이 사용됩니다.")
st.caption(status_detail)

if run_btn:
    render_pipeline_loading("L0~L5 파이프라인 실행 중 — 서류 읽기 → 데이터 통합 → 보고서 생성 → 그린워싱 검증 → 규정 점검 → 제출 서류 생성")
    with st.spinner("분석을 진행하고 있습니다…"):
        st.session_state.result = _run_pipeline_now(
            corp_code=corp_code,
            corp_name=corp_name,
            industry=industry,
            report_year=report_year,
            use_dart=use_dart,
            area=area,
            threshold=threshold,
            max_iter=max_iter,
            demo_greenwash=demo_greenwash,
            llm_judge_opt=llm_judge_opt,
            upload_paths=upload_paths,
            profile_choice=profile_choice,
        )
        st.session_state.last_run_inputs = snapshot
    st.rerun()

result = st.session_state.result
if result is not None and area not in result.sections and result.requested_areas:
    active_area = result.requested_areas[0]
else:
    active_area = area

if result is not None:
    _render_ocr_health(result)
    summary_cards = []
    extraction = getattr(result, "extraction", None)
    v15_trace = getattr(result, "v15_trace", None)
    verify = result.sections.get(active_area)
    if extraction is not None:
        summary_cards.append({"label": "공시 완료율", "value": f"{extraction.coverage_pct:.1f}%", "note": extraction.profile_label})
    if v15_trace is not None:
        summary_cards.append({"label": "증빙 확인률", "value": f"{v15_trace.summary['verified_ratio']*100:.0f}%", "note": f"정량 {v15_trace.summary['data_point_count']}건"})
        summary_cards.append({"label": "규정 충족", "value": f"{v15_trace.summary['policy_pass']}/{v15_trace.summary['policy_total']}", "note": "법규·사내 규정"})
    if verify is not None:
        summary_cards.append({"label": "그린워싱 위험도", "value": f"{verify.final_score:.1f}", "note": verify.final_band})
        summary_cards.append({"label": "담당자 확인", "value": "필요" if verify.hitl_required else "완료", "note": f"검증 {verify.iterations_used}회"})
    render_metric_cards(summary_cards, columns=min(5, len(summary_cards)) or 1)

expert_mode = bool(st.session_state.expert_mode)
overview_profile = getattr(getattr(result, "extraction", None), "profile", None) or resolved_profile

if result is None and not expert_mode:
    _render_onboarding_guide()
else:
    tab_labels = ["📊 진단 결과", "🔍 그린워싱 검증", "📄 제출 서류"]
    if expert_mode:
        tab_labels += ["🗂 근거·검증 상세", "🧪 실험실"]
    main_tabs = st.tabs(tab_labels)

    with main_tabs[0]:
        render_diagnosis_workspace(
            result,
            active_area,
            uploaded_names=sorted(upload_paths.keys()),
            profile=overview_profile,
        )

    with main_tabs[1]:
        render_greenwash_workspace(result, active_area)

    with main_tabs[2]:
        render_submission_workspace(
            result,
            active_area,
            focus=PURPOSE_FOCUS.get(st.session_state.purpose_select, "both"),
        )

    if expert_mode:
        with main_tabs[3]:
            render_evidence_workspace(result, active_area, "", uploaded_names=sorted(upload_paths.keys()))
        with main_tabs[4]:
            render_lab_workspace("")
