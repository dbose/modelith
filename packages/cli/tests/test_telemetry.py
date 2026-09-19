"""Tests for the anonymous, opt-in usage telemetry (GTM §4.3).

All offline: ~/.modelith is redirected to a tmp dir and the HTTP POST is a spy, so
nothing ever leaves the machine. Covers: off-by-default (non-TTY), consent yes/no,
env kill-switches, command-name sanitization, payload shape (no leakage), and
fail-soft on network errors.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

import mdl_cli.telemetry as t


@pytest.fixture
def state_in_tmp(tmp_path: Path, monkeypatch):
    """Redirect the telemetry state file into a tmp dir, and clear kill-switch env."""
    monkeypatch.setattr(t, "STATE_DIR", tmp_path / ".modelith")
    monkeypatch.setattr(t, "STATE_FILE", tmp_path / ".modelith" / "telemetry.json")
    for var in ("MODELITH_TELEMETRY_DISABLED", "DO_NOT_TRACK", "CI"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def _fake_tty(monkeypatch, is_tty: bool = True):
    monkeypatch.setattr("sys.stdin.isatty", lambda: is_tty)
    monkeypatch.setattr("sys.stdout.isatty", lambda: is_tty)


# --- sanitize_command -------------------------------------------------------------
@pytest.mark.parametrize(
    "argv,expected",
    [
        (["ontology", "search", "diabetes"], "ontology search"),
        (["validate", "-m", "."], "validate"),
        (["init", "--demo"], "init"),
        (["bogus"], "<unknown>"),
        ([], "<none>"),
        (["-m", "x"], "<none>"),  # only options -> no command token
        (["catalog", "publish"], "catalog publish"),
    ],
)
def test_sanitize_command(argv, expected):
    assert t.sanitize_command(argv) == expected


# --- off by default ---------------------------------------------------------------
def test_non_tty_writes_nothing_and_records_nothing(state_in_tmp, monkeypatch):
    _fake_tty(monkeypatch, is_tty=False)
    assert t.ensure_consent() is False
    assert not t.STATE_FILE.exists()  # no prompt, no write
    post = mock.Mock()
    monkeypatch.setattr("httpx.post", post)
    t.record("validate", 0)
    post.assert_not_called()


@pytest.mark.parametrize("killswitch", ["MODELITH_TELEMETRY_DISABLED", "DO_NOT_TRACK", "CI"])
def test_env_killswitches_force_off(state_in_tmp, monkeypatch, killswitch):
    monkeypatch.setenv(killswitch, "1")
    _fake_tty(monkeypatch, is_tty=True)  # even on a TTY, the switch wins
    with mock.patch("typer.confirm", return_value=True) as confirm:
        assert t.ensure_consent() is False
        confirm.assert_not_called()  # no prompt at all
    assert not t.STATE_FILE.exists()
    post = mock.Mock()
    monkeypatch.setattr("httpx.post", post)
    t.record("init", 0)
    post.assert_not_called()


# --- consent yes / no -------------------------------------------------------------
def test_consent_yes_writes_state_and_hashed_id(state_in_tmp, monkeypatch):
    _fake_tty(monkeypatch, is_tty=True)
    with mock.patch("typer.confirm", return_value=True):
        assert t.ensure_consent() is True
    state = json.loads(t.STATE_FILE.read_text())
    assert state["consented"] is True
    assert state["enabled"] is True
    assert state["schema_version"] == t.SCHEMA_VERSION
    # install_id is a 64-char sha256 hex, and the file carries no reversible uuid.
    assert len(state["install_id"]) == 64
    assert all(c in "0123456789abcdef" for c in state["install_id"])


def test_consent_no_disables(state_in_tmp, monkeypatch):
    _fake_tty(monkeypatch, is_tty=True)
    with mock.patch("typer.confirm", return_value=False):
        assert t.ensure_consent() is False
    state = json.loads(t.STATE_FILE.read_text())
    assert state["consented"] is True and state["enabled"] is False
    post = mock.Mock()
    monkeypatch.setattr("httpx.post", post)
    t.record("validate", 0)
    post.assert_not_called()


def test_consent_is_remembered_not_reprompted(state_in_tmp, monkeypatch):
    _fake_tty(monkeypatch, is_tty=True)
    with mock.patch("typer.confirm", return_value=True):
        t.ensure_consent()
    # second call must not prompt again
    with mock.patch("typer.confirm") as confirm:
        assert t.ensure_consent() is True
        confirm.assert_not_called()


# --- payload shape ----------------------------------------------------------------
def test_record_payload_shape_and_no_leakage(state_in_tmp, monkeypatch):
    _fake_tty(monkeypatch, is_tty=True)
    with mock.patch("typer.confirm", return_value=True):
        t.ensure_consent()
    captured = {}

    def spy(url, json=None, timeout=None):  # noqa: A002 - matches httpx.post kw
        captured["url"] = url
        captured["body"] = json
        captured["timeout"] = timeout

    monkeypatch.setattr("httpx.post", spy)
    t.record("ontology search", 0)

    assert captured["url"] == f"{t.POSTHOG_HOST}{t.POSTHOG_CAPTURE_PATH}"
    assert captured["timeout"] == t.TELEMETRY_TIMEOUT
    body = captured["body"]
    assert body["event"] == "command_run"
    state = json.loads(t.STATE_FILE.read_text())
    assert body["distinct_id"] == state["install_id"]
    # coarse timestamp: minute/second zeroed
    assert body["timestamp"].endswith(":00:00+00:00")
    props = body["properties"]
    assert set(props) == {
        "command", "success", "exit_code", "mdl_version", "os", "$process_person_profile",
    }
    assert props["command"] == "ontology search"
    assert props["success"] is True
    # No model contents, paths, names, args, or errors anywhere in the payload.
    blob = json.dumps(body)
    assert "/" not in props["command"]
    assert "ontology search" in blob and "mdl-project" not in blob


def _consent_yes_and_spy(monkeypatch):
    """Grant consent and return a list that collects every emitted event name."""
    _fake_tty(monkeypatch, is_tty=True)
    with mock.patch("typer.confirm", return_value=True):
        t.ensure_consent()
    events: list[str] = []
    monkeypatch.setattr(
        "httpx.post",
        lambda url, json=None, timeout=None: events.append(json["event"]),
    )
    return events


# --- AARRR event taxonomy ---------------------------------------------------------
def test_cli_installed_fires_once(state_in_tmp, monkeypatch):
    events = _consent_yes_and_spy(monkeypatch)
    monkeypatch.setattr("sys.argv", ["mdl", "validate", "-m", "."])
    t.record("validate", 0)
    assert "cli_installed" in events  # acquisition marker on first emit
    events.clear()
    t.record("validate", 0)  # second run
    assert "cli_installed" not in events  # never repeats


def test_init_demo_vs_real_split(state_in_tmp, monkeypatch):
    events = _consent_yes_and_spy(monkeypatch)
    monkeypatch.setattr("sys.argv", ["mdl", "init", "--demo"])
    t.record("init", 0)
    assert "demo_scaffolded" in events and "model_initialized" not in events
    events.clear()
    monkeypatch.setattr("sys.argv", ["mdl", "init"])
    t.record("init", 0)
    assert "model_initialized" in events and "demo_scaffolded" not in events


@pytest.mark.parametrize(
    "command,exit_code,expected",
    [
        ("validate", 0, "model_validated"),
        ("validate", 1, "validation_failed"),  # a drop-off reason
        ("reverse", 0, "warehouse_reversed"),
        ("generate", 0, "dbt_generated"),
        ("drift", 0, "drift_checked"),
    ],
)
def test_semantic_event_for_command(state_in_tmp, monkeypatch, command, exit_code, expected):
    events = _consent_yes_and_spy(monkeypatch)
    monkeypatch.setattr("sys.argv", ["mdl", command])
    t.record(command, exit_code)
    assert expected in events
    assert "command_run" in events  # catch-all always fires too


def test_emit_canvas_opened_standalone(state_in_tmp, monkeypatch):
    events = _consent_yes_and_spy(monkeypatch)
    t.emit("canvas_opened", {"surface": "serve"})
    assert events == ["canvas_opened"]


def test_emit_noop_when_disabled(state_in_tmp, monkeypatch):
    _fake_tty(monkeypatch, is_tty=True)
    with mock.patch("typer.confirm", return_value=False):
        t.ensure_consent()  # opted out
    post = mock.Mock()
    monkeypatch.setattr("httpx.post", post)
    t.emit("canvas_opened", {"surface": "serve"})
    post.assert_not_called()


def test_record_swallows_network_error(state_in_tmp, monkeypatch):
    _fake_tty(monkeypatch, is_tty=True)
    with mock.patch("typer.confirm", return_value=True):
        t.ensure_consent()

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr("httpx.post", boom)
    # must not raise
    t.record("validate", 1)


# --- exit-code fidelity of the main() wrapper -------------------------------------
# The telemetry wrapper must record the true exit code AND leave the documented
# exit codes (0 ok / 1 validation / 2 usage) byte-for-byte unchanged.
@pytest.mark.parametrize(
    "argv,expected_code",
    [
        (["mdl", "--help"], 0),
        (["mdl", "bogus-command"], 2),  # unknown command -> Click usage error
    ],
)
def test_main_wrapper_preserves_exit_codes(monkeypatch, argv, expected_code):
    from mdl_cli import main as cli_main

    monkeypatch.setattr("sys.argv", argv)
    # never prompt / emit during the test
    monkeypatch.setattr("mdl_cli.telemetry.ensure_consent", lambda: False)
    recorded = []
    monkeypatch.setattr(
        "mdl_cli.telemetry.record", lambda cmd, code: recorded.append((cmd, code))
    )
    with pytest.raises(SystemExit) as exc:
        cli_main.main()
    assert exc.value.code == expected_code
    assert recorded and recorded[0][1] == expected_code


def test_main_wrapper_records_validate_pass_and_fail(tmp_path, monkeypatch):
    """A passing validate records exit 0; a failing one records exit 1 — and the
    process exit code matches, proving telemetry never masks a failure."""
    from typer.testing import CliRunner

    from mdl_cli import main as cli_main

    # scaffold a valid demo model to validate against
    CliRunner().invoke(cli_main.app, ["init", "--demo", str(tmp_path)])
    monkeypatch.setattr("mdl_cli.telemetry.ensure_consent", lambda: False)

    for target, expected in [(str(tmp_path / "model"), 0), (str(tmp_path), 1)]:
        recorded: list = []
        monkeypatch.setattr(
            "mdl_cli.telemetry.record",
            lambda cmd, code, _sink=recorded: _sink.append((cmd, code)),
        )
        monkeypatch.setattr("sys.argv", ["mdl", "validate", "-m", target])
        with pytest.raises(SystemExit) as exc:
            cli_main.main()
        assert exc.value.code == expected
        assert recorded == [("validate", expected)]
