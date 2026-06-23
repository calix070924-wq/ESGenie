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
    render_section_header,
    render_stat_row,
)
from esgenie.ui.tabs import (
    render_analysis_workspace,
    render_deliverables_workspace,
    render_evidence_workspace,
    render_lab_workspace,
    render_overview_workspace,
)
from esgenie.ui.theme import apply_theme

# SSOT / OCR 확장
from esgenie.ssot import ocr_router


OUT_ROOT = Path("outputs")
AREA_LABELS = {"E": "환경 (E)", "S": "사회 (S)", "G": "지배구조 (G)"}
INDUSTRY_OPTIONS = ["자동차부품", "전자부품", "화학", "금속가공", "식품", "기타"]
PROFILE_OPTIONS = ["자동 판별", "중소기업 기본형 (28)", "전체 (61)"]
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


st.set_page_config(page_title="ESGenie — K-ESG Demo Console", layout="wide", page_icon="🌿")
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
                <div style="font-size:12px;font-weight:800;letter-spacing:.08em;opacity:.82;">ESG DEMO CONSOLE</div>
                <div style="font-size:28px;font-weight:900;margin-top:6px;">ESGenie</div>
                <div style="font-size:13px;line-height:1.6;opacity:.86;margin-top:8px;">
                    공시 진단, 그린워싱 검증, 증빙 추적, 공급망 응답서 생성을 하나의 Streamlit 데모로 묶습니다.
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

        emb = embedding_backend()
        emb_note = "SBERT (정상)" if emb == "sbert" else "해시 폴백 - 품질 저하 가능"
        st.markdown("#### 운영 메모")
        st.caption(f"임베딩 백엔드: {emb_note}")
        st.caption("시연 추천 흐름: 기업 선택 → 증빙 업로드 → 분석 실행 → Overview/Analysis → Deliverables")


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


_ensure_state_defaults()
_render_sidebar()

render_section_header(
    "Analysis Setup",
    "대회 시연에서는 입력 준비 상태가 한눈에 보여야 하므로 회사, 증빙, 고급 설정을 한 패널에 모았습니다.",
    kicker="Control Deck",
)

with st.container(border=True):
    setup_company, setup_evidence, setup_advanced = st.tabs(["🏢 Company Context", "📎 Evidence Intake", "⚙️ Advanced Controls"])

    with setup_company:
        _handle_search_prefill()
        corp_col1, corp_col2, corp_col3 = st.columns([1.35, 1.0, 0.75])
        with corp_col1:
            st.text_input("회사명", key="corp_name_manual")
        with corp_col2:
            st.text_input("DART 코드 (없으면 공란)", key="corp_code_manual")
        with corp_col3:
            st.checkbox("DART 연동", key="use_dart")

        meta_col1, meta_col2, meta_col3 = st.columns(3)
        with meta_col1:
            st.selectbox("업종", INDUSTRY_OPTIONS, key="industry_select")
        with meta_col2:
            st.number_input("보고 연도", 2020, 2030, key="report_year")
        with meta_col3:
            st.selectbox(
                "분석 영역",
                options=["E", "S", "G"],
                format_func=lambda area_code: {"E": "🌿 환경 (E)", "S": "🤝 사회 (S)", "G": "🏛 지배구조 (G)"}[area_code],
                key="area_select",
            )

        st.markdown(
            panel_html(
                "Company Context Note",
                "검색으로 종목을 찾으면 DART 코드와 업종을 자동으로 채웁니다. 시연 익명화가 적용되는 경우 화면에는 별칭만 노출됩니다.",
                compact_note="기업 검색이 실패해도 직접 입력으로 분석을 계속할 수 있습니다.",
            ),
            unsafe_allow_html=True,
        )

    with setup_evidence:
        st.markdown("#### 내부 증빙 파일 업로드")
        st.caption("전기요금 고지서, 폐기물 대장, 규정집, 안전보건 문서, OEM SAQ PDF를 함께 업로드할 수 있습니다.")
        upload_rows, upload_paths = _handle_uploads()
        answered_count = _render_survey_editor()

        quick_cards = [
            {"label": "업로드 파일", "value": str(len(upload_paths)), "note": "증빙 + SAQ 포함"},
            {"label": "정성 설문", "value": str(answered_count), "note": "수동 입력 완료 항목"},
        ]
        render_stat_row(quick_cards, columns=2)

        if upload_rows:
            st.dataframe(upload_rows, use_container_width=True, hide_index=True)

    with setup_advanced:
        adv1, adv2 = st.columns(2)
        with adv1:
            st.slider("자가 검증 임계치 (위험도 ≤)", 10, 80, key="threshold", step=5)
            st.slider("최대 재생성 반복", 1, 5, key="max_iter")
            st.checkbox(
                "그린워싱 시연 모드",
                key="demo_greenwash",
                help="의도적 과장 생성 → L3/L4 탐지·수정 과정을 시연합니다.",
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

        st.markdown(
            callout_html(
                "Demo Tuning",
                [
                    "대회 시연은 `그린워싱 시연 모드`를 켜면 전후 대비가 더 선명합니다.",
                    "임계치를 너무 낮게 두면 재생성 루프가 늘고, 너무 높게 두면 개선 폭이 덜 보일 수 있습니다.",
                    "LLM 2차 판정은 실키가 있으면 설득력이 좋아지고, 없으면 아키텍처 데모용으로 동작합니다.",
                ],
                tone="info",
            ),
            unsafe_allow_html=True,
        )

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
    "DART 공시, OCR 증빙, 정성 설문을 하나의 워크벤치에서 연결해 K-ESG 분석과 제출 패키지를 만듭니다."
)

