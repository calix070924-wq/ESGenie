"""응답서 표시 계층 — UI·Excel·PDF·체크리스트가 같은 문장을 쓰게 하는 단일 출처.

2026-09-20 §5-1. 같은 ResponseSheet인데 화면과 제출본의 헤더·배지·값·범위·검토
사유가 서로 달랐다. 표기 규칙을 여기 한 곳에 두고 각 출력은 줄바꿈 문자만 바꿔 쓴다.

여기서는 새로 계산하지 않는다 — ResponseSheet/Answer가 이미 가진 값을 문자열로
바꾸기만 한다. 반올림도 하지 않는다(0.513216 TJ가 0.5로 뭉개지던 결함).
"""
from __future__ import annotations

from typing import Any

# 헤더에 반드시 함께 나가야 하는 4분할. 합이 100%가 되는 상호배타 집계다.
# 검토필요(flagged)는 자동응답 안에 포함되는 중첩 지표라 따로 적는다.
FOUR_WAY: tuple[tuple[str, str], ...] = (
    ("자동응답", "auto_pct"),
    ("AI초안(승인 대기)", "draft_pct"),
    ("작성필요", "hitl_pct"),
    ("증빙대기", "pending_pct"),
)


def coverage_parts(sheet: Any) -> list[tuple[str, float]]:
    return [(label, float(getattr(sheet, attr, 0.0) or 0.0)) for label, attr in FOUR_WAY]


def coverage_text(sheet: Any) -> str:
    """'자동응답 68.1% · AI초안(승인 대기) 10.6% · 작성필요 0.0% · 증빙대기 21.3%'."""
    return " · ".join(f"{label} {pct:.1f}%" for label, pct in coverage_parts(sheet))


def summary_line(sheet: Any, *, with_corp: bool = True) -> str:
    """표지·A2·화면 캡션이 공유하는 요약 한 줄."""
    head = f"기업: {sheet.corp_name or '—'}  |  " if with_corp else ""
    return (
        f"{head}{coverage_text(sheet)}"
        f"  |  검토필요 {sheet.flagged_count}건(자동응답 안에 중복 집계)"
        f"  |  문항 {len(sheet.answers)}개 (분모 {sheet.denominator}개, 해당없음 제외)"
    )


def page_label(link: Any) -> str:
    """'p.3' — 페이지를 모르면 빈 문자열. 모르는 페이지를 p.1로 적지 않는다.

    EvidenceLink.page는 0-기준이라 표시할 때 +1 한다(실제 1쪽 → p.1).
    """
    page = getattr(link, "page", None)
    if page is None:
        return ""
    try:
        return f"p.{int(page) + 1}"
    except (TypeError, ValueError):
        return ""


def locator(link: Any, *, bbox_mark: str = "") -> str:
    """'01_전기요금청구서_2026-05.pdf p.1 📍' — 파일명 + 알려진 페이지 + bbox 표시."""
    name = getattr(link, "file_name", "") or getattr(link, "node_id", "") or "—"
    bits = [name, page_label(link)]
    if bbox_mark and getattr(link, "bbox", None):
        bits.append(bbox_mark)
    return " ".join(b for b in bits if b)


def scope_line(answer: Any) -> str:
    """측정 범위 + 비교 판정 사유. 둘 다 없으면 빈 문자열."""
    bits: list[str] = []
    label = getattr(answer, "boundary_label", "")
    if label:
        bits.append(f"측정 범위: {label}")
    note = getattr(answer, "review_note", "")
    if note:
        bits.append(note)
    return " · ".join(bits)


def reference_line(answer: Any) -> str:
    """값 산정에 쓰이지 않은 보완 대상 근거 — 산정 근거와 섞지 않고 따로 적는다."""
    refs = getattr(answer, "reference_links", None) or []
    if not refs:
        return ""
    return "보완 대상(값 산정 미사용): " + " / ".join(locator(e) for e in refs)


def note_lines(answer: Any, *, fig_map: dict[int, str] | None = None,
               bbox_mark: str = "") -> list[str]:
    """근거/비고 칸의 줄 목록. Excel은 '\\n', PDF는 '<br/>'로 이으면 같은 내용이 된다."""
    lines: list[str] = []
    if getattr(answer, "rationale", ""):
        lines.append(answer.rationale)
    scope = scope_line(answer)
    if scope:
        lines.append(scope)
    lines.extend(getattr(answer, "flags", None) or [])
    parts: list[str] = []
    for e in getattr(answer, "evidence_links", None) or []:
        tag = f" → [{fig_map[id(e)]}]" if fig_map and id(e) in fig_map else ""
        parts.append(f"{locator(e, bbox_mark=bbox_mark)}{tag}")
    if parts:
        lines.append("근거: " + " / ".join(parts))
    ref = reference_line(answer)
    if ref:
        lines.append(ref)
    return lines
