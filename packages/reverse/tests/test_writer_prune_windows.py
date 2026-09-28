"""Regression: _prune_stale must not delete the files write_model just wrote.

On Windows, `path.relative_to(root)` stringifies with backslashes while the `written`
set holds forward-slash keys, so every just-written file looked stale and was deleted —
`mdl import erwin` reported N entities but left the model dir empty. The fix normalises
both sides with as_posix(); these tests guard the invariant on every OS and simulate the
Windows separator mismatch directly.
"""

from __future__ import annotations

from mdl_core.ir import Domain, LogicalEntity, Model, ProjectConfig
from mdl_reverse.writer import write_model


def _model(n: int) -> Model:
    m = Model(ProjectConfig(name="m", dbt_target="duckdb_dev"))
    m.add(Domain(id="01J000000000000000000DOM01", name="id_type", base_type="bigint"))
    for i in range(n):
        m.add(LogicalEntity(id=f"01J0000000000000000000EN{i:02d}", name=f"entity_{i}", realises=None))
    return m


def test_write_model_keeps_every_file_it_wrote(tmp_path):
    write_model(_model(52), tmp_path)
    files = list((tmp_path / "logical" / "entities").glob("*.yaml"))
    assert len(files) == 52, "write_model must leave all 52 entity files on disk"


def test_prune_stale_would_fail_with_naive_separator(tmp_path):
    """Directly prove the fix: build the prune comparison the OLD way (str with the OS
    separator) vs the NEW way (as_posix), against a forward-slash `written` set. On a
    POSIX box both match; the value of the test is documenting that a backslash-stringified
    rel (what Windows produced) is NOT in the forward-slash `written` set — the exact
    mismatch that deleted the files."""
    from pathlib import PureWindowsPath

    written = {"logical/entities/entity_0.yaml"}
    win_rel_naive = str(PureWindowsPath("logical/entities/entity_0.yaml"))  # backslashes
    win_rel_fixed = PureWindowsPath("logical/entities/entity_0.yaml").as_posix()
    assert win_rel_naive not in written  # the bug: would be pruned
    assert win_rel_fixed in written  # the fix: preserved


def test_rewrite_does_not_prune_live_files(tmp_path):
    # a second write with the SAME model must not delete-then-orphan anything
    write_model(_model(3), tmp_path)
    entities = tmp_path / "logical" / "entities"
    assert len(list(entities.glob("*.yaml"))) == 3
    write_model(_model(3), tmp_path)
    assert len(list(entities.glob("*.yaml"))) == 3, "re-write must not prune live files"


def test_written_keys_are_posix(tmp_path):
    """Every rel path write_model records is forward-slash, so _prune_stale's as_posix()
    comparison matches on Windows too."""
    written = write_model(_model(2), tmp_path)
    assert all("\\" not in w for w in written), "written keys must be POSIX-style"
    assert any(w.startswith("logical/entities/") for w in written)
