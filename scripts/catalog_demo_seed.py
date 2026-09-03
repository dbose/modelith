"""Seed a local catalog with all three demo models, each openable to its LDM canvas.

Turns each demo model into a throwaway local git repo (so the git backend can clone +
check it out at a commit), then publishes a catalog entry pointing at it. After running
this, `mdl catalog serve` lists all three and clicking a card opens its ER canvas.

    uv run python scripts/catalog_demo_seed.py

Everything it writes lives under ~/.modelith (catalog-cache + demo source repos) and
/tmp-free scratch under .catalog-demo/, so it never touches your working tree.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from mdl_catalog import GitBackend, RealGitRunner, entry_from_repo

ROOT = Path(__file__).resolve().parent.parent
SANDBOX = ROOT / ".catalog-demo"  # throwaway source repos
CACHE = Path.home() / ".modelith" / "catalog-cache"
SOURCES = Path.home() / ".modelith" / "sources"

# (catalog display purpose, path to the model dir inside the demo)
DEMOS = [
    ("ibor", ROOT / "demo" / "ibor" / "model"),
    ("legacy-warehouse", ROOT / "demo" / "legacy-warehouse" / "reversed-model"),
    ("retail-dwh", ROOT / "demo" / "retail-dwh" / "ldm"),
]


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def make_source_repo(name: str, model_dir: Path) -> tuple[Path, str]:
    """Copy the demo model into a fresh git repo; return (repo_path, head_sha)."""
    repo = SANDBOX / f"{name}-repo"
    shutil.rmtree(repo, ignore_errors=True)
    (repo / "model").parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(model_dir, repo / "model")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "demo@modelith.local")
    _git(repo, "config", "user.name", "modelith-demo")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"seed {name}")
    return repo, _git(repo, "rev-parse", "HEAD")


def main() -> None:
    # Fresh start so re-running is deterministic.
    shutil.rmtree(SANDBOX, ignore_errors=True)
    shutil.rmtree(CACHE, ignore_errors=True)
    shutil.rmtree(SOURCES, ignore_errors=True)

    be = GitBackend(work_dir=CACHE, remote=None, runner=RealGitRunner())
    for name, model_dir in DEMOS:
        if not (model_dir / "mdl-project.yaml").exists():
            print(f"skip {name}: no model at {model_dir}")
            continue
        repo, head = make_source_repo(name, model_dir)
        entry = entry_from_repo(repo / "model", remote=str(repo), commit=head)
        be.publish(entry)
        print(f"published {entry.model:20s} @ {head[:8]}  layers={entry.ontology_layers}")

    print("\ncatalog now holds:", sorted(e.model for e in be.list()))
    print("run:  uv run mdl catalog serve")


if __name__ == "__main__":
    main()
