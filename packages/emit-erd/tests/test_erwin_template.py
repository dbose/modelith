"""`mdl export erwin --template` — clone a native erwin export's envelope and inject the model.

The template path is the one proven to open in real erwin 10.10 (the self-built envelope freezes
erwin's import engine). It can't be verified against real erwin in CI, so these tests pin the
STRUCTURE the reference implementation (tools/ddl_to_erwin_xml.py) produces and real erwin accepts:
  - the template's settings (ModelProps, Domain_Groups) survive; its content is replaced;
  - entities carry Type 1/6, the real FK key-group wiring (Key_Group_Ref, child IFn with
    Relationship_Ref + Parent_Key_Group_Member_Ref, FK attrs' Parent_Attribute_Ref), numeric enums;
  - a scoped export emits only the area and drops cross-area FKs;
  - a preserved erwin_id is re-emitted verbatim (so erwin updates, not duplicates).
"""

from __future__ import annotations

from lxml import etree as ET
from mdl_emit_erd import emit_erwin
from test_erwin_export import _two_sa_model

_NS = "http://www.erwin.com/dm/data"


def _n(tag: str) -> str:
    return f"{{{_NS}}}{tag}"


# A minimal but structurally real erwin template: the model envelope (ModelProps, ModelEnvProps,
# named Domain_Groups) plus content groups that MUST be stripped, a diagram group that must be
# removed, and a Model_Proxy_Object sibling that must be removed. Mirrors a native export's shape.
_TEMPLATE = f"""<?xml version="1.0" encoding="UTF-8"?>
<erwin xmlns="http://www.erwin.com/dm" FileVersion="10.10.38485" Format="erwin">
  <Model xmlns="{_NS}" id="{{OLD-MODEL}}+00000001" name="OriginalMart">
    <ModelEnvProps>
      <Locator>C:/mart.erwin</Locator>
      <Persistence_Unit_Id>{{OLD-UNIT}}+01000000</Persistence_Unit_Id>
    </ModelEnvProps>
    <ModelProps>
      <Name>OriginalMart</Name>
      <Long_Id>{{OLD-MODEL}}+00000001</Long_Id>
      <Template_Path>C:/template.erwin</Template_Path>
    </ModelProps>
    <Domain_Groups>
      <Domain id="{{DOM-STRING}}+00000000"><DomainProps><Name>String</Name></DomainProps></Domain>
      <Domain id="{{DOM-NUMBER}}+00000000"><DomainProps><Name>Number</Name></DomainProps></Domain>
      <Domain id="{{DOM-DATE}}+00000000"><DomainProps><Name>d_date</Name></DomainProps></Domain>
    </Domain_Groups>
    <Entity_Groups>
      <Entity id="{{STALE-ENT}}+00000000"><EntityProps><Name>StaleTable</Name></EntityProps></Entity>
    </Entity_Groups>
    <Relationship_Groups>
      <Relationship id="{{STALE-REL}}+00000000"><RelationshipProps><Name>stale</Name></RelationshipProps></Relationship>
    </Relationship_Groups>
    <Subject_Area_Groups>
      <Subject_Area id="{{STALE-SA}}+00000000"><Subject_AreaProps><Name>StaleArea</Name></Subject_AreaProps></Subject_Area>
    </Subject_Area_Groups>
    <ER_Diagram_Groups>
      <ER_Diagram id="{{STALE-DIAG}}+00000000"><ER_DiagramProps><Name>Diagram_1</Name></ER_DiagramProps></ER_Diagram>
    </ER_Diagram_Groups>
  </Model>
  <Model_Proxy_Object id="{{PROXY}}+00000000"/>
</erwin>
"""


def _export(subject_area=None):
    return emit_erwin(
        _two_sa_model(), template=_TEMPLATE.encode("utf-8"), subject_area=subject_area,
        model_name="TestMart",
    )


def _root(xml: str):
    return ET.fromstring(xml.encode("utf-8"))


def _names(root, tag: str) -> list[str]:
    return [e.get("name") for e in root.iter(_n(tag))]


def test_template_envelope_survives_and_is_reminted():
    root = _root(_export())
    model = next(c for c in root if ET.QName(c).localname == "Model")
    # kept: the template's domains
    dom_names = {d.findtext(_n("Name")) for d in root.iter(_n("DomainProps"))}
    assert {"String", "Number", "d_date"} <= dom_names
    # reminted: the model id/name are ours, not the template's
    assert model.get("name") == "TestMart"
    assert model.get("id") != "{OLD-MODEL}+00000001"
    # Template_Path is stripped from ModelProps
    assert root.find(f".//{_n('Template_Path')}") is None


def test_template_content_is_stripped_then_replaced():
    root = _root(_export())
    # the template's stale content is gone
    assert "StaleTable" not in _names(root, "Entity")
    assert "StaleArea" not in _names(root, "Subject_Area")
    assert "stale" not in _names(root, "Relationship")
    # the diagram group and proxy sibling are removed entirely
    assert root.find(f".//{_n('ER_Diagram_Groups')}") is None
    assert not [c for c in root if ET.QName(c).localname == "Model_Proxy_Object"]
    # our content is injected
    assert "Ae" in _names(root, "Entity")
    assert "DimSubject" in _names(root, "Entity")


