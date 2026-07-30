"""Content-addressed fingerprints (spec §0.1.1, §5.1).

A fingerprint is a sha256 over a *canonical* serialisation of the model subgraph
that produced a file. Because it is canonical (sorted keys, no whitespace
variance), the same subgraph always yields the same fingerprint, which is what
makes idempotent regeneration testable (property 1).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel


def _canonical(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        # by_alias so `from`/`from_` serialise stably; exclude derived fields
        return _canonical(obj.model_dump(by_alias=True, exclude_none=True))
    if isinstance(obj, dict):
        return {k: _canonical(obj[k]) for k in sorted(obj, key=str)}
    if isinstance(obj, (list, tuple)):
        return [_canonical(x) for x in obj]
    return obj


def fingerprint_subgraph(*objects: Any) -> str:
    """sha256:... over the canonical JSON of the given objects, order-independent."""
    canon = sorted(
        json.dumps(_canonical(o), separators=(",", ":"), sort_keys=True) for o in objects
    )
    blob = "\n".join(canon).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def content_hash(text: str) -> str:
    """sha256:... over emitted file content, for merge-base comparison (§5.3)."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
