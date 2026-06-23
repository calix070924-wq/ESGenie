# -*- coding: utf-8 -*-
"""verify_routing_live.py — 라우팅 패치 A·B Mac 라이브 재검증.

샌드박스는 Upstage 망차단이라 A의 OCR 호출이 빈 결과(→파일명 폴백)로 보일 수 있다.
Mac(.env의 UPSTAGE_API_KEY 유효)에서 실행하면 두 패치가 라이브로 닫힌다:

  [B] 정형 문서 route_document이 layout_features(table_area_ratio)를 자동 주입해
      키워드가 약해도 표구조로 정형 승격되는지.
  [A] 스캔본(텍스트레이어 없는) PDF도 Upstage DP 1p 에스컬레이션으로 본문 키워드를
      확보해 파일명이 아닌 OCR 텍스트로 정형 라우팅되는지.

사용:
  python -m scripts.verify_routing_live                # 데모 세트 자동 사용
  python -m scripts.verify_routing_live a.pdf b.pdf    # 임의 PDF 지정
"""
from __future__ import annotations

import sys
from pathlib import Path

# .env 로드를 위해 config를 가장 먼저 import (config가 load_dotenv를 수행).
from esgenie.config import SETTINGS  # noqa: F401
from esgenie.ssot import ocr_router as R

DEMO_DIR = Path("시연증빙세트_한울정밀공업")
DEFAULT_STRUCTURED = [
    DEMO_DIR / "01_전기요금청구서_2026-05.pdf",
    DEMO_DIR / "02_도시가스요금고지서_2026-05.pdf",
    DEMO_DIR / "03_사업장폐기물_위탁처리명세_2026-04.pdf",
]


def _upstage_ready() -> bool:
    return bool(R._get_upstage_key())


def _make_scanned_copy(src: Path, dst: Path) -> bool:
    """src PDF 1페이지를 이미지로 렌더해 '텍스트레이어 없는 스캔본 모사' PDF를 만든다."""
    try:
        import fitz  # pymupdf
        doc = fitz.open(str(src))
        pix = doc[0].get_pixmap(dpi=200)
        out = fitz.open()
        page = out.new_page(width=pix.width, height=pix.height)
        page.insert_image(fitz.Rect(0, 0, pix.width, pix.height), pixmap=pix)
        out.save(str(dst))
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  [스캔본 모사 실패] {type(exc).__name__}: {exc}")
        return False


def check_b(pdfs: list[Path]) -> None:
    print("\n=== [B] 표비율 자동 배선 + 정형 라우팅 ===")
    print("  기대: table_ratio>0 가 자동 주입되고 정형 문서는 channel=structured")
    for f in pdfs:
        if not f.exists():
            print(f"  (없음) {f}")
            continue
        feats = R.estimate_layout_features(str(f))           # 자동 주입되는 값과 동일
        d = R.route_document(str(f))                          # 라이브 경로(preview_text 미주입)
        print(f"  {f.name[:34]:36} ch={d.channel.value:12} type={d.doc_type:18} "
              f"conf={d.confidence:<6} table_ratio={feats.get('table_area_ratio')} "
              f"kw={d.matched_keywords[:3]}")


def check_a(src: Path) -> None:
    print("\n=== [A] 스캔본 OCR 에스컬레이션 ===")
    print("  기대: 임베디드 텍스트 0 → _quick_preview가 OCR 본문 반환 → 정형 라우팅")
    if not src.exists():
        print(f"  소스 없음: {src}")
        return
    scanned = Path("outputs") / "_routing_check" / f"scanned_{src.name}"
    scanned.parent.mkdir(parents=True, exist_ok=True)
    if not _make_scanned_copy(src, scanned):
        return

    try:
        import fitz
        embedded = fitz.open(str(scanned))[0].get_text().strip()
    except Exception:  # noqa: BLE001
        embedded = ""
    print(f"  스캔본 임베디드 텍스트 길이: {len(embedded)}  (0이어야 정상 = 진짜 스캔본)")

    preview = R._quick_preview(str(scanned))
    used_ocr = bool(preview.strip()) and preview.strip() != scanned.stem
    print(f"  _quick_preview 길이={len(preview)} 앞60자={preview[:60]!r}")
    print(f"  → OCR 에스컬레이션 사용? {'예 ✅' if used_ocr else '아니오 ⚠️ (파일명 폴백 — Upstage 키/망 확인)'}")

    d = R.route_document(str(scanned))
    ok = d.channel is R.DocChannel.STRUCTURED
    print(f"  라우팅: ch={d.channel.value} type={d.doc_type} conf={d.confidence} 근거={d.rationale}")
    print(f"  → 스캔본도 정형 유지? {'예 ✅' if ok else '아니오 ⚠️'}")


def main() -> None:
    args = [Path(a) for a in sys.argv[1:]]
    pdfs = args or DEFAULT_STRUCTURED
    ready = _upstage_ready()
    print("Upstage API 키 감지:", "있음 ✅" if ready else "없음 ⚠️ (에스컬레이션은 파일명 폴백)")
    check_b(pdfs)
    check_a(pdfs[0])
    print("\n[참고] 샌드박스는 api.upstage.ai egress 차단이라 A의 Upstage 호출이 빈 결과일 수 있음.")
    print("       Mac(키 유효)에서는 _quick_preview가 OCR 본문을 반환하고 스캔본이 정형으로 떠야 정상.")


if __name__ == "__main__":
    main()
