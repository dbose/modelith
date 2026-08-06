"""Enumerated domains + CodeSet reference data (P0-pre-b): IR + validation."""

from __future__ import annotations

from mdl_core.ir import CodeSet, CodeValue, Domain, Model, ProjectConfig
from mdl_core.validate import validate


def _errors(m):
    return sorted({d.code for d in validate(m).items if d.severity.value == "error"})


def test_inline_enum_domain_valid():
    m = Model(ProjectConfig(name="x"))
    m.add(Domain(id="01D", name="status", base_type="string",
                 allowed_values=["open", "closed"]))
    assert _errors(m) == []


def test_code_set_backed_domain_valid():
    m = Model(ProjectConfig(name="x"))
    m.add(CodeSet(id="01CS", name="currency",
                  values=[CodeValue(code="USD"), CodeValue(code="EUR", label="Euro")]))
    m.add(Domain(id="01D", name="ccy", base_type="string", value_set="currency"))
    assert _errors(m) == []
    assert m.code_set_by_name("currency").values[1].label == "Euro"


def test_unknown_value_set_errors():
    m = Model(ProjectConfig(name="x"))
    m.add(Domain(id="01D", name="ccy", base_type="string", value_set="nonesuch"))
    assert "MDL-E109" in _errors(m)
