"""`mdl --version` / `-V` — the root-callback version flag.

Regression guard: before this, the root app had no callback, so `--version` fell through
to a usage error (and, on newer Typer, crashed the telemetry wrapper). It must now print
the installed version and exit 0, without disturbing subcommand dispatch.
"""

from __future__ import annotations

from importlib.metadata import version

from typer.testing import CliRunner

from mdl_cli.main import app

runner = CliRunner()


def test_version_flag_prints_and_exits_zero():
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0, r.output
    assert r.output.strip() == f"mdl {version('modelith-dbt')}"


def test_short_version_flag():
    r = runner.invoke(app, ["-V"])
    assert r.exit_code == 0, r.output
    assert r.output.strip().startswith("mdl ")


def test_root_callback_does_not_break_subcommands():
    # a normal command still dispatches (help for a leaf command)
    r = runner.invoke(app, ["validate", "--help"])
    assert r.exit_code == 0
    assert "Validate the model" in r.output
