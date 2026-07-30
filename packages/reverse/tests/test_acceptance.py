"""M2 acceptance (spec §12): on a 400-model project, an injected column drop is
classified breaking and fails CI in under 30 seconds."""

from __future__ import annotations

import time
from pathlib import Path

from mdl_core.ids import new_ulid
from mdl_core.repo import ModelRepo
from mdl_reverse.drift import DriftKind, DriftSeverity, compute_drift
from mdl_reverse.manifest import read_manifest_dict

from manifest_fixtures import manifest_from_model

TARGET = "duckdb_dev"


def _build_400_model_project(root: Path, n: int = 400) -> None:
    (root / "conceptual" / "entities").mkdir(parents=True)
    (root / "logical" / "entities").mkdir(parents=True)
    (root / "logical" / "domains").mkdir(parents=True)
    (root / "mdl-project.yaml").write_text(
        f"name: finance\ndbt_target: {TARGET}\nplatform_targets: [{TARGET}]\nnaming: {{}}\n"
    )
    (root / "logical" / "domains" / "d.yaml").write_text(
        f"id: {new_ulid()}\nkind: domain\nname: d\nbase_type: bigint\n"
    )
    for i in range(n):
        ce = new_ulid()
        le = new_ulid()
        (root / "conceptual" / "entities" / f"e{i}.yaml").write_text(
            f"id: {ce}\nkind: conceptual_entity\nname: Entity{i}\n"
        )
        attrs = (
            f"  - id: {new_ulid()}\n    name: id_{i}\n    domain: d\n"
            f"    role: business_key\n    nullable: false\n"
        )
        for j in range(8):
            attrs += (
                f"  - id: {new_ulid()}\n    name: col_{j}\n    domain: d\n"
                f"    role: attribute\n    nullable: true\n"
            )
        (root / "logical" / "entities" / f"e{i}.yaml").write_text(
            f"id: {le}\nkind: logical_entity\nname: entity_{i}\n"
            f"realises: {ce}\nattributes:\n{attrs}"
        )


def test_400_model_column_drop_breaking_under_30s(tmp_path: Path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    _build_400_model_project(model_dir, 400)

    t0 = time.perf_counter()
    repo = ModelRepo.load(model_dir)
    raw = manifest_from_model(repo.model, TARGET)
    # Inject a column drop into one model.
    raw["nodes"]["model.testproj.entity_137"]["columns"].pop("col_3", None)

    proj = read_manifest_dict(raw)
    report = compute_drift(repo.model, proj, TARGET)
    elapsed = time.perf_counter() - t0

    assert len(repo.model.logical_entities) == 400
    dropped = [i for i in report.items if i.kind == DriftKind.column_dropped]
    assert dropped, "column drop not detected"
    assert dropped[0].severity == DriftSeverity.breaking
    assert dropped[0].model == "entity_137" and dropped[0].column == "col_3"
    assert report.has_breaking
    assert elapsed < 30.0, f"drift took {elapsed:.2f}s, must be < 30s"
