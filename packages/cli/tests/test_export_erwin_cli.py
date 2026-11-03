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


def test_export_erwin_no_template_writes_self_built_envelope(tmp_path):
    """--no-template ignores the remembered import and emits the self-built envelope."""
    model = _import_model(tmp_path)
    dst = tmp_path / "out.erwin.xml"
    r = runner.invoke(app, ["export", "erwin", "-m", str(model), "--no-template", "-o", str(dst)])
    assert r.exit_code == 0, r.output
    xml = dst.read_text()
    assert 'FileVersion="10.10.38485"' in xml and 'Format="erwin"' in xml
    assert "<Entity " in xml


def test_export_erwin_auto_clones_the_remembered_import(tmp_path):
    """An erwin import is remembered (export.erwin.template_path), so a plain export CLONES it:
    the output carries the template's envelope (its FileVersion), not the self-built one."""
    model = _import_model(tmp_path)
    # the import stamped the template path
    proj = (model / "mdl-project.yaml").read_text()
    assert "template_path" in proj and "sample.erwin.xml" in proj

    dst = tmp_path / "out.erwin.xml"
    r = runner.invoke(app, ["export", "erwin", "-m", str(model), "-o", str(dst)])
    assert r.exit_code == 0, r.output
    assert "cloning remembered erwin template" in r.output
    xml = dst.read_text()
    # cloned from the template -> its FileVersion (9.8.00), NOT the self-built 10.10
    assert 'FileVersion="9.8.00"' in xml and 'Format="erwin"' in xml
    assert "<Entity " in xml


def test_export_erwin_remembered_template_gone_falls_back(tmp_path):
    """If the remembered template file has moved, export warns and uses the self-built envelope."""
    model = _import_model(tmp_path)
    # point the config at a missing file
    proj = model / "mdl-project.yaml"
    text = proj.read_text().replace("sample.erwin.xml", "gone.xml")
    proj.write_text(text)

    dst = tmp_path / "out.erwin.xml"
    r = runner.invoke(app, ["export", "erwin", "-m", str(model), "-o", str(dst)])
    assert r.exit_code == 0, r.output
    assert "remembered erwin template is gone" in r.output
    xml = dst.read_text()
    assert 'FileVersion="10.10.38485"' in xml  # self-built fallback


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
    # self-built envelope writes Physical_Name; the naming logic is shared with the template path
    r = runner.invoke(app, ["export", "erwin", "-m", str(model), "--no-template", "-o", str(dst)])
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
