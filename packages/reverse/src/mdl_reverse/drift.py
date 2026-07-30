"""Drift classification (spec §5.4).

Compare the committed model (projected via projection.py) to the compiled dbt
manifest (manifest.py). Classify every difference:

| Severity  | Example                                             | CI default        |
| breaking  | column dropped, type narrowed, PK changed, rel gone | fail (exit 2)     |
| additive  | new column, new model, new test                     | warn, offer PR    |
| cosmetic  | description changed, tag added                      | info              |
| unmanaged | dbt model with no model counterpart                 | warn, reverse-eng |

"Committed model" is the source of truth. So:
- a column in the model but missing from the manifest => the warehouse/dbt lacks
  it. From the model's perspective the *manifest* dropped it => breaking.
- a column in the manifest but not the model => additive (the project grew;
  reconcile can fold it into the model).
- a manifest model with no model counterpart => unmanaged.

Direction matters and is easy to get backwards; the tests pin it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from mdl_core.ir import Model
from mdl_reverse import lifting
from mdl_reverse.manifest import ManifestProjection
from mdl_reverse.projection import ExpectedModel, project_model


class DriftSeverity(str, Enum):
    breaking = "breaking"
    additive = "additive"
    cosmetic = "cosmetic"
    unmanaged = "unmanaged"

    @property
    def rank(self) -> int:
        return {"breaking": 4, "unmanaged": 3, "additive": 2, "cosmetic": 1}[self.value]


class DriftKind(str, Enum):
    column_dropped = "column_dropped"
    column_added = "column_added"
    type_changed = "type_changed"
    type_narrowed = "type_narrowed"
    pk_changed = "pk_changed"
    relationship_removed = "relationship_removed"
    model_added = "model_added"
    model_removed = "model_removed"
    unmanaged_model = "unmanaged_model"
    description_changed = "description_changed"
    contract_disabled = "contract_disabled"


@dataclass
class DriftItem:
    severity: DriftSeverity
    kind: DriftKind
    model: str
    detail: str
    column: str | None = None
    # for reconcile: structured payload describing how to fold into the model
    payload: dict = field(default_factory=dict)


@dataclass
class DriftReport:
    items: list[DriftItem] = field(default_factory=list)
    target: str = ""

    def add(self, item: DriftItem) -> None:
        self.items.append(item)

    def by_severity(self, sev: DriftSeverity) -> list[DriftItem]:
        return [i for i in self.items if i.severity == sev]

    @property
    def has_breaking(self) -> bool:
        return any(i.severity == DriftSeverity.breaking for i in self.items)

    @property
    def max_severity(self) -> DriftSeverity | None:
        if not self.items:
            return None
        return max((i.severity for i in self.items), key=lambda s: s.rank)

    def reconcilable(self) -> list[DriftItem]:
        """Additive + cosmetic deltas that --reconcile folds into the model."""
        return [
            i
            for i in self.items
            if i.severity in (DriftSeverity.additive, DriftSeverity.cosmetic)
        ]


# Narrowing is only meaningful *within* a comparable type family. A change across
# families (BIGINT -> DATE) is a plain type change, not a narrowing. Both are
# breaking today, but the distinction drives the message and future auto-cast logic.
_FAMILIES: dict[str, list[str]] = {
    # ordered widest-last; index = width within the family
    "numeric": ["BOOLEAN", "INTEGER", "BIGINT", "DECIMAL(38,0)", "DECIMAL(38,2)", "DOUBLE"],
    "string": ["VARCHAR(20)", "VARCHAR"],
    "temporal": ["DATE", "TIMESTAMP"],
}


def _family_and_width(t: str) -> tuple[str, int] | None:
    for fam, order in _FAMILIES.items():
        if t in order:
            return fam, order.index(t)
    return None


def _is_narrowing(old: str | None, new: str | None) -> bool:
    if not old or not new:
        return False
    o = _family_and_width(old)
    n = _family_and_width(new)
    if o is None or n is None or o[0] != n[0]:
        return False  # unknown type or cross-family -> not "narrowing"
    return n[1] < o[1]


def compute_drift(
    model: Model, manifest: ManifestProjection, target: str
) -> DriftReport:
    report = DriftReport(target=target)
    expected = project_model(model, target)

    expected_names = set(expected)
    manifest_names = manifest.model_names()

    # Models the model declares but the manifest lacks -> removed from dbt (breaking).
    for name in sorted(expected_names - manifest_names):
        report.add(
            DriftItem(
                severity=DriftSeverity.breaking,
                kind=DriftKind.model_removed,
                model=name,
                detail=f"model {name!r} exists in the Modelith model but not in the dbt project",
            )
        )

    # Manifest models with no model counterpart -> unmanaged. Staging/intermediate
    # models are engineer-owned by design (same rule as reverse lifting §6.3), so
    # they are not drift — flagging every stg_* would bury real findings in noise.
    for name in sorted(manifest_names - expected_names):
        mm = manifest.models[name]
        if lifting.is_staging(name, mm.tags):
            continue
        report.add(
            DriftItem(
                severity=DriftSeverity.unmanaged,
                kind=DriftKind.unmanaged_model,
                model=name,
                detail=f"dbt model {name!r} has no Modelith counterpart",
                payload={"reverse_engineer": True},
            )
        )

    # Models present in both -> column-level diff.
    for name in sorted(expected_names & manifest_names):
        _diff_columns(expected[name], manifest.models[name], report)
        _diff_metadata(expected[name], manifest.models[name], report)
        _diff_relationships(expected[name], manifest.models[name], report)

    return report


def _diff_columns(exp: ExpectedModel, man, report: DriftReport) -> None:
    exp_cols = exp.columns
    man_cols = man.columns

    for col in sorted(set(exp_cols) - set(man_cols)):
        # In the model, missing from the manifest: the compiled project dropped it.
        ec = exp_cols[col]
        report.add(
            DriftItem(
                severity=DriftSeverity.breaking,
                kind=DriftKind.column_dropped,
                model=exp.name,
                column=col,
                detail=f"column {exp.name}.{col} is in the model but dropped from the dbt project",
                payload={"data_type": ec.data_type},
            )
        )

    for col in sorted(set(man_cols) - set(exp_cols)):
        mc = man_cols[col]
        report.add(
            DriftItem(
                severity=DriftSeverity.additive,
                kind=DriftKind.column_added,
                model=exp.name,
                column=col,
                detail=f"column {exp.name}.{col} exists in dbt but not the model",
                payload={"data_type": mc.data_type, "description": mc.description},
            )
        )

    for col in sorted(set(exp_cols) & set(man_cols)):
        ec = exp_cols[col]
        mc = man_cols[col]
        if ec.data_type and mc.data_type and ec.data_type != mc.data_type:
            if _is_narrowing(ec.data_type, mc.data_type):
                sev, kind = DriftSeverity.breaking, DriftKind.type_narrowed
            else:
                sev, kind = DriftSeverity.breaking, DriftKind.type_changed
            report.add(
                DriftItem(
                    severity=sev,
                    kind=kind,
                    model=exp.name,
                    column=col,
                    detail=(
                        f"column {exp.name}.{col} type {ec.data_type} (model) "
                        f"vs {mc.data_type} (dbt)"
                    ),
                    payload={"model_type": ec.data_type, "dbt_type": mc.data_type},
                )
            )


def _diff_metadata(exp: ExpectedModel, man, report: DriftReport) -> None:
    if exp.contract_enforced and not man.contract_enforced:
        report.add(
            DriftItem(
                severity=DriftSeverity.breaking,
                kind=DriftKind.contract_disabled,
                model=exp.name,
                detail=f"contract enforced in model but not in dbt for {exp.name!r}",
            )
        )
    # Whitespace-normalised: YAML folded scalars carry trailing newlines that a
    # manifest string does not.
    if (exp.description or "").strip() != (man.description or "").strip():
        report.add(
            DriftItem(
                severity=DriftSeverity.cosmetic,
                kind=DriftKind.description_changed,
                model=exp.name,
                detail=f"description differs for {exp.name!r}",
                payload={"dbt_description": man.description},
            )
        )


def _diff_relationships(exp: ExpectedModel, man, report: DriftReport) -> None:
    exp_rels = set(exp.relationships)
    man_rels = set(man.relationship_tests)
    for col, to in sorted(exp_rels - man_rels):
        report.add(
            DriftItem(
                severity=DriftSeverity.breaking,
                kind=DriftKind.relationship_removed,
                model=exp.name,
                column=col,
                detail=(
                    f"relationship {exp.name}.{col} -> {to} declared in model but "
                    f"absent from dbt tests"
                ),
            )
        )
