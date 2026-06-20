"""협력사 자가주장(self-claim) 로딩 — 증빙과 대조할 '주장 채널'.

공급망 실사에서 핵심은 **협력사가 스스로 보고한 값 ↔ 증빙에서 검증된 값**의 대조다.
상장사 파이프라인의 D1(보고서 주장 vs 증빙)에 대응하는, SME용 주장 채널을 제공한다.

두 경로를 병합한다.
  1) 업로드한 OEM SAQ(협력사가 기입해 제출) 텍스트에서 자가응답 수치 파싱
  2) 설문/입력 필드로 직접 주입한 수치

여기서는 검출/판정을 하지 않는다 — 주장값을 K-ESG 코드에 실어 mapping 으로 넘길 뿐.
대조(불일치 → flagged)는 mapping._reconcile_claim 이 담당한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SupplierClaim:
    """협력사가 자가보고한 단일 수치."""
    code: str                 # K-ESG 코드 (예: "E-6-2")
    value: float              # 주장값 (비율이면 % 단위 숫자)
    unit: str = "%"
    raw: str = ""             # 원문 (예: "재활용률 92% 달성")
    source: str = "manual"    # "saq:파일명" | "manual"


# ── SAQ 자가응답 → K-ESG 코드 파싱 규칙 ──────────────────────────────────────
# (정규식, 코드, 단위, value 변환). value 변환은 매치 그룹(float)을 받아 최종값 반환.
_CLAIM_PATTERNS: list[tuple[re.Pattern[str], str, str, Any]] = [
    # "재활용률 92% 달성" / "재활용(순환이용)률 90% 이상"
    (re.compile(r"재활용[^0-9%]{0,8}?(\d{1,3}(?:\.\d+)?)\s*%"), "E-6-2", "%", lambda v: v),
    # "매립·소각 8% 수준" → 재활용 비율 = 100 - 8
    (re.compile(r"(?:매립[·\s]*소각|소각[·\s]*매립)[^0-9%]{0,6}?(\d{1,3}(?:\.\d+)?)\s*%"),
     "E-6-2", "%", lambda v: round(100.0 - v, 1)),
]

_SAQ_FILENAME_HINTS = (
    "saq",
    "자가진단",
    "설문",
    "questionnaire",
    "self-assessment",
    "self_assessment",
)
_SAQ_TEXT_HINTS = (
    "자가진단",
    "questionnaire",
    "self-assessment",
    "drive sustainability",
    "supplier sustainability",
)


def _extract_text(pdf_path: str) -> str:
    """PDF 1차 텍스트 추출 (pymupdf 우선, 없으면 pdftotext, 그것도 없으면 빈 문자열)."""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(pdf_path)
        return "\n".join(pg.get_text() for pg in doc)
    except Exception:
        pass
    try:
        import subprocess
        out = subprocess.run(["pdftotext", "-layout", pdf_path, "-"],
                             capture_output=True, text=True, timeout=30)
        if out.returncode == 0:
            return out.stdout
    except Exception:
        pass
    return ""


def is_saq_upload(file_path: str, *, file_name: str = "") -> bool:
    """업로드 파일이 OEM/협력사 SAQ(자가진단 설문)인지 가볍게 판별한다.

    1) 파일명 힌트 우선
    2) PDF면 임베디드 텍스트를 읽어 SAQ 시그니처 재확인
    """
    if Path(file_path).suffix.lower() != ".pdf":
        return False

    haystack = f"{Path(file_name or file_path).name} {Path(file_path).stem}".lower()
    if any(hint in haystack for hint in _SAQ_FILENAME_HINTS):
        return True

    text = _extract_text(file_path).lower()
    if not text:
        return False
    return any(hint in text for hint in _SAQ_TEXT_HINTS)


def parse_saq_claims(pdf_paths: list[str]) -> dict[str, SupplierClaim]:
    """업로드된 SAQ PDF들에서 자가응답 수치를 파싱한다.

    같은 코드가 여러 파일에서 잡히면 먼저 발견한 값을 유지한다(보수적).
    """
    claims: dict[str, SupplierClaim] = {}
    for p in pdf_paths:
        text = _extract_text(p)
        if not text:
            continue
        fname = Path(p).name
        for pat, code, unit, conv in _CLAIM_PATTERNS:
            if code in claims:
                continue
            m = pat.search(text)
            if not m:
                continue
            try:
                raw_val = float(m.group(1))
            except (TypeError, ValueError):
                continue
            claims[code] = SupplierClaim(
                code=code, value=float(conv(raw_val)), unit=unit,
                raw=m.group(0).strip(), source=f"saq:{fname}",
            )
    return claims


def merge_claims(*sources: dict[str, SupplierClaim] | None) -> dict[str, SupplierClaim]:
    """여러 주장 소스를 병합. 뒤쪽 소스가 우선(수동입력으로 SAQ 값 덮어쓰기 가능)."""
    merged: dict[str, SupplierClaim] = {}
    for src in sources:
        if src:
            merged.update(src)
    return merged


def manual_claims(values: dict[str, float], unit: str = "%") -> dict[str, SupplierClaim]:
    """수동 입력 {code: value} → SupplierClaim 맵."""
    return {
        code: SupplierClaim(code=code, value=float(v), unit=unit,
                            raw=f"{v}{unit} (수동입력)", source="manual")
        for code, v in values.items()
    }
