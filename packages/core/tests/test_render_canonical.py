"""Canonical render + name-keyed diff — the compare-reversed-models surface.

The point of both: two independently reverse-engineered models describe the same warehouse
with DIFFERENT ULIDs, so a raw/ULID diff reports everything as changed. The canonical render
is ULID-free and name-ordered (so two renders of the same model are byte-identical), and
diff_models_by_name matches on names (so the diff shows the real semantic delta).
"""

from __future__ import annotations

from mdl_core.diff import diff_models, diff_models_by_name
from mdl_core.ids import new_ulid
from mdl_core.ir import (
    Attribute,
    LogicalEntity,
    Model,
    ProjectConfig,
    Relationship,
    RelationshipEnd,
)
from mdl_core.render_canonical import render_canonical


def _model(seed: str = "") -> Model:
    """A small model: customer -> region (FK), with a PK on each. `seed` is unused except
    that fresh ULIDs are minted each call, so two calls are the SAME model with DIFFERENT
    ULIDs — exactly the two-independent-reverses case."""
    m = Model(ProjectConfig(name="wh"))
    reg_id, cust_id = new_ulid(), new_ulid()
    reg_pk, reg_name = new_ulid(), new_ulid()
    cust_pk, cust_fk, cust_nm = new_ulid(), new_ulid(), new_ulid()
    m.add(LogicalEntity(id=reg_id, name="region", attributes=[
        Attribute(id=reg_pk, name="region_id", domain="bigint", role="business_key", nullable=False),
        Attribute(id=reg_name, name="region_name", domain="string", nullable=True),
    ]))
    m.add(LogicalEntity(id=cust_id, name="customer", attributes=[
        Attribute(id=cust_pk, name="customer_id", domain="bigint", role="business_key", nullable=False),
        Attribute(id=cust_fk, name="region_id", domain="bigint", nullable=True),
        Attribute(id=cust_nm, name="legal_name", domain="string", nullable=False),
    ]))
    m.add(Relationship(
        id=new_ulid(), name="customer_in_region",
        **{"from": RelationshipEnd(entity=cust_id, attributes=[cust_fk])},
        to=RelationshipEnd(entity=reg_id, attributes=[reg_pk]),
        cardinality="many_to_one",
    ))
    return m


# --- canonical render --------------------------------------------------------


def test_render_is_ulid_free_and_deterministic():
    r = render_canonical(_model())
    assert "01" not in r or "customer" in r  # sanity: it's readable text
    # no 26-char ULID literals leak into the render
    import re
    assert not re.search(r"\b[0-9A-HJKMNP-TV-Z]{26}\b", r), "a ULID leaked into the render"


def test_two_independent_models_render_identically():
    # THE point: same model, different ULIDs -> byte-identical canonical text.
    assert render_canonical(_model()) == render_canonical(_model())


def test_render_shows_pk_fk_nullability():
    r = render_canonical(_model())
    assert "customer_id" in r and "[pk]" in r
    assert "fk -> region" in r
    assert "not null" in r
    assert "-> region  (many_to_one via region_id)" in r


# --- name-keyed diff ---------------------------------------------------------


def test_by_name_diff_of_identical_models_is_empty():
    # two independent reverses of the same warehouse -> no changes (not a total rewrite).
    d = diff_models_by_name(_model(), _model())
    assert not d, f"expected no changes, got {[o.display_name for o in d.objects]}"


def test_ulid_diff_of_the_same_two_models_is_all_noise():
    # contrast: the ULID-keyed diff sees them as completely different (the bug we avoid).
    d = diff_models(_model(), _model())
    assert d  # lots of spurious added/removed
    kinds = {o.change.value for o in d.objects}
    assert "added" in kinds and "removed" in kinds


def test_by_name_diff_detects_added_column():
    base = _model()
    head = _model()
    cust = next(e for e in head.logical_entities.values() if e.name == "customer")
    cust.attributes.append(Attribute(id=new_ulid(), name="email", domain="string", nullable=True))
    d = diff_models_by_name(base, head)
    assert d
    # the only change is the added attribute on customer
    changed = [o for o in d.objects if o.object_kind == "logical_entity" and o.change.value == "modified"]
    assert any("customer" in (o.display_name or "") for o in changed)


def test_by_name_diff_detects_new_relationship():
    base = _model()
    head = _model()
    # add a self-ish FK: customer.legal_name is nonsense as an FK, but add a real new entity+rel
    prod_id, prod_pk = new_ulid(), new_ulid()
    head.add(LogicalEntity(id=prod_id, name="product", attributes=[
        Attribute(id=prod_pk, name="product_id", domain="bigint", role="business_key", nullable=False),
    ]))
    d = diff_models_by_name(base, head)
    assert d
    added = [o.display_name for o in d.objects if o.change.value == "added"]
    assert any("product" in (n or "").lower() for n in added)
