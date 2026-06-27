"""Unit and numeric normalization utilities for grounding gate checks."""
from __future__ import annotations

import math
import re

# ── Number parsing ──────────────────────────────────────────────────────────

_COMMA_NUM_RE = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})*(?:\.\d+)?$")
_SCIENTIFIC_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?[eE][+-]?\d+$")
_PLAIN_NUM_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")

_KR_MULTIPLIERS = {"만": 1e4, "억": 1e8, "조": 1e12}
_KR_SUFFIX_RE = re.compile(
    r"^([+-]?\d[\d,]*(?:\.\d+)?)\s*(만|억|조)$"
)


def parse_number(text: str) -> float | None:
    """Parse a numeric string into float, handling commas, scientific notation, and Korean multipliers."""
    s = text.strip()
    if not s:
        return None

    # Korean multiplier suffix (e.g. "1.5만", "3억")
    m = _KR_SUFFIX_RE.match(s)
    if m:
        base = _parse_bare(m.group(1))
        if base is None:
            return None
        return base * _KR_MULTIPLIERS[m.group(2)]

    return _parse_bare(s)


def _parse_bare(s: str) -> float | None:
    """Parse plain, comma-separated, or scientific notation number."""
    if _COMMA_NUM_RE.match(s):
        return float(s.replace(",", ""))
    if _SCIENTIFIC_RE.match(s):
        return float(s)
    if _PLAIN_NUM_RE.match(s):
        return float(s)
    return None


# ── Unit normalization ──────────────────────────────────────────────────────

_UNIT_ALIASES: dict[str, str] = {
    # Mass / emissions
    "t": "t",
    "ton": "t",
    "tons": "t",
    "톤": "t",
    "tco2eq": "tCO2eq",
    "tco2": "tCO2eq",
    "톤co2eq": "tCO2eq",
    "톤co2": "tCO2eq",
    # Energy
    "kwh": "kWh",
    "킬로와트시": "kWh",
    "mwh": "MWh",
    "메가와트시": "MWh",
    "gwh": "GWh",
    "기가와트시": "GWh",
    # Percentage
    "%": "%",
    "퍼센트": "%",
    # Currency
    "원": "원",
    "백만원": "백만원",
    "억원": "억원",
    # People
    "명": "명",
    "인": "명",
}

# Groups of compatible units with conversion factor TO the base unit.
# Base unit is the first entry (factor=1).
_UNIT_GROUPS: list[dict[str, float]] = [
    {"kWh": 1.0, "MWh": 1000.0, "GWh": 1_000_000.0},
    {"원": 1.0, "백만원": 1_000_000.0, "억원": 100_000_000.0},
]

_UNIT_TO_GROUP: dict[str, dict[str, float]] = {}
for _group in _UNIT_GROUPS:
    for _unit in _group:
        _UNIT_TO_GROUP[_unit] = _group


def normalize_unit(raw: str) -> str | None:
    """Return canonical unit string, or None if unrecognized."""
    key = raw.strip().lower()
    return _UNIT_ALIASES.get(key)


def units_compatible(u1: str, u2: str) -> bool:
    """Check whether two canonical units are in the same compatibility group or identical."""
    if u1 == u2:
        return True
    g1 = _UNIT_TO_GROUP.get(u1)
    g2 = _UNIT_TO_GROUP.get(u2)
    if g1 is None or g2 is None:
        return False
    return g1 is g2


def convert_to_common(value: float, from_unit: str, to_unit: str) -> float | None:
    """Convert value from from_unit to to_unit if they are compatible."""
    if from_unit == to_unit:
        return value
    group = _UNIT_TO_GROUP.get(from_unit)
    if group is None or to_unit not in group:
        return None
    # value is in from_unit; convert to base then to target
    base_value = value * group[from_unit]
    return base_value / group[to_unit]


def numeric_equal(a: float, b: float, rel_tol: float = 0.01) -> bool:
    """Compare two numbers with relative tolerance (default 1%)."""
    return math.isclose(a, b, rel_tol=rel_tol)


# ── Extraction helper for (number, unit) pairs ─────────────────────────────

_NUM_UNIT_RE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*"
    r"(톤CO2eq|톤CO2|tCO2eq|tCO2|톤|ton|tons|t|"
    r"킬로와트시|kWh|메가와트시|MWh|기가와트시|GWh|"
    r"퍼센트|%|백만원|억원|원|명|인)",
    re.IGNORECASE,
)


def extract_number_unit_pairs(text: str) -> list[tuple[float, str]]:
    """Extract (numeric_value, canonical_unit) pairs from text."""
    pairs: list[tuple[float, str]] = []
    for m in _NUM_UNIT_RE.finditer(text):
        num = parse_number(m.group(1))
        unit = normalize_unit(m.group(2))
        if num is not None and unit is not None:
            pairs.append((num, unit))
    return pairs


__all__ = [
    "convert_to_common",
    "extract_number_unit_pairs",
    "normalize_unit",
    "numeric_equal",
    "parse_number",
    "units_compatible",
]
