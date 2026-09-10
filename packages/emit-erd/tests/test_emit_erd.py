"""ER interchange export + import (issue: Model-tab import/export).

Round-trip is the strongest assertion: export the fixture model, parse it back, apply
the resulting commands to an empty model, and confirm entities/attributes/keys/
relationships reconstruct."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core" / "tests"))
from mdl_emit_erd import emit_csv, emit_dbml, emit_mermaid, emit_sql_ddl  # noqa: E402
from mdl_emit_erd.imports import parse_json_schema, parse_mermaid, parse_sql_ddl  # noqa: E402
from mdl_emit_erd.imports.model import to_commands  # noqa: E402

from mdl_core.commands import apply_command  # noqa: E402
from mdl_core.repo import ModelRepo  # noqa: E402

from model_builders import write_model  # noqa: E402


@pytest.fixture
def model(tmp_path):
    write_model(tmp_path)
    return ModelRepo.load(tmp_path).model


def _empty_project() -> Path:
    d = Path(tempfile.mkdtemp()) / "m"
    d.mkdir()
    (d / "mdl-project.yaml").write_text("name: imported\ndbt_target: duckdb\n")
    return d


# --- export ----------------------------------------------------------------------


def test_sql_ddl_has_tables_pk_and_fk(model):
    ddl = emit_sql_ddl(model, dialect="postgres")
    assert "CREATE TABLE" in ddl
    assert "PRIMARY KEY" in ddl
    # the fixture has a trade -> counterparty relationship
    assert "FOREIGN KEY" in ddl
    assert "REFERENCES" in ddl


def test_mermaid_is_an_erdiagram_with_markers(model):
    mer = emit_mermaid(model)
    assert mer.startswith("erDiagram")
    assert " PK" in mer
    assert "||--" in mer  # a relationship line


def test_dbml_has_tables_and_refs(model):
    d = emit_dbml(model)
    assert "Table " in d
    assert "Ref:" in d
    assert "—" not in d  # no em-dash in output


def test_csv_header_and_rows(model):
    csv = emit_csv(model)
    lines = csv.splitlines()
    assert lines[0].startswith("entity,attribute,base_type")
    assert len(lines) > 1


# --- import + round-trip ----------------------------------------------------------


def _apply(cmds, d: Path):
    for c in cmds:
        apply_command(d, c["op"], c["payload"])


def test_sql_ddl_roundtrips(model):
    ddl = emit_sql_ddl(model, dialect="postgres")
    imp = parse_sql_ddl(ddl, dialect="postgres")
    assert not imp.warnings, imp.warnings
    d = _empty_project()
    _apply(to_commands(imp), d)
    out = ModelRepo.load(d).model
    assert len(out.logical_entities) == len(model.logical_entities)
    assert len(out.relationships) == len(model.relationships)
    # PK survived as a pk KeyGroup
    assert any(k.type == "pk" for k in out.key_groups.values())
    # the relationship kept its column anchoring on both ends
    r = next(iter(out.relationships.values()))
    assert r.from_.attributes and r.to.attributes


def test_mermaid_roundtrips_structurally(model):
    im = parse_mermaid(emit_mermaid(model))
    assert im.tables
    # every relationship line recovered as a FK
    assert sum(len(t.foreign_keys) for t in im.tables) == len(model.relationships)
    d = _empty_project()
    _apply(to_commands(im), d)
    out = ModelRepo.load(d).model
    assert len(out.logical_entities) == len(model.logical_entities)


def test_ddl_composite_pk_and_inline_ref():
    ddl = """
    CREATE TABLE customer (customer_id integer PRIMARY KEY, email varchar UNIQUE);
    CREATE TABLE line (
      order_id bigint,
      line_no int,
      customer_id integer REFERENCES customer(customer_id),
      PRIMARY KEY (order_id, line_no)
    );
    """
    imp = parse_sql_ddl(ddl, dialect="postgres")
    line = next(t for t in imp.tables if t.name == "line")
    assert [c.name for c in line.columns if c.pk] == ["order_id", "line_no"]  # composite
    assert line.foreign_keys[0].ref_table == "customer"
    d = _empty_project()
    _apply(to_commands(imp), d)
    out = ModelRepo.load(d).model
    pk = next(k for k in out.key_groups.values() if k.type == "pk" and len(k.members) == 2)
    assert len(pk.members) == 2  # composite PK materialised


def test_json_schema_defs_and_ref_fk():
    js = """
    {"$defs": {
      "Customer": {"type": "object", "required": ["customer_id"], "properties": {
        "customer_id": {"type": "integer"},
        "address": {"$ref": "#/$defs/Address"}}},
      "Address": {"type": "object", "properties": {"city": {"type": "string"}}}
    }}
    """
    imp = parse_json_schema(js)
    assert {t.name for t in imp.tables} == {"Customer", "Address"}
    cust = next(t for t in imp.tables if t.name == "Customer")
    assert cust.foreign_keys and cust.foreign_keys[0].ref_table == "Address"
    # customer_id is required -> not nullable
    cid = next(c for c in cust.columns if c.name == "customer_id")
    assert cid.nullable is False


def test_bad_sql_is_a_warning_not_a_crash():
    imp = parse_sql_ddl("this is not sql at all ;;;")
    # either no tables, or a warning — never an exception
    assert imp.tables == [] or imp.warnings


def test_ddl_import_via_cli(tmp_path):
    """The CLI import command materialises a model from a DDL file."""
    (tmp_path / "schema.sql").write_text(
        "CREATE TABLE product (product_id integer PRIMARY KEY, name varchar NOT NULL);"
    )
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "mdl-project.yaml").write_text("name: p\ndbt_target: duckdb\n")
    r = subprocess.run(
        [sys.executable, "-m", "mdl_cli.main", "import", "sql",
         str(tmp_path / "schema.sql"), "-m", str(proj)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    out = ModelRepo.load(proj).model
    assert any(e.name == "product" for e in out.logical_entities.values())
