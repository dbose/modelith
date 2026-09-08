"""Semantic model-to-model diff (plan §J)."""

from __future__ import annotations

from pathlib import Path

from mdl_core.diff import ChangeKind, ChangeType, diff_models
from mdl_core.repo import ModelRepo
from mdl_core.severity import ChangeSeverity

from model_builders import write_model


def _load(root: Path):
    return ModelRepo.load(root).model


def _twice(tmp_path: Path):
    """Two independent loads of the same model, so mutating one is safe."""
    ids = write_model(tmp_path)
    return ids, _load(tmp_path), _load(tmp_path)


def _find(diff, name):
    return next(o for o in diff.objects if (o.name_before or o.name_after) == name)


def test_identical_models_have_no_diff(tmp_path: Path):
    _, a, b = _twice(tmp_path)
    d = diff_models(a, b)
    assert not d
    assert d.objects == [] and d.counts()["objects"] == 0


def test_rename_is_one_change_not_add_plus_remove(tmp_path: Path):
    """THE headline test. erwin matches by name, so a rename reads as an entity
    removed plus one added, every attribute duplicated. ULID identity means it is
    one cosmetic field change with the attributes untouched."""
    ids, base, head = _twice(tmp_path)
    head.conceptual_entities[ids["ce"]].name = "Legal Entity"

    d = diff_models(base, head)

    assert len(d.objects) == 1, [o.to_doc() for o in d.objects]
    o = d.objects[0]
    assert o.change is ChangeType.modified
    assert o.renamed and o.ulid == ids["ce"]
    assert o.name_before == "Counterparty" and o.name_after == "Legal Entity"
    assert len(o.fields) == 1
    assert o.fields[0].kind is ChangeKind.object_renamed
    assert o.severity is ChangeSeverity.cosmetic
    assert o.children == []  # nothing beneath it moved
    # and emphatically NOT an add + a remove
    assert not any(x.change in (ChangeType.added, ChangeType.removed) for x in d.objects)


def test_changed_ulid_is_add_plus_remove(tmp_path: Path):
    """The deliberate counter-case: identity is the ULID, so replacing it really
    is a different object. Pinned so nobody 'fixes' it later."""
    ids, base, head = _twice(tmp_path)
    ce = head.conceptual_entities.pop(ids["ce"])
    ce.id = "01ZZZZZZZZZZZZZZZZZZZZZZZZ"
    head.conceptual_entities[ce.id] = ce

    d = diff_models(base, head)
    kinds = {o.change for o in d.objects}
    assert kinds == {ChangeType.added, ChangeType.removed}


def test_missing_side_is_all_added_or_all_removed(tmp_path: Path):
    _, a, _ = _twice(tmp_path)
    added = diff_models(None, a)
    assert added.objects and all(o.change is ChangeType.added for o in added.objects)
    removed = diff_models(a, None)
    assert removed.objects and all(o.change is ChangeType.removed for o in removed.objects)


def test_attribute_changes_nest_under_the_entity(tmp_path: Path):
    ids, base, head = _twice(tmp_path)
    le = head.logical_entities[ids["le"]]
    le.attributes = [a for a in le.attributes if a.id != ids["a2"]]

    d = diff_models(base, head)
    o = _find(d, "counterparty")
    assert o.change is ChangeType.modified
    assert len(o.children) == 1
    child = o.children[0]
    assert child.change is ChangeType.removed
    assert child.fields[0].kind is ChangeKind.attribute_removed
    # severity rolls up from the child
    assert o.severity is ChangeSeverity.breaking


def test_nullability_direction(tmp_path: Path):
    """Tightening breaks existing rows; relaxing does not."""
    ids, base, head = _twice(tmp_path)
    attr = next(a for a in head.logical_entities[ids["le"]].attributes if a.id == ids["a2"])
    attr.nullable = False  # was True
    tighten = diff_models(base, head).objects[0].children[0].fields[0]
    assert tighten.severity is ChangeSeverity.breaking

    ids2, base2, head2 = _twice(tmp_path / "second")
    a = next(x for x in head2.logical_entities[ids2["le"]].attributes if x.id == ids2["a1"])
    a.nullable = True  # was False
    relax = diff_models(base2, head2).objects[0].children[0].fields[0]
    assert relax.severity is ChangeSeverity.additive


def test_severity_rolls_up_to_the_worst(tmp_path: Path):
    ids, base, head = _twice(tmp_path)
    ce = head.conceptual_entities[ids["ce"]]
    ce.definition = "something else"          # cosmetic
    le = head.logical_entities[ids["le"]]
    le.attributes = [a for a in le.attributes if a.id != ids["a2"]]  # breaking

    d = diff_models(base, head)
    assert d.has_breaking
    assert d.max_severity is ChangeSeverity.breaking
    # breaking sorts first so the worst news is at the top
    assert d.objects[0].severity is ChangeSeverity.breaking


