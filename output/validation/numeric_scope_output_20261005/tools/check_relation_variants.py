"""`inject_relation_variants.py` 실행의 최종 파일(Markdown·보고서 PDF·감사 기록)에서 넣은 문장마다 결과를 대조한다(제품 코드 비의존).

사용: python check_relation_variants.py <run>/<stage> [--json out.json]
- hold: 넣은 주장이 Markdown·PDF에 확정 서술로 남지 않고, 감사 기록에 그 모델 원문의 `replaced` 기록이 있어야 한다.
- keep: Markdown·PDF에 그대로 있고 `replaced` 기록이 없어야 한다.
- observe: 결과만 기록한다(판정 대상 아님).
하나라도 기대와 다르면 종료 코드 1.
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path

import fitz


def flat(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def signature(text: str) -> tuple[str, list[str]]:
    """(형식, 확정 서술로 남으면 안 되는/남아야 하는 문구들). 인용 표기·끝 마침표는 뺀다."""
    text = re.sub(r"\s*\[[^\[\]]+\]", "", text).strip()
    if text.startswith("|"):
        rows = [r for r in text.splitlines() if r.startswith("|") and not re.fullmatch(r"\|[\s:|-]+\|?", r)]
        data = rows[-1]
        cells = [c.strip() for c in data.strip("|").split("|")]
        return "table", [" | ".join(cells), "".join(cells)]
    if text.startswith("#"):
        return "heading", [text.lstrip("#").strip()]
    return "sentence", [text.rstrip(" .")]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", type=Path)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    events = json.loads((args.stage / "injection_events.json").read_text(encoding="utf-8"))
    md = Path(glob.glob(str(args.stage / "exports" / "*" / "ESG보고서_*.md"))[0]).read_text(encoding="utf-8")
    pdf_path = glob.glob(str(args.stage / "exports" / "*" / "ESG보고서_*.pdf"))[0]
    pdf = " ".join(page.get_text() for page in fitz.open(pdf_path))
    reviews = json.loads(Path(glob.glob(str(args.stage / "exports" / "*" / "report_body_review.json"))[0])
                         .read_text(encoding="utf-8"))
    replaced = [r for r in reviews if r.get("action") == "replaced"]
    rows, ok = [], True
    for event in events["events"]:
        for v in event["variants"]:
            kind, needles = signature(v["text"])
            # 확인 보류 문구가 인용한 모델 수치(`생성 문장의 수치(…)`)는 확정 서술이 아니다 — 문구 단위로 본다.
            in_md = (needles[0] in md) if kind != "table" else (f"| {needles[0]} |" in md)
            in_pdf = flat(needles[-1]) in flat(pdf)
            record = [r for r in replaced if flat(needles[0].split(" | ")[-1]) in flat(r.get("model_text") or "")
                      and (kind == "table" or flat(needles[0])[:16] in flat(r.get("model_text") or ""))]
            if v["expect"] == "hold":
                passed = not in_md and not in_pdf and bool(record)
            elif v["expect"] == "keep":
                passed = in_md and in_pdf and not record
            else:
                passed = None
            ok = ok and passed is not False
            rows.append({"event": event["kind"], "variant": v["name"], "expect": v["expect"], "kind": kind,
                         "in_markdown": in_md, "in_pdf": in_pdf, "replaced_records": len(record),
                         "hold_output": (record[0].get("output") or "")[:240] if record else "",
                         "reason": record[0].get("reason") if record else "", "problems": record[0].get("problems")
                         if record else None, "pass": passed})
    for r in rows:
        state = "OBSERVE" if r["pass"] is None else ("PASS" if r["pass"] else "FAIL")
        print(f"{state} [{r['event']}] {r['variant']} :: md={r['in_markdown']} pdf={r['in_pdf']} "
              f"replaced={r['replaced_records']} {r['reason']} {r['problems'] or ''}")
    judged = [r for r in rows if r["pass"] is not None]
    print(f"variants passed {sum(r['pass'] for r in judged)}/{len(judged)} · observed {len(rows) - len(judged)}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({"stage": str(args.stage), "rows": rows, "all_pass": ok}, ensure_ascii=False,
                                        indent=2), encoding="utf-8")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
