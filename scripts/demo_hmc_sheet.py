# -*- coding: utf-8 -*-
"""demo_hmc_sheet.py — 현대차 실사 응답서(hmc) 샌드박스 데모.

라이브 OCR(Azure) 없이도 본선 양식이 어떻게 나오는지 보기 위해, 한울정밀공업㈜의
시연 증빙 세트 수치를 파이프라인 산출물(DataPoint + mapped + supplier_claims) 형태로
재구성해 현대차 양식에 물리고 Excel/PDF로 내보낸다.

수치 출처: 시연증빙세트_한울정밀공업/00_README_세트안내.md (모두 가상).
폐기물 재활용률에 D6/D1 그린워싱 모순(증빙 29.3% vs SAQ 자가주장 92%)이 심겨 있다.
"""
from __future__ import annotations

from types import SimpleNamespace

from esgenie.ssot.audit_trace import DataPoint, EvidenceLink
from esgenie.supplychain.claims import SupplierClaim
from esgenie.supplychain.exporters.excel import export_response_sheet
from esgenie.supplychain.exporters.pdf import export_response_sheet_pdf
from esgenie.supplychain.responder import build_response_sheet

CORP = "한울정밀공업㈜"
OUT = "outputs/_supplychain_demo"


def _ev(name: str, node: str) -> EvidenceLink:
    return EvidenceLink(file_name=name, relative_path="", origin="ocr_structured",
                        bbox=[0.1, 0.2, 0.3, 0.24], page=0, node_id=node)


# ── 증빙에서 확정된 정량값(verified) ──────────────────────────────────────
# 환경 정량(고지서·명세서) + 안전 재해율. 모두 시연용 가상 수치.
DATA_POINTS = [
    DataPoint("E-4-1", "에너지 사용량", 17.6, "TJ", 2026, 0.96,
              "verified", 0.04, [_ev("01_전기요금청구서_2026-05.pdf", "n_elec")]),
    DataPoint("E-6-1", "폐기물 배출량", 18.4, "톤", 2026, 0.92,
              "verified", 0.06, [_ev("03_사업장폐기물_위탁처리명세_2026-04.pdf", "n_waste")]),
    # 재활용률 — 증빙상 실제값 29.3% (5,400 / 18,400kg). SAQ 자가주장 92%와 충돌(D6/D1).
    DataPoint("E-6-2", "폐기물 재활용 비율", 29.3, "%", 2026, 0.90,
              "verified", 0.10, [_ev("03_사업장폐기물_위탁처리명세_2026-04.pdf", "n_waste")]),
    # 안전보건 — 산업재해율(사망만인율). 안전보건관리계획서 기준 가상값.
    DataPoint("S-4-2", "산업재해율", 0.94, "‰", 2026, 0.88,
              "verified", 0.08, [_ev("08_안전보건관리계획서_2026.pdf", "n_safety")]),
]

# ── 공시/규정 존재(presence) — 212명 자동차부품 1차 협력사가 보유할 법한 대표 증빙 ──
# 환경방침·인권방침·안전보건방침·윤리강령·정보보호·노사협의회·협력사관리 등.
# (본선용 실제 PDF는 추후 증빙세트 확장 시 생성. 여기서는 보유 가정한 대표 문서.)
MAPPED = {
    c: {"code": c, "name": c, "evidence_node_ids": ["n_doc"]}
    for c in [
        # 환경
        "E-1-1", "E-1-2", "E-4-1", "E-6-1", "E-6-2",
        # 노동·인권
        "S-5-1", "S-2-5", "S-2-6", "S-3-1",
        # 안전보건
        "S-4-1", "S-4-2",
        # 윤리 (G-4-1은 서술필요 hitl이라 presence로도 작성필요 유지)
        "S-8-1",
        # 경영시스템
        "S-5-2", "S-1-1", "S-2-4", "G-5-1", "S-6-1",
    ]
}

# ── 협력사 SAQ 자가주장 (그린워싱 트리거) ─────────────────────────────────
CLAIMS = {
    "E-6-2": SupplierClaim(code="E-6-2", value=92.0, unit="%",
                           raw="재활용률 92% 달성", source="saq:05_OEM_ESG자가진단설문_한성모터스.pdf"),
}


def main() -> None:
    extraction = SimpleNamespace(mapped=MAPPED, missing=["E-6-1"], corp_name=CORP)
    sheet = build_response_sheet(
        "hmc",
        corp_name=CORP,
        extraction=extraction,
        data_points=DATA_POINTS,
        supplier_claims=CLAIMS,
    )
    print(f"[{sheet.framework_label}] {sheet.corp_name}")
    print(f"  자동응답 {sheet.auto_pct}% · 작성필요 {sheet.hitl_pct}% · "
          f"증빙대기 {sheet.pending_pct}% · 검토필요 {sheet.flagged_count}건")
    for a in sheet.answers:
        if a.status == "flagged":
            print(f"  🚩 {a.qid} {a.question_text[:30]} → {a.rationale}")

    xlsx = export_response_sheet(sheet, OUT)
    pdf = export_response_sheet_pdf(sheet, OUT, embed_evidence=False)
    print("Excel:", xlsx)
    print("PDF  :", pdf)


if __name__ == "__main__":
    main()
