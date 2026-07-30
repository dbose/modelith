# Modelith: Collaboration Model

**Spec addendum to `modelith-spec.md`. Target path in repo: `docs/collaboration-model.md`.**

Three personas, one git repo, one truth. This document defines repo topology, ownership, change routes, merge mechanics and the escape hatches that stop the gate becoming a blocker.

---

## 1. The governing principle

Git holds the model. Every surface is a client:

| Surface | Persona | Access mode |
|---|---|---|
| VS Code extension (LSP) | Engineer, Architect | Direct working tree |
| Web app | SME, Steward | Read from ref, write as PR |
| `mdl` CLI | Architect, CI | Direct working tree |
| Drift bot | Nobody | PR only |

No surface owns state. The web app holds a cache keyed on commit SHA and nothing else. If the app's database is deleted, nothing is lost.

---

## 2. Repo topology

### 2.1 Default: one repo, two sibling roots

The model and the transformation code are peers at the repo root. The model is the durable asset; the dbt project is one consumer of it.

```
acme-analytics/                      # one repo
  models/                            # canonical Modelith model
    mdl-project.yaml
    conceptual/  logical/  physical/  semantic/  patterns/
  transform/                         # dbt project(s)
    warehouse/
      dbt_project.yml
      models/                        # dbt SQL, partly generated
      macros/
      target/                        # gitignored
  ontologies/
  apps/                              # SME web app, later
  .mdl/
    state/  decisions.yaml  debt.yaml  lock.yaml
  governance-profile.yaml
  .code-workspace
  .github/
    CODEOWNERS
    workflows/
```

**Rationale for one repo.** The entire thesis is that the model and the code cannot be allowed to diverge. Split repos reintroduce divergence by construction: there is always a window where the model PR is merged and the dbt PR is not. One repo makes the model change and the code change a single atomic commit, one review, one revert. Permissions are handled by CODEOWNERS on paths, not by repo boundaries, because repo boundaries are a blunt instrument applied to an access-control problem git already solves better.

**Rationale for siblings rather than nesting.** Three concrete gains over putting the model inside the dbt project root. The dbt project root stays clean, so `dbt` and the dbt VS Code extensions never parse foreign directories. A second or third dbt project can be added under `transform/` without relocating the model, which matters as soon as a lakehouse target joins a warehouse target. And CI path filters, CODEOWNERS globs and multi-root workspaces all split cleanly along the boundary that already matches the persona split.

**Naming caution.** `models/` at the root and `transform/warehouse/models/` inside dbt are two different things sharing a name at different depths. Expect it to cost time in globs, path filters, grep and conversation. `model/` singular or `semantics/` removes the ambiguity for free. If the name stands, every path must come from `mdl-project.yaml` rather than a hard-coded literal so a later rename is one line.

**Editor setup.** Ship a `.code-workspace` with two roots. Engineers can open `transform/` alone and still get full diagnostics, because the LSP walks up to find `.mdl/`. Architects open the repo root.

### 2.2 Split mode, for the shared-core case

When one conceptual and logical core serves several dbt projects (a conformed enterprise model consumed by three domain repos), the core model becomes its own repo, published as a versioned package and consumed via `mdl-project.yaml`:

```yaml
imports:
  - package: acme/core-model
    version: "2.4.0"        # pinned, never floating
```

Rules for split mode:
- Downstream repos may **extend** imported objects (add specialised-layer terms, add attributes) but never **mutate** them. The validator enforces this.
- A change needed in the core is raised from the downstream repo with `mdl propose upstream`, which opens a PR against the core repo with the downstream context attached.
- Version bumps are a deliberate PR in the downstream repo, so a core change never breaks a domain team without their consent.

Do not use split mode until a second consuming project actually exists. Premature splitting is the most common self-inflicted wound in this pattern.

---

## 3. Ownership and CODEOWNERS

Ownership is per path and mirrors the layer stack.

```
# .github/CODEOWNERS

# Conceptual: the business owns meaning
/models/conceptual/terms/           @data-stewards @business-glossary-council
/models/conceptual/entities/        @data-architects @data-stewards
/models/conceptual/subject-areas/   @data-architects

# Logical: architecture owns structure
/models/logical/                    @data-architects
/models/patterns/                   @data-architects

# Physical model: shared, architects hold the contract
/models/physical/                   @analytics-engineers @data-architects
/models/semantic/                   @data-architects @analytics-engineers

# Transformation code: engineering owns implementation
/transform/                         @analytics-engineers

# Governance mapping and ontology: central, high blast radius
/governance-profile.yaml            @data-governance @data-architects
/ontologies/                        @data-architects
/models/mdl-project.yaml            @data-architects
/.mdl/lock.yaml                     @data-architects

# Generated state: bot-owned, humans should rarely touch
/.mdl/state/                        @data-platform
```

