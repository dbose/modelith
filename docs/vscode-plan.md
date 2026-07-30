# VS Code integration — IMPLEMENTED (see vscode/)

> Original plan retained below for history. Status: shipped in `vscode/` with
> its own npm build (esbuild), packaged as .vsix. Features: canvas as a webview
> tab or external browser via forwarded port (`modelith.canvas.display`),
> Problems-panel diagnostics on save (`mdl validate --format json`), generate /
> drift-check / lint-fix / new-entity / vendor-fibo / emit-semantic commands,
> YAML schemas from the pydantic IR registered with redhat.vscode-yaml,
> auto-discovery of mdl-project.yaml + dbt_project.yml, mdl detection
> (.venv → PATH → uv run), `extensionKind: ["workspace"]` so the extension host
> runs inside devcontainers. Devcontainer template: profiles/devcontainer/.

# VS Code integration plan (standalone + devcontainer)

You asked for Modelith to integrate with VS Code, both standalone and in
devcontainers (the enterprise norm). This document captures the design so it can
be built once the prerequisites land. It is **not yet implemented** — the spec
gates the visual surface (canvas) at M5+, and the richest VS Code features
(inline model view, drift decorations) reuse the same read API the canvas needs.

## Why it slots where it does

The extension is a thin client. Its value comes from `mdl` and the M5 read
`server/`. Building the extension before those exist would mean shelling out to a
CLI that can't yet answer "show me the subgraph for this file" — so the plan
below is layered to deliver value incrementally as each backend milestone lands.

## Architecture

```
vscode/                         # new workspace member (TypeScript)
  extension/                    # the VS Code extension host code
    src/
      client.ts                 # LSP-style client -> `mdl` and (later) read API
      commands.ts               # mdl validate/generate/drift/lint as commands
      diagnostics.ts            # MDL-* codes -> VS Code Diagnostics (squiggles)
      driftDecorations.ts       # M2: gutter/inline drift severity decorations
      canvasView.ts             # M6: webview embedding packages/canvas build
      devcontainer.ts           # env detection + tool bootstrap
  server/                       # optional language server (reuses packages/server)
```

The extension talks to a **Modelith language server** (`mdl lsp`, added in the
CLI) so the same intelligence works from Neovim/JetBrains later. Standalone and
devcontainer differ only in *where the server runs*, not in protocol.

## Feature layering (tracks backend milestones)

| Extension feature | Needs | Milestone |
|---|---|---|
| Syntax highlight + schema for `model/**/*.yaml` (JSON Schema from the IR) | IR (done) | shippable now |
| `mdl validate` on save → Diagnostics with `MDL-*` codes and quick-fixes | M0 (done) | shippable now |
| `mdl lint --fix` as a code action | M0 (done) | shippable now |
| `mdl generate` command + protected-region CodeLens ("owned by Modelith") | M1 (done) | shippable now |
| Drift gutter decorations + PR-style diff view | M2 `mdl drift` | after M2 |
| ULID rename refactor (rename symbol → propagates by ULID) | M0 + LSP | after LSP |
| Embedded read-only canvas webview | M6 canvas + M5 read API | after M5 |

The first four rows are buildable against what exists today and are the sensible
first extension release.

## Standalone

- Ships a bundled `mdl` invocation strategy: prefer a project-local
  `.venv`/`uv run mdl`, fall back to a globally installed `mdl`, else prompt to
  install. Detection lives in `client.ts`.
- Publishes a JSON Schema generated from the pydantic IR
  (`mdl export json-schema`, a small CLI addition) and registers it for
  `model/**/*.yaml` via `yaml.schemas` contribution, so authoring gets
  completion + validation with zero server.

## Devcontainer (enterprise default)

- Ship a `devcontainer-feature` (`ghcr.io/modelith/features/mdl`) that installs
  `uv` + the `mdl` CLI into the container image, so
  `"features": { "ghcr.io/modelith/features/mdl:1": {} }` is all a repo needs.
- The extension declares `extensionKind: ["workspace"]` so it runs **inside** the
  container where `mdl`, dbt, and the warehouse creds live — not on the local UI
  host. This is the critical enterprise detail: model validation and generation
  must execute next to dbt-core, matching spec §1.2 ("same environment as dbt-core").
- Provide a `.devcontainer/devcontainer.json` template under `profiles/` and a
  `postCreateCommand` that runs `uv sync && mdl validate` so a cloned repo is
  immediately in a known-good state.
- Respect the offline constraint (spec §13.4/§14): no phone-home; the JSON Schema
  and FIBO subset are vendored, not fetched.

## Concrete next steps (when picked up)

1. Add `mdl export json-schema` and `mdl lsp` to the CLI (small, backend-only).
2. Scaffold `vscode/extension` with the four "shippable now" features.
3. Author the devcontainer feature + template and test in a Codespaces-style box.
4. Wire drift decorations once M2 lands; wire the canvas webview once M5/M6 land.
