"""erwin XML import tests (spec §6.4)."""

from __future__ import annotations

from mdl_core.diagnostics import Severity
from mdl_core.validate import validate
from mdl_reverse.erwin import import_erwin

_ERWIN = """\
<Model name="TradingModel">
  <SubjectArea name="Trading"/>
  <Entity name="Counterparty" subject_area="Trading" definition="A trading partner.">
    <Attribute name="counterparty_id" datatype="BIGINT" key="true" notnull="true"/>
    <Attribute name="legal_name" datatype="VARCHAR(255)"/>
  </Entity>
  <Entity name="Trade" subject_area="Trading">
    <Attribute name="trade_id" datatype="BIGINT" key="true" notnull="true"/>
    <Attribute name="counterparty_id" datatype="BIGINT"/>
  </Entity>
  <Relationship name="trade_has_cpty" parent="Counterparty" child="Trade"
                child_attr="counterparty_id" cardinality="many_to_one"/>
</Model>
"""


def test_import_erwin_entities_and_attributes():
    m = import_erwin(_ERWIN)
    names = {le.name for le in m.logical_entities.values()}
    assert names == {"counterparty", "trade"}
    cpty = next(le for le in m.logical_entities.values() if le.name == "counterparty")
    bk = next(a for a in cpty.attributes if a.name == "counterparty_id")
    assert bk.role == "business_key"
    assert bk.nullable is False
    assert bk.domain == "bigint"


def test_import_erwin_subject_area():
    m = import_erwin(_ERWIN)
    assert len(m.subject_areas) == 1
    sa = next(iter(m.subject_areas.values()))
    assert sa.name == "Trading"
    cpty_ce = next(c for c in m.conceptual_entities.values() if c.name == "Counterparty")
    assert cpty_ce.subject_area == sa.id


def test_import_erwin_relationship():
    m = import_erwin(_ERWIN)
    assert len(m.relationships) == 1
    rel = next(iter(m.relationships.values()))
    assert rel.cardinality == "many_to_one"
    # many side = Trade, one side = Counterparty
    trade = next(le for le in m.logical_entities.values() if le.name == "trade")
    cpty = next(le for le in m.logical_entities.values() if le.name == "counterparty")
    assert rel.from_.entity == trade.id
    assert rel.to.entity == cpty.id


def test_imported_erwin_model_validates():
    m = import_erwin(_ERWIN)
    diags = validate(m)
    assert not diags.has(Severity.error), [d.message for d in diags.items]


def test_import_erwin_tolerates_unknown_elements():
    xml = """\
<Model name="M">
  <Metadata author="someone"/>
  <Entity name="Thing"><Attribute name="id" datatype="BIGINT" key="true"/></Entity>
  <UnknownWrapper><Note>ignored</Note></UnknownWrapper>
</Model>
"""
    m = import_erwin(xml)
    assert len(m.logical_entities) == 1
