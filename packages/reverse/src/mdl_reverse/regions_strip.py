"""Strip mdl protected-region markers, returning the generated content.

Reuses core's region parser (reverse depends only on core, layering §1.3) so the
marker grammar stays single-sourced.
"""

from __future__ import annotations

from mdl_core.regions import RegionKind, parse


def strip_regions(text: str, prefix: str = "#") -> str:
    """Return the concatenated generated-region bodies (drops user/literal markers).
    For schema.yml the generated region holds the full YAML doc."""
    parsed = parse(text, prefix)
    gen = [r.content for r in parsed.regions if r.kind == RegionKind.generated]
    if gen:
        return "\n".join(gen)
    # no markers -> plain YAML
    return text
