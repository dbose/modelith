"""A tiny OLS4-compatible ontology server for the Modelith IBoR demo.

This stands in for a real OLS4 instance so `mdl serve` can demonstrate ontology-anchored
modelling against a *remote* resolver with zero external dependencies and fully offline.
It serves the three OLS4 endpoints the OLS4Resolver uses, backed by the hand-curated FIBO
subset in `terms.json`:

  GET /api/ontologies                     -> the one demo ontology
  GET /api/search?q=<text>&ontology=<id>  -> ranked term hits
  GET /api/ontologies/<id>/terms?iri=<iri> -> one term + HAL parent links

`mdl serve` auto-spawns this when the demo project declares a `demo_ols:` block in
mdl-project.yaml (see demo/ibor/model/mdl-project.yaml). To point the demo at the real
public OLS4 instead, delete that block and change the ontology_stack source url to
`https://www.ebi.ac.uk/ols4/api`.

Run standalone:  python demo/ibor/ols/mock_ols.py --port 4901
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_DATA = json.loads((Path(__file__).parent / "terms.json").read_text(encoding="utf-8"))
_ONTOLOGY = _DATA["ontology"]
_TERMS = _DATA["terms"]
_BY_IRI = {t["iri"]: t for t in _TERMS}
_BY_OBO = {t["obo_id"]: t for t in _TERMS}


def _doc(t: dict) -> dict:
    """One search doc in OLS4 shape."""
    return {
        "iri": t["iri"],
        "obo_id": t["obo_id"],
        "label": t["label"],
        "description": [t["description"]] if t.get("description") else [],
        "synonym": list(t.get("synonyms") or []),
        "ontology_name": _ONTOLOGY["id"],
    }


def _term(t: dict, base: str) -> dict:
    """One term-detail in OLS4 shape, with HAL parent links."""
    parents = [p for p in (t.get("parents") or []) if p in _BY_OBO]
    links = {}
    if parents:
        links["parents"] = {"href": f"{base}/api/ontologies/{_ONTOLOGY['id']}/parents?id={t['obo_id']}"}
    return {
        "iri": t["iri"],
        "obo_id": t["obo_id"],
        "label": t["label"],
        "description": [t["description"]] if t.get("description") else [],
        "synonyms": list(t.get("synonyms") or []),
        "_links": links,
    }


def _rank(q: str) -> list[dict]:
    q = q.lower().strip()
    if not q:
        return list(_TERMS)
    scored = []
    for t in _TERMS:
        hay = (t["label"] + " " + t.get("description", "") + " " + " ".join(t.get("synonyms") or [])).lower()
        s = 0
        if q == t["label"].lower():
            s += 100
        if q in t["label"].lower():
            s += 40
        if q in t["obo_id"].lower():
            s += 30
        if q in hay:
            s += 10
        if s:
            scored.append((s, t))
    scored.sort(key=lambda x: -x[0])
    return [t for _, t in scored]


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence access logs
        pass

    def _json(self, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # noqa: N802 - http.server API
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        base = f"http://{self.headers.get('Host', '127.0.0.1')}"
        if u.path == "/api/ontologies":
            self._json(
                {
                    "_embedded": {
                        "ontologies": [
                            {
                                "ontologyId": _ONTOLOGY["id"],
                                "numberOfTerms": len(_TERMS),
                                "config": {
                                    "title": _ONTOLOGY["title"],
                                    "description": _ONTOLOGY["description"],
                                },
                            }
                        ]
                    }
                }
            )
        elif u.path == "/api/search":
            hits = _rank(qs.get("q", [""])[0])[: int(qs.get("rows", ["20"])[0])]
            self._json({"response": {"docs": [_doc(t) for t in hits]}})
        elif u.path.endswith("/terms"):
            iri = qs.get("iri", [""])[0]
            t = _BY_IRI.get(iri)
            if t is None:
                self._json({"_embedded": {"terms": []}})
            else:
                self._json({"_embedded": {"terms": [_term(t, base)]}})
        elif u.path.endswith("/parents"):
            t = _BY_OBO.get(qs.get("id", [""])[0])
            parents = [_BY_OBO[p] for p in (t.get("parents") if t else []) if p in _BY_OBO]
            self._json({"_embedded": {"terms": [_term(p, base) for p in parents]}})
        else:
            self.send_response(404)
            self.end_headers()


def main() -> None:
    ap = argparse.ArgumentParser(description="Mock OLS4 server for the IBoR demo.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=4901)
    args = ap.parse_args()
    srv = HTTPServer((args.host, args.port), _Handler)
    print(f"mock OLS4 (demo) on http://{args.host}:{args.port}/api  ({len(_TERMS)} terms)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
