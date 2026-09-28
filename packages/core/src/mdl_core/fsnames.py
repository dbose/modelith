"""Filesystem-safe filename helpers — the one place object names become file names.

A modelled object's name (entity, domain, attribute, physical table…) is turned into a
YAML/SQL filename across the reverse writer, the interactive command engine, and the dbt
emitter. Windows is far stricter than POSIX about what a filename may be, so a name that
is fine on a Mac can crash `open()` or silently collide on a Windows box:

- illegal characters: ``< > : " / \\ | ? *`` and control chars (OSError 22);
- a name ending in a dot or space;
- reserved device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9) — uncreatable *whatever*
  the extension;
- case-insensitive collisions: ``Foo`` and ``FOO`` are the SAME file on Windows/macOS.

`fs_slug` produces a safe, lowercased slug (lowercasing also folds case collisions into
one string, which a caller can then dedup); `unique_namer` hands out collision-free names
within one write. Keeping this in one module means every writer stays portable and can't
drift apart.
"""

from __future__ import annotations

import re

# Illegal in a Windows filename, plus C0 control chars. Collapse every run to one "_".
_UNSAFE_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')

# Reserved DOS device names — uncreatable on Windows regardless of extension.
_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def fs_slug(name: str) -> str:
    """A filesystem-safe, portable slug: lowercased (which also folds case-only
    collisions), Windows-illegal characters and spaces collapsed to underscores, trailing
    dots/spaces stripped, and reserved device names escaped. Never returns empty."""
    s = _UNSAFE_FS.sub("_", name).lower().replace(" ", "_")
    # Windows forbids a name ending in a dot or space; bare dots are confusing anyway.
    s = s.strip("._")
    if not s:
        return "unnamed"
    # A reserved device stem (case-insensitive) can't be a filename even as "con.yaml".
    if s in _RESERVED:
        s = f"_{s}"
    return s


def unique_namer():
    """Return (unique, reset) — `unique(rel)` returns `rel`, or `rel` with a `-2`, `-3`…
    suffix on the stem when that path was already handed out in this write. Slugging is
    lossy (two names can map to one filename), so without this a later object silently
    overwrites an earlier one. Keys are compared case-insensitively, because the target
    filesystem may be too. Returns POSIX-style paths."""
    used: set[str] = set()

    def unique(rel: str) -> str:
        rel = rel.replace("\\", "/")
        key = rel.lower()
        if key not in used:
            used.add(key)
            return rel
        stem, dot, ext = rel.rpartition(".")
        n = 2
        while f"{stem}-{n}{dot}{ext}".lower() in used:
            n += 1
        cand = f"{stem}-{n}{dot}{ext}"
        used.add(cand.lower())
        return cand

    return unique
