"""Conformance kit + reference-profile tests (spec §9.5, §12 acceptance)."""

from __future__ import annotations

from mdl_governance import Profile, run_conformance
from mdl_governance.conformance import run_conformance as run


def test_all_reference_profiles_conform(model, profiles_dir):
    for fname in ("collibra-oob.yaml", "dbt-analytics.yaml", "minimal.yaml"):
        prof = Profile.load(profiles_dir / fname)
        result = run(model, prof)  # non-strict: partial profiles allowed
        assert result.passed, f"{fname} conformance errors: {result.errors}"


def test_bespoke_profile_passes_and_maps(model):
    # A profile "written by someone outside the team" (spec §12 acceptance).
    bespoke = Profile.from_dict(
        {
            "profile": "acme_custom",
            "version": 1,
            "domains": {"main": {"name": "Acme Catalog", "type": "Domain"}},
            "asset_types": {
                "conceptual_entity": {
                    "target": "Concept",
                    "domain": "main",
                    "attributes": {"Def": "{{ definition }}"},
                },
                "logical_entity": {"target": "Dataset", "domain": "main"},
                "attribute": {"target": "Field", "domain": "main"},
            },
            "relations": {"realises": {"type": "derives from"}, "has_attribute": {"type": "contains"}},
            "responsibilities": {"owner": {"role": "Owner"}},
        }
    )
    result = run_conformance(model, bespoke)
    assert result.passed, result.errors
    assert result.mapped_assets > 0


def test_template_error_fails_conformance(model):
    # A template referencing a non-existent attribute must fail (StrictUndefined).
    bad = Profile.from_dict(
        {
            "profile": "bad",
            "version": 1,
            "asset_types": {
                "logical_entity": {"target": "T", "attributes": {"X": "{{ nonexistent_field }}"}}
            },
        }
    )
    result = run_conformance(model, bad)
    assert not result.passed
    assert any("render error" in e for e in result.errors)


def test_bad_writeback_path_fails(model):
    bad = Profile.from_dict(
        {
            "profile": "bad",
            "version": 1,
            "asset_types": {"logical_entity": {"target": "T"}},
            "writeback": [{"collibra_attribute": "X", "model_path": "not_governance.field"}],
        }
    )
    result = run_conformance(model, bad)
    assert not result.passed


def test_strict_flags_unmapped_kind(model, profiles_dir):
    prof = Profile.load(profiles_dir / "minimal.yaml")
    assert run_conformance(model, prof, strict=False).passed
    assert not run_conformance(model, prof, strict=True).passed
