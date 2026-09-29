"""한울정밀 BM 개편 리허설(2026-09-28)의 저장 OCR 출력을 재생 픽스처로 고정한다.

입력은 리허설 `pipeline.json`의 `ocr_extractions[*]`다. 재생에 쓰는 것은 **Upstage
Document Parse 단계의 출력**(요소 text를 이어 붙인 raw_text, HTML 표를 파싱한 cells)이며,
그 뒤 단계(템플릿 매칭·정규화·metrics)는 싣지 않는다 — 재생 테스트가 추출 단계를 다시
돌리게 하기 위함이다.

한계: 리허설은 Upstage 원 응답을 저장하지 않았다. 요소 경계는 raw_text의 줄과 표 블록
(`|`로 시작하는 연속 줄)으로 복원하고, 비표 요소의 bbox는 저장되지 않아 None이다.
표 토큰 bbox는 cells에 공유된 표 외접 사각형을 쓴다.

사용:
  python scripts/build_ocr_numeric_replay_fixtures.py <pipeline.json> <출력 디렉터리>
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def rebuild_tokens(raw_text: str, tables: list[dict]) -> list[dict]:
    tokens: list[dict] = []
    table_iter = iter(tables)
    block: list[str] = []

    def flush() -> None:
        if not block:
            return
        t = next(table_iter, None)
        bbox = (t or {}).get("cells", [{}])[0].get("bbox") if t else None
        page = (t or {}).get("page", 0) if t else 0
        tokens.append({"text": "\n".join(block), "bbox": bbox, "page": page})
        block.clear()

    for line in raw_text.splitlines():
        if line.startswith("|"):
            block.append(line)
            continue
        flush()
        if line.strip():
            tokens.append({"text": line.strip(), "bbox": None, "page": 0})
    flush()
    return tokens


def main(pipeline_path: str, out_dir: str) -> None:
    src = Path(pipeline_path)
    data = json.loads(src.read_text(encoding="utf-8"))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for ext in data["ocr_extractions"]:
        if ext.get("channel") != "structured":
            continue
        name = ext["source_file"]
        fixture = {
            "source_file": name,
            "recorded_doc_type": ext["doc_type"],
            "source_sha256": ext["router_meta"].get("source_sha256"),
            "stage": "upstage_dp_output (raw_text elements + parsed HTML table cells)",
            "origin": {
                "pipeline_json": str(src),
                "pipeline_json_sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
                "code_commit": "6bf975005364e1a32aee1ca6e58c761edb11db08",
            },
            "recorded_metrics": ext["metrics"],
            "tokens": rebuild_tokens(ext["raw_text"], ext["tables"]),
            "tables": ext["tables"],
        }
        (out / f"{name.split('_')[0]}.json").write_text(
            json.dumps(fixture, ensure_ascii=False, indent=1), encoding="utf-8")
        print(name, len(fixture["tokens"]), "tokens", len(ext["tables"]), "tables")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
