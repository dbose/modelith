"""GenerationState.save must not delete the shards it just wrote.

Same Windows path-separator class as the reverse writer: `wanted` holds forward-slash
_shard_rel keys, but the prune loop compared str(relative_to()) which is backslash on
Windows — so every shard looked stale and the whole .mdl/state store was wiped on every
save, destroying the 3-way-merge base. The fix normalises with as_posix().
"""

from __future__ import annotations

from pathlib import PureWindowsPath

from mdl_core.state import FileState, GenerationState, _shard_rel


def _fs(path: str) -> FileState:
    return FileState(
        path=path, ulids=["01J"], fingerprint="fp", content_hash="ch", emitter_version="1"
    )


def test_save_keeps_shards_it_wrote(tmp_path):
    st = GenerationState()
    for i in range(5):
        st.record(_fs(f"models/model_{i}.sql"))
    st.save(tmp_path)
    shards = list((tmp_path / ".mdl" / "state").glob("*/*.json"))
    assert len(shards) == 5, "save must leave every shard it wrote on disk"

    # a re-save with the same state must not prune the live shards
    st.save(tmp_path)
    assert len(list((tmp_path / ".mdl" / "state").glob("*/*.json"))) == 5


def test_save_prunes_only_stale_shards(tmp_path):
    st = GenerationState()
    st.record(_fs("models/a.sql"))
    st.record(_fs("models/b.sql"))
    st.save(tmp_path)
    assert len(list((tmp_path / ".mdl" / "state").glob("*/*.json"))) == 2

    # drop one artifact -> its shard is pruned, the other survives
    st2 = GenerationState()
    st2.record(_fs("models/a.sql"))
    st2.save(tmp_path)
    assert len(list((tmp_path / ".mdl" / "state").glob("*/*.json"))) == 1


def test_shard_rel_is_posix_and_would_mismatch_backslash():
    # documents the bug: _shard_rel is forward-slash; a backslash-stringified rel (Windows)
    # is NOT in the wanted set, which is exactly what deleted every shard.
    rel = _shard_rel("models/a.sql")
    assert "/" in rel and "\\" not in rel
    win = str(PureWindowsPath(rel))
    assert win != rel  # backslashes on Windows
    assert PureWindowsPath(rel).as_posix() == rel  # the fix normalises back to a match
