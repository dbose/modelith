"""The writer must produce portable, filesystem-safe filenames.

A reversed/imported object name is used to derive its YAML filename. An erwin export can
carry names with characters that are illegal in a Windows filename (< > : " / \\ | ? *),
e.g. a domain named "<root>", which crashes open() on Windows (OSError 22). The writer
slugs every name to a safe form, and disambiguates collisions the slugging can create.
"""

from __future__ import annotations

from mdl_core.ir import Domain, LogicalEntity, Model, ProjectConfig
from mdl_reverse.writer import _slug, write_model


def test_slug_strips_windows_illegal_chars():
    assert _slug("<root>") == "root"
    assert _slug("Order:Line") == "order_line"
    assert _slug("Foo/Bar") == "foo_bar"
    assert _slug('a"b|c?d*e') == "a_b_c_d_e"
    assert _slug("trailing.") == "trailing"  # Windows forbids a trailing dot
    assert _slug("   ") == "unnamed"  # never empty
    assert _slug("Counterparty") == "counterparty"  # a clean name is unchanged


def test_writer_handles_illegal_domain_name(tmp_path):
    # a domain literally named "<root>" (erwin's structural node) must not crash the write
    m = Model(ProjectConfig(name="m", dbt_target="duckdb_dev"))
    m.add(Domain(id="01J000000000000000000DOM01", name="<root>", base_type="string"))
    written = write_model(m, tmp_path)
    # it landed at a safe filename, and no angle brackets reached the filesystem
    assert (tmp_path / "logical" / "domains" / "root.yaml").exists()
    assert any(w.endswith("root.yaml") for w in written)


def test_writer_disambiguates_filename_collisions(tmp_path):
    # two entities whose names slug to the SAME filename must both survive, not overwrite
    m = Model(ProjectConfig(name="m", dbt_target="duckdb_dev"))
    m.add(LogicalEntity(id="01J000000000000000000ENT01", name="Order Line", realises=None))
    m.add(LogicalEntity(id="01J000000000000000000ENT02", name="Order/Line", realises=None))
    write_model(m, tmp_path)
    files = sorted(p.name for p in (tmp_path / "logical" / "entities").glob("*.yaml"))
    # both were written (one disambiguated with a -2 suffix), neither clobbered
    assert files == ["order_line-2.yaml", "order_line.yaml"]
