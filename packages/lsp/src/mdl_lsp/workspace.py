"""Workspace state for the language server.

Discovers the model repo and the dbt project inside the opened folder, and
caches the parsed model / manifest / ontology registry behind cheap mtime
fingerprints — the same "git is the source of truth" discipline as the canvas
server. All feature builders (diagnostics/hover/lens/actions) are pure functions
over this object, which keeps them testable without an LSP transport.
"""

from __future__ import annotations

from pathlib import Path

from mdl_ontology import build_registry
from mdl_ontology.registry import OntologyRegistry

from mdl_core.commands import dir_fingerprint
from mdl_core.ir import Attribute, LogicalEntity, Model
from mdl_core.repo import ModelRepo
from mdl_reverse.manifest import ManifestProjection, read_manifest


class ModelWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.model_dir: Path | None = self._find("mdl-project.yaml")
        self.dbt_dir: Path | None = self._find("dbt_project.yml")
        self._repo: ModelRepo | None = None
        self._repo_fp: str | None = None
        self._manifest: ManifestProjection | None = None
        self._manifest_mtime: float | None = None
        self._registry: OntologyRegistry | None = None

    def _find(self, filename: str) -> Path | None:
        skip = {"node_modules", "dbt_packages", "target", ".venv", ".git"}
        hits = [
            p
            for p in self.root.rglob(filename)
            if not (set(p.parts) & skip)
        ]
        if not hits:
            return None
        hits.sort(key=lambda p: len(p.parts))
        return hits[0].parent

    # --- cached state -------------------------------------------------------

    @property
    def repo(self) -> ModelRepo | None:
        if self.model_dir is None:
            return None
        fp = dir_fingerprint(self.model_dir)
        if self._repo is None or self._repo_fp != fp:
            try:
                self._repo = ModelRepo.load(self.model_dir)
                self._repo_fp = fp
            except Exception:
                return self._repo  # keep last good model while user mid-edit
        return self._repo

    @property
    def model(self) -> Model | None:
        repo = self.repo
        return repo.model if repo else None

    @property
    def manifest_path(self) -> Path | None:
        if self.dbt_dir is None:
            return None
        p = self.dbt_dir / "target" / "manifest.json"
        return p if p.exists() else None

    @property
    def manifest(self) -> ManifestProjection | None:
        p = self.manifest_path
        if p is None:
            return None
        mtime = p.stat().st_mtime
        if self._manifest is None or self._manifest_mtime != mtime:
            try:
                self._manifest = read_manifest(p)
                self._manifest_mtime = mtime
            except Exception:
                return self._manifest
        return self._manifest

    @property
    def registry(self) -> OntologyRegistry | None:
        model = self.model
        if model is None or self.model_dir is None:
            return None
        if self._registry is None:
            reg = build_registry(self.model_dir, model.config.ontology_stack)
            reg.load()
            self._registry = reg
        return self._registry

    # --- lookups ------------------------------------------------------------

    def entity_for_dbt_model(self, name: str) -> LogicalEntity | None:
        model = self.model
        if model is None:
            return None
        for le in model.logical_entities.values():
            if le.name == name:
                return le
        return None

    def attribute(self, le: LogicalEntity, column: str) -> Attribute | None:
        for a in le.attributes:
            if a.name == column:
                return a
        return None

    def ulid_to_file(self) -> dict[str, str]:
        """ULID -> model-relative file, including attribute ULIDs."""
        repo = self.repo
        if repo is None:
            return {}
        out = {u: rel for rel, u in repo.file_ulid.items()}
        for le in repo.model.logical_entities.values():
            owner = repo.path_for_ulid(le.id)
            if owner:
                for a in le.attributes:
                    out.setdefault(a.id, owner)
        return out

    # --- SME knowledge card (the hover payload) -----------------------------

    def sme_card(self, le: LogicalEntity, attr: Attribute | None) -> str:
        """Markdown card: the conceptual layer arriving where the engineer works."""
        model = self.model
        assert model is not None
        ce = model.conceptual_entities.get(le.realises) if le.realises else None
        lines: list[str] = []
        if attr is not None:
            dom = model.domain_by_name(attr.domain)
            typ = dom.base_type if dom else (attr.domain or "string")
            role = {"business_key": "🔑 business key", "measure": "Σ measure"}.get(
                attr.role, attr.role
            )
            lines.append(f"**{attr.name}** · `{typ}` · {role}"
                         + ("" if attr.nullable else " · **not null**"))
            lines.append("")
        if ce:
            defn = f" — {ce.definition.strip()}" if ce.definition else ""
            lines.append(f"**{ce.name}**{defn}")
            if ce.ontology and ce.ontology.aligns_to:
                card = self.registry.describe(ce.ontology.aligns_to) if self.registry else None
                label = f" ({card['label']})" if card else ""
                lines.append(
                    f"- ontology: `{ce.ontology.aligns_to}`{label}"
                    + (f" · {ce.ontology.alignment}" if ce.ontology.alignment else "")
                    + (f" · layer **{ce.ontology.layer}**" if ce.ontology.layer else "")
                )
                if card and card.get("definition"):
                    lines.append(f"  > {card['definition']}")
            if ce.stewardship and (ce.stewardship.owner or ce.stewardship.steward):
                own = ce.stewardship.owner or "—"
                stw = ce.stewardship.steward or "—"
                lines.append(f"- owner: **{own}** · steward: **{stw}**")
        if attr is not None and attr.ontology and attr.ontology.aligns_to:
            lines.append(f"- column ontology: `{attr.ontology.aligns_to}`")
        if le.pattern:
            lines.append(f"- pattern: **{le.pattern}**")
        lines.append("")
        lines.append(f"`{(attr.id if attr else le.id)}` · Modelith")
        return "\n".join(lines)
