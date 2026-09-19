"""`mdl reverse --ddl <dir>` and the no-clobber output guard.

Two behaviours the VS Code right-click "Reverse Engineer" feature depends on:

1. `--ddl` accepts a DIRECTORY of .sql files, reversed as ONE warehouse — CREATE
   TABLEs split across files, and foreign keys that cross files, resolve because the
   engine sees every file in a single pass.
2. `reverse` never clobbers an existing model: if `-o <dir>` already holds an
   `mdl-project.yaml`, the run diverts to a fresh `<dir>-reversed-v<N>` sibling and
   says so, instead of overwriting hand-authored work.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from mdl_cli.main import app

runner = CliRunner()

# customer -> region FK lives in one file; region is defined in another. Reversing the
# directory as one warehouse is what lets the FK resolve.
_CUSTOMER_SQL = """
CREATE TABLE customer (
  customer_id BIGINT PRIMARY KEY,
  legal_name TEXT NOT NULL,
  region_id BIGINT REFERENCES region(region_id)
);
"""
_REGION_SQL = """
CREATE TABLE region (
  region_id BIGINT PRIMARY KEY,
  region_name TEXT
);
"""


def _entities(model: Path) -> set[str]:
    return {p.stem for p in (model / "logical" / "entities").glob("*.yaml")}


def test_reverse_ddl_directory_unions_files(tmp_path: Path):
    """A directory of .sql files reverses as one warehouse: entities from every file
    appear, and a cross-file FK is recorded (customer.region_id -> region)."""
    ddl = tmp_path / "warehouse"
    ddl.mkdir()
    (ddl / "01_customer.sql").write_text(_CUSTOMER_SQL, encoding="utf-8")
    (ddl / "02_region.sql").write_text(_REGION_SQL, encoding="utf-8")
    out = tmp_path / "model"

    result = runner.invoke(
        app, ["reverse", "--ddl", str(ddl), "-o", str(out), "--no-review"]
    )
    assert result.exit_code == 0, result.output
    ents = _entities(out)
    # both tables (from two separate files) reversed into one model
    assert "customer" in ents
    assert "region" in ents
    # the cross-file FK landed as a relationship (surfaced for review, not dropped)
    rel = (out / "logical" / "entities" / "customer.yaml").read_text(encoding="utf-8")
    assert "region" in rel


def test_reverse_ddl_directory_recurses_subfolders(tmp_path: Path):
    """*.sql are found recursively, so a nested layout (schemas/, staging/) still
    reverses as one warehouse."""
    ddl = tmp_path / "warehouse"
    (ddl / "core").mkdir(parents=True)
    (ddl / "ref").mkdir(parents=True)
    (ddl / "core" / "customer.sql").write_text(_CUSTOMER_SQL, encoding="utf-8")
    (ddl / "ref" / "region.sql").write_text(_REGION_SQL, encoding="utf-8")
    out = tmp_path / "model"

    result = runner.invoke(
        app, ["reverse", "--ddl", str(ddl), "-o", str(out), "--no-review"]
    )
    assert result.exit_code == 0, result.output
    ents = _entities(out)
    assert {"customer", "region"} <= ents


def test_reverse_ddl_empty_directory_errors(tmp_path: Path):
    """An empty directory (no .sql anywhere) is a clear error, not a silent no-op."""
    ddl = tmp_path / "empty"
    ddl.mkdir()
    out = tmp_path / "model"

    result = runner.invoke(
        app, ["reverse", "--ddl", str(ddl), "-o", str(out), "--no-review"]
    )
    assert result.exit_code == 1, result.output
    assert "no .sql files" in result.output


def test_reverse_single_file_still_works(tmp_path: Path):
    """The original single-file --ddl path is unchanged (no regression)."""
    f = tmp_path / "schema.sql"
    f.write_text(_CUSTOMER_SQL + _REGION_SQL, encoding="utf-8")
    out = tmp_path / "model"

    result = runner.invoke(
        app, ["reverse", "--ddl", str(f), "-o", str(out), "--no-review"]
    )
    assert result.exit_code == 0, result.output
    assert {"customer", "region"} <= _entities(out)


def test_reverse_does_not_clobber_existing_model(tmp_path: Path):
    """If -o already holds a model, reverse diverts to a suffixed sibling and says so,
    leaving the existing model's marker file untouched."""
    f = tmp_path / "schema.sql"
    f.write_text(_REGION_SQL, encoding="utf-8")
    out = tmp_path / "model"
    out.mkdir()
    # a pre-existing model marker + a sentinel file that must survive
    marker = out / "mdl-project.yaml"
    marker.write_text("name: hand_authored\n", encoding="utf-8")
    sentinel = out / "logical" / "entities" / "precious.yaml"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("name: precious\n", encoding="utf-8")

    result = runner.invoke(
        app, ["reverse", "--ddl", str(f), "-o", str(out), "--no-review"]
    )
    assert result.exit_code == 0, result.output
    # diverted to the suffixed sibling ...
    diverted = tmp_path / "model-reversed-v1"
    assert (diverted / "mdl-project.yaml").exists()
    assert "region" in _entities(diverted)
    # ... and the existing model is untouched
    assert marker.read_text(encoding="utf-8") == "name: hand_authored\n"
    assert sentinel.read_text(encoding="utf-8") == "name: precious\n"


