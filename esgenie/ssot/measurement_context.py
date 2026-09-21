"""원문 경계의 날짜/머리말 파싱. 문서의 다른 데이터 행을 머리말로 쓰지 않는다."""
from __future__ import annotations

import calendar
import re
from datetime import date


_DATE_RANGE = re.compile(
    r"(20\d{2})[년./-]\s*(\d{1,2})[월./-]\s*(\d{1,2})일?\s*[~–—-]\s*"
    r"(?:(20\d{2})[년./-]\s*)?(\d{1,2})[월./-]\s*(\d{1,2})일?")


def period_bounds(text):
    """확인된 구간만 ISO 날짜로 정규화. 연도만 주어지면 양끝은 미상."""
    text = str(text or "")
    year_match = re.search(r"20\d{2}", text)
    year = int(year_match[0]) if year_match else None
    m = _DATE_RANGE.search(text)
    if m:
        y, mo, d, y2, mo2, d2 = m.groups()
        try:
            start, end = date(int(y), int(mo), int(d)), date(int(y2 or y), int(mo2), int(d2))
            if end < start:
                return year, "", "", "unknown", None
            days = (end-start).days+1
            annual = start.month == start.day == 1 and end.month == 12 and end.day == 31 and start.year == end.year
            return start.year, start.isoformat(), end.isoformat(), "annual" if annual else "monthly" if 28 <= days <= 31 else "period_total", 12 if annual else 1 if 28 <= days <= 31 else None
        except ValueError:
            return year, "", "", "unknown", None
    if not year:
        return None, "", "", "unknown", None
    months = None
    if re.search(r"연간|연\s*합계|annual|년간", text, re.I):
        months = (1, 12)
    elif "상반기" in text:
        months = (1, 6)
    elif "하반기" in text:
        months = (7, 12)
    else:
        m = re.search(r"(\d{1,2})\s*월?\s*[~–-]\s*(\d{1,2})\s*월", text)
        q = re.search(r"([1-4])\s*분기|[qQ]([1-4])", text)
        if m:
            months = tuple(map(int, m.groups()))
        elif q:
            n = int(q[1] or q[2]); months = (n*3-2, n*3)
        else:
            m = re.search(r"20\d{2}\s*[년./-]\s*(\d{1,2})(?:\s*월|(?=$|\s))", text)
            if m:
                months = (int(m[1]), int(m[1]))
    if not months or not 1 <= months[0] <= months[1] <= 12:
        return year, "", "", "unknown", None
    start, end = date(year, months[0], 1), date(year, months[1], calendar.monthrange(year, months[1])[1])
    count = months[1]-months[0]+1
    agg = "annual" if count == 12 else "monthly" if count == 1 else "period_total"
    return year, start.isoformat(), end.isoformat(), agg, count


_HEADER = re.compile(r"(?:사용기간|집계기간|보고기간|산정기간|보고\s*범위|산정\s*범위|조직\s*범위|사업장\s*(?:명|주소)?|고객명|사용자명|분모)\s*[:：]?")
_SECTION = re.compile(r"^(?:\d+[.)]|제\d+조|구분(?:$|\s*\|)|에너지원(?:$|\s*\|)|전월지침|항목$)")


def header_context(raw):
    """표/절 이전의 명시적 메타데이터만 반환. 키 다음 줄의 값도 보존한다."""
    lines = [s.strip() for s in str(raw or "").splitlines() if s.strip()]
    if len(lines) == 1 and re.fullmatch(r"(?:전사|전\s*사업장|제?\s*\d+\s*공장(?:\s+[가-힣·]+라인)?)", lines[0]):
        return lines[0]
    result = []
    for i, line in enumerate(lines):
        if _SECTION.match(line):
            break
        if _HEADER.search(line):
            result.append(line)
            if i+1 < len(lines) and not _HEADER.search(lines[i+1]) and not _SECTION.match(lines[i+1]):
                result.append(lines[i+1])
    return "\n".join(result)


def site_path(text):
    """공장과 그 내부 라인의 명시된 포함 경로만 반환한다."""
    plant = re.search(r"제?\s*\d+\s*공장|[가-힣A-Za-z0-9]{1,12}(?:공장|사업장)", text)
    line = re.search(r"[가-힣A-Za-z0-9·]+라인", text)
    return tuple(re.sub(r"\s+", "", m[0]) for m in (plant, line) if m)
