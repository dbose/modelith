"""OSI support, version-isolated (spec §4.3).

All OSI knowledge lives behind osi/v<version>/ so adding a new spec version never
touches the IR. `get_osi(version)` returns the emit/import functions for a version.
Currently ships v0.1.1 (the tagged osi-0.1.1-rc1 release; main is 0.2.0.dev0 DRAFT
and intentionally NOT targeted, per spec §4.1 / §13.1).
"""

from __future__ import annotations

from mdl_emit_semantic.osi import v0_1_1

_VERSIONS = {"0.1.1": v0_1_1}
DEFAULT_VERSION = "0.1.1"


def get_osi(version: str = DEFAULT_VERSION):
    key = version.lstrip("v")
    if key not in _VERSIONS:
        raise KeyError(f"unsupported OSI version {version!r}; have {sorted(_VERSIONS)}")
    return _VERSIONS[key]
