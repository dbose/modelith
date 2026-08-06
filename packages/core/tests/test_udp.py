"""UDP fields round-trip on core objects, and typos elsewhere still error (P4)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mdl_core.ir import (
    Attribute,
    Domain,
    KeyGroup,
    LogicalEntity,
    Relationship,
    RelationshipEnd,
)


def test_udp_accepts_scalar_values():
    a = Attribute(id="01A", name="x", udp={"pii": True, "sla": 4, "score": 1.5, "src": "ERP"})
    assert a.udp == {"pii": True, "sla": 4, "score": 1.5, "src": "ERP"}


def test_udp_on_all_core_objects():
    LogicalEntity(id="01LE", name="e", udp={"k": "v"})
    KeyGroup(id="01KG", entity="01LE", name="pk", udp={"k": "v"})
    Domain(id="01D", name="d", base_type="string", udp={"k": "v"})
    Relationship(
        id="01R", name="r",
        **{"from": RelationshipEnd(entity="01A")},
        to=RelationshipEnd(entity="01B"),
        udp={"k": "v"},
    )


def test_extra_keys_still_forbidden():
    # UDPs go in `udp`, not as arbitrary top-level keys — typo safety preserved.
    with pytest.raises(ValidationError):
        Attribute(id="01A", name="x", pii=True)  # not a real field
