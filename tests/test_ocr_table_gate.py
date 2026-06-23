from __future__ import annotations

from esgenie.ssot.ocr_router import (
    DocChannel,
    ExtractedTable,
    OcrExtraction,
    TableCell,
)
from esgenie.ssot.ocr_table_gate import (
    apply_table_gate,
    evaluate_table_gate,
    restore_table_deterministic,
)


def _table(rows: list[list[str]], *, confidences: list[list[float | None]] | None = None) -> ExtractedTable:
    cells = []
    for r_idx, row in enumerate(rows):
        for c_idx, content in enumerate(row):
            conf = None
            if confidences and r_idx < len(confidences) and c_idx < len(confidences[r_idx]):
                conf = confidences[r_idx][c_idx]
            cells.append(TableCell(
                row_index=r_idx,
                column_index=c_idx,
                content=content,
                confidence=conf,
            ))
    return ExtractedTable(
        table_id="t0",
        row_count=len(rows),
        column_count=max((len(row) for row in rows), default=0),
        cells=cells,
        source="azure_docintel",
        page=0,
    )


def test_table_gate_accepts_well_formed_total_table():
    table = _table(
        [
            ["항목", "2024 (kWh)", "2025 (kWh)"],
            ["전력", "100", "120"],
            ["가스", "50", "60"],
            ["합계", "150", "180"],
        ],
        confidences=[
            [0.98, 0.99, 0.99],
            [0.97, 0.95, 0.96],
            [0.96, 0.95, 0.95],
            [0.97, 0.96, 0.96],
        ],
    )
    result = evaluate_table_gate(table)
    assert result.decision == "ACCEPT"
    assert result.hard_fail_count == 0


def test_table_gate_escalates_on_total_mismatch():
    table = _table(
        [
            ["항목", "2025 (ton)"],
            ["재활용", "5"],
            ["소각", "3"],
            ["합계", "12"],
        ],
        confidences=[
            [0.98, 0.98],
            [0.95, 0.95],
            [0.95, 0.95],
            [0.95, 0.95],
        ],
    )
    result = evaluate_table_gate(table)
    assert result.decision == "ESCALATE"
    assert any(flag.code == "B1_total_consistency" and not flag.passed for flag in result.flags)


def test_restore_table_drops_empty_rows_and_cols():
    restored = restore_table_deterministic(_table(
        [
            ["항목", "", "2025 (kWh)"],
            ["", "", ""],
            ["전력", "", "100"],
            ["가스", "", "50"],
            ["합계", "", "150"],
        ],
        confidences=[
            [0.95, None, 0.95],
            [None, None, None],
            [0.95, None, 0.95],
            [0.95, None, 0.95],
            [0.95, None, 0.95],
        ],
    ))
    assert restored.row_count == 4
    assert restored.column_count == 2
    op_names = [op["name"] for op in restored.meta.get("restoration_ops", [])]
    assert "drop_empty_rows" in op_names
    assert "drop_empty_cols" in op_names


def test_apply_table_gate_recovers_with_tier1_restore():
    ext = OcrExtraction(
        source_file="table.pdf",
        channel=DocChannel.STRUCTURED,
        doc_type="generic_table",
        tables=[_table(
            [
                ["항목", "에너지 (kWh)"],
                ["", "2025"],
                ["A", "10"],
                ["B", "20"],
                ["C", "30"],
                ["D", "4O"],
                ["합계", "100"],
            ],
            confidences=[
                [0.95, 0.95],
                [0.95, 0.95],
                [0.95, 0.95],
                [0.95, 0.95],
                [0.95, 0.95],
                [0.95, 0.95],
                [0.95, 0.95],
            ],
        )],
    )
    payload = apply_table_gate(ext, tier=0)
    assert payload["status"] == "ACCEPT"
    assert payload["resolved_tier"] == 1
    assert len(payload["attempts"]) == 2
    assert payload["attempts"][0]["status"] == "ESCALATE"
    assert ext.tables[0].meta["resolved_tier"] == 1
    op_names = [op["name"] for op in ext.tables[0].meta.get("restoration_ops", [])]
    assert "normalize_numeric_text" in op_names
    assert "flatten_multirow_header" in op_names
