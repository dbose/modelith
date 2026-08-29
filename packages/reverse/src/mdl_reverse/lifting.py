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

# The reverse heuristics are convention-driven, and conventions vary by shop (Kimball
# `dim_`/`fct_`, medallion `bronze_`/`silver_`/`gold_`, `f_`/`d_`, non-English, …). The
# built-in defaults below cover the common dbt/Kimball case; a project overrides any of
# them via `ReverseNaming` (mdl reverse --naming <file>, or the mdl-project.yaml naming
# block). Overrides are ADDITIVE — merged with these defaults — so an unset field keeps
# the default behaviour and you only declare what's non-standard.

_STRONG_SURROGATE_SUFFIXES = ("_sk", "_hashkey", "_hk", "_pk")
_SURROGATE_EXACT = frozenset(
    {"dbt_scd_id", "mdl_scd_id", "mdl_row_hash", "row_hash", "hashdiff"}
)
# Physical types a hash surrogate uses; a natural key is usually numeric/date/short.
_HASH_TYPES = ("VARCHAR", "CHAR", "TEXT", "STRING", "BINARY", "BYTES", "UUID")

_SCD2_FROM = frozenset(
    {"valid_from", "dbt_valid_from", "effective_from", "start_date", "eff_start_dt"}
)
_SCD2_TO = frozenset(
    {"valid_to", "dbt_valid_to", "effective_to", "end_date", "eff_end_dt"}
)
_SCD2_CURRENT = frozenset(
    {"is_current", "current_flag", "is_active", "dbt_is_current"}
)

_STAGING_PREFIXES = (
    "stg_", "staging_", "int_", "intermediate_", "base_", "tmp_", "temp_"
)
_STAGING_TAGS = frozenset({"staging", "intermediate", "stg", "int"})

# Reporting rollups / aggregates: kept in the model (for lineage) but NOT governed as
# business entities — they have no stable identity, just metric-by-dimension grain.
_ROLLUP_PREFIXES = ("mart_", "rpt_", "report_", "agg_", "kpi_", "metric_", "summary_")
_ROLLUP_TAGS = frozenset(
    {"mart", "report", "reporting", "aggregate", "agg", "kpi", "metric"}
)


@dataclass(frozen=True)
class ReverseNaming:
    """Naming conventions the reverse heuristics key off. Every field is the FULL set
    used at detection time; `merged()` folds user overrides into the built-in defaults
    so callers can declare only what differs from a standard dbt/Kimball project."""

    strong_surrogate_suffixes: tuple[str, ...] = _STRONG_SURROGATE_SUFFIXES
    surrogate_exact: frozenset[str] = _SURROGATE_EXACT
    hash_types: tuple[str, ...] = _HASH_TYPES
    scd2_from: frozenset[str] = _SCD2_FROM
    scd2_to: frozenset[str] = _SCD2_TO
    scd2_current: frozenset[str] = _SCD2_CURRENT
    staging_prefixes: tuple[str, ...] = _STAGING_PREFIXES
    staging_tags: frozenset[str] = _STAGING_TAGS
    rollup_prefixes: tuple[str, ...] = _ROLLUP_PREFIXES
    rollup_tags: frozenset[str] = _ROLLUP_TAGS

    @classmethod
    def merged(cls, overrides: dict | None) -> ReverseNaming:
        """Build a config by UNIONing user overrides onto the defaults. `overrides` is a
        plain dict (from YAML) keyed by the field names; list-valued fields are added
        to (not replaced), so declaring `rollup_prefixes: [gold_]` keeps mart_/rpt_/…
        and adds gold_. Unknown keys are ignored."""
        if not overrides:
            return cls()
        base = cls()
        kw: dict = {}
        for f in cls.__dataclass_fields__:
            extra = overrides.get(f)
            if not extra:
                continue
            cur = getattr(base, f)
            items = [str(x).lower() for x in extra]
            if isinstance(cur, tuple):
                kw[f] = tuple(dict.fromkeys([*cur, *items]))  # ordered union
            else:  # frozenset
                kw[f] = frozenset(cur | set(items))
        return cls(**kw)


DEFAULT_NAMING = ReverseNaming()