**Decision rights when personas disagree:**

| Question | Decides |
|---|---|
| What does this term mean? | SME / steward |
| Is this one entity or two? | Architect |
| What is the business key? | Architect, with SME input |
| Which platform materialisation, clustering, incremental strategy? | Engineer |
| Is this change breaking? | The tool, from the contract. Not a person. |
| Can we ship without the model updated? | Engineer, via the debt valve in section 7 |

The contract is the negotiated interface. Above the contract is the architect's call, below it is the engineer's, and the contract itself is the only thing both sides have to agree on.

---

## 4. Change routes

Every PR is classified by which paths it touches. `mdl classify` runs first in CI and drives the required checks, reviewers and PR template.

| Route | Trigger paths | Origin | Regeneration | Reviewers | Merge gate |
|---|---|---|---|---|---|
| **A. Meaning** | `models/conceptual/terms/**`, definitions, synonyms, stewards, ontology alignments | SME app | none | 1 steward | validate + ontology check |
| **B. Structure** | `models/logical/**`, `models/patterns/**` | Architect, VS Code | required | 1 architect + 1 engineer | validate + generate + drift clean |
| **C. Implementation** | `transform/**`, `models/physical/**` | Engineer, VS Code | partial | 1 engineer | drift `--check`, contract diff |
| **D. Reconcile** | model files only | Drift bot, nightly | none | 1 architect | validate |
| **E. Governance** | `governance-profile.yaml`, `.mdl/lock.yaml` | Architect / governance | none | governance + architect | conformance kit + `gov plan` |

Route A never requires an engineer. That is the single most important property of this table: a definition fix must not queue behind a sprint.

---

## 5. Persona workflows

### 5.1 SME, via the web app

1. Opens the app, lands on a glossary view scoped to their subject area. Not an ERD.
2. Finds a term. Sees definition, synonyms, steward, ontology alignment, and "where used": the logical entities, dbt models and metrics that realise it.
3. Edits a definition, adds a synonym, flags a term as duplicated, or proposes an ontology alignment.
4. Clicks propose. The app opens a PR on a branch `sme/<user>/<slug>`, with a plain-language body, a rendered before/after of the definition, and a `Co-authored-by` trailer so attribution is the SME, not the bot.
5. Steward reviews in GitHub or in the app's review view. Merge.

**Writable surface for SMEs is deliberately narrow:** definitions, synonyms, examples, stewardship, classification, subject-area membership, ontology alignment proposals. Not cardinality, not keys, not materialisation. A narrow surface means you never have to teach an SME what an identifying relationship is, and the app never produces a PR an architect must reject on principle.

**Ontology alignments are proposals, not commits.** An SME proposing `skos:exactMatch` to a FIBO class writes `alignment_status: proposed`. The validator treats proposed alignments as warnings and an architect promotes them to `accepted`.

### 5.2 Architect, in VS Code

1. Branch. Edit `model/logical/**` as YAML, with LSP completion over domains, ULID references, pattern names and FIBO IRIs.
2. Live preview pane renders the affected subgraph as a diagram while typing. Read-only, like the Markdown preview.
3. `mdl validate` runs continuously through the LSP. Naming violations, missing conceptual realisation, unresolvable IRIs and layer-alignment breaches appear in the Problems panel.
4. `mdl generate` writes dbt SQL and `schema.yml` into protected regions. Diff is reviewed locally before commit.
5. `mdl impact --attribute <name>` before anything risky, to see every downstream model, metric, exposure and governance asset.
6. Push. Route B checks run. An engineer reviews the generated implementation, the architect owns the model change.

### 5.3 Engineer, in VS Code

The design goal is that an engineer who never opens a model file still keeps the model correct.

1. Works in `transform/<project>/models/**` as normal, with `transform/` open as their workspace root. Generated regions are visibly delimited and the extension marks them read-only by default, with an explicit unlock command.
2. Diagnostics surface model violations inline: contract mismatch, dropped column against a declared contract, fan-out risk on a join path, drift from the committed model.
3. Hover on a column returns the glossary definition, ontology IRI, owner and classification. This is where the SME's work reaches the engineer.
4. Quick fixes handle the common cases: add the contract constraint, declare the relationship, adopt this edit into the model, unmanage this block.
5. If the engineer changed something the model should know about, `mdl adopt <file>` folds it back into the model YAML and the PR becomes route B rather than C.

### 5.4 Drift bot

