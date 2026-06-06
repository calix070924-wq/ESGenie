"""ESGenie v15 — 중소기업 담당자용 Streamlit UX.

흐름:
  ① 회사/DART 입력  →  ② 증빙 파일 업로드  →  ③ 파이프라인 실행
  →  ④ 결과 검토(SSOT 그래프 | 데이터시트 | 리스크 | 규정 검증 | 누락 보완)
  →  ⑤ 엑셀/JSON 서류철 다운로드

실행:  cd v15_scaffold && streamlit run app.py
"""
from __future__ import annotations

import io
import json
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

# v10 선택적 임포트
try:
    from esgenie.dart_client import load_report
    from esgenie.llm import LLMClient
    from esgenie.layer2_rag import HybridRAG
except Exception:
    load_report = None
    LLMClient = None
    HybridRAG = None

OUT_ROOT = Path("outputs")

# 정량 검증 대상 K-ESG 코드
TARGET_CODES = ["E-3-1", "E-4-1", "E-4-2", "E-5-1", "E-6-1", "E-6-2"]

# 규정 검증 대상 (업로드된 규정집/회의록이 있을 때만 실제 동작)
POLICY_CODES = ["E-1-1", "E-1-2", "E-3-1", "S-1-1", "S-2-1", "S-3-1", "S-4-1",
                "G-1-1", "G-3-1", "P-1-1"]


# ====================================================================
# Mock / 데모 폴백
# ====================================================================

def _demo_extraction(fname: str, decision) -> "ocr_router.OcrExtraction":
    from esgenie_v15.ocr_router import OcrExtraction, ExtractedMetric, ExtractedClause, DocChannel
    if decision.channel is DocChannel.STRUCTURED:
        return OcrExtraction(
            source_file=fname, channel=DocChannel.STRUCTURED, doc_type=decision.doc_type,
            metrics=[ExtractedMetric("사용전력량", 128_400, "kWh", "2025-12",
                                     kesg_code_guess="E-4-1", confidence=0.93)],
        )
    return OcrExtraction(
        source_file=fname, channel=DocChannel.UNSTRUCTURED, doc_type=decision.doc_type,
        clauses=[ExtractedClause("안전보건위원회", "분기별 위원회를 개최한다.",
                                 kesg_code_guess="S-3-1", page=1)],
    )


class _MockLLM:
    def complete(self, system="", user="", json_mode=False, temperature=0.0, **kw):
        class R: ...
        r = R()
        if json_mode:
            r.content = json.dumps({
                "item": "S-3-1",
                "findings": [{
                    "requirement": "근로자 대표의 참여 보장 문구",
                    "status": "missing", "evidence_quote": None,
                    "gap_comment": "근로자 대표 참여 조항이 확인되지 않음",
                    "suggested_fix": "산업안전보건법 제24조에 따른 근로자 대표 참여 조항 추가",
                }],
                "overall": {"met": 0, "insufficient": 0, "missing": 1, "pass": False},
            }, ensure_ascii=False)
        else:
            r.content = "제1조(목적) 본 규정은 안전보건 관리를 위해 제정된다.\n\n※ 담당자 확인 필요: [감축목표 수치], [위원회 개최 주기]"
        r.used_mock = True
        return r


# ====================================================================
# 레이아웃
# ====================================================================

st.set_page_config(page_title="ESGenie v15 — 공급망 ESG 자동화", layout="wide")

st.title("🧞 ESGenie v15 · 대기업 공급망 실사 대응")
st.caption(
    "DART 공시 + 내부 증빙(전기요금·폐기물 대장·규정집)을 **단일 진실 원천(SSOT)**으로 통합 → "
    "K-ESG 4축 그린워싱 검증 + 사내규정 누락 조항 자동 검출 → 대기업 제출용 엑셀 자동 생성"
)

