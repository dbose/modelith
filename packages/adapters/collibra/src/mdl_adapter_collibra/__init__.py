"""Collibra governance adapter (spec §9.4).

Depends only on `modelith-governance` (layering §1.3): it never imports core.
"""

from mdl_adapter_collibra.adapter import CollibraAdapter, CollibraTransport, MockTransport

__all__ = ["CollibraAdapter", "CollibraTransport", "MockTransport"]
