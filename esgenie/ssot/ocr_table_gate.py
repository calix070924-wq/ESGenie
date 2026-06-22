"""Tier 0/1 표 복원 검증 게이트.

PR-A/B 범위:
  - Tier 0: 신호 A/B/C 기반 구조 검증
  - Tier 1: 결정적 규칙 복원(빈 행/열 제거, merged header fill, multi-header flatten,
            숫자 안전 치환) 후 재검증
  - Tier 2/3는 아직 미구현이므로, Tier 1까지도 통과 못 하면 pending 플래그만 남긴다
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from statistics import median
from typing import Any

from .ocr_router import ExtractedTable, OcrExtraction, TableCell


_TOTAL_LABEL_RE = re.compile(r"(합계|소계|총계|계\b|total|subtotal)", re.IGNORECASE)
_UNIT_RE = re.compile(
    r"(%|kwh|mwh|gwh|mj|gj|tco2e?q?|ton|톤|kg|m3|㎥|l\b|ℓ|원|krw|usd)",
    re.IGNORECASE,
)
_NUMERIC_RE = re.compile(r"^\s*[-(]?\d[\d,\s]*(?:\.\d+)?\)?\s*%?\s*$")
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_NUMERIC_CONFUSION_MAP = str.maketrans({
    "O": "0",
    "o": "0",
    "I": "1",
    "l": "1",
    "|": "1",
})


@dataclass
class GateFlag:
    code: str
    passed: bool
    severity: str
    detail: str
    observed: Any = None
    threshold: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TableGateResult:
    table_id: str
    decision: str
    hard_fail_count: int
    soft_flag_count: int
    needs_semantic_check: bool = False
    flags: list[GateFlag] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "table_id": self.table_id,
            "decision": self.decision,
            "hard_fail_count": self.hard_fail_count,
            "soft_flag_count": self.soft_flag_count,
            "needs_semantic_check": self.needs_semantic_check,
            "flags": [f.to_dict() for f in self.flags],
        }


def _is_numeric(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return False
    if _YEAR_RE.fullmatch(stripped):
        return True
    return bool(_NUMERIC_RE.fullmatch(stripped))


def _to_float(text: str) -> float | None:
    stripped = (text or "").strip()
    if not stripped:
        return None
    stripped = stripped.replace("%", "").replace(",", "").replace(" ", "")
    stripped = stripped.strip("()")
    try:
        return float(stripped)
    except ValueError:
        return None


def _expand_table(table: ExtractedTable) -> list[list[list[TableCell]]]:
    max_row = max((c.row_index + c.row_span) for c in table.cells) if table.cells else 0
    max_col = max((c.column_index + c.column_span) for c in table.cells) if table.cells else 0
    rows = max(table.row_count, max_row)
    cols = max(table.column_count, max_col)
    grid: list[list[list[TableCell]]]
    grid = [[[] for _ in range(cols)] for _ in range(rows)]
    for cell in table.cells:
        for ridx in range(cell.row_index, min(rows, cell.row_index + max(1, cell.row_span))):
            for cidx in range(cell.column_index, min(cols, cell.column_index + max(1, cell.column_span))):
                grid[ridx][cidx].append(cell)
    return grid


def _slot_grid(
    table: ExtractedTable,
    *,
    expanded: list[list[list[TableCell]]] | None = None,
) -> list[list[TableCell]]:
    expanded = expanded if expanded is not None else _expand_table(table)
    out: list[list[TableCell]] = []
    for ridx, row in enumerate(expanded):
        slot_row: list[TableCell] = []
        for cidx, slot in enumerate(row):
            primary = slot[0] if slot else None
            slot_row.append(TableCell(
                row_index=ridx,
                column_index=cidx,
                content=(primary.content if primary else ""),
                row_span=1,
                column_span=1,
                kind=(primary.kind if primary else None),
                bbox=(primary.bbox if primary else None),
                page=(primary.page if primary else None),
                confidence=(primary.confidence if primary else None),
            ))
        out.append(slot_row)
    return out


def _table_from_slot_grid(
    grid: list[list[TableCell]],
    *,
    original: ExtractedTable,
    ops: list[dict[str, Any]],
) -> ExtractedTable:
    cells: list[TableCell] = []
    for ridx, row in enumerate(grid):
        for cidx, cell in enumerate(row):
            cells.append(TableCell(
                row_index=ridx,
                column_index=cidx,
                content=cell.content,
                row_span=1,
                column_span=1,
                kind=cell.kind,
                bbox=cell.bbox,
                page=cell.page,
                confidence=cell.confidence,
            ))
    meta = dict(getattr(original, "meta", {}) or {})
    if ops:
        meta["restoration_ops"] = ops
    return ExtractedTable(
        table_id=original.table_id,
        row_count=len(grid),
        column_count=len(grid[0]) if grid else 0,
        cells=cells,
        source=original.source,
        page=original.page,
        meta=meta,
    )


def _slot_text(cell: TableCell) -> str:
    return (cell.content or "").strip()


def _header_depth(grid: list[list[TableCell]]) -> int:
    if not grid:
        return 0
    limit = min(2, len(grid))
    header_rows = 0
    for ridx in range(limit):
        texts = [_slot_text(cell) for cell in grid[ridx] if _slot_text(cell)]
        if not texts:
            continue
        numeric_ratio = sum(_is_numeric(text) for text in texts) / len(texts)
        header_like_ratio = sum(bool(_YEAR_RE.search(text) or _UNIT_RE.search(text)) for text in texts) / len(texts)
        if numeric_ratio < 0.5 or (ridx > 0 and header_rows > 0 and header_like_ratio >= 0.5):
            header_rows += 1
    return header_rows or 1


def _numeric_columns(grid: list[list[TableCell]], *, header_depth: int) -> list[int]:
    if not grid:
        return []
    cols = len(grid[0])
    numeric_cols: list[int] = []
    for cidx in range(cols):
        values = [_slot_text(grid[ridx][cidx]) for ridx in range(header_depth, len(grid))]
        values = [value for value in values if value]
        if not values:
            continue
        if sum(_is_numeric(value) for value in values) / len(values) >= 0.8:
            numeric_cols.append(cidx)
    return numeric_cols


def _row_label_text(grid: list[list[TableCell]], row_idx: int) -> str:
    row = grid[row_idx] if 0 <= row_idx < len(grid) else []
    for cell in row:
        text = _slot_text(cell)
        if text and not _is_numeric(text):
            return text
    return ""


def _signal_rectangularity(expanded: list[list[list[TableCell]]]) -> GateFlag:
    if not expanded:
        return GateFlag("A1_rectangularity", False, "hard", "표 그리드가 비어 있음")
    expected = len(expanded[0])
    bad_rows = 0
    for row in expanded:
        occupied = sum(1 for slot in row if slot)
        if occupied != expected:
            bad_rows += 1
    ratio = bad_rows / len(expanded)
    return GateFlag(
        "A1_rectangularity",
        ratio <= 0.05,
        "hard",
        "행별 열 점유 수가 일관적인가",
        observed=round(ratio, 4),
        threshold=0.05,
    )


def _signal_blank_ratio(grid: list[list[TableCell]]) -> GateFlag:
    total = len(grid) * len(grid[0]) if grid else 0
    if total == 0:
        return GateFlag("A2_blank_ratio", False, "soft", "표 슬롯이 없음")
    blanks = sum(1 for row in grid for cell in row if not _slot_text(cell))
    ratio = blanks / total
    return GateFlag(
        "A2_blank_ratio",
        ratio <= 0.20,
        "soft",
        "빈 칸 비율이 과도하지 않은가",
        observed=round(ratio, 4),
        threshold=0.20,
    )


def _signal_header_presence(grid: list[list[TableCell]]) -> GateFlag:
    depth = _header_depth(grid)
    header_texts = [
        _slot_text(cell)
        for ridx in range(min(depth, len(grid)))
        for cell in grid[ridx]
        if _slot_text(cell)
    ]
    numeric_ratio = (
        sum(_is_numeric(text) for text in header_texts) / len(header_texts)
        if header_texts else 1.0
    )
    passed = bool(header_texts) and numeric_ratio < 0.5
    return GateFlag(
        "A3_header_presence",
        passed,
        "soft",
        "첫 1~2행이 헤더처럼 보이는가",
        observed=round(numeric_ratio, 4),
        threshold="<0.5 numeric ratio",
    )


def _signal_column_type(grid: list[list[TableCell]], *, header_depth: int) -> GateFlag:
    numeric_cols = _numeric_columns(grid, header_depth=header_depth)
    worst_ratio = 0.0
    for cidx in numeric_cols:
        vals = [_slot_text(grid[ridx][cidx]) for ridx in range(header_depth, len(grid))]
        vals = [value for value in vals if value]
        if not vals:
            continue
        non_numeric_ratio = sum(not _is_numeric(value) for value in vals) / len(vals)
        worst_ratio = max(worst_ratio, non_numeric_ratio)
    return GateFlag(
        "A4_column_type_consistency",
        worst_ratio <= 0.10,
        "soft",
        "수치 열 내부에 텍스트 오염이 과도하지 않은가",
        observed=round(worst_ratio, 4),
        threshold=0.10,
    )


def _signal_totals(grid: list[list[TableCell]], *, header_depth: int) -> GateFlag:
    numeric_cols = _numeric_columns(grid, header_depth=header_depth)
    total_rows = [
        ridx for ridx in range(header_depth, len(grid))
        if _TOTAL_LABEL_RE.search(_row_label_text(grid, ridx))
    ]
    mismatches: list[dict[str, Any]] = []
    for ridx in total_rows:
        for cidx in numeric_cols:
            observed = _to_float(_slot_text(grid[ridx][cidx]))
            if observed is None:
                continue
            vals = []
            for src_ridx in range(header_depth, ridx):
                if src_ridx in total_rows:
                    continue
                value = _to_float(_slot_text(grid[src_ridx][cidx]))
                if value is not None:
                    vals.append(value)
            if not vals:
                continue
            expected = sum(vals)
            tolerance = max(1.0, abs(expected) * 0.005)
            if abs(expected - observed) > tolerance:
                mismatches.append({
                    "row": ridx,
                    "col": cidx,
                    "expected": round(expected, 4),
                    "observed": round(observed, 4),
                    "tolerance": round(tolerance, 4),
                })
    return GateFlag(
        "B1_total_consistency",
        not mismatches,
        "hard",
        "합계/소계 값이 구성 항목의 합과 맞는가",
        observed=mismatches[:3],
        threshold="rel<=0.5% or abs<=1",
    )


def _signal_units(grid: list[list[TableCell]], *, header_depth: int) -> GateFlag:
    numeric_cols = _numeric_columns(grid, header_depth=header_depth)
    if not numeric_cols:
        return GateFlag("B2_unit_presence", True, "soft", "수치 열이 없어 단위 검사 스킵")
    header_text = " ".join(
        _slot_text(cell)
        for ridx in range(min(header_depth + 1, len(grid)))
        for cell in grid[ridx]
    )
    passed = bool(_UNIT_RE.search(header_text))
    return GateFlag(
        "B2_unit_presence",
        passed,
        "soft",
        "헤더/인접 셀에 단위가 보이는가",
        observed=header_text[:200],
        threshold="unit regex hit",
    )


def _signal_magnitude(grid: list[list[TableCell]], *, header_depth: int) -> GateFlag:
    numeric_cols = _numeric_columns(grid, header_depth=header_depth)
    flagged = False
    for cidx in numeric_cols:
        digits = []
        for ridx in range(header_depth, len(grid)):
            value = _to_float(_slot_text(grid[ridx][cidx]))
            if value is None:
                continue
            digits.append(len(str(int(abs(value)))) if abs(value) >= 1 else 1)
        if len(digits) >= 3:
            med = median(digits)
            if max(abs(digit - med) for digit in digits) >= 3:
                flagged = True
                break
    return GateFlag(
        "B3_magnitude_outlier",
        not flagged,
        "soft",
        "같은 열의 자릿수/범위가 과도하게 튀지 않는가",
    )


def _signal_year_order(grid: list[list[TableCell]], *, header_depth: int) -> GateFlag:
    texts = [
        _slot_text(cell)
        for ridx in range(min(header_depth, len(grid)))
        for cell in grid[ridx]
        if _slot_text(cell)
    ]
    years = [int(match.group()) for text in texts for match in _YEAR_RE.finditer(text)]
    if len(years) < 3:
        return GateFlag("B4_year_order", True, "soft", "연도 헤더가 충분치 않아 스킵")
    inc = all(left <= right for left, right in zip(years, years[1:]))
    dec = all(left >= right for left, right in zip(years, years[1:]))
    return GateFlag(
        "B4_year_order",
        inc or dec,
        "soft",
        "연도/기간 헤더 순서가 단조적인가",
        observed=years,
    )


def _signal_confidence_average(table: ExtractedTable) -> GateFlag:
    scores = [cell.confidence for cell in table.cells if cell.confidence is not None]
    if not scores:
        return GateFlag("C1_confidence_average", True, "soft", "cell confidence 없음")
    avg = sum(scores) / len(scores)
    return GateFlag(
        "C1_confidence_average",
        avg >= 0.85,
        "soft",
        "셀 평균 confidence가 충분한가",
        observed=round(avg, 4),
        threshold=0.85,
    )


def _signal_low_confidence_ratio(
    table: ExtractedTable,
    grid: list[list[TableCell]],
    *,
    header_depth: int,
) -> GateFlag:
    numeric_cols = set(_numeric_columns(grid, header_depth=header_depth))
    total_weight = 0.0
    low_weight = 0.0
    for cell in table.cells:
        if cell.confidence is None:
            continue
        weight = 2.0 if cell.column_index in numeric_cols else 1.0
        total_weight += weight
        if cell.confidence < 0.6:
            low_weight += weight
    if total_weight == 0:
        return GateFlag("C2_low_confidence_ratio", True, "hard", "cell confidence 없음")
    ratio = low_weight / total_weight
    return GateFlag(
        "C2_low_confidence_ratio",
        ratio <= 0.10,
        "hard",
        "저신뢰 셀 비율이 과도하지 않은가",
        observed=round(ratio, 4),
        threshold=0.10,
    )


def evaluate_table_gate(table: ExtractedTable) -> TableGateResult:
    if not table.cells:
        return TableGateResult(
            table_id=table.table_id,
            decision="HUMAN",
            hard_fail_count=1,
            soft_flag_count=0,
            flags=[GateFlag("TABLE_EMPTY", False, "hard", "셀 없는 표는 자동 검증 불가")],
        )

    expanded = _expand_table(table)
    grid = _slot_grid(table, expanded=expanded)
    header_depth = _header_depth(grid)
    flags = [
        _signal_rectangularity(expanded),
        _signal_blank_ratio(grid),
        _signal_header_presence(grid),
        _signal_column_type(grid, header_depth=header_depth),
        _signal_totals(grid, header_depth=header_depth),
        _signal_units(grid, header_depth=header_depth),
        _signal_magnitude(grid, header_depth=header_depth),
        _signal_year_order(grid, header_depth=header_depth),
        _signal_confidence_average(table),
        _signal_low_confidence_ratio(table, grid, header_depth=header_depth),
    ]
    hard_fail_count = sum(flag.severity == "hard" and not flag.passed for flag in flags)
    soft_flag_count = sum(flag.severity == "soft" and not flag.passed for flag in flags)

    if hard_fail_count >= 1:
        decision = "ESCALATE"
        needs_semantic_check = False
    elif soft_flag_count <= 1:
        decision = "ACCEPT"
        needs_semantic_check = False
    elif soft_flag_count <= 3:
        decision = "ESCALATE"
        needs_semantic_check = True
    else:
        decision = "ESCALATE"
        needs_semantic_check = False

    return TableGateResult(
        table_id=table.table_id,
        decision=decision,
        hard_fail_count=hard_fail_count,
        soft_flag_count=soft_flag_count,
        needs_semantic_check=needs_semantic_check,
        flags=flags,
    )


def _drop_empty_rows_cols(grid: list[list[TableCell]], ops: list[dict[str, Any]]) -> list[list[TableCell]]:
    if not grid:
        return grid

    keep_rows = [idx for idx, row in enumerate(grid) if any(_slot_text(cell) for cell in row)]
    removed_rows = len(grid) - len(keep_rows)
    grid = [grid[idx] for idx in keep_rows] if keep_rows else []
    if not grid:
        if removed_rows:
            ops.append({"name": "drop_empty_rows", "count": removed_rows})
        return grid

    keep_cols = []
    for cidx in range(len(grid[0])):
        if any(_slot_text(row[cidx]) for row in grid):
            keep_cols.append(cidx)
    removed_cols = len(grid[0]) - len(keep_cols)

    new_grid: list[list[TableCell]] = []
    for ridx, row in enumerate(grid):
        new_row: list[TableCell] = []
        for new_cidx, old_cidx in enumerate(keep_cols):
            cell = row[old_cidx]
            new_row.append(TableCell(
                row_index=ridx,
                column_index=new_cidx,
                content=cell.content,
                row_span=1,
                column_span=1,
                kind=cell.kind,
                bbox=cell.bbox,
                page=cell.page,
                confidence=cell.confidence,
            ))
        new_grid.append(new_row)

    if removed_rows:
        ops.append({"name": "drop_empty_rows", "count": removed_rows})
    if removed_cols:
        ops.append({"name": "drop_empty_cols", "count": removed_cols})
    return new_grid


def _fill_merged_headers(grid: list[list[TableCell]], *, header_depth: int, ops: list[dict[str, Any]]) -> None:
    fills = 0
    for ridx in range(min(header_depth, len(grid))):
        for cidx, cell in enumerate(grid[ridx]):
            if _slot_text(cell):
                continue
            if ridx > 0 and _slot_text(grid[ridx - 1][cidx]):
                cell.content = grid[ridx - 1][cidx].content
                fills += 1
                continue
            if cidx > 0 and _slot_text(grid[ridx][cidx - 1]):
                cell.content = grid[ridx][cidx - 1].content
                fills += 1
    if fills:
        ops.append({"name": "fill_merged_headers", "count": fills})


def _normalize_numeric_text(text: str) -> str:
    raw = (text or "").strip()
    if not raw or not any(ch.isdigit() for ch in raw):
        return raw
    candidate = raw.translate(_NUMERIC_CONFUSION_MAP)
    return candidate if _is_numeric(candidate) else raw


def _normalize_numeric_cells(grid: list[list[TableCell]], *, header_depth: int, ops: list[dict[str, Any]]) -> None:
    numeric_cols = _numeric_columns(grid, header_depth=header_depth)
    changes = 0
    for cidx in numeric_cols:
        for ridx in range(header_depth, len(grid)):
            cell = grid[ridx][cidx]
            normalized = _normalize_numeric_text(cell.content)
            if normalized != cell.content:
                cell.content = normalized
                changes += 1
    if changes:
        ops.append({"name": "normalize_numeric_text", "count": changes})


def _flatten_headers(grid: list[list[TableCell]], *, header_depth: int, ops: list[dict[str, Any]]) -> list[list[TableCell]]:
    if header_depth < 2 or len(grid) < 2:
        return grid

    new_header: list[TableCell] = []
    for cidx in range(len(grid[0])):
        parts: list[str] = []
        source = grid[header_depth - 1][cidx]
        for ridx in range(header_depth):
            cell = grid[ridx][cidx]
            text = _slot_text(cell)
            if not text:
                continue
            source = cell
            if not parts or parts[-1] != text:
                parts.append(text)
        new_header.append(TableCell(
            row_index=0,
            column_index=cidx,
            content="_".join(parts),
            row_span=1,
            column_span=1,
            kind=source.kind,
            bbox=source.bbox,
            page=source.page,
            confidence=source.confidence,
        ))

    new_grid = [new_header]
    for ridx, row in enumerate(grid[header_depth:], start=1):
        new_row: list[TableCell] = []
        for cidx, cell in enumerate(row):
            new_row.append(TableCell(
                row_index=ridx,
                column_index=cidx,
                content=cell.content,
                row_span=1,
                column_span=1,
                kind=cell.kind,
                bbox=cell.bbox,
                page=cell.page,
                confidence=cell.confidence,
            ))
        new_grid.append(new_row)
    ops.append({"name": "flatten_multirow_header", "from_rows": header_depth, "to_rows": 1})
    return new_grid


def restore_table_deterministic(table: ExtractedTable) -> ExtractedTable:
    """Tier 1 규칙 기반 복원. 구조만 고치고 원문 숫자 집합은 안전 치환만 허용한다."""
    ops: list[dict[str, Any]] = []
    grid = _slot_grid(table)
    grid = _drop_empty_rows_cols(grid, ops)
    if not grid:
        return _table_from_slot_grid(grid, original=table, ops=ops)

    header_depth = _header_depth(grid)
    _fill_merged_headers(grid, header_depth=header_depth, ops=ops)
    _normalize_numeric_cells(grid, header_depth=header_depth, ops=ops)
    grid = _flatten_headers(grid, header_depth=header_depth, ops=ops)
    grid = _drop_empty_rows_cols(grid, ops)
    return _table_from_slot_grid(grid, original=table, ops=ops)


def _evaluate_tables(tables: list[ExtractedTable], *, tier: int) -> tuple[dict[str, Any], list[TableGateResult]]:
    results = [evaluate_table_gate(table) for table in tables]
    order = {"ACCEPT": 0, "ESCALATE": 1, "HUMAN": 2}
    worst = max(results, key=lambda result: order.get(result.decision, 1))
    payload = {
        "status": worst.decision,
        "tier": tier,
        "needs_semantic_check": any(result.needs_semantic_check for result in results),
        "tables": [result.to_dict() for result in results],
    }
    return payload, results


def _annotate_resolved_tables(
    tables: list[ExtractedTable],
    *,
    resolved_tier: int,
    results: list[TableGateResult],
) -> None:
    by_id = {result.table_id: result for result in results}
    for table in tables:
        result = by_id.get(table.table_id)
        meta = dict(getattr(table, "meta", {}) or {})
        meta["resolved_tier"] = resolved_tier
        if result is not None:
            meta["gate_flags"] = [flag.code for flag in result.flags if flag.severity == "soft" and not flag.passed]
            meta["hard_fail_codes"] = [flag.code for flag in result.flags if flag.severity == "hard" and not flag.passed]
        table.meta = meta


def apply_table_gate(ext: OcrExtraction, *, tier: int = 0) -> dict[str, Any]:
    """구조화 문서 표에 Tier 0/1 캐스케이드를 적용하고 router_meta에 결과를 남긴다."""
    if getattr(ext, "channel", None) != "structured" and getattr(getattr(ext, "channel", None), "value", None) != "structured":
        payload = {"status": "skipped", "reason": "non_structured_channel", "tier": tier}
        ext.router_meta["table_gate"] = payload
        return payload

    if not getattr(ext, "tables", None):
        payload = {"status": "skipped", "reason": "no_tables", "tier": tier}
        ext.router_meta["table_gate"] = payload
        return payload

    attempts: list[dict[str, Any]] = []

    tier0_payload, tier0_results = _evaluate_tables(ext.tables, tier=0)
    attempts.append(tier0_payload)
    if tier0_payload["status"] == "ACCEPT":
        _annotate_resolved_tables(ext.tables, resolved_tier=0, results=tier0_results)
        payload = {
            **tier0_payload,
            "resolved_tier": 0,
            "attempts": attempts,
        }
        ext.router_meta["table_gate"] = payload
        ext.router_meta.pop("table_gate_pending", None)
        return payload

    restored_tables = [restore_table_deterministic(table) for table in ext.tables]
    tier1_payload, tier1_results = _evaluate_tables(restored_tables, tier=1)
    attempts.append(tier1_payload)
    if tier1_payload["status"] == "ACCEPT":
        ext.tables = restored_tables
        _annotate_resolved_tables(ext.tables, resolved_tier=1, results=tier1_results)
        payload = {
            **tier1_payload,
            "resolved_tier": 1,
            "attempts": attempts,
        }
        ext.router_meta["table_gate"] = payload
        ext.router_meta.pop("table_gate_pending", None)
        return payload

    payload = {
        **tier1_payload,
        "resolved_tier": None,
        "pending_tiers": [2, 3],
        "attempts": attempts,
    }
    ext.router_meta["table_gate"] = payload
    ext.router_meta["table_gate_pending"] = True
    ext.router_meta["hitl_required"] = True
    return payload
