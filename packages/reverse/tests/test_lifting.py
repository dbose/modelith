"""Physical->logical lifting heuristic tests (spec §6.3)."""

from __future__ import annotations

from mdl_reverse import lifting


def test_surrogate_key_detection():
    assert lifting.is_surrogate_key("customer_sk")
    assert lifting.is_surrogate_key("order_hashkey")
    assert lifting.is_surrogate_key("dbt_scd_id")
    assert not lifting.is_surrogate_key("customer_id")
    assert not lifting.is_surrogate_key("legal_name")


def test_staging_exclusion():
    assert lifting.is_staging("stg_customers")
    assert lifting.is_staging("int_orders_joined")
    assert lifting.is_staging("dim_customer", tags=["staging"])
    assert lifting.is_staging("customer", path="models/staging/customer.sql")
    assert not lifting.is_staging("dim_customer")


def test_scd2_detection():
    d = lifting.detect_scd2(["customer_id", "valid_from", "valid_to", "is_current"])
    assert d.is_scd2
    assert d.from_col == "valid_from" and d.to_col == "valid_to"
    assert set(d.tracking_cols) == {"valid_from", "valid_to", "is_current"}

    d2 = lifting.detect_scd2(["customer_id", "name"])
    assert not d2.is_scd2


def test_data_vault_detection():
    assert lifting.detect_data_vault("hub_customer", ["customer_hashkey", "customer_id"]).kind == "hub"
    assert lifting.detect_data_vault("link_order", ["order_hashkey"]).kind == "link"
    assert (
        lifting.detect_data_vault("sat_customer", ["customer_hashkey", "hashdiff", "load_dts"]).kind
        == "satellite"
    )
    assert lifting.detect_data_vault("dim_customer", ["customer_id"]).kind is None


def test_business_key_candidates():
    bks = lifting.business_key_candidates("dim_customer", ["customer_sk", "customer_id", "name"])
    assert "customer_id" in bks
    assert "customer_sk" not in bks


def test_natural_key_not_stripped_as_surrogate():
    """F5 regression: a conformed dimension's natural `<entity>_key` (date_key on
    dim_date) is NOT a hash surrogate. But a VARCHAR `_key` still is."""
    # names the entity -> natural key, keep
    assert not lifting.is_surrogate_key("date_key", entity="dim_date", data_type="BIGINT")
    assert not lifting.is_surrogate_key("product_key", entity="dim_product", data_type="INTEGER")
    # hash type or no entity match -> surrogate, strip
    assert lifting.is_surrogate_key("product_key", entity="dim_other", data_type="VARCHAR")
    assert lifting.is_surrogate_key("customer_sk", entity="dim_customers", data_type="VARCHAR")
    # date_key becomes the dimension's business key
    bks = lifting.business_key_candidates(
        "dim_date", ["date_key", "full_date"], {"date_key": "BIGINT"}
    )
    assert "date_key" in bks


def test_reverse_naming_override_is_additive():
    """A --naming override UNIONs with the defaults, so a medallion `gold_`/`bronze_`
    shop is handled without losing the built-in mart_/stg_ conventions."""
    n = lifting.ReverseNaming.merged({
        "rollup_prefixes": ["gold_"],
        "staging_prefixes": ["bronze_", "silver_"],
    })
    # override recognised
    assert lifting.is_reporting_rollup("gold_kpi", ["d", "k"], {}, naming=n)
    assert lifting.is_staging("bronze_raw", naming=n)
    # defaults still recognised
    assert lifting.is_reporting_rollup("mart_x", ["d", "k"], {}, naming=n)
    assert lifting.is_staging("stg_x", naming=n)
    # without the override, gold_ is neither
    assert not lifting.is_reporting_rollup("gold_kpi", ["d", "k"], {})
    assert not lifting.is_staging("bronze_raw")


def test_reverse_naming_merged_empty_is_defaults():
    assert lifting.ReverseNaming.merged(None) == lifting.DEFAULT_NAMING
    assert lifting.ReverseNaming.merged({}) == lifting.DEFAULT_NAMING


def test_classification_summary_surfaces_misfire():
    """The --review summary groups classifications so a misfire is visible: a keyless
    managed entity (a rollup the tool didn't recognise) lands in entities_keyless, so
    the engineer sees it without a prompt wall."""
    from mdl_core.ids import new_ulid
    from mdl_core.ir import (
        Attribute,
        ConceptualEntity,
        LogicalEntity,
        Model,
        ProjectConfig,
    )
    from mdl_reverse.ledger import Confidence, Decision, Verdict
    from mdl_reverse.reverse import ReverseResult, classification_summary

    m = Model(ProjectConfig(name="t"))
    # a real entity (has a business key)
    m.add(LogicalEntity(
        id=new_ulid(), name="dim_customers",
        attributes=[Attribute(id=new_ulid(), name="customer_id", role="business_key")],
    ))
    # a keyless managed entity (an unrecognised rollup — should be flagged)
    m.add(LogicalEntity(
        id=new_ulid(), name="gold_kpi",
        attributes=[Attribute(id=new_ulid(), name="metric")],
    ))
    _ = ConceptualEntity  # (entities need no CE for this summary)
    proposals = [
        Decision(kind="strip_column", signal="surrogate_key", confidence=Confidence.medium_high,
                 subject="strip", evidence={"model": "dim_customers", "column": "customer_sk",
                                            "reason": "surrogate_key"}, verdict=Verdict.accepted),
        Decision(kind="reporting_rollup", signal="rollup_naming", confidence=Confidence.medium_high,
                 subject="rollup", evidence={"model": "mart_x"}, verdict=Verdict.accepted),
    ]
    result = ReverseResult(model=m, proposals=proposals, excluded=["stg_a", "int_b"])
    s = classification_summary(result)

    assert "dim_customers" in s.entities_kept
    assert "gold_kpi" in s.entities_keyless          # the surfaced misfire
    assert "dim_customers.customer_sk" in s.surrogate_keys_stripped
    assert "mart_x" in s.rollups_unmanaged
    assert s.excluded_staging == ["int_b", "stg_a"]  # sorted


def test_reporting_rollup_detection():
    """F4 regression: a keyless mart_/kpi_ rollup is a reporting table, not a governed
    business entity. A mart WITH a business key stays a real entity."""
    assert lifting.is_reporting_rollup("mart_kpi_00", ["metric_date", "kpi"], {"kpi": "DOUBLE"})
    assert lifting.is_reporting_rollup("mart_daily_revenue", ["day", "revenue"], {})
    assert lifting.is_reporting_rollup("agg_sales", ["month", "total"], {})
    # has a business key -> real entity, not a rollup
    assert not lifting.is_reporting_rollup(
        "mart_customer_360", ["customer_id", "ltv"], {"customer_id": "BIGINT"}
    )
    # not a rollup name -> not a rollup even if keyless
    assert not lifting.is_reporting_rollup("dim_junk", ["a", "b"], {})


def test_foreign_key_candidates():
    guesses = lifting.foreign_key_candidates(
        "fct_orders",
        ["order_id", "customer_id", "product_id", "amount"],
        known_models={"dim_customer", "dim_product", "fct_orders"},
    )
    targets = {g.target_entity for g in guesses}
    assert "dim_customer" in targets and "dim_product" in targets
    # own key not treated as FK
    assert all(g.column != "order_id" for g in guesses)
    assert all(g.confidence == "medium" for g in guesses)  # propose, never auto-accept
