"""Neutral GovernanceGraph (spec §9.1).

Core emits a vendor-neutral graph; adapters consume it. The single most important
decision here is the deterministic `external_id` derived from the immutable ULID
("mdl:<ULID>"): it makes sync idempotent — re-running updates, never duplicates —
which removes the most common failure mode of catalog integrations (§9.1).

This module depends only on `modelith-core`. Adapters depend only on this
(layering §1.3): they never import core.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mdl_core.ir import Model


def external_id(ulid: str) -> str:
    """Deterministic, stable external id from the immutable ULID (§9.1)."""
    return f"mdl:{ulid}"


@dataclass
class GovernanceRelation:
    kind: str  # modelith relation kind: realises | aligns_to | has_attribute | ...
    target_external_id: str


@dataclass
class GovernanceAsset:
    external_id: str  # deterministic, "mdl:<ULID>"
    modelith_kind: str  # conceptual_entity | logical_entity | attribute | metric | term | ...
    name: str
    attributes: dict = field(default_factory=dict)  # definition, ontology_iri, layer, ...
    relations: list[GovernanceRelation] = field(default_factory=list)


@dataclass
class GovernanceGraph:
    assets: list[GovernanceAsset] = field(default_factory=list)

    def by_external_id(self) -> dict[str, GovernanceAsset]:
        return {a.external_id: a for a in self.assets}

    def duplicate_external_ids(self) -> list[str]:
        seen: set[str] = set()
        dups: list[str] = []
        for a in self.assets:
            if a.external_id in seen:
                dups.append(a.external_id)
            seen.add(a.external_id)
        return dups


def build_graph(model: Model) -> GovernanceGraph:
    """Project a Model into the neutral GovernanceGraph."""
    g = GovernanceGraph()

    for ce in model.conceptual_entities.values():
        attrs = {"definition": ce.definition}
        if ce.aligned_uri or ce.ontology_layer:
            attrs["ontology_iri"] = ce.aligned_uri
            attrs["ontology_layer"] = ce.ontology_layer
        if ce.stewardship:
            attrs["owner"] = ce.stewardship.owner
            attrs["steward"] = ce.stewardship.steward
        attrs["synonyms"] = list(ce.synonyms)
        g.assets.append(
            GovernanceAsset(
                external_id=external_id(ce.id),
                modelith_kind="conceptual_entity",
                name=ce.name,
                attributes=_clean(attrs),
            )
        )

    for term in model.terms.values():
        attrs = {"definition": term.definition}
        if term.aligned_uri or term.ontology_layer:
            attrs["ontology_iri"] = term.aligned_uri
            attrs["ontology_layer"] = term.ontology_layer
        g.assets.append(
            GovernanceAsset(
                external_id=external_id(term.id),
                modelith_kind="term",
                name=term.name,
                attributes=_clean(attrs),
            )
        )

    for le in model.logical_entities.values():
        relations = []
        if le.realises:
            relations.append(GovernanceRelation("realises", external_id(le.realises)))
        for attr in le.attributes:
            relations.append(GovernanceRelation("has_attribute", external_id(attr.id)))
        g.assets.append(
            GovernanceAsset(
                external_id=external_id(le.id),
                modelith_kind="logical_entity",
                name=le.name,
                attributes=_clean({"pattern": le.pattern}),
                relations=relations,
            )
        )
        for attr in le.attributes:
            a_attrs = {"role": attr.role, "nullable": attr.nullable, "domain": attr.domain}
            if attr.aligned_uri:
                a_attrs["ontology_iri"] = attr.aligned_uri
            g.assets.append(
                GovernanceAsset(
                    external_id=external_id(attr.id),
                    modelith_kind="attribute",
                    name=attr.name,
                    attributes=_clean(a_attrs),
                )
            )

    for pt in model.physical_tables.values():
        g.assets.append(
            GovernanceAsset(
                external_id=external_id(pt.id),
                modelith_kind="physical_table",
                name=pt.name,
                attributes=_clean({"target": pt.target, "materialization": pt.materialization}),
                relations=[GovernanceRelation("realises", external_id(pt.realises))]
                if pt.realises
                else [],
            )
        )

    return g


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}
