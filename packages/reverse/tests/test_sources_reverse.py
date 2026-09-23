"""Reverse a live-warehouse `sources.yml` (dbt-codegen generate_source output) through
the full reverse engine.

The live-database front door introspects a warehouse via the dbt adapter, which prints a
`sources.yml` of tables/columns/types. `read_sources_yml` normalises that to the SAME
ManifestProjection a dbt manifest or DDL produces, so it flows through the unchanged
reverse() engine: surrogate stripping, staging exclusion, business-key + FK inference,
confidence bands, the ledger.

Unlike the DDL path, generate_source carries NO declared keys — so keys/relationships come
from the engine's name-based inference (medium-confidence proposals), which is exactly what
the Reverse Review flow triages. These tests pin that front-door parity.
"""

from __future__ import annotations

from mdl_core.yaml_io import load_str
from mdl_reverse.ledger import DecisionLedger
from mdl_reverse.reverse import reverse
from mdl_reverse.schema_reader import read_sources_dict, read_sources_yml

TARGET = "duckdb_dev"

# A generate_source-shaped sources.yml: two governed tables + one staging table, typed
# columns, a nullability flag, a *_sk surrogate, and a *_id column that should infer an FK.
_SOURCES = """
version: 2
sources:
  - name: analytics
    database: prod
    tables:
      - name: customer
        columns:
          - name: customer_id
            data_type: bigint
            nullable: false
          - name: customer_sk
            data_type: varchar
          - name: region_id
            data_type: bigint
          - name: legal_name
            data_type: varchar
            nullable: false
      - name: region
        columns:
          - name: region_id
            data_type: bigint
            nullable: false
          - name: region_name
            data_type: varchar
      - name: stg_raw_events
        columns:
          - name: id
            data_type: bigint
          - name: payload
            data_type: varchar
"""


def _proj():
    return read_sources_dict(load_str(_SOURCES))


def _reverse():
    ledger = DecisionLedger()
    return reverse(
        _proj(),
        project_name="warehouse",
        target=TARGET,
        ledger=ledger,
        interactive=False,
        naming=None,
    )


# --- the reader / projection -------------------------------------------------


def test_projection_shape():
    proj = _proj()
    assert set(proj.models) == {"customer", "region", "stg_raw_events"}
    cust = proj.models["customer"]
    assert cust.unique_id == "model.live.customer"
    assert cust.columns["customer_id"].data_type == "bigint"
    # nullability carried through only where the source reports it
    assert cust.columns["customer_id"].meta.get("nullable") is False
    assert cust.columns["legal_name"].meta.get("nullable") is False
    assert "nullable" not in cust.columns["region_id"].meta  # not reported -> absent
    # generate_source declares no keys/relationships
    assert cust.relationship_tests == []
    assert "pk" not in cust.columns["customer_id"].meta
    # database carried as provenance meta
    assert cust.meta.get("source_database") == "prod"


def test_reader_from_file(tmp_path):
    p = tmp_path / "sources.yml"
    p.write_text(_SOURCES, encoding="utf-8")
    proj = read_sources_yml(p)
    assert set(proj.models) == {"customer", "region", "stg_raw_events"}


def test_duplicate_table_name_warns_and_keeps_first():
    data = {
        "sources": [
            {"name": "a", "tables": [{"name": "customer", "columns": []}]},
            {"name": "b", "tables": [{"name": "customer", "columns": []}]},
        ]
    }
    proj = read_sources_dict(data)
    assert list(proj.models) == ["customer"]
    assert any("duplicate table name" in w for w in proj.warnings)


# --- the engine on the live source (front-door parity) -----------------------


def test_surrogate_key_stripped():
    result = _reverse()
    customer = next(e for e in result.model.logical_entities.values() if e.name == "customer")
    attrs = {a.name for a in customer.attributes}
    assert "customer_sk" not in attrs  # stripped as a surrogate, same as DDL/manifest
    assert {"customer_id", "region_id", "legal_name"} <= attrs


def test_staging_excluded():
    result = _reverse()
    names = {e.name for e in result.model.logical_entities.values()}
    assert "stg_raw_events" not in names
    assert "stg_raw_events" in result.excluded


def test_nullability_inferred_from_source():
    result = _reverse()
    customer = next(e for e in result.model.logical_entities.values() if e.name == "customer")
    by_name = {a.name: a for a in customer.attributes}
    assert by_name["customer_id"].nullable is False  # reported not-null
    assert by_name["legal_name"].nullable is False  # reported not-null
    assert by_name["region_id"].nullable is True  # not reported -> default nullable


def test_fk_inferred_as_proposal():
    # region_id matches region's PK-ish column by name+type -> a relationship proposal
    # (medium confidence). The live path relies on this inference since generate_source
    # declares no FKs.
    result = _reverse()
    rel_proposals = [
        p for p in result.proposals if p.kind == "relationship"
    ]
    # at least the region_id -> region edge should be proposed
    assert any("region" in (p.subject or "").lower() for p in rel_proposals), (
        f"expected a region relationship proposal, got: "
        f"{[(p.kind, p.subject) for p in result.proposals]}"
    )
