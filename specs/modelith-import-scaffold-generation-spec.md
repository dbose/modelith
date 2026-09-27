# Separate model import from dbt generation; make the scaffold config CLI-editable and generate-aware

## Context

The erwin-scaffold work (branch `feat-erwin-scaffold`, already committed) made `mdl import erwin`
into an empty folder bootstrap a full project, defaulting to a `workspace` layout that also lays a
DuckDB `transform/warehouse` dbt shell. Review of the codebase (validated) shows this over-couples
concerns and leaves a dead config key:

- The tool already has a clean split: model creation (`scaffold`/`bootstrap_project`) vs dbt-project
  SHELL (`collab.scaffold_workspace_skeleton`) vs dbt MODELS (`mdl generate`, which writes .sql +
  schema.yml into a dbt dir but never creates the shell — [emitter.py:114](packages/emit-dbt/src/mdl_emit_dbt/emitter.py:114)).
- Convention from `mdl init`: plain init = model-only; `--workspace`/`--demo` = model + dbt shell.
- Gap 1: import stamps `config.scaffold.dbt_project_dir`, but `mdl generate` ignores it (reads only
  `--out`, default `target/dbt` — [main.py:299](packages/cli/src/mdl_cli/main.py:299)). The key is dead.
- Gap 2: no global config-edit doorway. Only `reverse-config apply` writes config, hard-scoped to the
  `reverse:` block ([main.py:2306](packages/cli/src/mdl_cli/main.py:2306)). The `scaffold` block can only
  be set at import time, never edited afterward.

Decisions (from the user):
1. **Default layout = `model`** (model-only, no dbt shell). dbt generation stays a separate, explicit
   step. In **VS Code**, surface the layout as a QuickPick at import time (not just a setting).
2. **New general `mdl config` command** (`get`/`set`), reusing the comment-preserving merge machinery,
   so `scaffold.dbt_project_dir` / `scaffold.layout` / `dbt_target` are editable globally after import.
3. **`mdl generate` honors `config.scaffold.dbt_project_dir`** as its default `-o` (explicit `-o` wins),
   so the stamped value drives generation.

Intended outcome: importing an erwin export produces a logical model by default; a dbt project is an
opt-in, separate concern; where that dbt project lives is one config value, editable from the CLI and
consumed by both `generate` and the VS Code workspace wiring.

## Deliverables

### A. Flip the default layout to `model` — `packages/cli/src/mdl_cli/main.py`
`import_erwin_cmd` `--scaffold` default `"model"` (already `model` in code — CONFIRM and keep).
Ensure the docstring/help states model-only is the default and dbt generation is a separate step
(`mdl generate`). No behavioural change for `workspace`/`none`; this is the documented contract.

### B. New `mdl config` command group — `packages/cli/src/mdl_cli/main.py`
Add a `config` typer sub-app mirroring `reverse-config`'s structure, backed by a small generic
merge helper (reuse the `load_file`/`dump_file` comment-preserving round-trip and pydantic validation
pattern from `_write_reverse_block`, [main.py:2275](packages/cli/src/mdl_cli/main.py:2275)):
- `mdl config get [KEY] [-m DIR]` — print the whole `mdl-project.yaml` (or a dotted key like
  `scaffold.dbt_project_dir`). Read-only.
- `mdl config set KEY VALUE [-m DIR]` — set a dotted key, comment-preserving, then validate the whole
  doc through `ProjectConfig.model_validate` before writing (reject a bad set wholesale, like
  `_write_reverse_block` does for ReverseConfig). Support at least `dbt_target`, `platform_targets`
  (comma-split list), `scaffold.layout`, `scaffold.dbt_project_dir`.
- Keep it general (dotted-path set on the loaded doc) but validate against `ProjectConfig` so a typo'd
  key or wrong type is caught. A new helper `_set_config_key(doc, dotted, value)` walks/creates nested
  maps; `_coerce_config_value(dotted, raw)` handles list/bool/None for known keys.
Register: `app.add_typer(config_app, name="config")`.

### C. `mdl generate` honors `scaffold.dbt_project_dir` — `packages/cli/src/mdl_cli/main.py:295`
Change the `--out` option default from `Path("target/dbt")` to `None`; resolve inside the body:
```python
out = out or (Path(repo.model.config.scaffold.dbt_project_dir)
              if repo.model.config.scaffold.dbt_project_dir else Path("target/dbt"))
```
So an explicit `-o` still wins; otherwise the persisted dbt project dir drives it; else the old
default. Update the help text to say so. (This finally consumes the key import stamps.)

### D. VS Code: layout QuickPick at import time + keep settings as defaults — `vscode/src/extension.ts` + `package.json`
- In `modelith.importErwin`, when bootstrapping a NEW project (no existing model), show a
  `vscode.window.showQuickPick` of the three layouts (model | workspace | none) — default-selected
  from the `modelith.import.scaffold` setting — INSTEAD of the current confirm-only modal. The setting
  becomes the default the picker starts on, not the only control. Pass the chosen layout as
  `--scaffold`.
