"""Render a Model to a self-contained static documentation site.

The data all comes from `mdl_server.projection.project()` (which already pre-joins every
doc-relevant field) and `mdl_emit_erd.emit_mermaid` (the ERD). This module only lays that
out into deterministic HTML via jinja2 templates plus one vendored CSS/JS asset bundle, so
the output opens offline with no server.
"""

from __future__ import annotations

import shutil
from collections import defaultdict, deque
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from mdl_emit_erd.mermaid import emit_mermaid

from mdl_core.ir import Model

_HERE = Path(__file__).parent
_TEMPLATES = _HERE / "templates"
_ASSETS = _HERE / "assets"


def _adjacency(model: Model) -> dict[str, set[str]]:
    """Undirected entity adjacency from relationships: each edge connects its two ends,
    so a neighbourhood walk reaches parents and children alike."""
    adj: dict[str, set[str]] = defaultdict(set)
    for rel in model.relationships.values():
        a, b = rel.from_.entity, rel.to.entity
        if a in model.logical_entities and b in model.logical_entities:
            adj[a].add(b)
            adj[b].add(a)
    return adj


def neighbourhood(model: Model, entity_id: str, radius: int) -> set[str]:
    """The entity plus every logical entity reachable within `radius` hops over the
    relationship graph (BFS). Radius 0 is the entity alone; negative is treated as 0."""
    if entity_id not in model.logical_entities:
        return set()
    if radius <= 0:
        return {entity_id}
    adj = _adjacency(model)
    seen = {entity_id}
    frontier = deque([(entity_id, 0)])
    while frontier:
        node, dist = frontier.popleft()
        if dist >= radius:
            continue
        for nxt in adj.get(node, ()):
            if nxt not in seen:
                seen.add(nxt)
                frontier.append((nxt, dist + 1))
    return seen


def _slug(name: str) -> str:
    """A filesystem/link-safe slug for an entity or subject-area page filename."""
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name).strip("-").lower() or "x"


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        autoescape=select_autoescape(["html"]),  # HTML output: escape by default
        trim_blocks=True,
        lstrip_blocks=True,
    )
    # Templates slug names identically to the files we write, so links never 404.
    env.filters["slug"] = _slug
    return env


