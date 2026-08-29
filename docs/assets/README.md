# README assets

The main README references these screenshots that live here.

**Core canvas / editor:**

- `canvas.png`: the full ER canvas, a seven-entity model with crow's-foot relationships.
- `inspector.png`: the entity inspector showing attributes, named keys, an enumerated
  domain, user-defined properties, and relationships.
- `vscode-split.png`: a VS Code window with a model YAML file open on the left and the
  live canvas preview on the right. Capture this from VS Code: open a `.yaml` model
  file, then run "Modelith: Open Model Preview to the Side" from the command palette.

**Ontology / knowledge-graph workflow** (all captured against the IBoR demo, which
auto-starts a bundled mock FIBO server — run `cd demo/ibor && mdl serve -m model`):

- `ontology-browser.jpg`: the ontology browser (⬡ toolbar button) with the source picker
  ("all sources" / "FIBO (demo subset)" chips) and live search results for "financial".
- `ontology-term-detail.jpg`: a term detail card (click a result) showing its definition,
  source, and class hierarchy — e.g. Party In Role with a broader link to Party.
- `inspector-aligned.jpg`: the entity inspector with a single accepted alignment
  (`fibo:FinancialInstrument`, `skos:closeMatch`, via a resolver).
- `inspector-multi-ref.jpg`: the inspector with two `ontology_refs` on one entity — one
  accepted, one proposed with a Promote button.
- `lsp-autocomplete.png`: the LSP completion popup on a `uri:` line in a model YAML,
  suggesting ontology terms with source and definition. Capture from VS Code / Cursor:
  open an entity YAML, add an `ontology_refs:` block, and type on the `uri:` line.

To regenerate the canvas captures, serve any model and capture the browser:

```bash
mdl serve -m <your-model>       # opens the canvas at http://127.0.0.1:4800
```

For `canvas.png`, fit the whole graph to view. For `inspector.png`, click an entity to
open the detail panel. A model that carries a named key group, an enumerated domain, and
user-defined properties shows the inspector at its fullest.
