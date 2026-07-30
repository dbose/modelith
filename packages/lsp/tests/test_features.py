"""LSP feature-builder tests: diagnostics, hover, lens, actions, commands —
pure functions over ModelWorkspace, no LSP transport needed."""

from __future__ import annotations

from lsprotocol import types as lsp
from mdl_lsp import commands as cmds
from mdl_lsp import features


def _codes(diag_map):
    return {str(d.code) for diags in diag_map.values() for d in diags}


# --- discovery ----------------------------------------------------------------


def test_workspace_discovery(workspace):
    assert workspace.model_dir is not None and workspace.model_dir.name == "model"
    assert workspace.dbt_dir is not None and workspace.dbt_dir.name == "transform"
    assert workspace.manifest is not None
    assert workspace.entity_for_dbt_model("counterparty") is not None


# --- diagnostics ---------------------------------------------------------------


def test_model_diagnostics_clean_then_broken(workspace):
    assert features.model_diagnostics(workspace) == {}

    f = workspace.model_dir / "logical" / "entities" / "counterparty.yaml"
    f.write_text(f.read_text().replace("realises:", "realises: 01BROKEN00000000000000000X\n#"))
    diag_map = features.model_diagnostics(workspace)
    assert "MDL-E102" in _codes(diag_map)
    # mapped to the file that declares the dangling ref
    assert any(p.name == "counterparty.yaml" for p in diag_map)


def test_dbt_diagnostics_drift_on_dbt_files(workspace):
    diag_map = features.dbt_diagnostics(workspace)
    codes = _codes(diag_map)
    # additive column -> warning on schema.yml at the column line
    assert "MDL-DRIFT-column_added" in codes
    schema = next(p for p in diag_map if p.name == "schema.yml")
    added = next(d for d in diag_map[schema] if d.code == "MDL-DRIFT-column_added")
    assert "rating_grade" in added.message
    assert added.range.start.line > 0  # found the actual line, not line 0
    # unmanaged hand model -> info on its own .sql
    assert "MDL-DRIFT-unmanaged_model" in codes
    assert any(p.name == "fct_hand.sql" for p in diag_map)


def test_edited_generated_block_surfaces_on_sql(workspace):
    sql = workspace.dbt_dir / "models" / "trade.sql"
    sql.write_text(sql.read_text().replace("    trade_id\nfrom", "    trade_id -- HAND EDIT\nfrom"))
    diag_map = features.dbt_diagnostics(workspace)
    per_file = {p.name: [str(d.code) for d in ds] for p, ds in diag_map.items()}
    # live warning on the .sql the engineer edited (model unchanged -> E201)
    assert "MDL-E201" in per_file.get("trade.sql", [])
    # ...and on schema.yml, where the colleague edited the generated contract
    assert "MDL-E201" in per_file.get("schema.yml", [])


# --- hover ---------------------------------------------------------------------


def test_hover_sme_card_on_sql_column(workspace):
    sql = workspace.dbt_dir / "models" / "counterparty.sql"
    text = sql.read_text()
    line = next(i for i, ln in enumerate(text.splitlines()) if "counterparty_id" in ln)
    col = text.splitlines()[line].index("counterparty_id") + 2
    h = features.hover(workspace, sql, line, col)
    assert h is not None
    md = h.contents.value
    assert "business key" in md
    assert "risk" in md  # owner from stewardship
    assert "fibo-fnd-pty-pty:PartyInRole" in md  # ontology IRI
    assert "Counterparty" in md  # glossary term


def test_hover_in_schema_yml_resolves_enclosing_model(workspace):
    sy = workspace.dbt_dir / "models" / "schema.yml"
    text = sy.read_text()
    line = next(i for i, ln in enumerate(text.splitlines()) if "name: legal_name" in ln)
    col = text.splitlines()[line].index("legal_name") + 2
    h = features.hover(workspace, sy, line, col)
    assert h is not None and "legal_name" in h.contents.value


