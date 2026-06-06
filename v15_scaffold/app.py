"""ESGenie v15 — 중소기업 담당자용 Streamlit UX.

흐름(좌→우):
  ① 회사/DART 입력  →  ② 증빙 파일 업로드  →  ③ 파이프라인 실행
  →  ④ 결과 검토(데이터시트 + 규정 검증 + 누락 보완)  →  ⑤ 엑셀/JSON 서류철 다운로드

실행:  streamlit run app.py

NOTE: ocr_router의 정형/비정형 추출은 STUB이므로, 데모에서는
      USE_DEMO_STUB=True 일 때 esgenie_v15/data/uploads_sample 의 mock 추출을 사용한다.
      실제 연동 시 _run_pipeline 내부의 TODO만 채우면 된다.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import streamlit as st

from esgenie_v15 import ocr_router, evidence_graph, detector_5axis, audit_trace, excel_exporter

# 기존 v10 패키지(있으면) — DART 로더 / LLM 클라이언트
try:
    from esgenie.dart_client import load_report
    from esgenie.llm import LLMClient
except Exception:                       # 데모 폴백
    load_report = None
    LLMClient = None

OUT_ROOT = Path("outputs")
TARGET_CODES = ["E-3-1", "E-4-1", "E-5-1", "E-6-1"]   # 대기업 실사 핵심 정량 항목
POLICY_CODES = ["S-3-1", "S-1-1", "E-1-1"]            # 규정 검증 대상


# --------------------------------------------------------------------
# 데모 폴백 (실제 OCR 미연동 시) — Streamlit은 top-to-bottom 실행이므로
# _run_pipeline()이 호출되기 전에 먼저 정의돼 있어야 한다.
# --------------------------------------------------------------------
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
    """LLMClient 미존재 시 데모용."""
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
            r.content = "제1조(목적) ... ※ 담당자 확인 필요: [감축목표 수치]"
        return r


# --------------------------------------------------------------------
st.set_page_config(page_title="ESGenie — 공급망 ESG 증빙 자동화", layout="wide")
st.title("🧞 ESGenie · 대기업 공급망 실사 대응 데이터 빌더")
st.caption("DART 공시 + 내부 증빙(전기요금·폐기물 대장·규정집)을 하나로 묶어 "
           "대기업 제출용 정량 엑셀과 증빙 서류철을 자동 생성합니다.")

# ① 회사 정보 ---------------------------------------------------------
with st.sidebar:
    st.header("① 회사 정보")
    corp_name = st.text_input("회사명", value="(주)예시중소기업")
    corp_code = st.text_input("DART 종목코드(선택)", value="")
    industry = st.selectbox("업종", ["자동차부품", "전자부품", "화학", "금속가공", "기타"])
    report_year = st.number_input("보고 연도", 2020, 2030, 2025)
    use_dart = st.checkbox("DART 사업보고서 연동", value=False,
                           help="중소기업은 비워두고 증빙만 업로드해도 됩니다")

# ② 증빙 업로드 -------------------------------------------------------
st.subheader("② 내부 증빙 파일 업로드")
st.markdown("한전 전기요금 고지서 · 도시가스 영수증 · 올바로 폐기물 대장 · "
            "안전보건위원회 회의록 · 사내 규정집 등 (PDF/이미지)")
uploads = st.file_uploader("증빙 파일", type=["pdf", "png", "jpg", "jpeg"],
                           accept_multiple_files=True)

# 업로드 미리보기 + 라우팅 결과 표시
upload_paths: dict[str, str] = {}
if uploads:
    tmp = OUT_ROOT / "_uploads"
    tmp.mkdir(parents=True, exist_ok=True)
    rows = []
    for uf in uploads:
        p = tmp / uf.name
        p.write_bytes(uf.getbuffer())
        upload_paths[uf.name] = str(p)
        decision = ocr_router.route_document(str(p))   # 1페이지 프리뷰 기반 라우팅
        rows.append({
            "파일": uf.name,
            "채널": decision.channel.value,
            "추정유형": decision.doc_type,
            "신뢰도": decision.confidence,
        })
    st.dataframe(rows, use_container_width=True)

# ③ 실행 -------------------------------------------------------------
run = st.button("🚀 파이프라인 실행", type="primary", disabled=not (uploads or use_dart))


def _run_pipeline() -> dict:
    """L0(통합) → L3(D1+규정검증) → L5(엑셀/서류철) 오케스트레이션."""
    llm = LLMClient() if LLMClient else _MockLLM()

    # --- L0-A: 증빙 OCR 추출 (채널 자동 분기) ---
    extractions = []
    for fname, path in upload_paths.items():
        decision = ocr_router.route_document(path)
        try:
            ext = ocr_router.extract_document(path, decision)   # 실제 OCR/VLM
        except NotImplementedError:
            ext = _demo_extraction(fname, decision)             # 데모 stub
        ext.source_file = fname
        extractions.append(ext)

    # --- L0: DART + OCR 통합 SSOT ---
    dart_report = load_report(corp_code) if (use_dart and load_report and corp_code) else None
    graph = evidence_graph.build_unified_graph(
        dart_report, extractions,
        corp_code=corp_code or "LOCAL", corp_name=corp_name, report_year=int(report_year),
    )

    # --- L3: D1 수치검증 (항목별) ---
    d1_scores = {}
    for code in TARGET_CODES:
        nodes = graph.nodes_by_metric(code)
        if not nodes:
            continue
        sent = f"{code} 값은 {nodes[-1].value}{nodes[-1].unit}이다."
        d1_scores[code] = detector_5axis.detect_d1_numeric(sent, code, graph).score

    # --- P축: 규정 검증 + 누락 보완 초안 ---
    policy_results, drafts = [], {}
    for code in POLICY_CODES:
        res = detector_5axis.audit_policy_documents(code, graph, llm)
        policy_results.append(res)
        if not res.passed:
            drafts[code] = detector_5axis.draft_missing_policy(
                code, res, corp_name, industry, llm)

    # --- L5: data_points + audit_trace + 엑셀/서류철 ---
    dps = audit_trace.build_data_points(graph, d1_scores, target_codes=TARGET_CODES)
    trace = audit_trace.build_audit_trace_v15(
        corp_code or "LOCAL", corp_name, dps, policy_results)
    out_dir = OUT_ROOT / f"{corp_code or 'LOCAL'}_{report_year}"
    paths = excel_exporter.export_datasheet(trace, out_dir, uploaded_files=upload_paths)
    return {"trace": trace, "drafts": drafts, "paths": paths, "graph": graph}


# ④ 결과 + ⑤ 다운로드 -----------------------------------------------
if run:
    with st.spinner("증빙 파싱 → SSOT 통합 → 검증 → 서류철 생성 중…"):
        result = _run_pipeline()
    trace = result["trace"]

    st.success(f"완료 · 정량 항목 {trace.summary['data_point_count']}개, "
               f"증빙확인 {trace.summary['verified_ratio']*100:.0f}%")

    tab1, tab2, tab3 = st.tabs(["📊 데이터시트", "📋 규정 검증", "✍️ 누락 보완 초안"])

    with tab1:
        st.dataframe([dp.to_dict() for dp in trace.data_points], use_container_width=True)
    with tab2:
        for pa in trace.policy_audit:
            badge = "✅ 통과" if pa["passed"] else "⚠️ 보완필요"
            st.markdown(f"**{pa['kesg_code']}** {badge}")
            for f in pa["findings"]:
                if f["status"] != "met":
                    st.markdown(f"- ❌ {f['requirement']} — {f['gap_comment']}")
    with tab3:
        for code, draft in result["drafts"].items():
            with st.expander(f"{code} 표준 조문 초안"):
                st.code(draft, language="markdown")

    st.divider()
    st.subheader("⑤ 대기업 제출 서류 다운로드")
    paths = result["paths"]
    c1, c2 = st.columns(2)
    with c1:
        with open(paths["xlsx"], "rb") as fh:
            st.download_button("📥 정량 데이터시트 (xlsx)", fh.read(),
                               file_name="ESG_DataSheet_대기업제출용.xlsx")
    with c2:
        with open(paths["audit_json"], "rb") as fh:
            st.download_button("📥 감사 추적 (audit_trace_v15.json)", fh.read(),
                               file_name="audit_trace_v15.json")
    st.info(f"증빙 서류철 폴더: `{paths['evidence_dir']}` "
            f"(엑셀 '증빙 파일' 열의 하이퍼링크와 동일 파일)")
