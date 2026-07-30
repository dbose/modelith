"""Property 2 + M3 acceptance (spec §5.5, §12).

reverse(generate(M)) is semantically equal to M modulo a *documented lossy set*,
and generate(reverse(generate(M))) produces a semantically-empty diff against
generate(M) (ignoring fingerprints, which are content hashes that ride along).

The lossy set is asserted explicitly here so any future regression that widens it
fails loudly. Lossy fields (recovered in later milestones):
  - ontology_iri / ontology alignments  (M4)
  - owner / steward stewardship           (M5 governance)
  - not_null constraints (nullability is not safely inferable; §6.3 interactive)
"""

from __future__ import annotations

import difflib
from pathlib import Path

from mdl_core.repo import ModelRepo
from mdl_emit_dbt.emitter import DbtEmitter
from mdl_reverse.reverse import reverse
from mdl_reverse.schema_reader import read_schema_yml
from mdl_reverse.writer import write_model as write_reversed

from model_builders import write_model as build_source

TARGET = "duckdb_dev"

# The only substrings allowed to appear in the generate->reverse->generate diff.
DOCUMENTED_LOSSY = ("ontology_iri", "owner:", "steward:", "not_null", "fingerprint=")


def _gen(model, out: Path):
    DbtEmitter(model, TARGET).generate(out, write=True)


def _diff_lines(a: str, b: str) -> list[str]:
    return [
        line
        for line in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=0)
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]


def test_p2_reverse_roundtrip_semantically_empty(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    build_source(src)
    repo = ModelRepo.load(src)

    gen1 = tmp_path / "gen1"
    _gen(repo.model, gen1)

    result = reverse(read_schema_yml(gen1 / "models" / "schema.yml"), target=TARGET)
    write_reversed(result.model, tmp_path / "rev")
    repo2 = ModelRepo.load(tmp_path / "rev")

    gen2 = tmp_path / "gen2"
    _gen(repo2.model, gen2)

    # Same file set.
    def files(root):
        return {
            str(p.relative_to(root))
            for p in root.rglob("*")
            if p.is_file() and ".mdl" not in str(p)
        }

    assert files(gen1) == files(gen2)

    # Every diff line must be a documented lossy field — nothing else may drift.
    for rel in files(gen1):
        a = (gen1 / rel).read_text()
        b = (gen2 / rel).read_text()
        for line in _diff_lines(a, b):
            assert any(tok in line for tok in DOCUMENTED_LOSSY), (
                f"undocumented round-trip drift in {rel}: {line!r}"
            )


def test_ulid_identity_survives_roundtrip(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    build_source(src)
    repo = ModelRepo.load(src)
    gen1 = tmp_path / "gen1"
    _gen(repo.model, gen1)

    result = reverse(read_schema_yml(gen1 / "models" / "schema.yml"), target=TARGET)

    src_ulids = {le.name: le.id for le in repo.model.logical_entities.values()}
    rev_ulids = {le.name: le.id for le in result.model.logical_entities.values()}
    assert src_ulids == rev_ulids, "logical entity ULIDs must survive the round-trip"

    # Attribute ULIDs too.
    for le in repo.model.logical_entities.values():
        rev_le = next(x for x in result.model.logical_entities.values() if x.name == le.name)
        src_attr = {a.name: a.id for a in le.attributes}
        rev_attr = {a.name: a.id for a in rev_le.attributes}
        assert src_attr == rev_attr, f"attribute ULIDs drifted on {le.name}"


def test_relationship_recovered_high_confidence(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    build_source(src)
    repo = ModelRepo.load(src)
    gen1 = tmp_path / "gen1"
    _gen(repo.model, gen1)

    result = reverse(read_schema_yml(gen1 / "models" / "schema.yml"), target=TARGET)
    rels = list(result.model.relationships.values())
    assert len(rels) == 1
    assert rels[0].name == "trade_has_counterparty"
    assert rels[0].from_.attributes and rels[0].to.attributes  # both ends resolved
    # The proposal was high confidence (dbt relationships test).
    rel_props = [p for p in result.proposals if p.kind == "relationship"]
    assert rel_props and rel_props[0].confidence.value == "high"
