# Design brief: Modelith animated demo (code-based, for `/design`)

Paste this whole brief into `/design`. It builds **one canvas with 3 artboards**, each an
**animated terminal + ER diagram** demo of one Modelith story. Everything below is real —
real `mdl` commands, real output, real entities — captured from the working CLI. Do not invent
commands or output; use exactly what's here.

## Ground it in the REAL repo (read these first)

This animation must reproduce the **real Modelith Studio canvas**, not an approximation. Before
building, read these in the repo:

- **`demos/design-assets/canvas-reference.jpg`** — a real screenshot of the live Studio canvas
  (the IBOR model). This is the visual target: the exact entity-card look, the amber top border,
  the gold-key PK rows, the dim mono types, the dashed crow's-foot relationship lines, the
  warm-charcoal dotted background. Match it.
- **`demos/design-assets/canvas-tokens.css`** — the REAL design tokens and entity-card CSS,
  extracted verbatim from `canvas/src/styles.css`. Use these exact values and class structure
  for the ER cards in the animation.
- For deeper fidelity, the real source is `canvas/src/styles.css` (the `:root` tokens,
  `.entity-node`, `.entity-header`, `.badge`, `.attr-row`) and `canvas/src/EntityNode.tsx`
  (the card markup: header + badges, a keys section, then the attribute rows).

The terminal panel is a plain dark terminal (same background/mono font); it doesn't need to copy
a specific app chrome.

---

## What to build

A horizontal filmstrip canvas, **3 artboards** (16:9, 1920×1080 each), named:
`Main.dc.html` = **1-Design-first**, `Adopt.dc.html` = **2-Adopt-a-warehouse**,
`Drift.dc.html` = **3-Drift-gate**.

Each artboard is a **split scene**: a terminal panel on the LEFT (~55% width) and a live
**ER diagram** on the RIGHT (~45%). The terminal types commands and reveals output; the ER
diagram animates in sync (entities appear, columns highlight, a change folds in). Animations
loop or play once on view — your call; prefer a ~12–18s timed sequence per artboard that loops.

## Visual identity (match the Modelith product)

- Background `#14110f` (warm charcoal). Panels `#1c1815`, cards `#221d19`, borders `#33291f`.
- Text `#ece6dd`, dim text `#a89a86`. **Accent amber `#e0a63a`** (the brand color).
- Success green `#6cbf73`, warn/amber `#e6a94a`, error `#f0857a`, PK gold `#f2c14e`.
- Monospace for the terminal: "JetBrains Mono", ui-monospace, Menlo. Sans for labels: Inter.
- Terminal prompt is `$`. Typed commands animate character-by-character (~40ms/char feel).
  Output reveals as a block after a short beat. Keep a blinking amber cursor.
