"""ModelRepo: load/save a model directory (spec §2.2 directory shape).

The repo holds two parallel representations of every file:
- `raw[path]` — the ruamel round-trip node, source of truth for re-serialisation
  with comments and key order intact.
- the parsed pydantic object in the `Model` graph, for validation and emission.

save() writes `raw` back out, so a load()->save() cycle is byte-identical
(the M0 acceptance criterion). Programmatic edits (e.g. a ULID rename) mutate the
raw node in place so comments survive.
"""

from __future__ import annotations

from pathlib import Path

from mdl_core.ir import (
    Model,
    ProjectConfig,
    object_class_for_kind,
)
from mdl_core.yaml_io import dump_file, load_file

# Glob patterns for object files, relative to the model root, in load order.
_OBJECT_GLOBS = [
    "conceptual/subject-areas/*.yaml",
    "conceptual/entities/*.yaml",
    "conceptual/terms/*.yaml",
    "logical/domains/*.yaml",
    "logical/value-sets/*.yaml",
    "logical/entities/*.yaml",
    "logical/relationships/*.yaml",
    "logical/key-groups/*.yaml",
    "logical/categories/*.yaml",
    "physical/*/tables/*.yaml",
]

PROJECT_FILE = "mdl-project.yaml"


class ModelRepo:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.model: Model
        # path (relative to root) -> ruamel round-trip node
        self.raw: dict[str, object] = {}
        # path (relative to root) -> the ULID declared in that file
        self.file_ulid: dict[str, str] = {}

    # --- loading -----------------------------------------------------------

    @classmethod
    def load(cls, root: Path) -> ModelRepo:
        repo = cls(root)
        repo._load()
        return repo

    def _load(self) -> None:
        project_path = self.root / PROJECT_FILE
        if not project_path.exists():
            raise FileNotFoundError(f"no {PROJECT_FILE} in {self.root}")
        project_raw = load_file(project_path)
        self.raw[PROJECT_FILE] = project_raw
        config = ProjectConfig.model_validate(_to_plain(project_raw))
        self.model = Model(config)

        for glob in _OBJECT_GLOBS:
            for path in sorted(self.root.glob(glob)):
                rel = str(path.relative_to(self.root))
                node = load_file(path)
                self.raw[rel] = node
                plain = _to_plain(node)
                kind = plain.get("kind")
                if kind is None:
                    raise ValueError(f"{rel}: object has no `kind`")
                cls_ = object_class_for_kind(kind)
                obj = cls_.model_validate(plain)
                self.model.add(obj)
                self.file_ulid[rel] = obj.id  # type: ignore[attr-defined]

    # --- saving ------------------------------------------------------------

    def save(self) -> None:
        """Write every raw node back to disk, preserving comments and order."""
        for rel, node in self.raw.items():
            dump_file(self.root / rel, node)

    # --- mutation helpers (used by the editor's command engine) -------------

    def add_file(self, rel: str, node: object, ulid: str | None = None) -> None:
        """Register a new object file; written on save()."""
        self.raw[rel] = node
        if ulid:
            self.file_ulid[rel] = ulid

    def remove_file(self, rel: str) -> None:
        """Drop a file from the repo and delete it on disk."""
        self.raw.pop(rel, None)
        self.file_ulid.pop(rel, None)
        p = self.root / rel
        if p.exists():
            p.unlink()

    def rename_file(self, old_rel: str, new_rel: str) -> None:
        """Move a file, keeping its raw node (and therefore its comments)."""
        if old_rel not in self.raw or old_rel == new_rel:
            return
        self.raw[new_rel] = self.raw.pop(old_rel)
        if old_rel in self.file_ulid:
            self.file_ulid[new_rel] = self.file_ulid.pop(old_rel)
        p = self.root / old_rel
        if p.exists():
            p.unlink()

    # --- lookups -----------------------------------------------------------

    def path_for_ulid(self, ulid: str) -> str | None:
        for rel, uid in self.file_ulid.items():
            if uid == ulid:
                return rel
        return None

    def raw_for_ulid(self, ulid: str) -> object | None:
        rel = self.path_for_ulid(ulid)
        return self.raw.get(rel) if rel else None


def _to_plain(node: object) -> dict:
    """Convert a ruamel CommentedMap tree into plain python dict/list/scalars
    for pydantic validation. ruamel types already subclass dict/list, but we
    normalise to be defensive against ruamel-specific scalar wrappers."""
    if isinstance(node, dict):
        return {str(k): _to_plain_value(v) for k, v in node.items()}
    raise TypeError(f"top-level YAML node must be a mapping, got {type(node).__name__}")


def _to_plain_value(v: object) -> object:
    if isinstance(v, dict):
        return {str(k): _to_plain_value(val) for k, val in v.items()}
    if isinstance(v, list):
        return [_to_plain_value(x) for x in v]
    # ruamel scalar string subclasses -> str; bool/int/float pass through
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return str(v)
    return v
