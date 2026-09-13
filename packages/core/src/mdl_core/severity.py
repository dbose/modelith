"""Change severity, shared by drift (model vs manifest) and diff (model vs model).

`{breaking, additive, cosmetic, unmanaged}` is already the vocabulary of the CI
gates, the drift PR comment and `mdl drift --check`'s exit codes. An SME who sees
"breaking" in a model diff and "breaking" in CI must be reading the same word, so
the enum lives in core rather than being duplicated per consumer.

`unmanaged` is meaningful only for drift (a dbt model with no counterpart in the
model); a model-to-model diff simply never emits it.

The type-lattice helpers came along for the ride: they are pure logic that only
lived in `mdl_reverse.drift` because that is where narrowing was first needed.
"""

from __future__ import annotations

from enum import Enum


class ChangeSeverity(str, Enum):
    breaking = "breaking"
    additive = "additive"
    cosmetic = "cosmetic"
    unmanaged = "unmanaged"

    @property
    def rank(self) -> int:
        return {"breaking": 4, "unmanaged": 3, "additive": 2, "cosmetic": 1}[self.value]


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


# Coarse type family by NAME, robust to precision/scale/length: DECIMAL(18,2) and
# DECIMAL(38,2) are both "numeric"; VARCHAR(255) and TEXT are both "string". Used to
# decide that two types differing ONLY in precision/length are not a breaking change —
# a warehouse DECIMAL(18,2) vs a model's DECIMAL(38,2) default is cosmetic, not a
# stop-the-line type change. Distinct from _FAMILIES (which pins narrowing WIDTH within
# a family from an exact-string lattice); this is the family membership test.
_FAMILY_BY_NAME: dict[str, str] = {
    "DECIMAL": "numeric", "NUMERIC": "numeric", "NUMBER": "numeric", "DEC": "numeric",
    "FLOAT": "numeric", "DOUBLE": "numeric", "REAL": "numeric",
    "BIGINT": "numeric", "INT8": "numeric", "INTEGER": "numeric", "INT": "numeric",
    "INT4": "numeric", "SMALLINT": "numeric", "TINYINT": "numeric", "BOOLEAN": "numeric",
    "BOOL": "numeric",
    "VARCHAR": "string", "CHAR": "string", "TEXT": "string", "STRING": "string",
    "NVARCHAR": "string", "CHARACTER": "string",
    "DATE": "temporal", "TIMESTAMP": "temporal", "DATETIME": "temporal",
    "TIMESTAMPTZ": "temporal", "TIMESTAMP_NTZ": "temporal", "TIMESTAMP_TZ": "temporal",
    "TIMESTAMP_LTZ": "temporal",
}


def type_family(t: str | None) -> str | None:
    """The coarse family (numeric | string | temporal) of a SQL type, ignoring
    precision/scale/length. None when unknown."""
    if not t:
        return None
    base = t.upper().strip().split("(", 1)[0].strip()
    return _FAMILY_BY_NAME.get(base)


def same_family_precision_change(old: str | None, new: str | None) -> bool:
    """True when `old` and `new` are the same family and differ only in
    precision/scale/length (e.g. DECIMAL(38,2) vs DECIMAL(18,2), VARCHAR(255) vs
    VARCHAR(64)). Such a change is cosmetic, not breaking."""
    if not old or not new or old.upper().strip() == new.upper().strip():
        return False
    fo, fn = type_family(old), type_family(new)
    return fo is not None and fo == fn


def _is_narrowing(old: str | None, new: str | None) -> bool:
    if not old or not new:
        return False
    o = _family_and_width(old)
    n = _family_and_width(new)
    if o is None or n is None or o[0] != n[0]:
        return False  # unknown type or cross-family -> not "narrowing"
    return n[1] < o[1]
