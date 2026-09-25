"""Shared helpers for lightweight grounding signals."""
from __future__ import annotations

import re
from dataclasses import dataclass, field


_CITATION_RE = re.compile(r"\[([0-9A-Za-z가-힣._:-]+)\]")
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?(?:[eE][+-]?\d+)?")
_SEPARATOR_RE = re.compile(r"^\|\s*[-: ]+\|\s*$")
# 한국어 자릿수 복합 표기('211억 1,600만'). 자릿수 글자가 붙은 조각이 하나라도
# 있어야 매칭되므로 평범한 숫자 나열('2,774명 7회')은 묶이지 않는다.
_KR_SCALES = {"조": 1e12, "억": 1e8, "만": 1e4}
# 숫자와 자릿수 글자는 붙어 있어야 한다. '3 조', '114\n조'처럼 떨어져 있으면 표·줄바꿈이
# 끼어든 것이지 한 금액이 아니다. 조각 사이 구분은 가로 공백만 허용한다(개행 금지).
_KR_SCALE_PART = r"\d[\d,]*(?:\.\d+)?(?:조|억|만)"
_KR_SCALE_RUN_RE = re.compile(rf"{_KR_SCALE_PART}(?:[^\S\r\n]*{_KR_SCALE_PART})*")
_KR_SCALE_PART_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(조|억|만)")
# '조'는 자릿수(兆)이기도 하고 법·정관의 조항 단위이기도 하다. 조항 번호를 금액으로 읽으면
# '제25조' 한 건이 25조 원이 되어 근거에서 찾을 수 없는 숫자로 보고된다(2026-09-23 실측:
# 조항문 647건 중 9건, 보고서 문장 484건 중 12건이 여기에 해당). 금액으로 확신할 수 있는
# 문맥에서만 자릿수로 읽고, 아니면 종전처럼 숫자 부분만 본다.
_ARTICLE_PREFIX_RE = re.compile(r"제\s*$")
_AMOUNT_SUFFIX_RE = re.compile(r"^[^\S\r\n]*(?:원|달러|엔|위안|USD|KRW|JPY|CNY|EUR|유로)")


@dataclass
class CitedSentence:
    raw_text: str
    clean_text: str
    cited_chunk_ids: list[str] = field(default_factory=list)


def parse_cited_sentences(text: str) -> list[CitedSentence]:
    out: list[CitedSentence] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or _is_structural_line(line):
            continue
        citations = [m.group(1) for m in _CITATION_RE.finditer(line)]
        clean = strip_citation_markers(line).strip()
        if clean:
            out.append(CitedSentence(raw_text=line, clean_text=clean, cited_chunk_ids=citations))
    return out


def strip_citation_markers(text: str) -> str:
    cleaned = _CITATION_RE.sub("", text)
    # 가로 공백(스페이스·탭)만 정리하고 개행은 보존한다.
    # \s{2,} 로 뭉개면 문단·헤딩·표 구분용 \n\n 이 사라져 마크다운이 깨진다.
    cleaned = re.sub(r"[^\S\r\n]{2,}", " ", cleaned)
    return cleaned.strip()


