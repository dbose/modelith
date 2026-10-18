"""The `modelith.commands` subcommand plugin seam (mdl_cli.plugins).

A separately-installed package (the paid modelith-enterprise) adds `mdl` subcommands by
publishing a `register(app)` entry point in the `modelith.commands` group. These tests pin
the two guarantees the free CLI depends on: a valid plugin's command appears, and a broken
plugin is swallowed rather than bricking the CLI. We drive the real discovery path by faking
`importlib.metadata.entry_points`, so no actual package install is needed.
"""

from __future__ import annotations

import typer
from typer.testing import CliRunner

import mdl_cli.plugins as plugins

runner = CliRunner()


class _FakeEP:
    """A stand-in importlib.metadata entry point: `.name` + `.load()` returning a callable."""

    def __init__(self, name, loader):
        self.name = name
        self._loader = loader

    def load(self):
        return self._loader()


class _FakeEPs:
    """A stand-in for entry_points(): supports the modern `.select(group=...)` API."""

    def __init__(self, by_group):
        self._by_group = by_group

    def select(self, group):
        return self._by_group.get(group, [])


def _patch_eps(monkeypatch, eps):
    monkeypatch.setattr("importlib.metadata.entry_points", lambda: _FakeEPs({plugins.COMMAND_GROUP: eps}))
    plugins._reset_for_tests()


def test_plugin_command_is_registered_and_visible(monkeypatch):
    app = typer.Typer()

    @app.command()
    def builtin():  # a built-in so the app isn't empty
        pass

    def register(a: typer.Typer) -> None:
        plug = typer.Typer()

        @plug.command()
        def run():
            """the enterprise CI gate"""
            print("ci ran")

        a.add_typer(plug, name="ci")

    _patch_eps(monkeypatch, [_FakeEP("ci", lambda: register)])
    applied = plugins.load_command_plugins(app)
    assert applied == ["ci"]
    # the plugin's command group now shows in --help and is runnable
    assert "ci" in runner.invoke(app, ["--help"]).output
    assert runner.invoke(app, ["ci", "run"]).exit_code == 0


def test_a_broken_plugin_is_swallowed(monkeypatch):
    app = typer.Typer()

    @app.command()
    def builtin():
        pass

    def boom(a):
        raise RuntimeError("plugin exploded on register")

    # one plugin that raises on register, one that works — the good one still lands,
    # the bad one never bricks the CLI.
    def ok(a: typer.Typer) -> None:
        a.add_typer(typer.Typer(), name="good")

    _patch_eps(
        monkeypatch,
        [_FakeEP("bad", lambda: boom), _FakeEP("good", lambda: ok)],
    )
    applied = plugins.load_command_plugins(app)
    assert applied == ["good"]  # bad swallowed, good applied
    # the CLI still works
    assert runner.invoke(app, ["--help"]).exit_code == 0


def test_a_plugin_that_fails_to_load_is_swallowed(monkeypatch):
    app = typer.Typer()

    @app.command()
    def builtin():
        pass

    def bad_load():
        raise ImportError("no such module")

    _patch_eps(monkeypatch, [_FakeEP("bad", bad_load)])
    assert plugins.load_command_plugins(app) == []
    assert runner.invoke(app, ["--help"]).exit_code == 0


def test_no_plugins_is_the_normal_case(monkeypatch):
    app = typer.Typer()

    @app.command()
    def builtin():
        pass

    _patch_eps(monkeypatch, [])
    assert plugins.load_command_plugins(app) == []


def test_discovery_runs_once_per_process(monkeypatch):
    app = typer.Typer()
    calls = {"n": 0}

    def register(a):
        calls["n"] += 1

    _patch_eps(monkeypatch, [_FakeEP("ci", lambda: register)])
    plugins.load_command_plugins(app)
    plugins.load_command_plugins(app)  # second call is a no-op (idempotent)
    assert calls["n"] == 1


def test_real_cli_does_not_crash_with_the_seam(monkeypatch):
    """The real `mdl` app loads the seam in main(); with no plugins installed it is a no-op
    and --help still works. Guards the wiring in main()."""
    from mdl_cli.main import app as real_app

    plugins._reset_for_tests()
    result = runner.invoke(real_app, ["--help"])
    assert result.exit_code == 0
