"""Shared helpers for lightweight grounding signals."""
from __future__ import annotations

import re
from dataclasses import dataclass, field


_CITATION_RE = re.compile(r"\[([0-9A-Za-z가-힣._:-]+)\]")
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?(?:[eE][+-]?\d+)?")
_SEPARATOR_RE = re.compile(r"^\|\s*[-: ]+\|\s*$")


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
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


def extract_numbers(text: str) -> list[str]:
    values: list[str] = []
    for match in _NUMBER_RE.finditer(text):
        token = match.group(0)
        compact = token.replace(",", "")
        if _looks_like_report_year(compact):
            continue
        values.append(compact)
    return values


def number_in_text(number: str, text: str) -> bool:
    """Check if a number appears in text using normalized comparison."""
    from .units import numeric_equal, parse_number as _parse

    target = _parse(number.replace(",", ""))
    if target is None:
        return False
    for match in _NUMBER_RE.finditer(text):
        candidate = _parse(match.group(0).replace(",", ""))
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