# --- code lens ------------------------------------------------------------------


def test_code_lens_ownership_and_lift(workspace):
    owned = features.code_lens(workspace, workspace.dbt_dir / "models" / "counterparty.sql")
    assert any("Modelith-owned" in lens.command.title for lens in owned)

    hand = features.code_lens(workspace, workspace.dbt_dir / "models" / "fct_hand.sql")
    assert any(lens.command.command == "mdl.lift" for lens in hand)


# --- code actions ----------------------------------------------------------------


def test_actions_from_diagnostics(workspace):
    diag_map = features.dbt_diagnostics(workspace)
    schema = next(p for p in diag_map if p.name == "schema.yml")
    actions = features.code_actions(workspace, schema, diag_map[schema])
    titles = [a.title for a in actions]
    assert any("Adopt column 'rating_grade'" in t for t in titles)

    hand = next(p for p in diag_map if p.name == "fct_hand.sql")
    actions = features.code_actions(workspace, hand, diag_map[hand])
    assert any(a.command.command == "mdl.lift" for a in actions)


# --- commands (the write path) ----------------------------------------------------


def test_lift_model_selection_scoped(workspace):
    msg = cmds.lift_model(workspace, str(workspace.dbt_dir / "models" / "fct_hand.sql"))
    assert "lifted" in msg
    assert (workspace.model_dir / "logical" / "entities" / "fct_hand.yaml").exists()
    le = workspace.entity_for_dbt_model("fct_hand")
    assert le is not None
    # lifting is selection-scoped: nothing else was created
    assert not (workspace.model_dir / "logical" / "entities" / "stg_trade.yaml").exists()
    # after lift, the unmanaged-model diagnostic disappears
    codes = _codes(features.dbt_diagnostics(workspace))
    assert "MDL-DRIFT-unmanaged_model" not in codes


def test_adopt_column(workspace):
    msg = cmds.adopt_column(workspace, "counterparty", "rating_grade", "VARCHAR")
    assert "adopted" in msg
    le = workspace.entity_for_dbt_model("counterparty")
    assert any(a.name == "rating_grade" for a in le.attributes)
    # the additive-drift diagnostic clears
    codes = _codes(features.dbt_diagnostics(workspace))
    assert "MDL-DRIFT-column_added" not in codes


def test_unmanage_stops_emission_and_drift(workspace):
    from mdl_core.repo import ModelRepo
    from mdl_emit_dbt.emitter import DbtEmitter

    msg = cmds.unmanage(workspace, "trade")
    assert "engineer-owned" in msg
    repo = ModelRepo.load(workspace.model_dir)
    result = DbtEmitter(repo.model, "duckdb_dev").generate(workspace.dbt_dir, write=False)
    assert "models/trade.sql" not in {m.path for m in result.merges}
    # and drift does not flag the dbt model as unmanaged noise
    codes = _codes(features.dbt_diagnostics(workspace))
    assert not any("trade" in d.message and d.code == "MDL-DRIFT-unmanaged_model"
                   for diags in features.dbt_diagnostics(workspace).values() for d in diags)
    assert "MDL-DRIFT-model_removed" not in codes


def test_declare_relationship(workspace):
    cmds.adopt_column(workspace, "counterparty", "trade_id", "VARCHAR")
    msg = cmds.declare_relationship(workspace, "counterparty", "trade_id", "trade")
    assert "declared" in msg
    from mdl_core.repo import ModelRepo

    repo = ModelRepo.load(workspace.model_dir)
    assert any(r.name == "counterparty_to_trade" for r in repo.model.relationships.values())


def test_hover_types_are_lsp(workspace):
    # sanity: builders return real lsprotocol structures
    diag_map = features.dbt_diagnostics(workspace)
    for diags in diag_map.values():
        for d in diags:
            assert isinstance(d, lsp.Diagnostic)
