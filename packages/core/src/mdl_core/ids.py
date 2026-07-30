"""ULID identity (spec §2.1).

Every object carries a ULID assigned at creation and never changed. We implement
Crockford base32 ULIDs without a third-party dependency so `core` stays lean, and
so identity generation is fully deterministic under test (an injectable clock/entropy).

A ULID is 26 chars: 48-bit millisecond timestamp + 80 bits of entropy, Crockford
base32 encoded. Lexicographic sort order matches creation time order.
"""

from __future__ import annotations

import os
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DECODE = {c: i for i, c in enumerate(_CROCKFORD)}

ULID = str  # semantic alias; the IR annotates ULID references with this


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def new_ulid(*, ms: int | None = None, entropy: bytes | None = None) -> ULID:
    """Generate a new ULID.

    Args:
        ms: millisecond timestamp; defaults to now. Injectable for tests.
        entropy: 10 bytes of randomness; defaults to os.urandom. Injectable for tests.
    """
    if ms is None:
        ms = int(time.time() * 1000)
    if entropy is None:
        entropy = os.urandom(10)
    if len(entropy) != 10:
        raise ValueError("ULID entropy must be exactly 10 bytes")
    ts_part = _encode(ms & ((1 << 48) - 1), 10)
    rand_int = int.from_bytes(entropy, "big")
    rand_part = _encode(rand_int, 16)
    return ts_part + rand_part


def is_ulid(value: str) -> bool:
    """True if `value` is a syntactically valid canonical ULID."""
    if not isinstance(value, str) or len(value) != 26:
        return False
    return all(c in _DECODE for c in value.upper())


def ulid_timestamp_ms(value: ULID) -> int:
    """Extract the millisecond timestamp encoded in a ULID."""
    if not is_ulid(value):
        raise ValueError(f"not a valid ULID: {value!r}")
    ts = 0
    for c in value[:10].upper():
        ts = (ts << 5) | _DECODE[c]
    return ts
