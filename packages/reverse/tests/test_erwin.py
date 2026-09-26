"""erwin XML import — against the REAL erwin export shape (spec §6.4).

The fixture mirrors real erwin: namespaced, GUID-referenced, properties as element text
inside <XxxProps>, keys as per-entity Key_Group, relationships linking entities by
Parent/Child_Entity_Ref, subject-area membership via attached-object ref arrays, and a
subtype relationship that becomes a Category. Unknown groups (a View, an ER_Diagram) are
skipped with warnings, not errors.
"""

from __future__ import annotations

from mdl_core.diagnostics import Severity
from mdl_core.validate import validate
from mdl_reverse.erwin import import_erwin

_ERWIN = """<?xml version="1.0"?>
<erwin xmlns="http://www.erwin.com/dm" FileVersion="9.8.00" Format="erwin">
  <Model xmlns="http://www.erwin.com/dm/data">
    <ModelProps><Name>TradingModel</Name></ModelProps>
    <Domain_Groups>
      <Domain id="D1"><DomainProps>
        <Name>id_type</Name><Logical_Data_Type>BIGINT</Logical_Data_Type>
      </DomainProps></Domain>
    </Domain_Groups>
    <Entity_Groups>
      <Entity id="E_CP"><EntityProps>
          <Name>Counterparty</Name><Physical_Name>COUNTERPARTY</Physical_Name>
          <Definition>A trading partner.</Definition>
        </EntityProps>
        <Attribute_Groups>
          <Attribute id="A_CP1"><AttributeProps>
            <Name>counterparty_id</Name><Parent_Domain_Ref>D1</Parent_Domain_Ref>
            <Null_Option_Type>Not_Null</Null_Option_Type>
          </AttributeProps></Attribute>
          <Attribute id="A_CP2"><AttributeProps>
            <Name>legal_name</Name><Logical_Data_Type>VARCHAR(255)</Logical_Data_Type>
          </AttributeProps></Attribute>
        </Attribute_Groups>
        <Key_Group_Groups>
          <Key_Group id="K_CP"><Key_GroupProps>
              <Name>pk_counterparty</Name><Key_Group_Type>Primary_Key</Key_Group_Type>
            </Key_GroupProps>
            <Key_Group_Member_Groups>
              <Key_Group_Member id="KM_CP"><Key_Group_MemberProps>
                <Attribute_Ref>A_CP1</Attribute_Ref>
              </Key_Group_MemberProps></Key_Group_Member>
            </Key_Group_Member_Groups>
          </Key_Group>
        </Key_Group_Groups>
      </Entity>
      <Entity id="E_TR"><EntityProps><Name>Trade</Name></EntityProps>
        <Attribute_Groups>
          <Attribute id="A_TR1"><AttributeProps><Name>trade_id</Name></AttributeProps></Attribute>
          <Attribute id="A_TR2"><AttributeProps>
            <Name>counterparty_id</Name>
            <Parent_Relationship_Ref>R1</Parent_Relationship_Ref>
            <Parent_Attribute_Ref>A_CP1</Parent_Attribute_Ref>
          </AttributeProps></Attribute>
        </Attribute_Groups>
        <Key_Group_Groups>
          <Key_Group id="K_TR"><Key_GroupProps>
            <Name>pk_trade</Name><Key_Group_Type>Primary_Key</Key_Group_Type></Key_GroupProps>
            <Key_Group_Member_Groups><Key_Group_Member id="KM_TR"><Key_Group_MemberProps>
              <Attribute_Ref>A_TR1</Attribute_Ref>
            </Key_Group_MemberProps></Key_Group_Member></Key_Group_Member_Groups>
          </Key_Group>
        </Key_Group_Groups>
      </Entity>
      <Entity id="E_INST"><EntityProps><Name>Institution</Name></EntityProps>
        <Attribute_Groups><Attribute id="A_INST1"><AttributeProps>
          <Name>counterparty_id</Name>
        </AttributeProps></Attribute></Attribute_Groups>
      </Entity>
    </Entity_Groups>
    <Relationship_Groups>
      <Relationship id="R1"><RelationshipProps>
        <Name>trade_has_counterparty</Name>
        <Parent_Entity_Ref>E_CP</Parent_Entity_Ref><Child_Entity_Ref>E_TR</Child_Entity_Ref>
        <Cardinality>Zero_One_or_More</Cardinality>
        <Parent_To_Child_Verb_Phrase>is party to</Parent_To_Child_Verb_Phrase>
        <Child_To_Parent_Verb_Phrase>is with</Child_To_Parent_Verb_Phrase>
      </RelationshipProps></Relationship>
      <Relationship id="R2"><RelationshipProps>
        <Name>cpty_is_institution</Name><Type>Subtype_Complete</Type>
        <Parent_Entity_Ref>E_CP</Parent_Entity_Ref><Child_Entity_Ref>E_INST</Child_Entity_Ref>
        <Subtype_Discriminator_Ref>A_CP2</Subtype_Discriminator_Ref>
      </RelationshipProps></Relationship>
    </Relationship_Groups>
    <Subject_Area_Groups>
      <Subject_Area id="S1"><Subject_AreaProps>
        <Name>Trading</Name>
        <User_Attached_Objects_Ref_Array>
          <User_Attached_Objects_Ref index="0">E_CP</User_Attached_Objects_Ref>
          <User_Attached_Objects_Ref index="1">E_TR</User_Attached_Objects_Ref>
        </User_Attached_Objects_Ref_Array>
      </Subject_AreaProps></Subject_Area>
    </Subject_Area_Groups>
    <View_Groups><View id="V1"><ViewProps><Name>v_active_trades</Name></ViewProps></View></View_Groups>
    <ER_Diagram_Groups><ER_Diagram id="ERD1"><ER_DiagramProps>
      <Name>Main</Name></ER_DiagramProps></ER_Diagram></ER_Diagram_Groups>
  </Model>
</erwin>
"""