- Nightly on `main`: compare committed model against compiled `manifest.json`.
- Additive and cosmetic drift becomes a route D PR with the model already updated, titled with the change count and assigned to the owning architect via CODEOWNERS.
- Breaking drift never auto-reconciles. It opens an issue and, in enforce mode, fails the next PR that touches the affected path.

---

## 6. Merge mechanics

### 6.1 Semantic merge driver

Line-based merge on model YAML produces nonsense when two people edit the same entity. Ship a git merge driver.

`.gitattributes`:
```
models/**/*.yaml        merge=mdl
.mdl/state/**/*.json    merge=mdl-state
.mdl/decisions.yaml     merge=mdl
```

`mdl merge-driver` resolves ULID-keyed collections structurally: two people adding different attributes to the same entity is not a conflict, two people renaming the same attribute is. Setup is one command, `mdl init --git-hooks`, and CI verifies the driver is configured so a contributor without it cannot silently corrupt a merge.

### 6.2 Shard the generation state

`.mdl/state/generation.json` as one file is a guaranteed merge hotspot on any active repo. Shard it: one small JSON per emitted artifact, path-hashed.

```
.mdl/state/
  a3/f21c8e4b.json      # one emitted file's fingerprint, ULIDs, content hash
  b7/09de1a52.json
```

Two engineers touching different models then touch different state files and never conflict. State is committed, because it is the merge base for the round-trip engine.

### 6.3 Branch protection

- `main` protected, linear history, merge queue on.
- Direct pushes to `main` disabled for everyone including bots.
- Generated dbt files are not protected from editing, because protection is enforced semantically by the round-trip engine, not by git. An engineer who edits a generated block gets `MDL-E201` with an adopt or unmanage path, which is a better outcome than a rejected push.

---

## 7. The debt valve

The gate must never be the reason a release slips, or the tool gets uninstalled inside two quarters.

`mdl unmanage <file> --reason "hotfix INC-4821" --expires 14d`

- Converts generated regions to user regions, so the engineer ships immediately.
- Writes an entry to `.mdl/debt.yaml`, committed, visible.
- Opens a tracking issue assigned to the owning architect.
- After expiry, the item appears in the weekly modeling review and drift `--check` escalates it from warn to error.

Rollout of enforcement, per domain, not per repo:

| Phase | Duration | Drift behaviour |
|---|---|---|
| Observe | 4 weeks | Report only, reconcile PRs open nightly |
| Warn | 4 weeks | PR comment, no failure |
| Enforce additive | 4 weeks | Fail on unmanaged new models |
| Enforce breaking | ongoing | Fail on breaking drift |

A domain does not advance a phase until its reconcile PRs are being merged without argument. That is the readiness signal.

---

## 8. Rituals

- **Weekly modeling review, 30 minutes.** Open route B PRs, expiring debt, proposed ontology alignments awaiting promotion, duplicate-term warnings. Architects plus one engineer plus the relevant steward. This is the forum that replaces the erwin modeling session, and it should be a PR review, not a slide deck.
- **Term-first rule.** No new logical attribute without a conceptual term backing it. Warn in the observe phase, error from enforce. This is what keeps the conceptual layer alive instead of decorative.
- **Definition of done for a model change:** validate clean, generate produces no unreviewed diff, drift clean, contracts declared, glossary term exists, governance plan produces no unexpected asset deletions.

---

## 9. Onboarding a domain: first 30 days

1. **Day 1.** `mdl reverse --project . --interactive` on the existing dbt project. Do not accept everything. Lift one subject area only.
2. **Week 1.** Architect curates that subject area's logical model, declares contracts on its five most-consumed models. Nothing is enforced yet.
3. **Week 2.** Turn on the nightly reconcile PR. Watch whether it gets merged. If it does not, the model is wrong and the phase does not advance.
4. **Week 3.** Point three SMEs at the read-only app with real terms from that subject area. If they cannot find and understand a term unaided, fix the projection before enabling writes.
5. **Week 4.** Enable SME write-as-PR on route A. Move to warn phase on drift.

Expand by subject area, never by repo-wide big bang. The reverse-engineering of a whole project in one shot produces hundreds of unreviewed entities and is the most reliable way to lose the room on day one.

---

## 10. Anti-patterns to design against

- A model repo separate from the dbt repo, before a second consumer exists.
- Hard-coded folder literals anywhere in the emitter or CLI. Every path comes from `mdl-project.yaml`.
- The dbt project treated as the root of the world, with the model as a subdirectory of it.
- The web app holding editable state outside git.
- SMEs given a full ERD editor.
- One monolithic generation state file.
- Enforcement enabled repo-wide on day one.
- Engineers required to open a modeling tool to ship a column.
- Ontology alignment treated as mandatory before the drift loop is trusted.
