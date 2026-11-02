"""`mdl export erwin` — the inverse of the erwin importer.

Two guarantees, each pinned:
  1. the output validates against the real erwin XSD (erwin/ERwinSchema.xsd);
  2. it round-trips through our own importer back to an equivalent model.

The strongest source model is the erwin import fixture itself (it exercises every family the
importer maps: entities+attributes, pk/alt key groups, an FK relationship with verb phrases, a
subtype -> Category, a subject area, a domain, UDPs). We import it, re-export, and compare.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from mdl_emit_erd import emit_erwin

from mdl_core.diagnostics import Severity
from mdl_core.ir import ErwinNaming
from mdl_core.validate import validate

# repo paths
_REPO = Path(__file__).resolve().parents[3]
_FIXTURE = _REPO / "demos" / "fixtures" / "sample.erwin.xml"
_XSD = _REPO / "erwin" / "ERwinSchema.xsd"


def _source_model():
    """The model to export: import the erwin fixture (covers all mapped families)."""
    from mdl_reverse.erwin import import_erwin_file

    return import_erwin_file(_FIXTURE).model


def _summary(m):
    return {
        "entities": sorted(le.name for le in m.logical_entities.values()),
        "relationships": len(m.relationships),
        "key_groups": sorted((kg.name, kg.type) for kg in m.key_groups.values()),
        "categories": sorted(c.name for c in m.categories.values()),
        "subject_areas": sorted(sa.name for sa in m.subject_areas.values()),
        "domains": sorted(d.name for d in m.domains.values()),
    }


def _reimport(xml: str):
    from mdl_reverse.erwin import import_erwin

    return import_erwin(xml).model


# --- XSD validity -----------------------------------------------------------------


@pytest.mark.skipif(not _XSD.exists(), reason="erwin XSDs not present (gitignored)")
@pytest.mark.skipif(shutil.which("xmllint") is None, reason="xmllint not installed")
def test_export_validates_against_the_erwin_xsd(tmp_path):
    xml = emit_erwin(_source_model())
    out = tmp_path / "out.erwin.xml"
    out.write_text(xml, encoding="utf-8")
    r = subprocess.run(
        ["xmllint", "--noout", "--schema", str(_XSD), str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, f"xmllint rejected the export:\n{r.stderr}"


def test_file_version_is_xsd_valid_and_skeleton_present():
    """Guards the two things that silently fail xmllint: the FileVersion build digits and the
    required Model id + ModelEnvProps/ModelProps."""
    xml = emit_erwin(_source_model())
    assert 'FileVersion="9.8.0000"' in xml  # 9.8.00 would be INVALID (needs 4 build digits)
    assert 'Format="erwin"' in xml
    assert "<ModelEnvProps/>" in xml and "<ModelProps/>" in xml
    assert "<Model " in xml and "id=" in xml


# --- round-trip -------------------------------------------------------------------


def test_round_trips_to_an_equivalent_model():
    src = _source_model()
    rt = _reimport(emit_erwin(src))
    assert _summary(rt) == _summary(src)
    # the re-imported model is valid
    errs = [d for d in validate(rt).items if d.severity == Severity.error]
    assert not errs, [f"{d.code} {d.message}" for d in errs]


def test_round_trip_preserves_key_group_members_in_order():
    src = _source_model()
    rt = _reimport(emit_erwin(src))

    def members_by_kg(m):
        aname = {a.id: a.name for le in m.logical_entities.values() for a in le.attributes}
        return {kg.name: [aname.get(x) for x in kg.members] for kg in m.key_groups.values()}

    assert members_by_kg(rt) == members_by_kg(src)


def test_round_trip_preserves_category_and_discriminator():
    src = _source_model()
    rt = _reimport(emit_erwin(src))
    assert len(rt.categories) == len(src.categories) == 1
    src_cat = next(iter(src.categories.values()))
    rt_cat = next(iter(rt.categories.values()))
    assert len(rt_cat.subtypes) == len(src_cat.subtypes)
    assert (rt_cat.discriminator is not None) == (src_cat.discriminator is not None)


def test_round_trip_preserves_relationship_verb_phrases():
    src = _source_model()
    rt = _reimport(emit_erwin(src))
    src_vp = sorted(r.verb_phrase for r in src.relationships.values() if r.verb_phrase)
    rt_vp = sorted(r.verb_phrase for r in rt.relationships.values() if r.verb_phrase)
    assert rt_vp == src_vp


def _subject_area_membership(m):
    """Each subject area's name -> sorted member ENTITY names. Membership is stored as
    conceptual-entity ids, so resolve each back to its logical entity name."""
    le_by_ce = {le.realises: le for le in m.logical_entities.values() if le.realises}
    return {
        sa.name: sorted(le_by_ce[mem].name for mem in sa.members if mem in le_by_ce)
        for sa in m.subject_areas.values()
    }


def test_round_trip_preserves_subject_area_membership():
    """Not just that the subject area survives, but that it keeps exactly its member entities."""
    src = _source_model()
    membership = _subject_area_membership(src)
    assert membership and any(members for members in membership.values()), (
        "fixture must have a subject area with members for this test to mean anything"
    )
    rt = _reimport(emit_erwin(src))
    assert _subject_area_membership(rt) == membership


# --- naming convention ------------------------------------------------------------


def test_naming_verbatim_by_default():
    """With no config, logical names are emitted as-is (the conceptual display name)."""
    xml = emit_erwin(_source_model())
    # the sample's entities title-case to Counterparty/Trade/Institution via the importer
    assert "<Name>Counterparty</Name>" in xml
    # physical name is the raw snake/upper identifier, untouched
    assert "<Physical_Name>COUNTERPARTY</Physical_Name>" in xml


def test_naming_convention_rewrites_logical_names_only():
    cfg = ErwinNaming(entity_case="title", replace_underscore_with_space=True)
    xml = emit_erwin(_source_model(), naming=cfg)
    # attribute display names get underscores -> spaces...
    assert "<Name>counterparty id</Name>" in xml
    # ...while physical names stay raw (never rewritten)
    assert "<Physical_Name>COUNTERPARTY</Physical_Name>" in xml
    # and the output is still XSD-shaped (skeleton intact)
    assert 'FileVersion="9.8.0000"' in xml


def test_naming_is_reproducible_and_valid_xml():
    """Deterministic GUIDs -> identical output for the same model (golden-friendly)."""
    m = _source_model()
    assert emit_erwin(m) == emit_erwin(m)


# --- GUID preservation (Part C: re-import updates, not duplicates) ----------------


def test_import_preserves_the_erwin_guid():
    """Every imported object carries its original erwin GUID in `erwin_id`, so a later export
    can re-emit it and erwin matches the object instead of duplicating it."""
    m = _source_model()
    assert all(le.erwin_id for le in m.logical_entities.values())
    assert all(a.erwin_id for le in m.logical_entities.values() for a in le.attributes)
    assert all(kg.erwin_id for kg in m.key_groups.values())
    assert all(r.erwin_id for r in m.relationships.values())
    assert all(sa.erwin_id for sa in m.subject_areas.values())
    assert all(d.erwin_id for d in m.domains.values())


def test_export_re_emits_the_original_guid():
    """The preserved GUID appears verbatim in the export (stable identity across the round-trip)."""
    m = _source_model()
    xml = emit_erwin(m)
    le = next(iter(m.logical_entities.values()))
    sa = next(iter(m.subject_areas.values()))
    assert f'id="{le.erwin_id}"' in xml
    assert f'id="{sa.erwin_id}"' in xml


# --- scoped export (Parts A + B) --------------------------------------------------


def _two_sa_model():
    """A mart with two subject areas and a CROSS-SA FK: Events.ae (SA1) -> Reference.dim (SA2).
    All objects carry erwin_id so we can assert GUID preservation in the slice too."""
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
    # SA2: shared dimension, NOT in SA1
    dim_ce = ConceptualEntity(id=new_ulid(), name="DimSubject")
    m.add(dim_ce)
    dim_bk = Attribute(id=new_ulid(), name="usubjid", role="business_key", nullable=False,
                       domain="string", erwin_id="{DIM-A}")
    dim = LogicalEntity(id=new_ulid(), name="dim_subject", physical_name="DIM_SUBJECT",
                        realises=dim_ce.id, attributes=[dim_bk], erwin_id="{DIM}")
    m.add(dim)
    m.add(KeyGroup(id=new_ulid(), entity=dim.id, name="pk_dim", type="pk",
                   members=[dim_bk.id], erwin_id="{DIM-KG}"))
    m.add(SubjectArea(id=new_ulid(), name="Reference", members=[dim_ce.id], erwin_id="{SA2}"))
    # SA1: ae with a cross-SA FK to dim_subject
    ae_ce = ConceptualEntity(id=new_ulid(), name="Ae")
    m.add(ae_ce)
    ae_bk = Attribute(id=new_ulid(), name="aeseq", role="business_key", nullable=False,
                      domain="integer", erwin_id="{AE-A1}")
    ae_fk = Attribute(id=new_ulid(), name="usubjid", role="attribute", nullable=False,
                      domain="string", erwin_id="{AE-A2}")
    ae = LogicalEntity(id=new_ulid(), name="ae", physical_name="AE", realises=ae_ce.id,
                       attributes=[ae_bk, ae_fk], erwin_id="{AE}")
    m.add(ae)
    m.add(KeyGroup(id=new_ulid(), entity=ae.id, name="pk_ae", type="pk",
                   members=[ae_bk.id], erwin_id="{AE-KG}"))
    m.add(SubjectArea(id=new_ulid(), name="Events", members=[ae_ce.id], erwin_id="{SA1}"))
    m.add(Relationship(
        id=new_ulid(), name="ae_for_subject",
        **{"from": RelationshipEnd(entity=ae.id, attributes=[ae_fk.id])},
        to=RelationshipEnd(entity=dim.id, attributes=[dim_bk.id]),
        cardinality="many_to_one", verb_phrase="describes", erwin_id="{REL}"))
    return m


def _entity_attr_counts(xml):
    import re

    out = {}
    for mm in re.finditer(r"<Entity id=\"[^\"]+\">.*?</Entity>", xml, re.S):
        block = mm.group(0)
        name = re.search(r"<Name>([^<]+)</Name>", block).group(1)
        out[name] = block.count("<Attribute id")
    return out


def test_scoped_export_includes_the_area_and_keeps_cross_sa_fk():
    m = _two_sa_model()
    xml = emit_erwin(m, subject_area="Events")
    counts = _entity_attr_counts(xml)
    # ae exported FULL (both its columns); dim_subject pulled in as a STUB (BK only)
    assert counts.get("Ae") == 2
    assert counts.get("DimSubject") == 1  # business key only
    # the cross-SA FK is retained
    assert "ae_for_subject" in xml


def test_scoped_export_stub_keeps_the_original_guid():
    """The out-of-area neighbour (dim) is emitted with its ORIGINAL erwin GUID, so a re-import
    into the mart matches the real dimension instead of creating a duplicate."""
    m = _two_sa_model()
    xml = emit_erwin(m, subject_area="Events")
    assert 'id="{DIM}"' in xml  # the stub carries dim's real GUID
    assert 'id="{AE}"' in xml  # ae too


@pytest.mark.skipif(not _XSD.exists(), reason="erwin XSDs not present (gitignored)")
@pytest.mark.skipif(shutil.which("xmllint") is None, reason="xmllint not installed")
def test_scoped_export_is_xsd_valid_and_round_trips(tmp_path):
    from mdl_reverse.erwin import import_erwin

    m = _two_sa_model()
    xml = emit_erwin(m, subject_area="Events")
    out = tmp_path / "slice.erwin.xml"
    out.write_text(xml, encoding="utf-8")
    r = subprocess.run(["xmllint", "--noout", "--schema", str(_XSD), str(out)],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    # round-trips with the FK intact, no dangling-ref error
    rt = import_erwin(xml).model
    assert len(rt.relationships) == 1
    errs = [d for d in validate(rt).items if d.severity == Severity.error]
    assert not errs, [f"{d.code} {d.message}" for d in errs]


def test_unscoped_export_unchanged_by_the_scope_feature():
    """Guard: emit_erwin(m) with no subject_area is identical to emit_erwin(m, subject_area=None)."""
    m = _source_model()
    assert emit_erwin(m) == emit_erwin(m, subject_area=None)


# --- erwin-native id shape ("{UPPERCASE-GUID}+00000000") ---------------------------
# erwin's import engine (EAT) HANGS on a lowercase or suffix-less brace GUID (observed against a
# real erwin 10.10 export, whose ids are "{UPPERCASE-GUID}+8hex"). Every id we MINT must be an
# UPPERCASE GUID carrying the "+00000000" new-object serial; a preserved erwin_id is emitted
# verbatim. These guard the exact regression that hung a real GHG mart import.

import re  # noqa: E402

# a minted erwin id: UPPERCASE {8-4-4-4-12} then "+00000000"
_MINTED_ID = re.compile(r'id="\{[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\}\+00000000"')


def test_minted_model_root_id_is_uppercase_with_the_erwin_serial():
    """The model root id is always minted (no erwin source), so it must be an UPPERCASE GUID
    ending with +00000000 — the exact shape erwin's import requires."""
    xml = emit_erwin(_source_model())
    assert _MINTED_ID.search(xml), "a minted id is not {UPPERCASE-GUID}+00000000"


