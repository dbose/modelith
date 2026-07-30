"""Pattern semantics shared across packages (spec §2.3 patterns).

The system columns a pattern's transformation adds beyond the logical attributes.
Single-sourced in core so the emitter (which declares them in contracts), the
drift projection (which must expect them), and reverse lifting (which strips
them) can never disagree — found dogfooding when drift flagged the emitter's own
SCD2 columns as additive drift.

Types are abstract base types; platform adapters map them to SQL types.
"""

from __future__ import annotations

PATTERN_SYSTEM_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "scd2": [
        ("mdl_scd_id", "string"),
        ("mdl_row_hash", "string"),
        ("valid_from", "timestamp"),
        ("valid_to", "timestamp"),
        ("is_current", "boolean"),
    ],
    "hub": [
        ("hub_hashkey", "string"),
        ("load_dts", "timestamp"),
    ],
}
