"""Semantic git merge driver for model YAML (collaboration model §6.1).

Line-based merge on model YAML produces nonsense when two people edit the same
entity. This driver resolves ULID-keyed collections structurally:

- two people adding *different* attributes to the same entity → clean union
- two people editing the *same* field of the same attribute → a real conflict

Invocation (configured by `mdl init --git-hooks`):

    [merge "mdl"]
        driver = mdl merge-driver %O %A %B

Git passes base/ours/theirs; the result is written over OURS. Exit 0 = clean,
1 = conflict. On conflict the merged-so-far document is written with a YAML
comment block describing exactly what needs a human — the file stays parseable.

Comments survive: OURS is mutated in place (ruamel round-trip nodes), so a
reviewer's hand comments are never lost by the merge itself.
"""

from __future__ import annotations

from pathlib import Path

from mdl_core.yaml_io import dump_str, load_str

_SCALAR_MERGE_KEYS_EXCLUDED = {"attributes"}  # merged structurally, not field-wise


def merge_model_files(base_text: str, ours_text: str, theirs_text: str) -> tuple[str, list[str]]:
    """Structural three-way merge. Returns (merged_text, conflicts)."""
    base = load_str(base_text) or {}
    ours = load_str(ours_text) or {}
    theirs = load_str(theirs_text) or {}

    if not isinstance(ours, dict) or not isinstance(theirs, dict):
        # not a mapping document — degrade to take-ours + conflict
        return ours_text, ["document is not a YAML mapping; resolve manually"]

    if "decisions" in ours or "decisions" in theirs:
        return _merge_decisions(base, ours, theirs)

    conflicts: list[str] = []
    _merge_fields(base, ours, theirs, conflicts, exclude=_SCALAR_MERGE_KEYS_EXCLUDED)

    if "attributes" in ours or "attributes" in theirs or "attributes" in base:
        _merge_ulid_list(
            base.get("attributes") or [],
            ours.setdefault("attributes", []),
            theirs.get("attributes") or [],
            conflicts,
        )
        if not ours.get("attributes") and "attributes" not in theirs and "attributes" not in base:
            ours.pop("attributes", None)

    merged = dump_str(ours)
    if conflicts:
        merged += _conflict_block(conflicts)
    return merged, conflicts


def _merge_fields(
    base, ours, theirs, conflicts: list[str], *, exclude: set[str], ctx: str = ""
) -> None:
    for key in list({*base.keys(), *ours.keys(), *theirs.keys()}):
        if key in exclude:
            continue
        bv, ov, tv = base.get(key), ours.get(key), theirs.get(key)
        label = f"{ctx}{key}"
        if _eq(ov, tv):
            continue  # agreement (includes both deleted)
        if _eq(bv, ov):
            # we didn't change it -> take theirs (set or delete)
            if key in theirs:
                ours[key] = tv
            else:
                ours.pop(key, None)
        elif _eq(bv, tv):
            continue  # theirs unchanged -> keep ours
        else:
            conflicts.append(f"field {label!r}: ours={ov!r} theirs={tv!r} (base={bv!r})")


def _merge_ulid_list(base_list, ours_list, theirs_list, conflicts: list[str]) -> None:
    """Merge lists of dicts keyed by `id` (attributes). Union of additions;
    field-wise merge of shared entries; delete-vs-modify is a conflict."""
    b = {a.get("id"): a for a in base_list if isinstance(a, dict)}
    o = {a.get("id"): a for a in ours_list if isinstance(a, dict)}
    t = {a.get("id"): a for a in theirs_list if isinstance(a, dict)}

    for aid, t_attr in t.items():
        name = t_attr.get("name", aid)
        if aid in o:
            _merge_fields(
                b.get(aid, {}), o[aid], t_attr, conflicts, exclude=set(), ctx=f"attribute {name!r}."
            )
        elif aid in b:
            # we deleted it
            if _eq(b[aid], t_attr):
                continue  # theirs unchanged -> stays deleted
            conflicts.append(f"attribute {name!r}: deleted by ours, modified by theirs")
        else:
            ours_list.append(t_attr)  # new in theirs -> union

    for aid in list(o.keys()):
        if aid not in t and aid in b:
            if _eq(b[aid], o[aid]):
                # theirs deleted it, we didn't touch it -> delete
                ours_list[:] = [a for a in ours_list if a.get("id") != aid]
            else:
                name = o[aid].get("name", aid)
                conflicts.append(f"attribute {name!r}: modified by ours, deleted by theirs")


def _merge_decisions(base, ours, theirs) -> tuple[str, list[str]]:
    """Decision ledger: union by (kind, signal, subject); a human verdict beats
    'proposed'; two different human verdicts are a conflict."""
    conflicts: list[str] = []

    def key(d: dict) -> tuple:
        return (d.get("kind"), d.get("signal"), d.get("subject"))

    b = {key(d): d for d in (base.get("decisions") or [])}
    o = {key(d): d for d in (ours.get("decisions") or [])}
    t = {key(d): d for d in (theirs.get("decisions") or [])}

    merged: dict[tuple, dict] = dict(o)
    for k, td in t.items():
        if k not in merged:
            merged[k] = td
            continue
        od = merged[k]
        ov, tv = od.get("verdict", "proposed"), td.get("verdict", "proposed")
        if ov == tv:
            continue
        if ov == "proposed":
            merged[k] = td  # theirs carries the human verdict
        elif tv == "proposed":
            continue  # ours carries the human verdict
        else:
            _ = b  # base not needed: two humans disagreed
            conflicts.append(f"decision {k[2]!r}: ours verdict={ov!r} theirs={tv!r}")

    ordered = sorted(merged.values(), key=lambda d: (d.get("kind", ""), d.get("subject", "")))
    out = {"decisions": ordered}
    text = dump_str(out)
    if conflicts:
        text += _conflict_block(conflicts)
    return text, conflicts


def _conflict_block(conflicts: list[str]) -> str:
    lines = ["# <<<<<<< MDL MERGE CONFLICTS — resolve, then delete this block"]
    lines += [f"#   {c}" for c in conflicts]
    lines.append("# >>>>>>>")
    return "\n".join(lines) + "\n"


def _eq(a, b) -> bool:
    return _plain(a) == _plain(b)


def _plain(v):
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


def run_merge_driver(base: Path, ours: Path, theirs: Path, *, state: bool = False) -> int:
    """CLI entry: git merge driver protocol (result -> OURS; 0 clean, 1 conflict)."""
    if state:
        # Generation-state shards are derived data: the next `mdl generate`
        # recomputes them. Taking ours is always safe and never blocks a merge.
        return 0
    merged, conflicts = merge_model_files(
        base.read_text(encoding="utf-8") if base.exists() else "",
        ours.read_text(encoding="utf-8"),
        theirs.read_text(encoding="utf-8"),
    )
    ours.write_text(merged, encoding="utf-8")
    return 1 if conflicts else 0
