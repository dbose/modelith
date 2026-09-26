# Real erwin XML import — parse the actual erwin schema, three visible entry points

## Context

erwin XML import is Modelith's parked GTM wedge: nobody migrates to a modeling tool greenfield, so
"point it at your erwin export and get a working model" is the on-ramp for every team that already
owns an enterprise model. The extracted erwin XSDs now live at `erwin/schemas/` (EMX.xsd is the data
schema, EMXProps.xsd the properties), so we can finally parse the REAL format.

**The existing importer is fiction.** `packages/reverse/src/mdl_reverse/erwin.py::import_erwin` was
written against an INVENTED flat shape (`<Entity name= subject_area=>`, `<Attribute datatype= key=>`,
`parent=`/`child=` by name). Real erwin is nothing like it: namespaced (`http://www.erwin.com/dm/data`),
GUID-referenced, and every object stores its data as child-element TEXT inside a sibling `<XxxProps>`
element — never as XML attributes. It silently "skips unrecognised structure", so on a real export it
produces an empty model. This is a full rewrite, not a patch.

Intended outcome: a data architect exports from erwin, runs one command (or clicks one button), and
gets a committable Modelith model — subject areas, entities, keys, relationships, subtype/supertype,
domains, verb phrases, physical + logical names — as git YAML.

## The real erwin shape (from the XSDs — drives the parser)

- Root `{http://www.erwin.com/dm}erwin` → `{.../dm/data}Model` → `<Object>_Groups` containers (e.g.
  `Entity_Groups` → `Entity`). Every object carries a required `id` GUID attribute.
- **Property values are element text inside `<XxxProps>`**: `<EntityProps><Name>Customer</Name>
  <Physical_Name>CUSTOMER</Physical_Name><Definition>…</Definition></EntityProps>`. Read `.text`, not
  attributes.
- **Cross-refs are GUIDs** (text of a `_Ref` element, or index-ordered `_Ref` children in a
  `_Ref_Array`). Resolve against the id map:
  - Relationship → `Parent_Entity_Ref` / `Child_Entity_Ref` (entities), `Key_Group_Ref`,
    `Parent_To_Child_Verb_Phrase` / `Child_To_Parent_Verb_Phrase`, `Cardinality`, `Type`,
    `Null_Option_Type`, `Subtype_Discriminator_Ref`.
  - Key: per-Entity `Key_Group` (NOT model-level); `Key_Group_Type` discriminates pk/alternate/
    inversion; members via `Key_Group_Member/Attribute_Ref`, ordered.
  - Attribute → `Parent_Domain_Ref` (domain), `Parent_Relationship_Ref` (marks a migrated FK column),
    `Logical_Data_Type`/`Physical_Data_Type`, `Null_Option_Type`, `Name`/`Physical_Name`.
  - Subject_Area → members via `User_Attached_Objects_Ref_Array` + `Auto_Attached_Objects_Ref_Array`
    (mixed types; filter to entity ids).
  - Domain → `Name`, `Logical_Data_Type`, `Parent_Domain_Ref`.
- **Logical vs physical:** erwin never switches to Table/Column — a physical model reuses
  `Entity`/`Attribute` with `Attribute_X` overlays. Always read `Entity`+`Attribute`, prefer
  `Name`/`Logical_Data_Type`, use `Physical_Name`/`Physical_Data_Type` as physical metadata, skip
  `Attribute_X`/`Key_Group_X`.
- **Skip gracefully:** subtype is `Relationship` + `Subtype_Symbol`/`*_Transform` objects (detect via
  relationship `Type`/discriminator, skip the symbols); plus `View`, `ER_Diagram`, `Annotation`,
  DBMS/`Oracle_*`/transform groups, `EM2:Model_Proxy_Object` — iterate-or-skip, never fatal.

## Deliverables