def _sidebar(proj: dict) -> list[dict]:
    """The left tree-view model: subject-area groups (sorted), each with its entities;
    an 'Ungrouped' bucket collects entities with no subject area. Shared by every page."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for ent in proj["entities"]:
        sa = (ent.get("conceptual") or {}).get("subject_area")
        key = sa["name"] if sa else "Ungrouped"
        groups[key].append({"name": ent["name"], "slug": _slug(ent["name"])})
    # deterministic: subject areas alphabetical, Ungrouped last; entities alphabetical
    ordered_keys = sorted(k for k in groups if k != "Ungrouped")
    if "Ungrouped" in groups:
        ordered_keys.append("Ungrouped")
    return [
        {"name": key, "entities": sorted(groups[key], key=lambda e: e["name"])}
        for key in ordered_keys
    ]


def _glossary(model: Model) -> dict:
    """Glossary data straight from the model: business terms, domains (with enum
    values), and shared code sets. Kept out of the canvas projection (which omits
    them) so docs can render a real glossary without changing that contract."""
    terms = [
        {
            "name": t.name,
            "definition": t.definition,
            "synonyms": list(t.synonyms),
        }
        for t in sorted(model.terms.values(), key=lambda x: x.name)
    ]
    domains = [
        {
            "name": d.name,
            "base_type": d.base_type,
            "definition": d.definition,
            # inline enumeration, or a reference to a shared code set by name.
            # NB: not "values" — Jinja's `d.values` would resolve dict.values().
            "enum": [str(v) for v in (d.allowed_values or [])],
            "value_set": d.value_set,
        }
        for d in sorted(model.domains.values(), key=lambda x: x.name)
    ]
    code_sets = [
        {
            "name": c.name,
            "definition": c.definition,
            "members": [
                (f"{cv.code} — {cv.label}" if cv.label else str(cv.code)) for cv in c.values
            ],
        }
        for c in sorted(model.code_sets.values(), key=lambda x: x.name)
    ]
    return {"terms": terms, "domains": domains, "code_sets": code_sets}


def _entity_relationships(proj: dict, entity_id: str) -> tuple[list[dict], list[dict]]:
    """(outgoing, incoming) relationships for an entity, name-resolved for display."""
    name_by_id = {e["id"]: e["name"] for e in proj["entities"]}
    attr_name = {
        a["id"]: a["name"] for e in proj["entities"] for a in e["attributes"]
    }

    def cols(ids: list[str]) -> str:
        return ", ".join(attr_name.get(i, "?") for i in ids)

    outgoing, incoming = [], []
    for rel in proj["relationships"]:
        frm, to = rel["from"]["entity"], rel["to"]["entity"]
        row = {
            "name": rel["name"],
            "cardinality": rel["cardinality"],
            "from_entity": name_by_id.get(frm, "?"),
            "to_entity": name_by_id.get(to, "?"),
            "from_slug": _slug(name_by_id.get(frm, "")),
            "to_slug": _slug(name_by_id.get(to, "")),
            "from_cols": cols(rel["from"]["attributes"]),
            "to_cols": cols(rel["to"]["attributes"]),
        }
        if frm == entity_id:
            outgoing.append(row)
        if to == entity_id:
            incoming.append(row)
    return outgoing, incoming


def render_site(
    model: Model,
    out: Path,
    *,
    base_url: str = "",
    neighbourhood_radius: int = 2,
    status_summary: dict | None = None,
) -> list[Path]:
    """Render the full docs site into `out` and return the list of files written.

    `base_url` prefixes asset/link hrefs for publishing under a sub-path (e.g. Pages).
    `neighbourhood_radius` scopes each entity page's ERD to that many hops (default 2).
    `status_summary` is an optional dict (from `mdl_core.status.assess(...).to_dict()`)
    rendered as a light "Next actions" block on the overview; docs stay reference-first.
    """
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "entities").mkdir(exist_ok=True)
    (out / "subject-areas").mkdir(exist_ok=True)

    # one projection call feeds every page
    from mdl_server.projection import project, where_used

    proj = project(model)
    sidebar = _sidebar(proj)
    env = _env()
    prefix = base_url.rstrip("/")

    written: list[Path] = []

    def write(rel: str, template: str, depth: int, **ctx) -> None:
        # link root: pages under entities/ or subject-areas/ need "../" to reach the root.
        # With a base_url the site is served from a fixed prefix, so links resolve from
        # there; otherwise relative "../" keeps the bundle openable straight from disk.
        root = prefix + "/" if prefix else "../" * depth
        html = env.get_template(template).render(
            base_url=prefix,
            root=root,
            sidebar=sidebar,
            project=proj["project"],
            counts=proj["counts"],
            **ctx,
        )
        path = out / rel
        path.write_text(html, encoding="utf-8")
        written.append(path)

    # overview
    write(
        "index.html",
        "index.html",
        depth=0,
        active=None,
        erd=emit_mermaid(model),
        subject_areas=proj["subject_areas"],
        entities=proj["entities"],
        status=status_summary,
    )

    # glossary: terms + domains/code sets (built from the model, not the projection)
    write(
        "glossary.html",
        "glossary.html",
        depth=0,
        active="__glossary__",
        glossary=_glossary(model),
    )

    # per-subject-area pages (scoped ERD)
    for sa in proj["subject_areas"]:
        scoped = project(model, subject_area=sa["id"])
        write(
            f"subject-areas/{_slug(sa['name'])}.html",
            "subject_area.html",
            depth=1,
            active=None,
            subject_area=sa,
            erd=emit_mermaid(model, include={e["id"] for e in scoped["entities"]}),
            entities=scoped["entities"],
        )

    # per-entity detail pages
    for ent in proj["entities"]:
        outgoing, incoming = _entity_relationships(proj, ent["id"])
        nbrs = neighbourhood(model, ent["id"], neighbourhood_radius)
        ce = ent.get("conceptual") or {}
        used = where_used(model, ce["id"]) if ce.get("id") else []
        write(
            f"entities/{_slug(ent['name'])}.html",
            "entity.html",
            depth=1,
            active=_slug(ent["name"]),
            entity=ent,
            outgoing=outgoing,
            incoming=incoming,
            erd=emit_mermaid(model, include=nbrs),
            radius=neighbourhood_radius,
            neighbour_count=len(nbrs) - 1,
            where_used=used,
        )

    # vendored assets (css + mermaid) copied verbatim, offline-safe
    dest_assets = out / "assets"
    dest_assets.mkdir(exist_ok=True)
    for f in sorted(_ASSETS.iterdir()):
        if f.is_file():
            shutil.copy2(f, dest_assets / f.name)
            written.append(dest_assets / f.name)

    return written
