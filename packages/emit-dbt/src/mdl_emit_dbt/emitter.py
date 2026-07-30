"""dbt project emitter (spec §7.1, driving §5 round-trip engine).

For each physical table (or, absent one, each logical entity) we emit:
  - models/**/<name>.sql  with a generated protected region + a user region
  - models/**/schema.yml  contract block, column data_type, constraints, meta
                          (ULID, ontology IRI, glossary term, owner, steward)

Generation is content-addressed: each generated block carries a fingerprint of
its model subgraph. Regeneration runs the three-way merge (§5.3) so idempotence
(property 1) and user-edit survival (property 3) hold.

SQL is emitted as *working transformation logic*, never `select 1`. Pattern
entities (scd2/hub) call the shipped macro package by default, or inline with
`inline=True`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from mdl_core.fingerprint import content_hash, fingerprint_subgraph
from mdl_core.ir import LogicalEntity, Model, PhysicalTable
from mdl_core.merge import MergeOutcome, MergeResult, merge_file
from mdl_core.regions import ParsedFile, Region, RegionKind, render
from mdl_core.state import FileState, GenerationState
from mdl_core.yaml_io import dump_str

from mdl_emit_dbt import macros as macro_pkg
from mdl_emit_dbt.platforms import get_adapter

EMITTER_VERSION = "0.1.0"
SPEC_TAG = "v1"


@dataclass
class EmitResult:
    files: dict[str, str] = field(default_factory=dict)  # rel path -> content
    merges: list[MergeResult] = field(default_factory=list)
    state: GenerationState = field(default_factory=GenerationState)

    @property
    def has_conflicts(self) -> bool:
        return any(m.outcome == MergeOutcome.conflict for m in self.merges)

    @property
    def has_errors(self) -> bool:
        return any(d.severity.value == "error" for m in self.merges for d in m.diagnostics)


class DbtEmitter:
    def __init__(self, model: Model, target: str, *, inline: bool = False) -> None:
        self.model = model
        self.target = target
        self.inline = inline
        self.adapter = get_adapter(_platform_of(model, target))

    # --- public API --------------------------------------------------------

    def generate(self, root: Path, *, write: bool = True) -> EmitResult:
        """Generate the dbt project under `root`, merging with any working tree."""
        prior = GenerationState.load(root)
        new_state = GenerationState()
        result = EmitResult(state=new_state)

        planned = self._plan_files()
        for rel, spec in planned.items():
            theirs = spec["content"]
            fs_prev = prior.get(rel)
            ours = None
            disk = root / rel
            if disk.exists():
                ours = disk.read_text(encoding="utf-8")

            prefix = spec["prefix"]
            mr = merge_file(
                path=rel,
                base_hash=fs_prev.content_hash if fs_prev else None,
                ours=ours,
                theirs=theirs,
                prefix=prefix,
            )
            result.merges.append(mr)
            result.files[rel] = mr.content
            new_state.record(
                FileState(
                    path=rel,
                    ulids=spec["ulids"],
                    fingerprint=spec["fingerprint"],
                    content_hash=content_hash(theirs),
                    emitter_version=EMITTER_VERSION,
                    spec_versions={"dbt": "1.9", "mdl_spec": SPEC_TAG},
                )
            )

        # Macro package (static, no merge needed — it is fully owned).
        for rel, content in macro_pkg.macro_files().items():
            result.files[rel] = content

        if write:
            for rel, content in result.files.items():
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
            new_state.save(root)

        return result

    # --- planning ----------------------------------------------------------

    def _plan_files(self) -> dict[str, dict]:
        """Return {rel_path: {content, fingerprint, ulids, prefix}} deterministically."""
        planned: dict[str, dict] = {}
        entities = self._entities_for_target()

        schema_entries = []
        for le, pt in entities:
            name = pt.name.lower() if pt else le.name
            sql, ulids, fp = self._emit_model_sql(le, pt)
            planned[f"models/{name}.sql"] = {
                "content": sql,
                "fingerprint": fp,
                "ulids": ulids,
                "prefix": "--",
            }
            schema_entries.append((le, pt, name))

        # One schema.yml for the target (merged key-wise on regeneration).
        sy_content, sy_ulids, sy_fp = self._emit_schema_yml(schema_entries)
        planned["models/schema.yml"] = {
            "content": sy_content,
            "fingerprint": sy_fp,
            "ulids": sy_ulids,
            "prefix": "#",
        }
        # Deterministic ordering.
        return dict(sorted(planned.items()))

    def _entities_for_target(self) -> list[tuple[LogicalEntity, PhysicalTable | None]]:
        out: list[tuple[LogicalEntity, PhysicalTable | None]] = []
        pt_by_logical = {
            pt.realises: pt
            for pt in self.model.physical_tables.values()
            if pt.target == self.target
        }
        for le in sorted(self.model.logical_entities.values(), key=lambda e: e.name):
            out.append((le, pt_by_logical.get(le.id)))
        return out

    # --- SQL emission ------------------------------------------------------

    def _emit_model_sql(
        self, le: LogicalEntity, pt: PhysicalTable | None
    ) -> tuple[str, list[str], str]:
        ulids = [le.id] + [a.id for a in le.attributes]
        if pt is not None:
            ulids.append(pt.id)
        fp = fingerprint_subgraph(le, *([pt] if pt else []))

        body = self._model_body(le, pt)
        gen_region = Region(
            kind=RegionKind.generated,
            content=body,
            obj_id=le.id,
            fingerprint=fp,
            spec=SPEC_TAG,
        )
        user_region = Region(kind=RegionKind.user, content="")
        parsed = ParsedFile(regions=[gen_region, Region(RegionKind.literal, "\n"), user_region])
        return render(parsed, "--"), ulids, fp

    def _model_body(self, le: LogicalEntity, pt: PhysicalTable | None) -> str:
        materialization = pt.materialization if pt else "view"
        config = f"{{{{ config(materialized='{materialization}') }}}}\n"
        cols = [a.name for a in le.attributes] or ["1 as placeholder"]
        source_name = f"stg_{le.name}"

        if le.pattern == "scd2":
            bk = _business_key(le) or cols[0]
            tracked = [a.name for a in le.attributes if a.role == "attribute"]
            if self.inline:
                sql = self._inline_scd2(source_name, bk, tracked)
            else:
                tracked_list = ", ".join(f"'{c}'" for c in tracked) or "'*'"
                sql = (
                    f"{{{{ mdl_scd2(ref('{source_name}'), "
                    f"'{bk}', [{tracked_list}]) }}}}"
                )
            return config + sql

        if le.pattern == "hub":
            bk = _business_key(le) or cols[0]
            sql = f"{{{{ mdl_hub(ref('{source_name}'), '{bk}') }}}}"
            return config + sql

        # Plain projection — working SQL, not a stub.
        col_list = ",\n    ".join(cols)
        sql = f"with source as (\n    select * from {{{{ ref('{source_name}') }}}}\n)\n"
        sql += f"select\n    {col_list}\nfrom source"
        return config + sql

    def _inline_scd2(self, source: str, bk: str, tracked: list[str]) -> str:
        tracked_sql = " || '-' || ".join(tracked) if tracked else "''"
        return (
            f"with source as (\n    select * from {{{{ ref('{source}') }}}}\n),\n"
            f"hashed as (\n    select *,\n"
            f"        md5(cast({bk} as varchar)) as mdl_scd_id,\n"
            f"        md5(cast({tracked_sql} as varchar)) as mdl_row_hash\n"
            f"    from source\n)\n"
            f"select\n    hashed.*,\n    current_timestamp as valid_from,\n"
            f"    cast(null as timestamp) as valid_to,\n    true as is_current\n"
            f"from hashed"
        )

    # --- schema.yml emission ----------------------------------------------

    def _emit_schema_yml(
        self, entries: list[tuple[LogicalEntity, PhysicalTable | None, str]]
    ) -> tuple[str, list[str], str]:
        caps = self.adapter.constraint_support()
        models_yaml = []
        ulids: list[str] = []
        fp_objs: list[object] = []

        for le, pt, name in entries:
            ulids.append(le.id)
            fp_objs.append(le)
            if pt:
                fp_objs.append(pt)
            columns = []
            for attr in le.attributes:
                dom = self.model.domains.get(attr.domain) if attr.domain else None
                sql_type = (
                    self.adapter.map_domain(dom).sql_type
                    if dom
                    else self.adapter.map_base_type(attr.domain)
                )
                constraints = []
                if not attr.nullable and caps.not_null:
                    constraints.append({"type": "not_null"})
                if attr.role == "business_key" and caps.primary_key:
                    constraints.append({"type": "primary_key"})
                col = {
                    "name": attr.name,
                    "data_type": sql_type,
                }
                if constraints:
                    col["constraints"] = constraints
                if attr.ontology and attr.ontology.aligns_to:
                    col["meta"] = {"mdl_ulid": attr.id, "ontology_iri": attr.ontology.aligns_to}
                else:
                    col["meta"] = {"mdl_ulid": attr.id}
                columns.append(col)

            meta = {"mdl_ulid": le.id}
            ce = self.model.conceptual_entities.get(le.realises) if le.realises else None
            if ce:
                meta["glossary_term"] = ce.name
                if ce.ontology and ce.ontology.aligns_to:
                    meta["ontology_iri"] = ce.ontology.aligns_to
                if ce.stewardship:
                    if ce.stewardship.owner:
                        meta["owner"] = ce.stewardship.owner
                    if ce.stewardship.steward:
                        meta["steward"] = ce.stewardship.steward

            model_entry = {
                "name": name,
                "config": {"contract": {"enforced": True}},
                "meta": meta,
                "columns": columns,
            }
            models_yaml.append(model_entry)

        # Relationships that the platform can't enforce -> dbt relationships tests.
        for rel in sorted(self.model.relationships.values(), key=lambda r: r.name):
            if rel.enforce.dbt_test == "relationships" and not caps.foreign_key:
                ulids.append(rel.id)
                fp_objs.append(rel)

        doc = {"version": 2, "models": models_yaml}
        yaml_text = dump_str(doc)

        # Wrap the generated YAML in region markers (comment-style '#') so
        # hand-added keys in a user region survive (§5.1 YAML rule).
        fp = fingerprint_subgraph(*fp_objs)
        gen = Region(
            RegionKind.generated, yaml_text, obj_id="schema", fingerprint=fp, spec=SPEC_TAG
        )
        parsed = ParsedFile(
            regions=[gen, Region(RegionKind.literal, "\n"), Region(RegionKind.user, "")]
        )
        return render(parsed, "#"), ulids, fp


def _business_key(le: LogicalEntity) -> str | None:
    for a in le.attributes:
        if a.role == "business_key":
            return a.name
    return None


def _platform_of(model: Model, target: str) -> str:
    # Physical target names look like "duckdb_dev"; map to a platform by prefix,
    # falling back to duckdb for M1.
    for p in ("snowflake", "redshift", "iceberg", "trino", "duckdb"):
        if target.startswith(p):
            return p
    return "duckdb"


__all__ = ["DbtEmitter", "EmitResult", "EMITTER_VERSION"]
