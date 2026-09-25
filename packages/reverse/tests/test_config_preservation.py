"""Re-reversing into a dir must NOT wipe the user's hand-authored config.

The reported bug: a user put `reverse: {exclude: ["*_dbt_*"]}` in the target
mdl-project.yaml, re-ran reverse, and the block was overwritten back to empty (so the
dbt-meta filter stopped working). Two fixes are covered here: the writer preserves an
existing project file, and reverse carries the config it was classified with onto the
written model.
"""

from __future__ import annotations

from mdl_core.ir import ProjectConfig, ReverseConfig
from mdl_core.repo import ModelRepo
from mdl_core.yaml_io import load_file
from mdl_reverse.writer import write_model


def _minimal_model(reverse_cfg: ReverseConfig | None = None):
    from mdl_core.ir import Model

    cfg = ProjectConfig(name="reversed_model", dbt_target="duckdb_dev")
    if reverse_cfg is not None:
        cfg.reverse = reverse_cfg
    return Model(cfg)


def test_writer_preserves_existing_user_config(tmp_path):
    # a hand-authored project file with an exclude, a glossary block, and a comment
    (tmp_path / "mdl-project.yaml").write_text(
        "name: reversed_model\n"
        "dbt_target: duckdb_dev\n"
        "# my hand-authored filter\n"
        "reverse:\n"
        '  exclude: ["*_dbt_*"]\n'
        "glossary:\n"
        "  source_of_truth: git\n",
        encoding="utf-8",
    )
    # reverse writes a model whose config has NO reverse block (the old clobber path)
    write_model(_minimal_model(), tmp_path)

    text = (tmp_path / "mdl-project.yaml").read_text(encoding="utf-8")
    assert "*_dbt_*" in text  # the authored exclude survives
    assert "source_of_truth: git" in text  # the glossary block survives
    assert "my hand-authored filter" in text  # even the comment survives (ruamel round-trip)

    # and it still parses + the exclude is still effective
    doc = load_file(tmp_path / "mdl-project.yaml")
    rc = ReverseConfig.model_validate(dict(doc["reverse"]))
    assert rc.exclude == ["*_dbt_*"]


def test_writer_fresh_dir_writes_clean(tmp_path):
    # first reverse into an empty dir: no prior file to preserve, writes fresh
    write_model(_minimal_model(ReverseConfig(exclude=["*_dbt_*"])), tmp_path)
    doc = load_file(tmp_path / "mdl-project.yaml")
    assert doc["name"] == "reversed_model"
    assert dict(doc["reverse"])["exclude"] == ["*_dbt_*"]


def test_reverse_config_round_trips_onto_model():
    # the carry-through: a reverse config passed to reverse() lands on the model.config
    from mdl_reverse.manifest import ManifestModel, ManifestProjection
    from mdl_reverse.reverse import reverse

    proj = ManifestProjection(
        models={
            "dim_customer": ManifestModel(
                name="dim_customer",
                unique_id="model.p.dim_customer",
                columns={},
            )
        }
    )
    rc = ReverseConfig(exclude=["*_dbt_*"])
    result = reverse(proj, reverse_config=rc)
    assert result.model.config.reverse.exclude == ["*_dbt_*"]


def test_reload_after_writer_preserve_keeps_model_loadable(tmp_path):
    # end-to-end: preserve, then ModelRepo.load still works (no malformed YAML)
    (tmp_path / "mdl-project.yaml").write_text(
        "name: reversed_model\ndbt_target: duckdb_dev\nreverse:\n  exclude: ['*_dbt_*']\n",
        encoding="utf-8",
    )
    write_model(_minimal_model(), tmp_path)
    repo = ModelRepo.load(tmp_path)
    assert repo.model.config.reverse.exclude == ["*_dbt_*"]


def _model_with(names):
    from mdl_core.ids import new_ulid
    from mdl_core.ir import ConceptualEntity, LogicalEntity, Model

    m = Model(ProjectConfig(name="p", dbt_target="duckdb"))
    for nm in names:
        ce = ConceptualEntity(id=new_ulid(), name=nm)
        m.add(ce)
        m.add(LogicalEntity(id=new_ulid(), name=nm, realises=ce.id))
    return m


def test_reverse_prunes_stale_entities_on_rewrite(tmp_path):
    """An in-place re-reverse removes entity files that no longer exist (e.g. now
    excluded), instead of leaving them orphaned — the reported in-place-update gap."""
    write_model(_model_with(["customer", "orphan"]), tmp_path)
    assert (tmp_path / "logical" / "entities" / "customer.yaml").exists()
    assert (tmp_path / "logical" / "entities" / "orphan.yaml").exists()

    # second write: only customer survives -> orphan.yaml must be pruned
    write_model(_model_with(["customer"]), tmp_path)
    assert (tmp_path / "logical" / "entities" / "customer.yaml").exists()
    assert not (tmp_path / "logical" / "entities" / "orphan.yaml").exists()


def test_prune_never_touches_project_file_or_mdl_state(tmp_path):
    from mdl_core.ir import Model

    (tmp_path / ".mdl").mkdir()
    (tmp_path / ".mdl" / "decisions.yaml").write_text("decisions: []\n", encoding="utf-8")
    (tmp_path / "mdl-project.yaml").write_text(
        "name: p\ndbt_target: duckdb\nreverse:\n  exclude: ['x_*']\n", encoding="utf-8"
    )
    write_model(Model(ProjectConfig(name="p", dbt_target="duckdb")), tmp_path)
    # the user's project file + .mdl state are untouched by the prune
    assert "x_*" in (tmp_path / "mdl-project.yaml").read_text()
    assert (tmp_path / ".mdl" / "decisions.yaml").exists()
