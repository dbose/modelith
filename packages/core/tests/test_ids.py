from mdl_core.ids import is_ulid, new_ulid, ulid_timestamp_ms


def test_ulid_shape():
    u = new_ulid()
    assert len(u) == 26
    assert is_ulid(u)


def test_ulid_timestamp_roundtrips():
    u = new_ulid(ms=1_700_000_000_000, entropy=b"\x00" * 10)
    assert ulid_timestamp_ms(u) == 1_700_000_000_000


def test_ulid_monotonic_by_time():
    a = new_ulid(ms=1000, entropy=b"\x00" * 10)
    b = new_ulid(ms=2000, entropy=b"\x00" * 10)
    assert a < b  # lexicographic order tracks time


def test_deterministic_under_injection():
    a = new_ulid(ms=5, entropy=b"\x01" * 10)
    b = new_ulid(ms=5, entropy=b"\x01" * 10)
    assert a == b


def test_bad_entropy_rejected():
    import pytest

    with pytest.raises(ValueError):
        new_ulid(entropy=b"short")


def test_the_ulid_format_the_typescript_port_must_match():
    """canvas/src/ulid.ts mints ULIDs client-side so a previewed creation keeps its
    identity through to the PR. That is a FORMAT port, so pin the format here: if
    this changes, the TS side must change with it or the server will reject ids it
    should accept."""
    from mdl_core.ids import is_ulid, new_ulid

    u = new_ulid()
    assert len(u) == 26
    assert u[0] in "01234567"  # 48-bit timestamp cannot overflow the first char
    assert set(u) <= set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")  # Crockford, no I L O U
    assert is_ulid(u)

    # samples minted by the TypeScript implementation must validate here
    for sample in (
        "01M20E3BBQ5SXPR7XKF12TH0XP",
        "01M20E3BBQZZ0000000000000A",
    ):
        assert is_ulid(sample), sample
