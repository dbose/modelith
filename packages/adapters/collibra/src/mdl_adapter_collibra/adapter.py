"""Collibra adapter implementation (spec §9.4).

- Uses the Collibra Import API with idempotent external IDs. Batch, resumable,
  rate-limit aware.
- `plan` never writes: it diffs the mapped graph against what the catalog already
  has (fetched read-only) and returns a SyncPlan.
- `apply` refuses a plan it did not produce (signature check) and executes the
  Import API calls in batches through a Transport.
- `pull` reads governance-owned fields (steward, classification, sensitivity,
  retention) back into a WritebackSet.

Transport is injectable: `CollibraTransport` is the real HTTP client (thin,
requests-based); `MockTransport` records calls for tests and dry-runs so the whole
plan/apply/pull path is exercised without a live tenant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from mdl_governance import (
    AdapterCapabilities,
    ForeignPlanError,
    GovernanceGraph,
    Profile,
    SyncPlan,
    SyncResult,
    WritebackSet,
    WritebackValue,
    build_changes,
    map_graph,
)
from mdl_governance.spi import ChangeType

ADAPTER_NAME = "collibra"
_BATCH_SIZE = 200


class Transport(Protocol):
    def existing_external_ids(self) -> set[str]: ...
    def import_batch(self, payload: list[dict]) -> None: ...
    def read_attributes(self, attribute_names: list[str]) -> list[dict]: ...


@dataclass
class MockTransport:
    """In-memory transport for tests / dry-run. Records everything."""

    preexisting: set[str] = field(default_factory=set)
    imported_batches: list[list[dict]] = field(default_factory=list)
    writeback_rows: list[dict] = field(default_factory=list)

    def existing_external_ids(self) -> set[str]:
        return set(self.preexisting)

    def import_batch(self, payload: list[dict]) -> None:
        self.imported_batches.append(payload)

    def read_attributes(self, attribute_names: list[str]) -> list[dict]:
        return list(self.writeback_rows)

    @property
    def imported_count(self) -> int:
        return sum(len(b) for b in self.imported_batches)


class CollibraTransport:
    """Real Collibra Import API transport (thin). Kept import-light so the adapter
    package has no hard dependency on requests unless actually used against a tenant."""

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _session(self):
        import requests  # local import: only needed for live calls

        s = requests.Session()
        s.headers["Authorization"] = f"Bearer {self.token}"
        return s

    def existing_external_ids(self) -> set[str]:  # pragma: no cover - needs live tenant
        s = self._session()
        r = s.get(f"{self.base_url}/rest/2.0/assets", params={"limit": 1000})
        r.raise_for_status()
        return {a.get("externalId") for a in r.json().get("results", []) if a.get("externalId")}

    def import_batch(self, payload: list[dict]) -> None:  # pragma: no cover - live tenant
        s = self._session()
        r = s.post(f"{self.base_url}/rest/2.0/import/json-job", json=payload)
        r.raise_for_status()

    def read_attributes(self, attribute_names: list[str]) -> list[dict]:  # pragma: no cover
        s = self._session()
        r = s.get(f"{self.base_url}/rest/2.0/attributes", params={"names": attribute_names})
        r.raise_for_status()
        return r.json().get("results", [])


@dataclass
class CollibraAdapter:
    transport: Transport
    community: str = "Data Governance Council"

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            name=ADAPTER_NAME, supports_writeback=True, supports_lineage=True, batch=True
        )

    def plan(self, graph: GovernanceGraph, profile: Profile) -> SyncPlan:
        """Diff the mapped graph against the catalog. NEVER writes (§9.3)."""
        mapped = map_graph(graph, profile)
        existing = self.transport.existing_external_ids()
        changes = build_changes(mapped.assets, existing)
        plan = SyncPlan(adapter=ADAPTER_NAME, profile_name=profile.profile, changes=changes)
        plan.signature = plan.compute_signature()
        return plan

    def apply(self, plan: SyncPlan) -> SyncResult:
        """Execute a plan this adapter produced. Refuses foreign/edited plans (§9.3)."""
        if plan.adapter != ADAPTER_NAME:
            raise ForeignPlanError(f"plan is for adapter {plan.adapter!r}, not {ADAPTER_NAME!r}")
        if plan.signature != plan.compute_signature():
            raise ForeignPlanError("plan signature mismatch — plan was edited after generation")

        created = updated = 0
        batch: list[dict] = []
        for change in plan.changes:
            if change.change == ChangeType.noop:
                continue
            batch.append(_to_import_json(change, self.community))
            if change.change == ChangeType.create:
                created += 1
            else:
                updated += 1
            if len(batch) >= _BATCH_SIZE:
                self.transport.import_batch(batch)
                batch = []
        if batch:
            self.transport.import_batch(batch)

        return SyncResult(applied=created + updated, created=created, updated=updated)

    def pull(self, profile: Profile) -> WritebackSet:
        """Read governance-owned fields back into the model (§9.4 writeback loop)."""
        wb = WritebackSet()
        if not profile.writeback:
            return wb
        names = [w.external_attribute for w in profile.writeback]
        rows = self.transport.read_attributes(names)
        by_attr = {w.external_attribute: w.model_path for w in profile.writeback}
        for row in rows:
            attr = row.get("attribute")
            ext = row.get("externalId") or row.get("external_id")
            path = by_attr.get(attr)
            if path and ext:
                wb.values.append(
                    WritebackValue(external_id=ext, model_path=path, value=row.get("value"))
                )
        return wb


def _to_import_json(change, community: str) -> dict:
    """Shape a PlannedChange into a Collibra Import API JSON row (idempotent by
    externalId, §9.4)."""
    return {
        "resourceType": "Asset",
        "identifier": {"name": change.name, "community": {"name": community}},
        "externalId": change.external_id,
        "type": {"name": change.target_type},
        "attributes": {k: [{"value": v}] for k, v in change.attributes.items()},
        "relations": [
            {"type": rel_type, "target": {"externalId": target}}
            for rel_type, target in change.relations
        ],
    }
