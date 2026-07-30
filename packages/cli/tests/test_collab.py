"""Collaboration CLI tests: classify (§4), debt (§7), workspace scaffold (§2.1)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from mdl_cli.collab import (
    add_debt,
    classify_paths,
    ensure_git_hooks,
    expired_debt,
    load_debt,
)


def test_classify_routes():
    c = classify_paths(["model/conceptual/terms/counterparty.yaml"])
    assert c.routes == ["A"] and c.primary == "A"
    assert "mdl validate" in c.gates and "data-stewards" in c.reviewers

    c = classify_paths(["model/logical/entities/trade.yaml", "transform/warehouse/models/trade.sql"])
    assert c.routes == ["B", "C"]
    assert c.primary == "B"  # strictest gate leads
    assert "analytics-engineers" in c.reviewers and "data-architects" in c.reviewers

    c = classify_paths(["governance-profile.yaml"])
    assert c.primary == "E"
    assert any("gov plan" in g for g in c.gates)

    c = classify_paths(["model/.mdl/decisions.yaml"])  # tool state rides along
    assert c.routes == []

    c = classify_paths(["README.md"])
    assert c.routes == [] and c.unmatched == ["README.md"]


def test_debt_ledger_lifecycle(tmp_path: Path):
    entry = add_debt(tmp_path, "trade", "hotfix INC-4821", 14)
    assert entry["expires"] == (dt.date.today() + dt.timedelta(days=14)).isoformat()
    assert load_debt(tmp_path)[0]["entity"] == "trade"
    assert expired_debt(tmp_path) == []  # not expired yet

    # re-adding the same entity replaces, not duplicates
    add_debt(tmp_path, "trade", "renewed", 7)
    assert len(load_debt(tmp_path)) == 1

    # force expiry
    p = tmp_path / ".mdl" / "debt.yaml"
    p.write_text(p.read_text().replace(load_debt(tmp_path)[0]["expires"], "2020-01-01"))
    assert len(expired_debt(tmp_path)) == 1


def test_ensure_git_hooks_idempotent(tmp_path: Path):
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    actions = ensure_git_hooks(tmp_path)
    assert any(".gitattributes" in a for a in actions)
    assert any("merge drivers configured" in a for a in actions)
    ga = (tmp_path / ".gitattributes").read_text()
    assert "merge=mdl" in ga and "merge=mdl-state" in ga
    # idempotent: second run adds no attribute rules
    actions2 = ensure_git_hooks(tmp_path)
    assert not any(".gitattributes" in a for a in actions2)
    drv = subprocess.run(
        ["git", "-C", str(tmp_path), "config", "merge.mdl.driver"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert drv == "mdl merge-driver %O %A %B"


def test_workspace_scaffold(tmp_path: Path):
    import subprocess

    from mdl_cli.collab import scaffold_workspace
    from mdl_cli.scaffold import scaffold

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    written = scaffold_workspace(tmp_path, "acme_ibor", scaffold)
    assert (tmp_path / "model" / "mdl-project.yaml").exists()
    assert (tmp_path / "transform" / "warehouse" / "dbt_project.yml").exists()
    assert (tmp_path / ".github" / "CODEOWNERS").exists()
    assert (tmp_path / "acme_ibor.code-workspace").exists()
    assert (tmp_path / ".gitattributes").exists()
    co = (tmp_path / ".github" / "CODEOWNERS").read_text()
    assert "/model/conceptual/terms/" in co and "@data-stewards" in co
    ws = (tmp_path / "acme_ibor.code-workspace").read_text()
    assert '"modelith.modelDir": "model"' in ws
    assert len(written) >= 5
