# -*- coding: utf-8 -*-
"""
dump_ocr.py — 특정 증빙에 대한 Azure OCR '원본'을 그대로 까본다.

OCR 엔진을 바꿀지 판단하려면 추측이 아니라 실제 추출물을 봐야 한다.
이 스크립트는 ① 라우팅 ② raw 텍스트(글자 인식 결과) ③ 정규화된 metric(코드/값/단위/bbox)
을 출력한다. '29.3 %' 같은 비율이 raw에 제대로 있으면 = OCR은 정상, 문제는 후처리.

사용:
    python3 -m scripts.dump_ocr                 # 폐기물 명세서(03) 기본
    python3 -m scripts.dump_ocr 01_*.pdf        # 다른 파일 지정(글롭/경로)
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EVID_DIR = REPO / "시연증빙세트_한울정밀공업"


def main():
    sys.path.insert(0, str(REPO))
    from esgenie.ssot import ocr_router

    pat = sys.argv[1] if len(sys.argv) > 1 else "03_*.pdf"
    matches = sorted(EVID_DIR.glob(pat)) or sorted(Path().glob(pat))
    if not matches:
        sys.exit(f"[!] 파일 못 찾음: {pat}")
    target = str(matches[0])
    print("대상:", Path(target).name)
    print("=" * 78)

    dec = ocr_router.route_document(target)
    print(f"라우팅: channel={dec.channel.value}  doc_type={dec.doc_type}  conf={dec.confidence}")

    ext = ocr_router.extract_document(target, dec)
    meta = ext.router_meta or {}
    print(f"엔진  : {meta.get('engine')}   azure_error={meta.get('azure_error')}")
    print("=" * 78)

    raw = getattr(ext, "raw_text", "") or ""
    print(f"[RAW 텍스트] 길이 {len(raw)}자")
    print("-" * 78)
    print(raw[:2500])
    print("-" * 78)

    # 비율(%) 출현 위치 — Azure가 '29.3 %'를 글자로 잡았는지 직접 확인
    hits = list(re.finditer(r"(\d{1,3}(?:\.\d+)?)\s*%", raw))
    print(f"\n[%] 패턴 출현 {len(hits)}건:")
    for h in hits:
        ctx = raw[max(0, h.start() - 25):h.end() + 3].replace("\n", " ")
        print(f"   …{ctx}…")

    print("\n[정규화된 metric]")
    for m in getattr(ext, "metrics", []) or []:
        print(f"   code={m.kesg_code_guess!s:7} value={m.value!s:>10} unit={m.unit!s:6} "
              f"hint={m.metric_hint!r} bbox={'Y' if m.bbox else '-'}")

    print("\n판정 가이드:")
    print("  · RAW/[%]에 '29.3 %'가 보이면 → Azure OCR 정상. 문제는 후처리(엔진 교체 불필요).")
    print("  · '29.3'이 깨졌거나(28.3 등) 한글이 망가졌으면 → OCR 인식 품질 이슈.")


if __name__ == "__main__":
    main()
