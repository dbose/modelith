"""Decision ledger tests (spec §6.2)."""

from __future__ import annotations

from pathlib import Path

from mdl_reverse.ledger import Confidence, Decision, DecisionLedger, Verdict


def _mk(subject="x", ev=None, signal="name_type"):
    return Decision(
        kind="relationship",
        signal=signal,
        confidence=Confidence.medium,
        subject=subject,
        evidence=ev or {"from": "a", "column": "b_id", "to": "b"},
    )


def test_rejected_not_reproposed_same_signal():
    ledger = DecisionLedger()
    d = _mk()
    assert ledger.should_propose(d)
    ledger.record(d)
    ledger.set_verdict(d.signal_key, Verdict.rejected)
    # Same evidence -> same signal_key -> not re-proposed (§6.2).
    assert not ledger.should_propose(_mk())


def test_reproposed_when_signal_changes():
    ledger = DecisionLedger()
    d = _mk()
    ledger.record(d)
    ledger.set_verdict(d.signal_key, Verdict.rejected)
    # Different evidence -> different key -> proposed again.
    changed = _mk(ev={"from": "a", "column": "b_id", "to": "c"})
    assert ledger.should_propose(changed)


def test_accepted_not_reproposed():
    ledger = DecisionLedger()
    d = _mk()
    ledger.record(d)
    ledger.set_verdict(d.signal_key, Verdict.accepted)
    assert not ledger.should_propose(_mk())


def test_ledger_persists_roundtrip(tmp_path: Path):
    ledger = DecisionLedger()
    d = _mk(subject="trade.cpty_id -> counterparty")
    ledger.record(d)
    ledger.set_verdict(d.signal_key, Verdict.accepted)
    ledger.save(tmp_path)

    assert (tmp_path / ".mdl" / "decisions.yaml").exists()
    reloaded = DecisionLedger.load(tmp_path)
    assert len(reloaded.decisions) == 1
    got = next(iter(reloaded.decisions.values()))
    assert got.verdict == Verdict.accepted
    assert got.subject == "trade.cpty_id -> counterparty"
    # verdict survives a re-run: not re-proposed
    assert not reloaded.should_propose(d)
