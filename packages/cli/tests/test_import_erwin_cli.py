"""CLI: `mdl import erwin` — write-a-model default + --apply into an existing model."""

from __future__ import annotations

import sys
from pathlib import Path

from typer.testing import CliRunner

from mdl_cli.main import app

runner = CliRunner()

# the real-shaped erwin fixture (reuse the reader's test fixture)
_FIXTURE = (
    Path(__file__).resolve().parents[2] / "reverse" / "tests" / "test_erwin.py"
)
sys.path.insert(0, str(_FIXTURE.parent))


def _erwin_xml() -> str:
    text = _FIXTURE.read_text()
    return text.split('_ERWIN = """')[1].split('"""')[0]


def test_import_erwin_writes_a_model(tmp_path):
    xml = tmp_path / "trading.xml"
    xml.write_text(_erwin_xml())
    out = tmp_path / "model"
    r = runner.invoke(app, ["import", "erwin", str(xml), "-o", str(out)])
    assert r.exit_code == 0, r.output
    assert (out / "logical" / "entities" / "counterparty.yaml").exists()
    # warnings surface as notes
    assert "note:" in r.output
    # and the written model validates
    v = runner.invoke(app, ["validate", "-m", str(out)])
    assert v.exit_code == 0, v.output


def test_import_erwin_apply_into_existing(tmp_path):
    xml = tmp_path / "trading.xml"
    xml.write_text(_erwin_xml())
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "mdl-project.yaml").write_text("name: existing\ndbt_target: duckdb\n")
    r = runner.invoke(app, ["import", "erwin", str(xml), "-o", str(existing), "--apply"])
    assert r.exit_code == 0, r.output
    assert "merged" in r.output
    assert (existing / "logical" / "entities" / "trade.yaml").exists()
    v = runner.invoke(app, ["validate", "-m", str(existing)])
    assert v.exit_code == 0, v.output


def test_import_erwin_diverts_on_existing_model(tmp_path):
    # a second write into a dir that already holds a model diverts to a sibling
    xml = tmp_path / "trading.xml"
    xml.write_text(_erwin_xml())
    out = tmp_path / "model"
    assert runner.invoke(app, ["import", "erwin", str(xml), "-o", str(out)]).exit_code == 0
    r = runner.invoke(app, ["import", "erwin", str(xml), "-o", str(out)])
    assert r.exit_code == 0, r.output
    # the original is untouched; a -reversed-v1 sibling was written
    assert (tmp_path / "model-reversed-v1" / "mdl-project.yaml").exists()
