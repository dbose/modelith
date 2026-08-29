"""Vendor a private ontology file into the repo and wire it into the project.

Shared by the canvas upload endpoint and the `mdl ontology add` CLI: it writes the
uploaded/added `.ttl`/`.jsonld`/`.owl` under `ontologies/<layer>/` and appends a
`local` source entry to `mdl-project.yaml`, preserving comments and key order.
"""

from __future__ import annotations

import re
from pathlib import Path

from rdflib import Graph

from mdl_core.yaml_io import dump_file, load_file

_EXT_FORMAT = {
    ".ttl": ("turtle", "turtle"),
    ".rdf": ("xml", "xml"),
    ".owl": ("xml", "xml"),
    ".xml": ("xml", "xml"),
    ".jsonld": ("json-ld", "jsonld"),
    ".nt": ("nt", "nt"),
    ".n3": ("n3", "n3"),
}
_LAYERS = {"industry", "core", "domain", "specialised"}


def _slug(name: str) -> str:
    return re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_").lower() or "ontology"


def save_ontology_upload(
    model_dir: Path,
    *,
    filename: str,
    content: bytes,
    layer: str = "core",
    prefix: str | None = None,
    prefix_iri: str | None = None,
    name: str | None = None,
) -> dict:
    """Write the ontology file into the repo and add its `ontology_stack` entry.

    Returns a summary dict: {name, layer, path, format, prefix, prefix_iri, valid}.
    Raises ValueError on a bad layer, unknown extension, or unparseable content.
    """
    model_dir = Path(model_dir)
    if layer not in _LAYERS:
        raise ValueError(f"layer must be one of {sorted(_LAYERS)}, got {layer!r}")
    ext = Path(filename).suffix.lower()
    if ext not in _EXT_FORMAT:
        raise ValueError(f"unsupported ontology extension {ext!r}")
    rdflib_fmt, mdl_fmt = _EXT_FORMAT[ext]

    # Validate it parses before committing it to the repo.
    try:
        g = Graph()
        g.parse(data=content, format=rdflib_fmt)
    except Exception as e:  # noqa: BLE001 - surface a clean error to the caller
        raise ValueError(f"could not parse ontology as {rdflib_fmt}: {e}") from e

    src_name = _slug(name or Path(filename).stem)
    rel_path = f"ontologies/{layer}/{src_name}{ext}"
    dest = model_dir / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)

    # If no prefix IRI was given, try to infer the most common namespace base.
    if prefix and not prefix_iri:
        prefix_iri = _guess_namespace(g)

    _wire_project(model_dir, src_name, layer, rel_path, mdl_fmt, prefix, prefix_iri)

    return {
        "name": src_name,
        "layer": layer,
        "path": rel_path,
        "format": mdl_fmt,
        "prefix": prefix,
        "prefix_iri": prefix_iri,
        "term_count": len(set(g.subjects())),
        "valid": True,
    }


def _guess_namespace(g: Graph) -> str | None:
    """The namespace shared by the most subjects (a decent default prefix IRI)."""
    from collections import Counter

    counts: Counter[str] = Counter()
    for s in g.subjects():
        iri = str(s)
        for sep in ("#", "/"):
            if sep in iri:
                counts[iri.rsplit(sep, 1)[0] + sep] += 1
                break
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def _wire_project(
    model_dir: Path,
    name: str,
    layer: str,
    rel_path: str,
    fmt: str,
    prefix: str | None,
    prefix_iri: str | None,
) -> None:
    """Append a local source to mdl-project.yaml, comment-preserving."""
    proj_path = model_dir / "mdl-project.yaml"
    doc = load_file(proj_path)
    stack = doc.get("ontology_stack")
    if stack is None:
        stack = []
        doc["ontology_stack"] = stack

    # Idempotent: replace an existing entry with the same name.
    for i, entry in enumerate(list(stack)):
        if isinstance(entry, dict) and entry.get("name") == name:
            del stack[i]
            break

    entry: dict = {"type": "local", "name": name, "layer": layer, "path": rel_path}
    if fmt != "turtle":
        entry["format"] = fmt
    if prefix and prefix_iri:
        entry["prefixes"] = {prefix: prefix_iri}
    stack.append(entry)
    dump_file(proj_path, doc)