def _import():
    return import_erwin(_ERWIN)


def test_entities_and_attributes():
    r = _import()
    m = r.model
    names = {le.name for le in m.logical_entities.values()}
    assert names == {"counterparty", "trade", "institution"}
    cp = next(le for le in m.logical_entities.values() if le.name == "counterparty")
    assert cp.physical_name == "COUNTERPARTY"
    bk = next(a for a in cp.attributes if a.name == "counterparty_id")
    assert bk.role == "business_key"
    assert bk.nullable is False
    assert bk.domain == "id_type"  # resolved via Parent_Domain_Ref
    ln = next(a for a in cp.attributes if a.name == "legal_name")
    assert ln.domain == "string" and ln.nullable is True


def test_primary_key_group():
    m = _import().model
    cp = next(le for le in m.logical_entities.values() if le.name == "counterparty")
    kg = next(k for k in m.key_groups.values() if k.entity == cp.id and k.type == "pk")
    assert len(kg.members) == 1
    assert kg.members[0] == cp.attributes[0].id  # counterparty_id


def test_relationship_with_verb_phrases():
    m = _import().model
    rel = next(r for r in m.relationships.values() if r.name == "trade_has_counterparty")
    trade = next(le for le in m.logical_entities.values() if le.name == "trade")
    cp = next(le for le in m.logical_entities.values() if le.name == "counterparty")
    assert rel.from_.entity == trade.id  # child = many side
    assert rel.to.entity == cp.id  # parent = one side
    assert rel.verb_phrase == "is party to"
    assert rel.inverse_verb_phrase == "is with"
    # the FK child attribute (Parent_Relationship_Ref -> R1) is the from-attribute
    fk = next(a for a in trade.attributes if a.name == "counterparty_id")
    assert rel.from_.attributes == [fk.id]


def test_subtype_becomes_category():
    m = _import().model
    cp = next(le for le in m.logical_entities.values() if le.name == "counterparty")
    inst = next(le for le in m.logical_entities.values() if le.name == "institution")
    cat = next(iter(m.categories.values()))
    assert cat.supertype == cp.id
    assert inst.id in cat.subtypes
    # the subtype relationship is NOT also a plain relationship
    assert all(r.name != "cpty_is_institution" for r in m.relationships.values())


