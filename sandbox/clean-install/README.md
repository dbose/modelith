# Clean-install verification (core-only)

A Docker harness that runs Modelith's logical flows in the install configuration our
dev workspace hides: a plain `pip install modelith-dbt` with **no extras**.

## Why this exists

`uv run` in the workspace always has every optional dependency (pyoxigraph, the RDF
backend, included). So a command that reaches the optional ontology stack passes
locally and crashes on a real core-only install. That is precisely what shipped:
`mdl import erwin` into an empty folder scaffolds `.mdl/lock.yaml` via
`mdl_ontology.lock.Lock`, which used to drag in the pyoxigraph backend — absent on a
core install — and died with `ModuleNotFoundError: No module named 'pyoxigraph'`. A
second bug (an erwin domain named `<root>` producing a Windows-illegal filename) also
only shows on a non-macOS filesystem.

This harness runs the flows where a real user runs them, so that class of
"green in dev, broken on install" bug is caught before shipping.

## Run it

From the repo root, with Docker running:

```bash
./sandbox/clean-install/run.sh
```

It builds the current wheel (`uv build`), builds the clean image, and runs the
core-only verification with the wheel mounted at `/tmp/wheels`. Exits non-zero on any
failure, so it doubles as a pre-ship / CI gate.

## What it asserts

| Check | What it proves |
|---|---|
| `pyoxigraph` absent | the env is genuinely core-only (the test is meaningful) |
| `mdl --version` | the CLI imports without the optional stack |
| `mdl import erwin` (empty folder) | the scaffold path runs without the ontology extra |
| `.mdl/lock.yaml` written | `Lock` is reachable without pyoxigraph |
| `<root>` domain → safe filename | filesystem-illegal names don't crash the writer |
| `mdl validate` | the imported model is valid on a core install |

## Scope

Today it covers the **erwin import** flow on a core-only install (the flows that
broke). Extend `run-clean.sh` with more bars (init --workspace, config set/get,
generate) and add a second `pip install 'modelith-dbt[ontology]'` pass when broader
coverage is wanted. Not yet wired into CI — run it by hand before shipping; a
GitHub Actions job is the natural follow-up.
