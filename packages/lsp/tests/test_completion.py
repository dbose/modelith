"""Ontology-refs autocomplete in the LSP (spec §1 UX).

Builds a minimal workspace with a local vocabulary and asserts that completion fires
on a `uri:` line inside an `ontology_refs` block and returns ranked term IRIs, and that
it stays quiet everywhere else.
"""

from __future__ import annotations

from pathlib import Path

from mdl_lsp import features
from mdl_lsp.workspace import ModelWorkspace

_TTL = """\
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix acme: <https://acme.example/core/> .

acme:Party a skos:Concept ; skos:prefLabel "Party" ;
  skos:definition "A legal person." ; skos:altLabel "Counterparty" .
acme:FinancialInstrument a skos:Concept ; skos:prefLabel "Financial Instrument" ;
  skos:definition "A tradable contract." .
"""

_PROJECT = """\
name: t
ontology_stack:
  - name: acme
    layer: core
    type: local
    path: vocab
    format: turtle
    prefixes:
      acme: "https://acme.example/core/"
"""


def _ws(tmp_path: Path) -> tuple[ModelWorkspace, Path]:
    model = tmp_path / "model"
    (model / "vocab").mkdir(parents=True)
    (model / "vocab" / "acme.ttl").write_text(_TTL)
    (model / "mdl-project.yaml").write_text(_PROJECT)
    (model / "conceptual" / "entities").mkdir(parents=True)
    return ModelWorkspace(tmp_path), model


_ENTITY_TMPL = """\
id: 01J000000000000000000000AA
kind: conceptual_entity
name: Counterparty
ontology_layer: core
ontology_refs:
  - uri: {typed}
"""


def _entity(model: Path, typed: str) -> Path:
    p = model / "conceptual" / "entities" / "counterparty.yaml"
    p.write_text(_ENTITY_TMPL.format(typed=typed))
    return p


def test_completion_on_uri_line(tmp_path):
    ws, model = _ws(tmp_path)
    path = _entity(model, "acme:Fin")
    # the uri: line is line index 5 (0-based)
    text = path.read_text().splitlines()
    line = next(i for i, s in enumerate(text) if "uri:" in s)
    items = features.completion(ws, path, line, len(text[line]))
    labels = {i.label for i in items}
    assert any("FinancialInstrument" in lbl for lbl in labels)
    # detail carries label + source
    fin = next(i for i in items if "FinancialInstrument" in i.label)
    assert fin.insert_text and fin.insert_text.startswith("acme:")
    assert "acme" in (fin.detail or "")


def test_completion_empty_uri_returns_all(tmp_path):
    ws, model = _ws(tmp_path)
    path = _entity(model, "")
    text = path.read_text().splitlines()
    line = next(i for i, s in enumerate(text) if "uri:" in s)
    items = features.completion(ws, path, line, len(text[line]))
    labels = {i.label for i in items}
    assert any("Party" in lbl for lbl in labels)
    assert any("FinancialInstrument" in lbl for lbl in labels)


def test_no_completion_off_uri_line(tmp_path):
    ws, model = _ws(tmp_path)
    path = _entity(model, "acme:Fin")
    text = path.read_text().splitlines()
    name_line = next(i for i, s in enumerate(text) if s.startswith("name:"))
    assert features.completion(ws, path, name_line, 10) == []


def test_no_completion_in_non_model_file(tmp_path):
    ws, model = _ws(tmp_path)
    # a yaml file OUTSIDE the model dir must not complete
    other = tmp_path / "notes.yaml"
    other.write_text("ontology_refs:\n  - uri: acme:Fin\n")
    assert features.completion(ws, other, 1, 20) == []


def test_completion_matches_legacy_aligns_to(tmp_path):
    ws, model = _ws(tmp_path)
    p = model / "conceptual" / "entities" / "legacy.yaml"
    p.write_text(
        "id: 01J000000000000000000000BB\n"
        "kind: conceptual_entity\nname: Legacy\n"
        "ontology:\n  layer: core\n  aligns_to: acme:Par\n"
    )
    text = p.read_text().splitlines()
    line = next(i for i, s in enumerate(text) if "aligns_to:" in s)
    items = features.completion(ws, p, line, len(text[line]))
    assert any("Party" in i.label for i in items)
