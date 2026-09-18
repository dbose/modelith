# Phase 1 onboarding sandbox

A clean-machine harness that measures Modelith's Day 1-30 onboarding acceptance
bars (GTM spec §4) the way a brand-new engineer experiences them: a container with
**no uv, no pipx, no global `mdl`, no project `.venv`** - only a system Python and a
CA bundle, like a fresh laptop. Everything else the engineer installs, the harness
installs too, and times.

## Run it

From this directory, with Docker running:

```bash
docker build -t modelith-onboarding . && docker run --rm modelith-onboarding
```

That builds the clean image and runs the full loop against the **published PyPI
release** (the real blessed path). Each acceptance bar prints `PASS` / `FAIL`, and
gap-#3 friction prints as `NOTE`. The container exits non-zero if any hard bar
fails, so it doubles as a CI check.

## What it asserts (GTM spec §4)

| Bar | Acceptance criterion | How it is measured |
|---|---|---|
| 1 | Fresh machine -> `mdl --help`, under 2 min | install uv, then `uv tool install modelith-dbt`, timed |
| 2 | `mdl init --demo` scaffolds a populated model | asserts >= 6 logical entities |
| 3 | `mdl validate` passes (the activation metric) | run offline, assert exit 0 |
| 4 | `mdl serve` renders a non-empty ERD | probe `/api/model`, assert >= 6 entities |
| 5a | `mdl generate` runs conflict-free | assert exit 0, no "conflict" in output |
| 5b | Full loop -> `dbt build` on DuckDB, no account | run twice: strict path (blocked), then after installing dbt |

## The dbt finding (gap #3)

`dbt-duckdb` is **not** a dependency of `modelith-dbt`. BAR 5b captures both sides
of that on purpose:

- **Strict blessed path**: following only `uv tool install modelith-dbt`, `dbt` is
  not present, so `dbt build` is blocked. This is what a new engineer hits, and the
  harness reports it as an expected `NOTE`, not a hidden failure.
- **After installing dbt**: `uv tool install dbt-core --with dbt-duckdb` (dbt-core
  provides the `dbt` executable; dbt-duckdb is only the adapter), then `dbt build`
  runs green on DuckDB. The install time is printed as the friction cost.

Decision this surfaces for the product: make `dbt-duckdb` an optional extra
(`modelith-dbt[demo]`), document it as a prerequisite for the `dbt build` step, or
leave it. The harness does not assume an answer; it measures the cost (~15s).

## Options

Override at build or run time:

```bash
# test a specific published version
docker build -t modelith-onboarding --build-arg MODELITH_VERSION=0.3.2 .

# test an UNRELEASED branch: build a wheel first, drop it where the harness looks
uv build --wheel                                  # from the repo root -> dist/*.whl
mkdir -p sandbox/onboarding/wheels && cp dist/*.whl sandbox/onboarding/wheels/
docker build -t modelith-onboarding --build-arg INSTALL_SOURCE=wheel \
  --build-context wheels=./wheels .               # or COPY wheels/ in the Dockerfile
docker run --rm -e INSTALL_SOURCE=wheel modelith-onboarding

# loosen the mdl --help time bar (seconds)
docker run --rm -e BAR_HELP_SECONDS=180 modelith-onboarding
```

## Poke around interactively

```bash
docker run --rm -it --entrypoint bash modelith-onboarding
# then, inside:
#   python3 -m pip install --user uv && export PATH="$HOME/.local/bin:$PATH"
#   uv tool install modelith-dbt==0.3.1
#   mdl init --demo demo && cd demo && mdl validate -m model
```

## What this sandbox does NOT test

- The VS Code / Cursor / Windsurf extension UI (zero-config `mdl` detection, the
  one-click "install the CLI for me", the 30-second canvas render). Those need the
  editor and a GUI; the acceptance bar for them is best tested via the
  `profiles/devcontainer` on a cold Codespaces / Gitpod boot.
- The `.devcontainer` feature (`ghcr.io/dbose/features/modelith`), which does not
  exist yet.

## Last known result (PyPI 0.3.1)

All five bars PASS; total wall time ~35s on a warm Docker image, of which ~15s is
the (separate) dbt install. `dbt build`: PASS=29 WARN=0 ERROR=0 on DuckDB, offline.
