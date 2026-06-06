"""ESGenie v15 — 중소기업 담당자용 Streamlit UX.

흐름:
  ① 회사 검색/입력  →  ② 증빙 파일 업로드  →  ③ 파이프라인 실행
  →  ④ 결과 검토(SSOT 그래프 | 데이터시트 | 리스크 | 규정 검증 | 누락 보완)
  →  ⑤ 엑셀/JSON 서류철 다운로드

실행:  cd v15_scaffold && streamlit run app.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import streamlit as st

from esgenie_v15 import (
    ocr_router,
    evidence_graph,
    detector_5axis,
    audit_trace,
    excel_exporter,
)
from esgenie_v15.ssot_pipeline import extract_with_ssot, build_rag_with_ssot, ssot_summary
from esgenie.dart_client import load_report, search_companies
from esgenie.llm import CLIENT as LLM_CLIENT
from esgenie.layer2_rag import HybridRAG

OUT_ROOT = Path("outputs")

TARGET_CODES  = ["E-3-1", "E-4-1", "E-4-2", "E-5-1", "E-6-1", "E-6-2"]
POLICY_CODES  = ["E-1-1", "E-1-2", "E-3-1", "S-1-1", "S-2-1", "S-3-1",
                 "S-4-1", "G-1-1", "G-3-1", "P-1-1"]


# ====================================================================
# 레이아웃
# ====================================================================

st.set_page_config(page_title="ESGenie v15 — 공급망 ESG 자동화", layout="wide")
st.title("🧞 ESGenie v15 · 대기업 공급망 실사 대응")
st.caption(
    "DART 공시 + 내부 증빙(전기요금·폐기물 대장·규정집)을 **단일 진실 원천(SSOT)**으로 통합 → "
    "K-ESG 4축 그린워싱 검증 + 사내규정 누락 조항 자동 검출 → 대기업 제출용 엑셀 자동 생성"
)

# ====================================================================
# ① 사이드바 — 회사 정보
# ====================================================================

with st.sidebar:
    st.header("① 회사 정보")

    # API 키 상태 표시
    dart_key_ok = bool(os.getenv("DART_API_KEY"))
    openai_key_ok = bool(os.getenv("OPENAI_API_KEY"))
    clova_key_ok  = bool(os.getenv("CLOVA_OCR_SECRET"))
    st.markdown(
        f"{'🟢' if dart_key_ok else '🔴'} DART API  |  "
        f"{'🟢' if openai_key_ok else '🔴'} OpenAI  |  "
        f"{'🟢' if clova_key_ok else '🔴'} CLOVA OCR"
    )
    if not dart_key_ok:
        st.caption("DART 키 없음 — 샘플 데이터 또는 수동 입력 사용")
    st.divider()

    # 회사 검색
    st.markdown("**회사 검색** (DART 상장사 · 비상장사)")
    search_query = st.text_input("회사명 입력", placeholder="예: 삼성전자, (주)예시중소기업")
    corp_code  = ""
    corp_name  = ""
    industry   = ""

    if search_query:
        results = search_companies(search_query)
        if results:
            options = [f"{r['corp_name']} ({r['corp_code']})" for r in results]
            selected = st.selectbox("검색 결과", options)
            idx = options.index(selected)
            corp_code = results[idx]["corp_code"]
            corp_name = results[idx]["corp_name"]
            industry  = results[idx].get("industry", "")
        else:
            st.caption("DART 검색 결과 없음 — 직접 입력합니다")
            corp_name = search_query

    if not corp_name:
        corp_name = st.text_input("회사명 직접 입력", value="")
    if not corp_code:
        corp_code = st.text_input("DART 종목코드 (없으면 비워도 됨)", value="")

    industry = st.selectbox(
        "업종", ["자동차부품", "전자부품", "화학", "금속가공", "식품", "기타"],
        index=["자동차부품","전자부품","화학","금속가공","식품","기타"].index(industry)
        if industry in ["자동차부품","전자부품","화학","금속가공","식품","기타"] else 6 % 6,
    )
    report_year = st.number_input("보고 연도", 2020, 2030, 2025)
    use_dart    = st.checkbox(
        "DART 공시 데이터 연동",
        value=bool(corp_code),
        help="종목코드가 있으면 DART에서 재무·ESG 데이터를 가져옵니다",
    )

# ====================================================================
# ② 증빙 파일 업로드
# ====================================================================

st.subheader("② 내부 증빙 파일 업로드")
st.markdown(
    "한전 전기요금 고지서 · 도시가스 영수증 · 올바로 폐기물 대장 · "
    "안전보건위원회 회의록 · 사내 규정집 등 **(PDF / 이미지)**"
)
uploads = st.file_uploader(
    "증빙 파일 (복수 선택 가능)",
    type=["pdf", "png", "jpg", "jpeg"],
    accept_multiple_files=True,
)

upload_paths: dict[str, str] = {}
if uploads:
    tmp = OUT_ROOT / "_uploads"
    tmp.mkdir(parents=True, exist_ok=True)
    route_rows = []
    for uf in uploads:
        p = tmp / uf.name
        p.write_bytes(uf.getbuffer())
        upload_paths[uf.name] = str(p)
        dec = ocr_router.route_document(str(p))
        route_rows.append({
            "파일명":        uf.name,
            "OCR 채널":     dec.channel.value,
            "추정 문서 유형": dec.doc_type,
            "신뢰도":        f"{dec.confidence:.0%}",
        })
    st.caption("📂 업로드 파일 · 채널 자동 분류")
    st.dataframe(route_rows, use_container_width=True, hide_index=True)

# ====================================================================
# ③ 실행 버튼
# ====================================================================

ready = bool(corp_name) and (bool(uploads) or use_dart)
run = st.button(
    "🚀 파이프라인 실행",
    type="primary",
    disabled=not ready,
    help="회사명을 입력하고, 증빙 파일 또는 DART 연동 중 하나 이상을 켜면 활성화됩니다",
)


# ====================================================================
# 파이프라인
# ====================================================================

def _run_pipeline() -> dict:
    # L0-A: OCR 추출 (실제 API 호출 — 키 없으면 ocr_router 내부 mock 자동 사용)
    extractions = []
    for fname, path in upload_paths.items():
        dec = ocr_router.route_document(path)
        ext = ocr_router.extract_document(path, dec)
        ext.source_file = fname
        extractions.append(ext)

    # L0: DART + OCR 통합 SSOT
    dart_report = None
    if use_dart and corp_code:
        try:
            dart_report = load_report(corp_code, report_year=int(report_year))
            # 회사명 동기화 (DART 공식 명칭 우선)
            if dart_report.corp_name and dart_report.corp_name != corp_code:
                pass   # corp_name은 DART 값 사용
        except Exception as e:
            st.warning(f"DART 로드 실패: {e}")

    graph = evidence_graph.build_unified_graph(
        dart_report, extractions,
        corp_code=corp_code or "LOCAL",
        corp_name=(dart_report.corp_name if dart_report else corp_name) or corp_name,
        report_year=int(report_year),
    )

    # L1: K-ESG 추출 (SSOT 연결)
    l1_result = None
    if dart_report is not None:
        l1_result = extract_with_ssot(dart_report, graph)

    # L2: Hybrid RAG (SSOT 연결)
    if dart_report is not None:
        rag = HybridRAG()
        build_rag_with_ssot(rag, dart_report, graph)

    # L3: 4축 리스크
    d1_scores: dict[str, float] = {}
    risk_rows: list[dict] = []
    for code in TARGET_CODES:
        nodes = graph.nodes_by_metric(code)
        if not nodes:
            continue
        n = nodes[-1]
        sent = f"{code} 값은 {n.value}{n.unit}이다."
        axes = detector_5axis.detect_risk_axes(sent, code, graph)
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
    policy_results, drafts = [], {}
    active_policy_codes = list({
        *[c for c in POLICY_CODES if graph.text_nodes_by_code(c) or graph.nodes_by_metric(c)],
        "S-3-1", "E-1-1",  # 누락 시연용 기본 포함
    })
    for code in active_policy_codes:
        res = detector_5axis.audit_policy_documents(code, graph, LLM_CLIENT)
        policy_results.append(res)
        if not res.passed:
            drafts[code] = detector_5axis.draft_missing_policy(
                code, res, corp_name, industry, LLM_CLIENT)

    # L5: DataPoint + AuditTrace + Excel
    dps   = audit_trace.build_data_points(graph, d1_scores, target_codes=TARGET_CODES)
    trace = audit_trace.build_audit_trace_v15(
        corp_code or "LOCAL", corp_name, dps, policy_results)
    out_dir = OUT_ROOT / f"{corp_code or corp_name}_{int(report_year)}"
    paths   = excel_exporter.export_datasheet(trace, out_dir, uploaded_files=upload_paths)

    return {
        "trace":     trace,
        "graph":     graph,
        "l1_result": l1_result,
        "risk_rows": risk_rows,
        "drafts":    drafts,
        "paths":     paths,
    }


# ====================================================================
# ④ 결과 표시
# ====================================================================

if run:
    with st.spinner("증빙 파싱 → SSOT 통합 → 4축 검증 → 규정 심사 → 서류철 생성…"):
        result = _run_pipeline()

    trace = result["trace"]
    graph = result["graph"]
    summ  = ssot_summary(graph)

    # KPI 배너
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("정량 항목",   trace.summary["data_point_count"])
    c2.metric("증빙 확인률", f"{trace.summary['verified_ratio']*100:.0f}%")
    c3.metric("규정 통과",   f"{trace.summary['policy_pass']}/{trace.summary['policy_total']}")
    c4.metric("SSOT 노드",  summ["total_nodes"])
    st.success(f"✅ 완료 — {trace.corp_name} ({int(report_year)}년)")

    tab_ssot, tab_data, tab_risk, tab_policy, tab_draft = st.tabs([
        "🗂 SSOT 그래프", "📊 데이터시트", "🎯 4축 리스크", "📋 규정 검증", "✍️ 누락 보완 초안",
    ])

    with tab_ssot:
        st.markdown("#### 단일 진실 원천(SSOT) 노드 현황")
        c1, c2, c3 = st.columns(3)
        c1.metric("DART 노드",       summ["by_origin"].get("dart", 0))
        c2.metric("OCR 정형 노드",   summ["by_origin"].get("ocr_structured", 0))
        c3.metric("OCR 비정형 노드", summ["by_origin"].get("ocr_unstructured", 0))
        st.caption(
            f"시계열 엣지 {summ['edges']}개 · "
            f"교차검증 엣지 {summ['cross_check_edges']}개 · "
            f"정성 조항 {summ['text_nodes']}개"
        )
        node_rows = [
            {"노드 ID": n.id, "K-ESG 코드": n.metric, "값": f"{n.value} {n.unit}",
             "연도": n.period, "출처": n.origin,
             "증빙 파일": n.source_file or "—", "신뢰도": round(n.confidence, 2)}
            for n in graph.nodes.values()
        ]
        if node_rows:
            st.dataframe(node_rows, use_container_width=True, hide_index=True)
        if graph.text_nodes:
            st.markdown("#### 정성 조항 노드")
            text_rows = [
                {"K-ESG": t.kesg_code or "—", "섹션": t.section,
                 "내용": t.text[:60] + ("…" if len(t.text) > 60 else ""),
                 "파일": t.source_file, "페이지": t.page}
                for t in graph.text_nodes.values()
            ]
            st.dataframe(text_rows, use_container_width=True, hide_index=True)
        if result["l1_result"]:
            l1 = result["l1_result"]
            st.markdown(f"#### K-ESG 추출 커버리지: **{l1.coverage_pct:.1f}%**")
            for note in l1.notes:
                st.caption(f"• {note}")

    with tab_data:
        st.markdown("#### K-ESG 정량 데이터시트")
        if trace.data_points:
            st.dataframe([dp.to_dict() for dp in trace.data_points],
                         use_container_width=True, hide_index=True)
        else:
            st.info("정량 수치가 없습니다. 증빙 파일을 업로드하거나 DART를 연동하세요.")

    with tab_risk:
        st.markdown("#### D1·D2·D3·D5 4축 그린워싱 리스크")
        st.caption("D1(40%) · D2(25%) · D3(25%) · D5(10%)")
        if result["risk_rows"]:
            import pandas as pd
            df = pd.DataFrame(result["risk_rows"])
            def _color(val):
                if isinstance(val, float):
                    if val >= 0.7: return "background-color:#FFC7CE"
                    if val >= 0.4: return "background-color:#FFEB9C"
                return ""
            st.dataframe(df.style.applymap(_color, subset=["종합 위험도"]),
                         use_container_width=True, hide_index=True)
        else:
            st.info("수치 항목이 없어 리스크 분석을 실행할 수 없습니다.")

    with tab_policy:
        st.markdown("#### P축 — 사내 규정 필수 조항 검증")
        st.caption("K-ESG + 중대재해처벌법·개인정보보호법·공정거래법 기준")
        for pa in trace.policy_audit:
            code   = pa["kesg_code"]
            badge  = "✅ 통과" if pa["passed"] else "⚠️ 보완 필요"
            passed_n = sum(1 for f in pa["findings"] if f["status"] == "met")
            with st.expander(f"**{code}** — {badge}  ({passed_n}/{len(pa['findings'])}개 충족)"):
                for f in pa["findings"]:
                    icon = {"met": "✅", "insufficient": "⚠️", "missing": "❌"}.get(f["status"], "—")
                    st.markdown(f"{icon} **{f['requirement']}**")
                    if f["status"] != "met":
                        st.markdown(f"  - 갭: {f['gap_comment']}")
                        st.markdown(f"  - 보완: {f['suggested_fix']}")
                if pa["source_files"]:
                    st.caption(f"검토 파일: {', '.join(pa['source_files'])}")

    with tab_draft:
        st.markdown("#### 누락·미흡 조항 표준 조문 초안 (LLM 자동 생성)")
        st.caption("초안은 검토·수정 후 사용하세요. 정량 수치는 [플레이스홀더]로 표시됩니다.")
        if result["drafts"]:
            for code, draft in result["drafts"].items():
                with st.expander(f"{code} — 보완 초안"):
                    st.code(draft, language="markdown")
        else:
            st.success("모든 규정 항목이 체크리스트를 충족합니다.")

    # ⑤ 다운로드
    st.divider()
    st.subheader("⑤ 대기업 제출 서류 다운로드")
    paths = result["paths"]
    dl1, dl2 = st.columns(2)
    with dl1:
        with open(paths["xlsx"], "rb") as fh:
            st.download_button(
                "📥 K-ESG 데이터시트 (.xlsx)", fh.read(),
                file_name="ESG_DataSheet_대기업제출용.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
    with dl2:
        with open(paths["audit_json"], "rb") as fh:
            st.download_button(
                "📥 감사 추적 (audit_trace_v15.json)", fh.read(),
                file_name="audit_trace_v15.json", mime="application/json",
            )
    st.info(f"📁 증빙 서류철: `{paths['evidence_dir']}`")
