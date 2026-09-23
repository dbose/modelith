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
from mdl_reverse.schema_reader import (
    apply_constraints,
    constraint_macro_sql,
    constraints_to_projection,
    parse_constraints_output,
    read_sources_dict,
    read_sources_yml,
)

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
    # (medium confidence). WITHOUT declared constraints the live path relies on this
    # inference, so it's a proposal a human triages — not a materialised relationship.
    result = _reverse()
    rel_proposals = [p for p in result.proposals if p.kind == "relationship"]
    assert any("region" in (p.subject or "").lower() for p in rel_proposals), (
        f"expected a region relationship proposal, got: "
        f"{[(p.kind, p.subject) for p in result.proposals]}"
    )
    # and because it's only a name/type guess (medium), it is NOT auto-materialised
    edges = {(r.from_.entity, r.to.entity) for r in result.model.relationships.values()}
    ent = {e.id: e.name for e in result.model.logical_entities.values()}
    named = {(ent.get(a), ent.get(b)) for a, b in edges}
    assert ("customer", "region") not in named  # inference alone doesn't materialise it


# --- the constraint overlay (erwin parity) -----------------------------------

# A mdl_get_constraints payload: real declared PK + FK + nullability, as the dispatched
# dbt macro returns from the warehouse's constraint catalog.
_CONSTRAINTS = {
    "customer": {
        "primary_key": ["customer_id"],
        "foreign_keys": [
            {"columns": ["region_id"], "ref_table": "region", "ref_columns": ["region_id"]}
        ],
        "columns": {"region_id": {"nullable": False}},
    },
    "region": {
        "primary_key": ["region_id"],
        "columns": {},
    },
}


def _reverse_with_constraints():
    proj = apply_constraints(_proj(), _CONSTRAINTS)
    return reverse(
        proj,
        project_name="warehouse",
        target=TARGET,
        ledger=DecisionLedger(),
        interactive=False,
        naming=None,
    )


def test_overlay_sets_pk_and_nullable_on_projection():
    proj = apply_constraints(_proj(), _CONSTRAINTS)
    cust = proj.models["customer"]
    assert cust.columns["customer_id"].meta.get("pk") is True
    # a PK column is forced not-null even though the columns block was silent
    assert cust.columns["customer_id"].meta.get("nullable") is False
    # the FK column's declared nullability is applied
    assert cust.columns["region_id"].meta.get("nullable") is False
    # the FK becomes a HIGH-confidence relationship test (the DDL path's contract)
    assert ("region_id", "region") in cust.relationship_tests


def test_declared_fk_is_materialised_not_just_proposed():
    # THE erwin-parity assertion: a declared FK from the warehouse constraint catalog is
    # auto-accepted and materialised as a real relationship on the model — not left as a
    # medium-confidence proposal like the inference path.
    result = _reverse_with_constraints()
    edges = {(r.from_.entity, r.to.entity) for r in result.model.relationships.values()}
    ent = {e.id: e.name for e in result.model.logical_entities.values()}
    named = {(ent.get(a), ent.get(b)) for a, b in edges}
    assert ("customer", "region") in named, (
        f"declared FK should be a materialised relationship; got {named}"
    )


def test_empty_constraints_is_a_noop():
    # No declared constraints (common on Snowflake/BigQuery) -> overlay changes nothing,
    # the engine falls back to inference (the graceful-degradation path).
    before = _proj()
    after = apply_constraints(_proj(), {})
    assert before.models["customer"].relationship_tests == after.models["customer"].relationship_tests
    assert "pk" not in after.models["customer"].columns["customer_id"].meta


def test_dangling_fk_skipped():
    # An FK to a table not in the projection can't become an edge -> skipped, not crashed.
    proj = apply_constraints(
        _proj(),
        {"customer": {"foreign_keys": [
            {"columns": ["region_id"], "ref_table": "not_here", "ref_columns": ["x"]}
        ]}},
    )
    assert ("region_id", "not_here") not in proj.models["customer"].relationship_tests


def test_pk_from_constraint_becomes_business_key():
    # A declared PK is taken authoritatively as the business key (meta["pk"] path),
    # regardless of name heuristics.
    result = _reverse_with_constraints()
    customer = next(e for e in result.model.logical_entities.values() if e.name == "customer")
    bks = {a.name for a in customer.attributes if a.role == "business_key"}
    assert "customer_id" in bks


def test_composite_pk_all_columns_are_business_keys():
    # A multi-column declared PK -> every member is a business key.
    proj = apply_constraints(
        _proj(),
        {"customer": {"primary_key": ["customer_id", "region_id"]}, "region": {}},
    )
    result = reverse(
        proj, project_name="warehouse", target=TARGET,
        ledger=DecisionLedger(), interactive=False, naming=None,
    )
    customer = next(e for e in result.model.logical_entities.values() if e.name == "customer")
    bks = {a.name for a in customer.attributes if a.role == "business_key"}
    assert {"customer_id", "region_id"} <= bks


