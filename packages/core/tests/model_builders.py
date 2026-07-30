"""Importable test model builders (shared across packages).

Kept separate from conftest.py so emit-dbt's tests can import it without the
two conftest modules colliding on the name `conftest`.
"""

from __future__ import annotations

from pathlib import Path

from mdl_core.ids import new_ulid


def write_model(root: Path, *, with_scd2: bool = False) -> dict[str, str]:
    sa = new_ulid()
    ce = new_ulid()
    ce2 = new_ulid()
    le = new_ulid()
    le2 = new_ulid()
    a1 = new_ulid()
    a2 = new_ulid()
    a3 = new_ulid()
    dom = new_ulid()
    rel = new_ulid()

    (root / "conceptual" / "subject-areas").mkdir(parents=True, exist_ok=True)
    (root / "conceptual" / "entities").mkdir(parents=True, exist_ok=True)
    (root / "logical" / "domains").mkdir(parents=True, exist_ok=True)
    (root / "logical" / "entities").mkdir(parents=True, exist_ok=True)
    (root / "logical" / "relationships").mkdir(parents=True, exist_ok=True)

    (root / "mdl-project.yaml").write_text(
        "name: testmodel\n"
        "dbt_target: duckdb_dev\n"
        "platform_targets:\n  - duckdb_dev\n"
        "ontology_stack: []\n"
        "naming:\n  logical_case: snake\n  physical_case: upper_snake\n"
    )
    (root / "conceptual" / "subject-areas" / "trading.yaml").write_text(
        f"id: {sa}\nkind: subject_area\nname: Trading\n"
    )
    (root / "conceptual" / "entities" / "counterparty.yaml").write_text(
        f"id: {ce}\n"
        "kind: conceptual_entity\n"
        "name: Counterparty\n"
        f"subject_area: {sa}\n"
        "definition: A legal person the firm transacts with.\n"
        "ontology:\n"
        "  aligns_to: fibo-fnd-pty-pty:PartyInRole\n"
        "  alignment: skos:exactMatch\n"
        "  layer: core\n"
        "stewardship:\n  owner: risk\n  steward: a.hough\n"
    )
    (root / "conceptual" / "entities" / "trade.yaml").write_text(
        f"id: {ce2}\nkind: conceptual_entity\nname: Trade\nsubject_area: {sa}\n"
    )
    (root / "logical" / "domains" / "id_bigint.yaml").write_text(
        f"id: {dom}\nkind: domain\nname: id_bigint\nbase_type: bigint\n"
    )
    # Omit `pattern` entirely when null: `pattern: null` is non-canonical (ruamel
    # normalises all null spellings to empty), so canonical form is to leave it out.
    scd_line = "pattern: scd2\n" if with_scd2 else ""
    (root / "logical" / "entities" / "counterparty.yaml").write_text(
        f"id: {le}\n"
        "kind: logical_entity\n"
        "name: counterparty\n"
        f"realises: {ce}\n"
        "attributes:\n"
        f"  - id: {a1}\n"
        "    name: counterparty_id\n"
        "    domain: id_bigint\n"
        "    role: business_key\n"
        "    nullable: false\n"
        f"  - id: {a2}\n"
        "    name: legal_name\n"
        "    domain: id_bigint\n"
        "    role: attribute\n"
        "    nullable: true\n"
        f"{scd_line}"
    )
    (root / "logical" / "entities" / "trade.yaml").write_text(
        f"id: {le2}\n"
        "kind: logical_entity\n"
        "name: trade\n"
        f"realises: {ce2}\n"
        "attributes:\n"
        f"  - id: {a3}\n"
        "    name: trade_id\n"
        "    domain: id_bigint\n"
        "    role: business_key\n"
        "    nullable: false\n"
    )
    (root / "logical" / "relationships" / "trade_cpty.yaml").write_text(
        f"id: {rel}\n"
        "kind: relationship\n"
        "name: trade_has_counterparty\n"
        f"from: {{entity: {le2}, attributes: [{a3}]}}\n"
        f"to: {{entity: {le}, attributes: [{a1}]}}\n"
        "cardinality: many_to_one\n"
        "identifying: false\n"
        "optionality: mandatory\n"
    )
    return {
        "sa": sa, "ce": ce, "ce2": ce2, "le": le, "le2": le2,
        "a1": a1, "a2": a2, "a3": a3, "dom": dom, "rel": rel,
    }
