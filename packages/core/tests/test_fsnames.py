"""The shared filesystem-safe name helper — the one place object names become filenames."""

from __future__ import annotations

from mdl_core.fsnames import fs_slug, unique_namer


def test_strips_windows_illegal_chars():
    assert fs_slug("<root>") == "root"
    assert fs_slug("Order:Line") == "order_line"
    assert fs_slug("Foo/Bar") == "foo_bar"
    assert fs_slug('a"b|c?d*e') == "a_b_c_d_e"
    assert fs_slug("trailing.") == "trailing"
    assert fs_slug("   ") == "unnamed"
    assert fs_slug("Counterparty") == "counterparty"


def test_escapes_reserved_device_names():
    # CON / NUL / COM1 / LPT9 are uncreatable on Windows even as con.yaml
    assert fs_slug("CON") == "_con"
    assert fs_slug("nul") == "_nul"
    assert fs_slug("Com1") == "_com1"
    assert fs_slug("LPT9") == "_lpt9"
    # a normal name that merely CONTAINS a reserved word is fine
    assert fs_slug("control") == "control"


def test_case_fold_collision_via_slug():
    # Foo and FOO slug to the same string, so a caller can dedup them
    assert fs_slug("Foo") == fs_slug("FOO") == "foo"


def test_unique_namer_disambiguates_collisions():
    u = unique_namer()
    assert u("logical/entities/foo.yaml") == "logical/entities/foo.yaml"
    assert u("logical/entities/foo.yaml") == "logical/entities/foo-2.yaml"
    assert u("logical/entities/foo.yaml") == "logical/entities/foo-3.yaml"


def test_unique_namer_is_case_insensitive():
    # a case-insensitive filesystem treats Foo.yaml and foo.yaml as the same file
    u = unique_namer()
    assert u("Foo.yaml") == "Foo.yaml"
    assert u("foo.yaml") == "foo-2.yaml"


def test_unique_namer_returns_posix():
    u = unique_namer()
    assert u("logical\\entities\\foo.yaml") == "logical/entities/foo.yaml"
