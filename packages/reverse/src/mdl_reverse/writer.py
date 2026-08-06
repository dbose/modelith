"""Write a reversed Model to the §2.2 directory shape.

Freshly-reversed output has no prior comments to preserve, so we serialise the
pydantic objects directly (by_alias for `from`/`to`, dropping None and derived
fields) through the comment-preserving dumper for consistent formatting. A
subsequent `mdl validate` / `mdl generate` treats it like any authored repo.
"""

from __future__ import annotations

from pathlib import Path

from mdl_core.ir import Model
from mdl_core.yaml_io import dump_str


def write_model(model: Model, root: Path) -> list[str]:
    root = Path(root)
    written: list[str] = []

    def dump(rel: str, obj) -> None:
        data = obj.model_dump(by_alias=True, exclude_none=True, mode="json")
        # `kind` is an enum -> its value; pydantic mode="json" already handles it.
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump_str(data), encoding="utf-8")
        written.append(rel)

    # project config
    cfg = model.config.model_dump(exclude_none=True, mode="json")
    (root / "mdl-project.yaml").parent.mkdir(parents=True, exist_ok=True)
    (root / "mdl-project.yaml").write_text(dump_str(cfg), encoding="utf-8")
    written.append("mdl-project.yaml")

    for sa in model.subject_areas.values():
        dump(f"conceptual/subject-areas/{sa.name.lower().replace(' ', '_')}.yaml", sa)
    for ce in model.conceptual_entities.values():
        dump(f"conceptual/entities/{_slug(ce.name)}.yaml", ce)
    for term in model.terms.values():
        dump(f"conceptual/terms/{_slug(term.name)}.yaml", term)
    for dom in model.domains.values():
        dump(f"logical/domains/{dom.name}.yaml", dom)
    for cs in model.code_sets.values():
        dump(f"logical/value-sets/{_slug(cs.name)}.yaml", cs)
    for le in model.logical_entities.values():
        dump(f"logical/entities/{le.name}.yaml", le)
    for rel in model.relationships.values():
        dump(f"logical/relationships/{rel.name}.yaml", rel)
    for kg in model.key_groups.values():
        dump(f"logical/key-groups/{_slug(kg.name)}.yaml", kg)
    for pt in model.physical_tables.values():
        dump(f"physical/{pt.target}/tables/{pt.name.lower()}.yaml", pt)

    return written


def _slug(name: str) -> str:
    return name.lower().replace(" ", "_")
