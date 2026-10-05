"""생성 본문의 수량 단정 대조(PR71 검토 R1·R2, 2026-10-05).

검토에서 확인한 실패: 교육 기록의 참석 46명(정규직 40 + 기간제 6)을 본문이 '정규직 15명과 기간제 6명이
출석', '총 21명이 참석'으로 썼고, 인용 근거의 날짜 `4월 15일`이 인원 `15명`의 근거로 통과했다. 숫자가
인용에 있다는 것과 그 수량이 근거로 확인됐다는 것은 다르다. 여기서는 문장의 **수량**(숫자 + 단위)을

  1. 원문 확인 수치(K-ESG 원장 값, K-ESG 코드가 없는 원문 집계 — 값·역할·날짜·출처가 붙은 사실)와
  2. 인용한 근거 청크의 수량(날짜·식별자 안의 숫자는 빼고)

에 값·단위·참여 역할(대상·참석·미참석)·날짜로 맞춘다. 역할 낱말은 참여 집계의 일반 어휘다 — 회사·문서·
정답 수치를 넣지 않는다. 생성 단계(layer2_rag)는 같은 사실을 생성 입력으로 넘기고, 보고서 조립(layer6_report)은
이 대조로 근거 없는 수량 문장을 확인 보류로 바꾼다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .numeric_tokens import TOKEN_END, TOKEN_START, VALID_NUMBER, parse_number

# 참여 역할. 부정형을 먼저 찾는다 — `미참석` 안의 `참석`을 참석으로 읽지 않는다(R6과 같은 원칙).
_ROLE_PATTERNS = (
    ("미참석", re.compile(r"미\s*참석|불참|결석|미\s*이수|미\s*수료|(?:참석|출석|이수|수료)(?:하지|되지)\s*(?:않|못)")),
    ("참석", re.compile(r"참석|출석|이수|수료|참여")),
    ("대상", re.compile(r"대상")),
)
# 수량 뒤 단위에 붙어도 되는 조사·어미. 그 밖의 말이 이어지면(`45001 인증`·`1인당`) 단위가 아니다.
_PARTICLES = frozenset({
    "", "이", "가", "은", "는", "을", "를", "의", "와", "과", "로", "으로", "에", "에서", "에게", "이며", "이고",
    "이다", "였다", "이었다", "입니다", "였습니다", "이었습니다", "이었으며", "였으며", "이었고", "였고", "씩", "만",
    "도", "까지", "부터", "이상", "이하", "중", "으로서", "으로써", "로서", "로써", "로는", "으로는", "이나", "나",
    "이라", "라", "이란", "란", "이므로", "이지만", "이자", "인", "임", "에는", "에도", "보다", "이라는", "라는",
    "대비", "이었음", "였음"})
# 단위 낱말은 숫자로 시작하지 않지만 안에 숫자를 품을 수 있다(`tCO2eq`).
_NUMBER_RE = re.compile(TOKEN_START + rf"(?P<num>{VALID_NUMBER})" + TOKEN_END
                        + r"\s?(?P<tok>(?:[^\s\d,.()\[\]{}|·:;~/\"'“”‘’<>][^\s,.()\[\]{}|·:;~/\"'“”‘’<>]*)?)")
# 절 경계. 숫자 사이 쉼표(`2,260`)는 천 단위 구분이다.
_CLAUSE_SEP_RE = re.compile(r"(?<!\d),|,(?!\d)|;|\n|\|")
# 라벨에서 대상을 가리키지 않는 낱말 — 문장이 이 사실을 '이름으로' 가리켰는지 볼 때 뺀다.
_GENERIC_TOKENS = frozenset({"인원", "수", "값", "수치", "명", "건", "현황", "기록", "이번", "해당", "전체", "총",
                             "합계", "계", "소계", "총계", "비율", "기준"})
_PSEUDO_CHUNK_PREFIXES = ("kesg_items_", "source_facts_")


@dataclass(frozen=True)
class Quantity:
    """문장·근거의 수량 하나. `unit`이 None이면 단위 없이 적힌 숫자다."""
    start: int
    end: int
    value: float
    unit: str | None
    raw: str
    decimals: int


@dataclass
class Fact:
    """대조에 쓰는 원문 확인 수치(원장 값 또는 코드 없는 원문 집계)."""
    label: str
    value: float
    unit: str | None
    role: str = ""
    period: Any = None          # ssot.ocr_router._DateSpan | None
    period_text: str = ""
    source_file: str = ""
    kind: str = "source"        # ledger | source
    code: str = ""
    tokens: tuple = field(default_factory=tuple)

    def describe(self) -> str:
        value = f"{self.value:g}" if isinstance(self.value, float) else str(self.value)
        when = f"({self.period_text})" if self.period_text else ""
        where = f" — {self.source_file}" if self.source_file else ""
        return f"{self.label} {value}{self.unit or ''}{when}{where}"


def _role_matches(text: str) -> list[tuple[int, int, str]]:
    taken: list[tuple[int, int, str]] = []
    for role, pattern in _ROLE_PATTERNS:
        for m in pattern.finditer(text):
            if not any(s < m.end() and m.start() < e for s, e, _r in taken):
                taken.append((m.start(), m.end(), role))
    return sorted(taken)


def count_role(text: str, anchor: int | None = None, end: int | None = None) -> str:
    """수량(`text[anchor:end]`)의 참여 역할(대상·참석·미참석). 없으면 "".

    수량 바로 앞의 역할 명사(`대상 50명`·`미참석 4명`)가 먼저다. 없으면 뒤에 오는 서술어(`46명이 참석`) —
    한국어 서술어는 수량 뒤에 온다. 둘 다 없으면 앞쪽의 가장 가까운 역할 낱말(`참석자: 정규직 40명`).
    """
    anchor = len(text) if anchor is None else anchor
    end = anchor if end is None else end
    found = _role_matches(text)
    before = [m for m in found if m[1] <= anchor]
    after = [m for m in found if m[0] >= end]
    if before and not text[before[-1][1]:anchor].strip(" :："):
        return before[-1][2]
    if after:
        return after[0][2]
    return before[-1][2] if before else ""


def label_role(label: str) -> str:
    """사실 라벨의 역할 — 표 칸 라벨(`행 · 열 머리`)은 뒤 토막(열 머리)의 역할이 앞선다."""
    for part in reversed(re.split(r"\s*·\s*", str(label or ""))):
        role = count_role(part)
        if role:
            return role
    return ""


def label_tokens(label: str) -> tuple:
    tokens = [t for t in re.split(r"[\s·\-/()（）,]+", str(label or "")) if len(t) >= 2]
    return tuple(t for t in tokens if t not in _GENERIC_TOKENS and not re.search(r"\d", t))


def _unit_of(tok: str) -> tuple[str | None, int]:
    from .rag_gates.units import normalize_unit
    for k in range(len(tok), 0, -1):
        unit = normalize_unit(tok[:k])
        if unit and tok[k:] in _PARTICLES:
            return unit, k
    return None, 0


def mask_non_quantities(text: str) -> str:
    """날짜·규격 번호·K-ESG 항목 코드를 같은 길이 공백으로 가린 문구(위치 보존).

    규격 식별자 모양이어도 뒤에 세는 단위가 붙으면(`ID 50개`) 수량이다 — 가리지 않는다.
    """
    from .ssot.ocr_router import _date_spans, _standard_refs
    masked = str(text or "")
    for _name, number, end in _standard_refs(masked):
        tail = re.match(r"\s?([^\s\d,.()\[\]{}|·:;~/]*)", masked[end:])
        if tail and _unit_of(tail.group(1))[0]:
            continue
        masked = masked[:number] + " " * (end - number) + masked[end:]
    masked = re.sub(r"(?<![A-Za-z0-9])[ESGP]-\d{1,2}-\d{1,2}(?![0-9])", lambda m: " " * len(m.group()), masked)
    cursor = 0
    for span in _date_spans(masked):
        at = masked.find(span.text, cursor)
        if at < 0:
            at = masked.find(span.text)
        if at < 0:
            continue
        masked = masked[:at] + " " * len(span.text) + masked[at + len(span.text):]
        cursor = at + len(span.text)
    return masked


def quantities(text: str) -> list[Quantity]:
    """날짜·식별자·규격 번호 밖의 숫자. 단위가 붙으면 `unit`(정규화)을 단다."""
    masked = mask_non_quantities(text)
    found = []
    for m in _NUMBER_RE.finditer(masked):
        value = parse_number(m.group("num"))
        if value is None:
            continue
        num = m.group("num")
        decimals = len(num.split(".")[1]) if "." in num else 0
        unit, length = _unit_of(m.group("tok") or "")
        end = m.end("num") if unit is None else m.start("tok") + length
        found.append(Quantity(m.start("num"), end, value, unit, text[m.start("num"):end].strip(), decimals))
    return found


def _clause(text: str, start: int, end: int) -> tuple[int, int]:
    before = [m.end() for m in _CLAUSE_SEP_RE.finditer(text, 0, start)]
    after = _CLAUSE_SEP_RE.search(text, end)
    return (before[-1] if before else 0), (after.start() if after else len(text))


def contexts(text: str, found: list[Quantity]) -> list[tuple[Quantity, str, int, int, int, int]]:
    """수량마다 (수량, 앞뒤 문맥, 문맥 안 시작·끝, 절 시작, 절 끝). 문맥은 같은 절의 앞뒤 수량 사이다."""
    out = []
    for i, q in enumerate(found):
        c_start, c_end = _clause(text, q.start, q.end)
        start = max(c_start, found[i - 1].end if i else 0)
        end = min(c_end, found[i + 1].start if i + 1 < len(found) else len(text))
        out.append((q, text[start:end], q.start - start, q.end - start, c_start, c_end))
    return out


def date_near(text: str, position: int, c_start: int, c_end: int):
    """수량과 같은 절의 날짜(앞쪽 우선). 없으면 None."""
    from .ssot.ocr_router import _date_spans
    clause = text[c_start:c_end]
    spans, cursor = [], 0
    for span in _date_spans(clause):
        at = clause.find(span.text, cursor)
        if at < 0:
            continue
        spans.append((c_start + at, span))
        cursor = at + len(span.text)
    before = [s for at, s in spans if at < position]
    if before:
        return before[-1]
    return spans[0][1] if spans else None


def period_of(period_text: str = "", start: str = "", end: str = "", label: str = ""):
    from .ssot.ocr_router import _date_spans
    for text in (period_text, f"{start}~{end}" if start and end else "", label):
        spans = _date_spans(text) if text else []
        if spans:
            return spans[0]
    return None


def date_relation(sentence_span, fact_span) -> str:
    """same | finer(사실이 문장 기간 안의 더 좁은 구간) | coarser(사실이 더 넓은 구간) | overlap | disjoint | unknown."""
    if sentence_span is None or fact_span is None:
        return "unknown"
    a0, a1, b0, b1 = sentence_span.start, sentence_span.end, fact_span.start, fact_span.end
    if not (sentence_span.year_known and fact_span.year_known):
        try:
            a0, a1, b0, b1 = (d.replace(year=2000) for d in (a0, a1, b0, b1))
        except ValueError:
            return "unknown"
    if (a0, a1) == (b0, b1):
        return "same"
    if b1 < a0 or a1 < b0:
        return "disjoint"
    if a0 <= b0 and b1 <= a1:
        return "finer"
    if b0 <= a0 and a1 <= b1:
        return "coarser"
    return "overlap"


def value_matches(q: Quantity, value: Any, unit: str | None, *, allow_bare: bool = False) -> bool:
    from .rag_gates.units import convert_to_common, units_compatible
    try:
        target = float(value)
    except (TypeError, ValueError):
        return False
    if q.unit and unit:
        if not units_compatible(q.unit, unit):
            return False
        converted = convert_to_common(target, unit, q.unit)
        if converted is None:
            return False
        target = converted
    elif q.unit and not unit and not allow_bare:
        return False
    tolerance = 0.5 * 10 ** -q.decimals + 1e-9 if q.decimals else 1e-9
    return abs(q.value - target) <= tolerance


def chunk_occurrences(text: str) -> list[tuple[Quantity, str]]:
    """근거 청크의 (수량, 역할). 표 행은 같은 표 머리글 행의 열 머리로 역할을 정한다."""
    out = []
    lines = str(text or "").split("\n")
    header: list[str] = []
    for line in lines:
        found = quantities(line)
        cells = [c.strip() for c in line.split("|")] if "|" in line else []
        if cells and not found:
            header = cells            # 숫자 없는 표 행 — 머리글
            continue
        if cells and header and len(header) == len(cells):
            offsets, at = [], 0
            for cell in line.split("|"):
                offsets.append((at, at + len(cell)))
                at += len(cell) + 1
            for q in found:
                k = next((i for i, (s, e) in enumerate(offsets) if s <= q.start < e), None)
                out.append((q, count_role(header[k]) if k is not None else ""))
            continue
        if not cells:
            header = []
        for q, window, anchor, anchor_end, _s, _e in contexts(line, found):
            out.append((q, count_role(window, anchor, anchor_end)))
    return out


@dataclass
class Support:
    supported: bool
    reason: str = ""                  # orphan_number | role_or_date_mismatch
    conflicting: list = field(default_factory=list)
    fact: Fact | None = None          # 근거가 된 원문 확인 수치(인용 청크로 확인됐으면 None)


def check_quantity(q: Quantity, window: str, anchor: int, anchor_end: int, when, facts: list[Fact],
                   cited: list[str] | None) -> Support:
    """문장 수량 `q`가 원문 확인 수치·인용 근거로 같은 값·역할·날짜로 확인되는가.

    - 문맥의 참여 역할(대상·참석·미참석)이 있으면 사실의 역할이 같아야 한다. 역할이 적히지 않은 사실은
      문장이 그 사실의 라벨 낱말로 직접 가리킬 때만(`중복 제외 50명`) 근거가 된다.
    - 문장 날짜와 사실 기간이 겹치지 않으면 근거가 아니다. 사실이 더 넓은 기간(월 합계)이면 라벨로 직접
      가리킬 때만 근거다 — `4월 22일 50명 참석`을 4월 중복 제외 합계 50명으로 통과시키지 않는다.
    - 인용 청크의 수량은 날짜·식별자 밖의 같은 값만 본다. 같은 값의 원문 확인 수치가 모두 역할·날짜가 달라
      어긋났으면, 청크 쪽도 같은 역할이 적혀 있어야 근거로 본다(역할 미상 칸으로 어긋남을 덮지 않는다).
    """
    role = count_role(window, anchor, anchor_end)
    conflicting = []
    for f in facts:
        if not value_matches(q, f.value, f.unit):
            continue
        named = any(t in window for t in f.tokens)
        problems = []
        if role and f.role and f.role != role:
            problems.append("role")
        elif role and not f.role and not named:
            problems.append("role_unstated")
        relation = date_relation(when, f.period)
        if relation == "disjoint" or (relation in ("coarser", "overlap") and not named):
            problems.append("date")
        if not problems:
            return Support(True, fact=f)
        conflicting.append((f, problems))
    for text in cited or []:
        for occ, occ_role in chunk_occurrences(text):
            if not value_matches(q, occ.value, occ.unit, allow_bare=True):
                continue
            if role and occ_role and occ_role != role:
                continue
            if conflicting and role and occ_role != role:
                continue
            return Support(True)
    reason = "role_or_date_mismatch" if conflicting else "orphan_number"
    return Support(False, reason, conflicting)


def related_facts(sentence: str, when, units: set[str], facts: list[Fact], limit: int = 6) -> list[Fact]:
    """확인 보류 문장에 함께 적을 원문 확인 값 — 같은 단위·겹치는 기간의 코드 없는 원문 집계.

    문장이 가리키는 낱말·역할의 사실을 먼저, 그 밖의 같은 단위 사실을 뒤에 둔다. 하루의 문장에는 그날을 품는
    더 넓은 기간(월 합계)의 값을 붙이지 않는다. 기간 순, 같은 기간은 대상 → 참석 → 미참석 순으로 적는다.
    """
    from datetime import date
    from .rag_gates.units import units_compatible
    roles = {role for _s, _e, role in _role_matches(sentence)}
    rank = {"대상": 0, "참석": 1, "미참석": 2}
    picked, seen = [], set()
    for f in facts:
        if f.kind != "source" or not f.unit or not any(units_compatible(f.unit, u) for u in units):
            continue
        relation = date_relation(when, f.period)
        if relation == "disjoint" or (relation == "coarser" and when is not None and when.grain == "day"):
            continue                  # 하루의 문장에 월 합계를 붙이지 않는다
        key = (f.value, f.role, f.period_text, f.unit)
        if key in seen:
            continue
        seen.add(key)
        direct = any(t in sentence for t in f.tokens) or f.role in roles
        picked.append((not direct, f.period.end if f.period is not None else date.max, rank.get(f.role, 3), f))
    picked.sort(key=lambda item: item[:3])
    return [f for *_k, f in picked[:limit]]


# ── 생성 입력: K-ESG 코드가 없는 원문 집계 ───────────────────────────────────

def _value_written(value: Any, quote: str) -> bool:
    try:
        target = float(value)
    except (TypeError, ValueError):
        return False
    for q in quantities(str(quote or "")):
        if abs(q.value - target) <= 1e-9:
            return True
    return False


def source_facts(graph: Any, source_files: set[str] | None = None, limit: int = 40) -> list[dict[str, Any]]:
    """K-ESG 코드가 없는 원문 수치 가운데 **인용 원문에 값이 그대로 적힌** 것(OCR 노드).

    회사 답변·설문·모델이 원문 밖에서 셈한 값은 넣지 않는다(값이 인용에 없으면 뺀다). 라벨이 `합계`·`인원`처럼
    대상이 없는 말뿐인 행(개인별 시간표의 `합계`)은 뺀다. `source_files`를 주면 그 문서의 사실만 싣는다
    (생성 문맥이 이미 근거로 삼은 문서). 같은 문서·라벨·값·기간은 하나로 둔다.
    """
    from .knowledge.kesg_items import by_code
    if graph is None:
        return []
    rows, seen = [], set()
    for node in getattr(graph, "nodes", {}).values():
        if getattr(node, "origin", "") not in ("ocr_structured", "ocr_unstructured"):
            continue
        if by_code(str(node.metric)) is not None or node.value is None:
            continue
        if source_files is not None and node.source_file not in source_files:
            continue
        label = str(node.metric).strip()
        if not label_tokens(label) or not _value_written(node.value, getattr(node, "quote", "")):
            continue
        b = getattr(node, "boundary", None)
        period_text = str(getattr(b, "period_text", "") or "")
        start, end = str(getattr(b, "period_start", "") or ""), str(getattr(b, "period_end", "") or "")
        if not period_text and start and end:
            period_text = start if start == end else f"{start}~{end}"
        key = (node.source_file, label, float(node.value), str(node.unit), period_text)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"label": label, "value": node.value, "unit": str(node.unit or ""),
                     "role": label_role(label), "value_role": getattr(node, "value_role", ""),
                     "period_text": period_text, "period_start": start, "period_end": end,
                     "site": str(getattr(b, "site", "") or ""), "source_file": node.source_file or "",
                     "page": node.page, "node_id": node.id,
                     "quote": str(getattr(node, "quote", "") or "").splitlines()[0][:120] if getattr(node, "quote", "") else ""})
    rows.sort(key=lambda r: (r["source_file"], r["period_text"], r["label"]))
    return rows[:limit]


def fact_line(row: dict[str, Any]) -> str:
    parts = [f"- {row['label']}: {row['value']:g}{row['unit']}" if isinstance(row["value"], float)
             else f"- {row['label']}: {row['value']}{row['unit']}"]
    if row.get("role"):
        parts.append(f"[역할: {row['role']}]")
    parts.append(f"[기간: {row.get('period_text') or '원문 기간 미기록'}]")
    parts.append(f"[사업장: {row.get('site') or '원문 미기록'}]")
    page = row.get("page")
    parts.append(f"[출처: {row.get('source_file') or '미상'}" + (f" {page + 1}쪽]" if isinstance(page, int) else "]"))
    if row.get("quote"):
        parts.append(f"[원문: {row['quote']}]")
    return " ".join(parts)


def facts_from_rows(rows: list[dict[str, Any]]) -> list[Fact]:
    from .rag_gates.units import normalize_unit
    out = []
    for row in rows or []:
        try:
            value = float(row["value"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append(Fact(label=row.get("label", ""), value=value,
                        unit=normalize_unit(str(row.get("unit") or "")) if row.get("unit") else None,
                        role=row.get("role") or label_role(row.get("label", "")),
                        period=period_of(row.get("period_text", ""), row.get("period_start", ""),
                                         row.get("period_end", ""), row.get("label", "")),
                        period_text=row.get("period_text", ""), source_file=row.get("source_file", ""),
                        kind="source", tokens=label_tokens(row.get("label", ""))))
    return out


def is_pseudo_chunk(chunk_id: str) -> bool:
    return str(chunk_id or "").startswith(_PSEUDO_CHUNK_PREFIXES)
