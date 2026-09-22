"""`mdl decisions add` — the generic ledger-intake doorway.

Any external proposer (a script, a notebook, or a paid AI package) records proposals into
the decision ledger as JSON; each becomes a `proposed` Decision the user Accept/Rejects in
the Reverse Review panel, exactly like a built-in engine proposal. Provenance is kept in
evidence.source. Never auto-accepted; de-duplicated; a decided proposal is not resurrected.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from mdl_cli.demo import scaffold_demo
from mdl_cli.main import app

runner = CliRunner()

_PROP = {
    "kind": "relationship",
    "subject": "orders.customer_id -> customer (AI-inferred)",
    "confidence": "medium",
    "evidence": {"from": "orders", "to": "customer", "column": "customer_id"},
    "source": "ai_copilot",
}


def _pending(m: Path) -> list:
    r = runner.invoke(app, ["decisions", "list", "--pending", "--format", "json", "-m", str(m)])
    assert r.exit_code == 0, r.output
    return json.loads(r.stdout or "[]")


def test_add_records_a_proposed_decision(tmp_path: Path):
    scaffold_demo(tmp_path)
    m = tmp_path / "model"
    r = runner.invoke(app, ["decisions", "add", "-m", str(m)], input=json.dumps(_PROP))
    assert r.exit_code == 0, r.output
    pending = _pending(m)
    assert len(pending) == 1
    d = pending[0]
    assert d["kind"] == "relationship"
    assert d["confidence"] == "medium"
    assert d["verdict"] == "proposed"
    assert d["evidence"]["source"] == "ai_copilot"  # provenance kept


def test_add_a_list_of_proposals(tmp_path: Path):
    scaffold_demo(tmp_path)
    m = tmp_path / "model"
    props = [
        {"kind": "strip_column", "subject": "strip order_sk", "evidence": {"model": "orders", "column": "order_sk"}},
        _PROP,
    ]
    r = runner.invoke(app, ["decisions", "add", "-m", str(m)], input=json.dumps(props))
    assert r.exit_code == 0, r.output
    assert "added 2" in r.output
    assert len(_pending(m)) == 2


def test_add_defaults_confidence_low_and_source_external(tmp_path: Path):
    scaffold_demo(tmp_path)
    m = tmp_path / "model"
    minimal = {"kind": "relationship", "subject": "x -> y", "evidence": {"a": 1}}
    runner.invoke(app, ["decisions", "add", "-m", str(m)], input=json.dumps(minimal))
    d = _pending(m)[0]
    assert d["confidence"] == "low"
    assert d["evidence"]["source"] == "external"


def test_add_respects_a_prior_reject(tmp_path: Path):
    scaffold_demo(tmp_path)
    m = tmp_path / "model"
    runner.invoke(app, ["decisions", "add", "-m", str(m)], input=json.dumps(_PROP))
    key = json.loads(
        runner.invoke(app, ["decisions", "list", "--format", "json", "-m", str(m)]).stdout
    )[0]["signal_key"]
    runner.invoke(app, ["decisions", "reject", key, "-m", str(m)])
    # re-adding the same proposal must NOT resurrect it
    r = runner.invoke(app, ["decisions", "add", "-m", str(m)], input=json.dumps(_PROP))
    assert "added 0" in r.output and "skipped 1" in r.output
    assert _pending(m) == []  # still rejected, not pending


def test_add_rejects_malformed(tmp_path: Path):
    scaffold_demo(tmp_path)
    m = tmp_path / "model"
    # missing subject
    r = runner.invoke(app, ["decisions", "add", "-m", str(m)], input='{"kind":"x"}')
    assert r.exit_code == 1, r.output
    # bad JSON
    r2 = runner.invoke(app, ["decisions", "add", "-m", str(m)], input="{not json")
    assert r2.exit_code == 1, r2.output
    # bad confidence
    r3 = runner.invoke(
        app, ["decisions", "add", "-m", str(m)],
        input='{"kind":"x","subject":"y","confidence":"sky-high"}',
    )
    assert r3.exit_code == 1, r3.output


def test_add_from_file_argument(tmp_path: Path):
    scaffold_demo(tmp_path)
    m = tmp_path / "model"
    f = tmp_path / "prop.json"
    f.write_text(json.dumps(_PROP), encoding="utf-8")
    r = runner.invoke(app, ["decisions", "add", str(f), "-m", str(m)])
    assert r.exit_code == 0, r.output
    assert len(_pending(m)) == 1
