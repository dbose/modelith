"""Tests for the Pydantic v2 model emitter."""

from __future__ import annotations

from mdl_emit_pydantic import emit_pydantic_models

from mdl_core.ir import (
    Attribute,
    Domain,
    LogicalEntity,
    Model,
    ProjectConfig,
)


def _model() -> Model:
    m = Model(ProjectConfig(name="sales"))
    m.domains["d1"] = Domain(id="d1", name="id_bigint", base_type="bigint")
    m.domains["d2"] = Domain(id="d2", name="text", base_type="string")
    m.domains["d3"] = Domain(id="d3", name="ts", base_type="timestamp")
    m.domains["d4"] = Domain(
        id="d4", name="status_dom", base_type="string", allowed_values=["open", "closed"]
    )
    m.logical_entities["e1"] = LogicalEntity(
        id="e1",
        name="customer_order",
        attributes=[
            Attribute(id="a1", name="order_id", domain="id_bigint", nullable=False),
            Attribute(id="a2", name="placed_at", domain="ts", nullable=True),
            Attribute(id="a3", name="status", domain="status_dom", nullable=False),
        ],
    )
    m.logical_entities["e2"] = LogicalEntity(
        id="e2", name="staging", attributes=[], unmanaged=True
    )
    return m


def _load_generated(src: str, tmp_path):
    """Write the generated source and import it as a real module.

    Pydantic resolves annotations against a module's globals, so the realistic
    check is importing a real file (how a consumer uses `mdl emit pydantic`),
    not exec() into a bare dict.
    """
    import importlib.util

    p = tmp_path / "generated_models.py"
    p.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("generated_models", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_emitted_source_is_importable_and_correct(tmp_path):
    import pytest
    from pydantic import ValidationError

    mod = _load_generated(emit_pydantic_models(_model()), tmp_path)
    Order = mod.CustomerOrder  # PascalCase class name
    assert not hasattr(mod, "Staging")  # unmanaged excluded

    # Valid instance
    inst = Order(order_id=1, placed_at=None, status="open")
    assert inst.order_id == 1

    # Non-null field enforced
    with pytest.raises(ValidationError):
        Order(placed_at=None, status="open")  # missing order_id

    # Literal enum enforced
    with pytest.raises(ValidationError):
        Order(order_id=1, status="not_a_status")


def test_types_and_optionality():
    src = emit_pydantic_models(_model())
    assert "order_id: int" in src
    assert "Optional[datetime] = None" in src
    assert "Literal['open', 'closed']" in src
    assert "from datetime import datetime" in src
