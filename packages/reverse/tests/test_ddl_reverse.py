"""Reverse a raw SQL DDL script through the full reverse engine.

DDL is projected to a ManifestProjection (ddl_projection) and flows through the SAME
reverse() as a dbt manifest — so it gets surrogate stripping, staging exclusion, SCD2
detection, confidence bands, and the ledger. Nullability + declared keys come from the
DDL, which the manifest path can't recover."""

from __future__ import annotations

from mdl_reverse.ddl_projection import ddl_projection
from mdl_reverse.drift import DriftSeverity  # noqa: F401 - keep import surface stable
from mdl_reverse.ledger import Confidence, DecisionLedger, Verdict
from mdl_reverse.reverse import reverse

TARGET = "duckdb_dev"

_DDL = """
CREATE TABLE customer (
  customer_id BIGINT PRIMARY KEY,
  customer_sk VARCHAR NOT NULL,
  legal_name TEXT NOT NULL,
  region_id BIGINT REFERENCES region(region_id)
);
CREATE TABLE region (
  region_id BIGINT PRIMARY KEY,
  region_name TEXT
);
CREATE TABLE stg_raw_events (
  id BIGINT,
  payload TEXT
);
"""


def _reverse(ddl: str, dialect: str | None = None):
    proj = ddl_projection(ddl, dialect=dialect)
    ledger = DecisionLedger()
    return reverse(
        proj,
        project_name="warehouse",
        target=TARGET,
        ledger=ledger,
        interactive=False,
        naming=None,
    )


# --- the projection bridge ---------------------------------------------------


def test_projection_carries_nullability_and_fk():
    proj = ddl_projection(_DDL)
    cust = proj.models["customer"]
    # NOT NULL / PK => not nullable; a plain column => nullable
    assert cust.columns["customer_id"].meta["nullable"] is False
    assert cust.columns["legal_name"].meta["nullable"] is False
    assert cust.columns["region_id"].meta["nullable"] is True
    # a declared DDL FK becomes a relationship_test (channel = HIGH confidence)
    assert ("region_id", "region") in cust.relationship_tests
    assert cust.columns["customer_id"].data_type == "bigint"


# --- the engine on DDL -------------------------------------------------------


def test_ddl_reverse_strips_surrogate_key():
    result = _reverse(_DDL)
    customer = next(e for e in result.model.logical_entities.values() if e.name == "customer")
    attrs = {a.name for a in customer.attributes}
    assert "customer_sk" not in attrs  # stripped as a surrogate key
    assert {"customer_id", "legal_name", "region_id"} <= attrs


def test_ddl_reverse_excludes_staging():
    result = _reverse(_DDL)
    names = {e.name for e in result.model.logical_entities.values()}
    assert "stg_raw_events" not in names
    assert "stg_raw_events" in result.excluded


def test_ddl_reverse_honours_nullability():
    result = _reverse(_DDL)
    customer = next(e for e in result.model.logical_entities.values() if e.name == "customer")
    by_name = {a.name: a for a in customer.attributes}
    assert by_name["customer_id"].nullable is False  # PK
    assert by_name["legal_name"].nullable is False  # NOT NULL
    assert by_name["region_id"].nullable is True


def test_ddl_declared_fk_is_high_confidence_relationship():
    result = _reverse(_DDL)
    # the FK is auto-accepted (high confidence) and materialised as a relationship
    assert len(result.model.relationships) == 1
    rel = next(iter(result.model.relationships.values()))
    le = {e.id: e.name for e in result.model.logical_entities.values()}
    assert le[rel.from_.entity] == "customer"
    assert le[rel.to.entity] == "region"
    # and it's recorded as a high-confidence relationship proposal
    rels = [p for p in result.proposals if p.kind == "relationship"]
    assert any(p.confidence == Confidence.high and p.verdict == Verdict.accepted for p in rels)


def test_ddl_reverse_produces_a_valid_model(tmp_path):
    from mdl_core.validate import validate
    from mdl_reverse.writer import write_model

    result = _reverse(_DDL)
    write_model(result.model, tmp_path)
    # reload and validate
    from mdl_core.repo import ModelRepo

    diags = validate(ModelRepo.load(tmp_path).model)
    from mdl_core.diagnostics import Severity

    assert not diags.has(Severity.error), [d.message for d in diags.items]


def test_ddl_reverse_composite_pk():
    ddl = """
    CREATE TABLE position (
      portfolio_code TEXT NOT NULL,
      instrument_id BIGINT NOT NULL,
      as_of_date DATE NOT NULL,
      quantity DECIMAL,
      PRIMARY KEY (portfolio_code, instrument_id, as_of_date)
    );
    """
    result = _reverse(ddl)
    position = next(e for e in result.model.logical_entities.values() if e.name == "position")
    bks = {a.name for a in position.attributes if a.role == "business_key"}
    assert bks == {"portfolio_code", "instrument_id", "as_of_date"}