- Keep `modelith.import.dbtTarget` as the `--dbt-target` value (a QuickPick for a free-form target is
  overkill; the setting is fine, could add an inputBox later).
- Everything else (empty-folder handling, set `modelith.modelDir`, workspace → model/ subdir, reused
  dbt project) stays as built.
- `package.json` settings already added; keep them (they now seed the picker default). Update the
  `import.scaffold` description to say it is the DEFAULT selection in the import picker.

### E. Tests + docs + version bump
- Python: `mdl config get/set` round-trips (set `scaffold.dbt_project_dir`, re-read, validate);
  `set` rejects an invalid value; `generate` with no `-o` writes to `scaffold.dbt_project_dir` when
  set (assert the output dir), and to `target/dbt` when unset; `import erwin` default is model-only
  (no `transform/`, no dbt shell), `--scaffold workspace` still lays the shell, existing-dbt reuse
  still holds.
- README: note that import is model-only by default and `mdl generate` (or `--scaffold workspace`)
  handles dbt; document `mdl config set/get` and that `generate` follows `scaffold.dbt_project_dir`.
- Bump CLI `pyproject.toml` (0.6.7 -> 0.6.8) and `vscode/package.json` (0.3.15 -> 0.3.16).

## Critical files
- `packages/cli/src/mdl_cli/main.py` — `config` sub-app (near `reverse-config`, :2271), `generate`
  `-o` default (:295), `import_erwin_cmd` default/help (:2729).
- `packages/cli/src/mdl_cli/scaffold.py` — no change needed to `bootstrap_project` (already returns
  `dbt_project_dir`); confirm default path stays `model`.
- `vscode/src/extension.ts:626` — QuickPick for layout on a fresh bootstrap.
- `vscode/package.json` — refine the `modelith.import.scaffold` description.

## Reuse (found in exploration)
- `_write_reverse_block` (main.py:2275): the load_file → merge → `model_validate` → dump_file pattern
  to mirror for `mdl config set`.
- `mdl_core.yaml_io.load_file`/`dump_file` (comment-preserving) and `ProjectConfig.model_validate`.
- `bootstrap_project` / `find_dbt_project` (scaffold.py) and `scaffold_workspace_skeleton` (collab.py)
  — unchanged; already do model-only vs workspace and reuse an existing dbt project.
- `repo.model.config.scaffold.dbt_project_dir` (ScaffoldConfig, ir.py) — the key generate/config read.

## Verification
- `uv run pytest && uv run ruff check packages/`:
  - `mdl config set scaffold.dbt_project_dir warehouse -m <proj>` then `mdl config get
    scaffold.dbt_project_dir` prints `warehouse`; the doc still `ProjectConfig`-valid; comments kept.
  - `mdl config set dbt_target snowflake_prod` updates it; a bad key/type is rejected.
  - `mdl generate -m <proj>` with `scaffold.dbt_project_dir` set writes into that dir (no `-o`); unset
    → `target/dbt`; explicit `-o` overrides both.
  - `mdl import erwin fx.xml -o <empty>` (no flag) → model-only: `.mdl/lock.yaml` yes, `transform/` NO,
    no dbt shell; `mdl validate` passes. `--scaffold workspace` → shell present. Existing dbt reuse
    still asserts no throwaway DuckDB.
- Extension (`cd vscode && npm run typecheck && npm run regression`, Node 20): all gates green; the
  import command still registered.
- Manual: empty folder in VS Code → Import erwin XML → layout QuickPick appears (default from setting)
  → chosen layout scaffolds; `mdl generate` in that project writes to the recorded dbt dir.

## Sequencing (branch `feat-erwin-scaffold`; each step green)
1. C: `generate` honors `scaffold.dbt_project_dir` (small, unblocks the config story) + test.
2. B: `mdl config get/set` + tests.
3. A: confirm/adjust import default help to model-only + test.
4. D: VS Code QuickPick + package.json description; regression.
5. E: README + version bumps.

## Risk posture
- Default flip to `model` is the documented, less-surprising behaviour (matches `mdl init`); workspace
  is still one flag away. No data-loss risk — import never had a model to clobber in the empty case.
- `generate` change is guarded: explicit `-o` still wins; only the omitted-`-o` default changes, and
  only when `scaffold.dbt_project_dir` is set (else identical to today's `target/dbt`).
- `mdl config set` validates the whole doc through `ProjectConfig` before writing — a bad set is
  rejected, not half-applied (same guarantee as `reverse-config apply`).
- No new dbt-shell writer — dbt-project creation stays solely in `collab.scaffold_workspace_skeleton`,
  invoked only by `init --workspace` and `import --scaffold workspace`.