def _number_tokens(text: str) -> list[tuple[str, float | None]]:
    """(원문 토큰, 값) 목록. 한국어 복합 자릿수 표기는 한 토큰으로 묶는다.

    '211억 1,600만 원'은 21,116,000,000원 하나이지 211과 1600 두 숫자가 아니다.
    종전에는 자릿수 글자를 무시해 두 조각으로 쪼갰고, 그래서 원장값과 같은 금액을
    써도 G2가 근거에서 찾지 못해 확인 항목이 됐다(2026-09-20 실측: S-2-4 교육훈련비
    21,116,000,000원을 본문이 '211억 1,600만 원'으로 적었는데 211·1600이 미확인
    숫자로 보고됨). 값은 정확한 곱셈·덧셈으로만 합치므로, 틀린 숫자는 여전히 걸린다.
    '천'은 '3천 5백' 같은 구어 표기와 섞여 오히려 오합침 위험이 커서 넣지 않는다.

    이 묶기만으로 그 사례가 해소되지는 않는다(2026-09-23 실측). 같은 금액을 담은 실제
    근거 청크가 '[S-2-4] 21116.0백만 원'처럼 **단위를 축약한 형태**여서, 본문의 금액
    표기와 숫자 문자열이 여전히 다르다. 전·후 모두 G2에 남는다(종전 '1600', 지금
    '211억 1600만'). 표기 단위와 원장 축약 단위의 대조는 별도 문제로 남아 있다.
    """
    tokens: list[tuple[str, float | None]] = []
    runs = [m for m in _KR_SCALE_RUN_RE.finditer(text) if _is_amount_scale_run(text, m)]
    for match in runs:
        tokens.append((match.group(0).strip(), _parse_kr_scale_run(match.group(0))))
    covered = [m.span() for m in runs]
    for match in _NUMBER_RE.finditer(text):
        if any(start <= match.start() and match.end() <= end for start, end in covered):
            continue
        tokens.append((match.group(0), None))
    return tokens


def _is_amount_scale_run(text: str, match: re.Match[str]) -> bool:
    """이 자릿수 표기를 금액으로 읽어도 되는지 판단한다.

    '억'·'만'은 수량 단위로만 쓰이므로 그대로 둔다. '조'만 걸러낸다 — 법·정관 조항 번호가
    같은 글자를 쓴다('상법 제388조', '정관 제29조 개정', "환경경영 정책 2조 '기본원칙'").
    금액이라고 볼 근거는 두 가지뿐이다: 뒤에 통화 단위가 붙거나('1조 원'), 더 작은 자릿수가
    이어진다('1조5000억'). 둘 다 아니면 종전 동작(숫자 부분만 보기)으로 되돌린다 —
    조항 번호를 25조 원으로 읽는 쪽이 놓치는 쪽보다 나쁘다.
    """
    raw = match.group(0)
    parts = _KR_SCALE_PART_RE.findall(raw)
    if not any(scale == "조" for _part, scale in parts):
        return True
    if len(parts) > 1:  # '1조5000억' — 조항 번호는 이렇게 이어지지 않는다
        return True
    if _ARTICLE_PREFIX_RE.search(text[:match.start()]):  # '제25조'
        return False
    return bool(_AMOUNT_SUFFIX_RE.match(text[match.end():]))


def _parse_kr_scale_run(raw: str) -> float | None:
    from .units import parse_number as _parse

    total = 0.0
    seen = False
    for part, scale in _KR_SCALE_PART_RE.findall(raw):
        value = _parse(part.replace(",", ""))
        if value is None:
            return None
        total += value * _KR_SCALES.get(scale, 1.0)
        seen = True
    return total if seen else None


def extract_numbers(text: str) -> list[str]:
    values: list[str] = []
    for token, _value in _number_tokens(text):
        compact = token.replace(",", "")
        if _looks_like_report_year(compact):
            continue
        values.append(compact)
    return values


def number_in_text(number: str, text: str) -> bool:
    """Check if a number appears in text using normalized comparison."""
    from .units import numeric_equal, parse_number as _parse

    target = _parse_kr_scale_run(number) if _KR_SCALE_RUN_RE.fullmatch(number.strip()) else None
    if target is None:
        target = _parse(number.replace(",", ""))
    if target is None:
        return False
    for token, value in _number_tokens(text):
        candidate = value if value is not None else _parse(token.replace(",", ""))
        if candidate is not None and numeric_equal(target, candidate):
            return True
    return False


def is_claim_sentence(text: str) -> bool:
    line = text.strip()
    if len(line) < 6:
        return False
    if _is_structural_line(line):
        return False
    return bool(re.search(r"[A-Za-z가-힣]", line))


def _is_structural_line(line: str) -> bool:
    return (
        line.startswith("#")
        or line.startswith("|")
        or _SEPARATOR_RE.match(line) is not None
        or line.startswith(">")
    )


def _looks_like_report_year(token: str) -> bool:
    if len(token) != 4 or not token.isdigit():
        return False
    year = int(token)
    return 1900 <= year <= 2100
