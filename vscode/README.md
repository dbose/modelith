# Modelith for VS Code

Ontology-anchored, git-native data modeling for dbt teams — inside VS Code,
standalone or in a devcontainer.

## Features

- **Canvas in a tab or your browser** — `Modelith: Open Canvas` spawns
  `mdl serve` and shows the full editor (ER diagram, ontology browser, layers
  view, git changes panel). `modelith.canvas.display` picks `tab` (webview) or
  `external` (browser via a forwarded port — devcontainer-friendly). Both routes
  go through VS Code's port forwarding, so remote/devcontainer just works.
- **Problems-panel validation** — `mdl validate` runs on save; MDL-* diagnostics
  land on the offending YAML line. Status-bar chip shows ✓ / error count.
- **Commands** — Generate dbt Project, Check Drift vs dbt Manifest (breaking
  drift raises an error notification), Lint Naming `--fix`, New Entity, Vendor
  FIBO, Emit Semantic (OSI/MetricFlow), Stop Canvas Server.
- **YAML completion** — JSON Schemas exported from the pydantic IR are
  registered with the Red Hat YAML extension (if installed) per model glob.
- **Auto-discovery** — finds `mdl-project.yaml` and `dbt_project.yml` anywhere
  in the workspace; every location is overridable in settings.
- **mdl detection** — resolves in order: `modelith.mdlPath` (if set) →
  `<workspace>/.venv/bin/mdl` → `mdl` on PATH → the active conda/virtualenv bin
  (`$CONDA_PREFIX`/`$VIRTUAL_ENV`) → well-known installs (`~/.local/bin/mdl` from
  `uv tool install`/`pipx`, homebrew, `/usr/local/bin`) → `uv run mdl`. No
  hardcoded paths — home-relative locations use each user's own `$HOME`. If none
  match (e.g. a nonstandard install), run `which mdl` in the integrated terminal
  and set that as `modelith.mdlPath`.

## Devcontainers

The extension declares `"extensionKind": ["workspace"]`, so in a devcontainer it
runs **inside the container**, next to `mdl`, dbt, and your warehouse
credentials. The canvas server binds in the container; VS Code forwards the
port to your browser or webview automatically. A ready-made devcontainer
template ships in the Modelith repo under `profiles/devcontainer/`.

## Build (from the Modelith repo)

```bash
cd vscode
npm install
npm run build        # typecheck + bundle to dist/
npm run package      # .vsix via vsce
```