def test_derived_fields_do_not_produce_phantom_changes(tmp_path: Path):
    """realised_by is declared but never populated on load, and the legacy
    `ontology` block is folded into ontology_refs by a validator — comparing
    either would report changes that did not happen."""
    ids, base, head = _twice(tmp_path)
    head.conceptual_entities[ids["ce"]].realised_by = [ids["le"]]
    assert not diff_models(base, head)


def test_definition_change_is_cosmetic_and_carries_both_sides(tmp_path: Path):
    ids, base, head = _twice(tmp_path)
    head.conceptual_entities[ids["ce"]].definition = "A counterparty, restated."

    f = diff_models(base, head).objects[0].fields[0]
    assert f.kind is ChangeKind.definition_changed
    assert f.severity is ChangeSeverity.cosmetic
    assert f.before.startswith("A legal person") and f.after == "A counterparty, restated."


def test_synonyms_diff_as_add_and_remove(tmp_path: Path):
    ids, base, head = _twice(tmp_path)
    head.conceptual_entities[ids["ce"]].synonyms = ["CPTY", "Legal Person"]
    base.conceptual_entities[ids["ce"]].synonyms = ["CPTY", "Counterpart"]

    kinds = {f.kind for f in diff_models(base, head).objects[0].fields}
    assert ChangeKind.synonym_added in kinds and ChangeKind.synonym_removed in kinds


def test_relationship_identifying_and_cardinality(tmp_path: Path):
    ids, base, head = _twice(tmp_path)
    rel = head.relationships[ids["rel"]]
    rel.identifying = True
    rel.cardinality = "one_to_many"

    kinds = {f.kind for f in _find(diff_models(base, head), "trade_has_counterparty").fields}
    assert ChangeKind.relationship_identifying_changed in kinds
    assert ChangeKind.relationship_cardinality_changed in kinds


def test_added_object_reports_as_added(tmp_path: Path):
    from mdl_core.ids import new_ulid
    from mdl_core.ir import SubjectArea

    _, base, head = _twice(tmp_path)
    sa = SubjectArea(id=new_ulid(), name="Risk")
    head.subject_areas[sa.id] = sa

    o = _find(diff_models(base, head), "Risk")
    assert o.change is ChangeType.added
    assert o.severity is ChangeSeverity.additive


def test_to_doc_is_json_safe_and_shaped_for_the_ui(tmp_path: Path):
    import json

    ids, base, head = _twice(tmp_path)
    head.conceptual_entities[ids["ce"]].name = "Legal Entity"
    doc = diff_models(base, head, base_label="main", head_label="working copy").to_doc()

    json.dumps(doc)  # must serialise
    assert doc["base"]["label"] == "main" and doc["head"]["label"] == "working copy"
    o = doc["objects"][0]
    assert o["renamed"] is True
    assert o["object_kind_label"] == "conceptual entity"  # plain language, for the UI
    assert o["fields"][0]["label"] == "Renamed"
    assert o["fields"][0]["detail"] == "Counterparty → Legal Entity"


def test_touched_ulids_covers_children(tmp_path: Path):
    ids, base, head = _twice(tmp_path)
    le = head.logical_entities[ids["le"]]
    le.attributes = [a for a in le.attributes if a.id != ids["a2"]]

    touched = diff_models(base, head).touched_ulids()
    assert ids["le"] in touched and ids["a2"] in touched


# --- renderers ------------------------------------------------------------------


def test_renderers_name_the_objects_and_severity(tmp_path: Path):
    from mdl_core.diff_render import render_json, render_markdown, render_text

    ids, base, head = _twice(tmp_path)
    head.conceptual_entities[ids["ce"]].name = "Legal Entity"
    le = head.logical_entities[ids["le"]]
    le.attributes = [a for a in le.attributes if a.id != ids["a2"]]
    d = diff_models(base, head, base_label="main", head_label="my changes")

    text = render_text(d)
    assert "main → my changes" in text and "Legal Entity" in text

    md = render_markdown(d)
    assert md.startswith("### Model changes")
    assert "**Legal Entity**" in md
    assert "[!WARNING]" in md  # breaking change flagged for the reviewer
    assert "legal_name" in md  # the removed attribute is named

    assert render_json(d) == d.to_doc()


def test_markdown_of_an_empty_diff_says_so(tmp_path: Path):
    from mdl_core.diff_render import render_markdown

    _, a, b = _twice(tmp_path)
    assert "No model changes" in render_markdown(diff_models(a, b))


def test_ulid_lists_render_as_names(tmp_path: Path):
    """A reviewer reading '+ 01KZ2B1RV0PN06WKGDR8CA4SD4' learns nothing. Subject-area
    members, key-group columns and category subtypes are all ULID lists."""
    ids, base, head = _twice(tmp_path)
    sa = head.subject_areas[ids["sa"]]
    sa.members = [ids["ce"], ids["ce2"]]

    f = diff_models(base, head).objects[0].fields[0]
    assert "Counterparty" in f.detail and "Trade" in f.detail
    assert ids["ce"] not in f.detail  # the raw identifier is gone
    assert f.detail.startswith("+ ")
