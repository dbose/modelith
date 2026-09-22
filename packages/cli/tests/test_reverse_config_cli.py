"""`mdl reverse-config apply` — the comment-preserving reverse:-block writer doorway."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from mdl_cli.demo import scaffold_demo
from mdl_cli.main import app

runner = CliRunner()


def _proj(m: Path) -> str:
    return (m / "mdl-project.yaml").read_text(encoding="utf-8")


def test_apply_merges_block_additively(tmp_path: Path):
    scaffold_demo(tmp_path)
    m = tmp_path / "model"
    src = tmp_path / "cfg.yaml"
    src.write_text(
        "layers:\n  - {name: dims, role: dimension, match: {prefix: dim_}}\nexclude: ['*_tmp']\n",
        encoding="utf-8",
    )
    r = runner.invoke(app, ["reverse-config", "apply", str(src), "-m", str(m)])
    assert r.exit_code == 0, r.output
    text = _proj(m)
    assert "role: dimension" in text
    assert "*_tmp" in text
    # a second additive apply unions exclude, no clobber
    src2 = tmp_path / "cfg2.yaml"
    src2.write_text("exclude: ['*_scratch']\n", encoding="utf-8")
    r2 = runner.invoke(app, ["reverse-config", "apply", str(src2), "-m", str(m)])
    assert r2.exit_code == 0, r2.output
    text2 = _proj(m)
    assert "*_tmp" in text2 and "*_scratch" in text2


def test_apply_accepts_wrapping_reverse_key(tmp_path: Path):
    scaffold_demo(tmp_path)
    m = tmp_path / "model"
    src = tmp_path / "cfg.yaml"
    src.write_text("reverse:\n  target_form: 3nf\n", encoding="utf-8")
    r = runner.invoke(app, ["reverse-config", "apply", str(src), "-m", str(m)])
    assert r.exit_code == 0, r.output
    assert "target_form: 3nf" in _proj(m)


def test_apply_rejects_invalid_block(tmp_path: Path):
    scaffold_demo(tmp_path)
    m = tmp_path / "model"
    src = tmp_path / "bad.yaml"
    # a layer with a role that isn't in the LayerRole literal -> validation fails
    src.write_text("layers:\n  - {name: x, role: not_a_role}\n", encoding="utf-8")
    before = _proj(m)
    r = runner.invoke(app, ["reverse-config", "apply", str(src), "-m", str(m)])
    assert r.exit_code == 1, r.output
    assert "invalid" in r.output.lower()
    # nothing written on rejection
    assert _proj(m) == before


def test_apply_replace_overwrites(tmp_path: Path):
    scaffold_demo(tmp_path)
    m = tmp_path / "model"
    runner.invoke(
        app,
        ["reverse-config", "apply", "-m", str(m)],
        input="exclude: ['*_a', '*_b']\n",
    )
    r = runner.invoke(
        app,
        ["reverse-config", "apply", "-m", str(m), "--replace"],
        input="exclude: ['*_c']\n",
    )
    assert r.exit_code == 0, r.output
    text = _proj(m)
    assert "*_c" in text and "*_a" not in text


def _write_manifest(path: Path, models: list) -> None:
    import json
    nodes = {}
    for name, p in models:
        fqn = ["wh"] + p.replace("models/", "").replace(".sql", "").split("/")
        nodes[f"model.wh.{name}"] = {
            "resource_type": "model", "name": name, "original_file_path": p, "fqn": fqn,
            "columns": {}, "config": {}, "tags": [], "meta": {},
        }
    path.write_text(json.dumps({
        "metadata": {"dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json"},
        "nodes": nodes,
    }), encoding="utf-8")


def test_suggest_yaml_and_apply(tmp_path: Path):
    scaffold_demo(tmp_path)
    m = tmp_path / "model"
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, [
        ("stg_a", "models/staging/stg_a.sql"),
        ("stg_b", "models/staging/stg_b.sql"),
        ("dim_c", "models/marts/dim_c.sql"),
        ("dim_d", "models/marts/dim_d.sql"),
    ])
    r = runner.invoke(app, ["reverse-config", "suggest", "--manifest", str(manifest)])
    assert r.exit_code == 0, r.output
    assert "role: dimension" in r.output or "role: staging" in r.output

    r2 = runner.invoke(
        app, ["reverse-config", "suggest", "--manifest", str(manifest), "-m", str(m), "--apply"]
    )
    assert r2.exit_code == 0, r2.output
    assert "role:" in _proj(m)


def test_suggest_json_shape(tmp_path: Path):
    scaffold_demo(tmp_path)
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, [
        ("hub_a", "hub_a.sql"), ("hub_b", "hub_b.sql"),
    ])
    r = runner.invoke(
        app, ["reverse-config", "suggest", "--manifest", str(manifest), "--format", "json"]
    )
    assert r.exit_code == 0, r.output
    import json
    data = json.loads(r.stdout)
    assert "reverse" in data and "rationale" in data


def test_suggest_no_convention_is_graceful(tmp_path: Path):
    scaffold_demo(tmp_path)
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, [("thing", "thing.sql"), ("other", "other.sql")])
    r = runner.invoke(app, ["reverse-config", "suggest", "--manifest", str(manifest)])
    assert r.exit_code == 0, r.output
    assert "nothing to suggest" in r.output


def test_explain_no_config_is_legacy(tmp_path: Path):
    scaffold_demo(tmp_path)
    m = tmp_path / "model"
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, [
        ("stg_orders", "models/staging/stg_orders.sql"),
        ("dim_customer", "models/marts/dim_customer.sql"),
    ])
    r = runner.invoke(
        app, ["reverse-config", "explain", "--manifest", str(manifest), "-m", str(m)]
    )
    assert r.exit_code == 0, r.output
    # stg_ excluded, dim_ kept as business (no config)
    assert "stg_orders" in r.output and "dim_customer" in r.output


def test_explain_reflects_config(tmp_path: Path):
    scaffold_demo(tmp_path)
    m = tmp_path / "model"
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, [
        ("dim_customer", "models/marts/dim_customer.sql"),
        ("orders_tmp", "models/scratch/orders_tmp.sql"),
    ])
    runner.invoke(
        app, ["reverse-config", "apply", "-m", str(m)],
        input="layers:\n  - {name: dims, role: dimension, match: {prefix: dim_}}\nexclude: ['**/scratch/**']\n",
    )
    r = runner.invoke(
        app,
        ["reverse-config", "explain", "--manifest", str(manifest), "-m", str(m), "--format", "json"],
    )
    assert r.exit_code == 0, r.output
    import json
    rows = {row["model"]: row for row in json.loads(r.stdout)}
    assert rows["dim_customer"]["role"] == "dimension"
    assert rows["orders_tmp"]["excluded"] is True


def test_import_local_file_merges(tmp_path: Path):
    scaffold_demo(tmp_path)
    m = tmp_path / "model"
    src = tmp_path / "shared.yaml"
    src.write_text(
        "reverse:\n  layers:\n    - {name: dims, role: dimension, match: {prefix: dim_}}\n",
        encoding="utf-8",
    )
    r = runner.invoke(app, ["reverse-config", "import", str(src), "-m", str(m)])
    assert r.exit_code == 0, r.output
    assert "role: dimension" in _proj(m)


def test_import_missing_file_errors(tmp_path: Path):
    scaffold_demo(tmp_path)
    m = tmp_path / "model"
    r = runner.invoke(app, ["reverse-config", "import", str(tmp_path / "nope.yaml"), "-m", str(m)])
    assert r.exit_code == 1, r.output
    assert "no such file" in r.output.lower()


def test_import_malformed_rejected(tmp_path: Path):
    scaffold_demo(tmp_path)
    m = tmp_path / "model"
    src = tmp_path / "bad.yaml"
    src.write_text("reverse:\n  layers:\n    - {name: x, role: bogus_role}\n", encoding="utf-8")
    before = _proj(m)
    r = runner.invoke(app, ["reverse-config", "import", str(src), "-m", str(m)])
    assert r.exit_code == 1, r.output
    assert _proj(m) == before  # nothing written


def test_import_from_url(tmp_path: Path, monkeypatch):
    scaffold_demo(tmp_path)
    m = tmp_path / "model"

    class _Resp:
        text = "reverse:\n  exclude: ['*_tmp']\n"

        def raise_for_status(self):
            return None

    def _fake_get(url, **kw):
        return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "get", _fake_get)
    r = runner.invoke(
        app, ["reverse-config", "import", "https://example.com/team/reverse.yaml", "-m", str(m)]
    )
    assert r.exit_code == 0, r.output
    assert "*_tmp" in _proj(m)
