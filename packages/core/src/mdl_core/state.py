"""Generation state (spec §5.2).

.mdl/state/generation.json records, per emitted file: path, contributing ULIDs,
subgraph fingerprint, emitted content hash, emitter version, spec versions.
This is the merge base for three-way merge (§5.3) and is committed to git.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

STATE_REL = ".mdl/state/generation.json"


@dataclass
class FileState:
    path: str  # relative to project root
    ulids: list[str]
    fingerprint: str  # subgraph fingerprint (§0.1.1)
    content_hash: str  # hash of the bytes we last emitted (merge base, §5.3)
    emitter_version: str
    spec_versions: dict[str, str] = field(default_factory=dict)


@dataclass
class GenerationState:
    files: dict[str, FileState] = field(default_factory=dict)

    def record(self, fs: FileState) -> None:
        self.files[fs.path] = fs

    def get(self, path: str) -> FileState | None:
        return self.files.get(path)

    # --- persistence -------------------------------------------------------

    @classmethod
    def load(cls, root: Path) -> GenerationState:
        p = root / STATE_REL
        if not p.exists():
            return cls()
        data = json.loads(p.read_text(encoding="utf-8"))
        files = {k: FileState(**v) for k, v in data.get("files", {}).items()}
        return cls(files=files)

    def save(self, root: Path) -> None:
        p = root / STATE_REL
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {"files": {k: asdict(v) for k, v in sorted(self.files.items())}}
        # sort_keys + trailing newline => stable, diff-friendly, idempotent bytes
        p.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
