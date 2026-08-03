"""Typed mutation commands over the model repo (E2; lives in core so the
HTTP server and the LSP server share one implementation).

The canvas never writes files; it sends semantic commands. Each handler mutates
the raw ruamel YAML nodes through ModelRepo (comment-preserving, same pattern as
`mdl lint --fix` and `mdl drift --reconcile`), saves, re-validates, and returns
fresh diagnostics. ULIDs are minted here, server-side, at creation — and never
touched on rename (property 4).

Concurrency: every command carries the directory fingerprint the client based
its edit on. If the repo changed underneath (git pull, IDE edit), the command is
rejected with 409 semantics instead of clobbering.

Git remains the only state: commands write the working tree; committing is the
user's act (see git_api.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from mdl_core.diagnostics import Severity
from mdl_core.ids import new_ulid
from mdl_core.repo import ModelRepo
from mdl_core.validate import validate

# ---------------------------------------------------------------------------


def dir_fingerprint(model_dir: Path) -> str:
    """Cheap change detector over model YAML files (count/mtime/size), string-
    encoded so it can round-trip through JSON."""
    n, mtime, size = 0, 0.0, 0
    for p in Path(model_dir).rglob("*.yaml"):
        st = p.stat()
        n += 1
        mtime = max(mtime, st.st_mtime)
        size += st.st_size
    return f"{n}:{mtime}:{size}"


class StaleModelError(Exception):
    """The client's view of the repo is out of date."""


class CommandError(Exception):
    """The command is invalid against the current model."""


@dataclass
class CommandResult:
    ok: bool
    fingerprint: str
    diagnostics: list[dict] = field(default_factory=list)
    created_id: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------


def apply_command(
    model_dir: Path, op: str, payload: dict, base_fingerprint: str | None = None
) -> CommandResult:
    model_dir = Path(model_dir)
    if base_fingerprint and dir_fingerprint(model_dir) != base_fingerprint:
        raise StaleModelError("model changed on disk — refresh before editing")

    handler = _HANDLERS.get(op)
    if handler is None:
        raise CommandError(f"unknown command {op!r}")

    repo = ModelRepo.load(model_dir)
    created_id = handler(repo, payload)
    repo.save()

    diags = validate(ModelRepo.load(model_dir).model)
    return CommandResult(
        ok=True,
        fingerprint=dir_fingerprint(model_dir),
        diagnostics=[
            {"code": d.code, "severity": d.severity.value, "message": d.message, "path": d.path}
            for d in diags.items
        ],
        created_id=created_id,
    )


# --- helpers ----------------------------------------------------------------


