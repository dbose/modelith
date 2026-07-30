"""Generation state (spec §5.2; sharded per collaboration model §6.2).

Records, per emitted file: path, contributing ULIDs, subgraph fingerprint,
emitted content hash, emitter version, spec versions. This is the merge base for
three-way merge (§5.3) and is committed to git.

One monolithic generation.json is a guaranteed merge hotspot on an active repo:
two engineers touching different models would conflict on the same file. So the
state is SHARDED — one small JSON per emitted artifact, path-hashed:

    .mdl/state/a3/f21c8e4b.json

Two engineers touching different models touch different state files and never
conflict. A legacy generation.json is read (and migrated to shards on the next
save) so existing repos upgrade transparently.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

STATE_REL = ".mdl/state"
_LEGACY_FILE = "generation.json"


def _shard_rel(artifact_path: str) -> str:
    h = hashlib.sha256(artifact_path.encode("utf-8")).hexdigest()
    return f"{h[:2]}/{h[2:12]}.json"


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
        state_dir = root / STATE_REL
        files: dict[str, FileState] = {}

        # legacy single-file format (pre-sharding)
        legacy = state_dir / _LEGACY_FILE
        if legacy.exists():
            data = json.loads(legacy.read_text(encoding="utf-8"))
            files.update({k: FileState(**v) for k, v in data.get("files", {}).items()})

        if state_dir.exists():
            for shard in sorted(state_dir.glob("*/*.json")):
                try:
                    v = json.loads(shard.read_text(encoding="utf-8"))
                    fs = FileState(**v)
                    files[fs.path] = fs
                except (json.JSONDecodeError, TypeError):
                    continue  # a corrupt shard costs one regeneration, not a crash
        return cls(files=files)

    def save(self, root: Path) -> None:
        state_dir = root / STATE_REL
        state_dir.mkdir(parents=True, exist_ok=True)

        wanted: set[str] = set()
        for fs in self.files.values():
            rel = _shard_rel(fs.path)
            wanted.add(rel)
            shard = state_dir / rel
            shard.parent.mkdir(parents=True, exist_ok=True)
            # sort_keys + newline => stable, diff-friendly, idempotent bytes
            shard.write_text(
                json.dumps(asdict(fs), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        # prune shards for artifacts that no longer exist + the legacy file
        for shard in state_dir.glob("*/*.json"):
            if str(shard.relative_to(state_dir)) not in wanted:
                shard.unlink()
        legacy = state_dir / _LEGACY_FILE
        if legacy.exists():
            legacy.unlink()
