"""Collaboration-model mechanics: state sharding (§6.2) + semantic merge (§6.1)."""

from __future__ import annotations

from pathlib import Path

from mdl_core.merge_driver import merge_model_files, run_merge_driver
from mdl_core.state import FileState, GenerationState

# --- §6.2 sharded generation state ---------------------------------------------


def _fs(path: str) -> FileState:
    return FileState(
        path=path, ulids=["01A"], fingerprint="sha256:x", content_hash="sha256:y",
        emitter_version="0.1.0",
    )


def test_state_shards_per_artifact(tmp_path: Path):
    st = GenerationState()
    st.record(_fs("models/a.sql"))
    st.record(_fs("models/b.sql"))
    st.save(tmp_path)
    shards = list((tmp_path / ".mdl" / "state").glob("*/*.json"))
    assert len(shards) == 2  # different artifacts -> different files -> no merge conflicts
    assert GenerationState.load(tmp_path).files.keys() == {"models/a.sql", "models/b.sql"}


def test_state_save_prunes_and_is_idempotent(tmp_path: Path):
    st = GenerationState()
    st.record(_fs("models/a.sql"))
    st.record(_fs("models/b.sql"))
    st.save(tmp_path)
    st2 = GenerationState()
    st2.record(_fs("models/a.sql"))
    st2.save(tmp_path)  # b removed -> its shard pruned
    assert GenerationState.load(tmp_path).files.keys() == {"models/a.sql"}
    before = {p: p.read_bytes() for p in (tmp_path / ".mdl" / "state").glob("*/*.json")}
    st2.save(tmp_path)
    after = {p: p.read_bytes() for p in (tmp_path / ".mdl" / "state").glob("*/*.json")}
    assert before == after  # byte-stable


def test_state_legacy_migration(tmp_path: Path):
    import json

    legacy_dir = tmp_path / ".mdl" / "state"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "generation.json").write_text(
        json.dumps({"files": {"models/x.sql": {
            "path": "models/x.sql", "ulids": [], "fingerprint": "f",
            "content_hash": "c", "emitter_version": "0", "spec_versions": {},
        }}})
    )
    st = GenerationState.load(tmp_path)
    assert "models/x.sql" in st.files
    st.save(tmp_path)  # migrates: shards written, legacy removed
    assert not (legacy_dir / "generation.json").exists()
    assert "models/x.sql" in GenerationState.load(tmp_path).files


# --- §6.1 semantic merge driver ---------------------------------------------------

_BASE = """\
id: 01ENTITY000000000000000000
kind: logical_entity
name: trade
attributes:
  - id: 01ATTRA0000000000000000000
    name: trade_id
    role: business_key
    nullable: false
"""


def test_two_attribute_additions_merge_clean():
    ours = _BASE.replace(
        "    nullable: false\n",
        "    nullable: false\n  - id: 01ATTRB0000000000000000000\n    name: broker_code\n    role: attribute\n",
    )
    theirs = _BASE.replace(
        "    nullable: false\n",
        "    nullable: false\n  - id: 01ATTRC0000000000000000000\n    name: venue\n    role: attribute\n",
    )
    merged, conflicts = merge_model_files(_BASE, ours, theirs)
    assert conflicts == []
    assert "broker_code" in merged and "venue" in merged  # clean union
    assert "trade_id" in merged


def test_same_field_edit_conflicts():
    ours = _BASE.replace("name: trade_id", "name: trade_ref")
    theirs = _BASE.replace("name: trade_id", "name: trade_number")
    merged, conflicts = merge_model_files(_BASE, ours, theirs)
    assert conflicts, "same-attribute rename must conflict"
    assert "MDL MERGE CONFLICTS" in merged
    assert "trade_ref" in merged  # ours kept in the document body


def test_scalar_field_takes_theirs_when_ours_unchanged():
    theirs = _BASE + "pattern: scd2\n"
    merged, conflicts = merge_model_files(_BASE, _BASE, theirs)
    assert conflicts == []
    assert "pattern: scd2" in merged


def test_delete_vs_modify_conflicts():
    ours = _BASE  # unchanged
    base_two = _BASE.replace(
        "    nullable: false\n",
        "    nullable: false\n  - id: 01ATTRB0000000000000000000\n    name: venue\n    role: attribute\n",
    )
    ours_del = _BASE  # deleted venue
    theirs_mod = base_two.replace("name: venue", "name: venue_code")
    _, conflicts = merge_model_files(base_two, ours_del, theirs_mod)
    assert any("deleted by ours" in c for c in conflicts)
    _ = ours


def test_decisions_ledger_union_and_verdict_precedence():
    base = "decisions: []\n"
    ours = """\
decisions:
  - kind: relationship
    signal: name_type
    confidence: medium
    subject: a -> b
    verdict: accepted
    evidence: {}
"""
    theirs = """\
decisions:
  - kind: relationship
    signal: name_type
    confidence: medium
    subject: a -> b
    verdict: proposed
    evidence: {}
  - kind: scd2_pattern
    signal: scd2_columns
    confidence: medium-high
    subject: model x looks like SCD2
    verdict: accepted
    evidence: {}
"""
    merged, conflicts = merge_model_files(base, ours, theirs)
    assert conflicts == []
    assert "verdict: accepted" in merged  # human verdict beats proposed
    assert "looks like SCD2" in merged  # union


def test_run_merge_driver_exit_codes(tmp_path: Path):
    b, o, t = tmp_path / "b", tmp_path / "o", tmp_path / "t"
    b.write_text(_BASE)
    o.write_text(_BASE.replace("name: trade_id", "name: trade_ref"))
    t.write_text(_BASE.replace("name: trade_id", "name: trade_number"))
    assert run_merge_driver(b, o, t) == 1  # conflict
    o.write_text(_BASE)
    t.write_text(_BASE + "pattern: scd2\n")
    assert run_merge_driver(b, o, t) == 0
    assert "pattern: scd2" in o.read_text()
    # state mode always takes ours
    assert run_merge_driver(b, o, t, state=True) == 0
