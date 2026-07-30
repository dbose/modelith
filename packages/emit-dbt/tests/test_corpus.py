"""Golden corpus regression (spec §5.5 item 5).

M1 ships two synthetic corpus shapes (a plain analytics model and a wide model)
plus a scale check. The spec's five named real-shaped projects (jaffle_shop, a
Data Vault project, a 400-model finance project, a heavy-Jinja project, a no-tests
project) are seeded into corpus/ as follow-up work; each must round-trip on every
commit. Here we assert the round-trip *property* holds at scale so the gate is
meaningful before those projects land.
"""

from __future__ import annotations

from pathlib import Path

from mdl_core.ids import new_ulid
from mdl_core.repo import ModelRepo
from mdl_emit_dbt.emitter import DbtEmitter


def _build_wide_model(root: Path, n_entities: int = 50, n_attrs: int = 12) -> None:
    (root / "conceptual" / "entities").mkdir(parents=True)
    (root / "logical" / "entities").mkdir(parents=True)
    (root / "logical" / "domains").mkdir(parents=True)
    (root / "mdl-project.yaml").write_text(
        "name: corpus\ndbt_target: duckdb_dev\nplatform_targets: [duckdb_dev]\nnaming: {}\n"
    )
    dom = new_ulid()
    (root / "logical" / "domains" / "d.yaml").write_text(
        f"id: {dom}\nkind: domain\nname: d\nbase_type: bigint\n"
    )
    for i in range(n_entities):
        ce = new_ulid()
        le = new_ulid()
        (root / "conceptual" / "entities" / f"e{i}.yaml").write_text(
            f"id: {ce}\nkind: conceptual_entity\nname: Entity{i}\n"
        )
        attrs = ""
        for j in range(n_attrs):
            role = "business_key" if j == 0 else "attribute"
            attrs += (
                f"  - id: {new_ulid()}\n    name: col_{j}\n    domain: d\n"
                f"    role: {role}\n    nullable: {'false' if j == 0 else 'true'}\n"
            )
        (root / "logical" / "entities" / f"e{i}.yaml").write_text(
            f"id: {le}\nkind: logical_entity\nname: entity_{i}\n"
            f"realises: {ce}\nattributes:\n{attrs}"
        )


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): p.read_text()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_wide_corpus_roundtrips_and_is_idempotent(tmp_path: Path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    _build_wide_model(model_dir)

    # M0: load->save zero diff
    before = {p.name: p.read_text() for p in model_dir.rglob("*.yaml")}
    repo = ModelRepo.load(model_dir)
    repo.save()
    after = {p.name: p.read_text() for p in model_dir.rglob("*.yaml")}
    assert before == after

    # M1: generate idempotence at scale
    out = tmp_path / "dbt"
    DbtEmitter(repo.model, "duckdb_dev").generate(out, write=True)
    first = _snapshot(out)
    DbtEmitter(ModelRepo.load(model_dir).model, "duckdb_dev").generate(out, write=True)
    assert first == _snapshot(out)

    # 50 models emitted
    assert len(list((out / "models").glob("*.sql"))) == 50
