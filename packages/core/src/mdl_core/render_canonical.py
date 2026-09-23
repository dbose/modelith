"""Render a Model as canonical, name-keyed, ULID-free text — the human diff surface.

Two independently reverse-engineered models describe the same warehouse with DIFFERENT
ULIDs, so a raw YAML diff (or the ULID-keyed `diff_models`) reports every object as
removed+added even when they are semantically identical. This renders a model as a
deterministic, ULID-free document keyed on NAMES, ordered stably, so feeding two such
documents to a line differ (VS Code's `vscode.diff`, or `diff`/`git diff --no-index`)
shows the real semantic delta — an added column, a new foreign key — with no ULID noise.

The format is compact, one block per logical entity in name order:

    entity customer
      customer_id      bigint    not null  [pk]
      region_id        bigint              [fk -> region]
      legal_name       string    not null
      email            string
      --
      -> region  (many_to_one via region_id)

Keys come from a `pk` KeyGroup when present (authoritative) else `role: business_key`;
foreign keys from relationships whose many-side (`from`) is this entity. Everything is
name-resolved so the text carries no identifiers.
"""

from __future__ import annotations

from mdl_core.ir import Attribute, LogicalEntity, Model

# column widths for the aligned attribute lines (name / type), kept modest so long names
# wrap gracefully rather than pushing the flags off-screen in a narrow diff pane.
_NAME_W = 24
_TYPE_W = 12


def render_canonical(model: Model) -> str:
    """The canonical text for one model (see module docstring). Deterministic and
    ULID-free, so two renders diff on meaning, not identity."""
    ent_name = {e.id: e.name for e in model.logical_entities.values()}
    attr_name = {
        a.id: a.name for e in model.logical_entities.values() for a in e.attributes
    }

    # Primary key per entity. A `pk` KeyGroup is authoritative when present; otherwise the
    # legacy `role: business_key` convention (which is what `mdl reverse` writes) marks the
    # key columns. Union both so the render is correct either way. Alternate/unique key
    # groups annotate their members too.
    pk_members: dict[str, set[str]] = {}
    unique_members: dict[str, set[str]] = {}
    for e in model.logical_entities.values():
        bks = {a.name for a in e.attributes if a.role == "business_key"}
        if bks:
            pk_members.setdefault(e.id, set()).update(bks)
    for kg in model.key_groups.values():
        target = pk_members if kg.type == "pk" else unique_members
        names = {attr_name.get(m) for m in kg.members if attr_name.get(m)}
        target.setdefault(kg.entity, set()).update(n for n in names if n)

    # foreign keys: relationship whose many-side (`from`) is this entity -> the one-side
    # (`to`) entity, via the from-side attribute names. Keyed by (entity_id) -> list of
    # (fk_column_name, target_entity_name, cardinality) plus a per-column fk target map.
    fk_by_entity: dict[str, list[tuple[str, str, str]]] = {}
    fk_col_target: dict[str, dict[str, str]] = {}  # entity_id -> {col_name: target_name}
    for rel in model.relationships.values():
        src = rel.from_.entity
        tgt_name = ent_name.get(rel.to.entity)
        if tgt_name is None:
            continue
        cols = [attr_name.get(a) for a in rel.from_.attributes]
        cols = [c for c in cols if c]
        col_label = ", ".join(cols) if cols else "?"
        fk_by_entity.setdefault(src, []).append((col_label, tgt_name, rel.cardinality))
        for c in cols:
            fk_col_target.setdefault(src, {})[c] = tgt_name

    lines: list[str] = []
    project = getattr(model.config, "name", None) or "model"
    lines.append(f"# model: {project}")
    lines.append("")

    for entity in sorted(model.logical_entities.values(), key=lambda e: e.name):
        _render_entity(
            entity, lines, pk_members, unique_members, fk_col_target, fk_by_entity
        )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_entity(
    entity: LogicalEntity,
    lines: list[str],
    pk_members: dict[str, set[str]],
    unique_members: dict[str, set[str]],
    fk_col_target: dict[str, dict[str, str]],
    fk_by_entity: dict[str, list[tuple[str, str, str]]],
) -> None:
    header = f"entity {entity.name}"
    if entity.pattern:
        header += f"  [{entity.pattern}]"
    if entity.unmanaged:
        header += "  [unmanaged]"
    lines.append(header)

    pk = pk_members.get(entity.id, set())
    uniq = unique_members.get(entity.id, set())
    fk_targets = fk_col_target.get(entity.id, {})
    # attributes in declared order (order is meaningful and stable across a re-reverse)
    for attr in entity.attributes:
        lines.append(_attr_line(attr, pk, uniq, fk_targets))

    rels = sorted(fk_by_entity.get(entity.id, []))
    if rels:
        lines.append("  --")
        for col, tgt, card in rels:
            lines.append(f"  -> {tgt}  ({card} via {col})")


def _attr_line(
    attr: Attribute, pk: set[str], uniq: set[str], fk_targets: dict[str, str]
) -> str:
    name = attr.name.ljust(_NAME_W)
    typ = (attr.domain or "").ljust(_TYPE_W)
    null = "not null" if not attr.nullable else "        "
    flags: list[str] = []
    if attr.name in pk:
        flags.append("pk")
    if attr.name in fk_targets:
        flags.append(f"fk -> {fk_targets[attr.name]}")
    if attr.name in uniq and attr.name not in pk:
        flags.append("unique")
    flag_str = ("  [" + ", ".join(flags) + "]") if flags else ""
    return f"  {name} {typ} {null}{flag_str}".rstrip()