### A. IR additions (small, additive — `packages/core/src/mdl_core/ir.py`)
erwin carries three things the IR doesn't yet; add them so import is faithful and a re-export round-trips.
- `Relationship` gains `verb_phrase: str | None` and `inverse_verb_phrase: str | None` (erwin's
  `Parent_To_Child_Verb_Phrase` / `Child_To_Parent_Verb_Phrase`, e.g. "places" / "is placed by").
- `LogicalEntity` and `Attribute` gain `physical_name: str | None` (erwin's `Physical_Name`; distinct
  from the logical `name`). All optional/defaulted → no existing model or test changes.
- DEFER ER-diagram x/y layout (Modelith auto-layouts via dagre; per-diagram coordinates are a separate
  feature). Surface a warning that layout was dropped.

### B. The real erwin reader (rewrite `packages/reverse/src/mdl_reverse/erwin.py`)
Produce a full `mdl_core.ir.Model` (the RICH family — subject areas, conceptual entities, categories,
domains, keys, relationships), NOT the thin table-oriented `ImportedModel`. Return warnings alongside.
- **Streaming parse for 7-10MB files:** `xml.etree.ElementTree.iterparse(source, events=("end",))`,
  building a lightweight `id -> (type, element-or-extracted-record)` index and calling `elem.clear()`
  (and clearing the root's processed children) as each `<Object>` closes, so a 10MB file never
  materialises as a full DOM. Two logical passes over the index: (1) index + extract entities/attrs/
  keys/domains/subject-areas; (2) resolve relationship + membership + FK-lineage GUID refs. A size
  guard (e.g. warn/refuse absurd files) + a clear parse-error message.
- `import_erwin(text_or_path, *, project_name=None) -> ErwinImport` where
  `ErwinImport = (model: Model, warnings: list[str])`. Map: Entity→ConceptualEntity+LogicalEntity
  (name from `Name`, `physical_name` from `Physical_Name`, `definition`); Attribute→`Attribute`
  (domain via `Parent_Domain_Ref` name, `role=business_key` when in the pk Key_Group, nullable from
  `Null_Option_Type`, `physical_name`); Key_Group(pk/alternate/unique)→`KeyGroup`; Relationship→
  `Relationship` (`from_`=child/many, `to`=parent/one, `verb_phrase`/`inverse_verb_phrase`,
  `identifying` from type/PK-migration, `cardinality`, `optionality`); Subtype→`Category`
  (supertype+subtypes+discriminator, per the IR's existing erwin-category shape); Subject_Area→
  `SubjectArea` with members; Domain→`Domain`. Everything unmapped (views, transforms, layout, UDP
  detail) appends a human warning.
- Reuse: `mdl_core.ids.new_ulid`, the IR classes, and the `_ERWIN_TYPE`/`_base_type` map already in the
  file (keep the datatype mapping, it's fine).

### C. Model → command list adapter (`packages/emit-erd/src/mdl_emit_erd/imports/model.py`, or a new
sibling)
The canvas `/api/import` and the CLI's `_apply_imported` both consume `to_commands`'s `{op,payload}`
list. To give erwin the canvas/warnings/staged-review surfaces WITHOUT flattening it to
`ImportedModel`, add `model_to_commands(model: Model) -> list[dict]` that walks a full IR Model and
emits the same op vocabulary `to_commands` uses (`create_entity` with `conceptual_id`, `add_attribute`,
`create_key_group`, `create_relationship`) PLUS the richer ops the mutation engine already supports for
subject areas and categories (confirm the op names in `mdl_core/commands.py` — `create_subject_area`,
`create_category`, `set_alignment` etc.). Mint stable ULIDs. This is the one new bridging piece; it
lets erwin ride the exact apply/preview path the other importers use.

### D. CLI: real `mdl import erwin` (`packages/cli/src/mdl_cli/main.py`)
Rewrite `import_erwin_cmd` to the rich path with a write-a-model-dir default (the 10MB-friendly,
server-free flow the VS Code command uses):
- `mdl import erwin <file> -o <dir>` → `import_erwin` → `write_model(model, out)` (reuse the
  config-preserving writer from the reverse-warehouse work). Print the warnings as yellow `note:` lines
  (the same channel `_apply_imported` uses). Honour the no-clobber/`--force` divert like `reverse`.
- Keep an `--apply` path that instead routes `model_to_commands` → `apply_command` for importing into
  an EXISTING model (parity with sql/mermaid), but default is write-a-fresh-model (erwin export = a
  whole model, like a reverse).

### E. Canvas Import wizard option (`canvas/src/sme/ImportExportMenu.tsx` + server dispatch)
- Add `{ id: "erwin", label: "erwin XML", hint: "…" }` to `IMPORT_FORMATS` (line ~135) and `.xml` to
  the file `accept` (line ~233).
- Server `/api/import` (`packages/server/src/mdl_server/app.py:218`): add `elif fmt == "erwin":`
  → `import_erwin(content)` → `model_to_commands(model)`, returning `{ok, changes, tables, warnings}`
  exactly as the other branches do. The warnings surface in the existing `.ie-warn` UI and the CLI
  note lines. (For a very large paste, the wizard is fine but the CLI/VS Code file path is the
  recommended route for the biggest exports.)

### F. VS Code: three entry points, NO dedicated frame (`vscode/src/extension.ts` + `package.json`)
Import is a one-shot action, not monitored state, so it does NOT get a panel frame (that would be the
blank-void anti-pattern). It gets maximum discoverability on existing surfaces:
- **New command `modelith.importErwin`** (registered via `cmd()`): `showOpenDialog({filters:{"erwin
  XML":["xml"]}})` → resolve a target dir (reuse `bestDefaultTarget`/`resolveReverseTarget` from the
  reverse flow, so no-clobber + "update in place vs new folder" is consistent) → `runMdl(bin,
  ["import","erwin", file, "-o", target])` under `withProgress` (10MB files take a moment) → on success
  `canvas.open(dir)` and refresh the panels. This alone gives **Cmd+Shift+P → "Modelith: Import erwin
  XML"** for free (palette shows every contributed command).
- **Reverse Review title-bar button:** a `view/title` menu entry `{command: "modelith.importErwin",
  when: "view == modelithReverse", group: "navigation@5"}` with a `$(cloud-download)` icon — erwin
  import is a "bring an external model in" action, so it belongs beside Reverse / Reverse Live /
  Compare, its natural conceptual home.
- **Explorer right-click on `.xml`:** an `explorer/context` menu entry `{command:
  "modelith.importErwin", when: "resourceExtname == .xml", group: "modelith"}` (title "Modelith: Import
  as erwin model"), passing the clicked URI so the command skips the file picker when invoked that way.
  Gate on `.xml` so it doesn't clutter unrelated files.

## Critical files
- `packages/core/src/mdl_core/ir.py` — add `verb_phrase`/`inverse_verb_phrase` (Relationship),
  `physical_name` (LogicalEntity, Attribute).
- `packages/reverse/src/mdl_reverse/erwin.py` — full rewrite: streaming iterparse, two-pass GUID
  resolution, props-as-text, returns `(Model, warnings)`.
- `packages/emit-erd/src/mdl_emit_erd/imports/model.py` — add `model_to_commands(model) -> list[dict]`
  (the rich Model → command-list bridge).
- `packages/cli/src/mdl_cli/main.py` — rewrite `import_erwin_cmd` (write-a-model default + `--apply`).
- `packages/server/src/mdl_server/app.py` — `elif fmt == "erwin"` in `/api/import` (:218).
- `canvas/src/sme/ImportExportMenu.tsx` — `IMPORT_FORMATS` erwin entry + `.xml` accept.
- `vscode/src/extension.ts` — `modelith.importErwin` command (file picker + runMdl + canvas.open).
- `vscode/package.json` — the command + the `view/title` (Reverse) and `explorer/context` (.xml) menus.

## Reuse (do not reinvent — found in exploration)
- IR already has `KeyGroup` (pk/alternate/unique/index), `Category` (subtype/supertype, explicitly
  erwin-shaped, `ir.py:365`), `Domain`, `SubjectArea`, relationship `identifying`/`optionality`, `udp`
  everywhere — erwin maps onto them directly.
- `to_commands` (`imports/model.py:49`) — the op vocabulary + ULID-minting pattern to mirror in
  `model_to_commands`; `mdl_core/commands.py` `apply_command` is the consumer.
- `/api/import` dispatch + `.ie-warn` warnings UI + `IMPORT_FORMATS` (canvas) — the exact insertion
  points for a new format.
- `write_model` (config-preserving, from the reverse-warehouse branch) for the CLI write path;
  `bestDefaultTarget`/`resolveReverseTarget`/`runReverseInto` no-clobber flow for the VS Code target.
- `cmd()` registrar, `runMdl`, `canvas.open(dir)` — the VS Code command plumbing.

## Verification
- **Python (`uv run pytest && uv run ruff check packages/`):**
  - Author a SMALL but REAL-SHAPED erwin fixture XML (namespaced, `<EntityProps><Name>…`, GUID refs,
    a `Key_Group` pk, a `Relationship` with `Parent/Child_Entity_Ref` + verb phrases, a `Subject_Area`
    with attached-object refs, a `Domain`, and one subtype relationship). Assert `import_erwin` yields a
    Model with the right entities/attributes, the pk KeyGroup, the relationship (correct from/to +
    verb_phrase), the subject-area membership, the domain, and the Category — and that unknown groups
    (a `View`, an `ER_Diagram`) are skipped with warnings, not errors.
  - Streaming: a generated LARGE fixture (a few thousand entities, multi-MB) parses within a sane
    memory/time budget (assert it completes; a rough resident-memory sanity check if practical).
  - `model_to_commands`: the imported Model → a command list that `apply_command` accepts and
    reconstructs the same entities/keys/relationships/subject-areas/categories (round-trip assert).
  - CLI: `mdl import erwin <fixture> -o <tmp>` writes a valid model (`mdl validate` passes) and prints
    the warnings.
- **Extension (`cd vscode && npm run typecheck && npm run regression`, Node 20):** all gates incl.
  Gate 3 (the new `modelith.importErwin` command is registered) green.
- **Canvas (`cd canvas && npm run build`, Node 20):** typechecks; the erwin option appears in the
  Import menu.
- **Manual (acceptance):** if a real erwin export is available, `mdl import erwin export.xml -o model`
  produces a browsable model in `mdl studio`; otherwise the real-shaped fixture is the bar. In VS Code:
  Cmd+Shift+P → Import erwin XML, and right-click an `.xml`, both land in the canvas.

## Sequencing (feature branch `feat-erwin-import`; each step green)
1. **A** IR fields (verb phrases, physical names) + a round-trip test. Tiny, unblocks the rest.
2. **B** the streaming reader rewrite + the fixture + parse tests. The core.
3. **C** `model_to_commands` + round-trip test.
4. **D** CLI `import erwin` (write-a-model + `--apply`) + CLI test.
5. **E** server `/api/import` erwin branch + canvas `IMPORT_FORMATS` entry.
6. **F** VS Code command + the three menu surfaces; regression + canvas build.
7. Version bumps + README (an "Import from erwin" section under reverse-engineering).

## Risk posture
- **Real schema, defensively parsed** — unknown groups/props skip with a warning, never fatal, so a
  novel erwin export degrades to a partial model plus notes rather than a crash. The XSDs are the
  ground truth, not a guessed shape.
- **Memory-safe on 10MB** — iterparse + `elem.clear()` keeps the footprint flat; the DOM approach
  (current stub) is explicitly replaced.
- **IR additions are additive** — three optional fields; no migration, no existing test churn, no
  change to how a model without them behaves.
- **No flattening** — erwin's subject areas, categories, domains, verb phrases survive because import
  produces a rich Model and the new adapter carries it to commands, instead of squeezing through the
  table-only `ImportedModel`.
- **No hollow UI** — three entry points on existing surfaces, validated against a dedicated frame
  (which would be the blank-void anti-pattern for a one-shot action).