# --- the dbt macro resource --------------------------------------------------


def test_constraint_macro_ships_and_dispatches():
    sql = constraint_macro_sql()
    # the wrapper dispatches on the running project's namespace (the macro is copied into
    # the user's dbt project, so the package name isn't known at author time)
    assert "adapter.dispatch('mdl_get_constraints', project_name)" in sql
    for branch in (
        "default__mdl_get_constraints",
        "duckdb__mdl_get_constraints",
        "snowflake__mdl_get_constraints",
        "redshift__mdl_get_constraints",
    ):
        assert branch in sql, f"missing macro branch {branch}"
    # it prints the sentinel the parser keys off
    assert "MDL_CONSTRAINTS_JSON" in sql
    # the DuckDB branch recovers FK targets from constraint_text (portable across DuckDB
    # versions) rather than SELECTing the referenced_table column (absent on older DuckDB
    # shipped with older dbt-duckdb). Guard against a regression back to the fragile column.
    assert "_mdl_parse_fk_ref" in sql
    assert "constraint_text\n    from duckdb_constraints()" in sql
    # referenced_table must not be SELECTed (mentions in explanatory comments are fine)
    assert "referenced_table\n" not in sql  # i.e. not a column in a select list


def test_parse_constraints_output_finds_the_json_line():
    stdout = (
        "12:00:00  Running with dbt=1.7.4\n"
        'MDL_CONSTRAINTS_JSON {"customer": {"primary_key": ["customer_id"], '
        '"unique": [], "foreign_keys": [], "columns": {}}}\n'
        "12:00:01  Done.\n"
    )
    parsed = parse_constraints_output(stdout)
    assert parsed["customer"]["primary_key"] == ["customer_id"]


def test_parse_constraints_output_absent_sentinel_is_empty():
    assert parse_constraints_output("12:00:00 Running with dbt\nno payload here\n") == {}


def test_parse_constraints_output_malformed_json_is_empty():
    assert parse_constraints_output("MDL_CONSTRAINTS_JSON {not valid json\n") == {}


# --- constraints_to_projection (the live front door, one call) ---------------

# The macro now returns columns+types+keys in one payload, so this builds the WHOLE
# projection — no generate_source spine needed.
_FULL_CONSTRAINTS = {
    "customer": {
        "columns": {
            "customer_id": {"type": "BIGINT", "nullable": False},
            "customer_sk": {"type": "VARCHAR", "nullable": False},
            "region_id": {"type": "BIGINT", "nullable": True},
            "legal_name": {"type": "VARCHAR", "nullable": False},
        },
        "primary_key": ["customer_id"],
        "foreign_keys": [
            {"columns": ["region_id"], "ref_table": "region", "ref_columns": ["region_id"]}
        ],
    },
    "region": {
        "columns": {
            "region_id": {"type": "BIGINT", "nullable": False},
            "region_name": {"type": "VARCHAR", "nullable": True},
        },
        "primary_key": ["region_id"],
    },
}


def test_constraints_to_projection_builds_full_spine():
    proj = constraints_to_projection(_FULL_CONSTRAINTS)
    assert set(proj.models) == {"customer", "region"}
    cust = proj.models["customer"]
    assert cust.columns["customer_id"].data_type == "BIGINT"
    assert cust.columns["customer_id"].meta["pk"] is True
    assert cust.columns["customer_id"].meta["nullable"] is False
    assert cust.columns["region_id"].meta["nullable"] is True
    assert ("region_id", "region") in cust.relationship_tests


def test_constraints_to_projection_reverses_to_erwin_grade_model():
    proj = constraints_to_projection(_FULL_CONSTRAINTS)
    result = reverse(
        proj, project_name="warehouse", target=TARGET,
        ledger=DecisionLedger(), interactive=False, naming=None,
    )
    # surrogate stripped, PK -> business key, declared FK -> materialised relationship
    customer = next(e for e in result.model.logical_entities.values() if e.name == "customer")
    attrs = {a.name for a in customer.attributes}
    assert "customer_sk" not in attrs
    assert {a.name for a in customer.attributes if a.role == "business_key"} == {"customer_id"}
    ent = {e.id: e.name for e in result.model.logical_entities.values()}
    edges = {(ent.get(a), ent.get(b)) for r in result.model.relationships.values()
             for a, b in [(r.from_.entity, r.to.entity)]}
    assert ("customer", "region") in edges


def test_constraints_to_projection_empty_is_empty():
    assert constraints_to_projection({}).models == {}
