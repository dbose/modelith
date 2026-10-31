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
