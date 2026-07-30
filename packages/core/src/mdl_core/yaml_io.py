"""Comment- and order-preserving YAML I/O (spec §1.2, §12 M0 acceptance).

We use ruamel.yaml in round-trip mode. The critical M0 acceptance test is that
loading a YAML file and dumping it back produces a byte-identical result,
including comments, key order, and formatting. All YAML I/O in the tool goes
through this module so that guarantee holds everywhere.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


def _yaml() -> YAML:
    y = YAML()  # round-trip mode by default
    y.preserve_quotes = True
    y.width = 4096  # avoid ruamel re-wrapping long scalars, which would change bytes
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def load_str(text: str) -> Any:
    """Load YAML text into a ruamel round-trip structure (CommentedMap/Seq)."""
    return _yaml().load(text)


def dump_str(data: Any) -> str:
    """Dump a ruamel round-trip structure back to text, preserving comments/order."""
    buf = io.StringIO()
    _yaml().dump(data, buf)
    return buf.getvalue()


def load_file(path: Path) -> Any:
    return load_str(path.read_text(encoding="utf-8"))


def dump_file(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_str(data), encoding="utf-8")


def round_trip(text: str) -> str:
    """Load then dump. The M0 invariant is round_trip(text) == text for
    canonically-formatted input."""
    return dump_str(load_str(text))