def test_no_minted_id_is_lowercase_or_suffixless():
    """A model with NO erwin_id anywhere -> every emitted object id is minted. None may be a
    LOWERCASE brace GUID, and none may be a brace GUID without a +NNNNNNNN serial — both shapes
    hang erwin's EAT import."""
    from mdl_core.ids import new_ulid
    from mdl_core.ir import Attribute, ConceptualEntity, LogicalEntity, Model, ProjectConfig

    m = Model(ProjectConfig(name="native"))
    ce = ConceptualEntity(id=new_ulid(), name="Thing")
    m.add(ce)
    bk = Attribute(id=new_ulid(), name="thing_id", domain="string")
    m.add(LogicalEntity(id=new_ulid(), name="thing", realises=ce.id, attributes=[bk]))

    xml = emit_erwin(m)
    # any brace GUID containing a lowercase hex digit is wrong (erwin writes them uppercase)
    lower = re.findall(r"\{[0-9A-Fa-f-]*[a-f][0-9A-Fa-f-]*\}", xml)
    assert not lower, f"{len(lower)} lowercase brace GUID(s) emitted (erwin ids are uppercase)"
    # any brace GUID (ids AND refs) not followed by a +NNNNNNNN serial is wrong
    bare = re.findall(r"\{[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\}(?!\+)", xml)
    assert not bare, f"{len(bare)} brace GUID(s) emitted without a +NNNNNNNN serial"


def test_preserved_erwin_id_keeps_its_own_serial_verbatim():
    """A preserved erwin_id (which, for a real mart, already includes its serial) is emitted
    UNCHANGED -- the serial fix only ever appends to ids we mint, never rewrites a preserved one."""
    m = _two_sa_model()
    xml = emit_erwin(m)
    le = next(iter(m.logical_entities.values()))
    assert f'id="{le.erwin_id}"' in xml  # verbatim, no extra serial appended