def _slug(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


def _require(payload: dict, *keys: str) -> None:
    missing = [k for k in keys if payload.get(k) in (None, "")]
    if missing:
        raise CommandError(f"missing field(s): {', '.join(missing)}")


def _node_for(repo: ModelRepo, ulid: str):
    rel = repo.path_for_ulid(ulid)
    if rel is None:
        raise CommandError(f"no object file for ULID {ulid}")
    return rel, repo.raw[rel]


def _conceptual_node(repo: ModelRepo, ulid: str):
    """Resolve an id that may be a logical entity (follow realises), conceptual
    entity, or term, to the node that owns ontology/definition fields."""
    le = repo.model.logical_entities.get(ulid)
    if le is not None:
        if not le.realises:
            raise CommandError(f"logical entity {le.name!r} has no conceptual entity")
        return _node_for(repo, le.realises)
    if ulid in repo.model.conceptual_entities or ulid in repo.model.terms:
        return _node_for(repo, ulid)
    raise CommandError(f"no conceptual object for {ulid}")


# --- entity lifecycle ---------------------------------------------------------


def _create_entity(repo: ModelRepo, p: dict) -> str:
    _require(p, "name")
    name = _slug(p["name"])
    if any(le.name == name for le in repo.model.logical_entities.values()):
        raise CommandError(f"entity {name!r} already exists")

    ce_id, le_id = new_ulid(), new_ulid()
    ce: dict = {
        "id": ce_id,
        "kind": "conceptual_entity",
        "name": p.get("display_name") or name.replace("_", " ").title().replace(" ", ""),
    }
    if p.get("subject_area"):
        ce["subject_area"] = p["subject_area"]
    if p.get("definition"):
        ce["definition"] = p["definition"]
    if p.get("layer"):
        ce["ontology"] = {"layer": p["layer"]}
    le: dict = {
        "id": le_id,
        "kind": "logical_entity",
        "name": name,
        "realises": ce_id,
        "attributes": [],
    }
    repo.add_file(f"conceptual/entities/{name}.yaml", ce, ce_id)
    repo.add_file(f"logical/entities/{name}.yaml", le, le_id)
    return le_id


def _rename_entity(repo: ModelRepo, p: dict) -> None:
    _require(p, "id", "name")
    le = repo.model.logical_entities.get(p["id"])
    if le is None:
        raise CommandError(f"no logical entity {p['id']}")
    new_name = _slug(p["name"])
    rel, node = _node_for(repo, le.id)
    node["name"] = new_name
    # keep the file name in step with the entity name (repo hygiene)
    repo.rename_file(rel, f"logical/entities/{new_name}.yaml")


def _delete_entity(repo: ModelRepo, p: dict) -> None:
    _require(p, "id")
    le = repo.model.logical_entities.get(p["id"])
    if le is None:
        raise CommandError(f"no logical entity {p['id']}")
    rels = [
        r
        for r in repo.model.relationships.values()
        if r.from_.entity == le.id or r.to.entity == le.id
    ]
    # Physical tables realise this logical entity; deleting the entity without
    # them would leave a dangling `realises` (validate would then error).
    physicals = [pt for pt in repo.model.physical_tables.values() if pt.realises == le.id]
    if (rels or physicals) and not p.get("cascade"):
        blockers = [f"{len(rels)} relationship(s)"] if rels else []
        if physicals:
            blockers.append(f"{len(physicals)} physical table(s)")
        raise CommandError(
            f"entity {le.name!r} is referenced by {', '.join(blockers)}; pass cascade=true"
        )
    for r in rels:
        rel_path = repo.path_for_ulid(r.id)
        if rel_path:
            repo.remove_file(rel_path)
    for pt in physicals:
        pt_path = repo.path_for_ulid(pt.id)
        if pt_path:
            repo.remove_file(pt_path)
    le_path = repo.path_for_ulid(le.id)
    if le_path:
        repo.remove_file(le_path)
    # drop the conceptual entity if nothing else realises it
    if le.realises:
        others = [
            x
            for x in repo.model.logical_entities.values()
            if x.realises == le.realises and x.id != le.id
        ]
        if not others:
            ce_path = repo.path_for_ulid(le.realises)
            if ce_path:
                repo.remove_file(ce_path)


# --- conceptual properties ----------------------------------------------------


def _set_definition(repo: ModelRepo, p: dict) -> None:
    _require(p, "id")
    _, node = _conceptual_node(repo, p["id"])
    if p.get("definition"):
        node["definition"] = p["definition"]
    else:
        node.pop("definition", None)


def _update_synonyms(repo: ModelRepo, p: dict) -> None:
    """Replace the synonym list on a conceptual entity or term (SME surface)."""
    _require(p, "id")
    _, node = _conceptual_node(repo, p["id"])
    syns = [str(s).strip() for s in (p.get("synonyms") or []) if str(s).strip()]
    if syns:
        node["synonyms"] = syns
    else:
        node.pop("synonyms", None)


def _set_stewardship(repo: ModelRepo, p: dict) -> None:
    _require(p, "id")
    _, node = _conceptual_node(repo, p["id"])
    st = node.setdefault("stewardship", {})
    for k in ("owner", "steward"):
        if p.get(k):
            st[k] = p[k]
        elif k in p:
            st.pop(k, None)
    if not st:
        node.pop("stewardship", None)


def _set_subject_area(repo: ModelRepo, p: dict) -> None:
    _require(p, "id")
    _, node = _conceptual_node(repo, p["id"])
    if p.get("subject_area"):
        if p["subject_area"] not in repo.model.subject_areas:
            raise CommandError(f"no subject area {p['subject_area']}")
        node["subject_area"] = p["subject_area"]
    else:
        node.pop("subject_area", None)


def _set_pattern(repo: ModelRepo, p: dict) -> None:
    _require(p, "id")
    le = repo.model.logical_entities.get(p["id"])
    if le is None:
        raise CommandError(f"no logical entity {p['id']}")
    _, node = _node_for(repo, le.id)
    if p.get("pattern"):
        node["pattern"] = p["pattern"]
    else:
        node.pop("pattern", None)


def _create_subject_area(repo: ModelRepo, p: dict) -> str:
    _require(p, "name")
    sa_id = new_ulid()
    sa: dict = {"id": sa_id, "kind": "subject_area", "name": p["name"]}
    if p.get("definition"):
        sa["definition"] = p["definition"]
    repo.add_file(f"conceptual/subject-areas/{_slug(p['name'])}.yaml", sa, sa_id)
    return sa_id


def _create_term(repo: ModelRepo, p: dict) -> str:
    _require(p, "name", "layer")
    t_id = new_ulid()
    t: dict = {"id": t_id, "kind": "term", "name": p["name"]}
    if p.get("definition"):
        t["definition"] = p["definition"]
    ont: dict = {"layer": p["layer"]}
    if p.get("aligns_to"):
        ont["aligns_to"] = p["aligns_to"]
        ont["alignment"] = p.get("alignment", "skos:closeMatch")
        # SME-created alignments are proposals until an architect promotes (§5.1)
        ont["status"] = p.get("status", "proposed")
    t["ontology"] = ont
    repo.add_file(f"conceptual/terms/{_slug(p['name'])}.yaml", t, t_id)
    return t_id


# --- attributes ----------------------------------------------------------------


def _entity_node(repo: ModelRepo, entity_id: str):
    le = repo.model.logical_entities.get(entity_id)
    if le is None:
        raise CommandError(f"no logical entity {entity_id}")
    return le, _node_for(repo, le.id)[1]


def _add_attribute(repo: ModelRepo, p: dict) -> str:
    _require(p, "entity_id", "name")
    le, node = _entity_node(repo, p["entity_id"])
    name = _slug(p["name"])
    if any(a.name == name for a in le.attributes):
        raise CommandError(f"attribute {name!r} already exists on {le.name!r}")
    attr_id = new_ulid()
    attr: dict = {
        "id": attr_id,
        "name": name,
        "domain": p.get("domain", "string"),
        "role": p.get("role", "attribute"),
        "nullable": bool(p.get("nullable", True)),
    }
    node.setdefault("attributes", []).append(attr)
    return attr_id


def _update_attribute(repo: ModelRepo, p: dict) -> None:
    _require(p, "entity_id", "attribute_id")
    _, node = _entity_node(repo, p["entity_id"])
    for attr in node.get("attributes", []):
        if attr.get("id") == p["attribute_id"]:
            for k in ("name", "domain", "role"):
                if p.get(k):
                    attr[k] = _slug(p[k]) if k == "name" else p[k]
            if "nullable" in p and p["nullable"] is not None:
                attr["nullable"] = bool(p["nullable"])
            return
    raise CommandError(f"no attribute {p['attribute_id']}")


def _delete_attribute(repo: ModelRepo, p: dict) -> None:
    _require(p, "entity_id", "attribute_id")
    aid = p["attribute_id"]
    for r in repo.model.relationships.values():
        if aid in r.from_.attributes or aid in r.to.attributes:
            raise CommandError(f"attribute is referenced by relationship {r.name!r}")
    _, node = _entity_node(repo, p["entity_id"])
    attrs = node.get("attributes", [])
    for i, attr in enumerate(attrs):
        if attr.get("id") == aid:
            del attrs[i]
            return
    raise CommandError(f"no attribute {aid}")


# --- relationships ---------------------------------------------------------------


def _create_relationship(repo: ModelRepo, p: dict) -> str:
    _require(p, "from_entity", "to_entity")
    frm = repo.model.logical_entities.get(p["from_entity"])
    to = repo.model.logical_entities.get(p["to_entity"])
    if frm is None or to is None:
        raise CommandError("both endpoints must be logical entities")
    name = _slug(p.get("name") or f"{frm.name}_to_{to.name}")
    if any(r.name == name for r in repo.model.relationships.values()):
        raise CommandError(f"relationship {name!r} already exists")

    from_attr = p.get("from_attribute")
    # One-gesture FK: mint the foreign-key column on the source, named after the
    # target's business key, with its domain — so drawing the link also creates
    # the column (no separate add-attribute step). No-op if the column exists.
    if p.get("create_from_fk"):
        to_bk = next((a for a in to.attributes if a.role == "business_key"), None)
        fk_name = _slug(p.get("from_fk_name") or (to_bk.name if to_bk else f"{to.name}_id"))
        fk_domain = to_bk.domain if to_bk else "string"
        existing = next((a for a in frm.attributes if a.name == fk_name), None)
        if existing:
            from_attr = existing.id
        else:
            _, frm_node = _entity_node(repo, frm.id)
            fk_id = new_ulid()
            frm_node.setdefault("attributes", []).append(
                {"id": fk_id, "name": fk_name, "domain": fk_domain,
                 "role": "attribute", "nullable": True}
            )
            from_attr = fk_id

    rel_id = new_ulid()
    rel: dict = {
        "id": rel_id,
        "kind": "relationship",
        "name": name,
        "from": {
            "entity": frm.id,
            "attributes": [from_attr] if from_attr else [],
        },
        "to": {
            "entity": to.id,
            "attributes": [p["to_attribute"]] if p.get("to_attribute") else [],
        },
        "cardinality": p.get("cardinality", "many_to_one"),
    }
    if p.get("optionality"):
        rel["optionality"] = p["optionality"]
    repo.add_file(f"logical/relationships/{name}.yaml", rel, rel_id)
    return rel_id


def _delete_relationship(repo: ModelRepo, p: dict) -> None:
    _require(p, "id")
    if p["id"] not in repo.model.relationships:
        raise CommandError(f"no relationship {p['id']}")
    rel_path = repo.path_for_ulid(p["id"])
    if rel_path:
        repo.remove_file(rel_path)


def _rename_relationship(repo: ModelRepo, p: dict) -> None:
    _require(p, "id", "name")
    rel_obj = repo.model.relationships.get(p["id"])
    if rel_obj is None:
        raise CommandError(f"no relationship {p['id']}")
    new_name = _slug(p["name"])
    if any(r.name == new_name and r.id != p["id"] for r in repo.model.relationships.values()):
        raise CommandError(f"relationship {new_name!r} already exists")
    rel, node = _node_for(repo, rel_obj.id)
    node["name"] = new_name
    repo.rename_file(rel, f"logical/relationships/{new_name}.yaml")


_CARDINALITIES = {"one_to_one", "one_to_many", "many_to_one", "many_to_many"}


def _update_relationship(repo: ModelRepo, p: dict) -> None:
    _require(p, "id")
    rel_obj = repo.model.relationships.get(p["id"])
    if rel_obj is None:
        raise CommandError(f"no relationship {p['id']}")
    _, node = _node_for(repo, rel_obj.id)
    if p.get("cardinality"):
        if p["cardinality"] not in _CARDINALITIES:
            raise CommandError(f"bad cardinality {p['cardinality']!r}")
        node["cardinality"] = p["cardinality"]
    if p.get("optionality"):
        if p["optionality"] not in {"mandatory", "optional"}:
            raise CommandError(f"bad optionality {p['optionality']!r}")
        node["optionality"] = p["optionality"]


# --- ontology alignment -----------------------------------------------------------


def _set_alignment(repo: ModelRepo, p: dict) -> None:
    _require(p, "id", "aligns_to")
    _, node = _conceptual_node(repo, p["id"])
    ont = node.setdefault("ontology", {})
    ont["aligns_to"] = p["aligns_to"]
    ont["alignment"] = p.get("alignment", "skos:closeMatch")
    if p.get("layer"):
        ont["layer"] = p["layer"]
    elif "layer" not in ont:
        ont["layer"] = "core"
    if "status" in p:
        ont["status"] = p["status"]
    ont.pop("no_industry_equivalent", None)


def _clear_alignment(repo: ModelRepo, p: dict) -> None:
    _require(p, "id")
    _, node = _conceptual_node(repo, p["id"])
    ont = node.get("ontology")
    if ont is not None:
        ont.pop("aligns_to", None)
        ont.pop("alignment", None)
        if not ont:
            node.pop("ontology", None)


def _set_unmanaged(repo: ModelRepo, p: dict) -> None:
    """Mark/unmark an entity's SQL as engineer-owned (spec `mdl unmanage`): the
    emitter stops emitting the model file entirely; the entity stays in the
    model for lineage/governance/canvas."""
    _require(p, "id")
    le = repo.model.logical_entities.get(p["id"])
    if le is None:
        raise CommandError(f"no logical entity {p['id']}")
    _, node = _node_for(repo, le.id)
    if p.get("unmanaged", True):
        node["unmanaged"] = True
    else:
        node.pop("unmanaged", None)


def _promote_alignment(repo: ModelRepo, p: dict) -> None:
    """Promote a proposed ontology alignment to accepted (collab model §5.1)."""
    _require(p, "id")
    _, node = _conceptual_node(repo, p["id"])
    ont = node.get("ontology")
    if not ont or not ont.get("aligns_to"):
        raise CommandError("object has no ontology alignment to promote")
    ont["status"] = "accepted"


_HANDLERS = {
    "create_entity": _create_entity,
    "rename_entity": _rename_entity,
    "delete_entity": _delete_entity,
    "set_definition": _set_definition,
    "update_synonyms": _update_synonyms,
    "set_stewardship": _set_stewardship,
    "set_subject_area": _set_subject_area,
    "set_pattern": _set_pattern,
    "set_unmanaged": _set_unmanaged,
    "promote_alignment": _promote_alignment,
    "create_subject_area": _create_subject_area,
    "create_term": _create_term,
    "add_attribute": _add_attribute,
    "update_attribute": _update_attribute,
    "delete_attribute": _delete_attribute,
    "create_relationship": _create_relationship,
    "delete_relationship": _delete_relationship,
    "rename_relationship": _rename_relationship,
    "update_relationship": _update_relationship,
    "set_alignment": _set_alignment,
    "clear_alignment": _clear_alignment,
}

COMMANDS = sorted(_HANDLERS)


def has_errors(result: CommandResult) -> bool:
    return any(d["severity"] == Severity.error.value for d in result.diagnostics)