# ① 사이드바 — 회사 정보
with st.sidebar:
    st.header("① 회사 정보")
    corp_name  = st.text_input("회사명", value="(주)예시중소기업")
    corp_code  = st.text_input("DART 종목코드 (선택)", value="")
    industry   = st.selectbox("업종", ["자동차부품", "전자부품", "화학", "금속가공", "식품", "기타"])
    report_year = st.number_input("보고 연도", 2020, 2030, 2025)
    use_dart   = st.checkbox("DART 사업보고서 연동", value=False,
                             help="종목코드를 입력하면 DART에서 공시 데이터를 가져옵니다")
    st.divider()
    st.caption("🔑 API 키 없이도 mock 모드로 전체 파이프라인 시연 가능")

# ② 증빙 파일 업로드
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
route_rows: list[dict] = []

if uploads:
    tmp = OUT_ROOT / "_uploads"
    tmp.mkdir(parents=True, exist_ok=True)
    for uf in uploads:
        p = tmp / uf.name
        p.write_bytes(uf.getbuffer())
        upload_paths[uf.name] = str(p)
        dec = ocr_router.route_document(str(p))
        route_rows.append({
            "파일명": uf.name,
            "OCR 채널": dec.channel.value,
            "추정 문서 유형": dec.doc_type,
            "라우팅 신뢰도": f"{dec.confidence:.0%}",
        })
    st.caption("📂 업로드 파일 · 채널 자동 분류 결과")
    st.dataframe(route_rows, use_container_width=True, hide_index=True)

# ③ 실행 버튼
run = st.button(
    "🚀 파이프라인 실행",
    type="primary",
    disabled=not (uploads or use_dart),
    help="증빙 파일을 업로드하거나 DART 연동을 켜면 활성화됩니다",
)


# ====================================================================
# 파이프라인
# ====================================================================

def _run_pipeline() -> dict:
    llm = (LLMClient() if LLMClient else _MockLLM())

    # L0-A: 증빙 OCR 추출
    extractions = []
    for fname, path in upload_paths.items():
        dec = ocr_router.route_document(path)
        try:
            ext = ocr_router.extract_document(path, dec)
        except NotImplementedError:
            ext = _demo_extraction(fname, dec)
        ext.source_file = fname
        extractions.append(ext)

    # L0: SSOT 통합 그래프 (DART + OCR)
    dart_report = None
    if use_dart and load_report and corp_code:
        try:
            dart_report = load_report(corp_code)
        except Exception:
            st.warning("DART 조회 실패 — 로컬 샘플 사용")
            from esgenie.dart_client import load_sample_report
            dart_report = load_sample_report(corp_code or "005930")

    graph = evidence_graph.build_unified_graph(
        dart_report, extractions,
        corp_code=corp_code or "LOCAL",
        corp_name=corp_name,
        report_year=int(report_year),
    )

    # L1: K-ESG 추출 (SSOT 연결)
    l1_result = None
    if dart_report is not None:
        l1_result = extract_with_ssot(dart_report, graph)

    # L2: Hybrid RAG (SSOT 연결)
    if HybridRAG and dart_report is not None:
        rag = HybridRAG()
        build_rag_with_ssot(rag, dart_report, graph)

    # L3: D1 수치 검증
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
            "K-ESG 코드": code,
            "값": f"{n.value} {n.unit}",
            "D1 수치": round(axes["D1"].score, 3),
            "D2 수식어": round(axes["D2"].score, 3),
            "D3 의미": round(axes["D3"].score, 3),
            "D5 시계열": round(axes["D5"].score, 3),
            "종합 위험도": round(axes["aggregate"].score, 3),
        })

    # P축: 규정 검증 + 누락 보완 초안
    policy_results, drafts = [], {}
    active_policy_codes = [
        c for c in POLICY_CODES
        if graph.text_nodes_by_code(c) or graph.nodes_by_metric(c)
    ]
    # TextNode가 없어도 S-3-1·E-1-1은 항상 검증 (누락 케이스 시연용)
    for must in ("S-3-1", "E-1-1"):
        if must not in active_policy_codes:
            active_policy_codes.append(must)

    for code in active_policy_codes:
        res = detector_5axis.audit_policy_documents(code, graph, llm)
        policy_results.append(res)
        if not res.passed:
            drafts[code] = detector_5axis.draft_missing_policy(
                code, res, corp_name, industry, llm)

    # L5: DataPoint + AuditTrace + Excel
    dps = audit_trace.build_data_points(graph, d1_scores, target_codes=TARGET_CODES)
    trace = audit_trace.build_audit_trace_v15(
        corp_code or "LOCAL", corp_name, dps, policy_results)
    out_dir = OUT_ROOT / f"{corp_code or 'LOCAL'}_{int(report_year)}"
    paths = excel_exporter.export_datasheet(trace, out_dir, uploaded_files=upload_paths)

    return {
        "trace": trace,
        "graph": graph,
        "l1_result": l1_result,
        "risk_rows": risk_rows,
        "drafts": drafts,
        "paths": paths,
    }


