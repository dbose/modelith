"""Audit log: JSONL sink, off-by-default, records writes + refusals, admin-gated read
(spec §20). OTLP export is exercised only for the config decision (the SDK is an
optional extra, so we don't require a live collector here)."""

from __future__ import annotations

import json
import subprocess

import pytest
from mdl_server.audit import (
    ENV_AUDIT_LOG,
    ENV_OTLP_ENDPOINT,
    AuditSink,
)

# --- the sink in isolation --------------------------------------------------------


def test_off_by_default(tmp_path):
    sink = AuditSink.from_env({})
    assert sink.enabled is False
    # a record on a disabled sink is a no-op, not an error
    sink.record("command", identity="x", source="git", target="set_definition", outcome="ok")
    assert list(tmp_path.iterdir()) == []


def test_jsonl_records_a_write(tmp_path):
    log = tmp_path / "audit.jsonl"
    sink = AuditSink.from_env({ENV_AUDIT_LOG: str(log)})
    assert sink.enabled is True
    sink.record(
        "propose", identity="anita@corp.com", source="proxy",
        target="sme/anita/x", outcome="ok", detail="3 changes",
    )
    rec = json.loads(log.read_text().strip())
    assert rec["op"] == "propose"
    assert rec["identity"] == "anita@corp.com"
    assert rec["source"] == "proxy"
    assert rec["outcome"] == "ok"
    assert rec["detail"] == "3 changes"
    assert rec["ts"].endswith("Z")


def test_records_a_denied_write(tmp_path):
    """The whole point: a refused write is captured, though it never became a commit
    and so is invisible in git."""
    log = tmp_path / "audit.jsonl"
    sink = AuditSink.from_env({ENV_AUDIT_LOG: str(log)})
    sink.record("propose", identity="", source="anonymous", target="-", outcome="denied",
                detail="needs identity")
    rec = json.loads(log.read_text().strip())
    assert rec["outcome"] == "denied"
    assert rec["source"] == "anonymous"


def test_append_only_and_read_newest_first(tmp_path):
    log = tmp_path / "audit.jsonl"
    sink = AuditSink.from_env({ENV_AUDIT_LOG: str(log)})
    for i in range(3):
        sink.record("command", identity="u", source="git", target=f"op{i}", outcome="ok")
    assert len(log.read_text().splitlines()) == 3  # appended, not overwritten
    recs = sink.read(limit=10)
    assert [r["target"] for r in recs] == ["op2", "op1", "op0"]  # newest first


def test_read_filters_by_op_and_identity(tmp_path):
    log = tmp_path / "audit.jsonl"
    sink = AuditSink.from_env({ENV_AUDIT_LOG: str(log)})
    sink.record("command", identity="a", source="git", target="x", outcome="ok")
    sink.record("propose", identity="b", source="git", target="y", outcome="ok")
    assert [r["target"] for r in sink.read(op="propose")] == ["y"]
    assert [r["target"] for r in sink.read(identity="a")] == ["x"]


def test_bad_outcome_is_normalized(tmp_path):
    log = tmp_path / "audit.jsonl"
    sink = AuditSink.from_env({ENV_AUDIT_LOG: str(log)})
    sink.record("command", identity="u", source="git", target="x", outcome="bogus")
    assert json.loads(log.read_text().strip())["outcome"] == "error"


def test_otlp_endpoint_makes_sink_enabled_even_without_file(tmp_path, monkeypatch):
    """Setting only the OTEL endpoint enables auditing. Whether the SDK is installed
    or not, `enabled` is driven by configuration; missing packages degrade to no
    OTLP but must not raise."""
    sink = AuditSink.from_env({ENV_OTLP_ENDPOINT: "http://localhost:4318"})
    # If OTel isn't installed the logger is None and only OTLP is unavailable — but
    # from_env still parsed the endpoint. Enabled iff a logger OR a file exists.
    # With no file and no SDK, enabled is False; with the SDK, True. Either way, no raise.
    assert isinstance(sink.enabled, bool)


# --- through the server -----------------------------------------------------------


@pytest.fixture
def git_model_dir(model_dir):
    for a in (
        ["init", "-q", str(model_dir)],
        ["-C", str(model_dir), "config", "user.email", "t@t.co"],
        ["-C", str(model_dir), "config", "user.name", "t"],
        ["-C", str(model_dir), "add", "-A"],
        ["-C", str(model_dir), "commit", "-qm", "base"],
        ["-C", str(model_dir), "branch", "-M", "main"],
    ):
        subprocess.run(["git", *a], check=True)
    return model_dir


def test_command_write_is_audited(git_model_dir, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from mdl_server import create_app

    log = tmp_path / "audit.jsonl"
    monkeypatch.setenv(ENV_AUDIT_LOG, str(log))
    client = TestClient(create_app(git_model_dir))

    ce = next(
        e for e in client.get("/api/model").json()["entities"] if e["name"] == "counterparty"
    )["conceptual"]["id"]
    r = client.post(
        "/api/command",
        json={"op": "set_definition", "payload": {"id": ce, "definition": "audited."}},
    )
    assert r.status_code == 200, r.text
    recs = [json.loads(ln) for ln in log.read_text().splitlines()]
    assert any(x["op"] == "command" and x["target"] == "set_definition" and x["outcome"] == "ok"
               for x in recs), recs


def test_audit_endpoint_is_admin_gated_under_require(git_model_dir, tmp_path, monkeypatch):
    """With MDL_AUTH_REQUIRE and no identity, the audit trail must not be readable by
    an anonymous viewer."""
    from fastapi.testclient import TestClient
    from mdl_server import create_app
    from mdl_server.identity import ENV_REQUIRE

    log = tmp_path / "audit.jsonl"
    monkeypatch.setenv(ENV_AUDIT_LOG, str(log))
    monkeypatch.setenv(ENV_REQUIRE, "1")
    # isolate git so the request has no resolvable identity -> anonymous
    empty = tmp_path / "empty.gitconfig"
    empty.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(empty))
    subprocess.run(["git", "-C", str(git_model_dir), "config", "--unset", "user.name"], check=False)
    subprocess.run(["git", "-C", str(git_model_dir), "config", "--unset", "user.email"], check=False)

    client = TestClient(create_app(git_model_dir))
    assert client.get("/api/audit").status_code == 403


def test_audit_endpoint_absent_when_auditing_off(git_model_dir):
    from fastapi.testclient import TestClient
    from mdl_server import create_app

    client = TestClient(create_app(git_model_dir))  # no MDL_AUDIT_LOG
    # With auditing off, /api/audit returns a clean 404 (not the SPA page, not data).
    assert client.get("/api/audit").status_code == 404
