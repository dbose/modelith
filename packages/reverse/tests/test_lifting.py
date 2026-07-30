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