hero_col, action_col = st.columns([5.2, 1.1])
with hero_col:
    hero_badges = [
        badge_html(status_label, status_tone),
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
            kicker="K-ESG WORKBENCH",
            title=f"{display_name} 분석 콘솔",
            subtitle=subtitle,
            badges=hero_badges,
            meta=hero_meta,
        ),
        unsafe_allow_html=True,
    )
    if corp_name and corp_name != corp_name_raw:
        st.caption("🔒 시연 익명화 적용 — 실명 대신 익명으로 표시합니다. DART 및 내부 처리에는 실제 식별값이 사용됩니다.")
    st.caption(status_detail)

with action_col:
    st.markdown(
        panel_html(
            "Run Control",
            "설정이 바뀌면 상단 상태 배지가 `설정 변경됨`으로 바뀝니다. 시연 전에는 한 번 더 재실행하는 것이 안전합니다.",
        ),
        unsafe_allow_html=True,
    )
    run_btn = st.button(
        "▶ 분석 시작" if result is None else "▶ 다시 분석",
        type="primary",
        use_container_width=True,
        disabled=not corp_name,
    )

if run_btn:
    with st.spinner("증빙 파싱 → SSOT 통합 → 보고서 생성 → 그린워싱 검증 → 규정 심사 → 서류철 생성…"):
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
    summary_cards = []
    extraction = getattr(result, "extraction", None)
    v15_trace = getattr(result, "v15_trace", None)
    verify = result.sections.get(active_area)
    if extraction is not None:
        summary_cards.append({"label": "Coverage", "value": f"{extraction.coverage_pct:.1f}%", "note": extraction.profile_label})
    if v15_trace is not None:
        summary_cards.append({"label": "Verified", "value": f"{v15_trace.summary['verified_ratio']*100:.0f}%", "note": f"정량 {v15_trace.summary['data_point_count']}건"})
        summary_cards.append({"label": "Policy", "value": f"{v15_trace.summary['policy_pass']}/{v15_trace.summary['policy_total']}", "note": "규정 충족"})
    if verify is not None:
        summary_cards.append({"label": "Risk", "value": f"{verify.final_score:.1f}", "note": verify.final_band})
        summary_cards.append({"label": "HITL", "value": "필요" if verify.hitl_required else "완료", "note": f"검증 {verify.iterations_used}회"})
    render_stat_row(summary_cards, columns=min(5, len(summary_cards)) or 1)

main_tabs = st.tabs(["🏠 Overview", "📊 Analysis", "🗂 Evidence", "📤 Deliverables", "🧪 Lab"])
tab_overview, tab_analysis, tab_evidence, tab_deliverables, tab_lab = main_tabs

overview_profile = getattr(getattr(result, "extraction", None), "profile", None) or resolved_profile

with tab_overview:
    render_overview_workspace(
        result,
        active_area,
        uploaded_names=sorted(upload_paths.keys()),
        profile=overview_profile,
    )

with tab_analysis:
    render_analysis_workspace(result, active_area, "")

with tab_evidence:
    render_evidence_workspace(result, active_area, "", uploaded_names=sorted(upload_paths.keys()))

with tab_deliverables:
    render_deliverables_workspace(result, active_area, "")

with tab_lab:
    render_lab_workspace("")
