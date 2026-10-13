"""Replay saved 48-row projects through the web PDF endpoint; no AI/OCR calls.

Usage: python scripts/check_web_pdf_rehearsal.py SOURCE_DIR OUTPUT_DIR
SOURCE_DIR contains initial_project.json and final_project.json from rehearsal.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fitz
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from esgenie.supplychain.exporters._fonts import pdf_safe_text
from esgenie.web.app import create_app
from esgenie.web.presenter import presented_answers

WEIGHTS = [125, 85, 110, 75, 195, 203]


def compact(text):
    return re.sub(r"\s+", "", pdf_safe_text(str(text)))


def source_text(source):
    page = f"{source['page'] + 1}쪽" if source['page'] is not None else "페이지 미확인"
    return f"{source['name']} · {page} · 버전 {source.get('version') or 1}\n{source['quote']}"


def expected_cells(row):
    sources = "\n\n".join(source_text(s) for s in row["sources"]) or "연결된 근거 없음"
    sources += "".join("\n\n보완 대상(값 산정 미사용): " + source_text(s) for s in row["reference_sources"])
    notes = "\n".join(dict.fromkeys([row["status_label"], row["why"], row["next_step"], *row["notices"],
                                    "메모 / 자료 부족 사유: " + row["saved"]["memo"],
                                    "수정 이유: " + row["saved"]["reason"]]))
    return [row["id"] + "\n" + row["question"], row["value_text"] + "\n" + row["method"],
            row["scope_label"] or "범위 미확인", row["review_label"], sources, notes]


def inspect_pdf(path, rows):
    ids = {r["id"] for r in rows}
    extracted = {qid: [[] for _ in WEIGHTS] for qid in ids}
    page_checks = []
    with fitz.open(path) as pdf:
        for number, page in enumerate(pdf, 1):
            spans = [s for b in page.get_text("dict")["blocks"] for line in b.get("lines", []) for s in line["spans"]]
            for s in spans:
                x0, y0, x1, y1 = s["bbox"]
                assert 24 <= x0 < x1 <= page.rect.width - 24 + 0.5, (number, s)
                assert 0 <= y0 < y1 <= page.rect.height - 9, (number, s)
            text = page.get_text()
            assert text.count("저장된 답변") <= 1, number
            header = page.search_for("저장된 답변")
            if not header:  # Appendix-only page has no response table.
                page_checks.append({"page": number, "appendix": True})
                continue
            top = header[0].y1 + 20
            width = page.rect.width - 60
            boundaries = [30 + sum(WEIGHTS[:i]) * width / sum(WEIGHTS) for i in range(7)]
            body = [s for s in spans if top <= s["bbox"][1] < page.rect.height - 28]
            starts = [(s["bbox"][1], s["text"]) for s in body
                      if s["bbox"][0] < boundaries[1] and s["text"] in ids]
            continuation = next((qid for qid in ids if f"{qid} (계속)" in text), None)
            if continuation:
                starts.insert(0, (top, continuation))
            starts.sort()
            assert starts, (number, text)
            for i, (start, qid) in enumerate(starts):
                end = starts[i + 1][0] if i + 1 < len(starts) else page.rect.height - 28
                for column in range(6):
                    extracted[qid][column].extend(s["text"] for s in body if start <= s["bbox"][1] < end
                                                 and boundaries[column] <= s["bbox"][0] < boundaries[column + 1])
            page_checks.append({"page": number, "questions": [qid for _, qid in starts],
                                "continued": continuation, "headers": 1})
        title = pdf[0].get_text()
        assert "한울정밀공업(주)" in title
    checks = []
    for row in rows:
        actual = [compact("\n".join(cell)) for cell in extracted[row["id"]]]
        expected = [compact(cell) for cell in expected_cells(row)]
        matches = [a == e for a, e in zip(actual, expected)]
        checks.append({"id": row["id"], "columns_match": matches})
        assert all(matches), {"id": row["id"], "matches": matches, "actual": actual, "expected": expected}
    return {"pages": page_checks, "rows": checks, "exact_cell_matches": len(rows) * 6}


def render(path):
    out = path.parent / "renders"
    out.mkdir(exist_ok=True)
    subprocess.run(["pdftoppm", "-r", "72", "-png", str(path), str(out / "page")], check=True)
    pages = sorted(out.glob("page-*.png"))
    for offset in range(0, len(pages), 4):
        sheet = Image.new("RGB", (1684, 1232), "#dfe5e1")
        draw = ImageDraw.Draw(sheet)
        for index, file in enumerate(pages[offset:offset + 4]):
            x, y = index % 2 * 842, index // 2 * 616
            with Image.open(file) as image:
                sheet.paste(image, (x, y + 20))
            draw.text((x + 8, y + 4), f"Page {offset + index + 1}", fill="black")
        sheet.save(out / f"contact-{offset // 4 + 1:02d}.png")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    records = {}
    final = json.loads((args.source / "final_project.json").read_text())
    for stage in ("initial", "final", "stress"):
        project = json.loads((args.source / f"{stage}_project.json").read_text()) if stage != "stress" else deepcopy(final)
        if stage == "stress":
            # Synthetic saved edit based on a copy, preserving the real inputs.
            rows = presented_answers(project)
            target = next(r for r in rows if r["id"] == "RBA-E-10")
            values = deepcopy(target["saved"])
            quote = values["sources"][0]["quote"]
            values["sources"][0]["quote"] = quote + "\n" + "\n".join(f"긴 인용 {i:03d}: 책임·조치·종결·원문을 확인했다." for i in range(130))
            values["sources"].append({**values["sources"][0], "name": "아주_긴_파일명_" * 8 + "원문㈜_<보관>&확인.pdf",
                                      "page": 6, "version": 7, "quote": "추가 원문 마지막 줄 46+4=50, 안내 수령 73명"})
            values.update(answer="담당자 저장 답변 <확인> & 보완㈜", scope="2026년 4월 · 제1공장 (저장 범위)",
                          memo="\n".join(f"저장 메모 {i:03d}: <원문> & 담당자㈜ 확인" for i in range(85)),
                          reason="저장한 수정 이유: 범위·원문 대조")
            project["reviews"][target["id"]] = {"values": values, "status": "complete", "method": "담당자 수정", "framework": project["framework"]}
            project["affected_questions"] = [qid for qid in project["affected_questions"] if qid != target["id"]]
        destination = args.output / stage
        destination.mkdir(exist_ok=True)
        with TestClient(create_app(data_root=destination / "workspace"), headers={"X-ESGenie-Client": "workspace"}) as client:
            client.app.state.store.save(project)
            before = client.app.state.store.read(project["id"])
            rows = client.get(f"/api/projects/{project['id']}").json()["result"]["answers"]
            assert len(rows) == 48
            response = client.get(f"/api/projects/{project['id']}/download/pdf")
            assert response.status_code == 200, response.text
            assert before == client.app.state.store.read(project["id"])
        path = destination / "응답서.pdf"
        path.write_bytes(response.content)
        (destination / "screen_rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))
        records[stage] = inspect_pdf(path, rows)
        records[stage]["pdf_sha256"] = hashlib.sha256(response.content).hexdigest()
        render(path)
        print(stage, len(records[stage]["pages"]), "pages;", records[stage]["exact_cell_matches"], "exact cells")
    records["source_hashes"] = {name: hashlib.sha256((args.source / name).read_bytes()).hexdigest()
                                for name in ("initial_project.json", "final_project.json")}
    records["base_sha"] = "6f5dd9488b54570b2115345e4a74d35d2aa2b28c"
    records["ai_ocr_requests"] = 0
    records["dataset"] = "hanwool_bm_normal5_20261007_v1"
    (args.output / "content_checks.json").write_text(json.dumps(records, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
