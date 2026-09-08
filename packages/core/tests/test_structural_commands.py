"""Mutation commands for the structural objects that were previously YAML-only.

`KeyGroup`, `Category`, `Domain` and `CodeSet` existed in the IR and validated,
but no command could create or edit them, so they were unreachable from the
canvas, the SME app and the API (an erwin-parity gap). These tests pin the
handlers, the referential guards, and the definition field that the five
previously-undocumentable kinds gained.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mdl_core.commands import CommandError, apply_command
from mdl_core.diagnostics import Severity
from mdl_core.repo import ModelRepo
from mdl_core.validate import validate

from model_builders import write_model


def _load(root: Path):
    return ModelRepo.load(root).model


def _clean(root: Path) -> None:
    """The model must stay valid after every structural command."""
    errs = validate(_load(root)).by_min_severity(Severity.error)
    assert not errs, [f"{d.code} {d.message}" for d in errs]


# --- key groups ----------------------------------------------------------------


def test_create_key_group_composite_ordered(tmp_path: Path):
    ids = write_model(tmp_path)
    r = apply_command(
        tmp_path,
        "create_key_group",
        {
            "entity": ids["le"],
            "name": "pk_counterparty",
            "type": "pk",
            "members": [ids["a1"], ids["a2"]],
            "definition": "Business key for a counterparty.",
        },
    )
    assert r.ok and r.created_id
    kg = _load(tmp_path).key_groups[r.created_id]
    # order is semantic for a composite key -> stored exactly as given
    assert kg.members == [ids["a1"], ids["a2"]]
    assert kg.type == "pk"
    assert kg.definition == "Business key for a counterparty."
    assert (tmp_path / "logical" / "key-groups" / "pk_counterparty.yaml").exists()
    _clean(tmp_path)


def test_key_group_rejects_foreign_attribute(tmp_path: Path):
    ids = write_model(tmp_path)
    # a3 belongs to `trade`, not `counterparty`
    with pytest.raises(CommandError, match="does not belong"):
        apply_command(
            tmp_path,
            "create_key_group",
            {"entity": ids["le"], "name": "bad", "members": [ids["a3"]]},
        )


def test_only_one_pk_per_entity(tmp_path: Path):
    ids = write_model(tmp_path)
    apply_command(
        tmp_path,
        "create_key_group",
        {"entity": ids["le"], "name": "pk_one", "members": [ids["a1"]]},
    )
    with pytest.raises(CommandError, match="already has a primary key"):
        apply_command(
            tmp_path,
            "create_key_group",
            {"entity": ids["le"], "name": "pk_two", "members": [ids["a2"]]},
        )


def test_update_key_group_renames_file_keeps_ulid(tmp_path: Path):
    ids = write_model(tmp_path)
    kg_id = apply_command(
        tmp_path,
        "create_key_group",
        {"entity": ids["le"], "name": "ak_name", "type": "alternate", "members": [ids["a2"]]},
    ).created_id
    apply_command(tmp_path, "update_key_group", {"id": kg_id, "name": "ak_legal_name"})
    m = _load(tmp_path)
    assert kg_id in m.key_groups  # ULID survives a rename (property 4)
    assert m.key_groups[kg_id].name == "ak_legal_name"
    assert (tmp_path / "logical" / "key-groups" / "ak_legal_name.yaml").exists()
    assert not (tmp_path / "logical" / "key-groups" / "ak_name.yaml").exists()
    _clean(tmp_path)


def test_delete_key_group(tmp_path: Path):
    ids = write_model(tmp_path)
    kg_id = apply_command(
        tmp_path,
        "create_key_group",
        {"entity": ids["le"], "name": "idx_name", "type": "index", "members": [ids["a2"]]},
    ).created_id
    apply_command(tmp_path, "delete_key_group", {"id": kg_id})
    assert kg_id not in _load(tmp_path).key_groups
    _clean(tmp_path)


# --- categories ----------------------------------------------------------------


def test_create_category_with_discriminator(tmp_path: Path):
    ids = write_model(tmp_path)
    r = apply_command(
        tmp_path,
        "create_category",
        {
            "name": "party_type",
            "supertype": ids["le"],
            "subtypes": [ids["le2"]],
            "discriminator": ids["a2"],
            "complete": True,
            "exclusive": True,
            "materialization": "single_table",
        },
    )
    cat = _load(tmp_path).categories[r.created_id]
    assert cat.supertype == ids["le"] and cat.subtypes == [ids["le2"]]
    assert cat.discriminator == ids["a2"] and cat.complete is True
    _clean(tmp_path)


def test_category_discriminator_must_belong_to_supertype(tmp_path: Path):
    ids = write_model(tmp_path)
    with pytest.raises(CommandError, match="not an attribute"):
        apply_command(
            tmp_path,
            "create_category",
            {
                "name": "bad_cat",
                "supertype": ids["le"],
                "subtypes": [ids["le2"]],
                "discriminator": ids["a3"],  # belongs to trade, not counterparty
            },
        )


def test_category_supertype_cannot_be_its_own_subtype(tmp_path: Path):
    ids = write_model(tmp_path)
    with pytest.raises(CommandError, match="cannot also be"):
        apply_command(
            tmp_path,
            "create_category",
            {"name": "loop", "supertype": ids["le"], "subtypes": [ids["le"]]},
        )


# --- domains -------------------------------------------------------------------


def test_create_domain_and_reject_duplicate_name(tmp_path: Path):
    write_model(tmp_path)
    apply_command(
        tmp_path,
        "create_domain",
        {"name": "money", "base_type": "decimal", "definition": "A monetary amount."},
    )
    assert _load(tmp_path).domain_by_name("money") is not None
    with pytest.raises(CommandError, match="already exists"):
        apply_command(tmp_path, "create_domain", {"name": "money", "base_type": "decimal"})
    _clean(tmp_path)


def test_rename_domain_rewrites_referencing_attributes(tmp_path: Path):
    """Attributes reference a domain by NAME, so a rename must rewrite them or it
    silently orphans every attribute using it."""
    ids = write_model(tmp_path)
    apply_command(tmp_path, "update_domain", {"id": ids["dom"], "name": "identifier_bigint"})
    m = _load(tmp_path)
    assert m.domain_by_name("identifier_bigint") is not None
    assert m.domain_by_name("id_bigint") is None
    for le in m.logical_entities.values():
        for a in le.attributes:
            assert a.domain == "identifier_bigint"
    _clean(tmp_path)


def test_delete_domain_refuses_while_in_use(tmp_path: Path):
    ids = write_model(tmp_path)
    with pytest.raises(CommandError, match="is used by"):
        apply_command(tmp_path, "delete_domain", {"id": ids["dom"]})


# --- code sets -----------------------------------------------------------------


def test_create_code_set_and_link_from_domain(tmp_path: Path):
    write_model(tmp_path)
    cs_id = apply_command(
        tmp_path,
        "create_code_set",
        {
            "name": "asset_class",
            "definition": "Permitted asset classes.",
            "values": [{"code": "EQ", "label": "Equity"}, "FI"],
        },
    ).created_id
    cs = _load(tmp_path).code_sets[cs_id]
    assert [v.code for v in cs.values] == ["EQ", "FI"]
    assert cs.values[0].label == "Equity"

    apply_command(
        tmp_path,
        "create_domain",
        {"name": "asset_class_enum", "base_type": "string", "value_set": "asset_class"},
    )
    # renaming the code set must keep the referencing domain in step
    apply_command(tmp_path, "update_code_set", {"id": cs_id, "name": "asset_classes"})
    m = _load(tmp_path)
    assert m.domain_by_name("asset_class_enum").value_set == "asset_classes"
    _clean(tmp_path)


def test_delete_code_set_refuses_while_referenced(tmp_path: Path):
    write_model(tmp_path)
    cs_id = apply_command(
        tmp_path, "create_code_set", {"name": "ccy", "values": ["GBP"]}
    ).created_id
    apply_command(
        tmp_path,
        "create_domain",
        {"name": "currency_code", "base_type": "string", "value_set": "ccy"},
    )
    with pytest.raises(CommandError, match="is used by"):
        apply_command(tmp_path, "delete_code_set", {"id": cs_id})


# --- definitions on the previously-undocumentable kinds -------------------------


def test_definition_on_logical_entity_and_attribute(tmp_path: Path):
    """LogicalEntity, Attribute, Relationship, KeyGroup and Category had no
    definition field at all — attribute-level documentation had nowhere to live."""
    ids = write_model(tmp_path)
    apply_command(
        tmp_path,
        "set_object_definition",
        {"id": ids["le"], "definition": "The counterparty as modelled."},
    )
    apply_command(
        tmp_path,
        "set_object_definition",
        {
            "id": ids["le"],
            "attribute_id": ids["a2"],
            "definition": "Registered legal name, as filed.",
        },
    )
    apply_command(
        tmp_path,
        "set_object_definition",
        {"id": ids["rel"], "definition": "Every trade faces exactly one counterparty."},
    )
    m = _load(tmp_path)
    le = m.logical_entities[ids["le"]]
    assert le.definition == "The counterparty as modelled."
    assert {a.id: a.definition for a in le.attributes}[ids["a2"]] == (
        "Registered legal name, as filed."
    )
    assert m.relationships[ids["rel"]].definition.startswith("Every trade")
    _clean(tmp_path)


def test_clearing_a_definition_removes_the_key(tmp_path: Path):
    ids = write_model(tmp_path)
    apply_command(tmp_path, "set_object_definition", {"id": ids["le"], "definition": "x"})
    apply_command(tmp_path, "set_object_definition", {"id": ids["le"], "definition": ""})
    assert _load(tmp_path).logical_entities[ids["le"]].definition is None
    assert "definition" not in (tmp_path / "logical" / "entities" / "counterparty.yaml").read_text()


def test_definition_on_unknown_attribute_errors(tmp_path: Path):
    ids = write_model(tmp_path)
    with pytest.raises(CommandError, match="no attribute"):
        apply_command(
            tmp_path,
            "set_object_definition",
            {"id": ids["le"], "attribute_id": ids["a3"], "definition": "x"},
        )


# --- identifying relationships (IDEF1X) -----------------------------------------


def test_identifying_is_settable(tmp_path: Path):
    """`identifying` was declared in the IR but no command could set it, so it was
    a dead field. It drives the solid-vs-dashed line on the canvas."""
    ids = write_model(tmp_path)
    assert _load(tmp_path).relationships[ids["rel"]].identifying is False
    apply_command(tmp_path, "update_relationship", {"id": ids["rel"], "identifying": True})
    assert _load(tmp_path).relationships[ids["rel"]].identifying is True
    apply_command(tmp_path, "update_relationship", {"id": ids["rel"], "identifying": False})
    assert _load(tmp_path).relationships[ids["rel"]].identifying is False
    _clean(tmp_path)


def test_relationship_definition_is_settable(tmp_path: Path):
    ids = write_model(tmp_path)
    apply_command(
        tmp_path,
        "update_relationship",
        {"id": ids["rel"], "definition": "A trade faces exactly one counterparty."},
    )
    assert _load(tmp_path).relationships[ids["rel"]].definition.startswith("A trade faces")
    _clean(tmp_path)


# --- client-supplied ULIDs (preview/propose identity) ----------------------------


def test_creation_honours_a_supplied_ulid(tmp_path: Path):
    """A previewed creation must keep its identity when the same change list is
    replayed at propose time — otherwise the entity the user saw is not the entity
    that lands in the PR, and a create-then-edit batch fails at submit."""
    from mdl_core.ids import new_ulid

    write_model(tmp_path)
    le_id, ce_id = new_ulid(), new_ulid()
    r = apply_command(
        tmp_path,
        "create_entity",
        {"name": "custody_account", "id": le_id, "conceptual_id": ce_id},
    )
    assert r.created_id == le_id
    m = _load(tmp_path)
    assert le_id in m.logical_entities
    assert m.logical_entities[le_id].realises == ce_id
    _clean(tmp_path)


def test_replaying_the_same_change_list_is_identical(tmp_path: Path, tmp_path_factory):
    """The property that matters: preview and propose run the same ops and must
    produce the same ULIDs."""
    from mdl_core.ids import new_ulid

    ids = {"id": new_ulid(), "conceptual_id": new_ulid()}
    first = tmp_path
    write_model(first)
    apply_command(first, "create_entity", {"name": "custody_account", **ids})

    second = tmp_path_factory.mktemp("replay")
    write_model(second)
    apply_command(second, "create_entity", {"name": "custody_account", **ids})

    # the fixture mints its own ULIDs per call, so compare the CREATED object only
    assert ids["id"] in _load(first).logical_entities
    assert ids["id"] in _load(second).logical_entities
    assert (
        _load(first).logical_entities[ids["id"]].realises
        == _load(second).logical_entities[ids["id"]].realises
        == ids["conceptual_id"]
    )


def test_a_malformed_ulid_is_rejected(tmp_path: Path):
    """Identity field, not free text — a bad value would corrupt every reference."""
    write_model(tmp_path)
    with pytest.raises(CommandError, match="not a valid ULID"):
        apply_command(tmp_path, "create_entity", {"name": "bad", "id": "not-a-ulid"})


def test_omitting_the_id_still_mints_one(tmp_path: Path):
    write_model(tmp_path)
    r = apply_command(tmp_path, "create_entity", {"name": "minted"})
    assert r.created_id and r.created_id in _load(tmp_path).logical_entities


def test_supplied_ulids_work_for_every_creator(tmp_path: Path):
    from mdl_core.ids import new_ulid

    ids = write_model(tmp_path)
    sa = new_ulid()
    apply_command(tmp_path, "create_subject_area", {"name": "Risk", "id": sa})
    assert sa in _load(tmp_path).subject_areas

    dom = new_ulid()
    apply_command(tmp_path, "create_domain", {"name": "money", "base_type": "decimal", "id": dom})
    assert dom in _load(tmp_path).domains

    cs = new_ulid()
    apply_command(tmp_path, "create_code_set", {"name": "ccy", "values": ["GBP"], "id": cs})
    assert cs in _load(tmp_path).code_sets

    kg = new_ulid()
    apply_command(
        tmp_path,
        "create_key_group",
        {"entity": ids["le"], "name": "ak_x", "type": "alternate", "members": [ids["a2"]], "id": kg},
    )
    assert kg in _load(tmp_path).key_groups
    _clean(tmp_path)
