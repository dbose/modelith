"""Write a reversed Model to the §2.2 directory shape.

Freshly-reversed output has no prior comments to preserve, so we serialise the
pydantic objects directly (by_alias for `from`/`to`, dropping None and derived
fields) through the comment-preserving dumper for consistent formatting. A
subsequent `mdl validate` / `mdl generate` treats it like any authored repo.
"""

from __future__ import annotations

from pathlib import Path

from mdl_core.ir import Model
from mdl_core.yaml_io import dump_str, load_file

# Config keys the USER owns — a re-reverse must never clobber them. Reverse only owns
# the identity/target of the project; everything below is hand-authored policy that a
# re-run into an existing dir must preserve (the reported data-loss bug: an authored
# `reverse.exclude` was wiped on re-reverse).
_USER_OWNED_CONFIG = (
    "reverse",
    "naming",
    "glossary",
    "ontology_stack",
    "platform_targets",
    "kg_base_iri",
)


def _write_project_config(model: Model, root: Path) -> None:
    """Write mdl-project.yaml, PRESERVING an existing one's user-authored config.

    A first reverse into an empty dir writes the fresh config as-is. Re-reversing into a
    dir that already has an mdl-project.yaml loads it (comment-preserving) and updates
    ONLY the fields reverse owns (name, dbt_target), keeping the user's reverse/naming/
    glossary/ontology_stack blocks and any hand edits intact."""
    fresh = model.config.model_dump(exclude_none=True, mode="json")
    dest = root / "mdl-project.yaml"
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists():
        try:
            existing = load_file(dest)  # ruamel round-trip node (keeps comments)
        except Exception:  # noqa: BLE001 - an unreadable prior config: fall back to fresh
            existing = None
        if existing is not None and hasattr(existing, "get"):
            # Reverse-owned identity fields refresh; user-owned policy is preserved. A
            # user-owned key absent from `existing` but present in `fresh` (e.g. reverse
            # carried a config it was classified with) is filled in, not dropped.
            for key in ("name", "dbt_target"):
                if key in fresh:
                    existing[key] = fresh[key]
            for key in _USER_OWNED_CONFIG:
                if key not in existing and key in fresh:
                    existing[key] = fresh[key]
            dest.write_text(dump_str(existing), encoding="utf-8")
            return

    dest.write_text(dump_str(fresh), encoding="utf-8")


def write_model(model: Model, root: Path) -> list[str]:
    root = Path(root)
    written: list[str] = []

    # Collection fields that default to []: `exclude_none` does not drop an empty
    # list, so a reversed model would carry a noise `members: []` on every object.
    _EMPTY_OK = ("members", "synonyms", "subtypes", "ontology_refs", "values")

    def dump(rel: str, obj) -> None:
        data = obj.model_dump(by_alias=True, exclude_none=True, mode="json")
        for key in _EMPTY_OK:
            if data.get(key) == []:
                data.pop(key)
        # `kind` is an enum -> its value; pydantic mode="json" already handles it.
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump_str(data), encoding="utf-8")
        written.append(rel)

    # project config — preserves a user's existing mdl-project.yaml on a re-reverse
    # (the authored reverse:/naming:/glossary: blocks survive; see _write_project_config).
    _write_project_config(model, root)
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
    for cat in model.categories.values():
        dump(f"logical/categories/{_slug(cat.name)}.yaml", cat)
    for pt in model.physical_tables.values():
        dump(f"physical/{pt.target}/tables/{pt.name.lower()}.yaml", pt)

    # Prune stale object files from a PRIOR reverse into this dir: an entity that no
    # longer exists (e.g. now excluded by an edited reverse.exclude) would otherwise
    # linger. Only the object subdirs reverse OWNS are swept, and only `.yaml` files —
    # never mdl-project.yaml, .mdl/, or anything the user added elsewhere.
    _prune_stale(root, set(written))

    return written


# The directory trees write_model manages — swept for stale files on a re-reverse.
# mdl-project.yaml (root) and .mdl/ are deliberately excluded (user/state, not objects).
_OWNED_DIRS = ("conceptual", "logical", "physical")


def _prune_stale(root: Path, written: set[str]) -> None:
    """Delete `.yaml` files under the reverse-owned object dirs that this write did not
    produce, so an in-place re-reverse doesn't leave orphaned entities behind. Scoped to
    _OWNED_DIRS; empties leftover directories. Never touches mdl-project.yaml or .mdl/."""
    for owned in _OWNED_DIRS:
        base = root / owned
        if not base.is_dir():
            continue
        for path in base.rglob("*.yaml"):
            rel = str(path.relative_to(root))
            if rel not in written:
                try:
                    path.unlink()
                except OSError:
                    pass
        # tidy now-empty subdirectories (deepest first), leaving the tree clean
        for d in sorted(base.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if d.is_dir() and not any(d.iterdir()):
                try:
                    d.rmdir()
                except OSError:
                    pass


def _slug(name: str) -> str:
    return name.lower().replace(" ", "_")
