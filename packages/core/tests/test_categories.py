"""Subtype/supertype (category) IR + validation (P3)."""

from __future__ import annotations

from mdl_core.ir import Attribute, Category, LogicalEntity, Model, ProjectConfig
from mdl_core.validate import validate


def _base():
    m = Model(ProjectConfig(name="x"))
    disc = Attribute(id="01DISC", name="party_type", domain="string")
    m.add(LogicalEntity(id="01SUP", name="party",
                        attributes=[Attribute(id="01PID", name="party_id"), disc]))
    m.add(LogicalEntity(id="01PER", name="person", attributes=[Attribute(id="01DOB", name="dob")]))
    m.add(LogicalEntity(id="01ORG", name="org", attributes=[Attribute(id="01REG", name="reg_no")]))
    return m


def _errors(m):
    return sorted({d.code for d in validate(m).items if d.severity.value == "error"})


def test_valid_category_passes():
    m = _base()
    m.add(Category(id="01CAT", name="party_cat", supertype="01SUP",
                   subtypes=["01PER", "01ORG"], discriminator="01DISC"))
    assert _errors(m) == []


def test_discriminator_must_be_supertype_attr():
    m = _base()
    # 01DOB belongs to person (a subtype), not the supertype
    m.add(Category(id="01CAT", name="c", supertype="01SUP",
                   subtypes=["01PER"], discriminator="01DOB"))
    assert "MDL-E111" in _errors(m)


def test_supertype_cannot_be_its_own_subtype():
    m = _base()
    m.add(Category(id="01CAT", name="c", supertype="01SUP",
                   subtypes=["01SUP"], discriminator="01DISC"))
    assert "MDL-E112" in _errors(m)


def test_dangling_refs_reported():
    m = _base()
    m.add(Category(id="01CAT", name="c", supertype="01NOPE",
                   subtypes=["01ALSO_NOPE"], discriminator="01DISC"))
    assert "MDL-E110" in _errors(m)


def test_single_table_without_discriminator_warns():
    m = _base()
    m.add(Category(id="01CAT", name="c", supertype="01SUP", subtypes=["01PER"],
                   materialization="single_table"))
    codes = {d.code for d in validate(m).items}
    assert "MDL-W113" in codes
