import sys
from pathlib import Path

import pytest

# Make the shared model builder importable (lives in core/tests).
_CORE_TESTS = Path(__file__).resolve().parents[2] / "core" / "tests"
sys.path.insert(0, str(_CORE_TESTS))

from model_builders import write_model  # noqa: E402


@pytest.fixture
def model_dir(tmp_path):
    write_model(tmp_path)
    return tmp_path


@pytest.fixture
def scd2_model_dir(tmp_path):
    write_model(tmp_path, with_scd2=True)
    return tmp_path
