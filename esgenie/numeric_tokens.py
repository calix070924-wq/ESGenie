"""Shared numeric syntax. Callers retain their existing supported unit vocabularies."""
from __future__ import annotations

import math
import re

# Block partial matches inside identifiers, signs, decimal/grouping mistakes.
TOKEN_START = r'(?<![0-9A-Za-z가-힣_.,+\-−])'
VALID_NUMBER = r'[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'
CANDIDATE_NUMBER = r'[+\-−]?[\d.,]+(?:[eE][+\-]?\d+)?'
TOKEN_END = r'(?![\d.,]|[eE][+\-]?\d)'


def number_pattern(units: str, *, candidates: bool = False) -> re.Pattern:
    number = CANDIDATE_NUMBER if candidates else VALID_NUMBER
    return re.compile(TOKEN_START + rf'(?P<num>{number})' + TOKEN_END
                      + rf'\s*(?P<unit>{units})(?![A-Za-z0-9_])')


def parse_number(raw: str) -> float | None:
    """Malformed grouping and unsupported notation never become partial values."""
    if not re.fullmatch(VALID_NUMBER, raw):
        return None
    value = float(raw.replace(',', '').replace('−', '-'))
    return value if math.isfinite(value) else None
