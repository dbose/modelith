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


def test_ddl_dedupes_a_duplicated_pk_member():
    """A `pk` key group with the same attribute listed twice (seen in real erwin-imported
    models) must NOT emit `PRIMARY KEY (col, col)` — erwin's DDL importer rejects that with
    'Cannot add the same Attribute to Key'. The column appears exactly once."""
    from mdl_core.ids import new_ulid
    from mdl_core.ir import (
        Attribute,
        ConceptualEntity,
        KeyGroup,
        LogicalEntity,
        Model,
        ProjectConfig,
    )

    m = Model(ProjectConfig(name="dup"))
    ce = ConceptualEntity(id=new_ulid(), name="Account")
    m.add(ce)
    acc_id = Attribute(id=new_ulid(), name="account_id", role="business_key", nullable=False,
                       domain="integer")
    le = LogicalEntity(id=new_ulid(), name="account", realises=ce.id, attributes=[acc_id])
    m.add(le)
    # the bug condition: the PK key group lists account_id TWICE
    m.add(KeyGroup(id=new_ulid(), entity=le.id, name="pk_account", type="pk",
                   members=[acc_id.id, acc_id.id]))

    ddl = emit_sql_ddl(m, dialect="postgres")
    pk_line = next(ln for ln in ddl.splitlines() if "PRIMARY KEY" in ln)
    assert pk_line.count("account_id") == 1, pk_line


def test_ddl_fk_defaults_to_parent_pk_when_unspecified():
    """When a relationship doesn't name the parent columns, the FK REFERENCES the parent's PK
    (not nothing), so erwin can actually create the key instead of dropping it with 'there is
    not a primary or unique key in table ... that contains column'."""
    from mdl_core.ids import new_ulid
    from mdl_core.ir import (
        Attribute,
        ConceptualEntity,
        KeyGroup,
        LogicalEntity,
        Model,
        ProjectConfig,
        Relationship,
        RelationshipEnd,
    )

    m = Model(ProjectConfig(name="fk"))
    pce = ConceptualEntity(id=new_ulid(), name="Account")
    m.add(pce)
    pk_attr = Attribute(id=new_ulid(), name="account_id", role="business_key", nullable=False,
                        domain="integer")
    parent = LogicalEntity(id=new_ulid(), name="account", realises=pce.id, attributes=[pk_attr])
    m.add(parent)
    m.add(KeyGroup(id=new_ulid(), entity=parent.id, name="pk_account", type="pk",
                   members=[pk_attr.id]))
    cce = ConceptualEntity(id=new_ulid(), name="Trade")
    m.add(cce)
    c_pk = Attribute(id=new_ulid(), name="trade_id", role="business_key", nullable=False,
                     domain="integer")
    c_fk = Attribute(id=new_ulid(), name="account_id", role="attribute", nullable=False,
                     domain="integer")
    child = LogicalEntity(id=new_ulid(), name="trade", realises=cce.id, attributes=[c_pk, c_fk])
    m.add(child)
    # relationship names the CHILD FK column but NOT the parent column
    m.add(Relationship(
        id=new_ulid(), name="trade_for_account",
        **{"from": RelationshipEnd(entity=child.id, attributes=[c_fk.id])},
        to=RelationshipEnd(entity=parent.id, attributes=[]),
        cardinality="many_to_one"))

    ddl = emit_sql_ddl(m, dialect="postgres")
    fk_line = next(ln for ln in ddl.splitlines() if "FOREIGN KEY" in ln)
    assert "REFERENCES account (account_id)" in fk_line, fk_line


def test_sql_ddl_scopes_to_a_subject_area_and_drops_cross_area_fk():
    """A scoped DDL export emits only the subject area's tables, keeps within-area FKs, and
    DROPS cross-area FKs with NO stub table for the out-of-area neighbour (it already exists in
    the target model; the FK is re-established there by hand)."""
    from mdl_core.ids import new_ulid
    from mdl_core.ir import (
        Attribute,
        ConceptualEntity,
        KeyGroup,
        LogicalEntity,
        Model,
        ProjectConfig,
        Relationship,
        RelationshipEnd,
        SubjectArea,
    )

    m = Model(ProjectConfig(name="mart"))
    # out-of-area neighbour
    acc_ce = ConceptualEntity(id=new_ulid(), name="Account")
    m.add(acc_ce)
    acc_pk = Attribute(id=new_ulid(), name="account_id", role="business_key", nullable=False,
                       domain="integer")
    acc = LogicalEntity(id=new_ulid(), name="account", realises=acc_ce.id, attributes=[acc_pk])
    m.add(acc)
    m.add(KeyGroup(id=new_ulid(), entity=acc.id, name="pk_acc", type="pk", members=[acc_pk.id]))
    m.add(SubjectArea(id=new_ulid(), name="Core", members=[acc_ce.id]))
    # in-area entities: trade (pk) and trade_leg (within-area FK to trade), plus a cross-area
    # FK trade -> account
    t_ce = ConceptualEntity(id=new_ulid(), name="Trade")
    m.add(t_ce)
    t_pk = Attribute(id=new_ulid(), name="trade_id", role="business_key", nullable=False,
                     domain="integer")
    t_fk = Attribute(id=new_ulid(), name="account_id", role="attribute", nullable=False,
                     domain="integer")
    trade = LogicalEntity(id=new_ulid(), name="trade", realises=t_ce.id, attributes=[t_pk, t_fk])
    m.add(trade)
    m.add(KeyGroup(id=new_ulid(), entity=trade.id, name="pk_trade", type="pk",
                   members=[t_pk.id]))
    leg_ce = ConceptualEntity(id=new_ulid(), name="TradeLeg")
    m.add(leg_ce)
    leg_pk = Attribute(id=new_ulid(), name="leg_id", role="business_key", nullable=False,
                       domain="integer")
    leg_fk = Attribute(id=new_ulid(), name="trade_id", role="attribute", nullable=False,
                       domain="integer")
    leg = LogicalEntity(id=new_ulid(), name="trade_leg", realises=leg_ce.id,
                        attributes=[leg_pk, leg_fk])
    m.add(leg)
    m.add(KeyGroup(id=new_ulid(), entity=leg.id, name="pk_leg", type="pk", members=[leg_pk.id]))
    m.add(SubjectArea(id=new_ulid(), name="Trading", members=[t_ce.id, leg_ce.id]))
    # within-area FK: trade_leg -> trade
    m.add(Relationship(
        id=new_ulid(), name="leg_of_trade",
        **{"from": RelationshipEnd(entity=leg.id, attributes=[leg_fk.id])},
        to=RelationshipEnd(entity=trade.id, attributes=[t_pk.id]), cardinality="many_to_one"))
    # cross-area FK: trade -> account (Core)
    m.add(Relationship(
        id=new_ulid(), name="trade_for_account",
        **{"from": RelationshipEnd(entity=trade.id, attributes=[t_fk.id])},
        to=RelationshipEnd(entity=acc.id, attributes=[acc_pk.id]), cardinality="many_to_one"))

    ddl = emit_sql_ddl(m, dialect="postgres", subject_area="Trading")
    # only the Trading tables are present; the Core neighbour is NOT emitted (no stub)
    assert "CREATE TABLE trade (" in ddl
    assert "CREATE TABLE trade_leg (" in ddl
    assert "CREATE TABLE account" not in ddl
    # the within-area FK survives; the cross-area FK CONSTRAINT to account is dropped (the
    # account_id COLUMN on trade stays as an ordinary attribute).
    assert "REFERENCES trade (" in ddl
    assert "REFERENCES account" not in ddl


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
