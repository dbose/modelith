"""KeyGroup IR + validation (P0-pre-a)."""

from __future__ import annotations

from mdl_core.ir import Attribute, KeyGroup, LogicalEntity, Model, ProjectConfig
from mdl_core.validate import validate


def _model():
    m = Model(ProjectConfig(name="x"))
    m.add(
        LogicalEntity(
            id="01LE",
            name="cust",
            attributes=[
                Attribute(id="01A1", name="cust_id"),
                Attribute(id="01A2", name="region"),
            ],
        )
    )
    return m


def _errors(m):
    return sorted({d.code for d in validate(m).items if d.severity.value == "error"})


def test_valid_key_group_passes():
    m = _model()
    m.add(KeyGroup(id="01KG", entity="01LE", name="pk_cust", type="pk", members=["01A1"]))
    assert _errors(m) == []


def test_unknown_member_errors():
    m = _model()
    m.add(KeyGroup(id="01KG", entity="01LE", name="pk", type="pk", members=["01UNKNOWN"]))
    # dangling member ULID → referential error MDL-E105
    assert "MDL-E105" in _errors(m)


def test_member_belongs_to_other_entity_errors():
    m = _model()
    m.add(LogicalEntity(id="01LE9", name="other", attributes=[Attribute(id="01OZ", name="z")]))
    # key group on cust referencing an attribute of `other`
    m.add(KeyGroup(id="01KG", entity="01LE", name="pk", type="pk", members=["01OZ"]))
    assert "MDL-E106" in _errors(m)


def test_two_primary_keys_errors():
    m = _model()
    m.add(KeyGroup(id="01KG1", entity="01LE", name="pk1", type="pk", members=["01A1"]))
    m.add(KeyGroup(id="01KG2", entity="01LE", name="pk2", type="pk", members=["01A2"]))
    assert "MDL-E108" in _errors(m)


def test_alternate_key_allows_multiple():
    m = _model()
    m.add(KeyGroup(id="01KG1", entity="01LE", name="pk", type="pk", members=["01A1"]))
    m.add(KeyGroup(id="01KG2", entity="01LE", name="ak", type="alternate", members=["01A2"]))
    assert _errors(m) == []