# ====================================================================
# 결과 표시
# ====================================================================

if run:
    with st.spinner("증빙 파싱 → SSOT 통합 → 4축 검증 → 규정 심사 → 서류철 생성…"):
        result = _run_pipeline()

    trace = result["trace"]
    graph = result["graph"]
    summ  = ssot_summary(graph)

    # KPI 배너
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("정량 항목", trace.summary["data_point_count"])
    col2.metric("증빙 확인률",
                f"{trace.summary['verified_ratio']*100:.0f}%",
                help="verified / 전체 data_points")
    col3.metric("규정 통과",
                f"{trace.summary['policy_pass']}/{trace.summary['policy_total']}",
                help="P축 체크리스트 통과 항목 수")
    col4.metric("SSOT 노드",
                summ["total_nodes"],
                help="DART + OCR 통합 증거 노드 수")

    st.success(f"✅ 파이프라인 완료 — {corp_name} ({int(report_year)}년)")

    # ④ 결과 탭
    tab_ssot, tab_data, tab_risk, tab_policy, tab_draft = st.tabs([
        "🗂 SSOT 그래프",
        "📊 데이터시트",
        "🎯 4축 리스크",
        "📋 규정 검증",
        "✍️ 누락 보완 초안",
    ])

    # ── SSOT 그래프 탭 ──────────────────────────────────────────────────
    with tab_ssot:
        st.markdown("#### 단일 진실 원천(SSOT) 노드 현황")
        c1, c2, c3 = st.columns(3)
        c1.metric("DART 노드",    summ["by_origin"].get("dart", 0))
        c2.metric("OCR 정형 노드", summ["by_origin"].get("ocr_structured", 0))
        c3.metric("OCR 비정형 노드", summ["by_origin"].get("ocr_unstructured", 0))

        origin_map = {"dart": 0, "ocr_structured": 0, "ocr_unstructured": 0}
        for o, cnt in summ["by_origin"].items():
            origin_map[o] = cnt

        st.caption(
            f"시계열 엣지 {summ['edges']}개 "
            f"(교차검증 엣지 {summ['cross_check_edges']}개) · "
            f"정성 조항 노드 {summ['text_nodes']}개"
        )

        # 노드 테이블
        node_rows = [
            {
                "노드 ID": n.id,
                "K-ESG 코드": n.metric,
                "값": f"{n.value} {n.unit}",
                "연도": n.period,
                "출처": n.origin,
                "증빙 파일": n.source_file or "—",
                "신뢰도": round(n.confidence, 2),
            }
            for n in graph.nodes.values()
        ]
        if node_rows:
            st.dataframe(node_rows, use_container_width=True, hide_index=True)

        # TextNode 테이블
        if graph.text_nodes:
            st.markdown("#### 정성 조항 노드 (규정집·회의록)")
            text_rows = [
                {
                    "ID": t.id,
                    "K-ESG 코드": t.kesg_code or "—",
                    "섹션": t.section,
                    "내용(요약)": t.text[:60] + ("…" if len(t.text) > 60 else ""),
                    "파일": t.source_file,
                    "페이지": t.page,
                }
                for t in graph.text_nodes.values()
            ]
            st.dataframe(text_rows, use_container_width=True, hide_index=True)

        # L1 커버리지 (DART 연동 시)
        if result["l1_result"]:
            l1 = result["l1_result"]
            st.markdown(f"#### K-ESG L1 추출 결과 · 커버리지 {l1.coverage_pct:.1f}%")
            for note in l1.notes:
                st.caption(f"• {note}")

    # ── 데이터시트 탭 ───────────────────────────────────────────────────
    with tab_data:
        st.markdown("#### K-ESG 정량 데이터시트 (대기업 제출용)")
        if trace.data_points:
            st.dataframe(
                [dp.to_dict() for dp in trace.data_points],
                use_container_width=True, hide_index=True,
            )
        else:
            st.info("정량 수치가 없습니다. 증빙 파일을 업로드하거나 DART를 연동하세요.")

    # ── 4축 리스크 탭 ───────────────────────────────────────────────────
    with tab_risk:
        st.markdown("#### D1·D2·D3·D5 4축 그린워싱 리스크")
        st.caption("D1 수치정확성(40%) · D2 수식어과장(25%) · D3 의미일관성(25%) · D5 시계열모순(10%)")
        if result["risk_rows"]:
            import pandas as pd
            df = pd.DataFrame(result["risk_rows"])
            # 종합 위험도 기준 색상 하이라이트
            def _color(val):
                if isinstance(val, float):
                    if val >= 0.7: return "background-color:#FFC7CE"
                    if val >= 0.4: return "background-color:#FFEB9C"
                return ""
            st.dataframe(df.style.applymap(_color, subset=["종합 위험도"]),
                         use_container_width=True, hide_index=True)
        else:
            st.info("수치 항목이 없어 리스크 분석을 실행할 수 없습니다.")

    # ── 규정 검증 탭 ────────────────────────────────────────────────────
    with tab_policy:
        st.markdown("#### P축 — 사내 규정 필수 조항 검증")
        st.caption("K-ESG 가이드라인 + 중대재해처벌법·개인정보보호법·공정거래법 기준")
        for pa in trace.policy_audit:
            code = pa["kesg_code"]
            badge = "✅ 통과" if pa["passed"] else "⚠️ 보완 필요"
            passed_n  = sum(1 for f in pa["findings"] if f["status"] == "met")
            total_n   = len(pa["findings"])
            with st.expander(f"**{code}** — {badge}  ({passed_n}/{total_n}개 충족)"):
                for f in pa["findings"]:
                    icon = {"met": "✅", "insufficient": "⚠️", "missing": "❌"}.get(f["status"], "—")
                    st.markdown(f"{icon} **{f['requirement']}**")
                    if f["status"] != "met":
                        st.markdown(f"  - 갭: {f['gap_comment']}")
                        st.markdown(f"  - 보완: {f['suggested_fix']}")
                if pa["source_files"]:
                    st.caption(f"검토 파일: {', '.join(pa['source_files'])}")

    # ── 누락 보완 초안 탭 ───────────────────────────────────────────────
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
                "📥 K-ESG 데이터시트 (.xlsx)",
                fh.read(),
                file_name="ESG_DataSheet_대기업제출용.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
    with dl2:
        with open(paths["audit_json"], "rb") as fh:
            st.download_button(
                "📥 감사 추적 (audit_trace_v15.json)",
                fh.read(),
                file_name="audit_trace_v15.json",
                mime="application/json",
            )
    st.info(
        f"📁 증빙 서류철: `{paths['evidence_dir']}`  \n"
        "엑셀 '증빙 파일' 열의 하이퍼링크와 동일한 파일명으로 복사됩니다."
    )
