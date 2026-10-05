"""생성 본문의 수량 단정 대조(PR71 검토 R1·R2, 2026-10-05).

검토에서 확인한 실패: 교육 기록의 참석 46명(정규직 40 + 기간제 6)을 본문이 '정규직 15명과 기간제 6명이
출석', '총 21명이 참석'으로 썼고, 인용 근거의 날짜 `4월 15일`이 인원 `15명`의 근거로 통과했다. 숫자가
인용에 있다는 것과 그 수량이 근거로 확인됐다는 것은 다르다. 여기서는 문장의 **수량**(숫자 + 단위)을

  1. 원문 확인 수치(K-ESG 원장 값, K-ESG 코드가 없는 원문 집계 — 값·역할·날짜·출처가 붙은 사실)와
  2. 인용한 근거 청크의 수량(날짜·식별자 안의 숫자는 빼고)

에 값·단위·참여 역할(대상·참석·미참석)·날짜로 맞춘다. 역할 낱말은 참여 집계의 일반 어휘다 — 회사·문서·
정답 수치를 넣지 않는다. 생성 단계(layer2_rag)는 같은 사실을 생성 입력으로 넘기고, 보고서 조립(layer6_report)은
이 대조로 근거 없는 수량 문장을 확인 보류로 바꾼다.

PR71 재검토(2026-10-05) A·D: 원문 청크의 수량은 값·역할만 보아, 구조화 사실에서 찾은 날짜 불일치(`6월 9일 27명
참석` ↔ 6월 3일 참석 27명)를 같은 숫자로 덮었고, 고용형태별 인원을 뒤바꾼 문장(`정규직 6명과 기간제 40명`)을 숫자
집합이 같다는 이유로 통과시켰다. 이제 사실·청크 모두 **값·단위·역할·대상 집단(고용형태)·날짜·사업장이 연결된 사실**로
대조한다(`check_quantity`). 같은 값의 다른 날짜 후보가 있어도 실제로 맞는 근거가 있으면 그것을 채택하고, 채택·기각한
근거를 감사 기록에 남긴다.
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
# 대상 집단(고용형태). 동의어는 같은 집단으로 읽는다(`계약직` = 기간제). 긴 말을 먼저 찾는다 — `비정규직` 안의 `정규직`을
# 정규직으로 읽지 않는다. 성별·직급 등 다른 집단 축은 이 사전에 없다(그 축의 내역은 집단으로 대조하지 않는다).
_GROUP_PATTERNS = (
    ("비정규직", re.compile(r"비\s*정규직")),
    ("무기계약직", re.compile(r"무기\s*계약직?")),
    ("직접고용", re.compile(r"직접\s*고용")),
    ("간접고용", re.compile(r"간접\s*고용")),
    ("정규직", re.compile(r"정규직")),
    ("기간제", re.compile(r"기간제|계약직")),
    ("파견", re.compile(r"파견")),
    ("단시간", re.compile(r"단시간|시간제")),
    ("일용직", re.compile(r"일용직")),
)
# 집단 낱말과 바로 뒤 수량 사이에 와도 되는 말(`기간제 근로자는 6명`·`정규직: 40명`). 다른 낱말(`포함`·`각각`)이 끼면
# 그 수량의 집단이 아니다 — `정규직·기간제·파견 포함 50명`은 합계다.
_GROUP_NOUN_GAP_RE = re.compile(r"\s*(?:직|근로자|직원|인원|근무자|사원|인력)?\s*(?:수)?\s*(?:은|는|이|가|의|:|：)?\s*")
# 수량 뒤 서술어로 집단을 밝힌 경우(`40명은 정규직이다`). 괄호 내역(`50명(정규직 40명 …)`)은 이 수량의 집단이 아니다.
_GROUP_AFTER_RE = re.compile(r"\s*(?:은|는|이|가)\s*")
_GROUP_COPULA_RE = re.compile(r"\s*(?:직|근로자|직원)?\s*(?:이다|이며|이고|이었|였|입니다|임)")
# 날짜·수량으로 읽지 않을 파일명(`09_…_2026-04-22.pdf`)과 근거 꼬리표(`(출처: …, 1쪽)`).
_FILE_NAME_RE = re.compile(r"[^\s/|()\[\]]+\.(?:pdf|xlsx|xls|csv|docx?|hwp|png|jpe?g)\b(?:\s*,?\s*\d+\s*쪽)?", re.I)
_SOURCE_TAG_RE = re.compile(r"[(（]\s*출처\s*[:：][^()（）]*[)）]")
# 문장 경계(소수점·자릿수 마침표는 경계가 아니다).
_SENTENCE_END_RE = re.compile(r"(?<!\d)[.!?。](?!\d)|\n")


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
    kind: str = "source"        # ledger | source | summary_input(요약 생성 입력의 분석 결과값)
    code: str = ""
    tokens: tuple = field(default_factory=tuple)
    group: str = ""             # 대상 집단(고용형태, `label_group`). 없으면 집단을 가리지 않은 값(합계 등)
    sites: frozenset = frozenset()   # 사업장 식별값(`ocr_router._site_keys`). 없으면 원문 미기록

    def describe(self) -> str:
        value = f"{self.value:g}" if isinstance(self.value, float) else str(self.value)
        when = f"({self.period_text})" if self.period_text else ""
        where = f" — {self.source_file}" if self.source_file else ""
        return f"{self.label} {value}{self.unit or ''}{when}{where}"


@dataclass
class Claim:
    """생성 문장·표 칸의 수량 하나와 그 수량에 연결된 대상(역할·집단)·날짜·사업장."""
    q: Quantity
    window: str                       # 이웃 수량 사이의 같은 절 문맥(표는 행 라벨·열 머리) — 라벨 낱말 대조용
    role: str = ""
    group: str = ""
    groups_mentioned: frozenset = frozenset()   # 문맥에 나온 집단 낱말(바로 꾸미지 않아도)
    when: Any = None
    sites: frozenset = frozenset()


@dataclass(frozen=True)
class Occurrence:
    """근거 청크 안의 수량 하나와 원문이 그 수량에 붙인 역할·집단·날짜·사업장(PR71 재검토 A·D)."""
    q: Quantity
    role: str = ""
    group: str = ""
    when: Any = None
    sites: frozenset = frozenset()
    context: str = ""                 # 그 수량의 원문 문맥(행·서술)
    chunk_id: str = ""
    source_file: str = ""

    def describe(self) -> str:
        when = f"({self.when.text.strip()})" if self.when is not None else ""
        where = f" — {self.source_file or self.chunk_id}"
        return f"원문 '{self.context.strip()[:60]}'의 {self.q.raw}{when}{where}"


def _role_matches(text: str) -> list[tuple[int, int, str]]:
    taken: list[tuple[int, int, str]] = []
    for role, pattern in _ROLE_PATTERNS:
        for m in pattern.finditer(text):
            if not any(s < m.end() and m.start() < e for s, e, _r in taken):
                taken.append((m.start(), m.end(), role))
    return sorted(taken)


NEXT_QUANTITY = "\x00"
# 역할 낱말이 서술어로 쓰였는가(`참석했다`·`참여하여`·`미참석`으로 끝남) — 뒤 수량의 명사구(`대상 인원은 50명`)와 가른다.
_ROLE_PREDICATE_RE = re.compile(r"(?:하|했|한|함|해|되|됐|하였|으로|으며|였|이었|이다|않|못)|\s*$|\s*[.,;/|)）]")
# 역할 명사가 바로 뒤 수량을 꾸미는가(`미참석 인원은 4명`·`참석자: 정규직 40명`·`참석률 92%`). 동사 어미가 끼면 아니다.
_ROLE_NOUN_GAP_RE = re.compile(r"(?:인원수|인원|자수|자|수|률|율)?\s*(?:은|는|이|가|의|:|：)?\s*"
                               r"(?:(?!하|했|한|함|해|되|됐|였|이었)[가-힣A-Za-z]{1,6}\s*)?")


def count_role(text: str, anchor: int | None = None, end: int | None = None) -> str:
    """수량(`text[anchor:end]`)의 참여 역할(대상·참석·미참석). 없으면 "".

    한국어 서술어는 수량 뒤에 온다 — 뒤의 역할 낱말이 서술어로 쓰였으면(`4명이 추가 교육에 참여하여`) 그것이
    역할이다. 아니면 수량 바로 앞에서 그 수량을 꾸미는 역할 명사(`미참석 인원은 4명`·`대상 50명`). 둘 다 없으면
    역할을 정하지 않는다 — 앞 수량의 서술어(`46명이 참석하고 4명이`)나 뒤 수량의 명사(`… 4명으로 총 대상 인원은
    50명`)를 끌어오지 않는다(PR71 후속 실측: 맞는 문장 `미참석 인원은 4명으로 총 대상 인원은 50명`을 보류했다).
    """
    anchor = len(text) if anchor is None else anchor
    end = anchor if end is None else end
    found = _role_matches(text)
    for start, stop, role in found:
        if start < end:
            continue
        if text[start:stop].endswith(("않", "못")) or _ROLE_PREDICATE_RE.match(text, stop):
            return role
    before = [m for m in found if m[1] <= anchor]
    if before and _ROLE_NOUN_GAP_RE.fullmatch(text[before[-1][1]:anchor]):
        return before[-1][2]
    return ""


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


def _group_matches(text: str) -> list[tuple[int, int, str]]:
    taken: list[tuple[int, int, str]] = []
    for group, pattern in _GROUP_PATTERNS:
        for m in pattern.finditer(str(text or "")):
            if not any(s < m.end() and m.start() < e for s, e, _g in taken):
                taken.append((m.start(), m.end(), group))
    return sorted(taken)


def groups_in(text: str) -> frozenset:
    return frozenset(g for _s, _e, g in _group_matches(text))


def count_group(text: str, anchor: int | None = None, end: int | None = None) -> str:
    """수량(`text[anchor:end]`)의 대상 집단(고용형태). 없으면 "".

    수량을 바로 꾸미는 집단 낱말(`정규직 40명`·`기간제 근로자는 6명`) 또는 수량 뒤 서술어(`40명은 정규직이다`)만
    읽는다. 집단 낱말이 여럿 나열되고 다른 말이 끼면(`정규직·기간제·파견 포함 50명`) 합계로 보고 정하지 않는다.
    """
    anchor = len(text) if anchor is None else anchor
    end = anchor if end is None else end
    found = _group_matches(text)
    before = [m for m in found if m[1] <= anchor]
    if before and _GROUP_NOUN_GAP_RE.fullmatch(text[before[-1][1]:anchor]):
        return before[-1][2]
    after = next((m for m in found if m[0] >= end), None)
    if after and _GROUP_AFTER_RE.fullmatch(text[end:after[0]]) and _GROUP_COPULA_RE.match(text, after[1]):
        return after[2]
    return ""


def label_group(label: str) -> str:
    """사실 라벨의 집단 — 표 칸 라벨(`행 · 열 머리`)은 뒤 토막(열 머리)이 앞선다. 한 토막에 집단이 여럿이면 정하지 않는다."""
    for part in reversed(re.split(r"\s*·\s*|\s+-\s+", str(label or ""))):
        found = groups_in(part)
        if len(found) == 1:
            return next(iter(found))
        if found:
            return ""
    return ""


def _sites(text: str) -> frozenset:
    from .ssot.ocr_router import _site_keys
    return frozenset(_site_keys(str(text or "")))


def sites_compatible(a: frozenset, b: frozenset) -> bool | None:
    """두 사업장 표기가 같은 곳을 가리킬 수 있는가. 한쪽이 비면 None(판정하지 않음).

    지역이 빠진 표기(`제1공장`)는 같은 번호·종류의 지역 표기(`김해 제1공장`)와 맞을 수 있다. 지역이 다르면(`부산 제1공장`
    ↔ `김해 제1공장`) 다른 곳이다.
    """
    if not a or not b:
        return None

    def fits(x: str, y: str) -> bool:
        if x == y:
            return True
        short, long_ = sorted((x, y), key=len)
        return short[:1].isdigit() and long_.endswith(short)
    return any(fits(x, y) for x in a for y in b)


def date_positions(text: str) -> list[tuple[int, int, Any]]:
    """`ocr_router._date_spans`의 날짜 표기와 그 원문 위치 [(시작, 끝, 범위)] — 위치 순.

    구간(`4월 13일부터 19일까지`)의 `text`는 정규화된 문구(`4월 13일 ~ 19일`)라 원문에서 그대로 찾을 수 없다. 이전
    판은 `find(span.text)`가 실패해 그 문장의 날짜를 '없음'으로 두었다(날짜 대조 누락). 앞 날짜를 찾은 뒤 이음말 뒤의
    뒷 날짜까지를 구간 위치로 본다.
    """
    from .ssot.ocr_router import _RANGE_JOIN_RE, _date_spans
    text = str(text or "")
    found: list[tuple[int, int, Any]] = []

    def free(at: int, stop: int) -> bool:
        return not any(s < stop and at < e for s, e, _sp in found)

    for span in _date_spans(text):
        first, _sep, second = span.text.partition(" ~ ")
        at = text.find(first)
        while at >= 0:
            stop = at + len(first)
            if second:
                join = _RANGE_JOIN_RE.match(text, stop)
                stop = join.end() + len(second) if join and text.startswith(second, join.end()) else -1
            if stop > at and free(at, stop):
                found.append((at, stop, span))
                break
            at = text.find(first, at + 1)
    return sorted(found, key=lambda item: item[0])


def _bounds(text: str, pattern: re.Pattern[str], start: int, end: int) -> tuple[int, int]:
    before = [m.end() for m in pattern.finditer(text, 0, start)]
    after = pattern.search(text, end)
    return (before[-1] if before else 0), (after.start() if after else len(text))


def nearest(items: list[tuple[int, int, Any]], text: str, start: int, end: int):
    """수량(`text[start:end]`)에 붙는 표기 하나: 같은 문장의 가장 가까운 앞 표기, 없으면 같은 절의 뒤 표기. 없으면 None.

    쉼표 뒤 절(`2026년 6월 9일 교육에는, 27명이 참석`)도 같은 문장의 앞 날짜를 잇는다 — 쉼표 하나로 날짜 대조가 꺼지지 않게 한다.
    """
    s_start, _s_end = _bounds(text, _SENTENCE_END_RE, start, end)
    _c_start, c_end = _clause(text, start, end)
    before = [item for item in items if s_start <= item[0] and item[1] <= start]
    if before:
        return before[-1][2]
    after = [item for item in items if end <= item[0] < c_end]
    return after[0][2] if after else None


def site_positions(text: str) -> list[tuple[int, int, frozenset]]:
    from .ssot.ocr_router import _SITE_MENTION_RE
    out = []
    for m in _SITE_MENTION_RE.finditer(str(text or "")):
        keys = _sites(m.group(0))
        if keys:
            out.append((m.start(), m.end(), keys))
    return out


def _unit_of(tok: str) -> tuple[str | None, int]:
    from .rag_gates.units import normalize_unit
    for k in range(len(tok), 0, -1):
        unit = normalize_unit(tok[:k])
        if unit and tok[k:] in _PARTICLES:
            return unit, k
    return None, 0


def mask_non_quantities(text: str) -> str:
    """날짜·규격 번호·K-ESG 항목 코드를 같은 길이 공백으로 가린 문구(위치 보존).

    규격 식별자 모양이어도 뒤에 세는 단위가 붙으면(`ID 50개`) 수량이다 — 가리지 않는다. 파일명·근거 꼬리표
    (`09_…_2026-04-22.pdf`·`(출처: …, 1쪽)`) 안의 숫자도 수량·날짜가 아니다.
    """
    from .ssot.ocr_router import _standard_refs

    def blank(m: re.Match) -> str:
        return " " * len(m.group())
    masked = _FILE_NAME_RE.sub(blank, _SOURCE_TAG_RE.sub(blank, str(text or "")))
    # 인원번호 구간(`HN-G01~40: 정규직`)의 뒤 번호는 인원 수가 아니다 — 앞 식별자와 함께 가린다.
    masked = re.sub(r"(?<![A-Za-z0-9])[A-Z]{1,5}-[A-Z]{0,3}\d+\s*[~∼〜]\s*\d+(?!\s*(?:명|건|개|%|\d))", blank, masked)
    for _name, number, end in _standard_refs(masked):
        tail = re.match(r"\s?([^\s\d,.()\[\]{}|·:;~/]*)", masked[end:])
        if tail and _unit_of(tail.group(1))[0]:
            continue
        masked = masked[:number] + " " * (end - number) + masked[end:]
    masked = re.sub(r"(?<![A-Za-z0-9])[ESGP]-\d{1,2}-\d{1,2}(?![0-9])", blank, masked)
    for at, stop, _span in date_positions(masked):
        masked = masked[:at] + " " * (stop - at) + masked[stop:]
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
    """수량마다 (수량, 앞뒤 문맥, 문맥 안 시작·끝, 절 시작, 절 끝). 문맥은 같은 절의 앞뒤 수량 사이다.

    문맥이 다음 수량 앞에서 끊기면 끝에 `NEXT_QUANTITY` 표지를 붙인다 — 끝의 역할 낱말(`중 참석 ` + 46명)은 다음
    수량의 명사이지 이 수량의 서술어가 아니다(`count_role`).
    """
    out = []
    for i, q in enumerate(found):
        c_start, c_end = _clause(text, q.start, q.end)
        start = max(c_start, found[i - 1].end if i else 0)
        cut = i + 1 < len(found) and found[i + 1].start < c_end
        end = found[i + 1].start if cut else c_end
        window = text[start:end] + (NEXT_QUANTITY if cut else "")
        out.append((q, window, q.start - start, q.end - start, c_start, c_end))
    return out


def _scope_text(text: str) -> str:
    """날짜·사업장을 읽을 문구 — 파일명·근거 꼬리표를 같은 길이 공백으로 가린다(위치 보존)."""
    def blank(m: re.Match) -> str:
        return " " * len(m.group())
    return _FILE_NAME_RE.sub(blank, _SOURCE_TAG_RE.sub(blank, str(text or "")))


def date_near(text: str, start: int, end: int | None = None):
    """수량(`text[start:end]`)의 날짜: 같은 문장의 가장 가까운 앞 날짜, 없으면 같은 절의 뒤 날짜. 없으면 None.

    이전 판은 같은 절만 보고 구간 날짜(`4월 13일부터 19일까지`)를 찾지 못했다(`date_positions`).
    """
    scoped = _scope_text(text)
    return nearest(date_positions(scoped), scoped, start, start if end is None else end)


def sites_near(text: str, start: int, end: int | None = None) -> frozenset:
    scoped = _scope_text(text)
    found = nearest(site_positions(scoped), scoped, start, start if end is None else end)
    return found or frozenset()


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


def sentence_claims(text: str, found: list[Quantity] | None = None) -> list[Claim]:
    """문장(제목·요약 문장 포함)의 단위 있는 수량마다 역할·집단·날짜·사업장을 읽은 `Claim`."""
    found = quantities(text) if found is None else found
    out = []
    for q, window, anchor, anchor_end, _c_start, _c_end in contexts(text, found):
        if not q.unit:
            continue
        out.append(Claim(q, window, role=count_role(window, anchor, anchor_end),
                         group=count_group(window, anchor, anchor_end), groups_mentioned=groups_in(window),
                         when=date_near(text, q.start, q.end), sites=sites_near(text, q.start, q.end)))
    return out


def _cell_group(text: str) -> str:
    found = groups_in(text)
    return next(iter(found)) if len(found) == 1 else ""


def _header_unit(head: str) -> str | None:
    """열 머리에 적힌 단위(`실적(명)`·`인원 [명]`·`단위: 명`). 없으면 None."""
    for m in re.finditer(r"[(\[（]\s*([^()\[\]（）]{1,12}?)\s*[)\]）]|단위\s*[:：]?\s*([^\s|,]+)", str(head or "")):
        token = (m.group(1) or m.group(2) or "").strip()
        unit, length = _unit_of(token)
        if unit and length == len(token):
            return unit
    return None


def _row_items(cells: list[str], header: list[str] | None, heading_when=None,
               heading_sites: frozenset = frozenset()) -> list[tuple[int, Quantity, str, str, Any, frozenset, str]]:
    """표 한 행의 수량마다 (칸 번호, 수량, 역할, 집단, 날짜, 사업장, 행 라벨·열 머리 문맥).

    역할·집단은 그 칸의 열 머리 → 행 라벨(값이 아닌 칸) 순, 날짜·사업장은 같은 행 → 열 머리 → 위 머리 줄 순으로 읽는다.
    칸에 단위가 없으면 열 머리의 단위(`실적(명)`)를 쓴다. 머리글의 칸 수가 행과 다르면 열 머리를 쓰지 않는다.
    """
    line = " | ".join(cells)
    offsets, at = [], 0
    for cell in cells:
        offsets.append((at, at + len(cell)))
        at += len(cell) + 3
    scoped = _scope_text(line)
    dates, sites = date_positions(scoped), site_positions(scoped)
    found = quantities(line)
    value_cells = {k for k, (s, e) in enumerate(offsets) if any(s <= q.start < e for q in found)}
    labels = [c for k, c in enumerate(cells) if k not in value_cells]
    aligned = bool(header) and len(header) == len(cells)
    items = []
    for q in found:
        k = next((i for i, (s, e) in enumerate(offsets) if s <= q.start < e), None)
        head = header[k] if aligned and k is not None else ""
        if q.unit is None and _header_unit(head):
            q = Quantity(q.start, q.end, q.value, _header_unit(head), q.raw, q.decimals)
        head_dates = date_positions(_scope_text(head))
        when = nearest(dates, scoped, q.start, q.end) or (head_dates[0][2] if head_dates else None) or heading_when
        where = nearest(sites, scoped, q.start, q.end) or _sites(head) or heading_sites
        role = count_role(head) or next((r for r in map(count_role, labels) if r), "")
        group = _cell_group(head) or next((g for g in map(_cell_group, labels) if g), "")
        items.append((k, q, role, group, when, frozenset(where), " ".join([*labels, head]).strip()))
    return items


def row_claims(cells: list[str], header: list[str] | None) -> list[tuple[int, Claim]]:
    """생성 표 한 행의 (칸 번호, `Claim`) — 행 라벨·열 머리·칸을 함께 읽는다(PR71 재검토 B·D)."""
    return [(k, Claim(q, window, role=role, group=group, groups_mentioned=groups_in(window), when=when, sites=sites))
            for k, q, role, group, when, sites, window in _row_items(cells, header) if q.unit]


def _heading_line(line: str) -> bool:
    """아래 줄에 날짜·사업장을 물려줄 수 있는 머리 줄 — 표가 아니고, 단위 있는 수량이 없고, 문장으로 끝나지 않는 줄
    (`개인별 주간 근로시간 기록: … / 김해 제1공장 / 2026-04-13`). 서술 문장의 날짜(`4월 27일 추가 교육을 예정했습니다.`)는
    그 문장의 것이다 — 다른 문장의 수량에 물려주지 않는다."""
    stripped = _SOURCE_TAG_RE.sub("", line).strip()
    return bool(stripped) and "|" not in stripped and not any(q.unit for q in quantities(stripped)) \
        and not re.search(r"[.!?。]\s*$", stripped)


def chunk_occurrences(text: str, chunk_id: str = "", source_file: str = "") -> list[Occurrence]:
    """근거 청크의 수량과 원문이 그 수량에 붙인 역할·집단·날짜·사업장.

    - 표 행: 같은 표 머리글 행의 열 머리로 역할·집단을 정하고, 없으면 행 라벨(값이 아닌 칸)에서 읽는다. 날짜·사업장은
      그 행 → 열 머리 → 위 머리 줄 순으로 읽는다.
    - 서술: 같은 문장의 앞 날짜(없으면 같은 절의 뒤 날짜)·사업장. 줄 전체에 날짜가 없을 때만 위 머리 줄(`_heading_line`)의
      날짜를 잇는다. 한 청크에 여러 날짜·집단의 수량이 있어도 수량마다 자기 관계를 갖는다(PR71 재검토 A).
    """
    out: list[Occurrence] = []
    header: list[str] = []
    heading_when, heading_sites = None, frozenset()
    for line in str(text or "").split("\n"):
        scoped = _scope_text(line)
        found = quantities(line)
        dates, sites = date_positions(scoped), site_positions(scoped)
        cells = [c.strip() for c in line.split("|")] if "|" in line else []
        if cells and not found:
            header = cells            # 숫자 없는 표 행 — 머리글
            continue
        if cells:
            for _k, q, role, group, when, where, _window in _row_items(cells, header, heading_when, heading_sites):
                out.append(Occurrence(q, role=role, group=group, when=when, sites=where, context=line,
                                      chunk_id=chunk_id, source_file=source_file))
            continue
        header = []
        for q, window, anchor, anchor_end, _s, _e in contexts(line, found):
            when = nearest(dates, scoped, q.start, q.end) or (None if dates else heading_when)
            where = nearest(sites, scoped, q.start, q.end) or (frozenset() if sites else heading_sites)
            out.append(Occurrence(q, role=count_role(window, anchor, anchor_end),
                                  group=count_group(window, anchor, anchor_end), when=when, sites=frozenset(where),
                                  context=window.replace(NEXT_QUANTITY, ""), chunk_id=chunk_id,
                                  source_file=source_file))
        if _heading_line(line):
            heading_when = dates[-1][2] if dates else heading_when
            heading_sites = sites[-1][2] if sites else heading_sites
    return out


@dataclass
class Support:
    supported: bool
    reason: str = ""                  # orphan_number | relation_mismatch
    # 값이 같지만 관계가 맞지 않은 후보 [(설명, 문제 목록)]. 확인된 경우에도 남긴다(채택 근거와 함께 감사 기록).
    conflicting: list = field(default_factory=list)
    fact: Fact | None = None          # 근거가 된 원문 확인 수치
    occurrence: Occurrence | None = None   # 근거가 된 청크 수량
    outside_citation: bool = False    # 인용한 청크가 아닌 다른 생성 문맥 청크로 확인했다(채택 출처를 감사 기록에 남긴다)
    problems: list = field(default_factory=list)   # 확인하지 못한 경우 어긋난 관계(role·group·date·site …)

    def adopted(self) -> str:
        if self.fact is not None:
            return self.fact.describe()
        return self.occurrence.describe() if self.occurrence is not None else ""


# 문제 코드 → 관계(역할·집단·날짜·사업장). 같은 관계에서 어긋난 후보가 있으면 그 관계가 '확인 안 됨'인 근거는 쓰지 않는다.
_RELATION_OF = {"role": "role", "role_unstated": "role", "group": "group", "group_unstated": "group",
                "group_missing": "group", "date": "date", "date_unstated": "date", "site": "site",
                "site_unstated": "site"}


def _relation_problems(claim: Claim, *, role: str, group: str, when, sites: frozenset, named: bool,
                       soft_role: bool) -> tuple[list[str], list[str]]:
    """(어긋남, 확인 안 됨). 어긋남이 하나라도 있으면 근거가 아니다. '확인 안 됨'만 있는 근거는 같은 값의 어긋난
    후보가 없을 때만 근거로 본다(`check_quantity`)."""
    hard, weak = [], []
    if claim.role and role and role != claim.role:
        hard.append("role")
    elif claim.role and not role and not named:
        (weak if soft_role else hard).append("role_unstated")
    # 집단: 문장이 집단을 밝혔으면 근거도 같은 집단이어야 한다(합계를 집단 내역으로 쓰지 않는다). 문장이 집단 없이 쓴 값이
    # 근거에서는 한 집단의 값이면, 그 집단을 문맥에서 말하지 않은 한 근거가 아니다(집단 내역을 합계처럼 쓰지 않는다).
    if claim.group and group != claim.group:
        hard.append("group" if group else "group_unstated")
    elif not claim.group and group and group not in claim.groups_mentioned:
        hard.append("group_missing")
    relation = date_relation(claim.when, when)
    if relation == "disjoint" or (relation in ("coarser", "overlap") and not named):
        hard.append("date")
    elif relation == "unknown" and claim.when is not None and when is None:
        weak.append("date_unstated")
    fit = sites_compatible(claim.sites, sites)
    if fit is False:
        hard.append("site")
    elif fit is None and claim.sites and not sites:
        weak.append("site_unstated")
    return hard, weak


def check_quantity(claim: Claim, facts: list[Fact], scope: list[Occurrence],
                   others: list[Occurrence] = ()) -> Support:
    """생성 수량 `claim`이 같은 값·단위·역할·집단·날짜·사업장의 근거로 확인되는가.

    근거는 원문 확인 수치(`facts`), 인용 청크의 수량(`scope` — 인용이 없는 문장은 생성 문맥 전체), 그 밖의 생성 문맥
    청크(`others` — 인용이 있는데 인용 청크로 확인되지 않을 때만)이다. 세 경로가 같은 대조 기준(`_relation_problems`)을 쓴다.

    - 문맥의 역할·집단·날짜·사업장이 근거와 어긋나면 그 근거는 쓰지 않는다. 같은 숫자가 원문에 있다는 것만으로
      어긋남을 덮지 않는다(PR71 재검토 A: 원문 인용 `[c1]`·인용 없음 경로가 날짜 불일치를 덮었다).
    - 어긋난 후보와 별도로 **모든 관계가 맞는** 근거가 있으면 그것으로 확인한다(다른 날짜의 동률 후보가 정상 근거를 막지
      않는다). 채택한 근거와 어긋난 후보를 함께 돌려준다.
    - 날짜·역할·사업장이 원문에 적히지 않아 '확인 안 됨'뿐인 근거는, 같은 값의 후보가 그 관계에서 어긋나지 않을 때만
      근거다. 인용 밖 청크는 모든 관계가 맞을 때만 근거다(다른 출처의 같은 숫자가 인용을 대신하지 않는다).
    """
    q = claim.q
    candidates: list[tuple[list[str], list[str], Fact | None, Occurrence | None, bool]] = []
    for f in facts:
        if not value_matches(q, f.value, f.unit):
            continue
        named = any(t in claim.window for t in f.tokens)
        hard, weak = _relation_problems(claim, role=f.role, group=f.group, when=f.period, sites=f.sites,
                                        named=named, soft_role=False)
        candidates.append((hard, weak, f, None, False))
    for outside, pool in ((False, scope), (True, others)):
        for occ in pool:
            if not value_matches(q, occ.q.value, occ.q.unit, allow_bare=True):
                continue
            hard, weak = _relation_problems(claim, role=occ.role, group=occ.group, when=occ.when, sites=occ.sites,
                                            named=False, soft_role=True)
            candidates.append((hard, weak, None, occ, outside))
    conflicts = [(f.describe() if f is not None else o.describe(), hard) for hard, _w, f, o, _x in candidates if hard]
    # 고르는 순서: 인용 청크 → 원문 확인 수치 → 인용 밖 청크. 인용한 근거가 확인해 주면 그것을 채택 근거로 남긴다.
    strong = sorted((c for c in candidates if not c[0] and not c[1]), key=lambda c: (c[4], c[3] is None))
    pick = next(iter(strong), None)
    if pick is None:
        # '확인 안 됨'뿐인 근거는, 같은 값의 후보가 **그 관계에서** 어긋날 때 쓰지 않는다(날짜 없는 청크로 날짜 불일치를
        # 덮지 않는다). 다른 관계에서만 어긋난 후보(날짜가 다른 같은 값)는 막지 않는다 — 날짜·역할이 맞고 사업장만 적히지
        # 않은 근거를 다른 날짜의 같은 숫자 때문에 버리지 않는다(BM 실측: `4월 22일 김해 제1공장 … 대상 50명`).
        contested = {_RELATION_OF[p] for hard, *_r in candidates for p in hard}
        weak = [c for c in candidates if not c[0] and not c[4] and not ({_RELATION_OF[p] for p in c[1]} & contested)]
        pick = next(iter(sorted(weak, key=lambda c: c[3] is None)), None)
    if pick is not None:
        return Support(True, conflicting=conflicts, fact=pick[2], occurrence=pick[3], outside_citation=pick[4])
    # 보류 사유는 어긋난 관계다. 어긋남 없이 '확인 안 됨'만 있었으면(인용 밖 청크) 그것을 적는다.
    problems = sorted({p for hard, _w, *_r in candidates for p in hard}) \
        or sorted({p for _h, weak, *_r in candidates for p in weak})
    return Support(False, "relation_mismatch" if candidates else "orphan_number", conflicts, problems=problems)


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
        key = (f.value, f.role, f.period_text, f.unit, f.group, f.sites)   # 다른 집단·사업장의 같은 값은 따로 적는다
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


def table_rows(rows: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    """본문 표(`원문 확인 수치`)에 실을 사실 — 참여 역할(대상·참석·미참석)이 붙었거나 합계인 집계만.

    주간·개인 행(`발생량`·`합계` 시간)과 구성값(사업장별 인원)은 생성 입력에만 두고 표에는 싣지 않는다. 같은 값·역할·
    기간·고용형태·사업장은 한 줄로 둔다(같은 집계를 두 문서가 다시 적은 경우). 기간 순으로 적는다.
    """
    picked, seen = [], set()
    for r in rows:
        if not (r.get("role") or r.get("value_role") == "total"):
            continue
        # 다른 고용형태·사업장의 같은 값·역할·기간은 한 줄로 합치지 않는다(PR71 재검토 §10 — `참석 · 정규직 6명`·`참석 · 기간제 6명`).
        key = (r.get("value"), r.get("unit"), r.get("role"), r.get("period_text"), label_group(r.get("label", "")),
               _sites(r.get("label", "")) or _sites(r.get("site", "")))
        if key in seen:
            continue
        seen.add(key)
        picked.append(r)

    def order(r):
        from datetime import date
        span = period_of(r.get("period_text", ""), r.get("period_start", ""), r.get("period_end", ""), r.get("label", ""))
        when = (span.end, span.start) if span is not None else (date.max, date.max)
        return when, {"대상": 0, "참석": 1, "미참석": 2}.get(r.get("role") or "", 3), r.get("label", "")
    return sorted(picked, key=order)[:limit]


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
        label = row.get("label", "")
        out.append(Fact(label=label, value=value,
                        unit=normalize_unit(str(row.get("unit") or "")) if row.get("unit") else None,
                        role=row.get("role") or label_role(label),
                        period=period_of(row.get("period_text", ""), row.get("period_start", ""),
                                         row.get("period_end", ""), label),
                        period_text=row.get("period_text", ""), source_file=row.get("source_file", ""),
                        kind="source", tokens=label_tokens(label), group=label_group(label),
                        sites=_sites(label) or _sites(row.get("site", ""))))
    return out


def summary_input_facts(rows: list[tuple[str, Any, tuple[str, ...]]]) -> list[Fact]:
    """요약(Executive Summary) 생성 입력의 수치 [(이름, 값, 단위들)] — 요약이 그대로 옮긴 커버리지·누락 수의 근거.

    원문 사실이 아니라 분석 결과다(`kind="summary_input"`). 역할·집단·기간이 없으므로 역할·집단을 밝힌 수량(교육 인원 등)의
    근거는 되지 않는다(`_relation_problems`).
    """
    from .rag_gates.units import normalize_unit
    out: list[Fact] = []
    for label, value, units in rows:
        if value is None or isinstance(value, bool):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        for unit in units:
            out.append(Fact(label=label, value=number, unit=normalize_unit(unit), kind="summary_input",
                            source_file="요약 생성 입력"))
    return out


def is_pseudo_chunk(chunk_id: str) -> bool:
    return str(chunk_id or "").startswith(_PSEUDO_CHUNK_PREFIXES)
