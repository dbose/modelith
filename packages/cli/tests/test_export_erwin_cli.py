"""CLI: `mdl export erwin` — writes erwin XML, honours the export.erwin.naming config."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from mdl_cli.main import app

runner = CliRunner()

_REPO = Path(__file__).resolve().parents[3]
_FIXTURE = _REPO / "demos" / "fixtures" / "sample.erwin.xml"


def _import_model(tmp_path: Path) -> Path:
    out = tmp_path / "model"
    r = runner.invoke(app, ["import", "erwin", str(_FIXTURE), "-o", str(out)])
    assert r.exit_code == 0, r.output
    return out


def test_export_erwin_writes_a_document(tmp_path):
    model = _import_model(tmp_path)
    dst = tmp_path / "out.erwin.xml"
    r = runner.invoke(app, ["export", "erwin", "-m", str(model), "-o", str(dst)])
    assert r.exit_code == 0, r.output
    xml = dst.read_text()
    assert 'FileVersion="9.8.0000"' in xml and 'Format="erwin"' in xml
    assert "<Entity " in xml


def test_export_erwin_honours_the_naming_config(tmp_path):
    model = _import_model(tmp_path)
    # opt into the team convention via the project file
    proj = model / "mdl-project.yaml"
    text = proj.read_text()
    import re

    text = re.sub(
        r"export:\n(?:  .*\n?)*",
        "export:\n  erwin:\n    naming:\n      replace_underscore_with_space: true\n",
        text,
    )
    proj.write_text(text)

    dst = tmp_path / "named.erwin.xml"
    r = runner.invoke(app, ["export", "erwin", "-m", str(model), "-o", str(dst)])
    assert r.exit_code == 0, r.output
    xml = dst.read_text()
    # logical/display name got underscores -> spaces; physical stayed raw
    assert "<Name>counterparty id</Name>" in xml
    assert "<Physical_Name>COUNTERPARTY</Physical_Name>" in xml


def test_export_erwin_scoped_to_subject_area(tmp_path):
    """`-s <SA>` exports just that subject area's slice."""
    model = _import_model(tmp_path)
    dst = tmp_path / "slice.erwin.xml"
    r = runner.invoke(app, ["export", "erwin", "-m", str(model), "-s", "Trading", "-o", str(dst)])
    assert r.exit_code == 0, r.output
    xml = dst.read_text()
    assert 'Format="erwin"' in xml and "<Subject_Area " in xml
    # the Trading area's entities are present
    assert "<Name>Counterparty</Name>" in xml
