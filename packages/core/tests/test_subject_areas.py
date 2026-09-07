"""Subject-area membership and closure expansion (erwin's Subject Area editor)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mdl_core.closure import expand
from mdl_core.commands import CommandError, apply_command
from mdl_core.diagnostics import Severity
from mdl_core.ids import new_ulid
from mdl_core.repo import ModelRepo
from mdl_core.validate import validate

from model_builders import write_model


def _load(root: Path):
    return ModelRepo.load(root).model


def _clean(root: Path) -> None:
    errs = validate(_load(root)).by_min_severity(Severity.error)
    assert not errs, [f"{d.code} {d.message}" for d in errs]


def _sa(root: Path) -> str:
    return next(iter(_load(root).subject_areas))


# --- the members field ----------------------------------------------------------


def test_members_defaults_empty_and_file_is_untouched(tmp_path: Path):
    """A model written before this feature must load unchanged, and load->save must
    stay byte-identical — the M0 round-trip guarantee, re-asserted for the new key."""
    write_model(tmp_path)
    f = tmp_path / "conceptual" / "subject-areas" / "trading.yaml"
    before = f.read_text()
    m = _load(tmp_path)
    assert next(iter(m.subject_areas.values())).members == []
    ModelRepo.load(tmp_path).save()
    assert f.read_text() == before


def test_add_remove_set_members_is_idempotent(tmp_path: Path):
    ids = write_model(tmp_path)
    sa = _sa(tmp_path)
    f = tmp_path / "conceptual" / "subject-areas" / "trading.yaml"
    original = f.read_text()

    apply_command(tmp_path, "add_subject_area_members", {"id": sa, "members": [ids["ce"]]})
    apply_command(tmp_path, "add_subject_area_members", {"id": sa, "members": [ids["ce"]]})
    assert _load(tmp_path).subject_areas[sa].members == [ids["ce"]]  # no duplicate

    # removing something absent is a no-op, not an error
    apply_command(tmp_path, "remove_subject_area_members", {"id": sa, "members": [ids["ce2"]]})
    apply_command(tmp_path, "remove_subject_area_members", {"id": sa, "members": [ids["ce"]]})
    assert _load(tmp_path).subject_areas[sa].members == []
    # emptied -> the key is gone and the file matches its original bytes
    assert f.read_text() == original
    _clean(tmp_path)


def test_set_members_replaces_and_dedupes(tmp_path: Path):
    ids = write_model(tmp_path)
    sa = _sa(tmp_path)
    apply_command(
        tmp_path,
        "set_subject_area_members",
        {"id": sa, "members": [ids["ce"], ids["ce2"], ids["ce"]]},
    )
    assert _load(tmp_path).subject_areas[sa].members == [ids["ce"], ids["ce2"]]
    _clean(tmp_path)


def test_a_logical_ulid_is_coerced_to_its_conceptual_entity(tmp_path: Path):
    """Closure traverses the logical graph, so callers may pass either kind."""
    ids = write_model(tmp_path)
    sa = _sa(tmp_path)
    apply_command(tmp_path, "add_subject_area_members", {"id": sa, "members": [ids["le"]]})
    assert _load(tmp_path).subject_areas[sa].members == [ids["ce"]]
    _clean(tmp_path)


def test_an_object_can_belong_to_many_areas(tmp_path: Path):
    """The erwin-parity point: a domain view and a use-case view can both hold it."""
    ids = write_model(tmp_path)
    other = apply_command(
        tmp_path, "create_subject_area", {"name": "UseCase-ApraStressTesting"}
    ).created_id
    apply_command(tmp_path, "add_subject_area_members", {"id": _sa(tmp_path), "members": [ids["ce"]]})
    apply_command(tmp_path, "add_subject_area_members", {"id": other, "members": [ids["ce"]]})
    m = _load(tmp_path)
    assert ids["ce"] in m.subject_areas[other].members
    assert sum(ids["ce"] in sa.members for sa in m.subject_areas.values()) == 2
    _clean(tmp_path)


def test_members_survive_a_hand_comment(tmp_path: Path):
    ids = write_model(tmp_path)
    sa = _sa(tmp_path)
    f = tmp_path / "conceptual" / "subject-areas" / "trading.yaml"
    f.write_text(f.read_text() + "# curated by the risk team\n")
    apply_command(tmp_path, "add_subject_area_members", {"id": sa, "members": [ids["ce"]]})
    assert "# curated by the risk team" in f.read_text()


def test_a_bad_member_is_rejected(tmp_path: Path):
    write_model(tmp_path)
    with pytest.raises(CommandError, match="not a conceptual entity or term"):
        apply_command(
            tmp_path, "add_subject_area_members", {"id": _sa(tmp_path), "members": [new_ulid()]}
        )


def test_dangling_member_is_an_error(tmp_path: Path):
    write_model(tmp_path)
    f = tmp_path / "conceptual" / "subject-areas" / "trading.yaml"
    f.write_text(f.read_text() + f"members:\n  - {new_ulid()}\n")
    codes = {d.code for d in validate(_load(tmp_path)).by_min_severity(Severity.error)}
    assert "MDL-E107" in codes


def test_wrong_kind_member_is_an_error(tmp_path: Path):
    ids = write_model(tmp_path)
    f = tmp_path / "conceptual" / "subject-areas" / "trading.yaml"
    f.write_text(f.read_text() + f"members:\n  - {ids['rel']}\n")  # a relationship
    codes = {d.code for d in validate(_load(tmp_path)).by_min_severity(Severity.error)}
    assert "MDL-E113" in codes


def test_home_not_in_members_warns_but_does_not_fail(tmp_path: Path):
    """The home tag and the member list answer different questions, so drift is a
    nudge, not a failure."""
    ids = write_model(tmp_path)
    apply_command(
        tmp_path, "add_subject_area_members", {"id": _sa(tmp_path), "members": [ids["ce2"]]}
    )
    diags = validate(_load(tmp_path))
    assert any(d.code == "MDL-W114" for d in diags.items)
    assert not diags.by_min_severity(Severity.error)  # still a valid model


# --- rename / delete ------------------------------------------------------------


def test_rename_moves_the_file_and_keeps_the_ulid(tmp_path: Path):
    write_model(tmp_path)
    sa = _sa(tmp_path)
    apply_command(tmp_path, "update_subject_area", {"id": sa, "name": "Front Office"})
    m = _load(tmp_path)
    assert sa in m.subject_areas and m.subject_areas[sa].name == "Front Office"
    assert (tmp_path / "conceptual" / "subject-areas" / "front_office.yaml").exists()
    assert not (tmp_path / "conceptual" / "subject-areas" / "trading.yaml").exists()
    _clean(tmp_path)


def test_delete_refuses_while_it_is_a_home_area(tmp_path: Path):
    write_model(tmp_path)
    with pytest.raises(CommandError, match="home area"):
        apply_command(tmp_path, "delete_subject_area", {"id": _sa(tmp_path)})


def test_delete_cascade_keeps_the_entities(tmp_path: Path):
    """Deleting a VIEW must never delete the objects in it."""
    ids = write_model(tmp_path)
    before = len(_load(tmp_path).conceptual_entities)
    apply_command(tmp_path, "delete_subject_area", {"id": _sa(tmp_path), "cascade": True})
    m = _load(tmp_path)
    assert len(m.conceptual_entities) == before
    assert m.conceptual_entities[ids["ce"]].subject_area is None
    _clean(tmp_path)


# --- closure expansion ----------------------------------------------------------


def test_ancestors_are_the_parent_side(tmp_path: Path):
    """from_ is the MANY side and to is the ONE side, so from Trade the ancestor is
    Counterparty. The single most reversible decision here, so it is pinned."""
    ids = write_model(tmp_path)
    hops = expand(_load(tmp_path), {ids["ce2"]}, direction="ancestors", levels=1)
    assert [h.id for h in hops] == [ids["ce"]]
    assert hops[0].via == "relationship"
    assert hops[0].via_name == "trade_has_counterparty"
    assert hops[0].from_name == "Trade" and hops[0].name == "Counterparty"


def test_descendants_are_the_child_side(tmp_path: Path):
    ids = write_model(tmp_path)
    hops = expand(_load(tmp_path), {ids["ce"]}, direction="descendants", levels=1)
    assert [h.id for h in hops] == [ids["ce2"]]


def test_seeds_are_never_returned(tmp_path: Path):
    ids = write_model(tmp_path)
    hops = expand(_load(tmp_path), {ids["ce"], ids["ce2"]}, direction="both", levels=3)
    assert hops == []  # both ends already in the seed set


def test_levels_limit_depth_and_exhaust_cleanly(tmp_path: Path):
    ids = write_model(tmp_path)
    m = _load(tmp_path)
    assert len(expand(m, {ids["ce2"]}, direction="ancestors", levels=1)) == 1
    # a 2-object graph is exhausted after one hop; asking for more is not an error
    assert len(expand(m, {ids["ce2"]}, direction="ancestors", levels=99)) == 1
    assert expand(m, {ids["ce2"]}, levels=0) == []


def test_expansion_returns_conceptual_ulids_only(tmp_path: Path):
    ids = write_model(tmp_path)
    m = _load(tmp_path)
    hops = expand(m, {ids["ce"]}, direction="both", levels=5)
    assert hops
    assert all(h.id in m.conceptual_entities for h in hops)


def test_hops_carry_provenance_for_the_picker(tmp_path: Path):
    ids = write_model(tmp_path)
    h = expand(_load(tmp_path), {ids["ce2"]}, direction="both", levels=1)[0]
    doc = h.to_doc()
    assert doc["via_name"] == "trade_has_counterparty" and doc["level"] == 1
