"""GovernanceGraph + mapping DSL tests (spec §9.1, §9.2)."""

from __future__ import annotations

from mdl_governance import Profile, build_graph, external_id, map_graph


def test_external_id_deterministic_from_ulid():
    assert external_id("01J8ZR000000000000000000AA") == "mdl:01J8ZR000000000000000000AA"


def test_build_graph_covers_kinds(model):
    g = build_graph(model)
    kinds = {a.modelith_kind for a in g.assets}
    assert {"conceptual_entity", "logical_entity", "attribute"} <= kinds
    # deterministic ids, no duplicates
    assert g.duplicate_external_ids() == []
    # every logical entity has a realises relation
    le_assets = [a for a in g.assets if a.modelith_kind == "logical_entity"]
    assert all(any(r.kind == "realises" for r in a.relations) for a in le_assets)


def test_build_graph_is_idempotent(model):
    a = build_graph(model)
    b = build_graph(model)
    assert {x.external_id for x in a.assets} == {x.external_id for x in b.assets}


def test_map_graph_renders_jinja(model, profiles_dir):
    prof = Profile.load(profiles_dir / "collibra-oob.yaml")
    mapped = map_graph(build_graph(model), prof)
    ce = next(a for a in mapped.assets if a.target_type == "Business Asset")
    assert "Definition" in ce.attributes
    assert ce.attributes["Definition"]  # non-empty definition rendered
    assert "Ontology IRI" in ce.attributes
    assert ce.attributes["Ontology IRI"] == "fibo-fnd-pty-pty:PartyInRole"


def test_map_graph_unmapped_kinds_recorded(model, profiles_dir):
    prof = Profile.load(profiles_dir / "minimal.yaml")
    mapped = map_graph(build_graph(model), prof)
    # minimal profile does not map conceptual_entity
    assert "conceptual_entity" in mapped.unmapped_kinds
    # but logical_entity + attribute are mapped
    assert any(a.target_type == "Table" for a in mapped.assets)
