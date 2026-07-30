import sys
from pathlib import Path

import pytest

_CORE_TESTS = Path(__file__).resolve().parents[3] / "core" / "tests"
sys.path.insert(0, str(_CORE_TESTS))

from model_builders import write_model  # noqa: E402

PROFILES_DIR = Path(__file__).resolve().parents[4] / "profiles" / "governance"


@pytest.fixture
def model(tmp_path):
    from mdl_core.repo import ModelRepo

    write_model(tmp_path)
    return ModelRepo.load(tmp_path).model


@pytest.fixture
def profiles_dir():
    return PROFILES_DIR
