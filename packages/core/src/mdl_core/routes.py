"""Change routes (collaboration model §4): who reviews a change, and which gates run.

Pure string-prefix logic over a static table — no git, no yaml, no subprocess —
so it lives in core where every surface can reach it. It was in `mdl_cli.collab`,
which the server cannot import: `modelith-cli` depends on `modelith-server`, so
that direction is a dependency cycle. The CLI re-exports from here.

A PR spanning several routes inherits the STRICTEST gate (`_PRECEDENCE`), which is
why a meaning-only change bundled with a structural one waits on architects.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- §4 change routes ---------------------------------------------------------

_ROUTE_META = {
    "A": {
        "name": "Meaning",
        "reviewers": ["data-stewards"],
        "gates": ["mdl validate", "mdl ontology check"],
    },
    "B": {
        "name": "Structure",
        "reviewers": ["data-architects", "analytics-engineers"],
        "gates": ["mdl validate", "mdl generate --dry-run", "mdl drift --check"],
    },
    "C": {
        "name": "Implementation",
        "reviewers": ["analytics-engineers"],
        "gates": ["mdl drift --check"],
    },
    "E": {
        "name": "Governance",
        "reviewers": ["data-governance", "data-architects"],
        "gates": ["mdl gov conformance", "mdl gov plan"],
    },
}
# Precedence when a PR spans routes: the strictest gate wins the headline.
_PRECEDENCE = ["B", "E", "C", "A"]


@dataclass
class Classification:
    routes: list[str] = field(default_factory=list)
    primary: str | None = None
    gates: list[str] = field(default_factory=list)
    reviewers: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "routes": self.routes,
            "primary": self.primary,
            "primary_name": _ROUTE_META[self.primary]["name"] if self.primary else None,
            "gates": self.gates,
            "reviewers": self.reviewers,
            "unmatched": self.unmatched,
        }


def classify_paths(
    paths: list[str], *, model_root: str = "model", transform_root: str = "transform"
) -> Classification:
    routes: set[str] = set()
    unmatched: list[str] = []
    m = model_root.rstrip("/")
    t = transform_root.rstrip("/")
    for p in paths:
        p = p.strip().lstrip("./")
        if not p:
            continue
        if p.startswith((f"{m}/conceptual/",)):
            routes.add("A")
        elif p.startswith((f"{m}/logical/", f"{m}/patterns/")):
            routes.add("B")
        elif p.startswith((f"{t}/", f"{m}/physical/", f"{m}/semantic/")):
            routes.add("C")
        elif (
            p.endswith("governance-profile.yaml")
            or p.endswith(".mdl/lock.yaml")
            or p.endswith(f"{m}/mdl-project.yaml")
            or p.endswith("mdl-project.yaml")
        ):
            routes.add("E")
        elif ".mdl/state/" in p or ".mdl/decisions.yaml" in p or ".mdl/debt.yaml" in p:
            continue  # bot/tool-owned state rides along with whatever else changed
        else:
            unmatched.append(p)

    ordered = [r for r in _PRECEDENCE if r in routes]
    gates: list[str] = []
    reviewers: list[str] = []
    for r in ordered:
        for g in _ROUTE_META[r]["gates"]:
            if g not in gates:
                gates.append(g)
        for rv in _ROUTE_META[r]["reviewers"]:
            if rv not in reviewers:
                reviewers.append(rv)
    return Classification(
        routes=sorted(routes),
        primary=ordered[0] if ordered else None,
        gates=gates,
        reviewers=reviewers,
        unmatched=unmatched,
    )
