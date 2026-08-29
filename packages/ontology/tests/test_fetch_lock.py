"""ontology.lock modes + fetch + gitignored cache (spec §3)."""

from __future__ import annotations

import pytest
from mdl_ontology import (
    CACHE_REL,
    FetchError,
    Lock,
    OntologyLayerLock,
    compute_lock,
    fetch_all,
    fetch_layer,
)
from mdl_ontology.registry import build_registry

_TTL = """\
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix acme: <https://acme.example/onto/> .

acme:Party a skos:Concept ;
  skos:prefLabel "Party" ;
  skos:definition "A legal person or organisation." ;
  skos:altLabel "Counterparty" .
"""


def _artifact(tmp_path):
    src = tmp_path / "src" / "acme.ttl"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(_TTL, encoding="utf-8")
    return src


# --- lock round-trip -------------------------------------------------------


def test_lock_ontology_layers_roundtrip(tmp_path):
    lock = Lock.load(tmp_path)
    lock.ontology_layers["industry"] = OntologyLayerLock(
        mode="artifact",
        source="https://spec.example/fibo.owl",
        version="2026Q2",
        sha256="deadbeef",
        fmt="xml",
    )
    lock.save(tmp_path)

    reloaded = Lock.load(tmp_path)
    pin = reloaded.ontology_layers["industry"]
    assert pin.mode == "artifact"
    assert pin.version == "2026Q2"
    assert pin.sha256 == "deadbeef"
    assert pin.fmt == "xml"
    # the existing lock sections survive
    assert reloaded.dbt == lock.dbt


# --- fetch + verify --------------------------------------------------------


def test_compute_lock_then_fetch(tmp_path):
    src = _artifact(tmp_path)
    pin = compute_lock(tmp_path, "core", "artifact", str(src))
    assert pin.sha256 and len(pin.sha256) == 64
    # the cached copy exists under the gitignored cache
    cached = tmp_path / CACHE_REL / "core" / "core.ttl"
    assert cached.exists()

    # a fetch with the recorded hash succeeds and serves from cache
    lock = Lock.load(tmp_path)
    lock.ontology_layers["core"] = pin
    results = fetch_all(tmp_path, lock)
    assert len(results) == 1
    assert results[0].cached is True
    assert results[0].sha256 == pin.sha256


def test_fetch_fails_closed_on_tampered_hash(tmp_path):
    src = _artifact(tmp_path)
    pin = OntologyLayerLock(mode="artifact", source=str(src), sha256="0" * 64)
    with pytest.raises(FetchError, match="sha256 mismatch"):
        fetch_layer(tmp_path, "core", pin)
    # nothing cached on a failed verify
    assert not (tmp_path / CACHE_REL / "core").exists()


def test_fetch_repopulates_when_cache_missing(tmp_path):
    src = _artifact(tmp_path)
    pin = compute_lock(tmp_path, "core", "artifact", str(src))
    cached = tmp_path / CACHE_REL / "core" / "core.ttl"
    cached.unlink()  # simulate a fresh clone (cache gitignored, so absent)

    lock = Lock.load(tmp_path)
    lock.ontology_layers["core"] = pin
    results = fetch_all(tmp_path, lock)
    assert results[0].cached is False
    assert cached.exists()


# --- lock-based layer browses like a committed one -------------------------


def test_lock_layer_browsable_via_registry(tmp_path):
    src = _artifact(tmp_path)
    pin = compute_lock(tmp_path, "core", "artifact", str(src))
    lock = Lock.load(tmp_path)
    lock.ontology_layers["core"] = pin
    lock.save(tmp_path)

    # no committed ontology_stack — only the lock layer
    reg = build_registry(tmp_path, [], lock)
    loaded = reg.load()
    # loaded exactly once (no double-registration)
    assert loaded.count(loaded[0]) == 1
    hits = reg.search("party")
    assert any("Party" in (h.label or "") for h in hits)
    # synonym search works too (altLabel enrichment carries through the cache)
    syn = reg.search("counterparty")
    assert any(h.iri.endswith("Party") for h in syn)


def test_lock_layer_prefix_resolves_offline(tmp_path):
    """A locked layer with a bound prefix resolves its prefixed alignments offline
    against the fetched cache (the IBoR case: fibo-* / acme-* IRIs)."""
    src = _artifact(tmp_path)
    pin = compute_lock(tmp_path, "core", "artifact", str(src))
    pin.prefixes["acme"] = "https://acme.example/onto/"
    lock = Lock.load(tmp_path)
    lock.ontology_layers["core"] = pin
    lock.save(tmp_path)
    # the prefix survives the lock round-trip
    assert Lock.load(tmp_path).ontology_layers["core"].prefixes["acme"]

    reg = build_registry(tmp_path, [], lock)
    reg.load()
    assert reg.expand("acme:Party") == "https://acme.example/onto/Party"
    assert reg.resolves("acme:Party") is True


def test_committed_stack_wins_over_same_named_lock_layer(tmp_path):
    """A committed ontology_stack entry and a lock layer of the same name must not
    double-register (which would load the graph twice)."""
    src = _artifact(tmp_path)
    pin = compute_lock(tmp_path, "core", "artifact", str(src))
    lock = Lock.load(tmp_path)
    lock.ontology_layers["core"] = pin
    lock.save(tmp_path)

    stack = [
        {
            "name": "core",
            "layer": "core",
            "type": "local",
            "path": f"{CACHE_REL}/core",
            "prefixes": {"acme": "https://acme.example/onto/"},
        }
    ]
    reg = build_registry(tmp_path, stack, lock)
    loaded = reg.load()
    assert len(loaded) == 1  # not two
