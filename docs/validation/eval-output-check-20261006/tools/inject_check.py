#!/usr/bin/env python3
"""출력 일치 검사기(scripts/check_output_consistency.py)가 변조를 정말 잡는지 확인한다.

작업지시서 B §2 '검증' 2~4. 검사기가 통과만 하는 것으로는 아무것도 증명되지 않으므로,
같은 산출물에 20종의 변조를 넣어 **매번 실패하는지**와 **변경 없는 사본은 통과하는지**를 본다.

원본 산출물은 읽기만 한다.
  · Excel 3종(값·상태·범위)은 `--out` 아래에 **실제 파일 사본**을 만들어 변조하고,
    검사기를 그 폴더에 CLI로 돌려 종료 코드까지 확인한다.
  · 나머지는 메모리 사본(`load_bundle` 결과)만 바꾼다. PDF 변조는 반드시 **추출 텍스트
    단계**에서 한다 — PDF 파일을 다시 쓰면 검사 대상이 달라진다.
메모리 사본 방식은 `probe_output_checker_second_review_29defa0.py`가 쓴 것과 같다.

변조 대상 행은 문항 ID를 적어 넣지 않고 **데이터의 성질로 고른다**(소수 수치가 있는 행,
비율(%) 행, 답변·범위가 모두 빈 행 …). 어느 실행에도 그대로 돌아가고, 고른 행을 로그에 남긴다.

사용:
  python docs/validation/eval-output-check-20261006/tools/inject_check.py \
      --run-dir <run>/<stage> --out <결과 폴더>

종료 코드: 0 모두 기대대로 / 1 기대와 다른 주입이 있음 / 2 입력 오류.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]
CHECKER_PATH = ROOT / "scripts" / "check_output_consistency.py"


def load_checker():
    spec = importlib.util.spec_from_file_location("check_output_consistency", CHECKER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = load_checker()


# ── 변조 대상 행 고르기 (문항 ID를 적어 넣지 않는다) ─────────────────────────
def pick_rows(rows) -> dict[str, str]:
    """데이터의 성질로 대상 행을 고른다. 고른 결과는 로그에 남긴다."""
    picked: dict[str, str] = {}
    for r in rows:
        dv = r.display_value or ""
        if "decimal" not in picked and re.search(r"\d+\.\d{3,}", dv):
            picked["decimal"] = r.qid          # 소수 자리가 긴 수치 행
        if "percent" not in picked and "%" in dv:
            picked["percent"] = r.qid          # 비율 행
        if "blank" not in picked and dv.strip() == "—" and not r.boundary_label \
                and not r.comparison_label:
            picked["blank"] = r.qid            # 답변·범위가 모두 빈 행
        if "evidence" not in picked and len(r.evidence_file_names) >= 1:
            picked["evidence"] = r.qid         # 근거 링크가 있는 행
    # 맞바꿈 주입용 — 답변 **값이 서로 다른** 두 행이어야 한다.
    # 같은 값('예'끼리)을 맞바꾸면 산출물이 달라지지 않아 변조가 아니다.
    by_value: dict[str, str] = {}
    for r in rows:
        value = (r.display_value or "").strip()
        if value in ("", "—"):
            continue
        by_value.setdefault(value, r.qid)
    if len(by_value) >= 2:
        picked["swap_a"], picked["swap_b"] = list(by_value.values())[:2]
    return picked


# ── 주입 도구 ────────────────────────────────────────────────────────────────
def excel_set(b, qid: str, cell: str, value: str) -> dict | None:
    row = b.excel_rows.get(qid)
    if row is None:
        return None
    before = row[cell][0]
    row[cell] = (value, row[cell][1])
    return {"target": f"{qid} / Excel {row[cell][1]}", "before": str(before), "after": value}


def excel_sub(b, qid: str, cell: str, old: str, new: str) -> dict | None:
    row = b.excel_rows.get(qid)
    if row is None or old not in str(row[cell][0]):
        return None
    before = str(row[cell][0])
    row[cell] = (before.replace(old, new, 1), row[cell][1])
    return {"target": f"{qid} / Excel {row[cell][1]}", "before": f"…{old}…",
            "after": f"…{new}…"}


def excel_append(b, qid: str, cell: str, suffix: str) -> dict | None:
    row = b.excel_rows.get(qid)
    if row is None:
        return None
    before = str(row[cell][0])
    row[cell] = (before + suffix, row[cell][1])
    return {"target": f"{qid} / Excel {row[cell][1]}", "before": before[:60],
            "after": f"{before[:40]}… + {suffix!r}"}


def excel_bump_page(b, qid: str, cell: str) -> dict | None:
    """근거 칸의 쪽 표기를 한 자리 늘린다(`p.2` → `p.20`).

    쪽 번호를 적어 넣지 않는다 — 세트가 바뀌면 그 문서의 쪽이 달라진다(실측:
    예전 세트는 `p.1`, 새 세트의 같은 자리는 `p.2`). 실제 칸에서 패턴을 찾아
    바꾸되, 원래 값이 **부분 문자열로 남게** 늘린다 — 그래야 '포함 검사'로는
    통과하고 '칸 전체 일치'로만 잡히는 변조를 재현한다.
    """
    row = b.excel_rows.get(qid)
    if row is None:
        return None
    before = str(row[cell][0])
    m = re.search(r"p\.(\d+)", before)
    if m is None:
        return None
    old, new = m.group(0), f"p.{m.group(1)}0"
    row[cell] = (before.replace(old, new, 1), row[cell][1])
    return {"target": f"{qid} / Excel {row[cell][1]}", "before": old, "after": new}


def excel_swap(b, qid_a: str, qid_b: str, cell: str) -> dict | None:
    ra, rb = b.excel_rows.get(qid_a), b.excel_rows.get(qid_b)
    if ra is None or rb is None:
        return None
    va, vb = ra[cell][0], rb[cell][0]
    if str(va) == str(vb):
        return None        # 같은 값을 맞바꾸면 산출물이 달라지지 않는다 — 변조가 아니다
    ra[cell], rb[cell] = (vb, ra[cell][1]), (va, rb[cell][1])
    return {"target": f"{qid_a} ↔ {qid_b} / Excel {cell}",
            "before": f"{va!r} / {vb!r}", "after": f"{vb!r} / {va!r}"}


def pdf_sub(b, old: str, new: str, *, count: int = 0) -> dict | None:
    """추출 텍스트에서만 치환. PDF는 표 셀을 폭에 맞춰 줄바꿈하므로 공백을 허용한다."""
    pattern = re.compile(r"\s*".join(re.escape(c) for c in old))
    hit = 0
    for i, page in enumerate(b.pdf_pages):
        replaced, n = pattern.subn(new, page, count=count)
        if n:
            b.pdf_pages[i] = replaced
            hit += n
    if not hit:
        return None
    return {"target": f"PDF 추출 텍스트 {hit}곳", "before": old, "after": new or "(삭제)"}


def pdf_answer_dash(b, qid: str) -> dict | None:
    """답변 칸의 '—'만 바꾼다.

    문항 본문에도 '—'가 쓰이므로 블록의 첫 '—'를 바꾸면 문항 칸이 변조된다.
    답변 칸과 범위 칸이 모두 비면 '—'가 연달아 나오므로 그 쌍의 앞을 고른다.
    """
    pair = re.compile(r"—\s*—")
    for i, page in enumerate(b.pdf_pages):
        pos = page.find(qid)
        if pos == -1:
            continue
        m = pair.search(page, pos)
        if m is None:
            continue
        b.pdf_pages[i] = page[:m.start()] + "7" + page[m.start() + 1:]
        return {"target": f"{qid} / PDF 답변 칸", "before": "—", "after": "7"}
    return None


def pdf_body_tail(b, text: str) -> dict | None:
    """응답표 영역의 끝(체크리스트 표 앞)에 문구를 더한다."""
    for i, page in enumerate(b.pdf_pages):
        end = page.find(checker.PDF_BODY_END)
        if end == -1:
            continue
        b.pdf_pages[i] = page[:end] + f"\n{text}\n" + page[end:]
        return {"target": "PDF 응답표 영역 끝", "before": "(없음)", "after": text}
    return None


def pdf_header_in_row(b, qid: str) -> dict | None:
    """행 안에 표 머리글처럼 보이는 문구를 끼워 넣는다."""
    from esgenie.supplychain.exporters.excel import _HEADER

    header = "\n".join(_HEADER)
    for i, page in enumerate(b.pdf_pages):
        pos = page.find(qid)
        if pos == -1:
            continue
        cut = pos + len(qid)
        b.pdf_pages[i] = page[:cut] + f"\n{header}\n" + page[cut:]
        return {"target": f"{qid} / PDF 행 안", "before": "(없음)",
                "after": "표 머리글형 문구"}
    return None


# ── 주입 목록 ────────────────────────────────────────────────────────────────
def cases(p: dict[str, str]) -> list[tuple[str, str, str, object]]:
    """(이름, 대상, 설명, 적용 함수). 대상이 'excel_file'이면 실제 파일 사본으로 한다."""
    dec, pct, blank, ev = p.get("decimal"), p.get("percent"), p.get("blank"), p.get("evidence")
    sa, sb = p.get("swap_a"), p.get("swap_b")
    return [
        # 작업지시서 §2 검증 2 — Excel 사본 3종(값·상태·범위). 실제 파일로 만든다.
        ("excel_file_value", "excel_file", "Excel 답변 칸 값 1개 변경",
         lambda b: excel_set(b, dec, "answer", "0.5 TJ (2026년)")),
        ("excel_file_status", "excel_file", "Excel 신뢰 칸 상태 1개 변경",
         lambda b: excel_set(b, dec, "badge", "✅ 증빙검증")),
        ("excel_file_scope", "excel_file", "Excel 범위 문구 1개 변경",
         lambda b: excel_set(b, dec, "scope",
                             "측정 범위: 2026-01-01~2026-12-31 · 연간 · 전사 · 전체")),
        # Excel 추가
        ("excel_note_strip", "excel", "Excel 근거/비고에서 검토 사유 제거",
         lambda b: excel_set(b, dec, "note", "에너지 사용량 집계")),
        ("excel_note_number", "excel", "Excel 근거/비고의 수치 미세 변경(부분 문자열로는 통과)",
         lambda b: excel_sub(b, pct, "note", "29.3", "29.35")),
        ("excel_note_page", "excel", "Excel 근거/비고의 쪽 번호 변경(p.N → p.N0)",
         lambda b: excel_bump_page(b, ev, "note")),
        ("excel_scope_append", "excel", "Excel 범위 칸 끝에 문구 덧붙임",
         lambda b: excel_append(b, pct, "scope", " · 전사 · 연간")),
        ("excel_blank_scope_add", "excel", "비교 판정이 빈 행의 Excel 범위 칸에 문구 추가",
         lambda b: excel_append(b, blank, "scope", " · 범위 확인 필요")),
        ("excel_badge_blank_row", "excel", "다른 행(비교 판정 빈 행)의 Excel 신뢰 칸 변경",
         lambda b: excel_set(b, blank, "badge", "✅ 증빙검증")),
        ("excel_swap_answers", "excel", "서로 다른 두 행의 Excel 답변 칸 맞바꿈",
         lambda b: excel_swap(b, sa, sb, "answer")),
        # 작업지시서 §2 검증 3 — PDF 추출 텍스트 단계에서 같은 3종
        ("pdf_value", "pdf", "PDF 답변 값 변경",
         lambda b: pdf_sub(b, "0.513216", "0.5")),
        ("pdf_status", "pdf", "PDF 신뢰 칸 상태 변경",
         lambda b: pdf_sub(b, "자가신고", "증빙검증")),
        ("pdf_scope", "pdf", "PDF 범위 문구 변경",
         lambda b: pdf_sub(b, "사용전력량", "전사합산")),
        # PDF 추가
        ("pdf_pct_far", "pdf", "PDF 비율 값 크게 변경(29.3 → 92)",
         lambda b: pdf_sub(b, "29.3", "92")),
        ("pdf_pct_near", "pdf", "PDF 비율 값 미세 변경(29.3 → 29.8)",
         lambda b: pdf_sub(b, "29.3", "29.8")),
        ("pdf_evidence_rename", "pdf", "PDF 근거 파일명 변경",
         lambda b: pdf_sub(b, "02_전기요금청구서", "99_다른문서")),
        ("pdf_evidence_drop", "pdf", "PDF 근거 파일명 삭제",
         lambda b: pdf_sub(b, "02_전기요금청구서_2026-04.pdf", "")),
        ("pdf_answer_dash", "pdf", "PDF 답변 칸 '—' → '7'(범위 칸의 '—'를 소비하던 구멍)",
         lambda b: pdf_answer_dash(b, blank)),
        ("pdf_body_tail_add", "pdf", "PDF 응답표 영역 끝에 문구 추가",
         lambda b: pdf_body_tail(b, "전사 연간 총량으로 확정")),
        ("pdf_header_in_row", "pdf", "PDF 행 안에 표 머리글형 문구 삽입",
         lambda b: pdf_header_in_row(b, blank)),
    ]


# ── 실행 ─────────────────────────────────────────────────────────────────────
def run_excel_file_case(run_dir: Path, out_dir: Path, name: str, apply) -> dict:
    """run 폴더를 복사해 Excel 파일을 실제로 변조하고 검사기를 CLI로 돌린다."""
    import openpyxl

    copy_dir = out_dir / "copies" / name
    if copy_dir.exists():
        shutil.rmtree(copy_dir)
    copy_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(run_dir, copy_dir)

    xlsx = sorted((copy_dir / "exports" / "response_sheet").glob("*.xlsx"))[0]
    probe = checker.OutputBundle(
        json_rows=checker.load_json_rows(json.loads(
            (copy_dir / "result.json").read_text(encoding="utf-8"))),
        excel_rows=checker.load_excel_rows(xlsx), pdf_pages=[])
    record = apply(probe)                      # 어느 셀을 무엇으로 바꿀지 결정
    if record is None:
        shutil.rmtree(copy_dir)
        return {"name": name, "applied": False, "reason": "대상 행·문구를 찾지 못했다"}

    sheet_name, coord = record["target"].split("Excel ")[1].split("!")
    wb = openpyxl.load_workbook(xlsx)
    wb[sheet_name][coord] = record["after"]
    wb.save(xlsx)

    done = subprocess.run(
        [sys.executable, str(CHECKER_PATH), "--run-dir", str(copy_dir),
         "--out", str(out_dir / "checks" / name)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        payload = json.loads(done.stdout.strip() or "{}")
    except json.JSONDecodeError:
        payload = {"stdout": done.stdout[-300:], "stderr": done.stderr[-300:]}
    return {"name": name, "applied": True, "mode": "excel_file_copy",
            "copy_dir": str(copy_dir.relative_to(out_dir)), **record,
            "exit_code": done.returncode,
            "mismatches": payload.get("mismatches"),
            "cell_exact_mismatches": payload.get("cell_exact_mismatches")}


def run_memory_case(base, name: str, apply) -> dict:
    b = copy.deepcopy(base)
    record = apply(b)
    if record is None:
        return {"name": name, "applied": False, "reason": "대상 행·문구를 찾지 못했다"}
    outcome = checker.compare(b)
    return {"name": name, "applied": True, "mode": "memory_copy", **record,
            "mismatches": outcome["summary"]["mismatches"],
            "cell_exact_mismatches": outcome["summary"]["cell_exact_mismatches"],
            "fields": sorted({m["field"] for m in outcome["mismatches"]}),
            "related_fields": sorted({f for m in outcome["mismatches"]
                                      for f in m["related_fields"]})}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", type=Path, required=True, help="<run>/<stage> 폴더")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    try:
        base = checker.load_bundle(args.run_dir)
    except checker.InputError as exc:
        print(f"입력 오류: {exc}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)

    clean = checker.compare(base)
    picked = pick_rows(base.json_rows)
    results = []
    for name, kind, desc, apply in cases(picked):
        if kind == "excel_file":
            row = run_excel_file_case(args.run_dir, args.out, name, apply)
        else:
            row = run_memory_case(base, name, apply)
        row["description"] = desc
        row["target_kind"] = kind
        # 변조는 반드시 '깨끗한 상태보다 불일치가 늘어야' 한다.
        row["detected"] = bool(row.get("applied")) and \
            (row.get("mismatches") or 0) > clean["summary"]["mismatches"]
        row["expected"] = "실패(불일치 검출)"
        results.append(row)

    untouched = checker.compare(copy.deepcopy(base))
    log = {
        "run_dir": str(args.run_dir),
        "checker": str(CHECKER_PATH.relative_to(ROOT)),
        "checked_at": checker.datetime.now(checker.KST).isoformat(),
        "picked_rows": picked,
        "clean": {"mismatches": clean["summary"]["mismatches"],
                  "checked_field_values": clean["summary"]["checked_field_values"],
                  "checked_cells": clean["summary"]["checked_cells"],
                  "expected": "통과(불일치 0)"},
        "clean_copy_recheck": {"mismatches": untouched["summary"]["mismatches"],
                               "expected": "통과(불일치 0)"},
        "injections": results,
        "summary": {"total": len(results),
                    "applied": sum(1 for r in results if r.get("applied")),
                    "detected": sum(1 for r in results if r["detected"]),
                    "missed": [r["name"] for r in results if not r["detected"]]},
    }
    (args.out / "inject_log.json").write_text(
        json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out / "inject_summary.md").write_text(_markdown(log), encoding="utf-8")

    for r in results:
        mark = "검출" if r["detected"] else "** 미검출 **"
        print(f"  {mark:12s} {r['name']:24s} 불일치 {r.get('mismatches')} "
              f"({r.get('mode', '-')})")
    print(json.dumps({"clean": log["clean"]["mismatches"],
                      "clean_copy": log["clean_copy_recheck"]["mismatches"],
                      **log["summary"]}, ensure_ascii=False))
    ok = (log["clean"]["mismatches"] == 0
          and log["clean_copy_recheck"]["mismatches"] == 0
          and not log["summary"]["missed"])
    return 0 if ok else 1


def _markdown(log: dict) -> str:
    s = log["summary"]
    lines = [
        "# 주입 검사 — 검사기가 변조를 잡는지",
        "",
        f"- 대상 실행: `{log['run_dir']}`",
        f"- 검사기: `{log['checker']}`",
        f"- 검사 시각(KST): {log['checked_at']}",
        f"- 변조 대상으로 고른 행(데이터 성질로 선택): `{log['picked_rows']}`",
        "",
        "| 항목 | 기대 | 실제 |",
        "|---|---|---|",
        f"| 변경 없는 원본 | 통과(불일치 0) | 불일치 {log['clean']['mismatches']} |",
        f"| 변경 없는 사본 재검사 | 통과(불일치 0) | 불일치 {log['clean_copy_recheck']['mismatches']} |",
        f"| 주입 {s['total']}종 | 모두 실패(검출) | 검출 {s['detected']} / 적용 {s['applied']} |",
        "",
        f"미검출: {s['missed'] or '없음'}",
        "",
        "## 주입별 결과",
        "",
        "| # | 이름 | 방식 | 설명 | 바꾼 곳 | 전 → 후 | 기대 | 불일치 | 검출 |",
        "|---:|---|---|---|---|---|---|---:|---|",
    ]
    for i, r in enumerate(log["injections"], start=1):
        lines.append("| {i} | {n} | {m} | {d} | {t} | {b} → {a} | {e} | {c} | {ok} |".format(
            i=i, n=r["name"], m=r.get("mode", "-"), d=r["description"],
            t=_cut(r.get("target", r.get("reason", "—"))), b=_cut(r.get("before", "—")),
            a=_cut(r.get("after", "—")), e=r["expected"], c=r.get("mismatches", "—"),
            ok="예" if r["detected"] else "**아니오**"))
    return "\n".join(lines) + "\n"


def _cut(value: object, limit: int = 46) -> str:
    text = str(value).replace("|", "\\|").replace("\n", " ")
    return text[:limit] + "…" if len(text) > limit else text


if __name__ == "__main__":
    sys.exit(main())
