"""Mapping DSL — governance-profile.yaml (spec §9.2).

The customer owns a governance-profile.yaml in their repo (not ours). It maps
Modelith asset kinds to a target catalog's operating model via Jinja templates
over the GovernanceAsset. No Python required to customise a tenant.

This module parses a profile and renders a GovernanceGraph through it into a
`MappedGraph` of target-shaped assets, which an adapter turns into API calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Environment, StrictUndefined, UndefinedError

from mdl_core.yaml_io import load_str
from mdl_governance.graph import GovernanceAsset, GovernanceGraph


@dataclass
class DomainDef:
    name: str
    type: str


@dataclass
class AssetTypeMapping:
    target: str  # target asset type, e.g. "Business Asset"
    domain: str | None  # domain key
    attributes: dict[str, str] = field(default_factory=dict)  # target attr -> jinja


@dataclass
class RelationMapping:
    type: str
    direction: str = "source_to_target"


@dataclass
class WritebackMapping:
    external_attribute: str  # catalog attribute name
    model_path: str  # dotted path in the model, e.g. governance.classification


@dataclass
class Profile:
    profile: str  # e.g. "collibra"
    version: int
    community: str | None
    domains: dict[str, DomainDef]
    asset_types: dict[str, AssetTypeMapping]
    relations: dict[str, RelationMapping]
    responsibilities: dict[str, dict]
    writeback: list[WritebackMapping] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> Profile:
        return cls.from_dict(load_str(Path(path).read_text(encoding="utf-8")) or {})

    @classmethod
    def from_dict(cls, data: dict) -> Profile:
        domains = {
            k: DomainDef(name=v.get("name", k), type=v.get("type", ""))
            for k, v in (data.get("domains") or {}).items()
        }
        asset_types = {
            k: AssetTypeMapping(
                target=v.get("target", k),
                domain=v.get("domain"),
                attributes=dict(v.get("attributes") or {}),
            )
            for k, v in (data.get("asset_types") or {}).items()
        }
        relations = {
            k: RelationMapping(
                type=v.get("type", k),
                direction=v.get("direction", "source_to_target"),
            )
            for k, v in (data.get("relations") or {}).items()
        }
        writeback = [
            WritebackMapping(
                external_attribute=w.get("collibra_attribute") or w.get("external_attribute", ""),
                model_path=w.get("model_path", ""),
            )
            for w in (data.get("writeback") or [])
        ]
        return cls(
            profile=str(data.get("profile", "custom")),
            version=int(data.get("version", 1)),
            community=data.get("community"),
            domains=domains,
            asset_types=asset_types,
            relations=relations,
            responsibilities=dict(data.get("responsibilities") or {}),
            writeback=writeback,
        )


@dataclass
class MappedAsset:
    external_id: str
    target_type: str
    domain: DomainDef | None
    name: str
    attributes: dict[str, str]  # rendered target attribute -> value
    relations: list[tuple[str, str]]  # (target relation type, target external_id)


@dataclass
class MappedGraph:
    assets: list[MappedAsset] = field(default_factory=list)
    unmapped_kinds: set[str] = field(default_factory=set)
    render_errors: list[str] = field(default_factory=list)


def _env() -> Environment:
    # StrictUndefined so a template referencing a missing attribute is an error the
    # conformance kit can catch, not a silent empty string.
    return Environment(undefined=StrictUndefined, autoescape=False)


def map_graph(graph: GovernanceGraph, profile: Profile) -> MappedGraph:
    env = _env()
    out = MappedGraph()

    for asset in graph.assets:
        mapping = profile.asset_types.get(asset.modelith_kind)
        if mapping is None:
            out.unmapped_kinds.add(asset.modelith_kind)
            continue
        rendered: dict[str, str] = {}
        ctx = _context(asset)
        for target_attr, template in mapping.attributes.items():
            try:
                rendered[target_attr] = env.from_string(template).render(**ctx)
            except UndefinedError as e:
                out.render_errors.append(
                    f"{asset.modelith_kind} {asset.name!r}: attribute {target_attr!r}: {e}"
                )
        rel_out = []
        for rel in asset.relations:
            rm = profile.relations.get(rel.kind)
            if rm is not None:
                rel_out.append((rm.type, rel.target_external_id))
        out.assets.append(
            MappedAsset(
                external_id=asset.external_id,
                target_type=mapping.target,
                domain=profile.domains.get(mapping.domain) if mapping.domain else None,
                name=asset.name,
                attributes=rendered,
                relations=rel_out,
            )
        )
    return out


def _context(asset: GovernanceAsset) -> dict:
    """Jinja context: the asset's attributes at top level plus `name`/`external_id`,
    and an `ontology` object so templates can write `{{ ontology.aligns_to }}`."""
    ctx = dict(asset.attributes)
    ctx["name"] = asset.name
    ctx["external_id"] = asset.external_id
    ctx["definition"] = asset.attributes.get("definition", "")
    # convenience nested accessor mirroring the IR shape used in spec examples
    ctx["ontology"] = {
        "aligns_to": asset.attributes.get("ontology_iri", ""),
        "layer": asset.attributes.get("ontology_layer", ""),
    }
    return ctx
