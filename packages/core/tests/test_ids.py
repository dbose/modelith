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
