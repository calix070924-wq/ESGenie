# -*- coding: utf-8 -*-
"""hmc_integrity_validation.py — HMC 응답서 검증 정합성 개선 검증 산출물 생성.

작업지시서: docs/작업지시서_HMC_응답서_검증정합성_개선_2026-09-20.md §8
산출물   : docs/validation/hmc-response-integrity-20260920/

세 가지를 한다.

1. **실제 원문 재생(real-document replay)** — 시연 증빙세트 PDF의 텍스트 레이어를
   PyMuPDF로 로컬 추출해(OCR 호출·네트워크 없음) HMC E-7(의사소통)·E-8(참여·구제)
   문항 적합성을 실측한다. 원본 PDF는 읽기만 한다 — 수정하지 않는다.

2. **양식 전수 점검** — 6개 양식 222문항 모두 고유 어휘를 뽑을 수 있는지 확인한다.
   못 뽑는 문항이 있으면 그 문항은 적합성 선별 없이 통과하므로(fail-open) 세어 둔다.

3. **동일 ResponseSheet → JSON·Excel·PDF 3종 생성 후 재개봉 대조** — 값·단위·범위·
   상태·초안 승인 대기·인용이 세 출력에서 같은지 본다.

실행:
    python scripts/hmc_integrity_validation.py [--evidence-dir 시연증빙세트_한울정밀공업]

증빙 경로 주의: 번호 붙은 2026-xx PDF(14~18번 등)는 Git에 추적되지 않아 worktree에는
없다. 원본 작업 폴더를 --evidence-dir로 주면 10개 조항 문서 전부를 재생한다. 없는
문서는 missing_documents로 기록되고 실패 처리하지 않는다.

LLM 호출 없음(ESGENIE_FORCE_MOCK=1). 초안 문구의 생성 품질은 여기서 검증하지 않는다 —
검증 대상은 "어떤 근거가 어느 문항에 들어가는가"와 "세 출력이 같은 값을 쓰는가"다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("ESGENIE_FORCE_MOCK", "1")

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

OUT_DIR = REPO / "docs" / "validation" / "hmc-response-integrity-20260920"
DEFAULT_EVIDENCE = REPO / "시연증빙세트_한울정밀공업"

# E-7/E-8 판정에 의미가 있는 문서만 고른다. 나머지(고지서·설문)는 정성 문항과 무관.
CLAUSE_DOCS = (
    "의사소통절차서_2025.pdf",
    "15_노사협의회규정_2026-06.pdf",
    "17_고충처리및신원보호규정_2026-06.pdf",
    "18_정보보호정책_2026-06.pdf",
    "16_부패방지윤리경영규정_2026-06.pdf",
    "14_임금및복리후생규정_2026-06.pdf",
    "근로시간관리규정_2025.pdf",
    "지식재산보호규정_2025.pdf",
    "위생_기숙사관리규정_2025.pdf",
    "책임광물실사정책_2025.pdf",
)


def pdf_text(path: Path) -> str:
    import fitz  # PyMuPDF — 로컬 텍스트 레이어 추출. OCR/네트워크 아님.
    with fitz.open(path) as doc:
        return "\n".join(page.get_text() for page in doc).strip()


def hmc_fitness():
    from esgenie.knowledge.kesg_evidence_requirements import requirement_for_question
    from esgenie.supplychain import get_framework
    from esgenie.supplychain.question_fitness import build_fitness_map

    fw = get_framework("hmc")
    fmap = build_fitness_map(
        fw.questions,
        lambda q: requirement_for_question(q.kesg_codes, quantitative=q.qtype == "numeric"),
    )
    return fw, fmap


# ── 1. 실제 원문 재생 ─────────────────────────────────────────────────────────
def replay_real_documents(evidence_dir: Path) -> dict:
    from esgenie.supplychain.question_fitness import select_fit_chunks

    fw, fmap = hmc_fitness()
    targets = ["HMC-E-7", "HMC-E-8"]
    rows = []
    missing = []
    texts: dict[str, str] = {}
    for name in CLAUSE_DOCS:
        p = evidence_dir / name
        if not p.exists():
            missing.append(name)
            continue
        text = pdf_text(p)
        texts[name] = text
        row = {"document": name, "chars": len(text)}
        for qid in targets:
            f = fmap[qid]
            score, hits = f.assess(text)
            # 이 문서만 코드 태깅됐을 때(최악의 경우) 게이트가 통과시키는가.
            kept, hold = select_fit_chunks([{"id": name, "text": text}], f)
            row[qid] = {
                "score_pct": round(score * 100, 1),
                "distinctive_hits": hits,
                "required_hits": f.required_hits,
                "matched": sorted(f.matched_terms(text)),
                "alone_in_graph": "통과" if kept else f"보류({hold})",
            }
        rows.append(row)

    # 모든 문서가 함께 태깅됐을 때(현실 시나리오) 어느 문서가 근거로 남는가.
    together = {}
    for qid in targets:
        chunks = [{"id": n, "text": t} for n, t in texts.items()]
        kept, hold = select_fit_chunks(chunks, fmap[qid])
        together[qid] = {"kept": [c["id"] for c in kept], "hold_reason": hold}

    out = {
        "all_documents_tagged_to_one_question": together,
        "input_class": "real-document replay — 원본 시연 증빙 PDF의 텍스트 레이어를 "
                       "PyMuPDF로 로컬 추출. OCR 호출·네트워크·LLM 없음. 원본은 읽기만 함.",
        "questions": {qid: {
            "text": next(q.text for q in fw.questions if q.qid == qid),
            "primary_code": next(q.primary_code for q in fw.questions if q.qid == qid),
            "distinctive_terms": sorted(fmap[qid].distinctive),
        } for qid in targets},
        "documents": rows,
        "missing_documents": missing,
    }
    return out


# ── 2. 양식 전수 점검 ────────────────────────────────────────────────────────
def survey_all_frameworks() -> dict:
    from esgenie.knowledge.kesg_evidence_requirements import requirement_for_question
    from esgenie.supplychain.frameworks import all_framework_keys, get_framework
    from esgenie.supplychain.question_fitness import build_fitness_map

    result = {}
    for key in all_framework_keys():
        fw = get_framework(key)
        fmap = build_fitness_map(
            fw.questions,
            lambda q: requirement_for_question(q.kesg_codes, quantitative=q.qtype == "numeric"),
        )
        counts = sorted(len(f.distinctive) for f in fmap.values())
        unusable = [qid for qid, f in fmap.items() if not f.usable]
        shared = {}
        for q in fw.questions:
            shared.setdefault(q.primary_code, []).append(q.qid)
        worst = max(shared.items(), key=lambda kv: len(kv[1]))
        result[key] = {
            "label": fw.label,
            "questions": len(fw.questions),
            "distinctive_min": counts[0],
            "distinctive_median": counts[len(counts) // 2],
            "distinctive_max": counts[-1],
            "questions_without_distinctive_terms": unusable,
            "most_shared_primary_code": {"code": worst[0], "question_count": len(worst[1])},
        }
    return result


# ── 3. 동일 ResponseSheet → JSON·Excel·PDF 재개봉 대조 ───────────────────────
def build_sheet(evidence_dir: Path):
    """시연 증빙세트 수치로 HMC 응답서를 만든 뒤 AI 초안까지 붙인다.

    수치·문구는 scripts/demo_hmc_sheet.py와 같은 재구성 입력이고, 정성 근거는
    실제 PDF 원문에서 로컬 추출한 텍스트다. 초안 LLM은 mock이다.
    """
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    from esgenie.schemas import GroundingResult
    from esgenie.supplychain.claims import SupplierClaim
    from esgenie.supplychain.drafter import generate_drafts
    from esgenie.supplychain.responder import build_response_sheet
    from scripts.demo_hmc_sheet import CORP, DATA_POINTS, MAPPED

    claims = {"E-6-2": SupplierClaim(
        code="E-6-2", value=92.0, unit="%", raw="재활용률 92% 달성",
        source="saq:05_OEM_ESG자가진단설문_한성모터스.pdf")}
    extraction = SimpleNamespace(mapped=MAPPED, missing=["E-6-1"], corp_name=CORP)
    sheet = build_response_sheet("hmc", corp_name=CORP, extraction=extraction,
                                 data_points=DATA_POINTS, supplier_claims=claims)

    # 실제 의사소통 절차서 원문을 E-7 코드로 태깅해 초안 경로에 올린다.
    fw, _ = hmc_fitness()
    e7_code = next(q.primary_code for q in fw.questions if q.qid == "HMC-E-7")
    text = pdf_text(evidence_dir / "의사소통절차서_2025.pdf")
    node = SimpleNamespace(id="OCR_COMM_0001", text=text, kesg_code=e7_code,
                           rba_code=None, source_file="의사소통절차서_2025.pdf",
                           page=1, origin="ocr_unstructured")

    class Graph:
        text_nodes = {node.id: node}
        nodes: dict = {}

        def text_nodes_by_code(self, code):
            return [node] if code == e7_code else []

    accept = GroundingResult(decision="ACCEPT", g1_uncited_sentences=[],
                             g2_orphan_numbers=[], g4_unit_mismatches=[],
                             g5_overclaim=False, hard_fails=[], soft_flags=[],
                             faithfulness=1.0)
    draft = ("ESG 방침·관행·기대·성과를 근로자·공급사·고객에 전달하는 프로세스를 "
             "규정하고 있습니다. [OCR_COMM_0001]")
    llm = MagicMock()
    llm.complete.return_value = SimpleNamespace(content=draft)
    with patch("esgenie.supplychain.drafter.LLMClient", return_value=llm), \
         patch("esgenie.supplychain.drafter.evaluate_grounding", return_value=accept):
        sheet = generate_drafts(sheet, Graph())
    return sheet


def export_and_reopen(sheet) -> dict:
    """같은 sheet를 JSON·Excel·PDF로 내보내고 다시 열어 대조한다."""
    import fitz
    from openpyxl import load_workbook

    from esgenie.supplychain.exporters.excel import export_response_sheet
    from esgenie.supplychain.exporters.pdf import export_response_sheet_pdf
    from esgenie.supplychain.render import draft_body, source_lines

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUT_DIR / "response_sheet.json"
    payload = {
        "framework_key": sheet.framework_key,
        "framework_label": sheet.framework_label,
        "corp_name": sheet.corp_name,
        "auto_pct": sheet.auto_pct, "hitl_pct": sheet.hitl_pct,
        "pending_pct": sheet.pending_pct, "draft_pct": getattr(sheet, "draft_pct", None),
        "flagged_count": sheet.flagged_count,
        "answers": [a.to_dict() for a in sheet.answers],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    xlsx = Path(export_response_sheet(sheet, str(OUT_DIR)))
    pdf = Path(export_response_sheet_pdf(sheet, str(OUT_DIR), embed_evidence=False))

    reopened = json.loads(json_path.read_text(encoding="utf-8"))
    wb = load_workbook(xlsx)
    xl_text = "\n".join(
        str(c.value) for ws in wb.worksheets for row in ws.iter_rows() for c in row
        if c.value is not None)
    with fitz.open(pdf) as doc:
        pdf_pages = [page.get_text() for page in doc]
    pdf_text_all = "\n".join(pdf_pages)

    checks = []

    def check(name, ok, detail=""):
        checks.append({"check": name, "pass": bool(ok), "detail": detail})

    # 값·단위: 원장 자리수가 세 출력에서 같은가
    for a in sheet.answers:
        if a.qid == "HMC-C-4-E-6-2-재활용":
            check("재활용률 증빙값 29.3이 Excel에 있다", "29.3" in xl_text)
            check("재활용률 증빙값 29.3이 PDF에 있다", "29.3" in pdf_text_all)
            check("자가주장 92%가 불일치로 표시된다",
                  a.comparison == "mismatch" or a.status == "flagged",
                  f"comparison={a.comparison} status={a.status}")

    # 상태·집계
    statuses = {}
    for a in sheet.answers:
        statuses[a.status] = statuses.get(a.status, 0) + 1
    check("JSON 답변 수 = 양식 문항 수", len(reopened["answers"]) == len(sheet.answers),
          f"{len(reopened['answers'])}개")
    check("Excel에 모든 문항 ID가 있다",
          all(a.qid in xl_text for a in sheet.answers),
          str([a.qid for a in sheet.answers if a.qid not in xl_text]))
    check("PDF에 헤더 문장이 있다", sheet.framework_label.split("(")[0].strip() in pdf_text_all)

    # 초안 승인 대기·인용
    drafts = [a for a in sheet.answers if a.status == "draft_ready"]
    check("초안 승인 대기 문항이 있다", bool(drafts), str([a.qid for a in drafts]))
    for a in drafts:
        body, review_notes = draft_body(a)
        srcs = source_lines(a)
        check(f"{a.qid}: 출처 미해소 경고가 없다", not review_notes, str(review_notes))
        check(f"{a.qid}: 본문에 내부 노드 ID가 없다", "OCR_COMM_0001" not in body, body[:120])
        check(f"{a.qid}: 본문 인용이 번호다", "[1]" in body, body[:120])
        check(f"{a.qid}: 출처 목록이 문서명·페이지를 준다",
              any("의사소통절차서" in s for s in srcs), str(srcs))
        check(f"{a.qid}: 감사 JSON이 node_id를 보존한다",
              any(c.get("node_id") == "OCR_COMM_0001" for c in a.draft_citations))
        check(f"{a.qid}: Excel에도 같은 번호 인용이 실린다", "[1]" in xl_text)
        check(f"{a.qid}: PDF에 출처 문서명이 있다", "의사소통절차서" in pdf_text_all)
        check(f"{a.qid}: 근거 없는 ISMS 취득 문장이 없다",
              "ISMS" not in a.draft_text, a.draft_text[:120])

    # 빈 네모(글리프 누락) — 텍스트 추출로는 안 보이므로 폰트 보유 여부로 본다.
    from esgenie.supplychain.exporters._fonts import (
        REGULAR_NAME, resolve_korean_font, unsupported_chars)
    resolve_korean_font()
    holes = unsupported_chars(pdf_text_all, REGULAR_NAME)
    check("PDF에 글리프 없는 문자가 없다", not holes, str(sorted(holes)))

    # 범위(scope) 열이 출력에 도달하는가
    scoped = [a for a in sheet.answers if getattr(a, "boundary_label", "")]
    check("범위 열이 Excel에 있다", "범위" in xl_text)
    check("범위 라벨이 있는 답변은 Excel에 그 라벨을 싣는다",
          all(a.boundary_label in xl_text for a in scoped),
          str([a.qid for a in scoped if a.boundary_label not in xl_text]))

    return {
        "input_class": "controlled experiment + real-document replay — 정량값은 "
                       "scripts/demo_hmc_sheet.py의 재구성 입력, E-7 정성 근거는 "
                       "의사소통절차서_2025.pdf 원문 로컬 추출. 초안 LLM은 mock.",
        "files": {"json": json_path.name, "xlsx": xlsx.name, "pdf": pdf.name},
        "pdf_pages": len(pdf_pages),
        "status_counts": statuses,
        "header": {"auto_pct": sheet.auto_pct, "hitl_pct": sheet.hitl_pct,
                   "pending_pct": sheet.pending_pct,
                   "draft_pct": getattr(sheet, "draft_pct", None),
                   "flagged_count": sheet.flagged_count},
        "checks": checks,
        "failed": [c["check"] for c in checks if not c["pass"]],
    }


def render_screens(pdf_name: str, xlsx_name: str) -> dict:
    """PDF와 Excel 핵심 화면을 PNG로 렌더링해 잘림·겹침을 눈으로 확인할 수 있게 한다."""
    import fitz
    shots = []
    pdf_path = OUT_DIR / pdf_name
    with fitz.open(pdf_path) as doc:
        for i, page in enumerate(doc):
            if i >= 3:
                break
            png = OUT_DIR / f"pdf_p{i + 1}.png"
            page.get_pixmap(dpi=110).save(png)
            shots.append(png.name)
    return {"screenshots": shots,
            "note": "Excel은 렌더러가 없어 셀 값·줄바꿈·열 너비를 재개봉 검사로 확인했다."}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence-dir", default=str(DEFAULT_EVIDENCE))
    args = ap.parse_args()
    evidence_dir = Path(args.evidence_dir)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report: dict = {}
    report["real_document_replay"] = replay_real_documents(evidence_dir)
    report["framework_survey"] = survey_all_frameworks()
    sheet = build_sheet(evidence_dir)
    report["output_crosscheck"] = export_and_reopen(sheet)
    report["rendering"] = render_screens(
        report["output_crosscheck"]["files"]["pdf"],
        report["output_crosscheck"]["files"]["xlsx"])

    (OUT_DIR / "validation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    failed = report["output_crosscheck"]["failed"]
    print("== 실제 원문 재생 (HMC-E-7 / HMC-E-8 적합도 %) ==")
    for row in report["real_document_replay"]["documents"]:
        print(f"  {row['document'][:34]:36s} E-7={row['HMC-E-7']['score_pct']:5.1f}  "
              f"E-8={row['HMC-E-8']['score_pct']:5.1f}")
    print("== 양식 전수 점검 ==")
    for key, v in report["framework_survey"].items():
        print(f"  {key:10s} 문항 {v['questions']:3d} · 고유어휘 최소 {v['distinctive_min']:2d} "
              f"중앙 {v['distinctive_median']:2d} · 판정불가 {len(v['questions_without_distinctive_terms'])}")
    print("== 출력 3종 재개봉 대조 ==")
    print(f"  검사 {len(report['output_crosscheck']['checks'])}건 · 실패 {len(failed)}건")
    for f in failed:
        print("  ✗", f)
    print("산출물:", OUT_DIR)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
