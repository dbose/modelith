"""M0 acceptance (spec §12): parse->serialise with zero diff incl comments/order."""

from pathlib import Path

from mdl_core.repo import ModelRepo
from mdl_core.yaml_io import round_trip

_COMMENTED = """\
# top comment
id: 01J8ZQ7X4K5N9P2R3S6T8V0W1Y
kind: conceptual_entity
name: Counterparty          # inline comment
subject_area: 01J8ZQ0000000000000000000
definition: >
  A legal person with whom the firm
  has a contractual obligation.
synonyms:
  - Counterparty            # first synonym
  - CPTY
"""


def test_comments_and_order_preserved():
    assert round_trip(_COMMENTED) == _COMMENTED


def test_round_trip_is_idempotent_for_noncanonical_null():
    # ruamel normalises `null`/`~`/empty to empty. The guarantee is *stability*:
    # a second round-trip is a fixed point, so generation stays idempotent.
    once = round_trip("pattern: null\n")
    assert round_trip(once) == once


def test_repo_load_save_zero_diff(model_dir: Path):
    before = {p.name: p.read_text() for p in model_dir.rglob("*.yaml")}
    repo = ModelRepo.load(model_dir)
    repo.save()
    after = {p.name: p.read_text() for p in model_dir.rglob("*.yaml")}
    assert before == after, "load->save must be byte-identical"


def test_load_indexes_all_objects(model_dir: Path):
    repo = ModelRepo.load(model_dir)
    m = repo.model
    assert len(m.conceptual_entities) == 2
    assert len(m.logical_entities) == 2
    assert len(m.relationships) == 1
    assert len(m.domains) == 1
    assert len(m.subject_areas) == 1
