# Adopting Modelith: a step-by-step guide

This guide takes a team from an **empty git repo** to a working, collaborative
data-modeling practice, in the order the personas actually come online:
**platform setup → SMEs → architects → engineers → CI enforcement.**

Every command below has been run end-to-end. Nothing here is aspirational.

> **The one idea to hold onto.** Git holds the model. Every surface — the web
> canvas, the VS Code extension, the `mdl` CLI, the drift bot — is a *client*.
> No surface owns state. Delete the web app's cache and nothing is lost. This is
> why three personas can work the same repo without stepping on each other.

**Contents**

1. [Prerequisites](#1-prerequisites)
2. [Day 0 — Platform lead: scaffold the repo](#2-day-0--platform-lead-scaffold-the-repo)
3. [Understand the topology and ownership](#3-understand-the-topology-and-ownership)
4. [SMEs — meaning, via the web app (route A)](#4-smes--meaning-via-the-web-app-route-a)
5. [Architects — structure, in VS Code (route B)](#5-architects--structure-in-vs-code-route-b)
6. [Engineers — implementation, in VS Code (route C)](#6-engineers--implementation-in-vs-code-route-c)
7. [The drift bot (route D)](#7-the-drift-bot-route-d)
8. [CI: classify-driven checks](#8-ci-classify-driven-checks)
9. [Rolling out enforcement, per domain](#9-rolling-out-enforcement-per-domain)
10. [The debt valve](#10-the-debt-valve)
11. [Rituals and definition of done](#11-rituals-and-definition-of-done)
12. [Command reference](#12-command-reference)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. Prerequisites

Modelith runs in the same environment as dbt-core (Python 3.11+). Install the
CLI so `mdl` is on `PATH` for everyone — this is what makes it available in
every VS Code integrated terminal, standalone or devcontainer:

```bash
# recommended: an isolated tool install (puts `mdl` on PATH)
uv tool install modelith
# or with pipx
pipx install modelith

mdl --help        # sanity check
```

**Devcontainer teams** (the enterprise norm): a ready-made template lives at
`profiles/devcontainer/devcontainer.json`. Copy it to `.devcontainer/` in your
repo. Its `postCreateCommand` installs `uv` + `mdl` inside the container, so the
CLI, the language server, and the canvas all run next to dbt and your warehouse
credentials — not on the laptop. The VS Code extension declares
`extensionKind: ["workspace"]`, so its host runs inside the container too.

**Editors.** Install the **Modelith VS Code extension** (from
`vscode/modelith-vscode-0.1.0.vsix`: `code --install-extension <path>`), and the
Red Hat **YAML** extension for schema-backed completion. Architects and
engineers use VS Code; SMEs use the web app (below) and never install anything.

---

## 2. Day 0 — Platform lead: scaffold the repo

Start from nothing:

```bash
mkdir acme-analytics && cd acme-analytics
git init
mdl init --workspace --name acme_analytics .
```

`--workspace` scaffolds the whole collaboration topology in one shot:

```
  model/ (model repo)
  transform/warehouse/ (dbt project)
  .github/CODEOWNERS
  acme_analytics.code-workspace
  .gitattributes += 3 rule(s)
  git merge drivers configured (merge.mdl, merge.mdl-state)
```

What each piece is:

- **`model/`** — the canonical Modelith model (the durable asset). The seed
  includes one example subject area, entity, and domain so `mdl validate` is
  green from minute one.
- **`transform/warehouse/`** — a dbt project (one consumer of the model). A
  second target (a lakehouse, say) later becomes `transform/lakehouse/` with no
  model relocation.
- **`.github/CODEOWNERS`** — path-based ownership mirroring the layer stack
  (see §3). Replace the placeholder teams (`@data-stewards`, `@data-architects`,
  `@analytics-engineers`, `@data-governance`) with yours.
- **`<name>.code-workspace`** — a two-root VS Code workspace with the Modelith
  settings pre-wired (`modelith.modelDir: model`,
  `modelith.dbtProjectDir: transform/warehouse`).
- **`.gitattributes` + git config** — the **semantic merge driver** (§6.1 of the
  collaboration model). This is what lets two people edit the same entity without
  a nonsense line-based conflict.

Commit the scaffold and set up branch protection on `main` (GitHub side):
protected branch, linear history, merge queue on, **direct pushes disabled for
everyone including bots**. Generated dbt files are *not* protected from editing —
protection is enforced semantically by the round-trip engine (an engineer who
edits a generated block gets a helpful `MDL-E201`, not a rejected push).

```bash
git add -A
git commit -m "Scaffold Modelith collaboration workspace"
git branch -M main
git remote add origin <your-remote> && git push -u origin main
```

> **CI must verify the merge driver is configured**, because a contributor
> without it can silently corrupt a model merge. Anyone cloning fresh runs
> `mdl init --git-hooks` once (it is idempotent).

---

## 3. Understand the topology and ownership

```
acme-analytics/                      # one repo, two sibling roots
  model/                             # canonical Modelith model
    mdl-project.yaml
    conceptual/  logical/  physical/  semantic/  patterns/
    .mdl/  state/ ⋯ decisions.yaml  debt.yaml  lock.yaml
  transform/
    warehouse/                       # a dbt project
      dbt_project.yml  models/  macros/  target/  (target gitignored)
  ontologies/
  governance-profile.yaml
  .github/CODEOWNERS
```

**Ownership is per path, and mirrors the layer stack:**

| Path | Owner | Persona |
|---|---|---|
| `model/conceptual/terms/` | `@data-stewards` | **SME** owns meaning |
| `model/conceptual/entities/` | `@data-architects @data-stewards` | shared |
| `model/logical/`, `model/patterns/` | `@data-architects` | **Architect** owns structure |
| `model/physical/`, `model/semantic/` | `@analytics-engineers @data-architects` | shared; architects hold the contract |
| `transform/` | `@analytics-engineers` | **Engineer** owns implementation |
| `governance-profile.yaml`, `ontologies/`, `model/mdl-project.yaml` | `@data-architects @data-governance` | high blast radius |

**Decision rights when personas disagree:**

| Question | Decides |
|---|---|
| What does this term *mean*? | SME / steward |
| Is this one entity or two? | Architect |
| What is the business key? | Architect, with SME input |
| Which materialisation / clustering / incremental strategy? | Engineer |
| Is this change *breaking*? | The tool, from the contract. Not a person. |
| Can we ship without the model updated? | Engineer, via the debt valve (§10) |

The **contract** is the negotiated interface. Above it is the architect's call,
below it is the engineer's, and the contract is the only thing both must agree
on.

---

## 4. SMEs — meaning, via the glossary app (route A)

**Goal:** an SME who never touches git *or* a CLI keeps the glossary correct, and
a definition fix never queues behind a sprint.

The glossary app is **git-native** — it reads the repo, edits the working tree,
and opens a pull request, all hidden behind one "Submit for review" button. The
SME sees a glossary, not a terminal and not an ERD. It is a *client* over git;
delete its cache and nothing is lost (the model is git).

### 4.1 Stand up the glossary

The platform lead serves it (inside the container, if devcontainer):

```bash
mdl glossary -m model                    # http://127.0.0.1:4810/sme
# read-only for the onboarding week-3 gate (browse only, no editing):
mdl glossary -m model --read-only
```

Start read-only. SMEs land on a **term list scoped to their subject area**,
search, and open a term to see its definition, synonyms, steward, standard
mapping, and **where it's used** (the dbt models that realise it). **If they
cannot find and understand a term unaided, fix the projection before enabling
writes.** That is the week-3 readiness gate (§9).

### 4.2 What an SME can change (deliberately narrow)

The app exposes exactly: **definition, synonyms, stewardship (owner/steward),
subject-area membership, and an ontology-alignment *proposal*.** There is no
control anywhere in the app for cardinality, keys, attributes, relationships, or
materialisation. A narrow surface means you never teach an SME what an
identifying relationship is, and the app never produces a PR an architect must
reject on principle.

### 4.3 The SME workflow (all in the app)

1. Open **http://…/sme** on a glossary scoped to your subject area (not an ERD).
2. Find a term → see definition, synonyms, steward, standard mapping, and **where
   it's used**.
3. Click **Suggest an edit**. Refine the definition, add/remove synonyms, set the
   steward, or **Propose a mapping…** to a standard. Each change is collected in a
   change tray — nothing is written yet.
4. Click **Submit for review**. A dialog shows a plain-language **before/after** of
   every change; enter your name and a title, then confirm. The app:
   - branches `sme/<user>/<slug>` from a clean base,
   - applies your changes to the model YAML (comment-preserving),
   - commits with your note and a **`Co-authored-by`** trailer (attribution is you),
   - pushes and opens a **pull request** (via `gh`), or — with no `gh`/remote —
     gives you the compare link.
5. A **steward** reviews and merges. **No engineer is involved.** The alignment you
   proposed rides as `status: proposed`; an architect promotes it later.

> Verified end-to-end: an SME editing "Counterparty" via the app lands the change
> on `sme/a-hough/clarify-counterparty` with a `Co-authored-by: a.hough` commit —
> no git or CLI touched.

### 4.4 The same acts on the CLI (platform lead only, for seeding/scripting)

An SME never runs these. A platform lead can seed the glossary directly:

```bash
# a glossary term is the SME's primary object
mdl new term "Obligor" -m model \
  --definition "A party that owes a financial obligation to the firm." \
  --layer domain \
  --aligns-to fibo-fnd-pty-pty:PartyInRole
```

**Ontology alignments are proposals, not commits.** The command above writes
`status: proposed`. `mdl validate` then emits a warning, not an error:

```
MDL-W206 [warning] 'Obligor': ontology alignment to 'fibo-fnd-pty-pty:PartyInRole'
  is proposed — awaiting architect promotion (mdl ontology promote)
```

An architect promotes it (§5.2):

```bash
mdl ontology promote Obligor -m model      # status -> accepted, warning clears
```

### 4.5 Optional but recommended: vendor an ontology

So alignments resolve to real definitions in hovers and the layers view:

```bash
mdl ontology vendor fibo -m model          # downloads + pins the FIBO release
# ...or a curated subset ships in-repo at ontologies/industry/fibo-subset/
```

FIBO is only *one example* — ACORD, FHIR, ISO 20022, GS1, or your own RDF/Turtle
vocabulary plug in by a declaration in `model/mdl-project.yaml` (`ontology_stack`),
no code.

---

## 5. Architects — structure, in VS Code (route B)

**Goal:** the architect owns the logical model and the contract; the diagram is
a *preview*, the source of truth is YAML.

### 5.1 Open the workspace

```bash
code acme_analytics.code-workspace     # two roots: model + transform
```

The status bar shows **◮ Modelith ✓**, meaning the language server started and
the model validates.

### 5.2 Author the logical model

Create objects with ULIDs minted for you (never hand-mint IDs):

```bash
mdl new entity portfolio -m model \
  --definition "A mandate within the scheme with its own strategy and benchmark."
```

Then edit `model/logical/entities/portfolio.yaml` — the YAML extension gives
completion over domains, roles, and pattern names from Modelith's JSON Schemas.
Add attributes, mark the business key:

```yaml
attributes:
  - id: 01J...            # minted; never change it
    name: portfolio_code
    domain: string
    role: business_key
    nullable: false
```

As you type, the **Problems panel** surfaces `MDL-*` diagnostics live —
unresolvable IRIs, missing conceptual realisation, layer-alignment breaches,
naming violations. This is `mdl validate` running continuously through the LSP.

### 5.3 The preview pane (not an editor)

Right-click a model file → **Modelith: Open Model Preview to the Side**, or use
the command palette. The ER diagram opens beside your YAML, like the Markdown
preview, and re-centres on whatever entity you're editing. Read-only by design —
your source is the text.

### 5.4 See impact before anything risky

```bash
mdl drift --manifest transform/warehouse/target/manifest.json -m model --check
```

Before a structural change, know every downstream model, metric, and governance
asset it touches. (Compile the dbt project first with `dbt parse` — no warehouse
needed.)

### 5.5 Generate the dbt implementation

```bash
mdl generate -m model -o transform/warehouse
```

This writes dbt SQL and `schema.yml` into **protected regions** and records the
generation state as **sharded** JSON (`transform/warehouse/.mdl/state/ab/….json`,
one file per artifact — so two architects touching different models never
conflict on state). Review the diff locally, then commit.

Push. A **route B** PR requires **1 architect + 1 engineer**: the architect owns
the model change, an engineer reviews the generated implementation.

### 5.6 Promoting SME proposals

Part of the weekly review: promote proposed alignments that check out.

```bash
mdl ontology check -m model            # coverage report + the proposed list
mdl ontology promote Counterparty -m model
```

---

## 6. Engineers — implementation, in VS Code (route C)

**Goal (the design target):** an engineer who **never opens a model file** still
keeps the model correct.

### 6.1 Open just the dbt project

Engineers open `transform/warehouse/` as their workspace root. The LSP walks up
to find `model/`, so they get full diagnostics without seeing model YAML.

### 6.2 Model violations arrive as squiggles

Working in `transform/warehouse/models/**`, the Problems panel surfaces:

- **contract mismatch** — a column type that no longer matches the declared
  contract (a real dbt build failure, caught before you run dbt)
- **dropped column** against a declared contract (breaking drift)
- **fan-out risk** on a join path
- **drift** from the committed model
- **edited generated block** — `MDL-E201`, live, the moment you edit
  Modelith-owned SQL (not weeks later in CI)

Generated regions are visibly delimited; a CodeLens marks them **◮ Modelith-owned**.

### 6.3 Hover carries the SME's knowledge

Hover any column in a `.sql` file or `schema.yml`:

> **counterparty_id** · `bigint` · 🔑 business key · **not null**
> **Counterparty** — A legal person the firm transacts with.
> - ontology: `fibo-fnd-pty-pty:PartyInRole` (Party In Role) · skos:exactMatch · layer **core**
> - owner: **risk-data-office** · steward: **a.hough**

This is where the SME's and architect's work reaches the engineer. No tool does
this today.

### 6.4 Quick fixes for the common cases

Code actions on the diagnostics handle the routine work without opening a model
file:

- **Adopt column into model** — an additive column you added in dbt gets folded
  into the model YAML (ULID minted). Your PR becomes route B.
- **Declare relationship** — from a `*_id` foreign-key hint.
- **Lift to logical model** — right-click one un-modeled dbt model to lift it
  (selection-scoped: *one* model, never a whole-project sweep).
- **Unmanage this block** — hand the SQL to engineers permanently (§10).

If you changed something the model should know about, `mdl adopt`/the quick fix
folds it back and your PR is route B rather than C.

A **route C** PR requires **1 engineer** and passes `mdl drift --check` +
the contract diff.

---

## 7. The drift bot (route D)

Nightly on `main`, the bot compares the committed model against the compiled
`manifest.json`:

- **Additive / cosmetic drift** → opens a **route D** PR with the model already
  updated, titled with the change count, assigned to the owning architect via
  CODEOWNERS. Route D requires only `mdl validate`.
- **Breaking drift** → never auto-reconciles. Opens an issue; in enforce mode,
  fails the next PR touching the affected path.

The reconcile PR is the adoption mechanism: teams that never open the modeling
tool still keep the model current. **Watching whether these PRs get merged is
the readiness signal** for advancing enforcement (§9).

Ship it as a scheduled workflow (see `profiles/ci/mdl-drift.yml` and the
`--reconcile` mode):

```bash
mdl drift --manifest transform/warehouse/target/manifest.json -m model --reconcile
```

---

## 8. CI: classify-driven checks

`mdl classify` runs **first** in CI and drives everything else. It reads the
paths a PR touches and prints the route, the required gates, and the reviewers.

```bash
mdl classify --base origin/main
```

Example — a PR touching both a logical entity and its dbt SQL:

```
routes: B (Structure), C (Implementation)  ->  primary B
gates:     mdl validate; mdl generate --dry-run; mdl drift --check
reviewers: @data-architects, @analytics-engineers
```

The strictest gate leads a mixed PR (B > E > C > A). **Route A never pulls in an
engineer** — a definition fix runs only `mdl validate` + `mdl ontology check`
with one steward.

Copy the shipped templates into `.github/workflows/`:

| Template | Runs on | Purpose |
|---|---|---|
| `profiles/ci/mdl-classify.yml` | every PR | classify → comment routes → run the route's gates |
| `profiles/ci/mdl-validate.yml` | model PRs | validate + ontology check |
| `profiles/ci/mdl-drift.yml` | dbt PRs | drift `--check`, PR comment with Mermaid diff |
| `profiles/ci/mdl-gov-sync.yml` | merge to `main` | governance plan → approve → apply |

The routes at a glance (collaboration model §4):

| Route | Trigger paths | Reviewers | Merge gate |
|---|---|---|---|
| **A. Meaning** | `model/conceptual/**`, definitions, alignments | 1 steward | validate + ontology check |
| **B. Structure** | `model/logical/**`, `model/patterns/**` | 1 architect + 1 engineer | validate + generate + drift clean |
| **C. Implementation** | `transform/**`, `model/physical/**` | 1 engineer | drift `--check`, contract diff |
| **D. Reconcile** | model files only (bot) | 1 architect | validate |
| **E. Governance** | `governance-profile.yaml`, `.mdl/lock.yaml` | governance + architect | conformance + `gov plan` |

---

## 9. Rolling out enforcement, per domain

**Never enable enforcement repo-wide on day one.** Advance per domain, and only
when its reconcile PRs are being merged without argument — that is the readiness
signal.

| Phase | Duration | Drift behaviour |
|---|---|---|
| **Observe** | 4 weeks | Report only; reconcile PRs open nightly |
| **Warn** | 4 weeks | PR comment, no failure |
| **Enforce additive** | 4 weeks | Fail on unmanaged **new** models |
| **Enforce breaking** | ongoing | Fail on **breaking** drift |

### Onboarding a domain: the first 30 days

1. **Day 1.** `mdl reverse --project transform/warehouse/target/manifest.json --out model --interactive`
   on the existing dbt project. **Do not accept everything. Lift one subject area
   only.** A one-shot reverse of 400 models produces 400 unreviewed entities and
   loses the room on day one.
2. **Week 1.** Architect curates that subject area's logical model, declares
   contracts on its five most-consumed models. Nothing is enforced yet.
3. **Week 2.** Turn on the nightly reconcile PR. Watch whether it merges. If it
   does not, the model is wrong — the phase does not advance.
4. **Week 3.** Point three SMEs at the read-only app with real terms from that
   subject area. Fix the projection before enabling writes.
5. **Week 4.** Enable SME write-as-PR on route A. Move to **warn** phase on drift.

Expand by **subject area, never by repo-wide big bang.**

---

## 10. The debt valve

The gate must never be the reason a release slips. When an engineer needs to
ship SQL the model can't yet describe:

```bash
mdl unmanage counterparty -m model --reason "hotfix INC-4821" --expires 14d
```

- The entity **stays in the model** (lineage, governance, the canvas all keep
  it) but its SQL becomes **engineer-owned** — the emitter stops regenerating
  that file.
- Writes a **committed, visible** entry to `model/.mdl/debt.yaml`.
- The engineer ships immediately; file a tracking issue for the owning architect.

```bash
mdl debt list -m model
#   [open] counterparty: hotfix INC-4821 (since 2026-07-31, expires 2026-08-14)
```

After expiry, the item surfaces in the weekly modeling review, and
`mdl drift --check` **escalates it from warn to error** — so debt is impossible
to forget.

---

## 11. Rituals and definition of done

- **Weekly modeling review (30 min).** Open route B PRs, expiring debt, proposed
  alignments awaiting promotion, duplicate-term warnings. Architects + one
  engineer + the relevant steward. This *replaces the erwin modeling session* —
  it should be a PR review, not a slide deck.
- **Term-first rule.** No new logical attribute without a conceptual term backing
  it. Warn in observe, error from enforce. This keeps the conceptual layer alive
  instead of decorative.
- **Definition of done for a model change:** `validate` clean · `generate`
  produces no unreviewed diff · `drift` clean · contracts declared · glossary
  term exists · `gov plan` shows no unexpected asset deletions.

---

## 12. Command reference

| Command | Who | What |
|---|---|---|
| `mdl init --workspace --name <n> .` | platform | scaffold the full topology |
| `mdl init --git-hooks` | anyone (post-clone) | wire the semantic merge driver |
| `mdl serve -m model [--read-only]` | architect/engineer | the ER canvas (editor or viewer) |
| `mdl glossary -m model [--read-only]` | SME (platform lead runs it) | the git-native glossary app (edits → PR) |
| `mdl lsp` | editors (auto) | the language server |
| `mdl new term\|entity\|subject-area <n> -m model` | SME / architect | scaffold an object (ULIDs minted) |
| `mdl ontology search\|check\|promote\|vendor` | SME / architect | vocabulary + alignment lifecycle |
| `mdl validate -m model [--format json]` | all / CI | schema, refs, ontology, naming |
| `mdl generate -m model -o transform/warehouse` | architect | emit dbt (protected regions) |
| `mdl drift --manifest <m> -m model --check\|--reconcile` | engineer / bot | drift classify + reconcile |
| `mdl reverse --project <m> --out model --interactive` | architect (onboarding) | lift a dbt project (one subject area) |
| `mdl classify --base origin/main` | CI | route a change set (A–E) |
| `mdl unmanage <entity> -m model --reason <r> --expires 14d` | engineer | the debt valve |
| `mdl debt list -m model` | all | the committed debt ledger |
| `mdl emit semantic --format osi\|metricflow -m model` | architect | semantic layer |
| `mdl gov plan\|apply\|conformance` | governance | catalog sync |
| `mdl merge-driver` | git (auto) | structural model-YAML merge |

Exit codes (for CI): `0` ok · `1` validation error · `2` breaking drift ·
`3` merge conflict · `4` governance/adapter failure.

---

## 13. Troubleshooting

**`mdl: command not found` in a VS Code terminal.**
Install it on PATH (`uv tool install modelith`). Standalone, that's enough. In a
devcontainer, the template's `postCreateCommand` handles it; the extension also
prepends the detected `mdl` directory to every integrated terminal's PATH. As a
fallback, set `modelith.mdlPath` in settings.

**The status bar shows `◮ Modelith ?` (question mark).**
The language server failed to start — usually `mdl` isn't found. Check the
**Modelith** output channel; run **Modelith: Restart Language Server** after
fixing the path.

**A merge produced a garbled model file.**
The contributor's clone is missing the merge driver. Run `mdl init --git-hooks`
and re-do the merge. CI should verify the driver is configured to prevent this.

**Drift flags every `stg_*` model as unmanaged.**
It shouldn't — staging/intermediate models are engineer-owned by convention and
excluded. If you see this, your staging models don't match the `stg_`/`int_`
prefix or a `staging`/`intermediate` tag; add the tag or rename.

**An SME's alignment shows a warning forever.**
It's `status: proposed` and needs an architect to run `mdl ontology promote
<name>`. That's the intended gate (§4.4), not a bug.

**`mdl reverse` created hundreds of noisy entities.**
You accepted a whole-project sweep. Delete them, and re-run with
`--interactive`, lifting **one subject area**. This is the single most common
first-run mistake.
```
