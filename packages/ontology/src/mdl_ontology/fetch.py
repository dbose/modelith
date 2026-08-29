"""Ontology fetch + hash-verify (spec §3).

Resolves each locked ontology layer into a gitignored cache and verifies its sha256,
failing closed on a mismatch. This is the `dbt deps` / `npm ci` analogue: the lock
pins content, the cache holds it, no ontology files are committed to the repo.

Two resolution modes (spec §3):
- ``artifact``          - download an immutable file at a URL, hash covers the bytes.
- ``endpoint_snapshot`` - CONSTRUCT a point-in-time export from a live SPARQL store,
  hash covers the serialized snapshot (so alignment-pass approvals reference a fixed
  ontology commit, not a moving target).

Network access is only ever a fetch-time concern; nothing here runs during a build or
validate. A layer already present and matching in the cache is not re-fetched.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from rdflib import Graph

from mdl_ontology.lock import CACHE_REL, Lock, OntologyLayerLock

_EXT = {
    "turtle": "ttl",
    "ttl": "ttl",
    "xml": "rdf",
    "rdfxml": "rdf",
    "owl": "rdf",
    "json-ld": "jsonld",
    "jsonld": "jsonld",
    "nt": "nt",
    "n3": "n3",
}


class FetchError(RuntimeError):
    """A layer could not be fetched, or its hash did not match the lock."""


@dataclass
class FetchResult:
    layer: str
    path: Path
    sha256: str
    cached: bool  # True if served from an existing matching cache entry


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cache_dir(root: Path) -> Path:
    return Path(root) / CACHE_REL


def _cache_path(root: Path, layer: str, fmt: str) -> Path:
    ext = _EXT.get(fmt.lower(), "ttl")
    return cache_dir(root) / layer / f"{layer}.{ext}"


def _download_artifact(source: str, timeout: float) -> bytes:
    """Fetch an artifact's raw bytes. A local path or file:// URL is read directly so
    fetch works fully offline in tests and air-gapped setups."""
    if source.startswith("file://"):
        return Path(source[len("file://") :]).read_bytes()
    if "://" not in source:
        return Path(source).read_bytes()
    import httpx

    resp = httpx.get(source, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


def _export_endpoint(source: str, fmt: str, timeout: float) -> bytes:
    """CONSTRUCT the full graph from a SPARQL endpoint and serialize it — the
    point-in-time snapshot the lock hashes."""
    from rdflib.plugins.stores.sparqlstore import SPARQLStore

    store = SPARQLStore(query_endpoint=source)
    g = Graph(store=store)
    snap = Graph()
    for triple in g.triples((None, None, None)):
        snap.add(triple)
    return snap.serialize(format=_ser_format(fmt)).encode("utf-8")


def _ser_format(fmt: str) -> str:
    f = fmt.lower()
    if f in ("ttl", "turtle"):
        return "turtle"
    if f in ("rdf", "rdfxml", "owl", "xml"):
        return "xml"
    if f in ("jsonld", "json-ld"):
        return "json-ld"
    return f


def _resolve_bytes(pin: OntologyLayerLock, timeout: float) -> bytes:
    if pin.mode == "artifact":
        return _download_artifact(pin.source, timeout)
    if pin.mode == "endpoint_snapshot":
        return _export_endpoint(pin.source, pin.fmt, timeout)
    raise FetchError(f"unknown lock mode {pin.mode!r} for layer")


def fetch_layer(
    root: Path,
    layer: str,
    pin: OntologyLayerLock,
    *,
    verify: bool = True,
    timeout: float = 30.0,
) -> FetchResult:
    """Materialise one layer into the cache and verify its hash. Serves an existing
    matching cache entry without re-fetching. Raises FetchError on a hash mismatch
    when `verify` and the lock carries a sha256."""
    dest = _cache_path(root, layer, pin.fmt)
    if dest.exists() and pin.sha256:
        existing = sha256_bytes(dest.read_bytes())
        if existing == pin.sha256:
            return FetchResult(layer, dest, existing, cached=True)
    data = _resolve_bytes(pin, timeout)
    digest = sha256_bytes(data)
    if verify and pin.sha256 and digest != pin.sha256:
        raise FetchError(
            f"layer {layer!r}: sha256 mismatch — lock has {pin.sha256[:12]}…, "
            f"fetched {digest[:12]}… (source {pin.source}). Refusing to cache."
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return FetchResult(layer, dest, digest, cached=False)


def fetch_all(
    root: Path, lock: Lock | None = None, *, verify: bool = True, timeout: float = 30.0
) -> list[FetchResult]:
    """Fetch + verify every locked ontology layer. Fail-closed: the first hash
    mismatch aborts the whole fetch (like `npm ci`)."""
    lock = lock or Lock.load(root)
    results: list[FetchResult] = []
    for layer, pin in sorted(lock.ontology_layers.items()):
        results.append(fetch_layer(root, layer, pin, verify=verify, timeout=timeout))
    return results


def compute_lock(
    root: Path,
    layer: str,
    mode: str,
    source: str,
    *,
    version: str | None = None,
    snapshot_tag: str | None = None,
    fmt: str = "turtle",
    timeout: float = 30.0,
) -> OntologyLayerLock:
    """Fetch a layer once and return a fully-populated pin (with sha256) plus the
    cached copy on disk. Backs `mdl ontology lock <layer>`."""
    pin = OntologyLayerLock(
        mode=mode,
        source=source,
        version=version,
        snapshot_tag=snapshot_tag,
        fmt=fmt,
    )
    result = fetch_layer(root, layer, pin, verify=False, timeout=timeout)
    pin.sha256 = result.sha256
    return pin
