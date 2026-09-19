"""The auto-accept confidence FLOOR (reverse.auto_accept).

Reverse auto-accepts any inference at or above a confidence floor and leaves the rest
`proposed` for manual review. `none` reviews everything regardless of score; the default
floor (medium-high) preserves the historical auto_accept_high=True behaviour. This is the
knob a cautious team turns to gate every inference through the Reverse Review panel.
"""

from __future__ import annotations

from mdl_reverse.ddl_projection import ddl_projection
from mdl_reverse.ledger import (
    DEFAULT_AUTO_ACCEPT,
    Confidence,
    DecisionLedger,
    Verdict,
    parse_auto_accept,
    verdict_for,
)
from mdl_reverse.reverse import reverse

TARGET = "duckdb_dev"

# A declared FK (HIGH), a surrogate to strip (MEDIUM-HIGH), and an undeclared *_id that
# only the name heuristic catches (MEDIUM) — one of each band to test the floor.
_DDL = """
CREATE TABLE customer (
  customer_id BIGINT PRIMARY KEY,
  customer_sk VARCHAR NOT NULL,
  region_id BIGINT REFERENCES region(region_id),
  country_id BIGINT
);
CREATE TABLE region (
  region_id BIGINT PRIMARY KEY,
  region_name TEXT
);
CREATE TABLE country (
  country_id BIGINT PRIMARY KEY,
  iso TEXT
);
"""


def _reverse(auto_accept):
    proj = ddl_projection(_DDL)
    ledger = DecisionLedger()
    result = reverse(
        proj,
        project_name="warehouse",
        target=TARGET,
        ledger=ledger,
        interactive=False,
        auto_accept=auto_accept,
        naming=None,
    )
    return ledger, result


def _by_conf(ledger, verdict):
    return {d.confidence for d in ledger.decisions.values() if d.verdict == verdict}


# --- the parser --------------------------------------------------------------


def test_parse_auto_accept_levels():
    assert parse_auto_accept("high") is Confidence.high
    assert parse_auto_accept("medium-high") is Confidence.medium_high
    assert parse_auto_accept("medium_high") is Confidence.medium_high  # underscore ok
    assert parse_auto_accept("MEDIUM") is Confidence.medium
    assert parse_auto_accept("none") is None
    assert parse_auto_accept("off") is None
    assert parse_auto_accept(False) is None
    assert parse_auto_accept(True) is DEFAULT_AUTO_ACCEPT
    assert parse_auto_accept(None) is DEFAULT_AUTO_ACCEPT


def test_parse_auto_accept_rejects_garbage():
    import pytest

    with pytest.raises(ValueError):
        parse_auto_accept("sometimes")


def test_verdict_for_floor():
    # floor = medium-high: high & medium-high accepted; medium & low proposed
    f = Confidence.medium_high
    assert verdict_for(Confidence.high, f) is Verdict.accepted
    assert verdict_for(Confidence.medium_high, f) is Verdict.accepted
    assert verdict_for(Confidence.medium, f) is Verdict.proposed
    # floor = None: nothing auto-accepts
    assert verdict_for(Confidence.high, None) is Verdict.proposed


# --- the floor end-to-end through the engine ---------------------------------


def test_none_floor_proposes_everything():
    """auto_accept=None (manual-always): every inference, including the HIGH declared
    FK, is left proposed for review — nothing auto-accepts."""
    ledger, _ = _reverse(None)
    assert ledger.decisions, "expected some inferences"
    assert all(d.verdict == Verdict.proposed for d in ledger.decisions.values())
    # the HIGH FK is among the pending ones
    assert Confidence.high in _by_conf(ledger, Verdict.proposed)


def test_default_floor_matches_legacy_behaviour():
    """Default floor (medium-high): HIGH + MEDIUM-HIGH accepted, MEDIUM proposed —
    the historical auto_accept_high=True behaviour, unchanged."""
    ledger, _ = _reverse(DEFAULT_AUTO_ACCEPT)
    accepted = _by_conf(ledger, Verdict.accepted)
    proposed = _by_conf(ledger, Verdict.proposed)
    assert Confidence.high in accepted
    assert Confidence.medium_high in accepted
    assert Confidence.medium in proposed
    assert Confidence.medium not in accepted


def test_high_floor_reviews_medium_high():
    """Floor = high: only the declared FK auto-accepts; the surrogate strip
    (medium-high) drops to manual review."""
    ledger, _ = _reverse(Confidence.high)
    accepted = _by_conf(ledger, Verdict.accepted)
    proposed = _by_conf(ledger, Verdict.proposed)
    assert accepted == {Confidence.high}
    assert Confidence.medium_high in proposed


def test_medium_floor_accepts_and_materialises_heuristic_fk():
    """Floor = medium: the name-heuristic FK (customer.home_country_id -> country) is
    accepted AND materialised into the model, like a declared relationship would be."""
    ledger, result = _reverse(Confidence.medium)
    accepted = _by_conf(ledger, Verdict.accepted)
    assert Confidence.medium in accepted
    # the medium heuristic relationship is now in the model, not just the ledger
    name_by_id = {le.id: le.name for le in result.model.logical_entities.values()}
    rels = {
        (name_by_id.get(r.from_.entity, r.from_.entity), name_by_id.get(r.to.entity, r.to.entity))
        for r in result.model.relationships.values()
    }
    assert ("customer", "country") in rels
