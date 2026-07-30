"""`mdl init` scaffold: a minimal but valid model repo (spec §2.2)."""

from __future__ import annotations

from pathlib import Path

from mdl_core.ids import new_ulid

_GITIGNORE = """\
# dbt build artifacts (generated projects live here by default)
target/
dbt_packages/
logs/
"""


def scaffold(root: Path, *, project_name: str = "modelith_model") -> dict[str, str]:
    """Create the directory shape and seed a tiny conceptual+logical example.
    Returns {relative_path: content} for everything written."""
    sa_id = new_ulid()
    ce_id = new_ulid()
    le_id = new_ulid()
    attr_id = new_ulid()

    files: dict[str, str] = {}

    files["mdl-project.yaml"] = f"""\
# Modelith project config (spec §2.2)
name: {project_name}
dbt_target: duckdb_dev
platform_targets:
  - duckdb_dev
ontology_stack: []
naming:
  logical_case: snake
  physical_case: upper_snake
  abbreviations: {{}}
"""

    files["conceptual/subject-areas/counterparty.yaml"] = f"""\
id: {sa_id}
kind: subject_area
name: Counterparty Management
definition: >
  Everything about the parties the firm transacts with.
"""

    files["conceptual/entities/counterparty.yaml"] = f"""\
id: {ce_id}
kind: conceptual_entity
name: Counterparty
subject_area: {sa_id}
definition: >
  A legal person with whom the firm has or may have a contractual obligation.
stewardship:
  owner: risk-data-office
  steward: a.hough
synonyms: [Counterparty, CPTY]
"""

    files["logical/domains/identifier_bigint.yaml"] = f"""\
id: {new_ulid()}
kind: domain
name: identifier_bigint
base_type: bigint
definition: A surrogate or business identifier stored as a 64-bit integer.
"""

    files["logical/entities/counterparty.yaml"] = f"""\
id: {le_id}
kind: logical_entity
name: counterparty
realises: {ce_id}
attributes:
  - id: {attr_id}
    name: counterparty_id
    domain: identifier_bigint
    role: business_key
    nullable: false
"""

    files[".gitignore"] = _GITIGNORE

    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    # Empty state dirs the tool expects.
    (root / ".mdl" / "state").mkdir(parents=True, exist_ok=True)
    return files
