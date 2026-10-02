# Modelith CLI demos

Three short **films** of the `mdl` CLI, each a realistic project followed end to end as one
continuous session — the project is set up once and evolves on screen, no mid-story resets.
Scripted with [VHS](https://github.com/charmbracelet/vhs) (not screen-captured) so every take
is identical, typo-free, re-renderable when the CLI changes, and visually consistent.

**Everything is real** — real `mdl` commands, real `dbt parse`, real output. Changes are shown
on-camera; nothing is faked or staged.

## The three films

### 1 — Design-first (greenfield, ~45s)
From a blank repo to running dbt + a data contract.
`mdl model render` (the designed model) → `mdl new entity` (mints ULIDs) → `mdl validate` →
`mdl generate` (protected regions + an ENFORCED contract) → `mdl export contract` (one model →
an ODCS data contract too). **Story: design in git, compile to more than dbt.**

### 2 — Adopt a warehouse (brownfield, ~55s)
Take over a legacy dbt project that has no data model.
`ls` the warehouse (staging / marts / vault) → `mdl reverse` (real SCD2 / Data Vault /
surrogate-key detection) → `mdl decisions list` (every inference, confidence-tagged, reviewable)
→ `mdl validate` → `mdl model render` (clean owned model). **Story: inherit a mess, own it in
one command, nothing is a black box.**

### 3 — The drift gate (the climax, ~60s)
The model and the warehouse stay in lockstep.
`mdl drift` (clean) → a teammate adds a `broker_code` column to a dbt model + its contract (a
real edit) → `dbt parse` → `mdl drift --check` (real: classified **additive**) → `mdl drift
--reconcile` (real: folds the safe change into the model) → `mdl drift` (clean) + `mdl validate`.
**Story: drift is caught and classified; safe change folds in; work is never lost.**

The Studio **canvas** is a browser UI, not terminal output — recorded separately as a browser
screen capture, not a VHS tape. (Spotlight clips for structural merge, multi-target compile, and
the AI/MCP surface can be added the same way these films are built.)

## Rendering

Requirements (macOS):

```bash
brew install vhs ffmpeg
brew install --cask font-jetbrains-mono
uv sync   # so .venv/bin/mdl exists — the tapes put it on PATH for a fast, clean `mdl`
```

Then:

```bash
demos/render.sh        # render all films → demos/out/*.mp4
demos/render.sh 3      # just film 3
```

## How the tapes work

- **`tapes/_settings.tape`** — shared look: JetBrains Mono 22px, 1280×900 (tall enough that a
  continuous session doesn't scroll earlier scenes off the top), the Modelith amber theme, 70ms
  typing. `Source`d by every film; tune once here.
- Each film has ONE **hidden setup block** (`Hide … Show`) that puts a fast `mdl` on PATH from
  the project venv and copies the film's fixture to a throwaway `/tmp/mdl-film*` dir. After that
  the whole arc runs in one session; the on-screen `clear`s are scene breaks, not resets.
- `setopt interactivecomments` lets the narration `# …` lines show as comments in zsh.
- Timing is **fixed `Sleep`s** sized to the CLI's ~2s startup plus a reading beat (not `Wait`,
  which raced the slow start and echoed keystrokes).
- Fixtures: the bundled `demo/` models (ibor, legacy-warehouse), the greenfield `fixtures/shop`
  model, and `fixtures/add-broker-code.sh` (the real warehouse edit for film 3).

## Re-rendering when the CLI changes

The films drive the real `mdl` + `dbt`, so a change can alter the output. After upgrading:
`uv sync`, then `demos/render.sh`, and watch the new films. If a command's flags changed, edit
the matching `.tape` (commands are plain text) and re-render just that one.