- ER entity cards: rounded, dark card, a 3px amber top border, a bold entity name header, then
  attribute rows (name left, type right). PK rows show a gold `PK` chip; FK rows an amber `FK`
  chip. Relationship lines connect FK column to the referenced entity (crow's-foot if easy).

---

## Artboard 1 — "Design-first" (greenfield)

**Tagline (small, top):** "Design in git. Compile to contract-enforced dbt."

**Terminal sequence (type + reveal, in order):**

```
$ mdl model render
# model: shop
entity customer      customer_id [pk], email, full_name, created_at
entity order         order_id [pk], customer_id, order_date, status
entity order_line    order_line_id [pk], order_id, product_id, quantity
entity product       product_id [pk], sku, name, unit_price

$ mdl new entity payment --definition "A settlement against an order"
created entity 'payment' (01M3WX64NZ...)

$ mdl validate
validation passed

$ mdl generate
wrote 9 files to target/dbt

$ mdl export contract
wrote contract to datacontract.yaml
```

**ER diagram animation (synced):**
- As `model render` reveals, the 4 entity cards **fade/slide in**: `customer`, `order`,
  `order_line`, `product`. Draw FK relationship lines: `order.customer_id → customer`,
  `order_line.order_id → order`, `order_line.product_id → product`.
- On `mdl new entity payment`, a **5th card `payment` animates in** (amber glow pulse), with a
  `payment_id [pk]` row (it's a fresh entity, minimal attrs).
- On `mdl validate` → a green "✓ valid" badge pulses over the diagram.
- On `mdl generate` / `export contract` → two small output chips slide in at the bottom:
  a dbt logo-ish chip "dbt • contract enforced" and a "ODCS data contract" chip — the
  "one model, many targets" idea. Keep subtle.

**Entity/attribute data (use verbatim):**
- customer: customer_id (bigint, PK), email (string), full_name (string), created_at (timestamp)
- order: order_id (bigint, PK), customer_id (bigint, FK→customer), order_date (date), status (string)
- order_line: order_line_id (bigint, PK), order_id (bigint, FK→order), product_id (bigint, FK→product), quantity (integer)
- product: product_id (bigint, PK), sku (string), name (string), unit_price (decimal)
- payment: payment_id (bigint, PK)  [appears on `new entity`]

---

## Artboard 2 — "Adopt a warehouse" (brownfield / reverse)

**Tagline:** "Point it at a legacy warehouse. Get a clean model back."

**Terminal sequence:**

```
$ ls models/staging models/marts models/vault
stg_customers.sql  stg_orders.sql  int_order_enriched.sql
dim_customer.sql  dim_product.sql  fct_orders.sql
hub_customer.sql  link_order.sql  sat_customer_details.sql

$ mdl reverse --project target/manifest.json --out owned
reversed 6 entities (4 staging/intermediate excluded)
  kept as business entities (4):  dim_customer, dim_product, fct_orders, hub_customer
  excluded as staging/intermediate (4):  stg_customers, stg_orders, ...
  SCD2 pattern detected (2):  dim_customer, dim_product
  Data Vault detected (3):  hub_customer, link_order, sat_customer_details
  surrogate keys stripped (7)

$ mdl validate -m owned
validation passed
```

**Reverse output must keep the color coding:** "kept as business entities" in GREEN, "managed
but no business key" in AMBER, "excluded" in BLUE-ish, "SCD2 / Data Vault" headers in blue,
"surrogate keys stripped" header in blue. This color-coded classification is the hero of the shot.

**ER diagram animation:**
- Start with a **messy pile** of raw SQL file chips (stg_*, dim_*, fct_*, hub_*, link_*, sat_*)
  scattered/greyed on the right.
- On `mdl reverse`, animate the transform: staging chips **fade out** (excluded); the business
  entities **snap into clean ER cards** — `dim_customer [SCD2]`, `dim_product [SCD2]`,
  `fct_orders`, `hub_customer [HUB]` — with `[SCD2]`/`[HUB]`/`[LINK]` badge tags. Draw FK lines:
  `fct_orders.customer_id → dim_customer`, `fct_orders.product_id → dim_product`.
- Small floating annotations pulse as each pattern is "detected": a tag "SCD2" on dim_customer,
  "Data Vault" on the hub/link/sat group, "surrogate key stripped" briefly on a removed `_sk`
  column. These mirror the real detection.

**Entity/attribute data (verbatim from the real reverse):**
- dim_customer [SCD2]: customer_id (integer, PK), name (string), tier (string)
- dim_product [SCD2]: product_id (integer, PK), sku (string), category (string), list_price (decimal)
- fct_orders: order_id (integer, PK), customer_id (FK→dim_customer), product_id (FK→dim_product), order_date (date), amount (decimal)
- hub_customer [HUB]: customer_id (integer, PK), load_dts (date)
- link_order [LINK], sat_customer_details — shown as tagged cards, fewer attrs

---

## Artboard 3 — "The drift gate" (the climax)

**Tagline:** "The model and the warehouse, always in lockstep."

**Terminal sequence (this is the emotional peak — pace it):**

```
$ mdl drift -m model --manifest target/manifest.json
no drift detected

# a teammate adds a broker_code column to the warehouse
$ mdl drift -m model --manifest target/manifest.json --check
drift vs target 'duckdb_dev':
  [additive] 1
    - column transaction.broker_code exists in dbt but not the model

$ mdl drift -m model --manifest target/manifest.json --reconcile
reconciled 1 delta(s); skipped 0 breaking
  + add column transaction.broker_code

$ mdl drift -m model --manifest target/manifest.json
no drift detected
```

**Color:** `no drift detected` neutral/green; `[additive] 1` in AMBER; `reconciled 1 delta(s);
skipped 0 breaking` in GREEN; `+ add column transaction.broker_code` with a green `+`.

**ER diagram animation (the key moment):**
- Center the **`transaction`** entity card: transaction_id [PK], trade_date, amount,
  portfolio_code [FK→portfolio], counterparty_id [FK→counterparty]. (Optionally small
  portfolio + counterparty cards to its sides with FK lines.)
- Scene 1 — a steady state; a subtle "model ⇄ warehouse in sync" indicator (two panels linked,
  green).
- Scene 2 — a **new `broker_code` row** appears on the WAREHOUSE side only (right), glowing
  AMBER, with a floating "additive" tag. The sync indicator turns amber. This is drift.
- Scene 3 — on `--reconcile`, the `broker_code` row **animates from the warehouse side INTO the
  model's `transaction` card** (slides over, snaps in, turns from amber to normal). The sync
  indicator returns to GREEN. A small "nothing lost" / "✓ reconciled" note pulses.
- End on both sides matching, green, calm.

**Entity data (verbatim):**
- transaction: transaction_id (string, PK), trade_date (date), amount (decimal),
  portfolio_code (string, FK→portfolio), counterparty_id (bigint, FK→counterparty)
- the added column: broker_code (string) — additive, folded in by reconcile.

---

## Overall feel

- Calm, confident, "engineering-grade" — not flashy. Amber accent used sparingly for emphasis
  (the cursor, a badge, a glow on the key moment). Everything else restrained and dark.
- Each artboard should read in one glance (what story) and reward watching (the animation).
- No fake UI chrome that impersonates another product. The terminal + ER diagram are generic
  Modelith surfaces.
- If sound/voice is off the table (it is, in /design), let the on-screen taglines + the typed
  commands carry the narrative.

## Source of truth

These flows are verified real. The matching VHS reference clips and the exact `.tape` scripts
are under `demos/tapes/films/`, and the live commands run against `demos/fixtures/shop`,
`demo/legacy-warehouse`, and `demo/ibor`. Nothing here is mocked.
