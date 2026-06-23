"""PDF용 한글 폰트 해석기.

reportlab은 CFF(OTF) 임베드가 불가하므로 **TrueType(glyf)** 한글 폰트가 필요하다.
해석 순서(먼저 잡히는 것 사용):

  1. 환경변수 ``ESGENIE_PDF_FONT`` (정규 경로, ``경로:인덱스`` 로 .ttc 서브폰트 지정 가능).
     bold는 ``ESGENIE_PDF_FONT_BOLD`` (없으면 regular로 대체).
  2. 레포 번들 ``esgenie/assets/fonts/NotoSansKR-{Regular,Bold}.ttf`` (서브셋·glyf 변환본).
  3. OS 시스템 한글 TTF/TTC (macOS AppleSDGothicNeo, Linux Noto TTF 등).
  4. 모두 실패하면 ``None`` → 호출측이 Helvetica로 폴백(한글은 □로 깨지나 생성은 됨).

번들(2)이 항상 존재하므로 실사용·테스트에서 1~2가 거의 항상 성공한다.
시스템 폰트(3)는 번들을 일부러 지웠거나 다른 글꼴을 원하는 경우의 안전망.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# 레포 번들 폰트 위치 (esgenie/assets/fonts)
_BUNDLE_DIR = Path(__file__).resolve().parents[2] / "assets" / "fonts"
_BUNDLE_REGULAR = _BUNDLE_DIR / "NotoSansKR-Regular.ttf"
_BUNDLE_BOLD = _BUNDLE_DIR / "NotoSansKR-Bold.ttf"

# (regular, bold) 후보. bold가 None이면 regular를 bold로도 씀.
# .ttc/.otc 는 "경로:인덱스" 형식으로 서브폰트를 지정.
_SYSTEM_CANDIDATES: tuple[tuple[str, Optional[str]], ...] = (
    # macOS
    ("/System/Library/Fonts/AppleSDGothicNeo.ttc", None),
    ("/System/Library/Fonts/Supplemental/AppleGothic.ttf", None),
    ("/Library/Fonts/AppleGothic.ttf", None),
    # Linux — 배포판이 글리프(TTF) 한글을 깔았을 때
    ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
     "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
    ("/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
     "/usr/share/fonts/truetype/nanum/NanumBarunGothicBold.ttf"),
    # Windows
    ("C:/Windows/Fonts/malgun.ttf", "C:/Windows/Fonts/malgunbd.ttf"),
)

# reportlab에 등록할 폰트 이름(전역 유일)
REGULAR_NAME = "ESGenieKR"
BOLD_NAME = "ESGenieKR-Bold"


@dataclass(frozen=True)
class FontResult:
    regular: str          # reportlab 등록 폰트명(또는 "Helvetica")
    bold: str             # 볼드 폰트명(또는 "Helvetica-Bold")
    embedded: bool        # 한글 임베드 성공 여부(False면 폴백 — 한글 깨짐)
    source: str           # 출처 설명(디버그/감사용)


def _split_index(spec: str) -> tuple[str, int]:
    """``경로:2`` → (경로, 2). 인덱스 없으면 0. Windows 드라이브(``C:``)는 보존."""
    # 마지막 ':' 뒤가 정수일 때만 인덱스로 해석
    head, sep, tail = spec.rpartition(":")
    if sep and tail.isdigit() and head not in ("", "C", "D", "c", "d"):
        return head, int(tail)
    return spec, 0


def _try_register(name: str, spec: Optional[str]) -> bool:
    """폰트 1개 등록 시도. 성공 True. CFF/없음/형식오류면 False."""
    if not spec:
        return False
    path, idx = _split_index(spec)
    if not Path(path).exists():
        return False
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        pdfmetrics.registerFont(TTFont(name, path, subfontIndex=idx))
        return True
    except Exception:
        # CFF .ttc(Noto CJK), 손상, 비TTF 등 — 다음 후보로
        return False


def _register_pair(regular_spec: str, bold_spec: Optional[str], source: str) -> Optional[FontResult]:
    if not _try_register(REGULAR_NAME, regular_spec):
        return None
    # 볼드는 실패해도 regular로 대체(품질만 떨어질 뿐 생성은 됨)
    if _try_register(BOLD_NAME, bold_spec):
        bold = BOLD_NAME
    else:
        bold = REGULAR_NAME
    return FontResult(regular=REGULAR_NAME, bold=bold, embedded=True, source=source)


def resolve_korean_font() -> FontResult:
    """등록 가능한 한글 폰트를 찾아 reportlab에 등록하고 결과를 반환한다.

    이미 등록돼 있으면(같은 프로세스 재호출) 재등록 없이 그대로 반환한다.
    """
    from reportlab.pdfbase import pdfmetrics

    # 같은 프로세스에서 이미 등록됨 → 재사용
    try:
        pdfmetrics.getFont(REGULAR_NAME)
        try:
            pdfmetrics.getFont(BOLD_NAME)
            bold = BOLD_NAME
        except KeyError:
            bold = REGULAR_NAME
        return FontResult(REGULAR_NAME, bold, embedded=True, source="이미 등록됨")
    except KeyError:
        pass

    # 1) 환경변수
    env_reg = os.environ.get("ESGENIE_PDF_FONT")
    if env_reg:
        res = _register_pair(env_reg, os.environ.get("ESGENIE_PDF_FONT_BOLD"),
                             f"환경변수 ESGENIE_PDF_FONT={env_reg}")
        if res:
            return res

    # 2) 레포 번들(거의 항상 성공)
    if _BUNDLE_REGULAR.exists():
        res = _register_pair(
            str(_BUNDLE_REGULAR),
            str(_BUNDLE_BOLD) if _BUNDLE_BOLD.exists() else None,
            "번들 NotoSansKR",
        )
        if res:
            return res

    # 3) 시스템 폰트
    for reg, bold in _SYSTEM_CANDIDATES:
        res = _register_pair(reg, bold, f"시스템 폰트 {reg}")
        if res:
            return res

    # 4) 폴백 — 한글은 깨지나 PDF 생성 자체는 진행
    return FontResult(
        regular="Helvetica",
        bold="Helvetica-Bold",
        embedded=False,
        source="폴백(Helvetica) — 한글 폰트 미발견, 한글이 □로 표시됨",
    )
