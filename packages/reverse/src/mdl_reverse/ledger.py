"""Decision ledger — .mdl/decisions.yaml (spec §6.2).

Reverse engineering never guesses silently. Every inference is a recorded
decision: it proposes, a human accepts or rejects, and the choice is persisted so
the next run does not re-ask (§0.1.5). Rejected proposals are never re-proposed
unless the underlying signal changes — we detect "changed" via a stable
`signal_key` hash over the evidence.

The file is comment-preserving YAML (via mdl_core.yaml_io) and committed to git.
It is what makes the second run fast and the tenth run trustworthy.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from mdl_core.yaml_io import dump_str, load_str

LEDGER_REL = ".mdl/decisions.yaml"


class Verdict(str, Enum):
    proposed = "proposed"  # awaiting a human
    accepted = "accepted"
    rejected = "rejected"


class Confidence(str, Enum):
    high = "high"
    medium_high = "medium-high"
    medium = "medium"
    low = "low"


@dataclass
class Decision:
    kind: str  # e.g. "relationship", "scd2_pattern", "surrogate_key"
    signal: str  # which signal produced it, e.g. "fk_constraint", "name_type"
    confidence: Confidence
    subject: str  # human-readable description of the proposal
    verdict: Verdict = Verdict.proposed
    # Structured evidence used both to render the proposal and to hash a signal_key.
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def signal_key(self) -> str:
        """Stable hash over (kind, signal, evidence). Same evidence => same key,
        so a rejected proposal is recognised and not re-proposed. If the evidence
        changes (the signal changed), the key changes and it is re-proposed."""
        blob = _canonical(
            {"kind": self.kind, "signal": self.signal, "evidence": self.evidence}
        )
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _canonical(obj: Any) -> str:
    import json

    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


@dataclass
class DecisionLedger:
    decisions: dict[str, Decision] = field(default_factory=dict)  # signal_key -> Decision

    # --- proposal flow -----------------------------------------------------

    def should_propose(self, candidate: Decision) -> bool:
        """True unless we already have a verdict on this exact signal."""
        prior = self.decisions.get(candidate.signal_key)
        if prior is None:
            return True
        # Already accepted or rejected for this evidence -> don't re-ask (§6.2).
        return prior.verdict == Verdict.proposed

    def record(self, decision: Decision) -> None:
        self.decisions[decision.signal_key] = decision

    def set_verdict(self, signal_key: str, verdict: Verdict) -> None:
        if signal_key in self.decisions:
            self.decisions[signal_key].verdict = verdict

    def accepted(self) -> list[Decision]:
        return [d for d in self.decisions.values() if d.verdict == Verdict.accepted]

    def pending(self) -> list[Decision]:
        return [d for d in self.decisions.values() if d.verdict == Verdict.proposed]

    # --- persistence -------------------------------------------------------

    @classmethod
    def load(cls, root: Path) -> DecisionLedger:
        p = root / LEDGER_REL
        if not p.exists():
            return cls()
        data = load_str(p.read_text(encoding="utf-8")) or {}
        decisions: dict[str, Decision] = {}
        for entry in data.get("decisions", []) or []:
            d = Decision(
                kind=str(entry.get("kind")),
                signal=str(entry.get("signal")),
                confidence=Confidence(entry.get("confidence", "medium")),
                subject=str(entry.get("subject", "")),
                verdict=Verdict(entry.get("verdict", "proposed")),
                evidence=dict(entry.get("evidence") or {}),
            )
            decisions[d.signal_key] = d
        return cls(decisions=decisions)

    def save(self, root: Path) -> None:
        p = root / LEDGER_REL
        p.parent.mkdir(parents=True, exist_ok=True)
        # Deterministic ordering by (kind, subject) for diff-friendliness.
        ordered = sorted(self.decisions.values(), key=lambda d: (d.kind, d.subject))
        doc = {
            "decisions": [
                {
                    "kind": d.kind,
                    "signal": d.signal,
                    "confidence": d.confidence.value,
                    "subject": d.subject,
                    "verdict": d.verdict.value,
                    "evidence": d.evidence,
                }
                for d in ordered
            ]
        }
        p.write_text(dump_str(doc), encoding="utf-8")
