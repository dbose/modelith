"""PK-nullability warning (MDL-W115) and FK domain-match error (MDL-E114) — issue #6.

Modelith already models primary keys as `pk` KeyGroups and foreign keys as
Relationships (from/to ends with paired attribute lists). These two checks close the
validation gaps the issue asked for, on that existing model."""

from __future__ import annotations

from mdl_core.ir import (
    Attribute,
    Domain,
    KeyGroup,
    LogicalEntity,
    Model,
    ProjectConfig,
    Relationship,
    RelationshipEnd,
)
from mdl_core.validate import validate


def _codes(m, severity):
    return sorted({d.code for d in validate(m).items if d.severity.value == severity})


# --- MDL-W115: a primary-key column must not be nullable --------------------------


def test_nullable_pk_member_warns():
    m = Model(ProjectConfig(name="x"))
    m.add(
        LogicalEntity(
            id="01LE",
            name="cust",
            attributes=[Attribute(id="01A1", name="cust_id", nullable=True)],
        )
    )
    m.add(KeyGroup(id="01KG", entity="01LE", name="pk", type="pk", members=["01A1"]))
    warns = _codes(m, "warning")
    assert "MDL-W115" in warns
    # it is a warning, not an error — legacy models must not suddenly fail
    assert "MDL-W115" not in _codes(m, "error")


def test_non_nullable_pk_member_is_clean():
    m = Model(ProjectConfig(name="x"))
    m.add(
        LogicalEntity(
            id="01LE",
            name="cust",
            attributes=[Attribute(id="01A1", name="cust_id", nullable=False)],
        )
    )
    m.add(KeyGroup(id="01KG", entity="01LE", name="pk", type="pk", members=["01A1"]))
    assert "MDL-W115" not in _codes(m, "warning")


def test_nullable_non_pk_key_does_not_warn():
    """Only PK members carry the non-null contract; an alternate/unique key may be
    nullable (a nullable unique column is legal SQL)."""
    m = Model(ProjectConfig(name="x"))
    m.add(
        LogicalEntity(
            id="01LE",
            name="cust",
            attributes=[
                Attribute(id="01A1", name="cust_id", nullable=False),
                Attribute(id="01A2", name="email", nullable=True),
            ],
        )
    )
    m.add(KeyGroup(id="01KG1", entity="01LE", name="pk", type="pk", members=["01A1"]))
    m.add(KeyGroup(id="01KG2", entity="01LE", name="uq_email", type="unique", members=["01A2"]))
    assert "MDL-W115" not in _codes(m, "warning")


def test_composite_pk_flags_only_the_nullable_member():
    m = Model(ProjectConfig(name="x"))
    m.add(
        LogicalEntity(
            id="01LE",
            name="line",
            attributes=[
                Attribute(id="01A1", name="order_id", nullable=False),
                Attribute(id="01A2", name="line_no", nullable=True),
            ],
        )
    )
    m.add(KeyGroup(id="01KG", entity="01LE", name="pk", type="pk", members=["01A1", "01A2"]))
    diags = [d for d in validate(m).items if d.code == "MDL-W115"]
    assert len(diags) == 1
    assert diags[0].path == "01A2"  # only the nullable member


# --- MDL-E114: a foreign key must be domain-compatible with its target ------------


def _fk_model(from_domain, to_domain):
    m = Model(ProjectConfig(name="x"))
    m.add(Domain(id="01D1", name="integer", base_type="integer"))
    m.add(Domain(id="01D2", name="text", base_type="string"))
    m.add(
        LogicalEntity(
            id="01ADDR",
            name="address",
            attributes=[Attribute(id="01AID", name="address_id", domain=to_domain, nullable=False)],
        )
    )
    m.add(
        LogicalEntity(
            id="01CUST",
            name="customer",
            attributes=[
                Attribute(id="01CID", name="customer_id", domain="integer", nullable=False),
                Attribute(id="01FK", name="address_id", domain=from_domain, nullable=True),
            ],
        )
    )
    m.add(
        Relationship(
            id="01REL",
            name="customer_address",
            **{"from": RelationshipEnd(entity="01CUST", attributes=["01FK"])},
            to=RelationshipEnd(entity="01ADDR", attributes=["01AID"]),
        )
    )
    return m


def test_fk_domain_match_is_clean():
    assert "MDL-E114" not in _codes(_fk_model("integer", "integer"), "error")


def test_fk_domain_mismatch_errors():
    codes = _codes(_fk_model("text", "integer"), "error")
    assert "MDL-E114" in codes


def test_fk_with_missing_domain_is_not_flagged():
    """An absent domain on either side is a different concern; the FK check only
    compares when both sides declare one."""
    assert "MDL-E114" not in _codes(_fk_model(None, "integer"), "error")


def test_fk_with_no_attribute_mapping_is_not_flagged():
    """A relationship whose ends are not column-mapped is a modelling choice, not a
    type error — nothing to compare."""
    m = Model(ProjectConfig(name="x"))
    m.add(Domain(id="01D1", name="integer", base_type="integer"))
    m.add(LogicalEntity(id="01A", name="a", attributes=[Attribute(id="01AA", name="x", domain="integer")]))
    m.add(LogicalEntity(id="01B", name="b", attributes=[Attribute(id="01BB", name="y", domain="integer")]))
    m.add(
        Relationship(
            id="01REL",
            name="a_b",
            **{"from": RelationshipEnd(entity="01A", attributes=[])},
            to=RelationshipEnd(entity="01B", attributes=[]),
        )
    )
    assert "MDL-E114" not in _codes(m, "error")
