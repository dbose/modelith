"""Open Data Contract Standard (ODCS v3) emitter.

Modelith as a Data Contract Factory: take a snapshot of the current model and emit
a pristine, valid ODCS v3 `datacontract.yaml`. One contract document; each managed
logical entity becomes an ODCS schema object, each attribute a property.

ODCS is the Bitol / Linux Foundation standard (supersedes the deprecated Data
Contract Specification). We target v3.0.x. The contract carries the schema plus the
governance context Modelith already holds: descriptions and ownership from the
realised conceptual entity, primary/unique keys from key groups, and enumerations
from domains / code sets as valid values.
"""

from __future__ import annotations

import io

from ruamel.yaml import YAML

from mdl_core.ir import Model
from mdl_emit_contract.types import odcs_logical_type

ODCS_API_VERSION = "v3.0.2"


def _description_for(model: Model, le) -> str | None:
    """Definition from the conceptual entity this logical entity realises."""
    if le.realises and le.realises in model.conceptual_entities:
        return model.conceptual_entities[le.realises].definition
    return None


def _stewardship_for(model: Model, le):
    if le.realises and le.realises in model.conceptual_entities:
        return model.conceptual_entities[le.realises].stewardship
    return None


def _valid_values(model: Model, attr) -> list | None:
    """Inline allowed_values, or a referenced CodeSet's codes."""
    dom = model.domain_by_name(attr.domain)
    if dom is None:
        return None
    if dom.allowed_values:
        return list(dom.allowed_values)
    if dom.value_set:
        cs = model.code_set_by_name(dom.value_set)
        if cs is not None:
            return [cv.code for cv in cs.values]
    return None


def _pk_member_ids(model: Model, entity_id: str) -> set[str]:
    """Attribute ULIDs that are members of this entity's primary key group."""
    for kg in model.key_groups.values():
        if kg.entity == entity_id and kg.type == "pk":
            return set(kg.members)
    return set()


def _unique_member_ids(model: Model, entity_id: str) -> set[str]:
    """Attribute ULIDs in any alternate/unique key group for this entity."""
    out: set[str] = set()
    for kg in model.key_groups.values():
        if kg.entity == entity_id and kg.type in ("alternate", "unique"):
            out.update(kg.members)
    return out


def _property(model: Model, entity_id: str, attr, pk_ids: set[str], uk_ids: set[str]) -> dict:
    dom = model.domain_by_name(attr.domain)
    base = dom.base_type if dom else None
    prop: dict = {
        "name": attr.name,
        "logicalType": odcs_logical_type(base),
        "required": not attr.nullable,
    }
    if attr.id in pk_ids:
        prop["primaryKey"] = True
    if attr.id in uk_ids and attr.id not in pk_ids:
        prop["unique"] = True
    values = _valid_values(model, attr)
    if values is not None:
        # ODCS quality: an enum of permitted values.
        prop["quality"] = [{"type": "library", "rule": "validValues", "validValues": values}]
    if dom is not None and dom.definition:
        prop["description"] = dom.definition
    return prop


def build_contract(model: Model, *, contract_id: str | None = None) -> dict:
    """Build the ODCS v3 contract document as a plain dict."""
    name = model.config.name if model.config and model.config.name else "modelith_model"
    schema_objects: list[dict] = []

    for le in sorted(model.logical_entities.values(), key=lambda e: e.name):
        if le.unmanaged:
            continue
        pk_ids = _pk_member_ids(model, le.id)
        uk_ids = _unique_member_ids(model, le.id)
        obj: dict = {
            "name": le.name,
            "logicalType": "object",
            "physicalName": le.name,
            "properties": [
                _property(model, le.id, attr, pk_ids, uk_ids) for attr in le.attributes
            ],
        }
        desc = _description_for(model, le)
        if desc:
            obj["description"] = desc
        schema_objects.append(obj)

    contract: dict = {
        "apiVersion": ODCS_API_VERSION,
        "kind": "DataContract",
        "id": contract_id or name,
        "version": "1.0.0",
        "status": "draft",
        "name": name,
        "schema": schema_objects,
    }

    # Ownership: promote a steward/owner if any entity carries one, into ODCS roles.
    roles: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for le in model.logical_entities.values():
        st = _stewardship_for(model, le)
        if st is None:
            continue
        for role_name, person in (("owner", st.owner), ("steward", st.steward)):
            if person and (role_name, person) not in seen:
                seen.add((role_name, person))
                roles.append({"role": role_name, "description": person})
    if roles:
        contract["roles"] = roles

    return contract


def emit_datacontract(model: Model, *, contract_id: str | None = None) -> str:
    """Emit an ODCS v3 data contract as YAML text."""
    contract = build_contract(model, contract_id=contract_id)
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.sort_keys = False
    buf = io.StringIO()
    yaml.dump(contract, buf)
    return buf.getvalue()
