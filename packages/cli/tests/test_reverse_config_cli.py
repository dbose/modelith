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
