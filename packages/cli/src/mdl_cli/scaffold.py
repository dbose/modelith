"""`mdl init` scaffold: a minimal but valid model repo (spec §2.2)."""

from __future__ import annotations

from pathlib import Path

from mdl_core.ids import new_ulid

_GITIGNORE = """\
# dbt build artifacts (generated projects live here by default)
target/
dbt_packages/
logs/
# Fetched ontology layers — pinned by .mdl/lock.yaml, not committed (spec §3).
# Run `mdl ontology fetch` to repopulate. Like node_modules to package-lock.json.
.mdl/ontology-cache/
# Reverse/generation working state (the committed lock/decisions stay tracked).
.mdl/state/
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
# Ontology sources plug in by declaration. A source is either a local file
# vocabulary (default type: local) or a remote resolver browsed live for
# autocomplete/search (types: ols, ols-compatible, ontoportal, collibra). FIBO is
# only one example — ACORD / FHIR / ISO 20022 / your own plug in the same way.
# ontology_stack:
#   - name: fibo                       # local file vocabulary
#     layer: industry
#     format: turtle
#     path: ontologies/industry/fibo/2024.03
#     modules: [fnd, fbc]
#     prefixes:
#       fibo-fnd-pty-pty: "https://spec.edmcouncil.org/fibo/ontology/FND/Parties/Parties/"
#   - name: ols                        # public OLS4 (no auth) — live search only
#     layer: industry
#     type: ols
#     url: https://www.ebi.ac.uk/ols4/api
#   - name: bioportal                  # OntoPortal/BioPortal (needs an API key)
#     layer: domain
#     type: ontoportal
#     url: https://data.bioontology.org
#     apikey_env: BIOPORTAL_APIKEY
#   - name: collibra                   # Collibra Ontology Domains (bearer token)
#     layer: core
#     type: collibra
#     url: https://acme.collibra.com
#     token_env: COLLIBRA_TOKEN
#     domain_types: [Ontology]
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

    # Pin spec versions (spec §2.2, §13.1). Import lazily so `core`/scaffold stay
    # free of an ontology dependency at module load.
    from mdl_ontology.lock import Lock

    Lock().save(root)
    files[".mdl/lock.yaml"] = (root / ".mdl" / "lock.yaml").read_text(encoding="utf-8")
    return files
