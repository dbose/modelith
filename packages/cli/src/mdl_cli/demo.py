"""`mdl init --demo`: scaffold a populated example from a bundled adoption asset.

The plain scaffold (`scaffold.py`) writes one entity, so a first `mdl serve` shows a
near-empty grid. `--demo` instead lays down a real 7-entity investment
book-of-record model (price, instrument, position, counterparty, transaction,
portfolio, benchmark) plus a matching DuckDB dbt project, so the very first
`mdl serve` renders a populated ERD and the full offline loop
(`serve` → `validate` → `generate` → `dbt build`) runs with no warehouse account
(spec §4.2).

The asset is vendored under `_demo/` and shipped in the wheel. On copy we mint a
fresh ULID for every id in the model so two `init --demo` runs, or two demos in one
workspace, never collide on identity — while preserving cross-references (a given
old id maps to one new id everywhere it appears).
"""

from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path

from mdl_core.ids import is_ulid, new_ulid

# Files whose ULIDs we remint. YAML/text model files only; CSV seeds and .sql
# carry no ULIDs, and the dbt project references models by name, not id.
_REMINT_SUFFIXES = {".yaml", ".yml"}


def _remint_ids(text: str, mapping: dict[str, str]) -> str:
    """Replace every 26-char ULID token in `text` with a stable fresh ULID, so
    references (realises:, subject_area:, relationship endpoints) stay consistent.
    A token is any whitespace/quote/punctuation-delimited run; we only swap ones
    `is_ulid` accepts, so ordinary words are untouched."""
    out: list[str] = []
    token = ""

    def flush() -> None:
        nonlocal token
        if token and is_ulid(token):
            out.append(mapping.setdefault(token, new_ulid()))
        else:
            out.append(token)
        token = ""

    for ch in text:
        if ch.isalnum():
            token += ch
        else:
            flush()
            out.append(ch)
    flush()
    return "".join(out)


def scaffold_demo(root: Path) -> list[str]:
    """Copy the bundled demo into `root`, reminting ids. Returns the relative paths
    written, sorted."""
    root.mkdir(parents=True, exist_ok=True)
    src = resources.files("mdl_cli") / "_demo"
    mapping: dict[str, str] = {}
    written: list[str] = []

    # resources.files gives a Traversable; materialise it to a real path so we can
    # walk it. For a normal (non-zipped) wheel install this is a directory on disk.
    with resources.as_file(src) as src_dir:
        for path in sorted(Path(src_dir).rglob("*")):
            if path.is_dir():
                continue
            rel = path.relative_to(src_dir)
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix in _REMINT_SUFFIXES:
                dest.write_text(
                    _remint_ids(path.read_text(encoding="utf-8"), mapping),
                    encoding="utf-8",
                )
            else:
                shutil.copyfile(path, dest)
            written.append(str(rel))

    # State dir the tool expects (excluded from the vendored asset).
    (root / "model" / ".mdl" / "state").mkdir(parents=True, exist_ok=True)
    return written
