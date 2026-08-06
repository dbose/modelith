"""Category materialization strategies emit correct dbt SQL + contracts (P3)."""

from __future__ import annotations

from mdl_core.ir import Attribute, Category, LogicalEntity, Model, ProjectConfig
from mdl_emit_dbt.emitter import DbtEmitter


def _model(strategy):
    m = Model(ProjectConfig(name="t", dbt_target="duckdb_dev"))
    disc = Attribute(id="01DISC", name="party_type", domain="string", nullable=False)
    m.add(
        LogicalEntity(
            id="01SUP", name="party",
            attributes=[
                Attribute(id="01PID", name="party_id", domain="bigint", nullable=False,
                          role="business_key"),
                disc,
            ],
        )
    )
    m.add(LogicalEntity(id="01PER", name="person",
                        attributes=[Attribute(id="01DOB", name="dob", domain="date")]))
    m.add(Category(id="01CAT", name="party_cat", supertype="01SUP", subtypes=["01PER"],
                   discriminator="01DISC", materialization=strategy))
    return m


def _files(strategy, tmp_path):
    return DbtEmitter(_model(strategy), "duckdb_dev").generate(tmp_path, write=False).files


def test_single_table_subtype_is_filtered_view(tmp_path):
    f = _files("single_table", tmp_path)
    sql = f["models/person.sql"]
    assert "ref('party')" in sql
    assert "where party_type = 'person'" in sql
    # contract lists inherited supertype columns, not the subtype's own dob
    schema = f["models/schema.yml"]
    person_block = schema.split("name: person", 1)[1]
    assert "party_id" in person_block and "party_type" in person_block


def test_table_per_subtype_joins_supertype(tmp_path):
    f = _files("table_per_subtype", tmp_path)
    sql = f["models/person.sql"]
    assert "join super on own.party_id = super.party_id" in sql
    assert "ref('stg_person')" in sql
    assert "super.party_id" in sql and "own.dob" in sql
    # contract lists inherited + own columns
    schema = f["models/schema.yml"]
    person_block = schema.split("name: person", 1)[1]
    assert "party_id" in person_block and "dob" in person_block


def test_supertype_model_unchanged(tmp_path):
    # the supertype emits normally (plain projection over its staging)
    f = _files("single_table", tmp_path)
    sql = f["models/party.sql"]
    assert "ref('stg_party')" in sql
