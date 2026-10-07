"""§4 실제 실행용 검증 변형본(가상) — 구간이 정의되지 않은 회계연도 제목 아래 환경 법규 위반 0건.

첫 변형본(산업재해율 0‰)은 main의 대표값 규칙(0은 단위 `건`인 위반·사고 지표만 대표값)으로 원장에 오르지 않아
소비 경로를 통과하지 못했다. 그래서 0이 대표값이 될 수 있는 E-8-1(환경 법규 위반 건수)로 바꿨다.

사용: python make_variant_source_only.py <code_path> <out_dir>
원본 BM 세트는 건드리지 않는다. 쪽 머리에 '검증용 가상 변형본 — 원본 아님'을 찍는다(PR68 변형본과 같은 방식).
기대: 원문 0은 보존(SOURCE_ONLY/fiscal_period_undefined, 사업장 김해 제1공장)되고, 요청 기간의 확정 실적으로
비교·답변·본문 단정·내보내기되지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

CODE, OUT = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
sys.path.insert(0, str(CODE))
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from esgenie.supplychain.exporters._fonts import resolve_korean_font  # noqa: E402

LINES = [
    ("검증용 가상 변형본 — 원본 아님 (2026-10-05 수치·범위 최종 출력 검증)", 9),
    ("한울정밀공업(주) 안전 현황 보충 자료 — 시연·검증용 가상 자료이며 실제 제출용이 아닙니다.", 9),
    ("", 9),
    ("FY2026 김해 제1공장 환경 법규 준수 현황", 13),
    ("구분 | 값", 10),
    ("환경 법규 위반 건수 | 0건", 10),
    ("비고: 이 자료에는 FY2026 회계연도의 시작일과 종료일을 적지 않았습니다.", 9),
]

OUT.mkdir(parents=True, exist_ok=True)
path = OUT / "14_검증용변형본_FY2026환경법규준수_위반0건.pdf"
font = resolve_korean_font()
c = canvas.Canvas(str(path), pagesize=A4)
y = A4[1] - 60
for text, size in LINES:
    x = 50
    # 번들 한글 글꼴에 ‰ 글리프가 없어(추출 시 \x00) 그 글자만 표준 Helvetica로 그린다.
    for i, part in enumerate(text.split("‰")):
        if i:
            c.setFont("Helvetica", size)
            c.drawString(x, y, "‰")
            x += c.stringWidth("‰", "Helvetica", size)
        name = font.bold if size >= 13 else font.regular
        c.setFont(name, size)
        c.drawString(x, y, part)
        x += c.stringWidth(part, name, size)
    y -= size + 10
c.showPage()
c.save()
digest = hashlib.sha256(path.read_bytes()).hexdigest()
(OUT / "variant.json").write_text(json.dumps({"file": path.name, "sha256": digest, "lines": [t for t, _ in LINES],
                                              "expected": {"E-8-1": {"value": 0, "unit": "건", "status": "SOURCE_ONLY",
                                                                     "cause": "fiscal_period_undefined",
                                                                     "site": "김해 제1공장"}}},
                                             ensure_ascii=False, indent=2), encoding="utf-8")
print(path, digest)
