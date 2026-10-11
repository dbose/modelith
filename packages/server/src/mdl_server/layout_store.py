"""Visual layout sidecar — node positions and edge routes, per canvas view.

Layout is a VISUAL concern only. It must never appear in the model, drift,
validation, exports, diffs, proposals or dbt output. It lives in
`<model_dir>/.mdl/layout.json` — JSON, not YAML, so it stays out of every
`*.yaml` glob (dir_fingerprint, ModelRepo.load, status, the reverse prune) by
construction, and it is git-ignored by default.

The file is read-modify-write safe: unknown top-level keys and a higher
`version` are preserved, and a corrupt file is treated as empty (logged once)
rather than crashing the server. Writes are atomic (temp file + os.replace).
"""

from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("mdl.layout")

LAYOUT_REL = ".mdl/layout.json"
VERSION = 1
MAX_BYTES = 1_000_000  # 1 MB body cap (enforced by the route; a guard here too)
MAX_KEY_LEN = 64

# log a corrupt-file warning at most once per process so a bad file is not noisy
_warned_corrupt: set[str] = set()


def _path(model_dir: Path) -> Path:
    return model_dir / LAYOUT_REL


def _read_doc(model_dir: Path) -> dict[str, Any]:
    """The whole layout document, or a fresh empty one. Never raises: a missing
    or corrupt file yields an empty doc (the corrupt case is logged once)."""
    p = _path(model_dir)
    if not p.exists():
        return {"version": VERSION, "views": {}}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            raise ValueError("layout.json is not an object")
        doc.setdefault("version", VERSION)
        if not isinstance(doc.get("views"), dict):
            doc["views"] = {}
        return doc
    except (OSError, ValueError, json.JSONDecodeError) as e:
        key = str(p)
        if key not in _warned_corrupt:
            _warned_corrupt.add(key)
            log.warning("ignoring unreadable layout file %s: %s", p, e)
        return {"version": VERSION, "views": {}}


def _write_doc(model_dir: Path, doc: dict[str, Any]) -> None:
    """Write the document atomically with a stable, diff-friendly shape:
    sorted keys, 1-space indent, trailing newline."""
    base = model_dir / ".mdl"
    base.mkdir(parents=True, exist_ok=True)
    target = base / "layout.json"
    tmp = base / "layout.json.tmp"
    text = json.dumps(doc, indent=1, sort_keys=True) + "\n"
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)  # atomic within the same directory


def get_view(model_dir: Path, view: str) -> dict[str, Any]:
    """The one view's maps (empty when absent). Shape:
    `{version, view, nodes: {ulid: {x, y}}, edges: {ulid: {routeX}}}`."""
    doc = _read_doc(model_dir)
    v = doc.get("views", {}).get(view) or {}
    nodes = v.get("nodes") if isinstance(v.get("nodes"), dict) else {}
    edges = v.get("edges") if isinstance(v.get("edges"), dict) else {}
    return {"version": doc.get("version", VERSION), "view": view, "nodes": nodes, "edges": edges}


class LayoutError(ValueError):
    """A layout payload the route should reject with 422."""


def _finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _clean_nodes(nodes: Any, valid_ids: set[str]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    if not isinstance(nodes, dict):
        return out
    for nid, pos in nodes.items():
        if len(nid) > MAX_KEY_LEN:
            raise LayoutError(f"node id too long: {nid[:16]}…")
        if nid not in valid_ids:
            continue  # drop dead ids so the file does not accumulate them
        if not isinstance(pos, dict) or not _finite(pos.get("x")) or not _finite(pos.get("y")):
            raise LayoutError(f"node {nid}: x and y must be finite numbers")
        out[nid] = {"x": round(pos["x"]), "y": round(pos["y"])}
    return out


def _clean_edges(edges: Any, valid_ids: set[str]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    if not isinstance(edges, dict):
        return out
    for eid, route in edges.items():
        if len(eid) > MAX_KEY_LEN:
            raise LayoutError(f"edge id too long: {eid[:16]}…")
        if eid not in valid_ids:
            continue
        if not isinstance(route, dict) or not _finite(route.get("routeX")):
            raise LayoutError(f"edge {eid}: routeX must be a finite number")
        out[eid] = {"routeX": round(route["routeX"])}
    return out


def put_view(
    model_dir: Path,
    view: str,
    nodes: Any,
    edges: Any,
    *,
    entity_ids: set[str],
    relationship_ids: set[str],
) -> None:
    """Replace one view. Drops ids not present in the model. Raises LayoutError
    (→ 422) on a bad view key, over-long id, or non-finite number."""
    if not view or len(view) > MAX_KEY_LEN:
        raise LayoutError("view key missing or too long")
    clean_nodes = _clean_nodes(nodes, entity_ids)
    clean_edges = _clean_edges(edges, relationship_ids)
    doc = _read_doc(model_dir)
    doc.setdefault("version", VERSION)
    doc.setdefault("views", {})
    doc["views"][view] = {"nodes": clean_nodes, "edges": clean_edges}
    _write_doc(model_dir, doc)
    _ensure_gitignore(model_dir)


def delete_view(model_dir: Path, view: str | None) -> None:
    """Remove one view, or the whole file when `view` is None (reset all)."""
    p = _path(model_dir)
    if view is None:
        if p.exists():
            try:
                p.unlink()
            except OSError as e:  # noqa: BLE001 - best effort; never fatal
                log.warning("could not remove layout file %s: %s", p, e)
        return
    doc = _read_doc(model_dir)
    if view in doc.get("views", {}):
        del doc["views"][view]
        _write_doc(model_dir, doc)


def _ensure_gitignore(model_dir: Path) -> None:
    """On first write, make sure layout.json is git-ignored. Create
    `<model_dir>/.mdl/.gitignore` with `layout.json` when it is not already
    ignored. Only inside a git work tree, and never beyond creating this file."""
    mdl = model_dir / ".mdl"
    gi = mdl / ".gitignore"
    if gi.exists():
        try:
            if "layout.json" in gi.read_text(encoding="utf-8").splitlines():
                return
        except OSError:
            return
        # a .gitignore exists but does not list layout.json — do not edit the
        # user's file beyond appending our one line
        try:
            with gi.open("a", encoding="utf-8") as f:
                f.write("layout.json\n")
        except OSError:
            pass
        return
    # no .gitignore yet: only create one if we are inside a git work tree
    if not _in_git_tree(model_dir):
        return
    try:
        gi.write_text("layout.json\n", encoding="utf-8")
    except OSError:
        pass


def _in_git_tree(model_dir: Path) -> bool:
    """True when `model_dir` sits inside a git work tree (a `.git` exists at or
    above it). Cheap walk-up; no subprocess."""
    p = model_dir.resolve()
    for d in (p, *p.parents):
        if (d / ".git").exists():
            return True
    return False
