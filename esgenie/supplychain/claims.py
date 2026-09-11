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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SupplierClaim:
    """협력사가 자가보고한 단일 수치."""
    code: str                 # K-ESG 코드 (예: "E-6-2")
    value: float | None       # 주장값 (비율이면 % 단위 숫자)
    unit: str = "%"
    raw: str = ""             # 원문 (예: "재활용률 92% 달성")
    source: str = "manual"    # "saq:파일명" | "manual"
    period: int | None = None
    page: int | None = None
    position: int | None = None
    status: str = "reported"
    diagnostics: list[str] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)


class ClaimSet(dict):
    """선택된 주장과, 실적으로 채택하지 않은 원문의 진단 기록."""
    def __init__(self, *args, diagnostics=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.diagnostics = list(diagnostics or [])


# ── SAQ 자가응답 → K-ESG 코드 파싱 규칙 ──────────────────────────────────────
# (정규식, 코드, 단위, value 변환). value 변환은 매치 그룹(float)을 받아 최종값 반환.
_SIGNED_RATE = r"([+\-−]?\d+(?:\.\d+)?)\s*%"
_CLAIM_PATTERNS = [
    (re.compile(r"재활용[^0-9%+\-−\n]{0,24}?" + _SIGNED_RATE), lambda v: v),
    (re.compile(r"(?:매립[·ㆍ\s]*소각|소각[·ㆍ\s]*매립)[^0-9%+\-−\n]{0,12}?" + _SIGNED_RATE),
     lambda v: 100.0 - v),
]
_TARGET = re.compile(r"목표|계획|전망|예정|지향|추진|target|plan|forecast", re.I)
_BOUNDARY = re.compile(r"[\n\f;/|]+|\.(?=\s|$)|(?=20\d{2}\s*년)")
_YEAR = re.compile(r"(20\d{2})\s*년")

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
        with fitz.open(pdf_path) as doc:
            return "\f".join(pg.get_text() for pg in doc)
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


def parse_saq_claims(pdf_paths: list[str]) -> ClaimSet:
    """재활용 비율의 실적만 선택한다. 목표·오류·상충하는 실적은 진단을 남긴다."""
    claims = ClaimSet()
    candidates = []
    for path in pdf_paths:
        text = _extract_text(path)
        if not text:
            claims.diagnostics.append({"code": "E-6-2", "source": f"saq:{Path(path).name}",
                                       "reason": "text_unavailable"})
            continue
        offset = 0
        for boundary in list(_BOUNDARY.finditer(text)) + [None]:
            end = boundary.start() if boundary else len(text)
            segment = text[offset:end]
            matches = sorted([(m, conv) for pat, conv in _CLAIM_PATTERNS
                              for m in pat.finditer(segment)], key=lambda pair: pair[0].start())
            for i, (match, convert) in enumerate(matches):
                # 숫자 뒤 목표 표기도 이 행/문장 안에서 확인. 다음 실적 행에는 전파하지 않는다.
                stop = matches[i + 1][0].start() if i + 1 < len(matches) else len(segment)
                context = segment[(0 if i == 0 else match.start()):stop].strip()
                year = _YEAR.search(context)
                raw_value = float(match.group(1).replace("−", "-"))
                record = {"code": "E-6-2", "raw": context, "source": f"saq:{Path(path).name}",
                          "period": int(year.group(1)) if year else None,
                          "page": text[:offset + match.start()].count("\f"),
                          "position": offset + match.start(), "input_value": raw_value}
                if _TARGET.search(context):
                    claims.diagnostics.append(dict(record, reason="target_not_actual"))
                elif not 0 <= raw_value <= 100:
                    claims.diagnostics.append(dict(record, reason="invalid_rate"))
                else:
                    candidates.append(dict(record, value=convert(raw_value)))
            offset = boundary.end() if boundary else len(text)
    if candidates:
        identities = {(c["value"], c["period"]) for c in candidates}
        # 여러 연도 또는 서로 다른 실적은 보고기간 선택 없이 첫 값을 확정하지 않는다.
        ambiguous = len(identities) > 1
        first = candidates[0]
        claims["E-6-2"] = SupplierClaim(
            "E-6-2", None if ambiguous else first["value"], "%",
            raw=" / ".join(dict.fromkeys(c["raw"] for c in candidates)),
            source=" / ".join(dict.fromkeys(c["source"] for c in candidates)),
            period=None if ambiguous else first["period"], page=first["page"], position=first["position"],
            status="ambiguous" if ambiguous else "reported",
            diagnostics=["여러 실적 주장값/연도가 상충하여 확정 불가"] if ambiguous else [],
            candidates=candidates)
    return claims


def merge_claims(*sources: dict[str, SupplierClaim] | None) -> ClaimSet:
    """뒤쪽(수동입력) 우선 정책을 유지하고 파서 진단도 보존한다."""
    merged = ClaimSet()
    for src in sources:
        if src is not None:
            merged.update(src)
            merged.diagnostics.extend(getattr(src, "diagnostics", []))
    return merged


def manual_claims(values: dict[str, float], unit: str = "%") -> dict[str, SupplierClaim]:
    """수동 입력 {code: value} → SupplierClaim 맵."""
    return {
        code: SupplierClaim(code=code, value=float(v), unit=unit,
                            raw=f"{v}{unit} (수동입력)", source="manual")
        for code, v in values.items()
    }
