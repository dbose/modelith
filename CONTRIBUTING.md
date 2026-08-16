# Contributing to Modelith

Thanks for wanting to help. Modelith is an ontology-anchored, git-native data modeling
tool for dbt-core teams, and it grows better with real-world use and feedback.

## Ways to help

- **Report first-run friction.** If installing or running the quickstart tripped you up,
  file a "First-run friction" issue. Those reports are high priority.
- **File bugs and feature requests** with the issue templates.
- **Improve docs and demos.** The `demo/` projects and the README are the front door;
  clarity there helps everyone.
- **Pick up a good-first-issue.** Look for the `good first issue` label.

## Development setup

You need Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/dbose/modelith
cd modelith
uv sync
uv run pytest                    # the full test suite
uv run ruff check packages/      # lint
```

The CLI is a [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/) of
`mdl_*` packages under `packages/`. Running `mdl` from a `uv sync`ed checkout uses your
working tree, so edits are picked up without reinstalling.

To work on the canvas or the VS Code extension (Node 20):

```bash
cd canvas && npm install && npm run build     # emits into the server static dir
cd vscode && npm install && npm run build     # the extension
```

## Before you open a pull request

- `uv run pytest` passes.
- `uv run ruff check packages/` passes.
- Update the README or `docs/` if you changed behavior.
- Keep the design git-native and stateless: git is the single source of truth, and the
  server, canvas, and extension are all clients over it. Changes that move model state
  out of git, or add server-owned state, need discussion first (open an issue).

## Commit and PR style

- Small, focused PRs are easier to review and land faster.
- Reference the issue you are closing (`Closes #123`).
- Write commit messages that explain the why, not just the what.

## Questions

Open a [Discussion](https://github.com/dbose/modelith/discussions) for anything that is
not yet a concrete bug or feature. It is the best place to sanity-check an idea before
writing code.
