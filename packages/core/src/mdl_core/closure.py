"""Neighbour expansion over the model graph — erwin's "add related objects".

Pure traversal over two `Model` tables; no git, no repo, no HTTP. It lives in core
because both the server (the picker endpoint) and the CLI need it, and putting it
in the server package would force the CLI to import the server — the layering
violation CI enforces.

Seeds and results are CONCEPTUAL ULIDs, because that is what a subject area holds.
The traversal itself runs over the LOGICAL graph, where relationships and
categories actually live, and projects back.

Direction follows the IR's own convention: `Relationship.from_` is the MANY side
and `to` is the ONE side, so the parent (the one side) is the ANCESTOR. From Trade,
the ancestor is Counterparty — which matches erwin, where "add ancestors" pulls in
the parents your foreign keys already point at.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from mdl_core.ir import Model

Direction = Literal["ancestors", "descendants", "both"]

MAX_LEVELS = 10


@dataclass(frozen=True)
class ClosureHop:
    """One expansion step. Returning hops rather than a bare set of ULIDs is
    deliberate: erwin shows you what it is about to add, and "Counterparty — via
    trade_has_counterparty" is the difference between a comprehensible picker and
    a black box."""

    id: str  # conceptual ULID reached
    name: str
    via: str  # "relationship" | "category" | "subtype"
    via_name: str
    from_id: str  # conceptual ULID we came from
    from_name: str
    direction: Direction
    level: int  # 1-based hops from the seed set

    def to_doc(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "via": self.via,
            "via_name": self.via_name,
            "from_id": self.from_id,
            "from_name": self.from_name,
            "direction": self.direction,
            "level": self.level,
        }


def _indexes(model: Model):
    """conceptual -> [logical], and logical -> conceptual. Same reverse-`realises`
    computation the server's where_used does, built once per call."""
    ce_to_les: dict[str, list[str]] = {}
    le_to_ce: dict[str, str] = {}
    for le in model.logical_entities.values():
        if not le.realises:
            continue
        ce_to_les.setdefault(le.realises, []).append(le.id)
        le_to_ce[le.id] = le.realises
    return ce_to_les, le_to_ce


def _edges(model: Model, direction: Direction):
    """logical ULID -> [(neighbour logical ULID, via, via_name)] for the requested
    direction. many_to_many participates both ways; otherwise the from/to roles
    already encode the cardinality."""
    want_up = direction in ("ancestors", "both")
    want_down = direction in ("descendants", "both")
    out: dict[str, list[tuple[str, str, str]]] = {}

    def add(a: str, b: str, via: str, name: str) -> None:
        out.setdefault(a, []).append((b, via, name))

    for rel in model.relationships.values():
        child, parent = rel.from_.entity, rel.to.entity  # many side, one side
        m2m = rel.cardinality == "many_to_many"
        if want_up or m2m:
            add(child, parent, "relationship", rel.name)
        if want_down or m2m:
            add(parent, child, "relationship", rel.name)

    for cat in model.categories.values():
        for sub in cat.subtypes:
            if want_up:
                add(sub, cat.supertype, "category", cat.name)
            if want_down:
                add(cat.supertype, sub, "category", cat.name)

    for le in model.logical_entities.values():
        for sub in le.subtypes:
            if want_up:
                add(sub, le.id, "subtype", le.name)
            if want_down:
                add(le.id, sub, "subtype", le.name)
    return out


def expand(
    model: Model,
    seeds: set[str] | list[str],
    *,
    direction: Direction = "both",
    levels: int = 1,
) -> list[ClosureHop]:
    """Breadth-first neighbour expansion. Seeds are conceptual ULIDs and are NOT
    returned. Deterministic order: level, then name."""
    if levels < 1:
        return []
    levels = min(levels, MAX_LEVELS)

    ce_to_les, le_to_ce = _indexes(model)
    edges = _edges(model, direction)
    name_of = {ce.id: ce.name for ce in model.conceptual_entities.values()}

    seen = {s for s in seeds if s in name_of}
    frontier = list(seen)
    hops: list[ClosureHop] = []

    for level in range(1, levels + 1):
        found: list[ClosureHop] = []
        for ce_id in frontier:
            for le_id in ce_to_les.get(ce_id, []):
                for nbr_le, via, via_name in edges.get(le_id, []):
                    nbr_ce = le_to_ce.get(nbr_le)
                    if nbr_ce is None or nbr_ce in seen:
                        continue
                    found.append(
                        ClosureHop(
                            id=nbr_ce,
                            name=name_of.get(nbr_ce, nbr_ce),
                            via=via,
                            via_name=via_name,
                            from_id=ce_id,
                            from_name=name_of.get(ce_id, ce_id),
                            direction=direction,
                            level=level,
                        )
                    )
        # dedupe within the level, keeping the first (shortest) route to each object
        best: dict[str, ClosureHop] = {}
        for h in sorted(found, key=lambda x: (x.name, x.via_name)):
            best.setdefault(h.id, h)
        if not best:
            break
        hops.extend(best.values())
        seen.update(best)
        frontier = list(best)

    return sorted(hops, key=lambda h: (h.level, h.name))
