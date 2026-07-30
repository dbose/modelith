"""Physical -> logical lifting heuristics (spec §6.3).

Lifting is ~60% right, so it is interactive by design: these functions *propose*
and the human confirms via the decision ledger. Here we implement the mechanical
detections; the orchestrator (reverse.py) turns them into ledger proposals.

Detections:
- surrogate keys: `<entity>_sk`, `<entity>_key`, `dbt_scd_id`, hashed *_hashkey
- SCD2 column triple: valid_from / valid_to / is_current (+ dbt_valid_* variants)
- Data Vault: hub_/link_/sat_ prefixes and *_hashkey columns
- staging/intermediate exclusion by name prefix or path/tag
- business keys: `<entity>_id` / `id` columns not surrogate
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Column-name signals -------------------------------------------------------

_SURROGATE_SUFFIXES = ("_sk", "_key", "_hashkey", "_hk", "_pk")
_SURROGATE_EXACT = {"dbt_scd_id", "mdl_scd_id", "mdl_row_hash", "row_hash", "hashdiff"}

_SCD2_FROM = {"valid_from", "dbt_valid_from", "effective_from", "start_date", "eff_start_dt"}
_SCD2_TO = {"valid_to", "dbt_valid_to", "effective_to", "end_date", "eff_end_dt"}
_SCD2_CURRENT = {"is_current", "current_flag", "is_active", "dbt_is_current"}

_STAGING_PREFIXES = ("stg_", "staging_", "int_", "intermediate_", "base_", "tmp_", "temp_")


def is_surrogate_key(col: str) -> bool:
    c = col.lower()
    if c in _SURROGATE_EXACT:
        return True
    return any(c.endswith(s) for s in _SURROGATE_SUFFIXES)


def is_staging(model_name: str, tags: list[str] | None = None, path: str | None = None) -> bool:
    name = model_name.lower()
    if any(name.startswith(p) for p in _STAGING_PREFIXES):
        return True
    tagset = {t.lower() for t in (tags or [])}
    if tagset & {"staging", "intermediate", "stg", "int"}:
        return True
    if path:
        pl = path.lower()
        if "/staging/" in pl or "/intermediate/" in pl or "/stg/" in pl:
            return True
    return False


@dataclass
class Scd2Detection:
    is_scd2: bool
    from_col: str | None = None
    to_col: str | None = None
    current_col: str | None = None

    @property
    def tracking_cols(self) -> list[str]:
        return [c for c in (self.from_col, self.to_col, self.current_col) if c]


def detect_scd2(columns: list[str]) -> Scd2Detection:
    lower = {c.lower(): c for c in columns}
    frm = next((lower[c] for c in _SCD2_FROM if c in lower), None)
    to = next((lower[c] for c in _SCD2_TO if c in lower), None)
    cur = next((lower[c] for c in _SCD2_CURRENT if c in lower), None)
    # SCD2 needs at least a from/to pair.
    is_scd2 = bool(frm and to)
    return Scd2Detection(is_scd2=is_scd2, from_col=frm, to_col=to, current_col=cur)


@dataclass
class DataVaultDetection:
    kind: str | None  # "hub" | "link" | "satellite" | None


def detect_data_vault(model_name: str, columns: list[str]) -> DataVaultDetection:
    n = model_name.lower()
    has_hashkey = any(c.lower().endswith("_hashkey") or c.lower().endswith("_hk") for c in columns)
    if n.startswith(("hub_", "h_")) and has_hashkey:
        return DataVaultDetection("hub")
    if n.startswith(("link_", "lnk_", "l_")) and has_hashkey:
        return DataVaultDetection("link")
    if n.startswith(("sat_", "s_")) and any(
        c.lower() in {"hashdiff", "load_dts", "load_date"} for c in columns
    ):
        return DataVaultDetection("satellite")
    return DataVaultDetection(None)


# Business-key detection ----------------------------------------------------

_ID_RE = re.compile(r"^(?P<ent>.+)_id$", re.IGNORECASE)


def business_key_candidates(model_name: str, columns: list[str]) -> list[str]:
    """Columns that look like the entity's own business key."""
    ent = _singular(model_name)
    out = []
    for c in columns:
        cl = c.lower()
        if is_surrogate_key(c):
            continue
        if cl == "id" or cl == f"{ent}_id" or cl == f"{model_name.lower()}_id":
            out.append(c)
    # Fall back to any *_id that isn't a surrogate and isn't clearly an FK.
    if not out:
        for c in columns:
            if _ID_RE.match(c) and not is_surrogate_key(c):
                out.append(c)
                break
    return out


@dataclass
class ForeignKeyGuess:
    column: str
    target_entity: str  # inferred referenced model name (singular-ish)
    confidence: str = "medium"
    evidence: dict = field(default_factory=dict)


def foreign_key_candidates(
    model_name: str, columns: list[str], known_models: set[str]
) -> list[ForeignKeyGuess]:
    """`<other>_id` columns that match another model's name/business key (§6.2
    name+type heuristic, medium confidence — propose, never auto-accept)."""
    guesses: list[ForeignKeyGuess] = []
    self_ent = _singular(model_name)
    model_lookup = {_singular(m): m for m in known_models}
    for c in columns:
        m = _ID_RE.match(c)
        if not m:
            continue
        ref_ent = m.group("ent").lower()
        if ref_ent == self_ent:
            continue  # own business key, not an FK
        target = model_lookup.get(ref_ent)
        if target:
            guesses.append(
                ForeignKeyGuess(
                    column=c,
                    target_entity=target,
                    confidence="medium",
                    evidence={"heuristic": "name_type", "column": c, "target": target},
                )
            )
    return guesses


def _singular(name: str) -> str:
    n = name.lower()
    for pre in _STAGING_PREFIXES:
        if n.startswith(pre):
            n = n[len(pre) :]
            break
    if n.startswith(("dim_", "fct_", "fact_")):
        n = n.split("_", 1)[1]
    # naive singularisation
    if n.endswith("ies"):
        return n[:-3] + "y"
    if n.endswith("ses"):
        return n[:-2]
    if n.endswith("s") and not n.endswith("ss"):
        return n[:-1]
    return n
