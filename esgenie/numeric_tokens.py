"""Shared numeric syntax. Callers retain their existing supported unit vocabularies."""
from __future__ import annotations

import math
import re

# Block partial matches inside identifiers, signs, decimal/grouping mistakes.
TOKEN_START = r'(?<![0-9A-Za-z가-힣_.,+\-−])'
VALID_NUMBER = r'[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'
CANDIDATE_NUMBER = r'(?:[+\-−]+\s*)?[\d.,]+(?:[eE][+\-]?\d+)?'
TOKEN_END = r'(?![\d.,]|[eE][+\-]?\d)'


class ValidNumberPattern:
    """Regex-compatible iteration that rejects entire malformed candidate tokens.

    Scanning candidates first also prevents '- 100' from leaking a positive 100
    after whitespace. Consumers use only finditer; returned objects are re.Match.
    """
    def __init__(self, pattern: re.Pattern):
        self.pattern = pattern.pattern
        self._pattern = pattern

    def finditer(self, text: str, *bounds):
        for match in self._pattern.finditer(text, *bounds):
            if parse_number(match.group('num')) is not None:
                yield match


def number_pattern(units: str, *, candidates: bool = False):
    pattern = re.compile(TOKEN_START + rf'(?P<num>{CANDIDATE_NUMBER})' + TOKEN_END
                         + rf'\s*(?P<unit>{units})(?![A-Za-z0-9_])')
    return pattern if candidates else ValidNumberPattern(pattern)


def parse_number(raw: str) -> float | None:
    """Malformed grouping and unsupported notation never become partial values."""
    if not re.fullmatch(VALID_NUMBER, raw):
        return None
    value = float(raw.replace(',', '').replace('−', '-'))
    return value if math.isfinite(value) else None
