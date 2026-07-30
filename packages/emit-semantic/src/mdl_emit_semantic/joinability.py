"""Joinability + fan-out validation (spec §8).

Before emitting a semantic layer: every declared relationship must be traversable,
and fan-out risk (many-to-many paths, or an unaggregated measure across a
one-to-many join) is flagged as an error. Catching a fan-out at model time is
worth more than any diagram (§8).

The entity graph already holds the join graph; this is a projection over it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mdl_core.diagnostics import Diagnostic, DiagnosticSet, Severity
from mdl_core.ir import Model


@dataclass
class JoinGraph:
    # entity_ulid -> list of (relationship_name, other_entity_ulid, cardinality)
    edges: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)

    def neighbours(self, entity: str) -> list[tuple[str, str, str]]:
        return self.edges.get(entity, [])


def build_join_graph(model: Model) -> JoinGraph:
    jg = JoinGraph()
    for rel in model.relationships.values():
        a, b = rel.from_.entity, rel.to.entity
        jg.edges.setdefault(a, []).append((rel.name, b, rel.cardinality))
        jg.edges.setdefault(b, []).append((rel.name, a, _invert(rel.cardinality)))
    return jg


def _invert(card: str) -> str:
    return {
        "one_to_many": "many_to_one",
        "many_to_one": "one_to_many",
        "one_to_one": "one_to_one",
        "many_to_many": "many_to_many",
    }[card]


def validate_joinability(model: Model) -> DiagnosticSet:
    diags = DiagnosticSet()
    ids = {le.id for le in model.logical_entities.values()}

    # 1) Every relationship endpoint must resolve to a logical entity.
    for rel in model.relationships.values():
        for end, side in ((rel.from_, "from"), (rel.to, "to")):
            if end.entity not in ids:
                diags.add(
                    Diagnostic(
                        code="MDL-E801",
                        severity=Severity.error,
                        message=(
                            f"relationship {rel.name!r} {side} endpoint is not a logical entity"
                        ),
                        path=rel.id,
                    )
                )

    # 2) Fan-out: many_to_many relationships need an explicit bridge (pattern) or
    #    they produce unaggregated fan-out. Flag as error (§8).
    for rel in model.relationships.values():
        if rel.cardinality == "many_to_many":
            diags.add(
                Diagnostic(
                    code="MDL-E802",
                    severity=Severity.error,
                    message=(
                        f"relationship {rel.name!r} is many_to_many; introduce a bridge "
                        f"entity or the semantic join fans out"
                    ),
                    path=rel.id,
                )
            )

    # 3) Measures crossing a one_to_many join without aggregation fan out. We flag
    #    any measure attribute on the *one* side reachable via a one_to_many edge.
    jg = build_join_graph(model)
    for le in model.logical_entities.values():
        measures = [a for a in le.attributes if a.role == "measure"]
        if not measures:
            continue
        for _rel_name, other, card in jg.neighbours(le.id):
            if card == "one_to_many":
                other_le = model.logical_entities.get(other)
                diags.add(
                    Diagnostic(
                        code="MDL-W803",
                        severity=Severity.warning,
                        message=(
                            f"measure(s) on {le.name!r} join one_to_many to "
                            f"{other_le.name if other_le else other!r}; ensure aggregation "
                            f"to avoid fan-out"
                        ),
                        path=le.id,
                    )
                )
                break
    return diags