def test_reverse_clobber_guard_increments_suffix(tmp_path: Path):
    """A second reverse into an occupied dir when -v1 also exists picks -v2."""
    f = tmp_path / "schema.sql"
    f.write_text(_REGION_SQL, encoding="utf-8")
    out = tmp_path / "model"
    out.mkdir()
    (out / "mdl-project.yaml").write_text("name: hand_authored\n", encoding="utf-8")
    # -v1 already taken
    v1 = tmp_path / "model-reversed-v1"
    v1.mkdir()
    (v1 / "mdl-project.yaml").write_text("name: prior_reverse\n", encoding="utf-8")

    result = runner.invoke(
        app, ["reverse", "--ddl", str(f), "-o", str(out), "--no-review"]
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "model-reversed-v2" / "mdl-project.yaml").exists()


# --- auto-accept floor (config + flags) --------------------------------------


def _pending_json(model: Path) -> list:
    import json as _json

    from mdl_cli.main import app as _app

    r = runner.invoke(_app, ["decisions", "list", "--pending", "--format", "json", "-m", str(model)])
    assert r.exit_code == 0, r.output
    return _json.loads(r.stdout or "[]")


# a declared FK (HIGH) so the default floor auto-accepts it and leaves nothing pending,
# while --review-all leaves it pending.
_FK_SQL = """
CREATE TABLE customer (customer_id BIGINT PRIMARY KEY, region_id BIGINT REFERENCES region(region_id));
CREATE TABLE region (region_id BIGINT PRIMARY KEY, region_name TEXT);
"""


def test_reverse_default_floor_auto_accepts_high(tmp_path: Path):
    """With the default floor, a declared FK (HIGH) is auto-accepted: nothing pending."""
    f = tmp_path / "s.sql"
    f.write_text(_FK_SQL, encoding="utf-8")
    out = tmp_path / "model"
    r = runner.invoke(app, ["reverse", "--ddl", str(f), "-o", str(out), "--no-review"])
    assert r.exit_code == 0, r.output
    assert _pending_json(out) == []


def test_reverse_review_all_proposes_everything(tmp_path: Path):
    """--review-all leaves even the HIGH FK pending for manual accept/reject."""
    f = tmp_path / "s.sql"
    f.write_text(_FK_SQL, encoding="utf-8")
    out = tmp_path / "model"
    r = runner.invoke(
        app, ["reverse", "--ddl", str(f), "-o", str(out), "--no-review", "--review-all"]
    )
    assert r.exit_code == 0, r.output
    pending = _pending_json(out)
    assert any(d["confidence"] == "high" for d in pending)


def test_reverse_auto_accept_none_equivalent(tmp_path: Path):
    """--auto-accept none behaves like --review-all."""
    f = tmp_path / "s.sql"
    f.write_text(_FK_SQL, encoding="utf-8")
    out = tmp_path / "model"
    r = runner.invoke(
        app, ["reverse", "--ddl", str(f), "-o", str(out), "--no-review", "--auto-accept", "none"]
    )
    assert r.exit_code == 0, r.output
    assert any(d["confidence"] == "high" for d in _pending_json(out))


def test_reverse_auto_accept_bad_level_errors(tmp_path: Path):
    """A typo'd level is a hard error, not a silent default."""
    f = tmp_path / "s.sql"
    f.write_text(_FK_SQL, encoding="utf-8")
    out = tmp_path / "model"
    r = runner.invoke(
        app, ["reverse", "--ddl", str(f), "-o", str(out), "--no-review", "--auto-accept", "sometimes"]
    )
    assert r.exit_code == 1, r.output
    assert "unknown auto_accept" in r.output


def test_reverse_auto_accept_from_naming_config(tmp_path: Path):
    """`reverse.auto_accept: none` in a --naming config gates everything to review."""
    f = tmp_path / "s.sql"
    f.write_text(_FK_SQL, encoding="utf-8")
    cfg = tmp_path / "rev.yaml"
    cfg.write_text("reverse:\n  auto_accept: none\n", encoding="utf-8")
    out = tmp_path / "model"
    r = runner.invoke(
        app, ["reverse", "--ddl", str(f), "-o", str(out), "--no-review", "--naming", str(cfg)]
    )
    assert r.exit_code == 0, r.output
    assert any(d["confidence"] == "high" for d in _pending_json(out))
