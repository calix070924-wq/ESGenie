# -*- coding: utf-8 -*-
"""verify_upstage_live.py — Upstage Document Parse 라이브 호출 Mac 검증.

OCR 엔진을 Azure → Upstage DP로 교체한 뒤, 실제 UPSTAGE_API_KEY로 호출이 닫히는지
직접 까보는 스크립트다. 샌드박스는 api.upstage.ai egress 차단이라 Mac(.env 키 유효)에서
실행해야 한다.

검증 항목:
  ① 정형 증빙 3종이 engine=upstage_dp 로 추출되고 upstage_error=None
  ② raw 텍스트에 한국어/숫자가 제대로 인식됐는지(전력량·MJ·% 등)
  ③ 표(table) 요소가 HTML로 복원돼 ExtractedTable 셀이 채워지는지
  ④ metric bbox(좌표)가 [0,1]로 정규화돼 붙는지

사용:
    python -m scripts.verify_upstage_live                 # 데모 세트 자동
    python -m scripts.verify_upstage_live a.pdf b.pdf     # 임의 PDF 지정
"""
from __future__ import annotations

import sys
from pathlib import Path

# .env 로드를 위해 config를 가장 먼저 import (config가 load_dotenv를 수행).
from esgenie.config import SETTINGS  # noqa: F401
from esgenie.ssot import ocr_router as R

DEMO_DIR = Path("시연증빙세트_한울정밀공업")
DEFAULT_DOCS = [
    DEMO_DIR / "01_전기요금청구서_2026-05.pdf",
    DEMO_DIR / "02_도시가스요금고지서_2026-05.pdf",
    DEMO_DIR / "03_사업장폐기물_위탁처리명세_2026-04.pdf",
]


def _check(path: Path) -> bool:
    if not path.exists():
        print(f"  (없음) {path}")
        return False
    dec = R.route_document(str(path))
    ext = R.extract_document(str(path), dec)
    meta = ext.router_meta or {}
    engine = meta.get("engine")
    err = meta.get("upstage_error")
    raw = (ext.raw_text or "")
    n_tables = len(getattr(ext, "tables", []) or [])
    n_cells = sum(len(t.cells) for t in (getattr(ext, "tables", []) or []))
    n_bbox = sum(1 for m in (ext.metrics or []) if m.bbox)

    print(f"\n■ {path.name}")
    print(f"  라우팅 : channel={dec.channel.value}  doc_type={dec.doc_type}  conf={dec.confidence}")
    print(f"  엔진   : engine={engine}   upstage_error={err}")
    print(f"  raw    : {len(raw)}자  앞80자={raw[:80]!r}")
    print(f"  표     : tables={n_tables}  cells={n_cells}")
    print(f"  metric : {len(ext.metrics or [])}개  bbox부착={n_bbox}")
    for m in (ext.metrics or [])[:6]:
        print(f"     code={m.kesg_code_guess!s:7} value={m.value!s:>12} unit={m.unit!s:6} "
              f"bbox={'Y' if m.bbox else '-'}")

    ok = engine == "upstage_dp" and not err
    print(f"  → {'✅ upstage_dp 라이브 추출 성공' if ok else '⚠️ upstage_dp 아님(키/망/폴백 확인)'}")
    return ok


def main() -> None:
    args = [Path(a) for a in sys.argv[1:]]
    docs = args or DEFAULT_DOCS
    ready = bool(R._get_upstage_key())
    print("Upstage API 키 감지:", "있음 ✅" if ready else "없음 ⚠️ (UPSTAGE_API_KEY 미설정 → pymupdf/mock 폴백)")
    if not ready:
        print("  → .env 에 UPSTAGE_API_KEY 를 넣고 다시 실행하세요.")

    results = [_check(d) for d in docs]
    passed = sum(1 for r in results if r)
    print(f"\n합계: {passed}/{len(results)} 문서가 engine=upstage_dp 로 추출됨")
    print("[참고] 샌드박스는 api.upstage.ai egress 차단 → Mac(키 유효)에서만 라이브로 닫힘.")


if __name__ == "__main__":
    main()
