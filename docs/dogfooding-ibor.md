# Dogfooding report: building a pension-fund IBoR with Modelith + real dbt

Role-played end-to-end as a dbt engineer on a pension-fund data team standing up
an Investment Book of Record, with Modelith as the modeling layer and **real dbt
1.12 + duckdb** (no synthetic manifests). Workspace: `/tmp/pension-ibor`
(`model/` + `transform/`, git-tracked).

## What was exercised

1. Authored 5 core entities (`portfolio`, `instrument` SCD2, `position`,
   `transaction`, `price`) + 5 relationships, FIBO-aligned, stewarded.
   `mdl validate` + `mdl ontology check` → 100% core-term coverage.
2. `mdl generate` → dbt project; wired seeds/staging by hand; `dbt build` green
   (after fixes below), including the 5 emitted `relationships` tests passing
   against real data. Queried a real valuation report off the generated views.
3. Hand edits in user regions (`position.sql` note + a `schema.yml` dashboard
   exposure) **survived regeneration**; dbt parses the exposure.
4. Colleague scenario: hand-written `fct_cash_flows`, `broker_code` added and
   `quantity` narrowed to INTEGER *inside generated regions*. `dbt parse` → real
   manifest → `mdl drift --check` classified exactly right: breaking narrow
   (exit 2), unmanaged model, additive column.
5. `mdl drift --reconcile` folded `broker_code` into the model (fresh ULID,
   validates); breaking skipped for the human. Regeneration then **auto-merged**
   `transaction.sql` (colleague's edit converged with the regenerated content)
   and raised a **true conflict** on the contract narrow (MDL-C301, exit 3) —
   never silently resolved. Model won; dbt fixed.
6. `mdl reverse` on the real manifest: staging excluded, SCD2 re-detected,
   surrogates stripped, all 5 relationships recovered at high confidence, the
   colleague's model lifted with sensible types and its FK proposed (not
   auto-accepted). Adopted into the model.
7. Final state: `dbt build` 23 PASS / 0 errors, `mdl drift` → **no drift, exit 0**.

## Verdict on the core promise (§0.1)

**Held.** Nothing hand-written was ever lost; the merge engine converged an
independent hand edit with a reconciled regeneration; the one silent-loss
opportunity (contract narrow vs model) surfaced as an explicit conflict. Drift
against a genuine dbt 1.12 manifest classified every change correctly. The
decision ledger recorded every inference.

**Discovered idiom worth documenting prominently:** bespoke transformation logic
lives in engineer-owned *staging* models; Modelith-generated marts are thin,
contract-enforced projections over them. This resolves "where does hand-written
SQL live" cleanly — the adopted `fct_cash_flows` became
`stg_fct_cash_flows` (engineer SQL) + generated `fct_cash_flows` (contract).

## Bugs found and fixed during the exercise (commit bda1c26)

1. SCD2/hub **contracts omitted pattern system columns** → dbt
   `assert_columns_equivalent` hard-fail. Now single-sourced in
   `core/patterns.py`, shared by emitter + drift projection (which had the
   mirror-image bug: flagging those columns as additive drift).
2. `current_timestamp` is TIMESTAMPTZ on duckdb → contract mismatch with the
   declared TIMESTAMP; macros now cast.
3. **`decimal` mapped to scale-0 types on every platform** — money truncation.
   Now (38,2) everywhere, with tolerant aliases in reverse maps.
4. Drift flagged every `stg_*` model as unmanaged noise; staging is
   engineer-owned by the same rule reverse lifting already applied.
5. Conceptual definitions never reached `schema.yml` `description` (so dbt docs
   were blank and drift saw phantom cosmetic diffs). Now emitted.

## Remaining friction (ranked, for the backlog)

1. **`mdl adopt` / `mdl unmanage` don't exist** but MDL-E201's message tells the
   user to run them. The staging idiom shrinks the need, but the spec'd commands
   should exist or the message should change.
2. **No `mdl new entity/relationship` scaffolder** — authoring means hand-minting
   ULIDs (I scripted it; a modeller can't).
3. `mdl init` doesn't create `logical/relationships/` (or `physical/`,
   `semantic/`) — first relationship write fails with FileNotFoundError.
4. **Conflict resolution is manual text surgery.** A
   `mdl resolve <file> --take-generated|--take-mine` would close the loop.
5. Reverse adoption is whole-repo; adopting *one* unmanaged model into an
   existing repo means copying files by hand, and the reversed conceptual
   filename (`cashflows.yaml` after prefix-stripping) is easy to guess wrong.
6. No CLI to accept/reject **pending ledger proposals** after a non-interactive
   run (`mdl decisions list/accept/reject`).
7. SQL user regions can only hold comments/hints since the generated region is a
   complete SELECT — fine once the staging idiom is understood, but worth
   documenting.
8. dbt seed type changes need `--full-refresh` (dbt behavior, not ours) — the
   drift PR comment could mention it when types drift on seed-backed staging.

## Net

The tool did the one thing that decides adoption: **it never destroyed work and
never lied about a difference**. With the five fixes landed the full
author → generate → build → drift → reconcile → reverse loop runs clean against
real dbt on a realistic financial model.