def test_entity_type_dependent_vs_independent():
    # ae_detail -> ae is identifying? No (FK is not part of ae_detail's PK), so both are Type 1.
    # dim_subject is a pure parent -> Type 1. All independent here; assert the code is present.
    root = _root(_export())
    for ent in root.iter(_n("Entity")):
        t = ent.find(f"{_n('EntityProps')}/{_n('Type')}")
        assert t is not None and t.text in ("1", "6")


def test_fk_relationship_has_full_key_group_structure():
    root = _root(_export())
    # the within-area FK detail_of_ae and the cross-area ae_for_subject are both present (whole model)
    rel_names = _names(root, "Relationship")
    assert "detail_of_ae" in rel_names
    assert "ae_for_subject" in rel_names
    for rel in root.iter(_n("Relationship")):
        p = rel.find(_n("RelationshipProps"))
        assert p.findtext(_n("Type")) in ("2", "7")  # numeric enum
        assert p.findtext(_n("Null_Option_Type")) in ("100", "101")
        assert p.findtext(_n("Cardinality")) in ("-1", "-3")
        assert p.find(_n("Key_Group_Ref")) is not None  # migrated parent key group
        assert p.find(_n("Parent_Entity_Ref")) is not None
        assert p.find(_n("Child_Entity_Ref")) is not None


def test_child_entity_has_ifn_key_group_with_back_refs():
    root = _root(_export())
    # ae_detail is the child of detail_of_ae -> it must carry an IFn key group with Relationship_Ref
    ae_detail = next(e for e in root.iter(_n("Entity")) if e.get("name") == "AeDetail")
    kg_types = [
        kg.findtext(f"{_n('Key_GroupProps')}/{_n('Key_Group_Type')}")
        for kg in ae_detail.iter(_n("Key_Group"))
    ]
    assert any(t and t.startswith("IF") for t in kg_types)
    assert any(t == "PK" for t in kg_types)
    # the IF key group references its relationship
    if_kg = next(
        kg for kg in ae_detail.iter(_n("Key_Group"))
        if (kg.findtext(f"{_n('Key_GroupProps')}/{_n('Key_Group_Type')}") or "").startswith("IF")
    )
    assert if_kg.find(f"{_n('Key_GroupProps')}/{_n('Relationship_Ref')}") is not None


def test_fk_attribute_carries_parent_refs():
    root = _root(_export())
    # ae_detail.aeseq is the FK column migrated from ae -> it carries Parent_Attribute_Ref + rel ref
    ae_detail = next(e for e in root.iter(_n("Entity")) if e.get("name") == "AeDetail")
    fk_attr = next(a for a in ae_detail.iter(_n("Attribute")) if a.get("name") == "aeseq")
    ap = fk_attr.find(_n("AttributeProps"))
    assert ap.find(_n("Parent_Attribute_Ref")) is not None
    assert ap.find(_n("Parent_Relationship_Ref")) is not None


def test_attribute_numeric_enums_and_domain_ref():
    root = _root(_export())
    ae = next(e for e in root.iter(_n("Entity")) if e.get("name") == "Ae")
    pk_attr = next(a for a in ae.iter(_n("Attribute")) if a.get("name") == "aeseq")
    ap = pk_attr.find(_n("AttributeProps"))
    assert ap.findtext(_n("Type")) == "0"  # PK member
    assert ap.findtext(_n("Null_Option_Type")) == "1"  # not null
    # a domain ref resolved by name from the template (Number for the integer PK)
    assert ap.find(_n("Parent_Domain_Ref")) is not None


def test_scoped_export_drops_cross_area_fk_no_stub():
    root = _root(_export(subject_area="Events"))
    ent_names = _names(root, "Entity")
    assert "Ae" in ent_names and "AeDetail" in ent_names
    assert "DimSubject" not in ent_names  # out-of-area neighbour is NOT emitted as a stub
    rel_names = _names(root, "Relationship")
    assert "detail_of_ae" in rel_names  # within-area FK kept
    assert "ae_for_subject" not in rel_names  # cross-area FK dropped
    # only the scoped subject area is present
    assert _names(root, "Subject_Area") == ["Events"]


def test_preserved_erwin_id_is_re_emitted_verbatim():
    xml = _export()
    # the entity ids come straight from the model's erwin_id (so erwin UPDATES, not duplicates)
    assert 'id="{AE}+00000000"' in xml or 'id="{AE}"' in xml
    assert 'id="{DIM}+00000000"' in xml or 'id="{DIM}"' in xml


def test_no_dangling_refs_to_template_only_objects():
    root = _root(_export())
    known = {e.get("id") for e in root.iter() if e.get("id")}
    known |= {e.text for e in root.iter(_n("Long_Id")) if e.text}
    import re

    guid_re = re.compile(r"^\{[0-9A-Fa-f-]{36}\}\+[0-9A-Fa-f]{8}$")
    for e in root.iter():
        ln = ET.QName(e).localname
        if "Ref" in ln and e.text and guid_re.match(e.text.strip()):
            assert e.text.strip() in known, f"{ln} points at unknown id {e.text}"
