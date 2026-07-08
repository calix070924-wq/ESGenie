# -*- coding: utf-8 -*-
"""probe_words_option.py — Upstage document-parse-260630 'words' 옵션 응답 구조 확인.

목적: 새 words 옵션이 단어 단위 bbox를 주는지, 어떤 요청 파라미터로 켜지는지 확인.
공식 문서가 릴리즈일(6/30)에 갱신된다고 공지됐으므로, 파라미터명은 아래 후보를
순서대로 시도해 실제로 무엇이 먹히는지 라이브로 검증한다.

⚠ Mac(.env에 유효 UPSTAGE_API_KEY)에서 실행. 샌드박스는 api.upstage.ai 차단됨.

사용:
    python -m scripts.probe_words_option 시연증빙세트_한울정밀공업/01_전기요금청구서_2026-05.pdf
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
from esgenie.config import SETTINGS  # noqa: F401
from esgenie.ssot import ocr_router as R

# 시도할 파라미터 후보 (문서 갱신 전이라 여러 형태를 실측)
CANDIDATES = [
    {"words": "true"},
    {"output_formats": "['html', 'text', 'words']"},
    {"return_words": "true"},
]


def main() -> None:
    if len(sys.argv) < 2:
        print("사용: python -m scripts.probe_words_option <pdf_path>")
        return
    path = sys.argv[1]
    key = R._get_upstage_key()
    if not key:
        print("UPSTAGE_API_KEY 없음 → .env 설정 후 Mac에서 실행")
        return

    import requests
    headers = {"Authorization": f"Bearer {key}"}
    doc_bytes = Path(path).read_bytes()

    for extra in CANDIDATES:
        data = {
            "model": "document-parse-260630",
            "ocr": "force",
            "output_formats": "['html', 'text']",
            "coordinates": "true",
            "base64_encoding": "[]",
            **extra,
        }
        files = {"document": (Path(path).name, doc_bytes)}
        print(f"\n=== 시도: {extra} ===")
        resp = requests.post(R._upstage_dp_url(), headers=headers, data=data, files=files, timeout=120)
        print("status:", resp.status_code)
        try:
            body = resp.json()
        except Exception:
            print(resp.text[:500])
            continue
        elements = body.get("elements", []) or []
        has_words = any("words" in (el.get("content") or {}) or "words" in el for el in elements)
        print("elements 수:", len(elements), " words 필드 감지:", has_words)
        if elements:
            print("첫 요소 키:", list(elements[0].keys()))
            print(json.dumps(elements[0], ensure_ascii=False)[:500])


if __name__ == "__main__":
    main()
