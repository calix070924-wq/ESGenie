"""응답서 표시 계층 — UI·Excel·PDF·체크리스트가 같은 문장을 쓰게 하는 단일 출처.

2026-09-20 §5-1. 같은 ResponseSheet인데 화면과 제출본의 헤더·배지·값·범위·검토
사유가 서로 달랐다. 표기 규칙을 여기 한 곳에 두고 각 출력은 줄바꿈 문자만 바꿔 쓴다.

여기서는 새로 계산하지 않는다 — ResponseSheet/Answer가 이미 가진 값을 문자열로
바꾸기만 한다. 반올림도 하지 않는다(0.513216 TJ가 0.5로 뭉개지던 결함).
"""
from __future__ import annotations

import re
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
        f"  |  검토필요 {sheet.flagged_count}건(별도 지표, 자동응답과 중복 가능)"
        f"  |  문항 {len(sheet.answers)}개 (분모 {sheet.denominator}개, 해당없음 제외)"
    )


def page_text(page: Any) -> str:
    """'p.3' — 페이지를 모르면 빈 문자열. 모르는 페이지를 p.1로 적지 않는다.

    저장된 page는 0-기준이라 표시할 때 +1 한다(실제 1쪽 → p.1, 2쪽으로 밀지 않는다).
    """
    if page is None:
        return ""
    try:
        return f"p.{int(page) + 1}"
    except (TypeError, ValueError):
        return ""


def page_label(link: Any) -> str:
    """EvidenceLink/인용 객체의 페이지 표시."""
    return page_text(getattr(link, "page", None))


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
    # 상세 사유는 근거/비고에 한 번만 표시한다. 좁은 범위 열에서 같은 장문을
    # 반복하면 한 답변이 페이지 높이를 넘거나 Excel에서 잘린다.
    note = getattr(answer, "comparison_label", "")
    if note:
        bits.append(note)
    return " · ".join(bits)


def reference_line(answer: Any) -> str:
    """값 산정에 쓰이지 않은 보완 대상 근거 — 산정 근거와 섞지 않고 따로 적는다."""
    refs = getattr(answer, "reference_links", None) or []
    if not refs:
        return ""
    return "보완 대상(값 산정 미사용): " + " / ".join(locator(e) for e in refs)


# ── AI 초안 인용 표기(§5-3) ──────────────────────────────────────────────────
# 본문에 내부 노드 ID가 그대로 노출됐다([LOCAL_TXT_0043]). 사용자용 본문·출처 목록은
# 번호와 문서명·실제 페이지로 적고, 내부 draft_text와 감사 JSON의 node_id는 건드리지
# 않는다 — 추적성은 그쪽에 남는다.
_BRACKET_TOKEN = re.compile(r"\[([^\[\]\n]{1,80})\]")
# 내부 노드 ID 모양: "{corp}_TXT_0043" (evidence_graph._next_text_id).
_NODE_ID_SHAPE = re.compile(r"[A-Za-z0-9]+_[A-Za-z]+_\d+")
UNRESOLVED_MARK = "[출처 미확인]"


def citation_numbering(answer: Any) -> dict[str, int]:
    """node_id → 본문 표기 번호(1부터, 출처 목록 순서와 동일)."""
    out: dict[str, int] = {}
    available = {str(c.get("node_id") or "").strip() for c in getattr(answer, "draft_citations", None) or []}
    for nid in _BRACKET_TOKEN.findall(getattr(answer, "draft_text", "") or ""):
        nid = nid.strip()
        if nid in available and nid not in out:
            out[nid] = len(out) + 1
    return out


def source_lines(answer: Any) -> list[str]:
    """'[1] 인권정책서.pdf p.3' 목록 — 번호는 본문 인용과 같은 번호다.

    파일명을 모르면 '문서명 미확인'이라고 적는다(없는 출처를 만들지 않는다).
    페이지를 모르면 p.1을 붙이지 않고 생략한다.
    """
    numbering = citation_numbering(answer)
    lines: list[str] = []
    seen: set[int] = set()
    for cit in getattr(answer, "draft_citations", None) or []:
        n = numbering.get(str(cit.get("node_id") or "").strip())
        if n is None or n in seen:
            continue
        seen.add(n)
        name = (cit.get("source_file") or "").strip() or "문서명 미확인"
        page = page_text(cit.get("page"))
        lines.append(f"[{n}] {name}{' ' + page if page else ' (위치 미확인)'}")
    return sorted(lines, key=lambda line: int(line.split(']')[0][1:]))


def draft_body(answer: Any) -> tuple[str, list[str]]:
    """(사용자용 초안 본문, 검토 사유 목록).

    본문의 내부 ID 인용을 출처 번호로 바꾼다. 출처 목록에 없는 ID는 꾸며내지 않고
    '[출처 미확인]'으로 두고 검토 사유를 남긴다. 괄호를 전부 벗기지는 않는다 —
    인용 표시가 사라지면 어느 문장이 증빙에 걸렸는지 알 수 없게 된다. 내부 ID 모양이
    아닌 대괄호(예: [E-4-1], [2026-05])는 원문 그대로 둔다.
    """
    text = getattr(answer, "draft_text", "") or ""
    if not text:
        return "", []
    numbering = citation_numbering(answer)
    unresolved: set[str] = set()

    def _sub(m: re.Match[str]) -> str:
        token = m.group(1).strip()
        n = numbering.get(token)
        if n is not None:
            return f"[{n}]"
        if _NODE_ID_SHAPE.fullmatch(token):
            unresolved.add(token)
            return UNRESOLVED_MARK
        return m.group(0)

    body = _BRACKET_TOKEN.sub(_sub, text)
    notes: list[str] = []
    if unresolved:
        notes.append(f"출처 미해소: 본문 인용 {len(unresolved)}건이 출처 목록과 "
                     "연결되지 않음 — 승인 전 원문 확인 필요")
    if any(c.get("node_id") in numbering and c.get("page") is None for c in getattr(answer, "draft_citations", []) or []):
        notes.append("인용 위치 미확인 — 원문 페이지 확인 필요")
    return body, notes


def draft_lines(answer: Any) -> list[str]:
    """초안 답변 칸의 줄 목록 — Excel은 '\\n', PDF는 '<br/>'로 이으면 같은 내용."""
    body, notes = draft_body(answer)
    if not body:
        return []
    lines = ["[AI 초안 — 승인 전]", body]
    sources = source_lines(answer)
    if sources:
        lines.append("출처: " + " / ".join(sources))
    lines.extend(notes)
    return lines


def note_lines(answer: Any, *, fig_map: dict[int, str] | None = None,
               bbox_mark: str = "") -> list[str]:
    """근거/비고 칸의 줄 목록. Excel은 '\\n', PDF는 '<br/>'로 이으면 같은 내용이 된다."""
    lines: list[str] = []
    if getattr(answer, "rationale", ""):
        lines.append(answer.rationale)
    review = getattr(answer, "review_note", "")
    if review:
        lines.append(review)
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
