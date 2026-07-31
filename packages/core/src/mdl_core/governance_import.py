"""Turn a catalog writeback set into mutation commands (catalog -> git import).

When the business glossary is mastered in a catalog (Collibra etc.), edits made
there flow back into git through ``mdl gov import``. This module is the pure,
testable core of that path: it maps each ``WritebackValue`` onto one of the
narrow glossary mutation ops, so the import reuses the SAME comment-preserving
engine the SME app uses — never a bespoke YAML writer, never a silent overwrite.

The external id is the deterministic ``mdl:<ULID>`` minted by the governance
graph, so it round-trips straight back to the model object. Only the meaning
fields are importable (definition / synonyms / stewardship); structural fields
are never catalog-owned and are ignored here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mdl_core.ir import Model

# writeback model_path -> how to build the mutation command.
# Values may arrive under a few spellings depending on how the profile names them.
_DEFINITION_PATHS = {"definition"}
_SYNONYM_PATHS = {"synonyms"}
_OWNER_PATHS = {"stewardship.owner", "owner"}
_STEWARD_PATHS = {"stewardship.steward", "steward"}


def _strip_external_id(external_id: str) -> str:
    """`mdl:<ULID>` -> `<ULID>` (the governance graph's deterministic scheme)."""
    return external_id[4:] if external_id.startswith("mdl:") else external_id


def _known(model: Model, ulid: str) -> bool:
    return ulid in model.conceptual_entities or ulid in model.terms


def writeback_to_commands(model: Model, wb) -> list[tuple[str, dict]]:
    """Resolve a WritebackSet into ``[(op, payload), ...]`` for ``apply_command``.

    Stewardship owner/steward for the same term are coalesced into a single
    ``set_stewardship`` command so both survive (the handler only writes keys it
    is given). Unmappable paths and unknown ids are dropped (an import must never
    invent objects).
    """
    stewardship: dict[str, dict] = {}
    commands: list[tuple[str, dict]] = []

    for v in wb.values:
        ulid = _strip_external_id(v.external_id)
        if not _known(model, ulid):
            continue
        path = v.model_path
        if path in _DEFINITION_PATHS:
            commands.append(("set_definition", {"id": ulid, "definition": v.value}))
        elif path in _SYNONYM_PATHS:
            syns = v.value if isinstance(v.value, list) else _split_synonyms(v.value)
            commands.append(("update_synonyms", {"id": ulid, "synonyms": syns}))
        elif path in _OWNER_PATHS:
            stewardship.setdefault(ulid, {})["owner"] = v.value
        elif path in _STEWARD_PATHS:
            stewardship.setdefault(ulid, {})["steward"] = v.value
        # any other model_path is not a catalog-owned glossary field: skip it

    for ulid, roles in stewardship.items():
        commands.append(("set_stewardship", {"id": ulid, **roles}))

    return commands


def _split_synonyms(value) -> list[str]:
    if value is None:
        return []
    return [s.strip() for s in str(value).split(",") if s.strip()]


__all__ = ["writeback_to_commands"]
