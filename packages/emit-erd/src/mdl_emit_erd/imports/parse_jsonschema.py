"""Parse JSON Schema into an ImportedModel.

Two accepted shapes, both common: (a) a top-level `$defs`/`definitions` map of named
object schemas (one entity per definition), or (b) a single top-level object schema
(one entity). Each object's `properties` become attributes; `required` drives
nullability; `type`/`format` fold to a base type. JSON Schema has no first-class
foreign keys, so relationships are not inferred (a warning says so) unless a property
carries an explicit `$ref` to another definition, which we treat as a FK."""

from __future__ import annotations

import json

from mdl_emit_erd.imports.model import ImportedColumn, ImportedFk, ImportedModel, ImportedTable

_JSON_TYPE = {
    "integer": "integer",
    "number": "decimal",
    "string": "string",
    "boolean": "boolean",
}
_FORMAT = {"date": "date", "date-time": "timestamp", "uuid": "string"}


def parse_json_schema(text: str) -> ImportedModel:
    model = ImportedModel()
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        model.warnings.append(f"invalid JSON: {e}")
        return model
    if not isinstance(doc, dict):
        model.warnings.append("top-level JSON Schema must be an object")
        return model

    defs = doc.get("$defs") or doc.get("definitions")
    entries: dict[str, dict] = {}
    if isinstance(defs, dict) and defs:
        entries = {k: v for k, v in defs.items() if isinstance(v, dict)}
    else:
        name = doc.get("title") or "entity"
        entries = {name: doc}

    ref_names = set(entries.keys())
    for name, schema in entries.items():
        t = ImportedTable(name=name, definition=schema.get("description"))
        required = set(schema.get("required") or [])
        props = schema.get("properties") or {}
        for pname, pschema in props.items():
            if not isinstance(pschema, dict):
                continue
            col = ImportedColumn(
                name=pname,
                base_type=_base_type(pschema),
                nullable=pname not in required,
                definition=pschema.get("description"),
            )
            # a $ref to another definition is treated as a foreign key
            ref = _ref_name(pschema)
            if ref and ref in ref_names:
                t.foreign_keys.append(ImportedFk(columns=[pname], ref_table=ref, ref_columns=[]))
            t.columns.append(col)
        model.tables.append(t)

    if not any(t.foreign_keys for t in model.tables):
        model.warnings.append(
            "JSON Schema has no native foreign keys; relationships were not inferred "
            "(a property $ref to another definition is treated as one when present)."
        )
    if not model.tables:
        model.warnings.append("no object schemas found")
    return model


def _base_type(pschema: dict) -> str:
    fmt = pschema.get("format")
    if fmt in _FORMAT:
        return _FORMAT[fmt]
    jt = pschema.get("type")
    if isinstance(jt, list):  # e.g. ["string", "null"]
        jt = next((x for x in jt if x != "null"), "string")
    return _JSON_TYPE.get(jt, "string")


def _ref_name(pschema: dict) -> str | None:
    ref = pschema.get("$ref")
    if not ref and pschema.get("type") == "array":
        ref = (pschema.get("items") or {}).get("$ref")
    if isinstance(ref, str) and ref:
        return ref.rsplit("/", 1)[-1]  # "#/$defs/Address" -> "Address"
    return None
