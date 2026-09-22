"""Suggest a `reverse:` config block from a compiled manifest (deterministic).

A user shouldn't author warehouse conventions from a blank page. `suggest_config` reads
the manifest's own signals — folder structure (fqn/path), name prefixes, tags — and
proposes a `reverse:` block: layers (folder/prefix -> role), likely exclusions, and
convention hints. It is pure frequency/pattern analysis: NO AI, NO data profiling, NO
functional-dependency inference (all spurious-prone). The output is a *proposal* the user
reviews as a git diff and edits — never auto-applied without an explicit --apply.

Mapping is grounded in dbt's own layering (staging = source wrappers to exclude; marts =
the entity layer) and the common Kimball/medallion/Data-Vault prefix conventions.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from mdl_reverse.manifest import ManifestProjection

# Prefix -> role, in the standard dbt/Kimball/medallion/DV vocabulary. A prefix that shows
# up on >=MIN_PREFIX models is proposed as a layer. Order matters only for readability.
_PREFIX_ROLE: list[tuple[str, str]] = [
    ("stg_", "staging"),
    ("staging_", "staging"),
    ("base_", "staging"),
    ("int_", "intermediate"),
    ("intermediate_", "intermediate"),
    ("bronze_", "staging"),
    ("silver_", "intermediate"),
    ("dim_", "dimension"),
    ("d_", "dimension"),
    ("fct_", "fact"),
    ("fact_", "fact"),
    ("f_", "fact"),
    ("mart_", "mart"),
    ("gold_", "mart"),
    ("rpt_", "mart"),
    ("agg_", "mart"),
    ("hub_", "hub"),
    ("h_", "hub"),
    ("link_", "link"),
    ("lnk_", "link"),
    ("sat_", "satellite"),
    ("s_", "satellite"),
]

# A folder segment name -> role, for folder-based layering (models/marts/... etc.).
_FOLDER_ROLE = {
    "staging": "staging",
    "stg": "staging",
    "bronze": "staging",
    "intermediate": "intermediate",
    "int": "intermediate",
    "silver": "intermediate",
    "marts": "mart",
    "mart": "mart",
    "gold": "mart",
    "dimensions": "dimension",
    "dims": "dimension",
    "facts": "fact",
    "vault": "hub",
}

# folder segments / name substrings that usually mean "don't reverse this".
_EXCLUDE_HINTS = ("scratch", "tmp", "temp", "sandbox", "_bak", "backup", "deprecated")

_MIN_PREFIX = 2  # a prefix must cover >=2 models to be worth a layer (avoid noise)


@dataclass
class SuggestReport:
    """The suggested block plus a human-readable rationale (what signal drove each rule)."""

    block: dict = field(default_factory=dict)
    rationale: list[str] = field(default_factory=list)


def _prefix_of(name: str) -> str | None:
    n = name.lower()
    for pre, _role in _PREFIX_ROLE:
        if n.startswith(pre):
            return pre
    return None


def _folder_segments(path: str | None, fqn: list[str]) -> list[str]:
    segs: list[str] = []
    if fqn:
        # fqn = [project, subfolders…, name]; drop project + the model name itself
        segs = [s.lower() for s in fqn[1:-1]]
    elif path:
        parts = [p.lower() for p in path.replace("\\", "/").split("/")]
        # drop a leading "models" and the filename
        parts = [p for p in parts if p not in ("", "models")]
        segs = parts[:-1] if parts else []
    return segs


def suggest_config(manifest: ManifestProjection) -> SuggestReport:
    """Propose a `reverse:` block from the manifest's folders/prefixes/tags."""
    models = list(manifest.models.values())
    report = SuggestReport()
    exclude: list[str] = []
    seen_layer_keys: set[tuple[str, str]] = set()  # (role, matcher) dedup

    # Emission ORDER matters: resolve_layer is first-match-wins, so a SPECIFIC role
    # (dimension/fact/hub/link/satellite from a dim_/fct_/hub_ prefix) must be proposed
    # BEFORE a broad folder layer (a marts folder holding dims + facts). So collect prefix
    # layers and folder layers separately, then concatenate prefix-first.
    prefix_layers: list[dict] = []
    folder_layers: list[dict] = []

    # 1) Prefix-based layers (the precise signal).
    prefix_counts: Counter[str] = Counter()
    for m in models:
        p = _prefix_of(m.name)
        if p:
            prefix_counts[p] += 1
    for pre, n in prefix_counts.most_common():
        if n < _MIN_PREFIX:
            continue
        role = dict(_PREFIX_ROLE)[pre]
        key = (role, f"prefix:{pre}")
        if key in seen_layer_keys:
            continue
        seen_layer_keys.add(key)
        prefix_layers.append({"name": pre.rstrip("_"), "role": role, "match": {"prefix": pre}})
        report.rationale.append(f"layer '{pre}' (role {role}) — {n} models named {pre}*")

    # 2) Folder-based layers (dbt's own structure; broader fallback).
    folder_role_counts: Counter[tuple[str, str]] = Counter()  # (folder_seg, role)
    for m in models:
        for seg in _folder_segments(m.path, m.fqn):
            if seg in _FOLDER_ROLE:
                folder_role_counts[(seg, _FOLDER_ROLE[seg])] += 1
            if any(h in seg for h in _EXCLUDE_HINTS):
                glob = f"**/{seg}/**"
                if glob not in exclude:
                    exclude.append(glob)
                    report.rationale.append(f"exclude {glob} — folder name looks non-durable")
    for (seg, role), n in folder_role_counts.most_common():
        key = (role, f"path:{seg}")
        if key in seen_layer_keys:
            continue
        seen_layer_keys.add(key)
        folder_layers.append({"name": seg, "role": role, "match": {"path_glob": f"**/{seg}/**"}})
        report.rationale.append(f"layer '{seg}' (role {role}) — {n} models under a /{seg}/ folder")

    layers = prefix_layers + folder_layers

    # 3) name-based exclude hints (e.g. orders_tmp) as globs.
    for m in models:
        nl = m.name.lower()
        for h in _EXCLUDE_HINTS:
            if h in nl:
                glob = f"*{h}*"
                if glob not in exclude:
                    exclude.append(glob)
                    report.rationale.append(f"exclude {glob} — model names contain '{h}'")
                break

    if layers:
        report.block["layers"] = layers
    if exclude:
        report.block["exclude"] = exclude
    return report