def test_subject_area_membership():
    m = _import().model
    sa = next(iter(m.subject_areas.values()))
    assert sa.name == "Trading"
    cp = next(le for le in m.logical_entities.values() if le.name == "counterparty")
    tr = next(le for le in m.logical_entities.values() if le.name == "trade")
    assert cp.realises in sa.members
    assert tr.realises in sa.members


def test_domain_imported():
    m = _import().model
    assert any(d.name == "id_type" for d in m.domains.values())


def test_unknown_objects_warn_not_fail():
    r = _import()
    joined = " ".join(r.warnings).lower()
    assert "view" in joined  # a View was skipped
    assert "layout" in joined or "diagram" in joined  # ER_Diagram layout dropped
    # nested Attribute/Key_Group are NOT reported as skipped Model-level objects
    assert "attribute" not in joined
    assert "key_group" not in joined


def test_imported_model_validates():
    m = _import().model
    diags = validate(m)
    # a clean import should not produce ERROR diagnostics (warnings are fine)
    assert not diags.has(Severity.error), [d.message for d in diags.items if d.severity == Severity.error]


def _big_erwin(n_entities: int) -> str:
    """Generate a large real-shaped erwin export with n_entities, each with 8 attributes
    and a pk, plus a chain of relationships — to exercise the streaming parser."""
    parts = [
        '<?xml version="1.0"?>',
        '<erwin xmlns="http://www.erwin.com/dm" FileVersion="9.8" Format="erwin">',
        '<Model xmlns="http://www.erwin.com/dm/data">',
        "<ModelProps><Name>Big</Name></ModelProps><Entity_Groups>",
    ]
    for i in range(n_entities):
        attrs = "".join(
            f'<Attribute id="A{i}_{j}"><AttributeProps><Name>col_{j}</Name>'
            f"<Logical_Data_Type>VARCHAR(50)</Logical_Data_Type></AttributeProps></Attribute>"
            for j in range(8)
        )
        parts.append(
            f'<Entity id="E{i}"><EntityProps><Name>entity_{i}</Name></EntityProps>'
            f"<Attribute_Groups><Attribute id=\"A{i}_pk\"><AttributeProps><Name>id_{i}</Name>"
            f"</AttributeProps></Attribute>{attrs}</Attribute_Groups>"
            f'<Key_Group_Groups><Key_Group id="K{i}"><Key_GroupProps><Name>pk_{i}</Name>'
            f"<Key_Group_Type>Primary_Key</Key_Group_Type></Key_GroupProps>"
            f'<Key_Group_Member_Groups><Key_Group_Member id="KM{i}"><Key_Group_MemberProps>'
            f"<Attribute_Ref>A{i}_pk</Attribute_Ref></Key_Group_MemberProps></Key_Group_Member>"
            f"</Key_Group_Member_Groups></Key_Group></Key_Group_Groups></Entity>"
        )
    parts.append("</Entity_Groups><Relationship_Groups>")
    for i in range(1, n_entities):
        parts.append(
            f'<Relationship id="R{i}"><RelationshipProps><Name>r_{i}</Name>'
            f"<Parent_Entity_Ref>E{i - 1}</Parent_Entity_Ref>"
            f"<Child_Entity_Ref>E{i}</Child_Entity_Ref></RelationshipProps></Relationship>"
        )
    parts.append("</Relationship_Groups></Model></erwin>")
    return "".join(parts)


def test_streaming_large_export():
    # ~a few thousand entities: a multi-MB document the streaming parser handles.
    xml = _big_erwin(2000)
    assert len(xml.encode()) > 1_000_000  # >1MB
    r = import_erwin(xml)
    assert len(r.model.logical_entities) == 2000
    assert len(r.model.relationships) == 1999
    # every entity got its pk key group
    assert sum(1 for k in r.model.key_groups.values() if k.type == "pk") == 2000
