from pathlib import Path

from mdl_core.diagnostics import Severity
from mdl_core.repo import ModelRepo
from mdl_core.validate import validate


def test_clean_model_validates(model_dir: Path):
    repo = ModelRepo.load(model_dir)
    diags = validate(repo.model)
    errors = diags.by_min_severity(Severity.error)
    assert errors == [], f"unexpected errors: {[d.message for d in errors]}"


def test_dangling_ref_is_error(model_dir: Path):
    # Point a logical entity at a non-existent conceptual ULID.
    le = model_dir / "logical" / "entities" / "counterparty.yaml"
    le.write_text(le.read_text().replace("realises:", "realises: 01BADBADBADBADBADBADBADBAD0\n#"))
    repo = ModelRepo.load(model_dir)
    diags = validate(repo.model)
    codes = {d.code for d in diags.items}
    assert "MDL-E102" in codes


def test_core_term_without_industry_alignment_errors(tmp_path: Path):
    (tmp_path / "conceptual" / "entities").mkdir(parents=True)
    (tmp_path / "mdl-project.yaml").write_text("name: t\nnaming: {}\n")
    (tmp_path / "conceptual" / "entities" / "x.yaml").write_text(
        "id: 01J8ZQ7X4K5N9P2R3S6T8V0W1Y\n"
        "kind: conceptual_entity\n"
        "name: Thing\n"
        "ontology:\n  layer: core\n"
    )
    repo = ModelRepo.load(tmp_path)
    diags = validate(repo.model)
    assert "MDL-E202" in {d.code for d in diags.items}
