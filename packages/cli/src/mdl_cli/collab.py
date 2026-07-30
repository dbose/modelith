"""Collaboration-model mechanics (docs/collaboration-model.md).

- classify_paths: §4 change routes A–E from the paths a PR touches
- debt ledger: §7 the valve that stops the gate becoming a blocker
- ensure_git_hooks: §6.1 semantic merge driver wiring
- scaffold_workspace: §2.1 one repo, two sibling roots
"""

from __future__ import annotations

import datetime as _dt
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from mdl_core.yaml_io import dump_str, load_str

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


def changed_paths(base: str) -> list[str]:
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git diff failed")
    return [line for line in proc.stdout.splitlines() if line.strip()]


# --- §7 the debt valve ---------------------------------------------------------

DEBT_REL = ".mdl/debt.yaml"


def add_debt(model_dir: Path, entity: str, reason: str, expires_days: int) -> dict:
    p = model_dir / DEBT_REL
    doc = load_str(p.read_text(encoding="utf-8")) if p.exists() else None
    doc = doc or {"debt": []}
    today = _dt.date.today()
    entry = {
        "entity": entity,
        "reason": reason,
        "created": today.isoformat(),
        "expires": (today + _dt.timedelta(days=expires_days)).isoformat(),
    }
    doc["debt"] = [d for d in (doc.get("debt") or []) if d.get("entity") != entity]
    doc["debt"].append(entry)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dump_str(doc), encoding="utf-8")
    return entry


def load_debt(model_dir: Path) -> list[dict]:
    p = model_dir / DEBT_REL
    if not p.exists():
        return []
    doc = load_str(p.read_text(encoding="utf-8")) or {}
    return list(doc.get("debt") or [])


def expired_debt(model_dir: Path) -> list[dict]:
    today = _dt.date.today().isoformat()
    return [d for d in load_debt(model_dir) if str(d.get("expires", "")) < today]


# --- §6.1 merge-driver wiring ---------------------------------------------------

_GITATTRIBUTES_LINES = [
    "{model}/**/*.yaml merge=mdl",
    "**/.mdl/decisions.yaml merge=mdl",
    "**/.mdl/state/**/*.json merge=mdl-state",
]


def ensure_git_hooks(repo_root: Path, model_root: str = "model") -> list[str]:
    """Idempotently wire .gitattributes + the git merge-driver config."""
    actions: list[str] = []
    ga = repo_root / ".gitattributes"
    existing = ga.read_text(encoding="utf-8") if ga.exists() else ""
    additions = []
    for line in _GITATTRIBUTES_LINES:
        line = line.format(model=model_root.rstrip("/"))
        if line not in existing:
            additions.append(line)
    if additions:
        body = existing.rstrip("\n") + ("\n" if existing else "") + "\n".join(additions)
        ga.write_text(body + "\n")
        actions.append(f".gitattributes += {len(additions)} rule(s)")

    def _git(*args: str) -> bool:
        return (
            subprocess.run(
                ["git", "-C", str(repo_root), *args], capture_output=True, timeout=15
            ).returncode
            == 0
        )

    if _git("rev-parse", "--git-dir"):
        _git("config", "merge.mdl.name", "Modelith semantic model merge")
        _git("config", "merge.mdl.driver", "mdl merge-driver %O %A %B")
        _git("config", "merge.mdl-state.name", "Modelith generation-state merge")
        _git("config", "merge.mdl-state.driver", "mdl merge-driver --state %O %A %B")
        actions.append("git merge drivers configured (merge.mdl, merge.mdl-state)")
    else:
        actions.append("not a git repo — .gitattributes written, run again after `git init`")
    return actions


# --- §2.1 workspace scaffold -----------------------------------------------------

_CODEOWNERS = """\
# Ownership mirrors the layer stack (collaboration model §3).
# Replace the placeholder teams with your org's.

# Conceptual: the business owns meaning
/{model}/conceptual/terms/           @data-stewards
/{model}/conceptual/entities/        @data-architects @data-stewards
/{model}/conceptual/subject-areas/   @data-architects

# Logical: architecture owns structure
/{model}/logical/                    @data-architects
/{model}/patterns/                   @data-architects

# Physical + semantic: shared, architects hold the contract
/{model}/physical/                   @analytics-engineers @data-architects
/{model}/semantic/                   @data-architects @analytics-engineers

# Transformation code: engineering owns implementation
/transform/                          @analytics-engineers

# Governance mapping and ontology: central, high blast radius
/governance-profile.yaml             @data-governance @data-architects
/ontologies/                         @data-architects
/{model}/mdl-project.yaml            @data-architects
/{model}/.mdl/lock.yaml              @data-architects

# Generated state: tool-owned, humans should rarely touch
/transform/**/.mdl/state/            @data-platform
"""

_WORKSPACE = """\
{{
  "folders": [
    {{ "name": "model", "path": "{model}" }},
    {{ "name": "transform", "path": "transform/warehouse" }},
    {{ "name": "repo", "path": "." }}
  ],
  "settings": {{
    "modelith.modelDir": "{model}",
    "modelith.dbtProjectDir": "transform/warehouse"
  }}
}}
"""

_DBT_PROJECT_STUB = """\
name: warehouse
version: "1.0.0"
profile: warehouse
model-paths: ["models"]
macro-paths: ["macros"]
target-path: target
"""


def scaffold_workspace(root: Path, project_name: str, scaffold_model) -> list[str]:
    """One repo, two sibling roots (§2.1): model/ + transform/warehouse/, with
    the collaboration plumbing (CODEOWNERS, .code-workspace, merge driver,
    classify CI). `scaffold_model` is the existing model scaffolder."""
    written: list[str] = []
    model_root = "model"  # §2.1 naming caution: singular removes the ambiguity

    scaffold_model(root / model_root, project_name=project_name)
    written.append(f"{model_root}/ (model repo)")

    tw = root / "transform" / "warehouse"
    (tw / "models" / "staging").mkdir(parents=True, exist_ok=True)
    (tw / "macros").mkdir(parents=True, exist_ok=True)
    if not (tw / "dbt_project.yml").exists():
        (tw / "dbt_project.yml").write_text(_DBT_PROJECT_STUB)
    written.append("transform/warehouse/ (dbt project)")

    (root / "ontologies" / "industry").mkdir(parents=True, exist_ok=True)

    gh = root / ".github"
    gh.mkdir(exist_ok=True)
    if not (gh / "CODEOWNERS").exists():
        (gh / "CODEOWNERS").write_text(_CODEOWNERS.format(model=model_root))
        written.append(".github/CODEOWNERS")

    ws = root / f"{project_name}.code-workspace"
    if not ws.exists():
        ws.write_text(_WORKSPACE.format(model=model_root))
        written.append(ws.name)

    written += ensure_git_hooks(root, model_root)
    return written