def is_surrogate_key(
    col: str,
    *,
    entity: str | None = None,
    data_type: str | None = None,
    naming: ReverseNaming = DEFAULT_NAMING,
) -> bool:
    """Whether `col` is a synthetic surrogate key that should be stripped from the
    logical view. `_sk`/`_hk`/`_hashkey`/`_pk` and known hash columns always are.

    `_key` is ambiguous: a conformed dimension's `date_key` / `product_key` is often the
    *natural* key, not a hash. When `entity`/`data_type` are given, treat a `_key`
    column as a natural key (NOT surrogate) if it names the entity itself
    (`date_key` on `dim_date`) or carries a non-hash type (INTEGER/DATE, not VARCHAR)."""
    c = col.lower()
    if c in naming.surrogate_exact:
        return True
    if any(c.endswith(s) for s in naming.strong_surrogate_suffixes):
        return True
    if c.endswith("_key"):
        stem = c[: -len("_key")]
        ent = _singular((entity or "").lower())
        # `<entity>_key` (date_key on dim_date / date) => natural key, keep it.
        if stem and ent and (stem == ent or ent.endswith(stem) or stem.endswith(ent)):
            return False
        # a non-hash physical type also signals a natural key.
        if data_type and data_type.upper().split("(")[0] not in naming.hash_types:
            return False
        return True
    return False


def is_staging(
    model_name: str,
    tags: list[str] | None = None,
    path: str | None = None,
    naming: ReverseNaming = DEFAULT_NAMING,
) -> bool:
    name = model_name.lower()
    if any(name.startswith(p) for p in naming.staging_prefixes):
        return True
    tagset = {t.lower() for t in (tags or [])}
    if tagset & naming.staging_tags:
        return True
    if path:
        pl = path.lower()
        if "/staging/" in pl or "/intermediate/" in pl or "/stg/" in pl:
            return True
    return False


def is_reporting_rollup(
    model_name: str,
    columns: list[str],
    types: dict[str, str] | None = None,
    tags: list[str] | None = None,
    naming: ReverseNaming = DEFAULT_NAMING,
) -> bool:
    """A reporting rollup / aggregate mart: a metric-by-dimension table with no entity
    identity. Detected by a rollup name prefix (mart_/rpt_/agg_/kpi_/…) or an
    aggregate tag, AND the absence of any business key. Such models are kept for
    lineage but marked unmanaged rather than minted as governed business entities
    (found dogfooding a warehouse with 20+ KPI marts)."""
    n = model_name.lower()
    tagset = {t.lower() for t in (tags or [])}
    named_rollup = n.startswith(naming.rollup_prefixes) or bool(tagset & naming.rollup_tags)
    if not named_rollup:
        return False
    # If it has a clear business key, it's a real (mart-layer) dimension/fact — keep it.
    if business_key_candidates(model_name, columns, types, naming=naming):
        return False
    return True


@dataclass
class Scd2Detection:
    is_scd2: bool
    from_col: str | None = None
    to_col: str | None = None
    current_col: str | None = None

    @property
    def tracking_cols(self) -> list[str]:
        return [c for c in (self.from_col, self.to_col, self.current_col) if c]


def detect_scd2(
    columns: list[str], naming: ReverseNaming = DEFAULT_NAMING
) -> Scd2Detection:
    lower = {c.lower(): c for c in columns}
    frm = next((lower[c] for c in naming.scd2_from if c in lower), None)
    to = next((lower[c] for c in naming.scd2_to if c in lower), None)
    cur = next((lower[c] for c in naming.scd2_current if c in lower), None)
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


def business_key_candidates(
    model_name: str,
    columns: list[str],
    types: dict[str, str] | None = None,
    naming: ReverseNaming = DEFAULT_NAMING,
) -> list[str]:
    """Columns that look like the entity's own business key. `types` (col -> data_type)
    lets a conformed dimension's natural `<entity>_key` (kept by is_surrogate_key) also
    count as a business key."""
    ent = _singular(model_name)
    types = types or {}
    out = []
    for c in columns:
        cl = c.lower()
        if is_surrogate_key(c, entity=model_name, data_type=types.get(c), naming=naming):
            continue
        if cl == "id" or cl == f"{ent}_id" or cl == f"{model_name.lower()}_id":
            out.append(c)
        # a natural `<entity>_key` (e.g. date_key on dim_date) that survived the
        # surrogate check is the dimension's key.
        elif cl.endswith("_key") and (cl[: -len("_key")] == ent
                                      or ent.endswith(cl[: -len("_key")])):
            out.append(c)
    # Fall back to any *_id that isn't a surrogate and isn't clearly an FK.
    if not out:
        for c in columns:
            if _ID_RE.match(c) and not is_surrogate_key(
                c, entity=model_name, data_type=types.get(c), naming=naming
            ):
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
